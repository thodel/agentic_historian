"""Step 1 of the switch is a copy by hand; this is what checks the copy (#567).

`deploy/systemd/env_diff.py` imports `config` twice — as the bot reads it today
and with `AH_ENV_FILE` pointing at the candidate — and prints the *names* of
the settings that differ. It exists because the first candidate, built from the
example's keys alone, would have put four settings silently back on their
defaults (tei, 22.10.2026): ATR_MOUNT_DIR, ATR_PAGE_CACHE_ARCHIVE,
LASSBERG_REGISTER, NEXTCLOUD_REMOTE_DIR. Nothing fails loudly on any of them.

The end-to-end cases run the real two-process comparison against a stand-in
`config.py` that reads its dotenv files the way the real one does, so the
answer comes from a subprocess import and not from a mock of one.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "systemd"


def _load():
    spec = importlib.util.spec_from_file_location("env_diff", DEPLOY / "env_diff.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ed = _load()

#: A config.py with the real one's reading rule: AH_ENV_FILE set → only that
#: file; unset → the checkout's dotenv files in order, first file wins.
STAND_IN_CONFIG = '''
import os
from pathlib import Path
from dotenv import load_dotenv
_HERE = Path(__file__).resolve().parent
_ded = os.environ.get("AH_ENV_FILE", "").strip()
_files = (Path(_ded),) if _ded else (_HERE.parent / ".env.gpustack", _HERE.parent / ".env", _HERE / ".env")
for _f in _files:
    if _f.exists():
        load_dotenv(_f, override=False)
TOKEN = os.environ.get("TOKEN", "")
MOUNT = Path(os.environ.get("MOUNT", "/mnt/default"))
REMOTE_DIR = os.environ.get("REMOTE_DIR", "")
lower_case_is_not_a_setting = os.environ.get("TOKEN", "")
'''


def _checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    package = repo / "agentic_historian"
    package.mkdir(parents=True)
    (package / "config.py").write_text(STAND_IN_CONFIG, encoding="utf-8")
    (repo / ".env.gpustack").write_text("TOKEN=secret-one\nREMOTE_DIR=outputs\n", encoding="utf-8")
    (repo / ".env").write_text("TOKEN=shadowed-by-the-first-file\n", encoding="utf-8")
    (package / ".env").write_text("MOUNT=/mnt/real\n", encoding="utf-8")
    return package


def _run(package: Path, candidate: Path, monkeypatch):
    files = (package.parent / ".env.gpustack", package.parent / ".env", package / ".env")
    scrub = ed.dotenv_keys(files)
    before = ed.snapshot(None, scrub=scrub, package=package)
    after = ed.snapshot(candidate, scrub=scrub, package=package)
    assert before is not None and after is not None
    return ed.differing(before, after)


# ── the pure part ───────────────────────────────────────────────────────────

def test_differing_names_both_changed_and_missing_settings():
    before = {"A": "'1'", "B": "'2'", "C": "'3'"}
    after = {"A": "'1'", "B": "'x'"}
    assert ed.differing(before, after) == ["B", "C"]


def test_differing_is_empty_when_nothing_moved():
    assert ed.differing({"A": "'1'"}, {"A": "'1'"}) == []


def test_dotenv_keys_are_names_only(tmp_path):
    f = tmp_path / ".env"
    f.write_text("TOKEN=do-not-print-me\nOTHER=1\n", encoding="utf-8")
    keys = ed.dotenv_keys((f, tmp_path / "absent"))
    assert keys == {"TOKEN", "OTHER"}
    assert "do-not-print-me" not in repr(keys)


# ── end to end, against a stand-in config that reads like the real one ──────

def test_a_candidate_with_only_the_obvious_key_reports_the_two_that_fall_back(tmp_path, monkeypatch):
    """The tei case in miniature: the secret was copied, the paths were not."""
    package = _checkout(tmp_path)
    candidate = tmp_path / "candidate.env"
    candidate.write_text("TOKEN=secret-one\n", encoding="utf-8")

    changed = _run(package, candidate, monkeypatch)

    assert changed == ["MOUNT", "REMOTE_DIR"]


def test_a_complete_candidate_changes_nothing(tmp_path, monkeypatch):
    package = _checkout(tmp_path)
    candidate = tmp_path / "candidate.env"
    candidate.write_text("TOKEN=secret-one\nREMOTE_DIR=outputs\nMOUNT=/mnt/real\n", encoding="utf-8")

    assert _run(package, candidate, monkeypatch) == []


def test_a_key_exported_in_the_shell_cannot_hide_a_difference(tmp_path, monkeypatch):
    """The operator's shell may have MOUNT exported from some earlier step; both
    imports must see the files, not the shell, or the diff is empty by accident."""
    package = _checkout(tmp_path)
    monkeypatch.setenv("MOUNT", "/mnt/real")
    candidate = tmp_path / "candidate.env"
    candidate.write_text("TOKEN=secret-one\nREMOTE_DIR=outputs\n", encoding="utf-8")

    assert _run(package, candidate, monkeypatch) == ["MOUNT"]


def test_the_report_never_prints_a_value(tmp_path, monkeypatch, capsys):
    package = _checkout(tmp_path)
    candidate = tmp_path / "candidate.env"
    candidate.write_text("TOKEN=secret-one\n", encoding="utf-8")
    monkeypatch.setattr(ed, "PACKAGE", package)
    monkeypatch.setattr(ed, "CHECKOUT_DOTENV",
                        (package.parent / ".env.gpustack", package.parent / ".env", package / ".env"))

    code = ed.main([str(candidate)])
    out = capsys.readouterr()

    assert code == 1
    assert "MOUNT" in out.out and "REMOTE_DIR" in out.out
    for value in ("secret-one", "/mnt/real", "outputs", "shadowed"):
        assert value not in out.out + out.err, value


def test_an_absent_candidate_is_exit_2_not_a_clean_diff(tmp_path, capsys):
    assert ed.main([str(tmp_path / "nope.env")]) == 2


def test_a_failing_import_is_exit_2_not_a_clean_diff(tmp_path, monkeypatch, capsys):
    package = tmp_path / "repo" / "agentic_historian"
    package.mkdir(parents=True)
    (package / "config.py").write_text("raise RuntimeError('broken config')\n", encoding="utf-8")
    candidate = tmp_path / "candidate.env"
    candidate.write_text("", encoding="utf-8")
    monkeypatch.setattr(ed, "PACKAGE", package)
    monkeypatch.setattr(ed, "CHECKOUT_DOTENV", ())

    assert ed.main([str(candidate)]) == 2
    assert "nothing compared" in capsys.readouterr().err
