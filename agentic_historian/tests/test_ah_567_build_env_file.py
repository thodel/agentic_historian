"""Step 1 of the switch, done by a script that prints names and not values (#567).

`build_env_file.py` is what actually filled `/etc/agentic_historian.env` on tei
(23.10.2026). These pin the four things that make it safe to run as root: only
the example's keys, the value `config.py` would resolve (first file wins, last
assignment within a file), a 0600 target that must not already exist, and an
output without a single value in it.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "systemd"


def _load():
    spec = importlib.util.spec_from_file_location("build_env_file", DEPLOY / "build_env_file.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


be = _load()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "deploy" / "systemd").mkdir(parents=True)
    (repo / "agentic_historian").mkdir()
    (repo / "deploy/systemd/agentic_historian.env.example").write_text(
        "# header\nTOKEN=   # comment\nMOUNT=\nREMOTE=\nUNSET_ANYWHERE=\n", encoding="utf-8")
    (repo / ".env.gpustack").write_text(
        "TOKEN=first-file-wins\nREMOTE=stale\nREMOTE=in-force\nOPENCLAW_MAIL=never-copied\n", encoding="utf-8")
    (repo / ".env").write_text("TOKEN=shadowed\n", encoding="utf-8")
    (repo / "agentic_historian/.env").write_text("MOUNT=/mnt/real\n", encoding="utf-8")
    return repo


def test_only_the_examples_keys_with_the_resolved_values(tmp_path):
    target = tmp_path / "out.env"
    written, left_out = be.build(_repo(tmp_path), target, owner_uid=None)

    body = target.read_text(encoding="utf-8")
    assert written == ["TOKEN", "MOUNT", "REMOTE"]
    assert left_out == ["UNSET_ANYWHERE"]
    assert "TOKEN='first-file-wins'" in body, "the first file wins, as in config.py"
    assert "REMOTE='in-force'" in body, "the last assignment within a file is the one in force"
    assert "MOUNT='/mnt/real'" in body
    assert "OPENCLAW_MAIL" not in body, "a key the example does not list is never copied"
    assert "shadowed" not in body and "stale" not in body


def test_the_target_is_owner_only(tmp_path):
    target = tmp_path / "out.env"
    be.build(_repo(tmp_path), target, owner_uid=None)
    assert os.stat(target).st_mode & 0o777 == 0o600


def test_an_existing_target_is_refused_not_overwritten(tmp_path):
    target = tmp_path / "out.env"
    target.write_text("KEEP=me\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        be.build(_repo(tmp_path), target, owner_uid=None)
    assert target.read_text(encoding="utf-8") == "KEEP=me\n"


def test_a_value_with_a_quote_stops_the_run(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".env").write_text("MOUNT=it's\n", encoding="utf-8")
    (repo / "agentic_historian/.env").unlink()
    with pytest.raises(ValueError, match="MOUNT"):
        be.build(repo, tmp_path / "out.env", owner_uid=None)
    assert not (tmp_path / "out.env").exists()


def test_the_report_names_keys_and_never_values(tmp_path, capsys):
    code = be.main(["--repo", str(_repo(tmp_path)), "--target", str(tmp_path / "out.env")])
    out = capsys.readouterr().out
    assert code == 0
    assert "TOKEN" in out and "UNSET_ANYWHERE" in out
    for value in ("first-file-wins", "in-force", "/mnt/real", "shadowed", "never-copied"):
        assert value not in out, value
