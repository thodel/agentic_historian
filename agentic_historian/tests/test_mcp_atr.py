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

import asyncio
import sys
import time
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                    # noqa: E402
from mcp_atr import jobs                         # noqa: E402
from mcp_atr.server import BearerAuth            # noqa: E402


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
    directory = jobs.jobs_root() / job.job_id
    meta = (directory / "meta.json").read_text()
    (directory / "meta.json").write_text(meta.replace(f'"pid": {job.pid}', '"pid": 2147483646'))
    assert jobs.status(job.job_id).state == "vanished"
    jobs.stop(job.job_id)      # leave nothing behind for the next test


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


# ── the bearer check ─────────────────────────────────────────────────────────

class _Recorder:
    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True


def _request(auth, header: "bytes | None") -> list[dict]:
    """One HTTP request through the middleware, as plain ASGI.

    Driven with ``asyncio.run`` rather than an async test: four tests are not
    worth a pytest plugin dependency, and the middleware is a coroutine, not a
    framework.
    """
    headers = [(b"authorization", header)] if header is not None else []
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    asyncio.run(auth({"type": "http", "headers": headers}, None, send))
    return sent


def test_the_right_token_passes_through():
    inner = _Recorder()
    _request(BearerAuth(inner, "s" * 40), b"Bearer " + b"s" * 40)
    assert inner.called


@pytest.mark.parametrize("header", [
    None,                       # no header at all
    b"",
    b"Bearer wrong",
    b"Bearer ",
    b"s" * 40,                  # the token without the scheme
    b"bearer " + b"s" * 40,     # scheme is case-sensitive here; be strict
    b"Basic " + b"s" * 40,
])
def test_everything_else_is_401(header):
    inner = _Recorder()
    sent = _request(BearerAuth(inner, "s" * 40), header)
    assert not inner.called, "the app must not see an unauthenticated request"
    assert sent[0]["status"] == 401


def test_a_prefix_of_the_token_does_not_pass():
    """compare_digest, not ==. A comparison that returns early leaks the length
    and then, request by request, the token."""
    inner = _Recorder()
    sent = _request(BearerAuth(inner, "s" * 40), b"Bearer " + b"s" * 39)
    assert not inner.called and sent[0]["status"] == 401


def test_a_websocket_is_not_an_http_request(monkeypatch):
    """The middleware must pass non-HTTP scopes through rather than 401 them —
    sending an HTTP response into a lifespan scope would break startup."""
    inner = _Recorder()
    asyncio.run(BearerAuth(inner, "s" * 40)({"type": "lifespan"}, None, None))
    assert inner.called


def test_the_server_refuses_to_start_without_a_token(monkeypatch):
    """A deployment accident must fail loudly at boot, not quietly serve an open
    endpoint that starts GPU jobs."""
    from mcp_atr import server as srv

    for value in ("", "short", "x" * 31):
        monkeypatch.setenv("ATR_MCP_TOKEN", value)
        with pytest.raises(srv.ConfigError):
            srv.build_app()

    monkeypatch.delenv("ATR_MCP_TOKEN", raising=False)
    with pytest.raises(srv.ConfigError):
        srv.build_app()
