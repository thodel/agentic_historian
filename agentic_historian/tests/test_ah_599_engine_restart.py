"""The engines a run needs, and getting them back — #599.

The run this is written for: `missiven`, 2026-10-09, **16 of 20 recognitions**
lost to

    kraken engine unreachable at http://127.0.0.1:8201/recognize

The gateway answered, the engine did not, the run asked twenty times anyway and
published what was left. Two things were missing: nobody read ``/health`` before
the first image, and nobody could restart anything from this host.

Offline. ``httpx.MockTransport`` is the seam for the gateway, which also keeps
the suite honest about the thing that is easy to get wrong here — that a *status
code* decides the outcome, and that five of the six outcomes are not successes.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import httpx
import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_engines as ae      # noqa: E402
import batch_runner as br     # noqa: E402
import config                 # noqa: E402
import corpus_manifest as cm  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "ENABLE_GITHUB_PUBLISH", False)
    monkeypatch.setattr(config, "ATR_GATEWAY_URL", "http://gw:8200")
    monkeypatch.setattr(config, "ATR_API_KEY", "k")
    cm.close()
    yield
    cm.close()


@pytest.fixture
def gateway(monkeypatch):
    """Answer the gateway's routes from a handler the test sets."""
    seen: list[httpx.Request] = []
    routes: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = routes.get(request.url.path)
        if answer is None:
            return httpx.Response(404, json={"detail": "no such route"})
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return answer(request)
        return answer

    real = httpx.AsyncClient

    class Client(real):                                   # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    return type("GW", (), {"routes": routes, "seen": seen})()


def _health(*engines) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "version": "1", "model_count": 3,
                                     "engines": list(engines)})


def E(name, reachable=True, busy=None, url=None):
    out = {"name": name, "url": url or f"http://{name}"}
    if reachable is not ...:
        out["reachable"] = reachable
    if busy is not None:
        out["busy"] = busy
    return out


# ── reading /health: three-valued, and tolerant ─────────────────────────────

def test_a_refused_engine_is_down():
    state = ae.read_health({"engines": [E("kraken", reachable=False)]})["kraken"]
    assert state.reachable is False
    assert state.down is True


def test_a_busy_engine_is_not_down():
    """The 17.09. case: party read pages for 30-80 s and probed as unreachable.

    This is the single most important line in the module. A process that treats
    a read timeout as death restarts an engine in the middle of the page it is
    reading, and taking 80 s over a page is normal here.
    """
    state = ae.read_health({"engines": [E("party", reachable=False, busy=True)]})["party"]
    assert state.down is False


def test_an_engine_health_did_not_mention_is_not_down_it_is_unmeasured():
    """``None`` is a third value, not a slow way of writing ``False``."""
    state = ae.read_health({"engines": [E("kraken", reachable=...)]})["kraken"]
    assert state.reachable is None
    assert state.down is False


def test_a_non_boolean_reachable_is_read_as_unmeasured():
    state = ae.read_health({"engines": [{"name": "kraken", "reachable": "yes"}]})["kraken"]
    assert state.reachable is None


def test_a_payload_without_engines_is_an_empty_map_not_an_exception():
    """A gateway in a bad way must cost a thinner answer, not a traceback on the
    one code path whose job is to survive the far side being in a bad way."""
    assert ae.read_health({"status": "ok"}) == {}
    assert ae.read_health(None) == {}
    assert ae.read_health({"engines": [{"url": "http://x"}, "junk", 7]}) == {}


# ── the preflight ───────────────────────────────────────────────────────────

def test_a_run_whose_engines_all_answer_is_ok():
    result = ae.preflight({"engines": [E("kraken"), E("trocr")]}, ["kraken", "trocr"])
    assert result.ok and result.down == ()


def test_a_down_engine_blocks_and_is_named():
    result = ae.preflight({"engines": [E("kraken", reachable=False), E("trocr")]},
                          ["vlm", "kraken", "trocr"])
    assert not result.ok
    assert result.down == ("kraken",)
    assert "kraken" in ae.format_preflight(result)


def test_vlm_is_never_in_health_and_must_not_block_a_run():
    """The gateway spawns vLLM as its own subprocesses and does not probe it.

    So silence about vlm is the normal case, every run, for ever. A preflight
    that read it as absence would refuse every run ever started — which is why
    ``unmeasured`` exists as a separate field from ``down``.
    """
    result = ae.preflight({"engines": [E("kraken"), E("trocr")]}, ae.PLANNED_ENGINES)
    assert result.ok
    assert result.unmeasured == ("vlm",)
    # Said, not hidden: a reader must not take the silence for a check.
    assert "vlm" in ae.format_preflight(result)


def test_a_busy_engine_does_not_block_a_run():
    result = ae.preflight({"engines": [E("kraken", reachable=False, busy=True)]},
                          ["kraken"])
    assert result.ok


