"""
test_ah_363_train_watch.py — the training watcher, offline (#363).

The acceptance list of the issue, as tests:

  1. The registry survives a restart, and the resumed watcher edits the SAME
     message — that is the whole reason it is on disk.
  2. A stage is announced exactly once each, across a restart included.
  3. A run that reached a terminal state while the bot was DOWN is reported on
     the next poll, not silently dropped. (The opposite of `atr_watch`, which
     deliberately says nothing on its first sight — and the reason this module
     has no `seeded` flag.)
  4. `queued_reason` appears verbatim.
  5. A terminal run reports CER/WER, model and wall time; a failure reports the
     error and the log tail.
  6. A failed poll keeps the run: unreachable is announced once after a grace,
     and a 404 is a different fact from silence.
  7. `format_list` is newest first, with CER where there is one.

And the contract fixes the watcher cannot work without: the trainer says
"completed", not "done"; its `stages` is a list; its `log_tail` is a list; its
`model_id` and `cer` are nested.

Run: pytest agentic_historian/tests/test_ah_363_train_watch.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                      # noqa: E402
import train_watch as tw                           # noqa: E402
from agent_a.training_client import (              # noqa: E402
    JobStatus,
    Stage,
    TrainJob,
    TrainingClientError,
    TrainingJobNotFound,
)


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


NOW = 1_760_000_000.0


def _record(**over) -> dict:
    """A job record in the trainer's own shape (contracts.py TrainJob)."""
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
        "progress": {"epoch": 3, "epochs": 10, "total_steps": 4000},
    }
    rec.update(over)
    return rec


def _run(**over) -> tw.WatchedRun:
    kw = dict(job_id="20261012T090000Z-medieval-smoke", channel_id="555",
              message_id="777", started_by="42", preset="medieval-smoke",
              model_id="dh-unibe/medieval-v3", registered_at=NOW - 3600)
    kw.update(over)
    return tw.WatchedRun(**kw)


# ── 1. the registry outlives the process ────────────────────────────────────

def test_a_watched_run_is_read_back_after_a_restart():
    """`/update` restarts this bot by design; a run outlives that by hours."""
    tw.register(_run())
    again = tw.load()
    assert [r.job_id for r in again] == ["20261012T090000Z-medieval-smoke"]
    assert again[0].message_id == "777"
    assert again[0].started_by == "42"
    assert again[0].preset == "medieval-smoke"


def test_the_resumed_watcher_edits_the_same_message():
    """The acceptance criterion: resume, do not re-post."""
    tw.register(_run(message_id="777"))
    resumed = tw.find("20261012T090000Z-medieval-smoke")
    update, fresh = tw.decide(resumed, TrainJob.from_dict(_record()), now=NOW)
    # The decision carries a board; which message it goes on is the registry's,
    # and it is the one the run was started with.
    assert fresh.message_id == "777"
    assert update.board


def test_an_unreadable_registry_is_not_an_outage(tmp_path):
    """Read in on_ready: a corrupt file may not stop the bot from starting."""
    tw.path().write_text("{not json", encoding="utf-8")
    assert tw.load() == []


def test_a_field_this_build_does_not_know_skips_only_that_entry(tmp_path):
    import json
    tw.path().write_text(json.dumps([
        {"job_id": "a", "channel_id": "1", "message_id": "2", "started_by": "3",
         "from_a_newer_build": True},
        {"job_id": "b", "channel_id": "1", "message_id": "2", "started_by": "3"},
    ]), encoding="utf-8")
    assert [r.job_id for r in tw.load()] == ["b"]


def test_register_replaces_by_job_id_and_drop_removes_one():
    tw.register(_run(job_id="a"))
    tw.register(_run(job_id="b"))
    tw.register(_run(job_id="a", message_id="999"))
    assert len(tw.load()) == 2
    assert tw.find("a").message_id == "999"
    tw.drop("a")
    assert [r.job_id for r in tw.load()] == ["b"]


