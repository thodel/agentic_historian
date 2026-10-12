"""
test_ah_364_train_ops.py — /train_log and /train_cancel, offline (#364).

The acceptance list of the issue, as tests:

  1. The log tail is cut to fit Discord, keeps the END, is marked as cut, and
     sits in a code block.
  2. An unknown stage lists the five that exist.
  3. `/train_cancel` is admin-gated and submits nothing without a click.
  4. A 409 on a job that is already over says "already <status>" — and the
     OTHER 409 the route gives, for a job being started this instant, says the
     opposite instead of being collapsed into it.
  5. After a cancel the watcher reports the terminal state and stops watching
     (that part is #363's loop; here it is exercised through a cancel).

Plus the contract fix the log command cannot work without: the trainer answers
`{"lines": [...]}`, not `{"log": "..."}`.

Run: pytest agentic_historian/tests/test_ah_364_train_ops.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                      # noqa: E402
import train_ops                                   # noqa: E402
import train_watch as tw                           # noqa: E402
from agent_a.training_client import (              # noqa: E402
    TrainJob,
    TrainingClientError,
    TrainingJobNotFound,
)


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def _record(**over) -> dict:
    rec = {
        "id": "20261012T090000Z-medieval-smoke",
        "status": "training",
        "stage": "train",
        "request": {"engine": "kraken", "model_id": "dh-unibe/medieval-v3"},
        "created_at": "2026-10-12T09:00:00Z",
        "started_at": "2026-10-12T09:01:00Z",
        "stages": [
            {"name": "prepare", "status": "completed"},
            {"name": "compile", "status": "completed"},
            {"name": "train", "status": "running"},
            {"name": "test", "status": "pending"},
            {"name": "register", "status": "pending"},
        ],
    }
    rec.update(over)
    return rec


JOB_ID = "20261012T090000Z-medieval-smoke"


# ── 1. the log tail ─────────────────────────────────────────────────────────

def test_the_tail_keeps_the_end_of_the_log():
    """A traceback is at the end. A cut that keeps the beginning throws away the
    only part anybody opened the command for."""
    lines = [f"line {i:04d} " + "x" * 80 for i in range(400)]
    out = train_ops.log_tail(lines)
    assert "line 0399" in out
    assert "line 0000" not in out


def test_the_tail_fits_in_a_discord_message():
    lines = [f"line {i:04d} " + "x" * 120 for i in range(500)]
    out = train_ops.log_tail(lines)
    assert len(out) < 2000


def test_the_cut_is_marked_and_says_how_much_was_dropped():
    """An unmarked tail reads as the whole log — and then the missing line is
    the one being looked for."""
    lines = [f"line {i:04d} " + "x" * 100 for i in range(300)]
    out = train_ops.log_tail(lines)
    assert "gekürzt" in out
    assert "von 300 Zeilen" in out


def test_a_short_log_is_not_marked_as_cut():
    out = train_ops.log_tail(["step 1", "step 2"])
    assert "gekürzt" not in out
    assert "step 1" in out and "step 2" in out


def test_the_tail_is_in_a_code_block():
    out = train_ops.log_tail(["RuntimeError: CUDA out of memory"])
    assert out.startswith("```") and out.rstrip().endswith("```")


def test_whole_lines_are_dropped_not_halves():
    """A line cut in the middle looks like a line the trainer wrote that way."""
    lines = [f"{i:04d}:" + "y" * 200 for i in range(100)]
    out = train_ops.log_tail(lines)
    body = out.split("```")[1]
    for row in body.strip().splitlines():
        if row.startswith("…"):
            continue
        assert len(row) == 205, row


def test_one_line_longer_than_the_whole_budget_keeps_its_end():
    """The pathological case: a single line of 5000 characters. Its end is where
    the error is."""
    out = train_ops.log_tail(["A" * 3000 + "ERROR: the actual problem"])
    assert "ERROR: the actual problem" in out
    assert len(out) < 2000


def test_an_empty_log_yields_nothing_to_post():
    assert train_ops.log_tail([]) == ""
    assert train_ops.log_tail(None) == ""


def test_a_string_log_is_split_into_lines():
    out = train_ops.log_tail("first\nsecond\nthird")
    assert "third" in out


def test_a_payload_of_the_wrong_shape_is_not_read_character_by_character():
    """#595's mistake: a str/dict iterated as a sequence of readings."""
    assert train_ops.log_tail({"lines": ["a"]}) == ""
    assert train_ops.log_tail(42) == ""