def test_an_engine_without_a_unit_is_reported_as_unfixable():
    result = ae.preflight({"engines": [E("vllm-ish", reachable=False)]}, ["vllm-ish"])
    assert result.down == ("vllm-ish",)
    assert result.fixable == ()
    assert result.unrestartable == ("vllm-ish",)
    assert "nicht behebbar" in ae.format_preflight(result)


def test_an_unreachable_gateway_is_its_own_fact():
    """Not "every engine is down": that sends the reader to idhefix to inspect
    engines which are, as far as anybody knows, fine."""
    result = ae.Preflight(needed=("kraken",), gateway_error="ConnectError an http://gw")
    assert not result.ok
    assert result.down == ()
    text = ae.format_preflight(result)
    assert "Gateway" in text and "ConnectError" in text
    assert "antworten nicht" not in text


def test_an_already_read_map_is_not_read_again():
    engines = ae.read_health({"engines": [E("kraken", reachable=False)]})
    assert ae.preflight(engines, ["kraken"]).down == ("kraken",)


def test_the_needed_engines_come_from_the_model_plan():
    class Pick:
        def __init__(self, engine):
            self.engine = engine
    picks = [Pick("vlm"), Pick("kraken"), Pick("trocr"), Pick("kraken")]
    assert ae.plan_engines(picks) == ("vlm", "kraken", "trocr")
    assert ae.plan_engines([{"engine": "KRAKEN"}, {"engine": " trocr "}]) == ("kraken", "trocr")


def test_the_planned_engine_set_matches_what_the_ensemble_can_emit():
    """A guard, and blunt like one: it reads ``ensemble.py`` for the engines
    ``plan_models`` names. ``PLANNED_ENGINES`` is what a batch assumes it will
    need, and a plan that grew a fourth engine would otherwise be preflighted
    against three.
    """
    source = (PKG / "agent_a" / "ensemble.py").read_text(encoding="utf-8")
    plan = source[source.index("def plan_models("):]
    plan = plan[:plan.index("\n# ")] if "\n# " in plan else plan
    emitted = set(re.findall(r'ModelPick\(\s*"([a-z]+)"', plan))
    assert emitted == set(ae.PLANNED_ENGINES), emitted


# ── asking for a restart: every status is its own answer ────────────────────

def _restart(engine="kraken"):
    return asyncio.run(ae.restart(engine))


def test_a_successful_restart_that_comes_back_is_restarted(gateway):
    gateway.routes["/engines/kraken/restart"] = httpx.Response(
        200, json={"engine": "kraken", "unit": "atr-kraken.service",
                   "restarted": True, "reachable": True, "detail": "answers again"})
    outcome = _restart()
    assert outcome.outcome == ae.RESTARTED and outcome.ok


def test_a_restart_that_does_not_come_back_is_still_down_not_restarted(gateway):
    """The gateway answers 200 — it did restart the unit. The engine is the
    separate question, and conflating the two is how a caller reports success
    for a service nobody can use."""
    gateway.routes["/engines/kraken/restart"] = httpx.Response(
        200, json={"restarted": True, "reachable": False})
    outcome = _restart()
    assert outcome.outcome == ae.STILL_DOWN
    assert not outcome.ok
    assert "Kaltstart" in ae.format_restart(outcome)


def test_a_gateway_without_the_route_is_unsupported_not_failed(gateway):
    """404 means this gateway has not been deployed with #209. Telling an
    operator "restart failed" sends them to look at a healthy engine."""
    outcome = _restart()          # no route registered → the handler 404s
    assert outcome.outcome == ae.UNSUPPORTED
    assert "209" in outcome.detail


def test_a_405_is_also_unsupported(gateway):
    gateway.routes["/engines/kraken/restart"] = httpx.Response(405)
    assert _restart().outcome == ae.UNSUPPORTED


def test_a_rejected_key_is_refused(gateway):
    gateway.routes["/engines/kraken/restart"] = httpx.Response(401)
    assert _restart().outcome == ae.REFUSED


def test_a_busy_engine_is_reported_as_busy(gateway):
    gateway.routes["/engines/kraken/restart"] = httpx.Response(409, json={"detail": "busy"})
    outcome = _restart()
    assert outcome.outcome == ae.BUSY
    assert "nichts neu gestartet" in outcome.detail


def test_another_error_status_is_a_failure_that_carries_the_body(gateway):
    gateway.routes["/engines/kraken/restart"] = httpx.Response(
        502, json={"detail": "systemctl exited 5"})
    outcome = _restart()
    assert outcome.outcome == ae.FAILED
    assert "systemctl exited 5" in outcome.detail


