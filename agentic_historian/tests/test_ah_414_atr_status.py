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
    assert not out.startswith("**")      # ...and being not-ours is not an alarm
    assert "⚠" not in out


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


# ── #418: "not ours" is not the same as "unexplained" ───────────────────────

SWEEP = {"pid": 2771780, "used_mib": 15638, "registered": False,
         "own_service": False, "orphaned": False, "service": None,
         "user": "tobias", "age_s": 21397.0,
         "command": ".venvs/kraken-train/bin/ketos --device cuda:0 train ..."}
NEIGHBOUR = [{"pid": 2351630 + i, "used_mib": 2610, "registered": False,
              "own_service": False, "orphaned": False,
              "service": "gunicorn.service", "user": "change",
              "age_s": 2365849.0, "command": "gunicorn ragchange.wsgi"}
             for i in range(4)]


def test_the_neighbours_rag_service_is_not_an_alarm():
    """The live payload opened every /atr_gpu with a red banner over four
    gunicorn workers that had been up for 657 hours. A warning that is always
    on is not a warning."""
    payload = {"cards": [_card(0, memory_used_mib=10466, unaccounted_mib=10440,
                               processes=NEIGHBOUR)],
               "job_attribution_available": True}
    out = atr_status.format_gpu(payload)
    assert not out.startswith("**")
    assert "⚠" not in out
    assert "10440 MiB fremd" in out      # stated as capacity, which it is


def test_four_workers_of_one_unit_are_one_row():
    """Printing them separately is how the row that matters gets read past."""
    payload = {"cards": [_card(0, unaccounted_mib=10440, processes=NEIGHBOUR)],
               "job_attribution_available": True}
    out = atr_status.format_gpu(payload)
    assert "4× gunicorn.service" in out
    assert out.count("gunicorn.service") == 1


def test_an_unmanaged_process_is_flagged_even_though_it_is_ours():
    """sweep_seed43.sh: real work, invisible to /jobs, and 15.6 GB a queued job
    will be told it cannot have (#418)."""
    payload = {"cards": [_card(1, memory_used_mib=25152, unaccounted_mib=15638,
                               processes=[SWEEP])],
               "job_attribution_available": True}
    out = atr_status.format_gpu(payload)
    assert out.startswith("**")
    assert "OHNE DIENST" in out
    assert "2771780" in out
    assert "ketos" in out                # the line that answers "what is that?"


def test_an_orphan_outranks_a_neighbour_on_the_same_card():
    payload = {"cards": [_card(0, memory_used_mib=38000, unaccounted_mib=38000,
                               orphaned_mib=27530,
                               processes=NEIGHBOUR + [dict(ORPHAN["cards"][0]["processes"][0])])],
               "job_attribution_available": True}
    out = atr_status.format_gpu(payload)
    body = out[out.index("GPU 0"):]
    assert body.index("VERWAIST") < body.index("fremd")
    assert out.startswith("**")


def test_a_card_with_only_our_own_engines_stays_one_line():
    out = atr_status.format_gpu(CLEAN)
    assert "fremd" not in out
    assert "alles zugeordnet" in out

# ── #439: two-machine GPU reports ────────────────────────────────────────────

import asyncio
import atr_status

def _card(index, **kw):
    base = dict(index=index, name='A40', memory_used_mib=1619,
                memory_total_mib=46068, utilisation_pct=0,
                unaccounted_mib=0, service_mib=0, orphaned_mib=0, processes=[])
    base.update(kw)
    return base

VLLM_IDHEFIX = {
    'host': 'idhefix',
    'cards': [
        _card(0, memory_used_mib=10440, unaccounted_mib=10440, processes=[
            {'pid': 27701, 'used_mib': 2610, 'registered': False,
             'own_service': False, 'orphaned': False,
             'service': 'gunicorn.service', 'user': 'change',
             'age_s': 2365849.0, 'command': 'gunicorn ragchange.wsgi'},
            {'pid': 27702, 'used_mib': 2610, 'registered': False,
             'own_service': False, 'orphaned': False,
             'service': 'gunicorn.service', 'user': 'change',
             'age_s': 2365849.0, 'command': 'gunicorn ragchange.wsgi'},
        ]),
        _card(1, memory_used_mib=1619, service_mib=1619, processes=[
            {'pid': 27710, 'used_mib': 1619, 'registered': False,
             'own_service': True, 'orphaned': False,
             'service': 'atr-kraken.service', 'user': 'tobias',
             'age_s': 6000.0, 'command': 'kraken engine'},
        ]),
    ],
    'vllm': {
        'gpu': 1,
        'service': 'atr-gateway.service',
        'pids': [27720],
        'residents': [
            {'id': 'qwen3vl-german-xix-v1', 'vram_mb': 12000, 'residency': 0.61},
        ],
        'budget_mb': 28176,
        'budget': '28176 MiB = 46068 MiB gpu 1 - 15844 MiB engines - 2048 MiB reserve',
    },
}

