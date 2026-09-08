"""#414: reading the training server from Discord.

Offline — the gateway is stubbed. What is tested is the formatting, because the
formatting is the feature: the same JSON can either surface a sixteen-hour orphan
or bury it under rows that are fine.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_status  # noqa: E402


# ── /atr_gpu — the one that matters ─────────────────────────────────────────

def _card(index, **kw):
    base = dict(index=index, name="A40", memory_used_mib=1619,
                memory_total_mib=46068, utilisation_pct=0,
                unaccounted_mib=0, service_mib=0, orphaned_mib=0, processes=[])
    base.update(kw)
    return base


CLEAN = {"cards": [_card(1, service_mib=1600, processes=[
    {"pid": 2757328, "used_mib": 1600, "registered": False, "own_service": True,
     "orphaned": False, "service": "atr-trocr.service", "user": "tobias",
     "age_s": 6000.0, "command": "trocr engine"}])],
    "job_attribution_available": True}

ORPHAN = {"cards": [_card(0, memory_used_mib=34300, unaccounted_mib=27530,
                          orphaned_mib=27530, processes=[
    {"pid": 2743851, "used_mib": 27530, "registered": False, "own_service": False,
     "orphaned": True, "service": None, "user": None, "age_s": 57600.0,
     "command": None}])],
    "job_attribution_available": True}


def test_a_clean_card_prints_one_line_not_a_table():
    """A report that is long when all is well trains people to skim it."""
    out = atr_status.format_gpu(CLEAN)
    assert "alles zugeordnet" in out
    assert "2757328" not in out          # the engine is explained; do not list it
    assert "⚠" not in out
    assert not out.startswith("**")      # no alarm banner


def test_the_orphan_is_flagged_named_and_aged():
    out = atr_status.format_gpu(ORPHAN)
    assert out.startswith("**")          # banner above the fence
    assert "⚠" in out
    assert "VERWAIST" in out
    assert "2743851" in out
    assert "27530" in out
    assert "16 h" in out                 # the number the endpoint exists for


def test_a_foreign_service_is_listed_by_its_unit():
    payload = {"cards": [_card(0, unaccounted_mib=2610, processes=[
        {"pid": 2351630, "used_mib": 2610, "registered": False,
         "own_service": False, "orphaned": False, "service": "gunicorn.service",
         "user": "change", "age_s": 2251639.0, "command": "gunicorn ..."}])],
        "job_attribution_available": True}
    out = atr_status.format_gpu(payload)
    assert "gunicorn.service" in out and "change" in out
    assert "VERWAIST" not in out         # it is alive, just not ours


def test_a_lost_trainer_is_stated_rather_than_implied():
    payload = dict(CLEAN, job_attribution_available=False)
    assert "Trainer nicht erreichbar" in atr_status.format_gpu(payload)


# ── the other three ─────────────────────────────────────────────────────────

def test_jobs_are_newest_first_and_capped():
    payload = {"jobs": [{"id": f"j{i}", "status": "done", "stage": "test",
                         "created_at": f"2026-09-0{i}"} for i in range(1, 8)]}
    out = atr_status.format_jobs(payload, limit=3)
    assert "j7" in out and "j1" not in out
    assert "(7 insgesamt)" in out


def test_an_empty_queue_says_so():
    assert "Keine" in atr_status.format_jobs({"jobs": []})


def test_a_queued_job_shows_why_it_waits():
    """The line that explains a job sitting still against free memory."""
    out = atr_status.format_job(
        {"id": "j1", "status": "queued", "stage": None,
         "queued_reason": "GPU 1 has 11185 MB free, need 24000 MB"})
    assert "11185" in out and "wartet" in out


def test_a_failed_job_shows_its_error():
    out = atr_status.format_job({"id": "j1", "status": "failed", "error": "boom"})
    assert "boom" in out


def test_the_log_shows_the_tail_not_the_head():
    payload = {"lines": [f"line {i}" for i in range(50)]}
    out = atr_status.format_log(payload, "j1")
    assert "line 49" in out and "line 0" not in out


def test_a_missing_log_is_not_an_error():
    assert "noch nicht" in atr_status.format_log({"lines": []}, "j1")


def test_output_stays_within_the_discord_limit():
    payload = {"cards": [_card(0, unaccounted_mib=999, processes=[
        {"pid": i, "used_mib": 100, "registered": False, "own_service": False,
         "orphaned": False, "service": None, "user": "u",
         "age_s": 60.0, "command": "x" * 200} for i in range(200)])],
        "job_attribution_available": True}
    assert len(atr_status.format_gpu(payload)) <= 2000
