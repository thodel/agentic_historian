"""The MCP surface on tei: what it refuses, and what happens to a started job.

This endpoint is reachable from the public internet and it starts processes on a
machine with two A40s. The corpus servers next door are read-only, so a stolen
token there costs a public-domain lexicon; here the blast radius of the token is
exactly the set of tools, which is why most of this file is about refusal.

Two halves:

* **Validation** — every way a request could become something other than "read
  this corpus with these models". Path escape, symlink escape, a run name that
  climbs out of its root, a model list long enough to be an attack.
* **Jobs** — the lifecycle a caller polls. The interesting case is the one nobody
  designs for: a job whose process is gone without an exit code. Calling that
  "done" would cost somebody a re-run they did not know they needed.

Offline. No gateway, no GPU, no network. Run from the repo root::

    pytest agentic_historian/tests/test_mcp_atr.py
"""

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                    # noqa: E402
from mcp_atr import jobs                         # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point every root at a tmp dir, so a test can never touch the real ones."""
    staging = tmp_path / "nextcloud"
    vlm = tmp_path / "vlm_test"
    data = tmp_path / "data"
    for d in (staging, vlm, data):
        d.mkdir()
    monkeypatch.setattr(config, "NEXTCLOUD_STAGING_DIR", staging)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", vlm)
    monkeypatch.setattr(config, "DATA_DIR", data)
    return tmp_path


# ── run names ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "atr_test_lassberg", "run-1", "a", "v1.2.3", "A" * 64,
])
def test_a_reasonable_run_name_is_accepted(name):
    assert jobs.validate_run(name) == name


@pytest.mark.parametrize("name", [
    "../escape",          # the obvious one: run becomes a directory under VLM_TEST_ROOT
    "a/b",                # a separator is a second directory, not a name
    "/absolute",
    "",
    "   ",
    ".hidden",            # must start with a letter or digit
    "-flag",              # and never look like one to the CLI it is passed to
    "A" * 65,
    "run;rm -rf /",       # harmless as argv, rejected anyway: nothing needs a ';'
    "run name",
    "run\nname",
])
def test_a_run_name_that_is_not_a_name_is_refused(name):
    with pytest.raises(jobs.JobError):
        jobs.validate_run(name)


# ── model ids ────────────────────────────────────────────────────────────────

def test_the_real_model_ids_are_accepted():
    ids = ["qwen3vl-german-xix-v1", "qwen3.5-4b-german-xix-v1",
           "kraken-fondue_gd_v2", "party"]
    assert jobs.validate_models(ids) == ids


def test_a_comma_separated_string_is_the_same_thing():
    assert jobs.validate_models("a,b , c") == ["a", "b", "c"]


@pytest.mark.parametrize("models", [[], "", [" "], ["--dry-run"], ["a b"], ["a/b"]])
def test_model_lists_that_are_not_model_lists(models):
    with pytest.raises(jobs.JobError):
        jobs.validate_models(models)


def test_a_model_list_long_enough_to_be_an_attack_is_refused():
    """Each model is a full pass over the corpus. Forty of them is a mistake or a
    denial of service, never an intention."""
    with pytest.raises(jobs.JobError):
        jobs.validate_models([f"m{i}" for i in range(40)])


# ── source containment ───────────────────────────────────────────────────────

def test_a_relative_source_resolves_inside_the_mirror(sandbox):
    (config.NEXTCLOUD_STAGING_DIR / "digitalisate").mkdir()
    assert jobs.resolve_source("digitalisate") == \
        (config.NEXTCLOUD_STAGING_DIR / "digitalisate").resolve()


def test_a_previous_run_is_a_legitimate_source(sandbox):
    (config.VLM_TEST_ROOT / "earlier").mkdir()
    assert jobs.resolve_source(str(config.VLM_TEST_ROOT / "earlier")).name == "earlier"


@pytest.mark.parametrize("source", ["/etc", "/", "../../..", ""])
def test_a_source_outside_the_corpus_roots_is_refused(sandbox, source):
    with pytest.raises(jobs.JobError):
        jobs.resolve_source(source)


def test_a_symlink_out_of_the_mirror_is_refused(sandbox):
    """The reason containment is checked on the *resolved* path. A string check
    passes this: the link is inside the mirror, and its target is not."""
    link = config.NEXTCLOUD_STAGING_DIR / "innocent"
    link.symlink_to("/etc")
    with pytest.raises(jobs.JobError) as err:
        jobs.resolve_source("innocent")
    assert "outside the corpus roots" in str(err.value)