def test_a_transport_error_is_unknown_not_failed(gateway):
    """A request that died in transit may well have restarted the engine. A
    caller that books this as a failure asks again, and asking again is how one
    restart becomes five."""
    gateway.routes["/engines/kraken/restart"] = httpx.ConnectError("refused")
    outcome = _restart()
    assert outcome.outcome == ae.UNKNOWN
    assert outcome.outcome != ae.FAILED


def test_an_engine_without_a_unit_is_refused_without_a_call(gateway):
    outcome = _restart("vlm")
    assert outcome.outcome == ae.UNSUPPORTED
    assert gateway.seen == []


def test_the_restart_sends_the_api_key(gateway):
    gateway.routes["/engines/kraken/restart"] = httpx.Response(
        200, json={"reachable": True})
    _restart()
    assert gateway.seen[0].headers.get("x-api-key") == "k"
    assert gateway.seen[0].method == "POST"


# ── /health reads, and the ledger ───────────────────────────────────────────

def test_health_reports_its_own_failure_rather_than_raising(gateway):
    gateway.routes["/health"] = httpx.ConnectError("no route")
    payload, error = asyncio.run(ae.health())
    assert payload == {}
    assert "ConnectError" in error and "gw:8200" in error


def test_check_turns_an_unreachable_gateway_into_a_blocking_preflight(gateway):
    gateway.routes["/health"] = httpx.ConnectError("no route")
    result = asyncio.run(ae.check(["kraken"]))
    assert not result.ok and result.gateway_error
    assert result.down == ()


def test_check_needs_no_key(gateway):
    """``/health`` is the one route the gateway serves unkeyed — which is what
    makes this work when the key itself is the problem."""
    gateway.routes["/health"] = _health(E("kraken"), E("trocr"))
    asyncio.run(ae.check(["kraken"]))
    assert "x-api-key" not in {k.lower() for k in gateway.seen[0].headers}


def test_an_engine_is_restarted_once_per_cooldown():
    ledger = ae.Ledger(cooldown_s=900.0)
    assert ledger.may("kraken", 0.0)
    ledger.note("kraken", 0.0)
    assert not ledger.may("kraken", 100.0)
    assert ledger.may("kraken", 1000.0)
    assert ledger.may("trocr", 100.0)


def test_the_second_failure_of_an_engine_is_reported_not_retried(gateway):
    """An engine that cannot stay up is a thing to be told about, and a process
    that keeps restarting it is a process that hides it."""
    gateway.routes["/engines/kraken/restart"] = httpx.Response(200, json={"reachable": True})
    result = ae.preflight({"engines": [E("kraken", reachable=False)]}, ["kraken"])
    ledger = ae.Ledger()

    first = asyncio.run(ae.recover(result, ledger, 0.0))
    second = asyncio.run(ae.recover(result, ledger, 60.0))

    assert [o.outcome for o in first] == [ae.RESTARTED]
    assert [o.outcome for o in second] == [ae.FAILED]
    assert "nicht eines fehlenden Neustarts" in second[0].detail
    assert len(gateway.seen) == 1


def test_recover_never_touches_an_engine_without_a_unit(gateway):
    result = ae.preflight({"engines": [E("nope", reachable=False)]}, ["nope"])
    assert asyncio.run(ae.recover(result, ae.Ledger(), 0.0)) == []
    assert gateway.seen == []


# ── the gate in front of a run ──────────────────────────────────────────────

def _pages(root, names=("001r.jpg", "002v.jpg")):
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / name).write_bytes(b"\xff\xd8\xff")
    return root


def _ok(needed=None, **kw):
    return ae.Preflight(needed=tuple(needed or ae.PLANNED_ENGINES))


def _down(needed=None, **kw):
    return ae.Preflight(needed=tuple(needed or ae.PLANNED_ENGINES),
                        down=("kraken",))


def test_a_run_with_a_dead_engine_is_refused_before_it_claims_anything(tmp_path):
    """The measured alternative cost 16 recognitions and published the result."""
    source = br.inspect_source(_pages(tmp_path / "b"))
    ran: list = []

    with pytest.raises(br.EnginesDown) as caught:
        br.run_batch("run", source, workers=1,
                     pipeline=lambda *a: ran.append(a),
                     check_engines=_down, sleep=lambda _s: None)

    assert ran == []
    assert "kraken" in str(caught.value)
    # Nothing claimed, so a later run is not resuming a run that never started.
    assert cm.progress("run").total == 0


def test_a_run_with_live_engines_starts_and_says_so(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "b"))
    said: list = []

    br.run_batch("run", source, workers=1, pipeline=lambda *a: None,
                 check_engines=_ok, announce=said.append, sleep=lambda _s: None)

    assert said[0].startswith("Engine-Preflight ok")


