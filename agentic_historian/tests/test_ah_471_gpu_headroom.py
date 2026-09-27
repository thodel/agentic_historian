"""`gpu_headroom()` — does this model fit on that card, and what is in the way (#471).

The subtraction existed nowhere. `/gpu` said what was on the cards, `/models`
said what a model needed, and a human laid the two JSON blocks side by side — so
on 2026-09-21 a batch learned the answer from the vLLM cold start instead:

    GPU 1 has 9742 MB free, qwen3.5-4b-german-xix-v2 needs 15848 MB

Both cards below are recordings of that week: the 21.09. card with atr-party
resident, and the 23.09. card after it was stopped. The fixtures are the test —
if the arithmetic here stops reproducing 6106 MiB short, it has stopped
describing the gateway it is a preflight for.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from utils.atr_gpu import Headroom, gpu_headroom, headroom_report, needed_mib  # noqa: E402

FORTY_ONE_DAYS_S = 41 * 86400

#: idhefix card 0: the neighbours' RAG service. Four gunicorn workers of the user
#: `change`, 10 392 MiB between them, untouched for 41 days. Memory we cannot
#: have, and never a candidate for anything.
GUNICORN = [
    {"pid": 1101, "used_mib": 2598, "service": "gunicorn.service",
     "user": "change", "age_s": FORTY_ONE_DAYS_S, "orphaned": False},
    {"pid": 1102, "used_mib": 2598, "service": "gunicorn.service",
     "user": "change", "age_s": FORTY_ONE_DAYS_S, "orphaned": False},
    {"pid": 1103, "used_mib": 2598, "service": "gunicorn.service",
     "user": "change", "age_s": FORTY_ONE_DAYS_S, "orphaned": False},
    {"pid": 1104, "used_mib": 2598, "service": "gunicorn.service",
     "user": "change", "age_s": FORTY_ONE_DAYS_S, "orphaned": False},
]

MODELS = {"models": [
    {"id": "qwen3.5-4b-german-xix-v2", "engine": "vllm",
     "vram_mb": 12000, "gpu_affinity": 1, "residency": "lazy"},
    {"id": "kraken-no-size", "engine": "kraken", "vram_mb": 0, "gpu_affinity": 1},
    {"id": "floating-model", "engine": "vllm", "vram_mb": 12000, "gpu_affinity": None},
]}

#: 2026-09-21. atr-party resident on card 1; 9742 MiB free.
GPU_21_09 = {
    "host": "idhefix",
    "cards": [
        {"index": 0, "memory_total_mib": 46068, "memory_free_mib": 35676,
         "processes": GUNICORN},
        {"index": 1, "memory_total_mib": 46068, "memory_free_mib": 9742,
         "processes": [
             {"pid": 2201, "used_mib": 18540, "service": "atr-party.service",
              "user": "atr", "age_s": 7200.0, "orphaned": False},
             {"pid": 2202, "used_mib": 12912, "service": "atr-gateway.service",
              "user": "atr", "age_s": 3600.0, "orphaned": False},
             {"pid": 2203, "used_mib": 3840, "service": "atr-trocr.service",
              "user": "atr", "age_s": 90000.0, "orphaned": False},
         ]},
    ],
    "vllm": {"gpu": 1, "pids": [2202]},
}

#: 2026-09-23. atr-party stopped; 40 595 MiB free on card 1.
GPU_23_09 = {
    "host": "idhefix",
    "cards": [
        {"index": 0, "memory_total_mib": 46068, "memory_free_mib": 35676,
         "processes": GUNICORN},
        {"index": 1, "memory_total_mib": 46068, "memory_free_mib": 40595,
         "processes": [
             {"pid": 2203, "used_mib": 3840, "service": "atr-trocr.service",
              "user": "atr", "age_s": 90000.0, "orphaned": False},
         ]},
    ],
    "vllm": {"gpu": 1, "pids": []},
}


def probe(gpu=GPU_21_09, models=MODELS) -> dict:
    return {"gateway": "http://idhefix:8000", "health": {"status": "ok"},
            "models": models, "gpu_serving": gpu,
            "gpu_training": {"host": "asteraix", "cards": []}}


MODEL = "qwen3.5-4b-german-xix-v2"


# ── the arithmetic the gateway actually refuses on ──────────────────────────
def test_needed_is_the_gateways_threshold_not_the_bare_weights():
    """12000 x 1.15 + 2048. The cold start of 2026-09-21 printed 15848, and a
    preflight comparing against the bare 12000 would have waved it through."""
    assert needed_mib(12000) == 15848


# ── the card that did not fit ───────────────────────────────────────────────
def test_the_21_09_card_does_not_fit_and_says_by_how_much():
    result = gpu_headroom(MODEL, probe(GPU_21_09))

    assert result.fits is False
    assert result.needed_mib == 15848
    assert result.free_mib == 9742
    assert result.shortfall_mib == 6106


def test_the_largest_thing_of_ours_is_named():
    """What V4 would have to ask for. Naming it is the whole difference between
    'it does not fit' and 'it does not fit, and here is the lever'."""
    result = gpu_headroom(MODEL, probe(GPU_21_09))

    assert result.largest_ours.service == "atr-party.service"
    assert result.largest_ours.used_mib == 18540
    assert "atr-party.service" in result.reason


def test_the_card_index_comes_from_gpu_affinity():
    assert gpu_headroom(MODEL, probe(GPU_21_09)).card == 1


# ── the card that did fit ───────────────────────────────────────────────────
def test_the_23_09_card_fits_once_party_is_stopped():
    result = gpu_headroom(MODEL, probe(GPU_23_09))

    assert result.fits is True
    assert result.free_mib == 40595
    assert result.shortfall_mib == 0


# ── whose memory it is ──────────────────────────────────────────────────────
def test_the_neighbours_gunicorn_workers_are_theirs_and_never_ours():
    """Card 0's four workers are memory we cannot have. Summing them with ours
    would invite exactly the mistake V4 must not make."""
    result = gpu_headroom("card-zero-model", {
        **probe(), "models": {"models": [
            {"id": "card-zero-model", "vram_mb": 12000, "gpu_affinity": 0}]}})

    assert [o.service for o in result.ours] == []
    assert {o.service for o in result.theirs} == {"gunicorn.service"}
    assert {o.user for o in result.theirs} == {"change"}
    assert result.theirs_mib == 10392


def test_our_engines_and_our_vllm_children_are_both_ours():
    """They run in different atr- units — atr-party, atr-trocr, and the vLLM
    children inside atr-gateway — and all three are ours."""
    result = gpu_headroom(MODEL, probe(GPU_21_09))

    assert {o.service for o in result.ours} == {
        "atr-party.service", "atr-gateway.service", "atr-trocr.service"}
    assert result.theirs == []
    assert result.ours_mib == 18540 + 12912 + 3840


def test_ours_is_listed_largest_first():
    assert [o.used_mib for o in gpu_headroom(MODEL, probe(GPU_21_09)).ours] == \
        [18540, 12912, 3840]


# ── unknown is not the same as fitting ──────────────────────────────────────
def test_a_model_without_vram_mb_is_unknown_not_fitting():
    result = gpu_headroom("kraken-no-size", probe(GPU_23_09))

    assert result.fits is None
    assert "unrecorded, not zero" in result.reason


def test_a_model_without_gpu_affinity_is_unknown_not_fitting():
    """Which card it lands on is the ModelManager's choice at launch, so there is
    no card to subtract from — even on a box with room to spare."""
    result = gpu_headroom("floating-model", probe(GPU_23_09))

    assert result.fits is None
    assert result.card is None


def test_a_failed_gpu_probe_is_not_a_fit():
    """The one default that would defeat the module: an unreachable /gpu read as
    'no obstacles'."""
    result = gpu_headroom(MODEL, {**probe(), "gpu_serving": {
        "error": "ConnectError: no route to host"}})

    assert result.fits is None
    assert result.free_mib is None
    assert "not the same as sufficient" in result.reason


def test_a_non_200_from_gpu_is_not_a_fit_either():
    result = gpu_headroom(MODEL, {**probe(), "gpu_serving": {
        "status": 502, "body": "nvidia-smi failed"}})

    assert result.fits is None


def test_a_failed_models_probe_is_not_a_fit():
    result = gpu_headroom(MODEL, {**probe(), "models": {"error": "timeout"}})

    assert result.fits is None


def test_an_unregistered_model_is_not_a_fit():
    result = gpu_headroom("never-heard-of-it", probe(GPU_23_09))

    assert result.fits is None
    assert "not in /models" in result.reason


def test_a_card_the_gpu_report_does_not_have_is_not_a_fit():
    """#452: the wrong card stood in the report for months. A card index that is
    simply absent must not resolve to the first one that is there."""
    result = gpu_headroom(MODEL, {**probe(), "gpu_serving": {
        "host": "idhefix", "cards": [{"index": 0, "memory_free_mib": 46000,
                                      "processes": []}]}})

    assert result.fits is None
    assert "no card 1" in result.reason


# ── nothing here touches the card ───────────────────────────────────────────
def test_the_function_is_pure_with_respect_to_the_probe():
    """No side effects, and no mutation of the caller's probe: V2 and V4 build on
    this, and a preflight that edited what it was given would be a poor start."""
    import copy

    payload = probe(GPU_21_09)
    before = copy.deepcopy(payload)

    gpu_headroom(MODEL, payload)

    assert payload == before


def test_no_probe_is_taken_when_one_is_passed(monkeypatch):
    """Asking the gateway twice for the same two reports is two chances for them
    to disagree."""
    import mcp_atr.server as server

    def boom():
        raise AssertionError("gateway_probe must not be called with a probe in hand")

    monkeypatch.setattr(server, "gateway_probe", boom)

    assert gpu_headroom(MODEL, probe(GPU_23_09)).fits is True


# ── the whole report, as gateway_models serves it ───────────────────────────
def test_the_report_covers_every_registered_model():
    report = headroom_report(probe(GPU_21_09))

    assert set(report) == {MODEL, "kraken-no-size", "floating-model"}
    assert report[MODEL]["shortfall_mib"] == 6106
    assert report["kraken-no-size"]["fits"] is None


def test_the_report_carries_both_sides_of_the_card():
    row = headroom_report(probe(GPU_21_09))[MODEL]

    assert row["ours_mib"] == 35292
    assert row["theirs_mib"] == 0
    assert [o["service"] for o in row["ours"]][0] == "atr-party.service"


def test_the_report_survives_a_models_probe_that_failed():
    assert "error" in headroom_report({**probe(), "models": {"error": "timeout"}})


def test_headroom_defaults_to_unknown():
    """The dataclass itself must not default to fitting."""
    assert Headroom(model="x").fits is None


# ── what the MCP tool actually hands back ───────────────────────────────────
def test_gateway_report_carries_the_headroom_beside_the_two_raw_reports(monkeypatch):
    """The wiring, at the level the tool calls it — module-level for the reason
    `gateway_probe` is, since the last reporting bug here shipped precisely
    because the logic sat inside the tool where nothing could look at it."""
    import mcp_atr.server as server

    monkeypatch.setattr(server, "gateway_probe", lambda: probe(GPU_21_09))

    report = server.gateway_report()

    assert report["gpu_serving"]["host"] == "idhefix"        # still there
    assert report["headroom"][MODEL]["shortfall_mib"] == 6106
    assert report["headroom"][MODEL]["fits"] is False