# ── 2. the stages ──────────────────────────────────────────────────────────

def test_an_unknown_stage_lists_the_five_that_exist():
    out = train_ops.unknown_stage("quantise")
    for stage in train_ops.STAGES:
        assert f"`{stage}`" in out
    assert "quantise" in out


def test_the_five_stages_are_the_trainers_own():
    assert train_ops.STAGES == ("prepare", "compile", "train", "test", "register")
    assert train_ops.STAGES == tw.STAGES


def test_no_log_for_a_stage_names_the_stages_the_job_does_have():
    """The trainer's 404 reads "no test log for job X", which tells nobody what
    to ask for instead."""
    job = TrainJob.from_dict(_record())
    out = train_ops.stage_hint("test", job)
    assert "`prepare` (completed)" in out
    assert "`train` (running)" in out
    assert "`register`" not in out          # pending: it has not begun
    assert JOB_ID in out


def test_a_queued_job_has_no_stages_and_says_so():
    """"Not yet" and "never will be" are different answers."""
    job = TrainJob.from_dict(_record(status="queued", stage=None, stages=[]))
    out = train_ops.stage_hint("train", job)
    assert "noch keine Stufe" in out
    assert "queued" in out


# ── 4. the two meanings of 409 ─────────────────────────────────────────────

def test_a_finished_job_is_reported_as_already_that():
    job = TrainJob.from_dict(_record(status="completed", stage=None,
                                     finished_at="2026-10-12T15:31:00Z"))
    out = train_ops.conflict(JOB_ID, job)
    assert "schon `completed`" in out
    assert "nichts abzubrechen" in out


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_every_terminal_status_is_named_in_the_refusal(status):
    job = TrainJob.from_dict(_record(status=status, stage=None))
    assert f"`{status}`" in train_ops.conflict(JOB_ID, job)


def test_a_job_being_started_right_now_is_not_called_finished():
    """The route's OTHER 409 (`_cancel_unstarted` holds the claim). Told the
    wrong one, an operator stops waiting for a run that is about to start."""
    job = TrainJob.from_dict(_record(status="queued", stage=None, stages=[],
                                     queued_reason="another job is running"))
    out = train_ops.conflict(JOB_ID, job)
    assert "in diesem Moment gestartet" in out
    assert "schon" not in out
    assert "nochmal" in out                 # what to actually do


def test_the_two_conflicts_do_not_share_a_sentence():
    over = TrainJob.from_dict(_record(status="cancelled", stage=None))
    starting = TrainJob.from_dict(_record(status="queued", stage=None, stages=[]))
    assert train_ops.conflict(JOB_ID, over) != train_ops.conflict(JOB_ID, starting)


def test_an_unfetchable_record_passes_the_trainers_own_detail_on():
    """Rather than guessing which 409 it was."""
    out = train_ops.conflict(JOB_ID, None, "job is being started right now")
    assert "being started right now" in out


# ── what a cancel reports ──────────────────────────────────────────────────

def test_a_cancel_that_took_effect_says_so():
    job = TrainJob.from_dict(_record(status="cancelled", stage=None,
                                     error="cancelled before it started"))
    out = train_ops.cancel_result(job)
    assert "abgebrochen" in out
    assert "cancelled before it started" in out


def test_a_cancel_whose_status_has_not_caught_up_does_not_claim_it_did():
    """The trainer SIGTERMs and the **runner** marks the job cancelled, so the
    record that comes back can still say `training`. Saying "cancelled" then is
    a claim about something that has not happened."""
    job = TrainJob.from_dict(_record(status="training"))
    out = train_ops.cancel_result(job)
    assert "abgeschickt" in out
    assert "`training`" in out
    assert "ist abgebrochen" not in out


def test_a_run_that_finished_between_the_click_and_the_call_is_not_called_cancelled():
    job = TrainJob.from_dict(_record(status="completed", stage=None))
    out = train_ops.cancel_result(job)
    assert "completed" in out
    assert "nichts abgebrochen" in out


# ── the contract the log command reads ─────────────────────────────────────

