"""
``python -m agentic_historian`` — CLI entry point (no Discord required).

    python -m agentic_historian run path/to/image.jpg [--lang de]
    python -m agentic_historian pull-share --folder digitalisate
    python -m agentic_historian atr-batch --source DIR --models a,b,c --run NAME
    python -m agentic_historian publish-batch --run-dir DIR --repo owner/name

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


def atr_batch(args: argparse.Namespace) -> int:
    """Read every page under --source with every --models entry, model-major."""
    import atr_batch as batch

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        print("Error: --models is empty", file=sys.stderr)
        return 2

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

    recognise = batch.gateway_recogniser()
    try:
        report = batch.run_batch(
            pages, models, args.run, out_root, recognise,
            retries=args.retries, concurrency=args.concurrency,
        )
    finally:
        close = getattr(recognise, "close", None)
        if close:
            close()

    path = batch.write_report(report)
    print(batch.format_report(report))
    print(f"report: {path}")
    # A model that was abandoned is a failed run even though the others finished:
    # the exit code is what a wrapper script reads, and it must not say "fine".
    return 1 if report.any_aborted else 0


def publish_batch(args: argparse.Namespace) -> int:
    """Publish a finished run directory to a GitHub repository."""
    from utils.publish_github import publish_tree

    run_dir = Path(args.run_dir).resolve()
    try:
        urls = publish_tree(
            run_dir, repo=args.repo, path_prefix=args.path, branch=args.branch,
            message=args.message or f"Add ATR outputs: {run_dir.name}",
        )
    except Exception as exc:  # noqa: BLE001 — a CLI reports, it does not traceback
        print(f"Error: publish failed: {exc}", file=sys.stderr)
        return 1
    for url in urls:
        print(url)
    return 0 if urls else 1


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

    p_batch = sub.add_parser(
        "atr-batch",
        help="Read every page under --source with every model, one model at a time",
    )
    p_batch.add_argument("--source", required=True, help="Directory of page images")
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
    p_batch.add_argument("--dry-run", action="store_true",
                         help="Print the plan (pages, models, calls) and exit")
    p_batch.set_defaults(func=atr_batch)

    p_pub = sub.add_parser("publish-batch", help="Publish a run directory to a GitHub repo")
    p_pub.add_argument("--run-dir", required=True, help="The run directory to publish")
    p_pub.add_argument("--repo", required=True, help="owner/name")
    p_pub.add_argument("--path", required=True, help="Path prefix inside the repo")
    p_pub.add_argument("--branch", default="main")
    p_pub.add_argument("--message", help="Commit message")
    p_pub.set_defaults(func=publish_batch)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
