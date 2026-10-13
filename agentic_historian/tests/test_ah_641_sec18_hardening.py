"""SEC-18 (#641): the audit's hardening has to reach code written after it.

The 17 SEC fixes of #571 are all in place, and every one of them is pinned by a
test that names a **hardcoded list** — nine commands, one module, the call sites
that existed in October. None of those tests knows about the code of next month.

Ten commands arrived after the audit and `agent_a/training_client.py` arrived
with them: a second HTTP client against the same gateway as `atr_status`, with
the opposite contract on both counts the audit cared about. Every test stayed
green. These tests are the ones that would not have.

Three parts:

* **Part 1** — the SEC-6 rule (a job id must not steer the request) now holds in
  both clients, from the same pattern, with a drift guard.
* **Part 2** — the SEC-14 rule (no internal URL into the channel) now holds in
  the trainer client and in `/sparql`.
* **Part 3** — gate coverage: every registered slash command is gated or named
  on `bot.OPEN_COMMANDS`. This is the one that makes the rule travel.
"""

import ast
from types import SimpleNamespace

import pytest

import atr_status
from agent_a import training_client as tc


BOT_PATH = "agentic_historian/bot.py"


# ── helpers ──────────────────────────────────────────────────────────────────

#: The payloads measured against ``httpx.Client(base_url=…)._merge_url`` in
#: #641, each with what it did before the fix.
STEERING_IDS = [
    ("../../../admin/keys", "GET /admin/keys at the gateway, with the API key"),
    ("../..",               "escapes /train/jobs/ upward"),
    ("..",                  "the traversal special itself"),
    (".",                   "the traversal special itself"),
    ("x?token=stolen",      "adds a query parameter ahead of the real ones"),
    ("x#frag",              "truncates the path: /log collapses to the record"),
    ("//evil.example.com",  "a protocol-relative authority"),
    ("a/b",                 "a second path segment"),
    ("a\\b",                "a backslash segment separator"),
    ("",                    "empty"),
    ("   ",                 "whitespace only"),
    ("x" * 65,              "longer than any real id"),
]

#: Ids that really occur and must keep working — a regression here breaks the
#: trainer, which is the failure mode a too-tight validator produces.
REAL_IDS = [
    "train-2026-10-13-missiven2",
    "9f8c1e52-3b7a-4c1d-9e2f-5a6b7c8d9e0f",
    "job_42",
    "run.1.2.3",
    "x",
    "X" * 64,
]


class _Recorder:
    """Stands in for the client's ``httpx.Client`` and records what was sent.

    The point of the tests below is not that an exception is raised — it is that
    **no request went out**. A validator that refuses after the request has left
    protects nothing.
    """

    def __init__(self, status=200, payload=None, text=""):
        self.calls: list[tuple[str, str, object]] = []
        self._status = status
        self._payload = payload if payload is not None else {}
        self._text = text

    def _resp(self):
        return SimpleNamespace(
            status_code=self._status,
            text=self._text,
            json=lambda: self._payload,
        )

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self._resp()

    def post(self, path, json=None):
        self.calls.append(("POST", path, json))
        return self._resp()


def _client(recorder):
    client = tc.TrainingClient(base_url="https://gateway.internal:8300")
    client._client = recorder
    return client


# ── Part 1: SEC-6 — a job id must not choose the route ───────────────────────

@pytest.mark.parametrize("job_id,why", STEERING_IDS)
def test_safe_job_id_refuses_steering_ids(job_id, why):
    with pytest.raises(tc.TrainingClientError):
        tc._safe_job_id(job_id)


@pytest.mark.parametrize("job_id", REAL_IDS)
def test_safe_job_id_accepts_real_ids(job_id):
    assert tc._safe_job_id(job_id) == job_id


def test_safe_job_id_message_names_the_rule_not_just_the_input():
    with pytest.raises(tc.TrainingClientError) as err:
        tc._safe_job_id("../../admin")
    msg = str(err.value)
    assert "Job-ID" in msg
    assert "64" in msg, "the message must say what is allowed, not only that it failed"