def test_the_trainer_answers_lines_not_log():
    """`get_log` read `log`, a key the route does not have, so every log that
    existed came back as "" — "the stage has no output yet" for a run that was
    printing one."""
    from unittest.mock import MagicMock

    from agent_a.training_client import TrainingClient

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"job_id": "j1", "stage": "train",
                                  "lines": ["epoch 1", "epoch 2"]}
    client = TrainingClient(base_url="http://localhost")
    client._client = MagicMock()
    client._client.get.return_value = response

    assert client.get_log("j1") == "epoch 1\nepoch 2"


def test_the_requested_line_count_reaches_the_gateway():
    from unittest.mock import MagicMock

    from agent_a.training_client import TrainingClient

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"lines": []}
    client = TrainingClient(base_url="http://localhost")
    client._client = MagicMock()
    client._client.get.return_value = response

    client.get_log("j1", lines=80)
    params = client._client.get.call_args[1]["params"]
    assert params["lines"] == 80


def test_a_stage_without_a_log_arrives_as_the_404_class():
    """Not an empty list: the route raises 404 for a log that is not on disk."""
    from unittest.mock import MagicMock

    from agent_a.training_client import TrainingClient

    response = MagicMock()
    response.status_code = 404
    response.text = '{"detail":"no test log for job j1"}'
    client = TrainingClient(base_url="http://localhost")
    client._client = MagicMock()
    client._client.get.return_value = response

    with pytest.raises(TrainingJobNotFound):
        client.get_log("j1")


def test_the_client_error_carries_the_status_code():
    """So no caller has to match this module's own wording (#364)."""
    from unittest.mock import MagicMock

    from agent_a.training_client import TrainingClient

    response = MagicMock()
    response.status_code = 409
    response.text = "already completed"
    client = TrainingClient(base_url="http://localhost")
    client._client = MagicMock()
    client._client.post.return_value = response

    with pytest.raises(TrainingClientError) as caught:
        client.cancel_job("j1")
    assert caught.value.status_code == 409


def test_the_one_shot_cancel_helper_closes_its_client(monkeypatch):
    """Like `submit_training_job` (#362): a client entered by hand leaks its
    pool."""
    import agent_a.training_client as tc

    closed = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        def post(self, path, json=None):
            from unittest.mock import MagicMock
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = _record(status="cancelled")
            return resp

        def close(self):
            closed.append(True)

    monkeypatch.setattr(tc.httpx, "Client", _Client)
    job = tc.cancel_training_job("j1", base_url="http://localhost")
    assert job.status.value == "cancelled"
    assert closed == [True]


# ── 5. after a cancel, the watcher closes the run out ──────────────────────

def test_after_a_cancel_the_watcher_reports_and_stops_watching():
    """#363's loop, reached through a cancel: the terminal state is announced
    once and the run leaves the registry."""
    tw.register(tw.WatchedRun(job_id=JOB_ID, channel_id="555", message_id="777",
                              started_by="42", preset="medieval-smoke",
                              last_status="training",
                              stages_told=["prepare", "compile", "train"]))
    cancelled = _record(status="cancelled", stage=None,
                        finished_at="2026-10-12T11:01:00Z",
                        error="cancelled on request")
    update, fresh = tw.decide(tw.find(JOB_ID), TrainJob.from_dict(cancelled))
    assert update.terminal is True
    assert "abgebrochen" in update.notice
    assert update.urgent is False       # somebody's own doing is not an alarm
    tw.drop(fresh.job_id)
    assert tw.find(JOB_ID) is None


# ── the bot side ───────────────────────────────────────────────────────────

def _source() -> str:
    return (PKG / "bot.py").read_text(encoding="utf-8")


def test_train_cancel_is_admin_gated_and_train_log_role_gated():
    """A cancel destroys hours of shared work; reading a log does not."""
    source = _source()
    block = source[source.index('name="train_cancel"'):]
    decorators = block[:block.index("async def")]
    assert "@admin_only" in decorators
    assert "@require_role" not in decorators

    block = source[source.index('name="train_log"'):]
    decorators = block[:block.index("async def")]
    assert "@require_role" in decorators


def test_the_cancel_command_submits_nothing_itself():
    """Only the confirm callback cancels."""
    source = _source()
    block = source[source.index("async def train_cancel_cmd("):]
    block = block[:block.index("\n# ── /train_status")]
    assert "cancel_training_job" not in block
    assert "_TrainCancelView" in block