def test_a_source_that_does_not_exist_is_refused_before_a_process_starts(sandbox):
    with pytest.raises(jobs.JobError):
        jobs.resolve_source("nothing-here")


# ── argv ─────────────────────────────────────────────────────────────────────

def test_batch_argv_is_the_documented_cli(sandbox):
    argv = jobs.batch_argv(Path("/corpus"), ["a", "b"], "run1", limit=3)
    assert argv[1:4] == ["-m", "agentic_historian", "atr-batch"]
    assert "--source" in argv and "/corpus" in argv
    assert argv[argv.index("--models") + 1] == "a,b"
    assert argv[argv.index("--limit") + 1] == "3"


def test_argv_is_a_list_so_nothing_is_ever_a_shell(sandbox):
    """The whole containment argument rests on this: argv goes to execve, so a
    metacharacter in a value is a character in a value."""
    argv = jobs.batch_argv(Path("/corpus"), ["m"], "run1")
    assert all(isinstance(a, str) for a in argv)
    assert not any(c in " ".join(argv) for c in ("|", ";", "&&", "$("))


def test_numbers_are_coerced_not_interpolated(sandbox):
    """`limit="3; rm -rf /"` must not survive as text, even though argv makes it
    harmless — an int() here is one fewer thing to reason about."""
    with pytest.raises(ValueError):
        jobs.batch_argv(Path("/corpus"), ["m"], "r", limit="3; rm -rf /")


# ── the job lifecycle ────────────────────────────────────────────────────────

def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_a_job_runs_detached_and_records_how_it_ended(sandbox):
    job = jobs.start("test", [sys.executable, "-c", "print('hello'); raise SystemExit(0)"])
    assert job.state == "running" and job.pid
    assert _wait_for(lambda: jobs.status(job.job_id).state == "done"), \
        "the wrapper must write an exit code"
    done = jobs.status(job.job_id)
    assert done.exit_code == 0
    assert "hello" in jobs.read_log(job.job_id)


def test_a_failing_job_is_failed_not_done(sandbox):
    job = jobs.start("test", [sys.executable, "-c", "raise SystemExit(3)"])
    assert _wait_for(lambda: jobs.status(job.job_id).state == "failed")
    assert jobs.status(job.job_id).exit_code == 3


def test_a_job_whose_process_vanished_is_not_reported_as_done(sandbox):
    """The case nobody designs for: a reboot, an OOM kill, a pulled plug. There
    is no exit code and no process. Calling that success would cost somebody a
    re-run they did not know they needed."""
    job = jobs.start("test", [sys.executable, "-c", "import time; time.sleep(30)"])
    real_pid = job.pid
    directory = jobs.jobs_root() / job.job_id
    meta = (directory / "meta.json").read_text()
    (directory / "meta.json").write_text(meta.replace(f'"pid": {real_pid}', '"pid": 2147483646'))
    try:
        assert jobs.status(job.job_id).state == "vanished"
    finally:
        # Not jobs.stop(): it reads the pid from meta.json, which this test just
        # replaced with a fiction. Signalling that would leave the real wrapper
        # and its child running for 30 seconds — which is exactly what CI
        # reported as two orphan processes at cleanup.
        os.killpg(os.getpgid(real_pid), signal.SIGTERM)


def test_the_log_tail_is_bounded_at_both_ends(sandbox):
    job = jobs.start("test", [sys.executable, "-c",
                              "print('\\n'.join(str(i) for i in range(5000)))"])
    assert _wait_for(lambda: jobs.status(job.job_id).state == "done")
    tail = jobs.read_log(job.job_id, lines=10).splitlines()
    assert tail == [str(i) for i in range(4990, 5000)]
    assert len(jobs.read_log(job.job_id, lines=10_000).splitlines()) <= 500


def test_an_unknown_job_id_is_an_error_not_a_traceback(sandbox):
    with pytest.raises(jobs.JobError):
        jobs.status("nope")


@pytest.mark.parametrize("job_id", ["../../etc/passwd", "a/b", "", "x" * 100])
def test_a_job_id_cannot_address_anything_but_a_job(sandbox, job_id):
    with pytest.raises(jobs.JobError):
        jobs.status(job_id)