def test_with_restart_engines_the_run_restarts_rechecks_and_starts(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "b"))
    said: list = []
    checks = [_down(), _ok()]
    recovered: list = []

    def check(needed=None, **kw):
        return checks.pop(0)

    def recover(result, ledger, now):
        recovered.append(result.fixable)
        return [ae.RestartOutcome(engine="kraken", outcome=ae.RESTARTED, reachable=True)]

    summary = br.run_batch("run", source, workers=1, pipeline=lambda *a: None,
                           restart_engines=True, check_engines=check,
                           recover_engines=recover, announce=said.append,
                           sleep=lambda _s: None)

    assert recovered == [("kraken",)]
    assert summary.progress.done == 2
    # The re-check is not optional: without it the run would start on the word
    # of a restart request rather than on a reading.
    assert checks == []
    assert any("neu gestartet und antwortet wieder" in s for s in said)


def test_a_restart_that_does_not_help_still_refuses_the_run(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "b"))
    ran: list = []

    def recover(result, ledger, now):
        return [ae.RestartOutcome(engine="kraken", outcome=ae.STILL_DOWN)]

    with pytest.raises(br.EnginesDown):
        br.run_batch("run", source, workers=1, pipeline=lambda *a: ran.append(a),
                     restart_engines=True, check_engines=_down,
                     recover_engines=recover, sleep=lambda _s: None)
    assert ran == []


def test_restart_engines_does_not_promise_what_it_cannot_do(tmp_path):
    """Nothing a restart can reach — no unit, or a gateway nobody could ask.

    Saying "restarting" and then not restarting is the lie this branch exists to
    avoid, so ``recover`` is never called.
    """
    source = br.inspect_source(_pages(tmp_path / "b"))
    called: list = []

    def check(needed=None, **kw):
        return ae.Preflight(needed=("kraken",),
                            gateway_error="ConnectError an http://gw:8200/health")

    with pytest.raises(br.EnginesDown):
        br.run_batch("run", source, workers=1, pipeline=lambda *a: None,
                     restart_engines=True, check_engines=check,
                     recover_engines=lambda *a: called.append(a) or [],
                     sleep=lambda _s: None)
    assert called == []


def test_the_runner_asks_for_the_engines_it_was_given(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "b"))
    asked: list = []

    def check(needed=None, **kw):
        asked.append(needed)
        return _ok(needed)

    br.run_batch("run", source, workers=1, pipeline=lambda *a: None,
                 engines=("kraken",), check_engines=check, sleep=lambda _s: None)
    assert asked == [("kraken",)]


def test_the_default_needed_set_is_what_a_plan_will_ask_for(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "b"))
    asked: list = []

    def check(needed=None, **kw):
        asked.append(needed)
        return _ok(needed)

    br.run_batch("run", source, workers=1, pipeline=lambda *a: None,
                 check_engines=check, sleep=lambda _s: None)
    assert asked == [None]        # None → atr_engines.check uses PLANNED_ENGINES


def test_check_defaults_to_the_planned_engines(gateway):
    gateway.routes["/health"] = _health(E("kraken"), E("trocr"))
    result = asyncio.run(ae.check())
    assert result.needed == ae.PLANNED_ENGINES


# ── the Discord side ────────────────────────────────────────────────────────

def test_the_restart_command_is_admin_gated_and_confirmed():
    """A guard on the source, because the alternative is a live Discord client.

    ``atr_status`` deferred write access and said what it would need: its own
    confirm flow and its own decision about who may (#414). Both are structural,
    and both are easy to lose in a later edit that only means to add an option.
    """
    source = (PKG / "bot.py").read_text(encoding="utf-8")
    block = source[source.index('name="atr_restart"'):]
    block = (block[:block.index("\n@bot.slash_command")]
             if "\n@bot.slash_command" in block else block)
    # The decorators sit between the command's name and its function.
    decorators = block[:block.index("async def")]
    assert "@admin_only" in decorators, \
        "/atr_restart must carry @admin_only, not @require_role"
    assert "@require_role" not in decorators, \
        "the base role gate is not enough for the first write to the machines"
    assert "_RestartView" in block, "/atr_restart must go through a Confirm view"
    # The confirm view, not the command, performs the restart.
    view = source[source.index("class _RestartView"):]
    view = view[:view.index("\n@bot.slash_command")]
    assert "atr_engines.restart(" in view


def test_the_command_choices_match_what_can_actually_be_restarted():
    """The literal next to the decorator and the module's list must agree: the
    decorator runs at import, so it cannot read the module, and a drifted choice
    would offer a restart that the gateway refuses with a 404."""
    import bot
    assert tuple(bot._RESTARTABLE_CHOICES) == ae.RESTARTABLE


def test_the_gateway_is_not_restartable_at_all():
    assert "gateway" not in ae.RESTARTABLE
    assert "train" not in ae.RESTARTABLE
    assert asyncio.run(ae.restart("gateway")).outcome == ae.UNSUPPORTED
