"""
``python -m agentic_historian`` — CLI entry point (no Discord required).

    python -m agentic_historian run path/to/image.jpg [--lang de]
    python -m agentic_historian pull-share --folder digitalisate
    python -m agentic_historian atr-batch --source DIR --models a,b,c --run NAME
    python -m agentic_historian report-run    --run-dir DIR
    python -m agentic_historian publish-batch --run-dir DIR

``run`` drives the full A→B→C pipeline on one image. The other three are the
batch path: mirror a Nextcloud share, read every page with every model, publish
the result. They are separate commands on purpose — each is restartable on its
own, and a publish should never be a side effect of a recognition run that took
all night.

All agent logs go to stdout (loguru default).
"""

import argparse
import sys
from pathlib import Path

# The package's modules import each other flatly (``import config``), which works
# when the process starts inside this directory — how the bot and the tests are
# run — and not when it starts one level up, which is where ``python -m
# agentic_historian`` starts. Putting the directory on the path here is the same
# thing tests/conftest.py does, for the same reason, and it is what makes the
# documented ``python -m agentic_historian …`` commands work from the repo root.
_PKG = Path(__file__).resolve().parent
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

import config  # noqa: E402  — must follow the sys.path fix above

config.ensure_dirs()


def run(args: argparse.Namespace) -> int:
    from orchestrator import run_full_pipeline  # heavy: agents, LLM clients

    image_path = Path(args.file).resolve()
    if not image_path.exists():
        print(f"Error: file not found: {image_path}", file=sys.stderr)
        return 1

    lang = args.lang or "la"
    print(f"[CLI] Transcribing {image_path} (lang={lang})...")
    print(run_full_pipeline(image_path, lang=lang))
    return 0


def pull_share(args: argparse.Namespace) -> int:
    """Mirror a Nextcloud public-share folder into a local staging directory."""
    from utils import nextcloud

    try:
        share = nextcloud.share_from_config() if not args.url else nextcloud.parse_share_url(
            args.url, args.password or config.NEXTCLOUD_SHARE_PASS
        )
    except nextcloud.NextcloudError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    folder = args.folder if args.folder is not None else config.NEXTCLOUD_REMOTE_DIR
    dest = Path(args.dest) if args.dest else config.NEXTCLOUD_STAGING_DIR / (folder or "share")

    if args.list:
        files = nextcloud.list_files(folder, share=share)
        total = sum(size for _, size in files)
        for path, size in files:
            print(f"{size:>12}  {path}")
        print(f"\n{len(files)} file(s), {total / 1e9:.2f} GB in '{folder or '/'}'")
        return 0

    files = nextcloud.pull_folder(folder, dest, share=share, limit=args.limit)
    print(f"{len(files)} file(s) in {dest}")
    return 0 if files else 1


def convert_mirror(args: argparse.Namespace) -> int:
    """Re-encode archival scans already in the mirror as JPEG working copies.

    For a mirror pulled before conversion existed. ``pull-share`` converts on
    ingest now, but it cannot help with what is already on disk: re-fetching
    those pages is exactly the transfer the conversion is meant to avoid, and on
    a full disk it cannot happen at all.
    """
    from utils import images

    root = Path(args.root) if args.root else config.NEXTCLOUD_STAGING_DIR
    if not root.is_dir():
        print(f"Error: not a directory: {root}", file=sys.stderr)
        return 2

    quality = args.quality or images.WORKING_QUALITY
    sources = sorted(p for p in root.rglob("*") if p.is_file() and images.needs_conversion(p))
    if not sources:
        print(f"Nothing to convert under {root}")
        return 0

    before = sum(p.stat().st_size for p in sources)
    print(f"{len(sources)} file(s), {before / 1e9:.2f} GB under {root}")
    if args.dry_run:
        print("Dry run — nothing written.")
        return 0

    converted = failed = 0
    after = 0
    for index, src in enumerate(sources, start=1):
        size = src.stat().st_size
        dest = images.convert_file(src, quality=quality, remove_source=True)
        if dest is None:
            failed += 1
            after += size
            continue
        converted += 1
        after += dest.stat().st_size
        if index % 50 == 0:
            print(f"  {index}/{len(sources)} … {after / 1e9:.2f} GB written so far")

    freed = before - after
    print(f"{converted} converted, {failed} failed — "
          f"{before / 1e9:.2f} GB → {after / 1e9:.2f} GB, {freed / 1e9:.2f} GB freed")
    return 0 if not failed else 1


