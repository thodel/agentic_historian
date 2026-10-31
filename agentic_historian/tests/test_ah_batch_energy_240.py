"""serving-atr-inference#240 — a batch reports what the card drew, per page.

The gateway meters its cards (``GET /gpu/energy``); the batch reads the meter
before and after each model — model-major is what makes that attributable —
and the report carries Wh per page and per 1 000 lines. These pin:

* the difference of two readings is the model's card, not another one;
* another model's calls or a foreign process inside the window make it
  *shared*, named in the report, never split;
* a gateway restart in between, or no reading, is said, not computed;
* ``watched`` below 1 is carried, so a floor reads as a floor;
* the energy survives into ``report.json``, ``energy.json`` and a rebuilt
  report; a run without the meter has no energy section;
* the reader fails soft and says so once.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import atr_batch as batch  # noqa: E402


def a_png(path: Path) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), "white").save(path, format="PNG")
    return path


def reading(text="Euer\nHochwohl\ngeboren"):
    return SimpleNamespace(text=text, lines=[{"text": t} for t in text.split("\n")],
                           confidence=0.9, engine="vllm", timing_ms=12, truncated=False,
                           service_version="0.9.1", segmented_by=None, second_opinion=None)


def snap(*, t, wh, measured, gap=0.0, requests=None, foreign=0, since="S", card=1):
    return {"since": since, "monotonic_s": t,
            "cards": {str(card): {"wh": wh, "measured_s": measured, "gap_s": gap,
                                  "foreign_seen": foreign}},
            "requests": requests or {}, "model": {"id": "m", "card": card}}


# ── energy_between ───────────────────────────────────────────────────────────

def test_the_difference_is_the_models_card_and_its_window():
    e = batch.energy_between(snap(t=100, wh=10.0, measured=90, requests={"m": 5}),
                             snap(t=400, wh=35.5, measured=388, gap=2, requests={"m": 25}), "m")
    assert e == {"card": 1, "wh": 25.5, "window_s": 300, "measured_s": 298, "gap_s": 2,
                 "watched": 0.993, "calls": 20, "other_calls": {}, "foreign_seen": 0,
                 "shared": False}


def test_another_models_calls_or_a_neighbour_make_it_shared():
    other = batch.energy_between(snap(t=0, wh=0, measured=0, requests={"m": 0, "x": 3}),
                                 snap(t=60, wh=5, measured=60, requests={"m": 10, "x": 5}), "m")
    assert other["shared"] is True and other["other_calls"] == {"x": 2}
    neighbour = batch.energy_between(snap(t=0, wh=0, measured=0, foreign=1),
                                     snap(t=60, wh=5, measured=60, foreign=2), "m")
    assert neighbour["shared"] is True and neighbour["foreign_seen"] == 1


def test_a_restart_or_a_missing_reading_is_said_not_computed():
    assert batch.energy_between(None, snap(t=1, wh=1, measured=1), "m") is None
    restarted = batch.energy_between(snap(t=0, wh=50, measured=0, since="A"),
                                     snap(t=9, wh=2, measured=9, since="B"), "m")
    assert restarted == {"unmeasured": "the gateway restarted while this model ran"}
    other_card = batch.energy_between(snap(t=0, wh=0, measured=0, card=0),
                                      snap(t=9, wh=1, measured=9, card=0) | {"model": {"card": 1}}, "m")
    assert other_card == {"unmeasured": "the meter has no reading for card 1"}


# ── through a run ────────────────────────────────────────────────────────────

class Meter:
    """A gateway meter that a fake recogniser drives: 30 Wh and 60 s per page."""

    def __init__(self, *, neighbour_on=None):
        self.wh = self.t = 0.0
        self.calls: dict = {}
        self.neighbour_on = neighbour_on

    def recognise(self, image, model):
        self.wh += 30.0
        self.t += 60.0
        self.calls[model] = self.calls.get(model, 0) + 1
        if model == self.neighbour_on:
            self.calls["someone-else"] = self.calls.get("someone-else", 0) + 1
        return reading()

    def read(self, model):
        return {"since": "S", "monotonic_s": self.t,
                "cards": {"1": {"wh": self.wh, "measured_s": self.t, "gap_s": 0.0,
                                "foreign_seen": 0}},
                "requests": dict(self.calls), "model": {"id": model, "card": 1}}


def _run(tmp_path, meter, models=("vlm-a", "vlm-b"), energy=True):
    share = tmp_path / "share"
    for i in range(4):
        a_png(share / f"{i:03d}.png")
    pages = batch.discover_pages(share)
    out = tmp_path / "out"
    report = batch.run_batch(pages, list(models), "run", out, meter.recognise,
                             energy=meter.read if energy else None, concurrency=1,
                             sleep=lambda s: None)
    batch.write_report(report)
    return report, out


def test_each_model_carries_its_own_watt_hours_per_page(tmp_path):
    report, out = _run(tmp_path, Meter(neighbour_on="vlm-b"))
    a, b = report.models
    assert a.energy["wh"] == 120.0 and a.energy["shared"] is False and a.energy["calls"] == 4
    assert a.wh_per_page == 30.0
    assert a.wh_per_1000_lines == pytest.approx(120.0 / a.lines * 1000)
    assert b.energy["shared"] is True and b.energy["other_calls"] == {"someone-else": 4}

    md = (out / "report.md").read_text(encoding="utf-8")
    assert "## Energy" in md
    assert f"| `vlm-a` | 1 | 120.0 | 30.00 | {a.wh_per_1000_lines:.1f} | 100% | no |" in md
    assert "| `vlm-b` | 1 | 120.0 | 30.00 |" in md and "yes: `someone-else` ×4" in md
    saved = json.loads((out / "report.json").read_text())
    assert saved["models"][0]["energy"]["wh"] == 120.0


def test_a_failed_page_costs_energy_but_is_not_a_page_read(tmp_path):
    """Per page *read*: the failed call drew power and stays in the numerator."""
    meter = Meter()
    recognise = meter.recognise

    def flaky(image, model):
        out = recognise(image, model)
        if Path(image).name.startswith("001"):
            raise RuntimeError("engine said no")
        return out
    meter.recognise = flaky
    report, _ = _run(tmp_path, meter, models=("vlm-a",))
    a = report.models[0]
    assert (a.done, a.failed) == (3, 1)
    calls = meter.calls["vlm-a"]
    assert calls > 4, "the failed page was retried, and every try drew power"
    assert a.energy["wh"] == 30.0 * calls and a.wh_per_page == 30.0 * calls / 3


def test_a_rebuilt_report_keeps_the_energy(tmp_path):
    _, out = _run(tmp_path, Meter(), models=("vlm-a",))
    assert json.loads((out / "vlm-a" / batch.ENERGY_FILE).read_text())["wh"] == 120.0
    rebuilt = batch.report_from_outputs(out)
    assert rebuilt.models[0].energy["wh"] == 120.0
    assert "## Energy" in batch.format_report(rebuilt)


def test_without_the_meter_there_is_no_energy_section(tmp_path):
    report, out = _run(tmp_path, Meter(), models=("vlm-a",), energy=False)
    assert report.models[0].energy is None and report.models[0].wh_per_page is None
    assert "## Energy" not in (out / "report.md").read_text(encoding="utf-8")
    assert not (out / "vlm-a" / batch.ENERGY_FILE).exists()


# ── the reader ───────────────────────────────────────────────────────────────

def test_the_reader_sends_the_key_and_the_model(monkeypatch):
    seen = {}

    def get(url, params, headers, timeout):
        seen.update(url=url, params=params, headers=headers)
        return httpx.Response(200, json={"since": "S"}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    read = batch.energy_reader(base_url="http://gw:8200/", api_key="k")
    assert read("vlm-a") == {"since": "S"}
    assert seen == {"url": "http://gw:8200/gpu/energy", "params": {"model": "vlm-a"},
                    "headers": {"X-API-Key": "k"}}


def test_a_gateway_without_the_meter_reads_as_none_said_once(monkeypatch):
    from loguru import logger
    said = []
    sink = logger.add(lambda m: said.append(str(m)), level="WARNING")

    def get(url, **kw):
        return httpx.Response(404, json={"detail": "Not Found"}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", get)
    try:
        read = batch.energy_reader(base_url="http://gw:8200", api_key="k")
        assert read("a") is None and read("b") is None
    finally:
        logger.remove(sink)
    assert len([s for s in said if "energy not read" in s]) == 1