@pytest.mark.parametrize("job_id,why", STEERING_IDS)
def test_get_job_issues_no_request_for_a_steering_id(job_id, why):
    """The id is refused *before* anything reaches the gateway."""
    rec = _Recorder()
    with pytest.raises(tc.TrainingClientError):
        _client(rec).get_job(job_id)
    assert rec.calls == [], f"a request went out for {job_id!r} ({why})"


@pytest.mark.parametrize("job_id,why", STEERING_IDS)
def test_cancel_job_issues_no_request_for_a_steering_id(job_id, why):
    """``cancel_job`` is a POST, so an unchecked id POSTs to any gateway route."""
    rec = _Recorder()
    with pytest.raises(tc.TrainingClientError):
        _client(rec).cancel_job(job_id)
    assert rec.calls == [], f"a POST went out for {job_id!r} ({why})"


@pytest.mark.parametrize("job_id,why", STEERING_IDS)
def test_get_log_issues_no_request_for_a_steering_id(job_id, why):
    rec = _Recorder()
    with pytest.raises(tc.TrainingClientError):
        _client(rec).get_log(job_id)
    assert rec.calls == [], f"a request went out for {job_id!r} ({why})"


def test_a_real_id_does_reach_the_gateway_on_the_expected_path():
    """The counter-test: the validator must not refuse everything."""
    rec = _Recorder(payload={"job_id": "train-1", "status": "completed"})
    _client(rec).get_job("train-1")
    assert rec.calls == [("GET", "/train/jobs/train-1", None)]


def test_get_log_path_is_the_log_route_not_the_record():
    """``x#frag`` used to collapse ``/log`` away and return the record instead."""
    rec = _Recorder(payload={"job_id": "train-1", "lines": ["a"]})
    _client(rec).get_log("train-1")
    method, path, params = rec.calls[0]
    assert path == "/train/jobs/train-1/log", (
        "the log route must survive id interpolation")


# ── Part 1b: the backstop at the one place every request passes ──────────────

@pytest.mark.parametrize("path", [
    "/train/jobs/../../admin",
    "/train/jobs/x?a=1",
    "/train/jobs/x#f",
    "/train/jobs//x",
    "/train/jobs/a\\b",
    "train/jobs/x",            # not absolute
])
def test_safe_path_refuses_a_steering_path(path):
    """``_safe_job_id`` helps the call sites that remember it; this covers all."""
    with pytest.raises(tc.TrainingClientError):
        tc._safe_path(path)


@pytest.mark.parametrize("path", [
    "/train/jobs",
    "/train/jobs/train-1",
    "/train/jobs/train-1/log",
    "/train/jobs/train-1/cancel",
])
def test_safe_path_accepts_the_real_routes(path):
    assert tc._safe_path(path) == path


def test_request_funnel_applies_the_backstop():
    """Even bypassing the public methods, a steering path does not go out."""
    rec = _Recorder()
    client = _client(rec)
    with pytest.raises(tc.TrainingClientError):
        client._get("/train/jobs/../../../admin/keys")
    with pytest.raises(tc.TrainingClientError):
        client._post("/train/jobs/../../../admin/shutdown", json={})
    assert rec.calls == [], "the backstop let a steering path through"


# ── Part 1c: the drift guard ─────────────────────────────────────────────────

def test_both_clients_share_one_pattern():
    """Not two regexes that happen to agree today — the same object."""
    assert tc.atr_status.JOB_ID_RE is atr_status.JOB_ID_RE


@pytest.mark.parametrize(
    "job_id", [j for j, _ in STEERING_IDS] + REAL_IDS)
def test_both_validators_agree(job_id):
    """The same string is accepted by both clients or refused by both.

    This is the test that would have caught #641 itself: `atr_status` was
    hardened for SEC-6 and `training_client`, written later against the same
    gateway, was not. Nothing compared them.
    """
    def verdict(fn, error):
        try:
            fn(job_id)
            return "accepted"
        except error:
            return "refused"

    assert verdict(tc._safe_job_id, tc.TrainingClientError) == \
        verdict(atr_status._safe_job_id, atr_status.AtrStatusError), \
        f"the two clients disagree about {job_id!r}"