def test_rebind_keeps_the_run_and_moves_the_board():
    """A deleted message may not end a watch — the run is still training."""
    moved = tw.rebind(_run(), "888")
    assert moved.message_id == "888"
    assert moved.job_id == _run().job_id and moved.preset == "medieval-smoke"


# ── 2. a stage is announced exactly once ────────────────────────────────────

def test_stages_already_run_are_announced_on_the_first_poll():
    update, fresh = tw.decide(_run(), TrainJob.from_dict(_record()), now=NOW)
    assert update.stages_new == ("prepare", "compile", "train")
    assert fresh.stages_told == ["prepare", "compile", "train"]


def test_the_same_poll_twice_announces_no_stage_again():
    _, fresh = tw.decide(_run(), TrainJob.from_dict(_record()), now=NOW)
    update, _ = tw.decide(fresh, TrainJob.from_dict(_record()), now=NOW + 60)
    assert update.stages_new == ()


def test_a_stage_told_before_a_restart_is_not_told_again():
    """The told-list is persisted, so the board does not grow a second trail."""
    tw.register(_run(stages_told=["prepare", "compile", "train"]))
    reloaded = tw.find("20261012T090000Z-medieval-smoke")
    update, _ = tw.decide(reloaded, TrainJob.from_dict(_record()), now=NOW)
    assert update.stages_new == ()


def test_the_told_list_grows_rather_than_being_replaced():
    """Three polls: a stage told on the first may not come back on the third.

    With the list replaced by the latest poll's new stages, a poll that
    announces nothing empties it and the next one announces the whole trail
    again.
    """
    _, fresh = tw.decide(_run(), TrainJob.from_dict(_record()), now=NOW)
    _, fresh = tw.decide(fresh, TrainJob.from_dict(_record()), now=NOW + 60)
    assert fresh.stages_told == ["prepare", "compile", "train"]
    update, _ = tw.decide(fresh, TrainJob.from_dict(_record()), now=NOW + 120)
    assert update.stages_new == ()


def test_a_new_stage_is_announced_when_it_starts():
    _, fresh = tw.decide(_run(), TrainJob.from_dict(_record()), now=NOW)
    later = _record(status="testing", stage="test", stages=[
        {"name": "prepare", "status": "completed"},
        {"name": "compile", "status": "completed"},
        {"name": "train", "status": "completed"},
        {"name": "test", "status": "running"},
    ])
    update, newer = tw.decide(fresh, TrainJob.from_dict(later), now=NOW + 60)
    assert update.stages_new == ("test",)
    # And the earlier three are still remembered, so they are not told again.
    assert newer.stages_told == ["prepare", "compile", "train", "test"]


def test_a_skipped_stage_is_never_claimed():
    """A run that adopts a cached artefact never prepares.

    Announcing it anyway would be a board saying more than was measured, in the
    one place a reader goes to find out what happened.
    """
    cached = _record(stages=[
        {"name": "prepare", "status": "pending"},
        {"name": "compile", "status": "completed"},
        {"name": "train", "status": "running"},
    ])
    update, _ = tw.decide(_run(), TrainJob.from_dict(cached), now=NOW)
    assert "prepare" not in update.stages_new
    assert update.stages_new == ("compile", "train")
    assert "⬚ prepare" in update.board


def test_a_queued_run_has_reached_no_stage():
    queued = _record(status="queued", stage=None, stages=[], started_at=None,
                     queued_reason="another job is running")
    update, _ = tw.decide(_run(), TrainJob.from_dict(queued), now=NOW)
    assert update.stages_new == ()


# ── 3. a run that finished while the bot was down ───────────────────────────

def test_a_terminal_state_reached_while_the_bot_was_down_is_reported():
    """No silence on first sight — the acceptance point a `seeded` flag breaks.

    The registry entry says only "training"; the first poll after the restart
    sees "completed" and must say so.
    """
    stored = _run(last_status="training", stages_told=["prepare", "compile", "train"])
    done = _record(status="completed", stage=None,
                   finished_at="2026-10-12T15:31:00Z",
                   metrics={"cer": 0.0712, "wer": 0.1903},
                   model_path="/srv/models/medieval-v3",
                   stages=[{"name": n, "status": "completed"} for n in tw.STAGES])
    update, fresh = tw.decide(stored, TrainJob.from_dict(done), now=NOW)
    assert update.terminal is True
    assert "7.12 %" in update.notice and "19.03 %" in update.notice
    assert "medieval-v3" in update.notice
    assert "6.5 h" in update.notice           # 09:01 → 15:31
    assert fresh.last_status == "completed"


