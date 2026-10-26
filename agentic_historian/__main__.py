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
import json
import sys
from datetime import datetime, timezone
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


#: How many unmatched keys `--keys-from` names in the warning. All of them, in
#: practice: the number is a cap against a key list from an unrelated corpus,
#: not a summary. Five used to be the limit, and on 2026-10-04 that hid which 35
#: ground-truth pages the share had stopped holding.
MISSING_KEYS_SHOWN = 100


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

    external = getattr(args, "via", "gateway") == "external"
    if external and not getattr(args, "no_preflight", False):
        # The gateway's /models cannot answer for a model that lives at an API:
        # asking it refused `gemini-3.8-flash` outright on 2026-10-10. The
        # external surface has its own models route, so the check moves rather
        # than disappearing — a model id nobody serves still makes every page
        # take the same 404, and here the successes are billed.
        import external_atr

        print("preflight:")
        verdicts = [external_atr.preflight(m) for m in models]
        for v in verdicts:
            print(v.line, file=sys.stderr if v.refused else sys.stdout)
        models = [v.model for v in verdicts if not v.refused]
        if not models:
            print("Error: the API serves none of these model ids — nothing to "
                  "run", file=sys.stderr)
            return 1
    elif not getattr(args, "no_preflight", False):
        checked = batch.preflight(models, probe=probe)
        for line in checked.lines():
            print(line, file=sys.stderr if checked.refused else sys.stdout)
        if checked.nothing_runs:
            # No run directory, no report: this run did not start. The two
            # reasons are different enough to name: a card that is full is a
            # "later", a model id the gateway does not have is a "never".
            why = ("no model fits on its card"
                   if not checked.unlisted
                   else "the gateway has none of these model ids"
                   if len(checked.unlisted) == len(checked.verdicts)
                   else "no model both exists on the gateway and fits")
            print(f"Error: {why} — nothing to run", file=sys.stderr)
            return 1
        models = checked.runnable

    from utils import nextcloud

    remote = nextcloud.is_remote_source(args.source)
    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else None
    page_source = None

    keys_file = getattr(args, "keys_from", None)
    if keys_file and args.limit is not None and args.sample is not None:
        print("Error: limit and sample are alternatives: first N, or N at random",
              file=sys.stderr)
        return 2
    # `--keys-from` filters and `--limit`/`--sample` cut, and the order is not a
    # detail: cutting first left the first three pages of a 6742-page corpus to be
    # matched against 276 ground-truth keys, matched none of them, and reported the
    # whole key list as absent from the source (#531). So when there is a key list,
    # discovery hands back the whole corpus and the cut happens after the filter.
    # `--exclude-from` is the same ordering problem from the other side: cutting
    # to 300 first and *then* removing everything already read would leave however
    # many of those 300 happened to be unread — a number nobody chose. So any
    # filter at all means discovery hands back the whole corpus.
    exclude_files = [f for f in (getattr(args, "exclude_from", None) or [])
                     if str(f).strip()]
    letters_file = getattr(args, "letters_from", None)
    filtering = bool(keys_file or exclude_files or letters_file)
    cut = None if filtering else args.limit
    pick = None if filtering else args.sample

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
            pages = batch.pages_from_paths(paths, root, limit=cut, sample=pick,
                                           seed=args.seed)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
    else:
        source = Path(args.source).resolve()
        if not source.is_dir():
            print(f"Error: --source is not a directory: {source}", file=sys.stderr)
            return 2
        try:
            pages = batch.discover_pages(source, limit=cut, sample=pick,
                                         seed=args.seed)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    if not pages:
        print(f"Error: no page images under {source}", file=sys.stderr)
        return 1

    # What the source holds, before any filter narrows it. `--exclude-from`
    # judges a key's absence against this and not against whatever is left by
    # then: otherwise the same list reports 35 missing keys over the corpus and
    # 235 after `--letters-from`, and only the first is about the source.
    discovered = [ref.key for ref in pages]

    if keys_file:
        try:
            keys = batch.read_keys(Path(keys_file).expanduser())
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        chosen, missing = batch.select_keys(pages, keys)
        # Named, never swallowed: a key list comes from somewhere else, and a run
        # that quietly dropped three of sixteen pages would be measured as if it
        # had read them.
        if missing:
            # All of them, up to a cap. Five was enough while a missing key meant
            # a typo; on 2026-10-04 it meant the share had stopped holding 35
            # pages we have ground truth for, and the list of *which* was the
            # thing to send to the people who keep the share. The cap is there so
            # a key list from an unrelated corpus cannot fill a terminal.
            shown = missing[:MISSING_KEYS_SHOWN]
            print(f"warning: {len(missing)} of {len(keys)} key(s) are not in this "
                  f"source:", file=sys.stderr)
            for key in shown:
                print(f"  - {key}", file=sys.stderr)
            if len(missing) > len(shown):
                print(f"  … {len(missing) - len(shown)} more (use --missing-out "
                      f"to write them all to a file)", file=sys.stderr)
        if getattr(args, "missing_out", None):
            # Written even when nothing is missing, and that is the point: a
            # dated file saying "all 276 resolved" is evidence, where a file that
            # is simply absent could also mean nobody looked.
            dest = Path(args.missing_out).expanduser()
            verdict = (f"{len(missing)} of {len(keys)} key(s) name no page here — "
                       f"the ground truth for them exists, the image does not"
                       if missing else
                       f"all {len(keys)} key(s) resolved; nothing is missing")
            dest.write_text(
                f"# page keys from {args.keys_from}\n"
                f"# checked against {source} on "
                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n"
                f"# {verdict}\n"
                + "".join(f"{k}\n" for k in missing), encoding="utf-8")
            print(f"missing keys: {dest}  ({len(missing)} page(s))",
                  file=sys.stderr)
        if not chosen:
            print(f"Error: none of the {len(keys)} key(s) in {args.keys_from} "
                  f"name a page under {source}", file=sys.stderr)
            return 1
        pages = chosen

    if letters_file:
        try:
            letter_ids = batch.read_keys(Path(letters_file).expanduser())
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        pages, no_pages = batch.select_letters(pages, letter_ids)
        print(f"letters   : {len(letter_ids) - len(no_pages)} of "
              f"{len(letter_ids)} letter(s) have pages here → {len(pages)} page(s)",
              file=sys.stderr)
        if no_pages:
            # Named for the same reason a missing page key is: the register is
            # somebody else's data and covers letters this corpus may not hold.
            shown = no_pages[:MISSING_KEYS_SHOWN]
            print(f"warning: {len(no_pages)} letter(s) have no page under this "
                  f"source:", file=sys.stderr)
            for lid in shown:
                print(f"  - {lid}", file=sys.stderr)
            if len(no_pages) > len(shown):
                print(f"  … {len(no_pages) - len(shown)} more", file=sys.stderr)
        if not pages:
            print(f"Error: none of the {len(letter_ids)} letter(s) in "
                  f"{letters_file} has a page under {source}", file=sys.stderr)
            return 1

    for path in exclude_files:
        try:
            skip = batch.read_keys(Path(path).expanduser())
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        before = len(pages)
        pages, absent = batch.drop_keys(pages, skip, present=discovered)
        print(f"excluded  : {before - len(pages)} of {before} page(s) named by "
              f"{path}", file=sys.stderr)
        if absent:
            # A key that excluded nothing is worth seeing. Here it means the
            # opposite of #535: not ground truth without an image, but a reading
            # of a page the source no longer offers.
            shown = absent[:MISSING_KEYS_SHOWN]
            print(f"warning: {len(absent)} of {len(skip)} excluded key(s) name no "
                  f"page under this source:", file=sys.stderr)
            for key in shown:
                print(f"  - {key}", file=sys.stderr)
            if len(absent) > len(shown):
                print(f"  … {len(absent) - len(shown)} more", file=sys.stderr)
        if not pages:
            print(f"Error: {path} excluded every page under {source}",
                  file=sys.stderr)
            return 1

    if filtering:
        pages = batch.narrow(pages, limit=args.limit, sample=args.sample,
                             seed=args.seed)

    if getattr(args, "keys_out", None):
        # The selection, written down. A run of "the 300 nobody has read" is only
        # repeatable if the 300 are a file: derive them again next week and a
        # share that gained or lost a page gives a different 300, silently.
        dest = Path(args.keys_out).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            f"# {len(pages)} page key(s) selected under {source}\n"
            f"# {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
            + (f", minus {len(exclude_files)} exclusion list(s)"
               if exclude_files else "")
            + "\n" + "".join(f"{ref.key}\n" for ref in pages), encoding="utf-8")
        print(f"keys      : {dest}  ({len(pages)} page(s))", file=sys.stderr)

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

    if getattr(args, "via", "gateway") == "external":
        import external_atr

        try:
            recognise = external_atr.openai_recogniser(
                prompt=args.prompt, structured=args.structured,
                reasoning=getattr(args, "reasoning", None))
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
    else:
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


