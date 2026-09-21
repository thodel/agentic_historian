"""#439: /atr_gpu shows both machines since the 16.09. split.

idhefix serves (``GET /gpu``), asteraix trains (``GET /train/gpu``). Before this,
/atr_gpu showed only the trainer — on 2026-09-21 a batch died on "GPU 1 has
9742 MB free, needs 15848 MB" while the only card Discord could show was the
trainer's, idle.

Offline — the gateway is stubbed. The fixtures carry the shape measured live on
2026-09-21.
"""

import asyncio
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_status  # noqa: E402


def _proc(pid, mib, service, own, user="tobias", age=500000.0, command="engine"):
    return {"pid": pid, "used_mib": mib, "registered": False, "own_service": own,
            "orphaned": False, "service": service, "user": user, "age_s": age,
            "command": command, "job_id": None}


def _card(index, procs, util=0, used=10000):
    return {"index": index, "name": "NVIDIA A40", "memory_used_mib": used,
            "memory_total_mib": 46068, "memory_free_mib": 46068 - used,
            "utilisation_pct": util, "memory_utilisation_pct": 0,
            "unaccounted_mib": 0, "service_mib": 0, "orphaned_mib": 0,
            "processes": procs}


# idhefix as measured: the neighbours' RAG on card 0, our engines and the vLLM
# child of the gateway on card 1. No `job_attribution_available` — on purpose,
# because format_gpu reads a `false` there as "trainer unreachable".
IDHEFIX = {
    "host": "srv",
    "cards": [
        _card(0, [_proc(2351630 + i, 2610, "gunicorn.service", False,
                        user="change", age=2750000.0,
                        command="/home/change/rag-change/venv/bin/gunicorn")
                  for i in range(4)]),
        _card(1, [_proc(2757327, 1636, "atr-kraken.service", True),
                  _proc(2757328, 3840, "atr-trocr.service", True),
                  _proc(2766764, 8910, "atr-party.service", True),
                  _proc(3049706, 12000, "atr-gateway.service", True,
                        command="vllm serve")], util=40, used=27000),
    ],
    "vllm": {"gpu": 1, "service": "atr-gateway.service", "pids": [3046243, 3049706],
             "residents": [{"id": "qwen3.5-4b-german-xix-v2", "vram_mb": 12000,
                            "residency": "lazy"}],
             "budget_mb": 30620,
             "budget": "30620 MiB = 46068 MiB gpu 1 - 13400 MiB engines - 2048 MiB reserve"},
}

ASTERAIX = {
    "host": "dhserver03",
    "cards": [_card(0, []), _card(1, [])],
    "job_attribution_available": True,
    "known_job_pids": [],
}


def _stub(monkeypatch, serving, training):
    async def _serving():
        if isinstance(serving, Exception):
            raise serving
        return serving

    async def _training():
        if isinstance(training, Exception):
            raise training
        return training
    monkeypatch.setattr(atr_status, "serving_gpu", _serving)
    monkeypatch.setattr(atr_status, "gpu", _training)


def _views(monkeypatch, serving=IDHEFIX, training=ASTERAIX):
    _stub(monkeypatch, serving, training)
    return asyncio.run(atr_status.gpu_views())


# ── the routes ───────────────────────────────────────────────────────────────

def test_the_two_routes_are_the_two_machines(monkeypatch):
    asked = []

    async def fake_get(path, params=None):
        asked.append(path)
        return {}
    monkeypatch.setattr(atr_status, "_get", fake_get)
    asyncio.run(atr_status.serving_gpu())
    asyncio.run(atr_status.gpu())
    assert asked == ["/gpu", "/train/gpu"], "gpu() keeps meaning the trainer"


# ── both machines ────────────────────────────────────────────────────────────

def test_atr_gpu_shows_both_machines(monkeypatch):
    text = "\n".join(atr_status.format_gpu_views(_views(monkeypatch)))
    assert "Serving — idhefix" in text and "Training — asteraix" in text
    assert text.index("Serving") < text.index("Training"), "serving first"


def test_the_labels_are_ours_not_the_payloads_host(monkeypatch):
    """`srv` and `dhserver03` are names nobody can place (#436)."""
    text = "\n".join(atr_status.format_gpu_views(_views(monkeypatch)))
    assert "srv" not in text.replace("service", "")
    assert "dhserver03" not in text


def test_one_unreachable_machine_does_not_hide_the_other(monkeypatch):
    down = atr_status.AtrStatusError("ConnectTimeout talking to http://x/train/gpu")
    views = _views(monkeypatch, training=down)
    text = "\n".join(atr_status.format_gpu_views(views))
    assert "ConnectTimeout" in text
    assert "vLLM: qwen3.5-4b-german-xix-v2" in text, "the serving view survived"


def test_an_unexpected_error_is_also_confined_to_its_section(monkeypatch):
    views = _views(monkeypatch, serving=ValueError("bad json"))
    labels = [(label, error is not None) for label, _, error in views]
    assert labels == [("Serving — idhefix", True), ("Training — asteraix", False)]


def test_a_healthy_idhefix_raises_no_alarm(monkeypatch):
    """Neighbours' gunicorn is foreign, engines and the vLLM child are ours."""
    text = "\n".join(atr_status.format_gpu_views(_views(monkeypatch)))
    assert "⚠" not in text
    assert "kein Dienst erklärt" not in text
    assert "Trainer nicht erreichbar" not in text, \
        "idhefix has no jobs; a missing attribution flag must not read as an outage"


# ── the serving section ──────────────────────────────────────────────────────

def test_the_serving_view_names_the_residents_and_the_budget(monkeypatch):
    text = "\n".join(atr_status.format_gpu_views(_views(monkeypatch)))
    assert "vLLM: qwen3.5-4b-german-xix-v2 (12000 MB, lazy) · Budget 30620 MiB" in text


def test_no_resident_model_is_said_rather_than_left_blank():
    line = atr_status.format_vllm({"residents": [], "budget_mb": 30620})
    assert line == "vLLM: nichts geladen · Budget 30620 MiB"


def test_the_trainer_section_carries_no_vllm_line(monkeypatch):
    views = _views(monkeypatch)
    training = atr_status.format_gpu_views([views[1]])[0]
    assert "vLLM" not in training


# ── Discord's limit ──────────────────────────────────────────────────────────

def test_every_message_fits_discord(monkeypatch):
    many = [_proc(4000000 + i, 900, None, False, command="x" * 150)
            for i in range(60)]
    busy = dict(ASTERAIX, cards=[_card(0, many), _card(1, many)])
    messages = atr_status.format_gpu_views(_views(monkeypatch, training=busy))
    assert all(len(m) <= 2000 for m in messages)
    assert any("Serving" in m for m in messages)
    assert any("Training" in m for m in messages)


def test_two_short_sections_are_one_message(monkeypatch):
    assert len(atr_status.format_gpu_views(_views(monkeypatch))) == 1