TRAINING_CARDS = {
    'cards': [
        _card(1, service_mib=1600, processes=[
            {'pid': 2757328, 'used_mib': 1600, 'registered': False,
             'own_service': True, 'orphaned': False,
             'service': 'atr-trocr.service', 'user': 'tobias',
             'age_s': 6000.0, 'command': 'trocr engine'},
        ]),
    ],
    'job_attribution_available': True,
}

# ── format_serving_gpu ───────────────────────────────────────────────────────

def test_the_serving_view_names_the_residents_and_the_budget():
    out = atr_status.format_serving_gpu(VLLM_IDHEFIX)
    assert 'qwen3vl-german-xix-v1' in out
    assert '12000 MB' in out
    assert '28176 MiB' in out

def test_serving_view_empty_vllm_is_no_data():
    assert 'keine Daten' in atr_status.format_serving_gpu({})

def test_serving_view_no_residents_budget_only():
    payload = {'vllm': {'budget_mb': 28176}}
    out = atr_status.format_serving_gpu(payload)
    assert '28176 MiB' in out
    assert 'keine Daten' not in out

# ── format_gpu on serving data ────────────────────────────────────────────────

def test_a_healthy_idhefix_raises_no_alarm():
    out = atr_status.format_gpu(VLLM_IDHEFIX)
    assert not out.startswith('**')
    assert 'gunicorn.service' in out
    assert 'VERWAIST' not in out
    assert '× gunicorn.service' in out

def test_own_service_on_serving_card_does_not_trigger_alarm():
    out = atr_status.format_gpu(VLLM_IDHEFIX)
    assert not out.startswith('**')
    assert 'alles zugeordnet' in out

# ── both-machines helper and tests ───────────────────────────────────────────

async def _safe(coro, label):
    try:
        return (label, await coro, None)
    except atr_status.AtrStatusError as exc:
        return (label, None, str(exc))
    except Exception as exc:  # noqa: BLE001
        return (label, None, f"{type(exc).__name__}: {exc}")

async def _both(serving_payload, training_payload):
    async def _ms(): return serving_payload
    async def _mt(): return training_payload
    serving_label = 'Serving (idhefix)'
    training_label = 'Training (asterAIx)'
    serving_res, training_res = await asyncio.gather(
        _safe(_ms(), serving_label),
        _safe(_mt(), training_label),
    )
    parts, errors = [], []
    for label, payload, err in [serving_res, training_res]:
        if err:
            errors.append(f'**{label}** -- {err}')
            continue
        parts.append(f'**{label}**')
        if label == serving_label:
            parts.append(f'  {atr_status.format_serving_gpu(payload)}')
        parts.append(atr_status.format_gpu(payload))
    if errors:
        parts.append('')
        parts.extend(errors)
    return parts

def test_atr_gpu_shows_both_machines():
    parts = asyncio.run(_both(VLLM_IDHEFIX, TRAINING_CARDS))
    text = chr(10).join(parts)
    assert '**Serving (idhefix)**' in text
    assert '**Training (asterAIx)**' in text
    assert 'qwen3vl-german-xix-v1' in text
    assert '28176 MiB' in text
    assert 'alles zugeordnet' in text

def test_one_unreachable_machine_does_not_hide_the_other():
    async def _run():
        async def _failing():
            raise atr_status.AtrStatusError('Connection refused')
        async def _mt(): return TRAINING_CARDS
        serving_label = 'Serving (idhefix)'
        training_label = 'Training (asterAIx)'
        serving_res, training_res = await asyncio.gather(
            _safe(_failing(), serving_label),
            _safe(_mt(), training_label),
        )
        parts, errors = [], []
        for label, payload, err in [serving_res, training_res]:
            if err:
                errors.append(f'**{label}** -- {err}')
                continue
            parts.append(f'**{label}**')
            if label == serving_label:
                parts.append(f'  {atr_status.format_serving_gpu(payload)}')
            parts.append(atr_status.format_gpu(payload))
        if errors:
            parts.append('')
            parts.extend(errors)
        return chr(10).join(parts)
    text = asyncio.run(_run())
    assert 'Training (asterAIx)' in text
    assert 'alles zugeordnet' in text
    assert 'Serving (idhefix)' in text
    assert 'Connection refused' in text