# ── the bounded wait ─────────────────────────────────────────────────────────
#
# A protocol with a request timeout, and work that does not respect one. The MCP
# broker cut a `share_list` call at 60 s on 2026-09-15 while the listing was
# still running on tei — a failure for the caller and work nobody could see.

def test_quick_work_answers_inline(sandbox):
    """Then the caller never learns there was a job at all."""
    result = jobs.start_and_peek("test", [sys.executable, "-c", "print('forty files')"])
    assert result["done"] is True
    assert result["state"] == "done" and result["exit_code"] == 0
    assert "forty files" in result["output"]


def test_slow_work_hands_back_a_handle(sandbox):
    """Not a timeout, and not a longer wait: the ceiling belongs to the client, so
    raising ours would only move the failure somewhere the job is lost too."""
    result = jobs.start_and_peek("test", [sys.executable, "-c", "import time; time.sleep(30)"],
                                 grace_s=1, poll_s=0.2)
    assert result["done"] is False
    assert result["state"] == "running" and result["job_id"]
    assert "job_log" in result["note"], "the caller has to be told what to do next"
    jobs.stop(result["job_id"])


def test_a_handle_from_a_peek_is_a_normal_job(sandbox):
    """The work continues either way — the peek only decides what is reported."""
    result = jobs.start_and_peek("test", [sys.executable, "-c", "print('late'); import time; time.sleep(0.2)"],
                                 grace_s=0)
    assert result["done"] is False
    assert _wait_for(lambda: jobs.status(result["job_id"]).state == "done")
    assert "late" in jobs.read_log(result["job_id"])


def test_failure_inside_the_grace_is_reported_as_failure(sandbox):
    """A job that died fast must not read as success just because it was quick."""
    result = jobs.start_and_peek("test", [sys.executable, "-c", "raise SystemExit(2)"])
    assert result["done"] is True and result["state"] == "failed"
    assert result["exit_code"] == 2


def test_the_wait_stays_inside_the_brokers_patience():
    """The one number that has to hold: whatever we wait for must leave the client
    room to still receive the answer."""
    assert jobs.PEEK_S < jobs.BROKER_TIMEOUT_S / 2


# ── progress ─────────────────────────────────────────────────────────────────

def test_progress_counts_the_runs_own_output(sandbox):
    """Not an estimate: `atr_batch` skips a page whose .json is on disk, so this
    is the same number it would compute when resuming."""
    run_dir = config.VLM_TEST_ROOT / "lassberg"
    (run_dir / "modelA").mkdir(parents=True)
    (run_dir / "modelB").mkdir()
    for i in range(3):
        (run_dir / "modelA" / f"p{i}.json").write_text("{}")
    progress = jobs.run_progress("lassberg")
    assert progress["pages_written"] == {"modelA": 3, "modelB": 0}
    assert "report" not in progress

    (run_dir / "report.md").write_text("# report")
    assert "report" in jobs.run_progress("lassberg")


def test_progress_for_a_run_that_has_not_started_is_empty(sandbox):
    assert jobs.run_progress("never-run") == {}
    assert jobs.run_progress(None) == {}


# ── configuration refusals ───────────────────────────────────────────────────
#
# Authentication itself moved to OAuth (mcp_atr/oauth.py) when it turned out the
# claude.ai connector cannot send a header — see tests/test_mcp_atr_oauth.py.
# What stays here is the refusal to start at all without one.

@pytest.mark.parametrize("url,ok", [
    ("https://tei.dh.unibe.ch/mcp/atr", True),
    ("http://127.0.0.1:8300", True),        # loopback, for driving the flow locally
    ("http://localhost:8300", True),
    ("http://tei.dh.unibe.ch/mcp/atr", False),   # clear-text off the loopback
    ("tei.dh.unibe.ch/mcp/atr", False),
    ("", False),
])
def test_the_public_url_must_be_public_and_encrypted(url, ok, monkeypatch):
    """Every OAuth redirect and both metadata documents are absolute and are read
    by a browser on the far side of nginx. A wrong value here yields a flow that
    looks like it works and lands the user on 127.0.0.1."""
    from mcp_atr import server as srv

    monkeypatch.setenv("ATR_MCP_PUBLIC_URL", url)
    if ok:
        assert srv.public_base() == url.rstrip('/')
    else:
        with pytest.raises(srv.ConfigError):
            srv.public_base()