# ── Part 2: SEC-14 — the internal host is not for the channel ────────────────

HOST = "gateway.internal"


def test_http_error_message_carries_no_internal_host():
    rec = _Recorder(status=500, text="boom: /srv/trainer/data is full")
    with pytest.raises(tc.TrainingClientError) as err:
        _client(rec).get_job("train-1")
    msg = str(err.value)
    assert HOST not in msg, f"the internal gateway host leaked into {msg!r}"
    assert "8300" not in msg, "the gateway port leaked"


def test_http_error_message_keeps_the_trainers_own_explanation():
    """Deliberately kept (#361): a 507 names the full filesystem, and that says
    what to do. Both callers that show it are admin_only."""
    rec = _Recorder(status=507, text="insufficient storage on /srv/trainer")
    with pytest.raises(tc.TrainingClientError) as err:
        _client(rec).get_job("train-1")
    msg = str(err.value)
    assert "insufficient storage on /srv/trainer" in msg
    assert "507" in msg


def test_not_found_message_carries_no_internal_host_and_keeps_its_class():
    rec = _Recorder(status=404, text="no such job")
    with pytest.raises(tc.TrainingJobNotFound) as err:
        _client(rec).get_job("train-1")
    assert HOST not in str(err.value)
    assert err.value.status_code == 404


def test_status_code_is_still_what_callers_read():
    """`train_ops.conflict` tells the two 409s apart by the record, and the
    watcher tells a 404 from an outage by the code — not by message text."""
    rec = _Recorder(status=409, text="already terminal")
    with pytest.raises(tc.TrainingClientError) as err:
        _client(rec).cancel_job("train-1")
    assert err.value.status_code == 409


def test_transport_failure_says_nothing_about_where_it_was_going():
    import httpx

    class _Boom(_Recorder):
        def get(self, path, params=None):
            raise httpx.ConnectError("[Errno 111] Connection refused")

    rec = _Boom()
    with pytest.raises(tc.TrainingClientError) as err:
        _client(rec).get_job("train-1")
    msg = str(err.value)
    assert HOST not in msg
    assert "8300" not in msg
    assert "Trainer" in msg, "it still has to say what is unreachable"


def test_transport_failure_is_logged_with_the_url():
    """Dropped from the message, not lost: the operator still needs it."""
    import httpx
    from loguru import logger

    seen: list[str] = []
    sink = logger.add(lambda m: seen.append(m), level="WARNING")
    try:
        class _Boom(_Recorder):
            def get(self, path, params=None):
                raise httpx.ConnectError("refused")

        with pytest.raises(tc.TrainingClientError):
            _client(_Boom()).get_job("train-1")
    finally:
        logger.remove(sink)

    joined = "".join(seen)
    assert HOST in joined, "the gateway URL must still reach the log"
    assert "/train/jobs/train-1" in joined, "the path must still reach the log"


def test_sparql_graph_failure_does_not_echo_the_exception():
    """`/sparql` is require_role — any role-holder, not just an admin. An OSError
    here names the graph's absolute path."""
    src = open(BOT_PATH).read()
    start = src.find("[sparql] graph unavailable")
    if start == -1:
        start = src.find("Der Korpus-Graph ist nicht lesbar")
    assert start != -1, "the /sparql graph-failure branch moved"
    window = src[max(0, start - 600):start + 600]
    assert "_report_error" in window, (
        "the graph failure must go through _report_error (SEC-14, #585)")
    assert "nicht lesbar: {exc}" not in window, (
        "the raw exception is back in the channel message")


# ── Part 3: gate coverage — the rule that travels ────────────────────────────

def _registered_commands():
    import bot as bot_module
    return {
        c.name: c
        for c in bot_module.bot.pending_application_commands
        if getattr(c, "name", None)
    }