def test_a_run_that_was_never_polled_has_no_measured_status():
    """`None` is not `queued`: nothing has been measured yet."""
    assert _run().last_status is None


# ── 4. queued_reason, verbatim ──────────────────────────────────────────────

@pytest.mark.parametrize("reason", [
    "waiting for job 20261011T2201Z-trocr",
    "GPU 1 has 2000 MB free",
])
def test_the_queued_reason_is_shown_word_for_word(reason):
    """Two reasons, one status, opposite responses from whoever reads it."""
    queued = _record(status="queued", stage=None, stages=[], started_at=None,
                     queued_reason=reason)
    update, _ = tw.decide(_run(), TrainJob.from_dict(queued), now=NOW)
    assert reason in update.board


# ── 5. what a terminal run reports ──────────────────────────────────────────

def test_a_failure_carries_the_error_and_the_log_tail():
    failed = _record(status="failed", stage="train",
                     finished_at="2026-10-12T11:01:00Z",
                     error="CUDA out of memory (tried to allocate 2.00 GiB)",
                     log_tail=["Epoch 2/10", "  step 410/4000",
                               "RuntimeError: CUDA out of memory"],
                     stages=[{"name": "prepare", "status": "completed"},
                             {"name": "compile", "status": "completed"},
                             {"name": "train", "status": "failed"}])
    update, _ = tw.decide(_run(), TrainJob.from_dict(failed), now=NOW)
    assert update.terminal is True and update.urgent is True
    assert "CUDA out of memory" in update.notice
    assert "RuntimeError: CUDA out of memory" in update.board
    assert "❌ train" in update.board


def test_a_cancelled_run_is_news_but_not_an_alarm():
    cancelled = _record(status="cancelled", stage=None,
                        finished_at="2026-10-12T09:31:00Z")
    update, _ = tw.decide(_run(), TrainJob.from_dict(cancelled), now=NOW)
    assert update.terminal is True
    assert update.urgent is False
    assert "abgebrochen" in update.notice


def test_a_cer_of_zero_is_a_measurement_and_not_unknown():
    """The project's recurring mistake, in the field where 0 is the best result."""
    done = _record(status="completed", finished_at="2026-10-12T10:01:00Z",
                   metrics={"cer": 0.0, "wer": 0.0})
    update, _ = tw.decide(_run(), TrainJob.from_dict(done), now=NOW)
    assert "0.00 %" in update.notice
    assert "unbekannt" not in update.notice


def test_a_missing_cer_says_unknown_rather_than_zero():
    done = _record(status="completed", finished_at="2026-10-12T10:01:00Z",
                   metrics={})
    update, _ = tw.decide(_run(), TrainJob.from_dict(done), now=NOW)
    assert "unbekannt" in update.notice
    assert "0.00 %" not in update.notice


def test_wall_time_is_unknown_rather_than_counted_from_the_registry():
    """A queued run has not been running; the bot's own clock is not its start.

    For an adopted run `registered_at` can be hours after the real start, so
    measuring from it would print a runtime nobody measured.
    """
    queued = TrainJob.from_dict(_record(status="queued", started_at=None,
                                       stage=None, stages=[]))
    assert tw.wall_s(queued, now=NOW) is None
    update, _ = tw.decide(_run(registered_at=NOW - 7200), queued, now=NOW)
    assert "Laufzeit: unbekannt" in update.board


def test_wall_time_of_a_finished_run_comes_from_its_own_timestamps():
    done = TrainJob.from_dict(_record(status="completed",
                                      finished_at="2026-10-12T12:01:00Z"))
    assert tw.wall_s(done, now=NOW) == pytest.approx(3 * 3600)


