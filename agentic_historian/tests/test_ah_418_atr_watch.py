"""#418: announcing what happened on the training server, rather than being asked.

Offline. What is tested is the decision — when to speak and when to stay quiet —
because that is the whole feature: a watcher that announces too much is muted,
and one that announces too little is the SSH session it was meant to replace.
"""

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_watch  # noqa: E402
from atr_watch import WatchState, decide  # noqa: E402


def _jobs(*items):
    return {"jobs": list(items)}


def _job(job_id, status, **kw):
    return {"id": job_id, "status": status, **kw}


def _gpu(*procs, index=1):
    return {"cards": [{"index": index, "processes": list(procs)}]}


def _proc(pid, used_mib=15638, age_s=7200.0, **kw):
    base = {"pid": pid, "used_mib": used_mib, "age_s": age_s, "registered": False,
            "own_service": False, "orphaned": False, "service": None,
            "user": "tobias", "command": "ketos --device cuda:0 train"}
    base.update(kw)
    return base


SEEDED = WatchState(seeded=True)


# ── the rule everything else depends on ─────────────────────────────────────

def test_a_cold_start_records_the_world_and_says_nothing():
    """Otherwise every deploy pastes the whole job history into the channel,
    and a channel that does that once is muted for ever."""
    out, state = decide(WatchState(),
                        _jobs(_job("j1", "failed"), _job("j2", "completed")),
                        _gpu(_proc(999)))
    assert out == []
    assert state.seeded is True
    assert state.jobs == {"j1": "failed", "j2": "completed"}
    assert state.flagged == {"999": 15638}


def test_after_seeding_a_new_failure_is_announced():
    state = WatchState(jobs={"j1": "training"}, seeded=True)
    out, _ = decide(state, _jobs(_job("j1", "failed")), _gpu())
    assert [a.key for a in out] == ["j1"]
    assert "failed" in out[0].text


def test_the_same_failure_is_announced_once():
    _, state = decide(WatchState(jobs={"j1": "training"}, seeded=True),
                      _jobs(_job("j1", "failed")), _gpu())
    out, _ = decide(state, _jobs(_job("j1", "failed")), _gpu())
    assert out == []


def test_a_job_that_is_still_running_is_not_an_event():
    out, _ = decide(SEEDED, _jobs(_job("j1", "training")), _gpu())
    assert out == []


# The shape every failed job on the box actually has: our own wrapper first,
# then the captured tail, ending in tqdm redraws and a bare ANSI cursor move.
REAL_ERROR = (
    "StageFailed in train: train failed: python exited 1. "
    "Last lines of logs/train.log:\n"
    "    outputs = func(self, *args, **kwargs)\n"
    "              ^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
    "  File \".../modeling_qwen3_vl.py\", line 1366, in forward\n"
    "    loss = self.loss_function(logits=logits, labels=labels)\n"
    "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 8.16 GiB. "
    "GPU 0 has a total capacity of 44.45 GiB of which 7.78 GiB is free.\n"
    " 33%|\u2588\u2588\u2588\u258e      | 785/2355 [11:24:48<22:49:37, 52.34s/it]\n"
    "\n"
    "                                                  [A"
)


def test_a_failure_names_its_cause_not_our_wrapper():
    """Measured against the 32 failed jobs on the box, neither end of the string
    works: the first line is our own wrapper, identical for every cause, and the
    last is a tqdm redraw or a bare `[A`."""
    out, _ = decide(WatchState(jobs={"j1": "training"}, seeded=True),
                    _jobs(_job("j1", "failed", error=REAL_ERROR)), _gpu())
    text = out[0].text
    assert "torch.OutOfMemoryError" in text and "8.16 GiB" in text
    assert "python exited 1" not in text      # the wrapper
    assert "[A" not in text and "52.34s/it" not in text   # the noise
    assert "line 1366" not in text            # the traceback
    assert len(text) < 400


def test_a_cancellation_has_no_traceback_and_still_reads():
    out, _ = decide(WatchState(jobs={"j1": "training"}, seeded=True),
                    _jobs(_job("j1", "failed", error="cancelled on request")), _gpu())
    assert "cancelled on request" in out[0].text


def test_a_dataloader_kill_is_reported_as_itself():
    error = ("StageFailed in train: train failed: ketos exited 1. Last lines:\n"
             "RuntimeError: DataLoader worker (pid 2722373) is killed by "
             "signal: Terminated.")
    out, _ = decide(WatchState(jobs={"j1": "training"}, seeded=True),
                    _jobs(_job("j1", "failed", error=error)), _gpu())
    assert "DataLoader worker" in out[0].text