def test_the_public_url_loses_its_trailing_slash(monkeypatch):
    """It is concatenated with /authorize, /token and /mcp; a trailing slash would
    produce // in every one of them, and RFC 8414 compares issuers as strings."""
    from mcp_atr import server as srv

    monkeypatch.setenv("ATR_MCP_PUBLIC_URL", "https://tei.dh.unibe.ch/mcp/atr/")
    assert srv.public_base() == "https://tei.dh.unibe.ch/mcp/atr"


def test_the_server_refuses_to_start_without_a_password(monkeypatch):
    """A deployment accident must fail loudly at boot, not quietly serve an
    endpoint that starts GPU jobs."""
    from mcp_atr import server as srv

    monkeypatch.setenv("ATR_MCP_PUBLIC_URL", "https://tei.dh.unibe.ch/mcp/atr")
    for value in ("", "short", "x" * 15):
        monkeypatch.setenv("ATR_MCP_PASSWORD", value)
        with pytest.raises(Exception) as err:
            srv.build_app()
        assert "ATR_MCP_PASSWORD" in str(err.value)


def test_an_optional_static_token_still_has_to_be_long(monkeypatch):
    """It is optional — the web connector cannot send one — but a short one is a
    mistake, not a choice."""
    from mcp_atr import server as srv

    monkeypatch.setenv("ATR_MCP_TOKEN", "too-short")
    with pytest.raises(srv.ConfigError):
        srv._token()

    monkeypatch.delenv("ATR_MCP_TOKEN", raising=False)
    assert srv._token() == "", "absent is allowed; OAuth is the other door"


# ── reading a run's transcriptions ───────────────────────────────────────────

def _write_run(vlm, run="lassberg", model="modelA", pages=3, text="Hochgeehrter Herr"):
    directory = vlm / run / model
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(pages):
        (directory / f"p{i}.txt").write_text(f"{text} {i}", encoding="utf-8")
        (directory / f"p{i}.json").write_text(
            json.dumps({"schema": 1, "text": f"{text} {i}", "truncated": False}),
            encoding="utf-8")
    return directory


def test_the_inventory_lists_pages_per_model(sandbox):
    """What a caller needs before asking for text, and no text in it."""
    _write_run(config.VLM_TEST_ROOT, pages=3)
    _write_run(config.VLM_TEST_ROOT, model="modelB", pages=1)
    (config.VLM_TEST_ROOT / "lassberg" / "report.md").write_text("# report")

    out = jobs.list_outputs("lassberg")
    assert out["models"]["modelA"]["pages"] == 3
    assert out["models"]["modelA"]["keys"] == ["p0", "p1", "p2"]
    assert out["models"]["modelB"]["pages"] == 1
    assert "report.md" in out["run_files"]
    assert "text" not in json.dumps(out["models"])


def test_the_inventory_can_name_one_model(sandbox):
    _write_run(config.VLM_TEST_ROOT)
    _write_run(config.VLM_TEST_ROOT, model="modelB")
    assert list(jobs.list_outputs("lassberg", "modelA")["models"]) == ["modelA"]


def test_a_long_inventory_says_how_many_keys_it_left_out(sandbox, monkeypatch):
    """The count stays exact even when the names do not all fit."""
    monkeypatch.setattr(jobs, "MAX_LISTED_KEYS", 2)
    _write_run(config.VLM_TEST_ROOT, pages=5)
    entry = jobs.list_outputs("lassberg")["models"]["modelA"]
    assert entry["pages"] == 5 and len(entry["keys"]) == 2
    assert entry["keys_omitted"] == 3


def test_reading_returns_the_text_and_the_ceiling_flag(sandbox):
    """`truncated_by_model` travels with every text, because a page cut off at
    the token ceiling reads as a normal success right up to where it stops."""
    directory = _write_run(config.VLM_TEST_ROOT, pages=1)
    (directory / "p0.json").write_text(
        json.dumps({"schema": 1, "text": "x", "truncated": True}), encoding="utf-8")

    page = jobs.read_outputs("lassberg", "modelA")["pages"][0]
    assert page["key"] == "p0"
    assert page["text"] == "Hochgeehrter Herr 0"
    assert page["truncated_by_model"] is True