# ── the board itself ────────────────────────────────────────────────────────

def test_the_board_names_the_run_the_preset_and_the_target():
    update, _ = tw.decide(_run(), TrainJob.from_dict(_record()), now=NOW)
    assert "20261012T090000Z-medieval-smoke" in update.board
    assert "medieval-smoke" in update.board          # the preset, from the registry
    assert "dh-unibe/medieval-v3" in update.board    # from request.model_id
    assert "Epoche 3/10" in update.board


def test_the_board_is_short_enough_for_discord():
    failed = _record(status="failed", error="x" * 2000,
                     log_tail=["y" * 500] * 20,
                     finished_at="2026-10-12T11:01:00Z")
    update, _ = tw.decide(_run(), TrainJob.from_dict(failed), now=NOW)
    assert len(update.board) <= 1900


def test_a_very_long_queued_reason_cannot_break_the_message():
    """``queued_reason`` is copied word for word, so it is the one field on the
    board with no length of its own — and Discord refuses at 2000 characters."""
    queued = _record(status="queued", stage=None, stages=[], started_at=None,
                     queued_reason="warten auf " + "x" * 3000)
    update, _ = tw.decide(_run(), TrainJob.from_dict(queued), now=NOW)
    assert len(update.board) <= 1900


def test_an_unchanged_board_is_not_re_sent():
    """60 edits an hour with identical content is the report nobody reads."""
    _, fresh = tw.decide(_run(), TrainJob.from_dict(_record()), now=NOW)
    update, _ = tw.decide(fresh, TrainJob.from_dict(_record()), now=NOW)
    assert update.changed is False


def test_a_changed_board_is_sent():
    _, fresh = tw.decide(_run(), TrainJob.from_dict(_record()), now=NOW)
    moved = _record(progress={"epoch": 4, "epochs": 10, "total_steps": 4000})
    update, _ = tw.decide(fresh, TrainJob.from_dict(moved), now=NOW)
    assert update.changed is True
    assert "Epoche 4/10" in update.board


# ── 6. a failed poll never loses a run ──────────────────────────────────────

def test_a_single_silent_poll_says_nothing_and_keeps_the_run():
    """The gateway is restarted for every deploy of the serving stack."""
    notice, run = tw.note_unreachable(_run(), NOW)
    assert notice == ""
    assert run.unreachable_since == NOW
    assert run.unreachable_told is False


def test_a_held_silence_is_announced_once():
    _, run = tw.note_unreachable(_run(last_status="training"), NOW)
    notice, run = tw.note_unreachable(run, NOW + tw.UNREACHABLE_GRACE_S + 1)
    assert "antwortet seit" in notice and "training" in notice
    again, run = tw.note_unreachable(run, NOW + 2 * tw.UNREACHABLE_GRACE_S)
    assert again == ""


def test_an_answer_closes_an_announced_silence_only():
    _, run = tw.note_unreachable(_run(), NOW)
    back, clean = tw.note_reachable(run, NOW + 60)
    assert back == ""                       # nobody was told about it
    assert clean.unreachable_since is None

    _, run = tw.note_unreachable(_run(), NOW)
    _, run = tw.note_unreachable(run, NOW + tw.UNREACHABLE_GRACE_S + 1)
    back, clean = tw.note_reachable(run, NOW + tw.UNREACHABLE_GRACE_S + 60)
    assert "antwortet wieder" in back
    assert clean.unreachable_since is None and clean.unreachable_told is False


def test_a_poll_that_answers_clears_a_silence_streak():
    _, run = tw.note_unreachable(_run(), NOW)
    _, fresh = tw.decide(run, TrainJob.from_dict(_record()), now=NOW + 60)
    assert fresh.unreachable_since is None and fresh.unreachable_told is False