def test_a_completed_job_carries_its_cer():
    out, _ = decide(WatchState(jobs={"j1": "testing"}, seeded=True),
                    _jobs(_job("j1", "completed", metrics={"cer": 0.2054})), _gpu())
    assert "0.2054" in out[0].text


# ── memory nothing accounts for ─────────────────────────────────────────────

def test_an_unmanaged_process_past_the_grace_period_is_announced():
    out, _ = decide(SEEDED, _jobs(), _gpu(_proc(2771780, age_s=7200.0)))
    assert [a.key for a in out] == ["2771780"]
    assert "ohne Dienst" in out[0].text
    assert "ketos" in out[0].text        # what turned "what is that pid?" into an answer


def test_a_process_inside_the_grace_period_is_not_announced_yet():
    # A sweep started by hand is legitimate work and needs no message the second
    # it starts.
    out, state = decide(SEEDED, _jobs(), _gpu(_proc(2771780, age_s=600.0)))
    assert out == []
    assert state.flagged == {}


def test_the_neighbours_service_is_never_announced():
    """It has held 10 GB for months. It is a capacity fact, not an incident."""
    neighbour = _proc(2351630, used_mib=2610, age_s=2365849.0,
                      service="gunicorn.service", user="change")
    out, state = decide(SEEDED, _jobs(), _gpu(neighbour))
    assert out == []
    assert state.flagged == {}


def test_an_orphan_with_no_unit_is_announced_even_though_it_has_no_command():
    orphan = _proc(2743851, used_mib=27530, age_s=57600.0, orphaned=True,
                   user=None, command=None)
    out, _ = decide(SEEDED, _jobs(), _gpu(orphan, index=0))
    assert "verwaist" in out[0].text
    assert "27530 MiB auf GPU 0" in out[0].text
    assert "16 h" in out[0].text


def test_our_own_engines_are_never_announced():
    engine = _proc(2757328, own_service=True, service="atr-trocr.service")
    registered = _proc(2786095, registered=True)
    out, state = decide(SEEDED, _jobs(), _gpu(engine, registered))
    assert out == [] and state.flagged == {}


def test_the_same_process_is_announced_once():
    proc = _proc(2771780)
    _, state = decide(SEEDED, _jobs(), _gpu(proc))
    out, _ = decide(state, _jobs(), _gpu(proc))
    assert out == []


def test_a_process_that_went_away_and_came_back_is_announced_again():
    proc = _proc(2771780)
    _, state = decide(SEEDED, _jobs(), _gpu(proc))
    _, state = decide(state, _jobs(), _gpu())          # gone: state clears
    assert state.flagged == {}
    out, _ = decide(state, _jobs(), _gpu(proc))
    assert [a.key for a in out] == ["2771780"]


# ── persistence ─────────────────────────────────────────────────────────────

def test_state_survives_a_round_trip(tmp_path):
    path = tmp_path / "watch.json"
    atr_watch.save_state(path, WatchState(jobs={"j1": "failed"},
                                          flagged={"7": 100}, seeded=True))
    back = atr_watch.load_state(path)
    assert back.jobs == {"j1": "failed"} and back.flagged == {"7": 100}
    assert back.seeded is True


def test_a_missing_state_file_is_unseeded_not_empty(tmp_path):
    # The safe direction: one silent poll, rather than announcing every terminal
    # job in the list at once.
    assert atr_watch.load_state(tmp_path / "nope.json").seeded is False


def test_a_corrupt_state_file_is_unseeded(tmp_path):
    path = tmp_path / "watch.json"
    path.write_text("{ not json", encoding="utf-8")
    assert atr_watch.load_state(path).seeded is False


def test_saving_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "watch.json"
    atr_watch.save_state(path, WatchState(seeded=True))
    assert [p.name for p in tmp_path.iterdir()] == ["watch.json"]
    assert json.loads(path.read_text())["seeded"] is True


# ── the incident this exists for ────────────────────────────────────────────

def test_the_eleven_hour_run_would_have_been_announced():
    state = WatchState(jobs={"20260908T101611Z-qwen3vl-german-pages-v1": "training"},
                       seeded=True)
    out, _ = decide(state, _jobs(_job(
        "20260908T101611Z-qwen3vl-german-pages-v1", "failed", error=REAL_ERROR,
        progress={"epoch": None, "epochs": 1})), _gpu())
    assert len(out) == 1
    assert "8.16 GiB" in out[0].text
    assert "qwen3vl-german-pages-v1" in out[0].text