def test_the_cancel_view_is_persistent_and_recorded():
    import asyncio

    import bot as bot_module

    async def go():
        return bot_module._TrainCancelView(token="t", requester="1",
                                           job_id=JOB_ID)

    view = asyncio.run(go())
    assert view.timeout is None
    for item in view.children:
        assert getattr(item, "custom_id", "").startswith("train_cancel:")


def test_a_stored_cancel_confirmation_rebuilds_its_view():
    """It outlives a restart, like every other confirmation since #614."""
    import asyncio

    import bot as bot_module
    import pending_confirm

    entry = pending_confirm.Pending(
        token="t", kind="train_cancel", channel_id="1", message_id="2",
        requester="9", data={"job_id": JOB_ID})

    async def go():
        return bot_module._view_for_confirm(entry)

    view = asyncio.run(go())
    assert view is not None
    assert view.job_id == JOB_ID


def test_the_cancel_card_says_what_is_lost():
    source = _source()
    block = source[source.index("async def train_cancel_cmd("):]
    block = block[:block.index("\n# ── /train_status")]
    assert "Laufzeit" in block           # the hours that go away
    assert "SIGTERM" in block            # what actually happens


def test_a_terminal_job_is_refused_before_a_card_is_posted():
    """Asking "cancel this?" about a run that ended hours ago is noise."""
    source = _source()
    block = source[source.index("async def train_cancel_cmd("):]
    block = block[:block.index("\n# ── /train_status")]
    assert block.index("is_terminal()") < block.index("_make_token()")


def test_the_log_command_tells_the_two_404s_apart_by_the_record():
    source = _source()
    block = source[source.index("async def train_log_cmd("):]
    block = block[:block.index("\nclass _TrainCancelView")]
    assert "status_code" in block
    assert "atr_status.job(job_id)" in block     # the record decides
    assert "stage_hint" in block


def test_the_cancel_confirm_reads_the_status_code_not_the_message():
    source = _source()
    block = source[source.index("class _TrainCancelView"):]
    block = block[:block.index('name="train_cancel"')]
    assert 'status_code", None) != 409' in block
    assert '"409" not in' not in block


def test_both_commands_register_with_their_options():
    import bot as bot_module

    commands = {c.name: c for c in bot_module.bot.pending_application_commands}
    stage, lines = None, None
    for option in commands["train_log"].options:
        if option.name == "stage":
            stage = option
        if option.name == "lines":
            lines = option
    assert stage is not None and stage.required is False
    assert lines is not None and lines.required is False
    assert [c.value for c in stage.choices] == list(train_ops.STAGES)
    [job_id] = commands["train_cancel"].options
    assert job_id.name == "job_id" and job_id.required is True


def test_the_requested_line_count_is_capped_before_the_request():
    source = _source()
    block = source[source.index("async def train_log_cmd("):]
    block = block[:block.index("\nclass _TrainCancelView")]
    assert "LOG_LINES_MAX" in block
    assert train_ops.LOG_LINES_MAX <= 5000      # the trainer's own ceiling


def test_atr_progress_cuts_its_log_at_the_same_end():
    """The bug lived here too: `_fence` keeps `body[:limit]`."""
    import atr_status

    payload = {"lines": [f"line {i:04d} " + "z" * 200 for i in range(40)]}
    out = atr_status.format_log(payload, "j1")
    assert "line 0039" in out
    assert len(out) < 2000


# ── the confirm callback, end to end ───────────────────────────────────────

def _pending(token="t"):
    import pending_confirm

    pending_confirm.add(pending_confirm.Pending(
        token=token, kind="train_cancel", channel_id="1", message_id="2",
        requester="9", data={"job_id": JOB_ID}))


def _interaction():
    from unittest.mock import AsyncMock, MagicMock

    interaction = MagicMock()
    interaction.user.id = 9
    interaction.response.defer = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _click(interaction, cancel_impl, *, job_payload=None, button="confirm"):
    """Run one click of the cancel view with the trainer's answer stubbed."""
    import asyncio

    import agent_a.training_client as tc
    import atr_status
    import bot as bot_module

    async def _job(job_id):
        if job_payload is None:
            raise atr_status.AtrStatusError("nicht erreichbar")
        return job_payload

    original_cancel = tc.cancel_training_job
    original_job = atr_status.job
    try:
        tc.cancel_training_job = cancel_impl
        atr_status.job = _job

        async def go():
            view = bot_module._TrainCancelView(token="t", requester="9",
                                               job_id=JOB_ID)
            await getattr(view, button).callback(interaction)

        asyncio.run(go())
    finally:
        tc.cancel_training_job = original_cancel
        atr_status.job = original_job