def test_a_404_is_a_different_fact_from_silence():
    """The gateway answered: the trainer has no such job. Graced, then given up.

    Not instantly, because a trainer restarted mid-request answers 404 for a
    moment and dropping a live run over that is the loss this registry prevents.
    """
    notice, run, give_up = tw.note_missing(_run(), NOW)
    assert notice == "" and give_up is False
    assert run.missing_since == NOW

    notice, run, give_up = tw.note_missing(run, NOW + tw.FORGOTTEN_GRACE_S + 1)
    assert give_up is True
    assert "kennt den Lauf" in notice


def test_silence_and_a_404_do_not_share_a_grace_period():
    """Half an hour for an outage, a day for a forgotten job — not one number."""
    assert tw.UNREACHABLE_GRACE_S != tw.FORGOTTEN_GRACE_S
    assert tw.FORGOTTEN_GRACE_S > tw.UNREACHABLE_GRACE_S


# ── 7. /train_list ──────────────────────────────────────────────────────────

def test_the_list_is_newest_first_with_cer_where_there_is_one():
    jobs = [
        TrainJob.from_dict(_record(id="old", status="completed",
                                   created_at="2026-10-01T09:00:00Z",
                                   metrics={"cer": 0.0931})),
        TrainJob.from_dict(_record(id="new", status="training",
                                   created_at="2026-10-12T09:00:00Z")),
        TrainJob.from_dict(_record(id="mid", status="failed",
                                   created_at="2026-10-05T09:00:00Z")),
    ]
    out = tw.format_list(jobs)
    assert out.index("new") < out.index("mid") < out.index("old")
    assert "9.31%" in out


def test_the_list_distinguishes_no_cer_from_a_cer_of_zero():
    perfect = TrainJob.from_dict(_record(id="p", status="completed",
                                         metrics={"cer": 0.0}))
    none = TrainJob.from_dict(_record(id="n", status="training"))
    assert "0.00%" in tw.format_list([perfect])
    assert "—" in tw.format_list([none])


def test_an_empty_list_says_so():
    assert "Keine" in tw.format_list([])


def test_a_job_without_a_date_sorts_last_rather_than_first():
    dated = TrainJob.from_dict(_record(id="dated",
                                       created_at="2026-10-01T09:00:00Z"))
    undated = TrainJob.from_dict(_record(id="undated", created_at=None))
    out = tw.format_list([undated, dated])
    assert out.index("dated") < out.index("undated")


def test_the_list_is_capped_and_says_how_many_there_were():
    jobs = [TrainJob.from_dict(_record(id=f"j{i}")) for i in range(14)]
    out = tw.format_list(jobs, limit=10)
    assert "(14 insgesamt)" in out


# ── the contract the watcher reads ──────────────────────────────────────────

def test_the_trainer_says_completed_and_that_is_terminal():
    """It said `done`, which the trainer never sends — so a finished run parsed
    as QUEUED and a watcher would have polled it for ever."""
    job = TrainJob.from_dict(_record(status="completed"))
    assert job.status is JobStatus.COMPLETED
    assert job.is_terminal() is True
    assert job.is_active() is False


def test_a_status_this_build_does_not_know_is_unknown_and_not_queued():
    job = TrainJob.from_dict(_record(status="rematerialising"))
    assert job.status is JobStatus.UNKNOWN
    assert job.status_raw == "rematerialising"
    assert job.is_terminal() is False       # unknown is not "over"
    assert job.is_active() is True          # keep watching it
    assert job.stage_description() == "rematerialising"


def test_the_stages_list_of_the_real_record_parses():
    """`stages` is a list of stage records; `.items()` on it raised."""
    job = TrainJob.from_dict(_record())
    assert job.stages[Stage.PREPARE] == "completed"
    assert job.stages[Stage.TRAIN] == "running"
    assert job.stages[Stage.TEST] == "pending"


def test_an_unknown_stage_name_is_skipped_not_guessed():
    job = TrainJob.from_dict(_record(stages=[{"name": "quantise",
                                              "status": "running"}]))
    assert job.stages == {}


def test_the_log_tail_list_becomes_text():
    job = TrainJob.from_dict(_record(log_tail=["a", "b"]))
    assert job.log_tail == "a\nb"