def resolve_run_dir(raw: str) -> Path:
    """A run directory from a path *or* a bare run name.

    `$VLM_TEST_ROOT` is in `.env`, which dotenv loads for Python and bash never
    sees. So `--run-dir $VLM_TEST_ROOT/atr_corpus_qwen35_line` in an interactive
    shell expands to `/atr_corpus_qwen35_line` and fails — which is exactly what
    it did on 2026-10-02, with a message that named the collapsed path and left
    the reason to be guessed.

    An existing path wins, so nothing that worked before changes. Otherwise the
    argument is taken as a run name under `VLM_TEST_ROOT`, which is where every
    run this stack produces lives anyway.
    """
    p = Path(raw).expanduser()
    if p.is_dir():
        return p
    candidate = Path(config.VLM_TEST_ROOT) / str(raw).strip().lstrip("/")
    return candidate if candidate.is_dir() else p


def score_gt(args: argparse.Namespace) -> int:
    """Score a run's readings against hand-corrected pages.

    The only table in this project that measures quality rather than
    disagreement — because here one side is truth.
    """
    from gt_score import (GroundTruthError, expand_gt_paths, format_summary,
                          score_run_dirs)

    gt_paths = [g for g in (args.gt or []) if str(g).strip()] or [str(config.GT_ROOT)]
    run_dirs = [resolve_run_dir(d) for d in args.run_dir]
    try:
        files = expand_gt_paths(gt_paths)
        if getattr(args, "limit", None):
            files = files[:max(1, int(args.limit))]
        scored, unusable, report = score_run_dirs(files, run_dirs)
    except (GroundTruthError, NotADirectoryError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    writers = ""
    if getattr(args, "writer_agreement", False):
        import writer_check
        from gt_score import readings_from_run_dirs

        # The readings are re-read rather than threaded through score_run_dirs'
        # triple: it is some seventeen hundred small text files against seven
        # minutes of matching, and a fourth element in that return would have to
        # be carried by every caller that does not want it.
        writers = writer_check.format_comparison(
            writer_check.compare(scored, readings_from_run_dirs(run_dirs)))
        report = f"{report}\n\n{writers}\n"
    if args.out:
        # The full report to the file, the summary to stdout. 552 pages of
        # per-page sections are the record and not something to read in a
        # terminal — and when this runs as a job, a log *tail* is all a caller
        # gets back, so the answer has to be the last thing printed.
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        # And the same report as data, beside it (#689): the Markdown is for a
        # person, and from a session the per-page sections were unreadable.
        from gt_score import report_data
        data = {"runs": [str(d) for d in run_dirs],
                "ground_truth": gt_paths,
                "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **report_data(scored, unusable)}
        out_json = out.with_suffix(".json")
        out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(format_summary(scored, unusable))
        print(f"\nreport: {out}  ({len(report.splitlines())} lines)")
        print(f"report json: {out_json}  ({len(data['pages'])} page(s))")
        # Last, because a job hands a caller the *tail* of a log and this is the
        # answer the run was started for.
        if writers:
            print(f"\n{writers}")
    else:
        print(report)
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


def export_hf(args: argparse.Namespace) -> int:
    """Assemble the located ground-truth pages into a `pagexml-hf` export tree.

    Deliberately stops at the tree. The upload is `pagexml-hf`, which built every
    one of the fourteen dh-unibe/image-text_* datasets; a second converter here
    would mean a second column layout for the trainer to tolerate.
    """
    import hf_export as hf
    from gt_score import GroundTruthError, expand_gt_paths, score_run_dirs

    gt_paths = [g for g in (args.gt or []) if str(g).strip()] or [str(config.GT_ROOT)]
    try:
        files = expand_gt_paths(gt_paths)
        scored, unusable, _ = score_run_dirs(
            files, [resolve_run_dir(d) for d in args.run_dir])
    except (GroundTruthError, NotADirectoryError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    source = (Path(args.source).expanduser() if args.source
              else hf.default_source())
    if not source.is_dir():
        print(f"Error: --source is not a directory: {source}", file=sys.stderr)
        return 2
    archive = (Path(args.archive).expanduser() if args.archive
               else config.ATR_PAGE_CACHE_ARCHIVE)
    if archive and not Path(archive).is_dir():
        print(f"warning: cold tier {archive} is not a directory — only the pages "
              f"still in the cache will be found", file=sys.stderr)
        archive = None
    index = hf.page_index(source, archive=archive)
    if not index:
        print(f"Error: no page images under {source}"
              + (f" or {archive}" if archive else "")
              + ". The page cache evicts to a cold tier after two days (#487); "
                "pass --archive, or set ATR_PAGE_CACHE_ARCHIVE.", file=sys.stderr)
        return 1
    book = None
    folder = getattr(args, "register", None) or config.LASSBERG_REGISTER
    if folder:
        import register as reg

        try:
            book = reg.read_register(Path(folder))
            print(f"[register] {len(book.letters)} letter(s), "
                  f"{len(book.lassberg_letters)} by Laßberg, from {book.source}"
                  + (f" @{book.revision}" if book.revision else ""))
        except (NotADirectoryError, ValueError) as exc:
            # A missing register is a fallback to the inference, not a failure:
            # it covers 279 of some 3226 letter ids either way.
            print(f"warning: no letter register ({exc}) — the hand will be "
                  f"inferred from datelines alone", file=sys.stderr)
    plan = hf.plan(scored, index, require_geometry=not args.no_geometry_check,
                   survey_lines=args.survey_lines, register=book)
    print(hf.format_plan(plan))

    # The hand is inferred, so the counts above are a claim. Write the per-page
    # table every time, like `atr-batch --missing-out`: the run that would have
    # needed it is always the one that did not ask for it, and 211 rows cost
    # nothing. It names the letter each page inherited from, which is the only
    # way to disagree with the inheritance usefully.
    if getattr(args, "writers_out", None) and plan.entries:
        out = Path(args.writers_out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(hf.writers_table(plan), encoding="utf-8")
        print(f"\nthe hand of every page: {out}")

    # The same argument, for the pages that did not make it: the plan prints
    # counts and five names, and 43 geometry rejections cannot be diagnosed from
    # five. Written on a dry run too — a dry run is exactly when this is read.
    if getattr(args, "rejected_out", None) and plan.rejected:
        out = Path(args.rejected_out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(hf.rejected_table(plan), encoding="utf-8")
        print(f"every page left out: {out}")

    # When the index holds pages but none of the wanted keys, the counts alone
    # cannot say why — and guessing cost two rounds on 2026-10-02/03, first on a
    # PDF renderer and then on a cache eviction that turned out not to be it. The
    # answer is always in the two spellings side by side, so print them.
    missing = [r for r in plan.rejected if r.reason.startswith("no image")]
    if index and missing:
        print(f"\n{len(index)} image(s) are indexed under {source}, and none of "
              f"them answers to a wanted key. The two spellings:")
        print("\n  wanted:")
        for r in missing[:3]:
            print(f"    {r.reason.split(': ', 1)[1].rsplit(' is not', 1)[0]}")
        print("\n  indexed:")
        for key in sorted(index)[:3]:
            print(f"    {key}")
        print("\nIf those differ by a leading segment, --source is one level off "
              "the root the run used.")
    if unusable:
        print(f"\n{len(unusable)} ground-truth file(s) could not be scored at all "
              f"and never reached the plan.")

    if not plan.entries:
        print("\nError: nothing to export", file=sys.stderr)
        return 1
    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0

    if not args.out:
        print("Error: --out is required unless --dry-run", file=sys.stderr)
        return 2
    out_dir = Path(args.out).expanduser()
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"Error: {out_dir} is not empty — a half-written export mixed with "
              f"an older one would upload both", file=sys.stderr)
        return 2
    written = hf.build(plan, out_dir)
    print(f"\nexport: {written['out_dir']}  ({written['pages']} page(s))")
    print("\nUpload with pagexml-hf (it owns the parquet layout; see "
          "github.com/The-Flow-Project/pagexml-hf):\n"
          f"  pagexml-hf {out_dir} \\\n"
          f"    --repo-id {args.repo_id} --private --mode raw_xml")
    return 0


def export_run_hf(args: argparse.Namespace) -> int:
    """Assemble a run's machine readings into a `pagexml-hf` export tree.

    The sibling of `export-hf`, for the other kind of text this project holds:
    not a hand-corrected transcription but what a recogniser said about a page
    nobody has transcribed. One PAGE document is synthesised per page — the text
    in a single region covering the page, no line geometry, because a chat
    completion has none — and the tree is written for `--mode text`, which it
    records in its own manifest so the upload cannot be given the wrong mode.
    """
    import hf_export as hf
    import run_export as rx

    run_dir = resolve_run_dir(args.run_dir)
    try:
        pages = rx.read_run(run_dir, args.model)
    except (NotADirectoryError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if not pages:
        print(f"Error: {run_dir} holds no readings", file=sys.stderr)
        return 1

    source = (Path(args.source).expanduser() if args.source
              else rx.default_source())
    if not source.is_dir():
        print(f"Error: --source is not a directory: {source}", file=sys.stderr)
        return 2
    archive = (Path(args.archive).expanduser() if args.archive
               else rx.default_archive())
    if archive and not Path(archive).is_dir():
        print(f"warning: cold tier {archive} is not a directory — only the pages "
              f"still in the cache will be found", file=sys.stderr)
        archive = None
    index = hf.page_index(source, archive=archive)
    if not index:
        print(f"Error: no page images under {source}"
              + (f" or {archive}" if archive else "")
              + ". The page cache evicts to a cold tier after two days (#487); "
                "pass --archive, or set ATR_PAGE_CACHE_ARCHIVE.", file=sys.stderr)
        return 1

    skip = [] if args.skip_verdicts.strip() == "" else [
        v.strip() for v in args.skip_verdicts.split(",") if v.strip()]
    plan = rx.plan(pages, index, skip_verdicts=skip,
                   skip_truncated=not args.keep_truncated, run=run_dir.name)
    print(rx.format_plan(plan))
    if args.rejected_out:
        Path(args.rejected_out).expanduser().write_text(
            rx.rejected_table(plan), encoding="utf-8")
        print(f"\nrefused pages: {args.rejected_out}")

    if not plan.entries:
        print("\nError: nothing to export", file=sys.stderr)
        return 1
    if args.dry_run:
        print("\n(dry run — nothing written)")
        return 0
    if not args.out:
        print("Error: --out is required unless --dry-run", file=sys.stderr)
        return 2
    out_dir = Path(args.out).expanduser()
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"Error: {out_dir} is not empty — a half-written export mixed with "
              f"an older one would upload both", file=sys.stderr)
        return 2
    written = rx.build(plan, out_dir)
    print(f"\nexport: {written['out_dir']}  ({written['pages']} page(s), "
          f"mode {written['mode']})")
    print("\nUpload with pagexml-hf (it owns the parquet layout; see "
          "github.com/The-Flow-Project/pagexml-hf):\n"
          f"  pagexml-hf {out_dir} \\\n"
          f"    --repo-id {args.repo_id} --private --mode {written['mode']}\n"
          "or `upload-hf --tree`, which reads the mode out of the tree itself.")
    print("\nThis dataset is uncorrected machine output. Say so in its card.")
    return 0


def letter_register(args: argparse.Namespace) -> int:
    """What the edition's correspondence register covers, and whose letters.

    Read-only. Its output is a letter-id file for `atr-batch --letters-from`,
    which is the join that makes "every page Laßberg wrote" answerable: the hand
    of an untranscribed page cannot be read off the page, and the register
    records it per letter.
    """
    import register as reg

    folder = args.register or config.LASSBERG_REGISTER
    if not folder:
        print("Error: no register — pass --register DIR or set "
              "LASSBERG_REGISTER. It is the edition's data/letters, from "
              "`git clone --depth 1 https://github.com/michaelscho/lassberg`",
              file=sys.stderr)
        return 2
    try:
        book = reg.read_register(Path(folder))
    except (NotADirectoryError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(reg.format_register(book))

    if args.sender_gnd:
        ids = sorted(l.id for l in book.letters.values()
                     if l.sender_gnd == args.sender_gnd)
        who = f"GND {args.sender_gnd}"
    elif args.lassberg:
        ids = book.lassberg_letters
        who = f"Joseph von Laßberg (GND {reg.LASSBERG_GND})"
    else:
        ids = sorted(book.letters)
        who = "every sender"
    print(f"\nselected  : {len(ids)} letter(s) — {who}")
    if not args.out:
        return 0
    dest = Path(args.out).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        f"# {len(ids)} letter id(s) — {who}\n"
        f"# from {book.source}"
        + (f" @{book.revision}" if book.revision else "")
        + f", read {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC\n"
        + "".join(f"{i}\n" for i in ids), encoding="utf-8")
    print(f"letters   : {dest}")
    return 0


def hf_survey_lines() -> int:
    """The survey's default width, for the parser's help text."""
    import hf_export as hf

    return hf.SURVEY_LINES


def hf_default_repo() -> str:
    """The default dataset id, for the parser's help text."""
    import hf_export as hf

    return hf.DEFAULT_REPO_ID


def run_repo_id() -> str:
    """The machine-reading dataset's id, for the parser's help text."""
    import run_export as rx

    return rx.DEFAULT_REPO_ID


def run_skip_verdicts() -> tuple:
    """Which verdicts a run export refuses by default, for the help text."""
    import run_export as rx

    return rx.SKIP_VERDICTS


def upload_hf(args: argparse.Namespace) -> int:
    """Hand an export tree to `pagexml-hf`, which uploads it to the hub.

    Private by default, and the flag is passed rather than relied on: the
    difference between the two states is whether a collection of unpublished
    archival images is on the open web, and a default that lives in somebody
    else's tool can change between versions.
    """
    import subprocess

    import hf_export as hf

    plan = hf.inspect_upload(args.tree, args.repo_id or hf.DEFAULT_REPO_ID,
                             private=not args.public)
    print(hf.format_upload(plan))
    argv = hf.upload_argv(plan.tree, plan.repo_id, private=plan.private,
                          mode=plan.mode)
    print(f"\ncommand   : {' '.join(argv)}")
    # `pagexml-hf` replaces the dataset; what is on the hub and not in the tree
    # goes. Refused unless asked for, so a second volume cannot erase the first.
    hub = hf.hub_projects(plan.repo_id) if plan.ok else {"projects": []}
    lost = [] if hub.get("error") else hf.projects_lost(hub["projects"], plan.projects)
    if hub.get("error"):
        print(f"hub now   : not checked ({hub['error']})")
    else:
        print(f"hub now   : {', '.join(hub['projects']) or '— (new dataset)'}")
    if lost:
        print(f"would remove: {', '.join(lost)} — the upload replaces the dataset. "
              "Export these into the same tree, or pass --replace.")
    if hub.get("error") and not args.replace:
        print("\nError: could not see what the hub holds; an upload replaces it. "
              "Retry, or pass --replace.", file=sys.stderr)
        return 1
    if lost and not args.replace:
        print("\nError: not uploading — it would remove the projects above", file=sys.stderr)
        return 1
    if not plan.ok:
        print("\nError: not uploading — fix the problems above", file=sys.stderr)
        return 1
    if args.dry_run:
        print("\n(dry run — nothing uploaded)")
        return 0
    if not plan.private and not args.yes:
        print("\nError: --public needs --yes as well. A dataset of unpublished "
              "archival images on the open web is not a thing to do by typo.",
              file=sys.stderr)
        return 2
    print()
    import shutil

    env, work = hf.upload_env(plan.scratch, datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                              arrow_cache=plan.arrow_cache)
    for w in work:
        print(f"staging in {w} (removed afterwards)")
    try:
        result = subprocess.run(argv, env=env)
    finally:
        for w in work:
            shutil.rmtree(w, ignore_errors=True)
    if result.returncode != 0:
        print(f"\nError: pagexml-hf exited {result.returncode}", file=sys.stderr)
        return 1
    where = "private" if plan.private else "PUBLIC"
    print(f"\nuploaded {plan.pages} page(s) to {plan.repo_id} ({where})")
    hf.record_upload(plan.tree, plan.repo_id, private=plan.private)
    # And what the hub holds now, from the hub (#691): the check that ran over
    # a second channel on 2026-10-22 — 281 rows? four columns? private?
    print(hf.format_hub_state(hf.hub_state(plan.repo_id)))
    return 0


def split_tree(args: argparse.Namespace) -> int:
    """Hold out whole objects of an export tree (#711). Dry run by default."""
    import tree_split as ts

    try:
        if args.undo:
            result = ts.undo(Path(args.tree), dry_run=not args.apply,
                             allow_after_upload=args.allow_after_upload)
        else:
            result = ts.split(Path(args.tree), fraction=args.fraction, seed=args.seed,
                              force_eval=[f for f in (args.force_eval or "").split(",") if f],
                              dry_run=not args.apply,
                              allow_after_upload=args.allow_after_upload)
    except ts.SplitError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(ts.format_result(result))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


def oai_gt_import(args: argparse.Namespace) -> int:
    """A named OAI-PMH source's images + ALTO as a ground-truth tree (#712)."""
    import oai_gt

    root = Path(args.root) if args.root else config.ATR_DATASETS_DIR
    if not root:
        print("Error: no root — set ATR_DATASETS_DIR or pass --root", file=sys.stderr)
        return 2
    try:
        src = oai_gt.source(args.source)
        if args.step == "inventory":
            result = oai_gt.inventory(src, Path(root))
        else:
            result = oai_gt.build(src, Path(root), dry_run=args.dry_run, limit=args.limit,
                                  workers=args.workers, progress=lambda m: print(m, flush=True))
    except oai_gt.OaiGtError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(oai_gt.format_result(result))
    if args.step == "build" and not args.dry_run:
        print(f"\nnext: split-tree --tree {result['tree']} (dry run), then upload-hf --tree "
              f"{result['tree']} --dry-run")
    return 1 if result.get("failures") else 0


def train_spec_cmd(args: argparse.Namespace) -> int:
    """A spec from training/specs/, pinned, as a request or a job (#715)."""
    import train_spec as tsp

    names = [n.strip().removesuffix(".json") for n in args.spec.split(",") if n.strip()]
    if len(names) > 1 and not (args.action == "ubelix" or args.write_request):
        print("Error: several specs only with --action ubelix or --write-request", file=sys.stderr)
        return 2
    try:
        all_prepared = [(n, tsp.prepare(tsp.load(n))) for n in names]
    except tsp.SpecError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.write_request:
        for n, prep in all_prepared:
            try:
                print(f"wrote {tsp.write_request(n, prep)}")
            except tsp.SpecError as exc:
                print(f"Error: {n}: {exc}", file=sys.stderr)
                return 1
        print("commit training/requests/ and merge — UBELIX fetches them from main")
        return 0
    if args.action == "ubelix":
        ok = True
        for n, prep in all_prepared:
            print(tsp.format_prepared(n, prep) + "\n")
            ok = ok and prep["ok"]
        states = {n: tsp.request_state(n, prep) for n, prep in all_prepared}
        print("\n".join(tsp.ubelix_commands([(n, prep["body"]) for n, prep in all_prepared], states)))
        return 0 if ok else 1
    prepared = all_prepared[0][1]
    args.spec = names[0]
    body = prepared["body"]
    if args.body_only:
        if not prepared["ok"]:
            print("Error: " + "; ".join(prepared["problems"]), file=sys.stderr)
            return 1
        print(json.dumps(body, ensure_ascii=False, indent=1))
        return 0
    print(tsp.format_prepared(args.spec, prepared))
    if args.action == "request":
        print("\n" + json.dumps(body, ensure_ascii=False, indent=1))
        print("\n(request — nothing sent)")
        return 0 if prepared["ok"] else 1
    if not prepared["ok"]:
        print("\nError: not sending — fix the problems above", file=sys.stderr)
        return 1
    from agent_a.training_client import TrainingClient

    with TrainingClient() as tc:
        mine = [j for j in tc.list_jobs(limit=50) if j.model_id == body["model_id"]]
        if args.action == "submit":
            if mine:
                print(f"\nError: {body['model_id']} already has a job ({mine[0].id}, "
                      f"{mine[0].status.value}) — a second one for the same model_id is "
                      f"not sent; change model_id in the spec", file=sys.stderr)
                return 2
            job = tc.submit(body)
            # The 202 answer does not always carry the id (15.10.); the list does.
            job_id = job.id or next((j.id for j in tc.list_jobs(limit=20)
                                     if j.model_id == body["model_id"]), "")
            print(f"\nsubmitted: {job_id or '?'}  status={job.status.value}")
            return 0
        job = tc.get_job(args.job_id) if args.job_id else (mine[0] if mine else None)
        if job is None:
            print(f"\nno job for {body['model_id']}")
            return 1
        print(f"\n{job.id}  status={job.status.value}  stage={job.stage}  cer={job.cer}")
        if job.error:
            print(f"error: {job.error}")
        prog = {k: job.progress.get(k) for k in ("epoch", "epochs", "val_accuracy")
                if (job.progress or {}).get(k) is not None}
        if prog:
            print("progress: " + ", ".join(f"{k}={v}" for k, v in prog.items()))
    return 0


def upload_hf_card(args: argparse.Namespace) -> int:
    """Put a dataset card from `docs/` on the hub as README.md (#691).

    Before the upload the card's front matter is replaced by the one the hub
    README carries — the block `pagexml-hf` wrote with the measured sizes — and
    the spliced card is written back to `docs/`, so the card in the repository
    is the card on the hub. A dry run prints the splice and writes nothing.
    """
    import hf_export as hf

    try:
        card = hf.resolve_card(args.card)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    repo_id = args.repo_id or hf.DEFAULT_REPO_ID
    if not hf._REPO_ID_RE.match(repo_id):
        print(f"Error: repo id {repo_id!r} is not owner/name", file=sys.stderr)
        return 2
    if not hf.hub_token():
        print("Error: no Hugging Face token — set HF_TOKEN in the environment "
              "the upload runs in", file=sys.stderr)
        return 2
    readme = hf.hub_readme(repo_id)
    if readme is None:
        print(f"Error: {repo_id} has no README on the hub (or is not reachable "
              f"with this token) — upload the dataset first; its sizes are "
              f"measured there", file=sys.stderr)
        return 1
    try:
        spliced = hf.splice_frontmatter(card.read_text(encoding="utf-8"), readme)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    fm, _ = hf.split_frontmatter(spliced)
    print(f"card      : {card}")
    print(f"repo      : {repo_id}")
    print("frontmatter (from the hub, verbatim):")
    print("\n".join("  " + ln for ln in (fm or "").splitlines()))
    changed = spliced != card.read_text(encoding="utf-8")
    print(f"card text : {'updated with the hub front matter' if changed else 'already carries it'}")
    if args.dry_run:
        print("\n(dry run — nothing written, nothing uploaded)")
        return 0
    if changed:
        card.write_text(spliced, encoding="utf-8")
    try:
        how = hf.upload_card(repo_id, card)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"\nuploaded {card.name} as README.md to {repo_id} (via {how})")
    print(hf.format_hub_state(hf.hub_state(repo_id)))
    return 0


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
        unassigned = None
        if args.by_letter:
            import register as reg

            folder = args.register or config.LASSBERG_REGISTER
            if not folder:
                print("Error: --by-letter needs the edition's register — pass "
                      "--register or set LASSBERG_REGISTER", file=sys.stderr)
                return 2
            registry = reg.read_register(Path(folder))
            source = Path(args.source).expanduser() if args.source else cache
            plan, unassigned = tk.plan_letters(source, colid, registry, sid,
                                               session=session, limit=args.limit,
                                               only=args.letter)
            print(f"register: {len(registry.letters)} letter(s) with a sender, "
                  f"{registry.source}@{registry.revision[:12] or '?'}")
        else:
            plan = tk.plan_upload(cache, colid, sid, session=session, limit=args.limit)
        print()
        print(tk.format_plan(plan))
        if unassigned is not None:
            print(tk.format_unassigned(unassigned))
    except (tk.TranskribusError, NotADirectoryError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\n(dry run — nothing uploaded)")
        return 0
    if not args.by_letter:
        print("Error: uploading folder-wise is not offered — the edition's unit is "
              "the letter; use --by-letter", file=sys.stderr)
        return 2
    if not args.letter and not args.all:
        print("Error: the first real upload is ONE letter, checked in the interface "
              "before the rest: pass --letter lassberg-letter-NNNN, then --all",
              file=sys.stderr)
        return 2
    if args.all and not args.letter and not tk.first_letter_confirmed(plan):
        print("Error: --all before a first letter is in the collection — upload one "
              "with --letter, look at it in the interface, then --all", file=sys.stderr)
        return 2
    if not plan.letters:
        print("nothing to upload — every planned letter is already in the collection")
        return 0
    done = 0
    for letter in plan.letters:
        try:
            result = tk.upload_letter(colid, letter, sid, session=session,
                                      progress=lambda l, i, n: print(f"  {l.title}: page {i}/{n}", end="\r"))
        except tk.TranskribusError as exc:
            print(f"\nError: {exc}", file=sys.stderr)
            print(f"stopped after {done} letter(s); the collection may hold a partial "
                  f"upload of {letter.title!r} — check it there before re-running",
                  file=sys.stderr)
            return 1
        done += 1
        tk.log_upload(colid, letter, result, mode="letter" if args.letter else "all")
        print(f"  {letter.title}: {result['pages']} page(s) sent, upload {result['upload_id']}"
              + (f", job {result['job_id']}" if result.get("job_id") else "") + "        ")
    print(f"\nuploaded {done} letter(s) into collection {colid}. The server ingests "
          f"them as jobs; look at the collection in the interface before the next batch.")
    return 0


def compare_runs_cmd(args: argparse.Namespace) -> int:
    """Put two or more batch readings of the same pages side by side.

    Disagreement, not quality — see the module docstring. The point of the
    numbers is to say whether the readings are a field of equals (where #416
    found fusion winning) or one leader among weaker ones (where it lost).
    """
    from compare_runs import compare_run_dirs

    dirs = [resolve_run_dir(d).resolve() for d in args.run_dir]
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
    if args.keys_out:
        # Written even under --dry-run: the flag asks for a selection, not for a
        # report, and refusing to write it because the report was not written
        # would make the dry run the only way to get the numbers and the only
        # way not to get the list (#616).
        keys = report.flagged_keys
        out = Path(args.keys_out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(keys) + ("\n" if keys else ""), encoding="utf-8")
        print(f"\nflagged: {len(keys)} page(s) → {out}")
    if args.dry_run:
        print("(dry run — report.md and report.json not written)")
        return 0
    print(f"report: {batch.write_report(report)}")
    return 0


def merge_runs_cmd(args: argparse.Namespace) -> int:
    """Assemble one run from several attempts at the same pages (#624)."""
    import atr_batch as batch

    run_dirs = [Path(d).resolve() for d in args.run_dir]
    out = Path(args.out).resolve()
    if out in run_dirs:
        print(f"Error: --out is also an input run: {out}", file=sys.stderr)
        return 2
    try:
        report = batch.merge_runs(run_dirs, out, dry_run=args.dry_run)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(batch.format_merge_report(report))
    if args.dry_run:
        print(f"(dry run — nothing written to {out})")
        return 0
    print(f"merged: {out}")
    print(f"report: {batch.write_report(batch.report_from_outputs(out))}")
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


def batch(args: argparse.Namespace) -> int:
    """Process a folder or a folder of orders with N workers (R2, #392).

    A subcommand of this CLI rather than the ``python -m agentic_historian.batch``
    the issue spelled: the package has no ``__init__.py`` and the flat
    ``import config`` convention only works once this directory is on the path,
    which ``__main__.py`` does once at the top. A second ``-m`` entry point
    would mean a second copy of that fix and a second ``--help`` for anyone to
    find. Twelve subcommands already live here, for the reason this module's
    docstring gives: each is restartable on its own.
    """
    import batch_runner
    import corpus_manifest

    root = Path(args.folder).resolve()
    run_id = args.run or root.name

    # #394 turned #392's refusal into a working path. The runner calls the
    # pipeline with publish=False and commits N documents at a time itself, so
    # ENABLE_GITHUB_PUBLISH being on is now what makes publishing happen rather
    # than what makes it pathological. `--allow-per-doc-publish` is kept for the
    # one case it still serves: letting the pipeline publish each document as it
    # used to, at one index rebuild per document.
    if config.ENABLE_GITHUB_PUBLISH:
        every = (args.publish_every if args.publish_every is not None
                 else config.BATCH_PUBLISH_EVERY)
        print("publishing: "
              + ("one commit per document (--allow-per-doc-publish)"
                 if args.allow_per_doc_publish else
                 f"every {every} document(s), one commit each" if every > 0
                 else "once at the end of the run, in one commit"))
    elif args.publish_every:
        print("--publish-every has no effect while ENABLE_GITHUB_PUBLISH is off.")

    try:
        source = batch_runner.inspect_source(root, mode=args.mode)
    except batch_runner.AmbiguousSource as e:
        print(f"{e}")
        return 2
    except RuntimeError as e:
        print(f"{e}")
        return 1

    if args.requeue:
        back = corpus_manifest.requeue(run_id)
        print(f"requeued {len(back)} document(s) that had failed or been given up on")

    print(f"{run_id}: {len(source.doc_ids)} document(s), {source.pages} page(s), "
          f"mode={source.mode} ({source.why})")
    if args.dry_run:
        corpus_manifest.register(run_id, source.doc_ids, source=str(root),
                                 label=source.mode)
        prog = corpus_manifest.progress(run_id)
        print(f"registered only (--dry-run): {prog.pending} pending, "
              f"{prog.done} done, {prog.failed} failed, {prog.dead} dead")
        for doc_id in source.doc_ids[:20]:
            print(f"  {doc_id} ({len(source.paths[doc_id])}p)")
        if len(source.doc_ids) > 20:
            print(f"  … and {len(source.doc_ids) - 20} more")
        return 0

    try:
        summary = batch_runner.run_batch(
            run_id, source, workers=args.workers, max_attempts=args.max_attempts,
            publish_every=args.publish_every,
            per_doc_publish=args.allow_per_doc_publish,
            restart_engines=args.restart_engines,
            announce=_batch_announce())
    except batch_runner.EnginesDown as e:
        # Exit 3, not 1: a run that was refused before it claimed anything is a
        # different thing from a run that produced dead letters, and a wrapper
        # script should be able to retry the one and not the other.
        print(f"{e}")
        return 3
    prog = summary.progress
    print(f"{run_id}: {prog.done} done, {prog.failed} failed, {prog.dead} dead "
          f"in {summary.seconds / 60:.1f} min")
    # Failed is not dead: a re-run picks those up. Only a dead letter is a
    # non-zero exit, because only it needs somebody to look.
    return 1 if prog.dead else 0


def _batch_announce():
    """Post progress to Discord when a channel and a token are configured.

    A one-shot REST call, not a client: this is a CLI with no event loop, and
    the alternative — logging into the gateway to say one sentence — is a
    second bot presence for every batch. Failure to post never fails the batch.
    """
    channel = getattr(config, "VERBOSE_PROGRESS_CHANNEL_ID", None)
    token = getattr(config, "DISCORD_BOT_TOKEN", "")
    if not channel or not token:
        return None

    def _say(text: str) -> None:
        from loguru import logger
        try:
            import requests
            requests.post(
                f"https://discord.com/api/v10/channels/{channel}/messages",
                headers={"Authorization": f"Bot {token}"},
                json={"content": text[:1900]}, timeout=15)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[batch] could not post progress: {e}")
        logger.info(f"[batch] {text}")

    return _say


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

    p_corpus = sub.add_parser(
        "batch",
        help="Process a folder (or a folder of orders) with N workers, resumable")
    p_corpus.add_argument("folder", help="Folder of pages, or of order subfolders")
    p_corpus.add_argument("--run", help="Manifest run id (default: the folder name)")
    p_corpus.add_argument("--workers", type=int, default=config.BATCH_WORKERS,
                          help="Documents in flight at once")
    p_corpus.add_argument("--mode", choices=["pages", "orders"], default=None,
                          help="One document per image, or per subfolder "
                               "(default: derived from the folder)")
    p_corpus.add_argument("--max-attempts", type=int,
                          default=config.BATCH_MAX_ATTEMPTS,
                          help="Attempts before a document is given up on")
    p_corpus.add_argument("--requeue", action="store_true",
                          help="Put failed and given-up documents back first")
    p_corpus.add_argument("--dry-run", action="store_true",
                          help="Register the manifest and print the plan only")
    p_corpus.add_argument("--publish-every", type=int, default=None,
                          metavar="N",
                          help="Commit N documents' outputs at a time "
                               "(default BATCH_PUBLISH_EVERY; 0 = once at the "
                               "end, the fewest index rebuilds)")
    p_corpus.add_argument("--allow-per-doc-publish", action="store_true",
                          help="Let the pipeline publish each document as it "
                               "goes — one commit and one index rebuild each")
    p_corpus.add_argument("--restart-engines", action="store_true",
                          help="If a planned engine does not answer, ask the "
                               "gateway to restart it (once per engine) and "
                               "start only if it comes back. Without this the "
                               "run is refused and says which engine is down — "
                               "exit 3. Never silent either way: a process that "
                               "quietly restarts services hides the failures "
                               "somebody needs to see (#599)")
    p_corpus.set_defaults(func=batch)

    p_batch = sub.add_parser(
        "atr-batch",
        help="Read every page under --source with every model, one model at a time",
    )
    p_batch.add_argument("--source", required=True,
                         help="Directory of page images, or dav:<folder> to read "
                              "the Nextcloud share directly (needs --cache-dir)")
    p_batch.add_argument("--via", choices=("gateway", "external"),
                         default="gateway",
                         help="Who reads the pages. `gateway` is the ATR "
                              "gateway on idhefix (kraken, TrOCR, party, vLLM) "
                              "and returns line geometry. `external` is an "
                              "OpenAI-compatible API (GEMINI_BASE_URL) and "
                              "returns page text and no lines at all, so its "
                              "readings can be compared and published but "
                              "cannot train a line model")
    p_batch.add_argument("--prompt", default="lassberg_atr.md",
                         help="Which versioned prompt the external reader uses "
                              "— a name under agentic_historian/prompts, never "
                              "a path. Its digest is recorded with every page, "
                              "because two readings under different prompts are "
                              "different measurements")
    p_batch.add_argument("--reasoning", default=None,
                         help="How much the external model may think before it "
                              "answers (default: GEMINI_REASONING). Reasoning "
                              "tokens are billed as output and spent against the "
                              "token ceiling, so for a transcription they are "
                              "budget going nowhere — and the page they eat is "
                              "the page that stops mid-sentence. \"\" sends "
                              "nothing and leaves the model's own default")
    p_batch.add_argument("--structured", action="store_true",
                         help="Ask the external reader for `diplomatic` and "
                              "`normalized` as JSON. Without it the prompt's "
                              "rule about a normalized field has nowhere to "
                              "land, which reads as 'do not expand'")
    p_batch.add_argument("--letters-from",
                         help="A file of letter ids (lassberg-letter-NNNN), one "
                              "per line — read every page of those letters. The "
                              "join the edition's correspondence register makes "
                              "possible: it names a sender per letter, and "
                              "selecting untranscribed pages by hand is "
                              "otherwise circular. `letter-register --out` "
                              "writes such a file")
    p_batch.add_argument("--exclude-from", action="append",
                         help="A file of page keys to leave out, one per line — "
                              "repeat for several. This is how 'the pages nobody "
                              "has read yet' is selected: the corpus minus the "
                              "ground-truth keys (`score-gt --keys-out`) minus an "
                              "earlier run's keys. Applied before --limit, which "
                              "is the only order that gives the number you asked "
                              "for")
    p_batch.add_argument("--keys-out",
                         help="Write the selected page keys here. A selection "
                              "derived again next week is a different selection "
                              "if the share gained or lost a page; a file is not")
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
    p_batch.add_argument("--cache-dir", default=str(config.page_cache_dir()),
                         help="Keep a local JPEG working copy of every page here. "
                              "Use it when --source is a mounted share: each page "
                              "then crosses the network once instead of once per "
                              "model. Omit for a corpus already on local disk.")
    p_batch.add_argument(
        "--missing-out", metavar="PATH",
        help="write the keys this source does not have to PATH, one per line "
             "(with --keys-from). The list to send to whoever keeps the corpus: "
             "these pages have hand-corrected text and no image.")
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
    p_rep.add_argument("--keys-out",
                       help="Write the keys of the pages the report flags "
                            "(empty, padded, cut off) to this file, one per "
                            "line — the selection `atr-batch --keys-from` takes")
    p_rep.set_defaults(func=report_run)

    p_merge = sub.add_parser(
        "merge-runs",
        help="One run from several attempts at the same pages, best verdict wins")
    p_merge.add_argument("--run-dir", action="append", required=True,
                         metavar="DIR",
                         help="A run directory; repeat it. ORDER MATTERS: "
                              "earlier is preferred where the verdicts tie")
    p_merge.add_argument("--out", required=True,
                         help="The run directory to write (must not be an input)")
    p_merge.add_argument("--dry-run", action="store_true",
                         help="Say what would be taken from where, write nothing")
    p_merge.set_defaults(func=merge_runs_cmd)

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
    p_sgt.add_argument("--gt", action="append",
                       help="A PAGE XML file or a directory of them; repeatable. "
                            "Default: GT_ROOT from the config, so this can be "
                            "left out entirely")
    p_sgt.add_argument("--run-dir", required=True, action="append",
                       help="A run directory, or just a run name under "
                            "VLM_TEST_ROOT; repeat for each reading to score")
    p_sgt.add_argument("--out",
                       help="Write the full report here and print only the "
                            "summary — the counts and the comparison table. "
                            "Without it the whole report goes to stdout, which "
                            "for 552 pages is some thousands of lines")
    p_sgt.add_argument("--limit", type=int, metavar="N",
                       help="Score only the first N ground-truth files, in path "
                            "order. For a quick check that the matching works "
                            "before spending a quarter of an hour on all of them")
    p_sgt.add_argument("--keys-out", metavar="FILE", nargs="?",
                       const=str(Path(config.GT_ROOT) / "gt-keys.txt"),
                       help="Write the matched page keys here, one per line, for "
                            "`atr-batch --keys-from`. Only confident matches: a "
                            "doubtful one names a page the run does not have, and "
                            "feeding it to another model would read the wrong page. "
                            "Bare `--keys-out` writes GT_ROOT/gt-keys.txt, which "
                            "survives a reboot — /tmp does not, and a reboot ate "
                            "the first list after sixteen minutes of work")
    p_sgt.add_argument("--writer-agreement", action="store_true",
                       help="Also measure whether a machine reading is enough to "
                            "tell whose hand a page is in: `writer_of` on each "
                            "reading against `writer_of` on the hand-corrected "
                            "text. The question behind selecting untranscribed "
                            "pages by writer, which cannot be answered from the "
                            "pages themselves")
    p_sgt.set_defaults(func=score_gt)

    p_reg = sub.add_parser(
        "letter-register",
        help="What the edition's correspondence register covers, and whose "
             "letters — and write a letter-id file for --letters-from")
    p_reg.add_argument("--register", default=None,
                       help="The edition's data/letters (default: "
                            "LASSBERG_REGISTER)")
    p_reg.add_argument("--lassberg", action="store_true",
                       help="Select the letters Laßberg himself sent")
    p_reg.add_argument("--sender-gnd", default=None,
                       help="Select one sender by GND instead. A name is a "
                            "label and an identifier is not: 'Laßberg' is also "
                            "written 'Lassberg' and 'Laspberg'")
    p_reg.add_argument("--out", help="Write the selected letter ids here, for "
                                     "`atr-batch --letters-from`")
    p_reg.set_defaults(func=letter_register)

    p_hf = sub.add_parser(
        "export-hf",
        help="Assemble the located ground-truth pages into a pagexml-hf export tree")
    p_hf.add_argument("--run-dir", required=True, action="append",
                      help="A run directory, or a run name under VLM_TEST_ROOT — "
                           "the run whose page keys the ground truth is matched to")
    p_hf.add_argument("--source",
                      help="Corpus directory the images come from — the page "
                           "cache, the mount or the mirror. Default: the cache "
                           "the runner itself would use, so this can be left "
                           "out. Do not pass \"$ATR_PAGE_CACHE\": a shell "
                           "variable that is set but not exported names a "
                           "directory Python cannot see in its environment")
    p_hf.add_argument("--gt", action="append",
                      help="PAGE XML file or directory; default GT_ROOT")
    p_hf.add_argument("--archive",
                      help="The page cache's cold tier, searched when a page is "
                           "no longer in the cache (default: "
                           "ATR_PAGE_CACHE_ARCHIVE). The cache evicts after two "
                           "days, so for anything but a run read today this is "
                           "where most pages are")
    p_hf.add_argument("--out", help="Where to write the export tree")
    p_hf.add_argument("--register", default=None,
                      help="The edition's data/letters (default: "
                           "LASSBERG_REGISTER). Where it names a letter's "
                           "sender, that decides the hand and the dateline "
                           "inference is only the fallback")
    p_hf.add_argument("--survey-lines", type=int, default=hf_survey_lines(),
                      help="How many lines of a page the dateline survey shows. "
                           "More than the rule reads, deliberately: the survey "
                           "exists to see what the rule cannot, and a place it "
                           "reveals on the third line still decides nothing")
    p_hf.add_argument("--writers-out",
                      help="Write one row per exported page — its letter, its "
                           "hand, and whether that came from its own dateline or "
                           "was inherited. Written on a dry run too, because the "
                           "plan's counts are a claim about an inference and this "
                           "is what makes it checkable")
    p_hf.add_argument("--rejected-out",
                      help="Write one row per page left out — its reason, and for "
                           "a geometry rejection both sizes and what the pair "
                           "says. The plan names five of them, which is a count "
                           "and not a task: diagnosing them needs every key")
    p_hf.add_argument("--repo-id", default="dh-unibe/image-text_lassberg-correspondence_xix",
                      help="Dataset repo for the printed pagexml-hf command")
    p_hf.add_argument("--dry-run", action="store_true",
                      help="Print the plan and the project split, write nothing")
    p_hf.add_argument("--no-geometry-check", action="store_true",
                      help="Export pages whose XML geometry disagrees with their "
                           "image. Off by default: mismatched line polygons crop "
                           "the wrong strip of every page and nothing downstream "
                           "would say so")
    p_hf.set_defaults(func=export_hf)

    p_rhf = sub.add_parser(
        "export-run-hf",
        help="Assemble a run's machine readings into a pagexml-hf export tree "
             "(image + page text, no line geometry)")
    p_rhf.add_argument("--run-dir", required=True,
                       help="The run directory, or a run name under "
                            "VLM_TEST_ROOT, whose readings are exported")
    p_rhf.add_argument("--model", default=None,
                       help="Only this model's directory. Default: every model "
                            "in the run, one project each")
    p_rhf.add_argument("--source",
                       help="Corpus directory the images come from. Default: the "
                            "cache the runner itself would use. Do not pass "
                            "\"$ATR_PAGE_CACHE\": a shell variable that is set "
                            "but not exported names a directory Python cannot "
                            "see in its environment")
    p_rhf.add_argument("--archive",
                       help="The page cache's cold tier (default: "
                            "ATR_PAGE_CACHE_ARCHIVE). The cache evicts after two "
                            "days, so for anything but a run read today this is "
                            "where most pages are")
    p_rhf.add_argument("--out", help="Where to write the export tree")
    p_rhf.add_argument("--skip-verdicts", default=",".join(run_skip_verdicts()),
                       help="page_quality verdicts to refuse, comma-separated. "
                            "The default refuses the three that are defects of "
                            "the reading rather than readings; pass '' to keep "
                            "them")
    p_rhf.add_argument("--keep-truncated", action="store_true",
                       help="Export readings cut off at the model's output "
                            "ceiling. Off by default: a truncated page is a "
                            "success that stops mid-sentence")
    p_rhf.add_argument("--rejected-out",
                       help="Write one row per page left out, with its reason")
    p_rhf.add_argument("--repo-id", default=run_repo_id(),
                       help="Dataset repo for the printed pagexml-hf command")
    p_rhf.add_argument("--dry-run", action="store_true",
                       help="Print the plan, write nothing")
    p_rhf.set_defaults(func=export_run_hf)

    p_up = sub.add_parser(
        "upload-hf",
        help="Upload an export tree to the hub with pagexml-hf (private)")
    p_up.add_argument("--tree", required=True,
                      help="The export tree `export-hf --out` wrote")
    p_up.add_argument("--repo-id", default=None,
                      help=f"Dataset repository (default: "
                           f"{hf_default_repo()})")
    p_up.add_argument("--public", action="store_true",
                      help="Upload WITHOUT --private. Needs --yes as well: these "
                           "are unpublished archival images, and the difference "
                           "between private and public here is not recoverable "
                           "by deleting the dataset afterwards")
    p_up.add_argument("--yes", action="store_true",
                      help="Confirm a --public upload")
    p_up.add_argument("--replace", action="store_true",
                      help="upload even if hub projects missing from the tree would go "
                           "(pagexml-hf replaces the dataset)")
    p_up.add_argument("--dry-run", action="store_true",
                      help="Check the tree, the tool, the token and the repo id, "
                           "print the command, upload nothing")
    p_up.set_defaults(func=upload_hf)

    p_split = sub.add_parser(
        "split-tree",
        help="Hold out whole objects of an export tree as <project>_eval (#711)")
    p_split.add_argument("--tree", required=True, help="The export tree")
    p_split.add_argument("--fraction", type=float, default=0.10,
                         help="Share of each project's objects held out (default 0.10)")
    p_split.add_argument("--seed", type=int, default=20261023)
    p_split.add_argument("--force-eval", default="",
                         help="Comma-separated objects (file-name stems before _NNNN) "
                              "always held out, e.g. K2911-17,K2911-107")
    p_split.add_argument("--undo", action="store_true",
                         help="Move every _eval page back and drop the record")
    p_split.add_argument("--apply", action="store_true",
                         help="Move the files. Without it: show what would move")
    p_split.add_argument("--allow-after-upload", action="store_true",
                         help="Split a tree export.json records as uploaded")
    p_split.add_argument("--json", action="store_true", help="Also print the result as JSON")
    p_split.set_defaults(func=split_tree)

    p_oai = sub.add_parser(
        "oai-gt-import",
        help="Harvest a named OAI-PMH source and build its ground-truth tree (#712)")
    p_oai.add_argument("--source", required=True, help="A name from oai_gt.SOURCES, e.g. blb-lassberg")
    p_oai.add_argument("--step", required=True, choices=("inventory", "build"))
    p_oai.add_argument("--root", default=None,
                       help="Where _oai/<source>/ and the tree go (default ATR_DATASETS_DIR)")
    p_oai.add_argument("--limit", type=int, default=None, help="Only the first N objects (smoke run)")
    p_oai.add_argument("--workers", type=int, default=4, help="Download threads (at most 8)")
    p_oai.add_argument("--dry-run", action="store_true",
                       help="build: plan and estimate the size, write nothing")
    p_oai.set_defaults(func=oai_gt_import)

    p_ts = sub.add_parser(
        "train-spec",
        help="A training spec from training/specs/, pinned: request, submit, status, ubelix (#715)")
    p_ts.add_argument("--spec", required=True,
                      help="Name of a file under training/specs/ (no .json); several, comma-"
                           "separated, for --action ubelix and --write-request")
    p_ts.add_argument("--write-request", action="store_true",
                      help="Write the pinned body to training/requests/<spec>@<rev>.json in this "
                           "checkout, to commit (a working tree, not tei's production checkout)")
    p_ts.add_argument("--action", default="request", choices=("request", "submit", "status", "ubelix"))
    p_ts.add_argument("--job-id", default=None, help="status: a trainer job id (default: the newest of the model)")
    p_ts.add_argument("--body-only", action="store_true",
                      help="Print only the pinned request body (for scp to UBELIX)")
    p_ts.set_defaults(func=train_spec_cmd)

    p_card = sub.add_parser(
        "upload-hf-card",
        help="Put a dataset card from docs/ on the hub as README.md, with the "
             "hub's own front matter spliced in first")
    p_card.add_argument("--card", required=True,
                        help="A card's name under docs/ (hf-dataset-card*.md), "
                             "not a path")
    p_card.add_argument("--repo-id", default=None,
                        help=f"Dataset repository (default: {hf_default_repo()})")
    p_card.add_argument("--dry-run", action="store_true",
                        help="Fetch the hub README, show the splice, write and "
                             "upload nothing")
    p_card.set_defaults(func=upload_hf_card)

    p_tk = sub.add_parser(
        "upload-transkribus",
        help="Put the page cache's letters into a Transkribus collection")
    p_tk.add_argument("--collection",
                      help="Collection id (default: TRANSKRIBUS_COLLECTION)")
    p_tk.add_argument("--cache-dir",
                      help="Page cache to read (default: ATR_PAGE_CACHE)")
    p_tk.add_argument("--limit", type=int, default=None,
                      help="Plan only the first N letters")
    p_tk.add_argument("--by-letter", action="store_true",
                      help="One document per lassberg-letter-NNNN, named by the "
                           "edition's register; pages whose path names no letter "
                           "or a letter the register lacks are left out")
    p_tk.add_argument("--register", default=None,
                      help="The edition's data/letters (default LASSBERG_REGISTER)")
    p_tk.add_argument("--source", default=None,
                      help="Where the page images are (default --cache-dir); the "
                           "cold tier of the page cache holds the whole corpus")
    p_tk.add_argument("--letter", default=None,
                      help="Only this letter id — the first real upload is one "
                           "letter, checked in the interface")
    p_tk.add_argument("--all", action="store_true",
                      help="Upload every planned letter (after --letter proved the path)")
    p_tk.add_argument("--dry-run", action="store_true",
                      help="Log in, report whether the collection is visible, "
                           "print the plan, upload nothing")
    p_tk.set_defaults(func=upload_transkribus)

    p_cmp = sub.add_parser(
        "compare-runs",
        help="Pairwise disagreement between two or more batch readings")
    p_cmp.add_argument("--run-dir", required=True, action="append",
                       help="A run directory, or just a run name under "
                            "VLM_TEST_ROOT; repeat for each run to compare")
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