def test_every_command_is_gated_or_named_as_open():
    """The test SEC-1 did not have.

    `test_ah_105` names nine commands. Ten more arrived after the audit; all ten
    happen to be gated, and the suite would have been just as green if none of
    them were. This walks what py-cord actually registered, so a new ungated
    command is a failure with a decision attached to it.
    """
    import bot as bot_module

    commands = _registered_commands()
    assert len(commands) > 30, f"only {len(commands)} commands found — did the import fail?"

    ungated = sorted(
        name for name, cmd in commands.items()
        if not hasattr(cmd.callback, bot_module.GATE_ATTR)
        and name not in bot_module.OPEN_COMMANDS
    )
    assert not ungated, (
        f"ungated command(s) {ungated}: add a gate (@require_role / @admin_only, "
        f"BELOW @bot.slash_command) or, if it is genuinely read-only, add it to "
        f"bot.OPEN_COMMANDS with the reason")


def test_open_commands_all_exist():
    """A stale allowlist silently widens: an entry for a renamed command would
    excuse a *new* command that happens to take the old name."""
    commands = _registered_commands()
    missing = sorted(set.difference(set(__import__("bot").OPEN_COMMANDS), set(commands)))
    assert not missing, f"bot.OPEN_COMMANDS names commands that do not exist: {missing}"


def test_open_commands_are_really_ungated():
    """The other direction: an entry on the allowlist for a command that *is*
    gated is noise, and noise is what nobody re-reads."""
    import bot as bot_module

    commands = _registered_commands()
    redundant = sorted(
        name for name in bot_module.OPEN_COMMANDS
        if name in commands and hasattr(commands[name].callback, bot_module.GATE_ATTR)
    )
    assert not redundant, (
        f"these are gated, so remove them from OPEN_COMMANDS: {redundant}")


def test_every_open_command_has_a_reason():
    import bot as bot_module

    for name, reason in bot_module.OPEN_COMMANDS.items():
        assert isinstance(reason, str) and len(reason) > 15, (
            f"/{name} needs a reason saying why it may stay open, not {reason!r}")


def test_gate_marker_survives_functools_wraps():
    """`functools.wraps` copies `__dict__` from the wrapped function, so a marker
    set before it is silently erased — and the coverage test above would then
    report every command as ungated (or, worse, be 'fixed' by dropping it)."""
    import bot as bot_module

    async def plain(ctx):
        return "ok"

    for decorator, expected in (
        (bot_module.require_role, "require_role"),
        (bot_module.admin_only, "admin_only"),
        (bot_module.deploy_admin_only, "deploy_admin_only"),
    ):
        wrapped = decorator(plain)
        assert getattr(wrapped, bot_module.GATE_ATTR, None) == expected
        assert wrapped.__name__ == "plain", "py-cord needs the metadata preserved"


def test_marker_is_not_merely_dunder_wrapped():
    """Why a marker and not `hasattr(callback, '__wrapped__')`: any
    `functools.wraps` decorator sets that, so such a check cannot tell a gate
    from an unrelated wrapper — it passes while the gate is gone."""
    import functools

    import bot as bot_module

    def unrelated(func):
        @functools.wraps(func)
        async def wrapper(ctx, *a, **k):
            return await func(ctx, *a, **k)
        return wrapper

    async def plain(ctx):
        return "ok"

    decorated = unrelated(plain)
    assert hasattr(decorated, "__wrapped__"), "precondition"
    assert not hasattr(decorated, bot_module.GATE_ATTR), (
        "an unrelated wrapper must not read as a gate")


def test_gates_sit_below_the_command_decorator_everywhere():
    """Position, not just presence. A gate above `@bot.slash_command` leaves
    py-cord registering the *un-gated* callback — the gate becomes dead code.
    `test_ah_105` checks this for the commands it names; this checks all of them.
    """
    src = open(BOT_PATH).read()
    tree = ast.parse(src)
    GATES = {"require_role", "admin_only", "deploy_admin_only"}
    offenders = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        rendered = [ast.unparse(d) for d in node.decorator_list]
        if not any("slash_command" in r for r in rendered):
            continue
        cmd_at = next(i for i, r in enumerate(rendered) if "slash_command" in r)
        above = [r for r in rendered[:cmd_at] if r in GATES]
        if above:
            offenders.append((node.name, above))

    assert not offenders, (
        f"gate(s) above @bot.slash_command, so the un-gated callback is the one "
        f"registered: {offenders}")
