"""
python -m agentic_historian run <file> — CLI entry point (no Discord required).

Usage:
    python -m agentic_historian run path/to/image.jpg [--lang de]

The bot runs headless: Discord bot token can be omitted; the pipeline is
invoked directly.  All agent logs go to stdout (loguru default).
"""

import argparse
import sys
from pathlib import Path

import config
config.ensure_dirs()

from orchestrator import run_full_pipeline


def run(args: argparse.Namespace) -> None:
    image_path = Path(args.file).resolve()
    if not image_path.exists():
        print(f"Error: file not found: {image_path}", file=sys.stderr)
        sys.exit(1)

    lang = args.lang or "la"
    print(f"[CLI] Transcribing {image_path} (lang={lang})...")

    result = run_full_pipeline(image_path, lang=lang)
    print(result)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentic-historian",
        description="Agentic Historian — multi-agent HTR/OCR pipeline for historical manuscripts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="Run full pipeline on an image file")
    run_parser.add_argument("file", help="Path to the manuscript image")
    run_parser.add_argument("--lang", help="Language/script code (default: la)")

    args = parser.parse_args()
    if args.command == "run":
        run(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()