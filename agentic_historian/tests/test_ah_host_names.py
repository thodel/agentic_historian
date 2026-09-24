"""130.92.59.240 is idhefix, not the name this repo used for it (serving#136).

For months both repositories called the serving box asterAIx. It is idhefix;
asteraix is a different machine, 130.92.59.242, and since 16.09.2026 it is the
training box. The count in serving-atr-inference#136 found 176 places and one
fact that made the fix mechanical: **not one of them meant the other machine**.

The serving repo's share was swept there; this file keeps ours swept. A name
that is wrong everywhere it appears cannot be fixed once — the next comment
written from memory brings it back, and here it reached a Discord command
description that every user of the bot could read (#478).

What the check cannot do is tell whether a *sentence* is still true. Two of them
were not, and were rewritten rather than renamed: `TrainingClient`'s "both
services live on the same box", and the topology in
`docs/TRAINING_INTEGRATION_PLAN.md`.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
#: Built, not written, so this file is not its own counter-example.
OLD_NAME = "aster" + "AIx"
SKIP_DIRS = {".git", ".venv", ".venvs", "__pycache__", ".pytest_cache", ".ruff_cache",
             ".claude", "node_modules", "workspace", "data"}
SUFFIXES = {".md", ".py", ".sh", ".txt", ".yaml", ".yml", ".toml", ".conf", ".service",
            ".json", ".example"}


def _text_files() -> list[Path]:
    return [p for p in sorted(REPO.rglob("*"))
            if p.is_file() and not (set(p.relative_to(REPO).parts) & SKIP_DIRS)
            and p.suffix in SUFFIXES]


def test_the_scan_is_not_vacuous():
    files = _text_files()
    assert any(p.name == "bot.py" for p in files), "the scan found no source"
    assert len(files) > 50


def test_nothing_calls_the_serving_box_by_its_old_name():
    offenders = sorted(
        str(p.relative_to(REPO)) for p in _text_files()
        if OLD_NAME in p.read_text(encoding="utf-8", errors="replace")
        and p.resolve() != Path(__file__).resolve()
    )
    assert not offenders, (
        "130.92.59.240 is idhefix and 130.92.59.242 is asteraix "
        "(serving-atr-inference#136); the old name is in:\n  " + "\n  ".join(offenders))