def test_a_confirmed_cancel_sends_the_cancel_and_reports_the_record():
    import pending_confirm

    _pending()
    interaction = _interaction()
    seen = []

    def _cancel(job_id):
        seen.append(job_id)
        return TrainJob.from_dict(_record(status="cancelled", stage=None))

    _click(interaction, _cancel)

    assert seen == [JOB_ID]
    [said] = [c.args[0] for c in interaction.followup.send.call_args_list]
    assert "abgebrochen" in said
    # The record is gone, so a second click cannot cancel twice.
    assert pending_confirm.lookup("t")[0] == pending_confirm.MISSING


def test_declining_cancels_nothing():
    import pending_confirm

    _pending()
    interaction = _interaction()

    def _cancel(job_id):           # pragma: no cover — must not be reached
        raise AssertionError("the trainer was called without a confirmation")

    _click(interaction, _cancel, button="keep")

    said = interaction.response.edit_message.call_args[1]["content"]
    assert "Nichts abgebrochen" in said
    assert pending_confirm.lookup("t")[0] == pending_confirm.MISSING


def test_an_expired_confirmation_cancels_nothing_and_says_why():
    import pending_confirm
    import time

    entry = pending_confirm.Pending(
        token="t", kind="train_cancel", channel_id="1", message_id="2",
        requester="9", data={"job_id": JOB_ID})
    entry.created_at = time.time() - pending_confirm.TTL_S - 60
    pending_confirm.save([entry])

    interaction = _interaction()

    def _cancel(job_id):           # pragma: no cover
        raise AssertionError("an expired confirmation cancelled a run")

    _click(interaction, _cancel)

    said = interaction.followup.send.call_args[0][0]
    assert "abgelaufen" in said


def test_a_409_on_a_finished_job_reports_already_that_status():
    """The acceptance point: information, not an error to swallow."""
    _pending()
    interaction = _interaction()

    def _cancel(job_id):
        raise TrainingClientError(
            "Trainer gateway 409 at http://x/train/jobs/j/cancel: "
            '{"detail":"job j is already completed"}', status_code=409)

    _click(interaction, _cancel,
           job_payload=_record(status="completed", stage=None,
                               finished_at="2026-10-12T15:31:00Z"))

    [said] = [c.args[0] for c in interaction.followup.send.call_args_list]
    assert "schon `completed`" in said


def test_a_409_on_a_job_being_started_says_to_ask_again():
    """The route's other 409. Collapsing the two would tell somebody a run that
    is starting had already finished."""
    _pending()
    interaction = _interaction()

    def _cancel(job_id):
        raise TrainingClientError(
            "Trainer gateway 409 ...: being started right now", status_code=409)

    _click(interaction, _cancel,
           job_payload=_record(status="queued", stage=None, stages=[]))

    [said] = [c.args[0] for c in interaction.followup.send.call_args_list]
    assert "in diesem Moment gestartet" in said
    assert "schon" not in said


def test_any_other_refusal_is_shown_with_the_trainers_detail():
    """A 507 names the full filesystem and a 503 the failed claim — those say
    what to do in a way "cancel failed" does not (#361)."""
    _pending()
    interaction = _interaction()

    def _cancel(job_id):
        raise TrainingClientError(
            "Trainer gateway 503 ...: job could not be claimed for the cancel",
            status_code=503)

    _click(interaction, _cancel)

    [said] = [c.args[0] for c in interaction.followup.send.call_args_list]
    assert "could not be claimed" in said


def test_the_confirmation_belongs_to_whoever_asked():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    import bot as bot_module

    other = MagicMock()
    other.user.id = 12345
    other.response.send_message = AsyncMock()

    async def go():
        view = bot_module._TrainCancelView(token="t", requester="9",
                                           job_id=JOB_ID)
        return await view.interaction_check(other)

    assert asyncio.run(go()) is False
    said = other.response.send_message.call_args[0][0]
    assert "Nur wer /train_cancel" in said


# ── /train_log, through the command body ───────────────────────────────────

def _ctx():
    from unittest.mock import AsyncMock, MagicMock

    ctx = MagicMock()
    ctx.defer = AsyncMock()
    ctx.followup.send = AsyncMock()
    ctx.author.id = 9
    return ctx


