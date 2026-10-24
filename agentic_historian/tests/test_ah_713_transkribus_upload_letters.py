"""#713 — digitisations letter by letter into a Transkribus collection, over MCP.

On 24.10. letter 0952 went up through the CLI (upload 19763082, job 31683095),
was looked at in the interface, and the rest followed with --all. The tool is
that protocol with its order enforced: an allowed collection only, a root by
name, one letter before all of them — and every upload logged.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import transkribus as tk                   # noqa: E402
from mcp_atr import jobs                   # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    values = {"TRANSKRIBUS_UPLOAD_COLLECTIONS": "2536466", "TRANSKRIBUS_COLLECTION": "2536466"}
    real = config._get
    monkeypatch.setattr(config, "_get", lambda k, d="": values.get(k, real(k, d)) if k in values else real(k, d))
    monkeypatch.setattr(config, "ATR_PAGE_CACHE_ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(config, "page_cache_dir", lambda: tmp_path / "page_cache")
    return values, tmp_path


def test_the_dry_run_is_the_default_and_names_the_root(env):
    _, tmp = env
    argv = jobs.transkribus_letters_argv("2536466")
    assert argv[3:6] == ["upload-transkribus", "--by-letter", "--collection"]
    assert argv[argv.index("--source") + 1] == str(tmp / "archive")
    assert argv[-1] == "--dry-run"


def test_only_allowed_collections_take_an_upload(env):
    with pytest.raises(jobs.JobError) as err:
        jobs.transkribus_letters_argv("36479", letter="lassberg-letter-0952", dry_run=False)
    assert "not one uploads may go to" in str(err.value) and "2536466" in str(err.value)


def test_the_allowlist_falls_back_to_the_configured_collection(env, monkeypatch):
    values, _ = env
    values.pop("TRANSKRIBUS_UPLOAD_COLLECTIONS")
    values["TRANSKRIBUS_COLLECTION"] = "777"
    assert tk.upload_collections() == ["777"]


def test_a_real_upload_is_one_letter_or_all_never_neither(env):
    with pytest.raises(jobs.JobError):
        jobs.transkribus_letters_argv("2536466", dry_run=False)
    with pytest.raises(jobs.JobError):
        jobs.transkribus_letters_argv("2536466", letter="lassberg-letter-0952",
                                      all_letters=True, dry_run=False)
    one = jobs.transkribus_letters_argv("2536466", letter="lassberg-letter-0952", dry_run=False)
    assert one[-2:] == ["--letter", "lassberg-letter-0952"] and "--dry-run" not in one
    every = jobs.transkribus_letters_argv("2536466", all_letters=True, dry_run=False)
    assert every[-1] == "--all"


@pytest.mark.parametrize("bad", ["lassberg-letter-0952; rm", "../x", "lassberg-letter-95", "0952"])
def test_a_letter_is_an_edition_id(env, bad):
    with pytest.raises(jobs.JobError):
        jobs.transkribus_letters_argv("2536466", letter=bad)


def test_the_source_is_a_root_by_name(env, monkeypatch):
    _, tmp = env
    assert "--source" in jobs.transkribus_letters_argv("2536466", source="cache")
    with pytest.raises(jobs.JobError):
        jobs.transkribus_letters_argv("2536466", source="/etc")
    monkeypatch.setattr(config, "ATR_PAGE_CACHE_ARCHIVE", None)
    with pytest.raises(jobs.JobError) as err:
        jobs.transkribus_letters_argv("2536466")
    assert "not configured" in str(err.value)


def test_all_needs_a_first_letter_in_the_collection():
    letter = tk.Letter(title="lassberg-letter-0952", folder=Path("."), pages=[])
    assert not tk.first_letter_confirmed(tk.Plan(collection="1", letters=[letter]))
    assert tk.first_letter_confirmed(tk.Plan(collection="1", skipped=[letter]))
    body = (PKG / "__main__.py").read_text(encoding="utf-8")
    assert "tk.first_letter_confirmed(plan)" in body and "tk.log_upload(" in body


def test_every_upload_is_one_json_line(tmp_path):
    log = tmp_path / "uploads.jsonl"
    letter = tk.Letter(title="lassberg-letter-0952", folder=Path("."), pages=[])
    tk.log_upload("2536466", letter, {"pages": 2, "upload_id": "19763082", "job_id": "31683095"},
                  mode="letter", path=log)
    tk.log_upload("2536466", letter, {"pages": 2, "upload_id": "2", "job_id": None},
                  mode="all", path=log)
    rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["upload_id"] == "19763082" and rows[0]["mode"] == "letter"
    assert rows[1]["mode"] == "all" and len(rows) == 2


def test_server_status_says_whether_the_credentials_are_there_never_what(env, monkeypatch, tmp_path):
    values, _ = env
    values["TRANSKRIBUS_USER"] = "someone@example.org"
    values["TRANSKRIBUS_PASS"] = "hunter2"
    monkeypatch.setattr(jobs, "_checkout_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "ENV_FILES", ())
    out = jobs.server_status()
    assert out["env"]["transkribus_user"] == "set" and out["env"]["transkribus_pass"] == "set"
    assert "hunter2" not in json.dumps(out) and "someone@example.org" not in json.dumps(out)