def atr_batch(args: argparse.Namespace) -> int:
    """Read every page under --source with every --models entry, model-major."""
    import atr_batch as batch

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        print("Error: --models is empty", file=sys.stderr)
        return 2

    # Before the share is walked and before anything is written (#472). The old
    # order spent 24 minutes enumerating, one page, and a vLLM cold start to
    # learn what this answers in 200 ms — and left a half-filled run directory
    # doing it. A model that does not fit is dropped and the others go on,
    # because the batch is model-major and one mistyped id never stopped the
    # rest either.
    # One probe, used twice: the preflight asks it whether each model fits, and
    # the provenance header asks it which weights each model is (#482). Taken
    # even with --no-preflight, which switches off the *gate* rather than the
    # question — a run whose model versions nobody can name afterwards is not
    # worth the 200 ms it would have cost to ask. Never fatal: a probe that
    # failed files its error and reads as "no answer" at both call sites.
    probe = None
    try:
        from mcp_atr.server import gateway_probe

        probe = gateway_probe()
    except Exception as exc:  # noqa: BLE001 — provenance is not worth a run
        print(f"Warning: no gateway probe ({exc}) — the run's model "
              "versions will read as unreported", file=sys.stderr)

    if not getattr(args, "no_preflight", False):
        checked = batch.preflight(models, probe=probe)
        for line in checked.lines():
            print(line, file=sys.stderr if checked.refused else sys.stdout)
        if checked.nothing_runs:
            # No run directory, no report: this run did not start.
            print("Error: no model fits on its card — nothing to run",
                  file=sys.stderr)
            return 1
        models = checked.runnable

    from utils import nextcloud

    remote = nextcloud.is_remote_source(args.source)
    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else None
    page_source = None

    if remote:
        # Reading the share directly, with no mirror and no mount. The cache is
        # not optional here: it is the only copy of a page that ever lands on
        # this disk, and without it every model's pass would re-download 25 MB a
        # page.
        if not cache_dir:
            print("Error: dav: sources need --cache-dir — it is where the pages "
                  "land", file=sys.stderr)
            return 2
        root = nextcloud.remote_source_root(args.source)
        source = f"{nextcloud.DAV_PREFIX}{root or '/'}"
        try:
            page_source = nextcloud.WebdavPageSource(
                cache_dir, root=root,
                listing_ttl=0 if args.no_listing_cache else None)
            paths = page_source.list_pages()
        except Exception as exc:  # noqa: BLE001 — a CLI reports, it does not traceback
            print(f"Error: cannot list the share: {exc}", file=sys.stderr)
            return 1
        try:
            pages = batch.pages_from_paths(paths, root, limit=args.limit,
                                           sample=args.sample, seed=args.seed)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
    else:
        source = Path(args.source).resolve()
        if not source.is_dir():
            print(f"Error: --source is not a directory: {source}", file=sys.stderr)
            return 2
        try:
            pages = batch.discover_pages(source, limit=args.limit, sample=args.sample,
                                         seed=args.seed)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    if not pages:
        print(f"Error: no page images under {source}", file=sys.stderr)
        return 1

    if getattr(args, "keys_from", None):
        try:
            keys = batch.read_keys(Path(args.keys_from).expanduser())
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        chosen, missing = batch.select_keys(pages, keys)
        # Named, never swallowed: a key list comes from somewhere else, and a run
        # that quietly dropped three of sixteen pages would be measured as if it
        # had read them.
        if missing:
            print(f"warning: {len(missing)} of {len(keys)} key(s) are not in this "
                  f"source: {', '.join(missing[:5])}"
                  + (f", … {len(missing) - 5} more" if len(missing) > 5 else ""),
                  file=sys.stderr)
        if not chosen:
            print(f"Error: none of the {len(keys)} key(s) in {args.keys_from} "
                  f"name a page under {source}", file=sys.stderr)
            return 1
        pages = chosen

    out_root = Path(args.out_root) if args.out_root else config.VLM_TEST_ROOT / args.run

    if args.dry_run:
        print(f"run       : {args.run}")
        print(f"source    : {source}")
        print(f"cache     : {cache_dir or '(none — pages are read where they are)'}")
        print(f"out       : {out_root}")
        print(f"gateway   : {config.ATR_GATEWAY_URL}")
        print(f"pages     : {len(pages)}"
              + (f"  (sampled at random, seed {args.seed})" if args.sample else ""))
        print(f"models    : {', '.join(models)}")
        print(f"calls     : {len(pages) * len(models)}")
        for page in pages[:5]:
            print(f"  - {page.key}  ({page.doc_id or 'root'})")
        if len(pages) > 5:
            print(f"  … {len(pages) - 5} more")
        return 0

    cache = page_source
    if cache is None and cache_dir:
        from utils.images import PageCache

        cache = PageCache(source, cache_dir)

    recognise = batch.gateway_recogniser()
    try:
        report = batch.run_batch(
            pages, models, args.run, out_root, recognise,
            retries=args.retries, concurrency=args.concurrency, cache=cache,
            probe=probe, source=str(source), gateway=config.ATR_GATEWAY_URL,
        )
    finally:
        close = getattr(recognise, "close", None)
        if close:
            close()

    path = batch.write_report(report)
    print(batch.format_report(report))
    if cache is not None:
        # The cold tier is named separately when it carried anything (#487):
        # folded into `hits` nobody could tell whether evicting to the share is
        # costing re-downloads or saving them.
        cold = getattr(cache, "cold_hits", 0)
        print(f"cache: {cache.hits} hit(s)"
              + (f", {cold} from the archive" if cold else "")
              + f", {cache.misses} fetched "
              f"({cache.source_bytes / 1e9:.1f} GB read from {source})")
    print(f"report: {path}")
    # A model that was abandoned is a failed run even though the others finished:
    # the exit code is what a wrapper script reads, and it must not say "fine".
    return 1 if report.any_aborted else 0