def test_model_id_and_engine_come_out_of_the_request():
    job = TrainJob.from_dict(_record())
    assert job.model_id == "dh-unibe/medieval-v3"
    assert job.engine == "kraken"


def test_cer_comes_out_of_metrics():
    job = TrainJob.from_dict(_record(metrics={"cer": 0.05, "wer": 0.1}))
    assert job.cer == pytest.approx(0.05)
    assert job.wer == pytest.approx(0.1)


def test_a_zero_cer_in_metrics_is_not_overridden_by_a_flat_key():
    """`or` would have reached past a measured 0.0 to the fallback."""
    job = TrainJob.from_dict(_record(metrics={"cer": 0.0}, cer=0.42))
    assert job.cer == 0.0


def test_the_summary_shape_of_the_job_list_still_parses():
    """serving#107's six keys: id, status, stage, created_at, queued_reason, error."""
    job = TrainJob.from_dict({"id": "j", "status": "queued", "stage": None,
                              "created_at": "2026-10-12T09:00:00Z",
                              "queued_reason": "another job is running",
                              "error": None})
    assert job.status is JobStatus.QUEUED
    assert job.queued_reason == "another job is running"
    assert job.stages == {} and job.cer is None


def test_a_404_has_its_own_exception_class():
    """The watcher has to tell "no such job" from "no answer"."""
    assert issubclass(TrainingJobNotFound, TrainingClientError)


def test_the_client_raises_the_404_class_for_an_unknown_job():
    from unittest.mock import MagicMock

    from agent_a.training_client import TrainingClient

    response = MagicMock()
    response.status_code = 404
    response.text = '{"detail":"no such job"}'
    client = TrainingClient(base_url="http://localhost")
    client._client = MagicMock()
    client._client.get.return_value = response

    with pytest.raises(TrainingJobNotFound):
        client.get_job("nope")


def test_the_client_raises_the_plain_class_for_every_other_status():
    """A 503 is the gateway failing, not the trainer denying the job."""
    from unittest.mock import MagicMock

    from agent_a.training_client import TrainingClient

    response = MagicMock()
    response.status_code = 503
    response.text = "upstream gone"
    client = TrainingClient(base_url="http://localhost")
    client._client = MagicMock()
    client._client.get.return_value = response

    with pytest.raises(TrainingClientError) as caught:
        client.get_job("j1")
    assert not isinstance(caught.value, TrainingJobNotFound)


# ── the bot side: wiring that cannot be unit-tested, asserted at the source ──

def _source() -> str:
    return (PKG / "bot.py").read_text(encoding="utf-8")


def _tasks_started_in_on_ready() -> set[str]:
    """Every ``asyncio.create_task(f())`` in ``on_ready``, by function name.

    Through the syntax tree rather than by grepping the text: the first version
    of this guard searched the source for the call and was satisfied by a
    commented-out one — which a mutation of exactly this line proved, and which
    is how a watcher could be switched off with the test still green.
    """
    import ast

    tree = ast.parse((PKG / "bot.py").read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != "on_ready":
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "create_task"
                    and inner.args
                    and isinstance(inner.args[0], ast.Call)
                    and isinstance(inner.args[0].func, ast.Name)):
                out.add(inner.args[0].func.id)
    return out


def test_the_watcher_is_started_in_on_ready():
    """The reattach IS this line: the registry is on disk, so starting the loop
    is all a restarted bot has to do to pick a run back up."""
    assert "_train_watch_loop" in _tasks_started_in_on_ready()


def test_the_watcher_needs_no_channel_and_no_flag():
    """Unlike the other two watchers: a registered run carries its own channel,
    so there is nothing to configure and nothing to get wrong."""
    source = _source()
    block = source[source.index("async def on_ready("):]
    block = block[:block.index("\n# ──")]
    start = block[block.index("_train_watch_loop()") - 400:
                  block.index("_train_watch_loop()")]
    assert "ENABLE_TRAIN_WATCH" not in start
    assert "TRAIN_WATCH_CHANNEL_ID" not in source
    assert not hasattr(config, "ENABLE_TRAIN_WATCH")