def test_reading_pages_through_the_run_and_states_the_remainder(sandbox):
    _write_run(config.VLM_TEST_ROOT, pages=5)
    first = jobs.read_outputs("lassberg", "modelA", limit=2)
    assert [p["key"] for p in first["pages"]] == ["p0", "p1"]
    assert first["remaining"] == 3 and first["next_offset"] == 2

    second = jobs.read_outputs("lassberg", "modelA", offset=first["next_offset"], limit=2)
    assert [p["key"] for p in second["pages"]] == ["p2", "p3"]

    last = jobs.read_outputs("lassberg", "modelA", offset=4, limit=2)
    assert [p["key"] for p in last["pages"]] == ["p4"]
    assert last["remaining"] == 0 and last["next_offset"] is None


def test_named_keys_are_read_exactly(sandbox):
    _write_run(config.VLM_TEST_ROOT, pages=5)
    out = jobs.read_outputs("lassberg", "modelA", keys=["p3", "p1"])
    assert [p["key"] for p in out["pages"]] == ["p3", "p1"]


def test_a_page_too_long_to_carry_is_cut_and_says_so(sandbox, monkeypatch):
    """Cut, never silently: the caller is usually copying this somewhere else."""
    monkeypatch.setattr(jobs, "MAX_PAGE_CHARS", 10)
    directory = _write_run(config.VLM_TEST_ROOT, pages=1)
    (directory / "p0.txt").write_text("y" * 50, encoding="utf-8")

    page = jobs.read_outputs("lassberg", "modelA")["pages"][0]
    assert page["chars"] == 50, "the real length is still reported"
    assert page["text"] == "y" * 10 and page["text_cut"] is True


def test_the_budget_stops_the_reply_and_counts_what_is_left(sandbox, monkeypatch):
    monkeypatch.setattr(jobs, "READ_BUDGET_CHARS", 25)
    _write_run(config.VLM_TEST_ROOT, pages=5)
    out = jobs.read_outputs("lassberg", "modelA", limit=5)
    assert out["returned"] < 5
    assert out["returned"] + out["remaining"] == 5


def test_a_read_cannot_ask_for_more_pages_than_the_cap(sandbox):
    _write_run(config.VLM_TEST_ROOT, pages=3)
    assert jobs.read_outputs("lassberg", "modelA", limit=10_000)["returned"] == 3


@pytest.mark.parametrize("key", [
    "../../../../etc/passwd", "..", "../report", "p0/../../../secret",
])
def test_a_key_cannot_climb_out_of_the_model_directory(sandbox, key):
    """The check is on the resolved path, not the spelling: page keys legitimately
    carry spaces and umlauts, so a charset test would reject real pages while
    still having to do this one."""
    _write_run(config.VLM_TEST_ROOT, pages=1)
    (config.VLM_TEST_ROOT / "lassberg" / "secret.txt").write_text("not yours")

    with pytest.raises(jobs.JobError):
        jobs.read_outputs("lassberg", "modelA", keys=[key])


def test_a_symlink_out_of_the_run_is_refused_too(sandbox):
    directory = _write_run(config.VLM_TEST_ROOT, pages=1)
    outside = sandbox / "outside.txt"
    outside.write_text("not yours")
    (directory / "escape.txt").symlink_to(outside)

    with pytest.raises(jobs.JobError):
        jobs.read_outputs("lassberg", "modelA", keys=["escape"])


def test_a_key_with_spaces_and_umlauts_is_read(sandbox):
    """What the archive's folders actually look like."""
    directory = _write_run(config.VLM_TEST_ROOT, pages=0)
    key = "Aarau__Brief an Lassberg (Entwurf)__00002-scan"
    (directory / f"{key}.txt").write_text("Hochgeehrter Herr", encoding="utf-8")

    out = jobs.read_outputs("lassberg", "modelA", keys=[key])
    assert out["pages"][0]["text"] == "Hochgeehrter Herr"


def test_reading_a_run_or_model_that_is_not_there_explains_which(sandbox):
    _write_run(config.VLM_TEST_ROOT, pages=1)
    with pytest.raises(jobs.JobError, match="no run directory"):
        jobs.read_outputs("never-run", "modelA")
    with pytest.raises(jobs.JobError, match="no output for model"):
        jobs.read_outputs("lassberg", "modelZ")
    with pytest.raises(jobs.JobError, match="no transcription"):
        jobs.read_outputs("lassberg", "modelA", keys=["p99"])


def test_a_run_name_that_climbs_is_refused_before_the_join(sandbox):
    with pytest.raises(jobs.JobError, match="invalid run name"):
        jobs.list_outputs("../../etc")