def _run_log(ctx, *, log=None, log_exc=None, job=None, job_exc=None, **kwargs):
    """Run the undecorated `/train_log` body with the gateway stubbed.

    Through ``__wrapped__``: the role gate is `require_role`'s and is covered by
    its own guard and #105's sweep over every gated command. What is under test
    here is what the command does once it is allowed to run.
    """
    import asyncio

    import atr_status
    import bot as bot_module

    asked = {"log": [], "job": []}

    async def _log(job_id, lines=None, stage=None):
        asked["log"].append((job_id, lines, stage))
        if log_exc is not None:
            raise log_exc
        return log

    async def _job(job_id):
        asked["job"].append(job_id)
        if job_exc is not None:
            raise job_exc
        return job

    original = (atr_status.log, atr_status.job)
    try:
        atr_status.log, atr_status.job = _log, _job
        body = bot_module.train_log_cmd.callback.__wrapped__
        asyncio.run(body(ctx, **kwargs))
    finally:
        atr_status.log, atr_status.job = original
    return asked


def test_an_unknown_stage_is_refused_without_asking_the_gateway():
    """The five are a literal in the trainer's contract, so a wrong name is
    known to be wrong before the request."""
    ctx = _ctx()
    asked = _run_log(ctx, job_id=JOB_ID, stage="quantise")
    assert asked["log"] == [] and asked["job"] == []
    said = ctx.followup.send.call_args[0][0]
    for stage in train_ops.STAGES:
        assert f"`{stage}`" in said


def test_a_log_is_posted_as_a_tail_with_the_job_and_stage_named():
    ctx = _ctx()
    asked = _run_log(ctx, log={"lines": ["epoch 1", "epoch 2"]},
                     job_id=JOB_ID, stage="train")
    assert asked["log"] == [(JOB_ID, train_ops.LOG_LINES_DEFAULT, "train")]
    said = ctx.followup.send.call_args[0][0]
    assert JOB_ID in said and "`train`" in said and "epoch 2" in said


def test_a_404_on_the_log_names_the_stages_the_job_does_have():
    import atr_status

    ctx = _ctx()
    asked = _run_log(ctx, log_exc=atr_status.AtrStatusError("404", status_code=404),
                     job=_record(), job_id=JOB_ID, stage="test")
    assert asked["job"] == [JOB_ID]          # the record decides which 404
    said = ctx.followup.send.call_args[0][0]
    assert "`prepare` (completed)" in said


def test_a_404_for_an_unknown_job_says_the_trainer_does_not_know_it():
    """The same status code, the opposite sentence."""
    import atr_status

    ctx = _ctx()
    _run_log(ctx, log_exc=atr_status.AtrStatusError("404", status_code=404),
             job_exc=atr_status.AtrStatusError("404", status_code=404),
             job_id=JOB_ID, stage="train")
    said = ctx.followup.send.call_args[0][0]
    assert "kennt den Lauf" in said


def test_any_other_gateway_failure_is_passed_on_as_is():
    import atr_status

    ctx = _ctx()
    asked = _run_log(ctx, log_exc=atr_status.AtrStatusError(
        "Das ATR-Gateway ist nicht erreichbar."), job_id=JOB_ID)
    assert asked["job"] == []                # no record to fetch, no guessing
    assert "nicht erreichbar" in ctx.followup.send.call_args[0][0]


def test_an_empty_log_is_not_an_empty_code_block():
    ctx = _ctx()
    _run_log(ctx, log={"lines": []}, job_id=JOB_ID, stage="compile")
    said = ctx.followup.send.call_args[0][0]
    assert "leer" in said
    assert "```" not in said


def test_a_line_count_over_the_ceiling_is_capped():
    ctx = _ctx()
    asked = _run_log(ctx, log={"lines": ["x"]}, job_id=JOB_ID, stage="train",
                     lines=9999)
    assert asked["log"][0][1] == train_ops.LOG_LINES_MAX


def test_a_line_count_of_zero_does_not_ask_for_zero_lines():
    """The trainer's own floor is 1; a 0 would be a 422 from the gateway."""
    ctx = _ctx()
    asked = _run_log(ctx, log={"lines": ["x"]}, job_id=JOB_ID, stage="train",
                     lines=0)
    assert asked["log"][0][1] >= 1