def test_the_loop_polls_at_the_configured_interval():
    source = _source()
    block = source[source.index("async def _train_watch_loop("):]
    block = block[:block.index("\nasync def _train_watch_one(")]
    assert "config.TRAIN_WATCH_INTERVAL_S" in block
    assert config.TRAIN_WATCH_INTERVAL_S > 0


def test_one_runs_failure_does_not_end_the_loop():
    """Gateway down must log and retry, not kill the task (the acceptance point).

    Two nets: one around the per-run poll, one around the registry read.
    """
    source = _source()
    block = source[source.index("async def _train_watch_loop("):]
    block = block[:block.index("\nasync def _train_watch_one(")]
    assert block.count("except Exception") >= 2
    assert "continue" not in block or "await asyncio.sleep" in block


def test_the_run_is_dropped_only_after_its_report_is_away():
    """The atr_watch trade: a repeated edit is noise, a lost result is the
    eleven hours this exists to prevent."""
    source = _source()
    block = source[source.index("async def _train_watch_one("):]
    block = block[:block.index("\nasync def _train_say(")]
    # The terminal branch, not the 404 one, which drops for its own reason.
    after_poll = block[block.index("train_watch.decide("):]
    assert after_poll.index("update.notice") < after_poll.index("train_watch.drop")


def test_a_404_and_a_silent_gateway_take_different_branches():
    source = _source()
    block = source[source.index("async def _train_watch_one("):]
    block = block[:block.index("\nasync def _train_say(")]
    assert "status_code" in block
    assert "note_missing" in block and "note_unreachable" in block


def test_train_status_asks_the_gateway_before_it_adopts():
    """A typo'd id may not land in the registry and be polled for ever."""
    source = _source()
    block = source[source.index("async def train_status_cmd("):]
    block = block[:block.index('@bot.slash_command(name="train_list"')]
    assert block.index("atr_status.job(job_id)") < block.index("train_watch.register")


def test_train_status_and_train_list_are_role_gated():
    source = _source()
    for name in ("train_status", "train_list"):
        block = source[source.index(f'name="{name}"'):]
        decorators = block[:block.index("async def")]
        assert "@require_role" in decorators, name


def test_train_list_asks_for_the_full_records_but_only_ten():
    """The summary shape has no ``metrics``, so a CER column needs the whole
    record — and the server-side limit is what keeps that cheap."""
    source = _source()
    block = source[source.index("async def train_list_cmd("):]
    block = block[:block.index("\ndef main")]
    assert "atr_status.jobs(limit=10)" in block
    assert "summary=True" not in block


def test_the_board_is_edited_in_place_rather_than_posted_per_tick():
    source = _source()
    block = source[source.index("async def _train_board("):]
    block = block[:block.index("\nasync def _train_watch_loop(")]
    assert "message.edit" in block
    # A new message only when the old one is gone, and the run is rebound to it.
    assert "rebind" in block


# ── one poll, end to end, with the channel faked ────────────────────────────

class _FakeMessage:
    def __init__(self, mid="777"):
        self.id = mid
        self.edits: list[str] = []

    async def edit(self, content=None, **_):
        self.edits.append(content)


class _FakeChannel:
    def __init__(self, message=None):
        self.message = message
        self.sent: list[str] = []
        self.fetch_fails = message is None

    async def fetch_message(self, mid):
        if self.fetch_fails:
            raise RuntimeError("Unknown Message")
        return self.message

    async def send(self, content=None, **_):
        self.sent.append(content)
        return _FakeMessage("999")


class _FakeStatus:
    """Stands in for ``atr_status``: one answer, or one failure."""

    AtrStatusError = None       # set below to the real class

    def __init__(self, payload=None, exc=None):
        self.payload, self.exc = payload, exc
        self.asked: list[str] = []

    async def job(self, job_id):
        self.asked.append(job_id)
        if self.exc is not None:
            raise self.exc
        return self.payload


