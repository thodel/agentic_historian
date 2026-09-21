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
        )
    finally:
        close = getattr(recognise, "close", None)
        if close:
            close()

    path = batch.write_report(report)
    print(batch.format_report(report))
    if cache is not None:
        print(f"cache: {cache.hits} hit(s), {cache.misses} fetched "
              f"({cache.source_bytes / 1e9:.1f} GB read from {source})")
    print(f"report: {path}")
    # A model that was abandoned is a failed run even though the others finished:
    # the exit code is what a wrapper script reads, and it must not say "fine".
    return 1 if report.any_aborted else 0


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