def harvest_gt(args: argparse.Namespace) -> int:
    """Download every hand-corrected page of a Transkribus collection.

    Only DONE/FINAL/GT: anything below is a machine's output, and scoring our
    readings against it would be scoring one model by another.
    """
    import transkribus as tk

    colid = args.collection or config._get("TRANSKRIBUS_COLLECTION", "")
    if not colid:
        print("Error: no collection — pass --collection", file=sys.stderr)
        return 2
    out_dir = Path(args.out).expanduser()
    try:
        session = __import__("requests").Session()
        sid = tk.login(session=session)
        files = tk.harvest_ground_truth(
            colid, sid, out_dir, session=session, limit=args.limit,
            statuses=(args.status or list(tk.GT_STATUSES)))
    except tk.TranskribusError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not files:
        print(f"no corrected pages in collection {colid} "
              f"(looked for {', '.join(args.status or tk.GT_STATUSES)})")
        return 1
    print(f"{len(files)} page(s) of ground truth in {out_dir}")
    return 0


def score_gt(args: argparse.Namespace) -> int:
    """Score a run's readings against hand-corrected pages.

    The only table in this project that measures quality rather than
    disagreement — because here one side is truth.
    """
    from gt_score import GroundTruthError, expand_gt_paths, score_run_dirs

    try:
        files = expand_gt_paths(args.gt)
        scored, unusable, report = score_run_dirs(
            files, [Path(d) for d in args.run_dir])
    except (GroundTruthError, NotADirectoryError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(report)
    if args.out:
        out = Path(args.out).expanduser()
        out.write_text(report, encoding="utf-8")
        print(f"\nreport: {out}")
    doubtful = [s for s in scored if not s.agreed_key]
    if getattr(args, "keys_out", None):
        keys = sorted({s.located for s in scored if s.located})
        if not keys:
            print("Error: no page was matched confidently enough to name a key — "
                  "nothing to write", file=sys.stderr)
            return 1
        dest = Path(args.keys_out).expanduser()
        dest.write_text(
            "# page keys of the ground-truth pages, for `atr-batch --keys-from`\n"
            f"# {len(keys)} of {len(scored)} scored page(s); the rest were not "
            f"matched confidently\n" + "\n".join(keys) + "\n", encoding="utf-8")
        print(f"\nkeys: {dest}  ({len(keys)} page(s))")
    return 1 if doubtful and len(doubtful) == len(scored) else 0


def upload_transkribus(args: argparse.Namespace) -> int:
    """Put the letters in the page cache into a Transkribus collection.

    `--dry-run` is the whole command for now: it logs in, says whether this API
    can see the collection, and prints the plan. Nothing moves until the upload
    endpoint is confirmed — see `transkribus.create_upload`.
    """
    import transkribus as tk

    raw = args.cache_dir or config.ATR_PAGE_CACHE or ""
    if not str(raw).strip():
        print("Error: no page cache — pass --cache-dir or set ATR_PAGE_CACHE. "
              "Defaulting to the working directory would inventory the checkout.",
              file=sys.stderr)
        return 2
    cache = Path(raw).expanduser()
    colid = args.collection or config._get("TRANSKRIBUS_COLLECTION", "")
    if not colid:
        print("Error: no collection — pass --collection or set "
              "TRANSKRIBUS_COLLECTION", file=sys.stderr)
        return 2
    try:
        session = __import__("requests").Session()
        sid = tk.login(session=session)
        print(f"logged in as {config._get('TRANSKRIBUS_USER', '')}")
        seen = tk.list_collections(sid, session=session)
        names = {str(c.get("colId")): str(c.get("colName", "")) for c in seen}
        print(f"collections visible to this API: {len(names)}")
        if str(colid) in names:
            print(f"  collection {colid} IS visible: {names[str(colid)]!r}")
        else:
            print(f"  collection {colid} is NOT among them. This API serves: "
                  + ", ".join(f"{k} ({v})" for k, v in sorted(names.items())[:20]))
            print("  → the collection lives on the newer platform; the upload "
                  "needs its API, not this one.")
            return 1
        plan = tk.plan_upload(cache, colid, sid, session=session, limit=args.limit)
        print()
        print(tk.format_plan(plan))
    except tk.TranskribusError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\n(dry run — nothing uploaded)")
        return 0
    print("Error: uploading is not wired yet — the endpoint is unconfirmed. "
          "Re-run with --dry-run.", file=sys.stderr)
    return 2


def compare_runs_cmd(args: argparse.Namespace) -> int:
    """Put two or more batch readings of the same pages side by side.

    Disagreement, not quality — see the module docstring. The point of the
    numbers is to say whether the readings are a field of equals (where #416
    found fusion winning) or one leader among weaker ones (where it lost).
    """
    from compare_runs import compare_run_dirs

    dirs = [Path(d).resolve() for d in args.run_dir]
    try:
        from compare_runs import SUBSTANTIAL_CHARS
        comparison, report = compare_run_dirs(
            dirs, no_merge_cer=args.no_merge_cer, worst=args.worst,
            min_chars=SUBSTANTIAL_CHARS if args.min_chars is None
            else args.min_chars)
    except (NotADirectoryError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not comparison.common:
        print("Error: the readings share no page keys — nothing to compare",
              file=sys.stderr)
        return 1
    print(report)
    if args.out:
        out = Path(args.out).resolve()
        out.write_text(report, encoding="utf-8")
        print(f"\nreport: {out}")
    return 0


def report_run(args: argparse.Namespace) -> int:
    """Rebuild a run's report from the results on disk."""
    import atr_batch as batch

    run_dir = Path(args.run_dir).resolve()
    try:
        report = batch.report_from_outputs(run_dir)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not report.models:
        print(f"Error: no model output under {run_dir}", file=sys.stderr)
        return 1
    print(batch.format_report(report))
    if args.dry_run:
        print("(dry run — report.md and report.json not written)")
        return 0
    print(f"report: {batch.write_report(report)}")
    return 0


def publish_batch(args: argparse.Namespace) -> int:
    """Propose a finished run directory to a GitHub repository as a pull request.

    A pull request rather than a push, because the edition repository is somebody
    else's and its maintainer decides what enters it. The commits land in our own
    fork, so this needs no write access to the target at all. ``--push`` is the
    old behaviour, for a repository we do own.
    """
    from utils.publish_github import publish_pr, publish_tree

    run_dir = Path(args.run_dir).resolve()
    repo = args.repo or config.GITHUB_TEXT_REPO
    path = args.path if args.path is not None else config.GITHUB_TEXT_PATH
    base = args.base or config.GITHUB_TEXT_BRANCH

    try:
        if args.push:
            branch = args.branch or base
            print(f"publishing {run_dir} → {repo}@{branch}:{path or '/'}")
            urls = publish_tree(
                run_dir, repo=repo, path_prefix=path, branch=branch,
                message=args.message or f"Add ATR outputs: {run_dir.name}",
            )
            for url in urls:
                print(url)
            return 0 if urls else 1

        fork = args.fork or config.GITHUB_TEXT_FORK or repo
        print(f"proposing {run_dir} → {repo}@{base}:{path or '/'}  (via {fork})")
        result = publish_pr(
            run_dir, repo=repo, path_prefix=path, head_repo=fork, base=base,
            branch=args.branch, message=args.message,
        )
    except Exception as exc:  # noqa: BLE001 — a CLI reports, it does not traceback
        print(f"Error: publish failed: {exc}", file=sys.stderr)
        return 1

    if not result["files"]:
        print(f"Error: nothing publishable under {run_dir}", file=sys.stderr)
        return 1
    print(f"{result['files']} file(s) on {result['head']} "
          f"in {len(result['commits'])} commit(s)")
    print(result["pull_request"] or "(no pull request URL returned)")
    return 0 if result["pull_request"] else 1


def batch_seed() -> int:
    """The default sampling seed, read without importing the batch runner.

    ``build_parser`` runs on every invocation including ``--help``; ``atr_batch``
    pulls in the recogniser and its clients. One constant is not worth that.
    """
    import atr_batch as batch

    return batch.SAMPLE_SEED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentic-historian",
        description="Agentic Historian — multi-agent HTR/OCR pipeline for historical manuscripts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run the full pipeline on one image")
    p_run.add_argument("file", help="Path to the manuscript image")
    p_run.add_argument("--lang", help="Language/script code (default: la)")
    p_run.set_defaults(func=run)

    p_pull = sub.add_parser("pull-share", help="Mirror a Nextcloud public share folder")
    p_pull.add_argument("--url", help="Share URL (default: NEXTCLOUD_SHARE_URL)")
    p_pull.add_argument("--password", help="Share password (default: NEXTCLOUD_SHARE_PASS)")
    p_pull.add_argument("--folder", help="Folder inside the share (default: NEXTCLOUD_REMOTE_DIR)")
    p_pull.add_argument("--dest", help="Local destination (default: NEXTCLOUD_STAGING_DIR/<folder>)")
    p_pull.add_argument("--limit", type=int, help="Only the first N files (smoke run)")
    p_pull.add_argument("--list", action="store_true",
                        help="List what is in the share and exit, without downloading")
    p_pull.set_defaults(func=pull_share)

    p_conv = sub.add_parser(
        "convert-mirror",
        help="Re-encode archival TIFFs already in the mirror as JPEG working copies")
    p_conv.add_argument("--root", help="Directory to walk (default: NEXTCLOUD_STAGING_DIR)")
    p_conv.add_argument("--quality", type=int, default=None,
                        help="JPEG quality (default: 85)")
    p_conv.add_argument("--dry-run", action="store_true",
                        help="Report what would be converted and exit")
    p_conv.set_defaults(func=convert_mirror)

    p_batch = sub.add_parser(
        "atr-batch",
        help="Read every page under --source with every model, one model at a time",
    )
    p_batch.add_argument("--source", required=True,
                         help="Directory of page images, or dav:<folder> to read "
                              "the Nextcloud share directly (needs --cache-dir)")
    p_batch.add_argument("--models", required=True,
                         help="Comma-separated gateway model ids (see GET /models)")
    p_batch.add_argument("--run", default="atr_batch", help="Run name = output subdirectory")
    p_batch.add_argument("--out-root", help="Output root (default: VLM_TEST_ROOT/<run>)")
    p_batch.add_argument("--limit", type=int, help="Only the first N pages (smoke run)")
    p_batch.add_argument("--sample", type=int,
                         help="N pages at random instead of the first N. Deterministic: "
                              "the same corpus and seed give the same pages, so a resumed "
                              "run reads what the first one did")
    p_batch.add_argument("--keys-from", metavar="FILE",
                         help="Only the pages whose key is listed in FILE, one per "
                              "line (# comments allowed). Written by `score-gt "
                              "--keys-out`: it is how a model reads the pages we "
                              "have ground truth for instead of the whole corpus")
    p_batch.add_argument("--seed", type=int, default=batch_seed(),
                         help="Seed for --sample (default: fixed, so samples are "
                              "reproducible across runs and machines)")
    p_batch.add_argument("--concurrency", type=int, default=config.ATR_BATCH_PAGE_CONCURRENCY,
                         help="Pages in flight per model (default 1 — the gateway already "
                              "parallelises the lines of one page)")
    p_batch.add_argument("--retries", type=int, default=config.ATR_BATCH_RETRIES,
                         help="Retries per page for timeouts and 5xx")
    p_batch.add_argument("--cache-dir", default=str(config.ATR_PAGE_CACHE)
                         if config.ATR_PAGE_CACHE else None,
                         help="Keep a local JPEG working copy of every page here. "
                              "Use it when --source is a mounted share: each page "
                              "then crosses the network once instead of once per "
                              "model. Omit for a corpus already on local disk.")
    p_batch.add_argument("--no-listing-cache", action="store_true",
                         help="Walk the share again instead of reusing a recent "
                              "listing. The walk is one PROPFIND per folder — 24 "
                              "minutes for the Lassberg share — so this is for "
                              "when you know pages were added.")
    p_batch.add_argument("--no-preflight", action="store_true",
                         help="Skip the card check and start anyway. For when the "
                              "arithmetic is wrong and the run should go — a check "
                              "without an exit gets removed at the first false "
                              "alarm instead of corrected (#472).")
    p_batch.add_argument("--dry-run", action="store_true",
                         help="Print the plan (pages, models, calls) and exit")
    p_batch.set_defaults(func=atr_batch)

    p_rep = sub.add_parser(
        "report-run",
        help="Rebuild a run's report.md/report.json from the results on disk")
    p_rep.add_argument("--run-dir", required=True, help="The run directory")
    p_rep.add_argument("--dry-run", action="store_true",
                       help="Print the report without overwriting the files")
    p_rep.set_defaults(func=report_run)

    p_gt = sub.add_parser(
        "harvest-gt",
        help="Download a Transkribus collection's hand-corrected pages as PAGE XML")
    p_gt.add_argument("--collection", help="Collection id, e.g. 36479")
    p_gt.add_argument("--out", required=True, help="Directory to write the XML into")
    p_gt.add_argument("--status", action="append",
                      help="Statuses to accept (default: DONE, FINAL, GT)")
    p_gt.add_argument("--limit", type=int, default=None,
                      help="Stop after N pages")
    p_gt.set_defaults(func=harvest_gt)

    p_sgt = sub.add_parser(
        "score-gt",
        help="Score a run's readings against hand-corrected pages (real CER)")
    p_sgt.add_argument("--gt", required=True, action="append",
                       help="A PAGE XML file or a directory of them; repeatable")
    p_sgt.add_argument("--run-dir", required=True, action="append",
                       help="A run directory; repeat for each reading to score")
    p_sgt.add_argument("--out", help="Also write the report to this path")
    p_sgt.add_argument("--keys-out", metavar="FILE",
                       help="Write the matched page keys here, one per line, for "
                            "`atr-batch --keys-from`. Only confident matches: a "
                            "doubtful one names a page the run does not have, and "
                            "feeding it to another model would read the wrong page")
    p_sgt.set_defaults(func=score_gt)

    p_tk = sub.add_parser(
        "upload-transkribus",
        help="Put the page cache's letters into a Transkribus collection")
    p_tk.add_argument("--collection",
                      help="Collection id (default: TRANSKRIBUS_COLLECTION)")
    p_tk.add_argument("--cache-dir",
                      help="Page cache to read (default: ATR_PAGE_CACHE)")
    p_tk.add_argument("--limit", type=int, default=None,
                      help="Plan only the first N letters")
    p_tk.add_argument("--dry-run", action="store_true",
                      help="Log in, report whether the collection is visible, "
                           "print the plan, upload nothing")
    p_tk.set_defaults(func=upload_transkribus)

    p_cmp = sub.add_parser(
        "compare-runs",
        help="Pairwise disagreement between two or more batch readings")
    p_cmp.add_argument("--run-dir", required=True, action="append",
                       help="A run directory; repeat for each run to compare")
    p_cmp.add_argument("--no-merge-cer", type=float, default=None,
                       help=f"Fusion's no-merge threshold "
                            f"(default: {config.ENSEMBLE_NO_MERGE_CER})")
    p_cmp.add_argument("--min-chars", type=int, default=None,
                       help="Pages shorter than this on any side are counted "
                            "but kept out of the distribution (default: 100)")
    p_cmp.add_argument("--worst", type=int, default=10,
                       help="How many widest-disagreement pages to name")
    p_cmp.add_argument("--out", help="Also write the report to this path")
    p_cmp.set_defaults(func=compare_runs_cmd)

    p_pub = sub.add_parser("publish-batch",
                           help="Propose a run directory to a GitHub repo as a PR")
    p_pub.add_argument("--run-dir", required=True, help="The run directory to publish")
    p_pub.add_argument("--repo", help=f"owner/name (default: {config.GITHUB_TEXT_REPO})")
    p_pub.add_argument("--path",
                       help="Path prefix inside the repo "
                            f"(default: {config.GITHUB_TEXT_PATH})")
    p_pub.add_argument("--base", help="Branch the pull request targets "
                                      f"(default: {config.GITHUB_TEXT_BRANCH})")
    p_pub.add_argument("--fork", help="Repository the commits land in "
                                      f"(default: {config.GITHUB_TEXT_FORK})")
    p_pub.add_argument("--branch", help="Branch to commit to "
                                        "(default: textrecognition/<run>). "
                                        "Re-running with the same one adds to the "
                                        "same pull request.")
    p_pub.add_argument("--push", action="store_true",
                       help="Commit straight to --branch instead of opening a "
                            "pull request. Needs write access to --repo.")
    p_pub.add_argument("--message", help="Commit message")
    p_pub.set_defaults(func=publish_batch)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