def _poll(run, status, channel):
    """Run one `_train_watch_one` against a faked channel."""
    import asyncio
    from unittest.mock import patch

    import atr_status as real_status
    import bot

    _FakeStatus.AtrStatusError = real_status.AtrStatusError
    with patch.object(bot.bot, "get_channel", return_value=channel):
        asyncio.run(bot._train_watch_one(run, status, tw, TrainJob))


def test_one_poll_edits_the_run_s_own_message():
    """The acceptance criterion: resume, and edit the SAME message."""
    tw.register(_run())
    message = _FakeMessage("777")
    channel = _FakeChannel(message)
    _poll(tw.find(_run().job_id), _FakeStatus(_record()), channel)

    assert len(message.edits) == 1
    assert "medieval-smoke" in message.edits[0]
    assert channel.sent == []                   # nothing posted, only edited
    assert tw.find(_run().job_id).stages_told == ["prepare", "compile", "train"]


def test_a_second_identical_poll_edits_nothing():
    tw.register(_run())
    message = _FakeMessage("777")
    channel = _FakeChannel(message)
    _poll(tw.find(_run().job_id), _FakeStatus(_record()), channel)
    _poll(tw.find(_run().job_id), _FakeStatus(_record()), channel)
    assert len(message.edits) == 1


def test_a_deleted_board_is_re_posted_and_the_run_rebound():
    tw.register(_run())
    channel = _FakeChannel(None)                # fetch_message raises
    _poll(tw.find(_run().job_id), _FakeStatus(_record()), channel)
    assert len(channel.sent) == 1
    assert tw.find(_run().job_id).message_id == "999"


def test_a_run_that_finished_while_the_bot_was_down_is_reported_and_dropped():
    """The acceptance point a `seeded` flag would have broken."""
    tw.register(_run(last_status="training",
                     stages_told=["prepare", "compile", "train"]))
    message = _FakeMessage("777")
    channel = _FakeChannel(message)
    done = _record(status="completed", stage=None,
                   finished_at="2026-10-12T15:31:00Z",
                   metrics={"cer": 0.0712, "wer": 0.19},
                   model_path="/srv/models/medieval-v3",
                   stages=[{"name": n, "status": "completed"} for n in tw.STAGES])
    _poll(tw.find(_run().job_id), _FakeStatus(done), channel)

    assert message.edits, "the board is brought up to date first"
    assert any("7.12 %" in m for m in channel.sent), channel.sent
    assert tw.find(_run().job_id) is None       # dropped once reported


def test_a_silent_gateway_keeps_the_run_and_says_nothing_yet():
    import atr_status as real_status

    tw.register(_run())
    channel = _FakeChannel(_FakeMessage("777"))
    _poll(tw.find(_run().job_id),
          _FakeStatus(exc=real_status.AtrStatusError("nicht erreichbar")),
          channel)
    kept = tw.find(_run().job_id)
    assert kept is not None                     # never lose a training run
    assert kept.unreachable_since is not None
    assert channel.sent == []                   # a deploy is not an incident


def test_a_404_keeps_the_run_inside_the_grace_period():
    import atr_status as real_status

    tw.register(_run())
    channel = _FakeChannel(_FakeMessage("777"))
    _poll(tw.find(_run().job_id),
          _FakeStatus(exc=real_status.AtrStatusError("404", status_code=404)),
          channel)
    kept = tw.find(_run().job_id)
    assert kept is not None
    assert kept.missing_since is not None
    assert channel.sent == []


def test_a_404_that_has_held_for_a_day_gives_up_with_a_word():
    import atr_status as real_status

    import time
    # Against the real clock, because the loop reads it.
    tw.register(_run(missing_since=time.time() - tw.FORGOTTEN_GRACE_S - 10,
                     last_status="training"))
    channel = _FakeChannel(_FakeMessage("777"))
    _poll(tw.find(_run().job_id),
          _FakeStatus(exc=real_status.AtrStatusError("404", status_code=404)),
          channel)
    assert tw.find(_run().job_id) is None
    assert any("kennt den Lauf" in m for m in channel.sent), channel.sent
