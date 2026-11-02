"""#474 — the run says what it cost the machine: which engines, how many MiB,
from when to when, and whether they came back.

Without it an engine a lease stopped looks, from outside, exactly like one that
crashed. These pin:

* a run with a lease writes a section with engine, MiB and span, and says the
  gateway confirmed the restore; a run without one writes no section at all —
  an empty one would read as if something had been freed;
* a restore that failed stands as a failure (with what is still down), a
  release nobody answered as unconfirmed, a lease lost mid-run as expired —
  none of them as a success;
* ``lease.json`` is written when the lease is taken, so ``job_status`` sees it
  while the model still runs, and ``report-run`` rebuilds the section from it;
* neither ``lease.json`` nor ``energy.json`` is a page: not in the progress
  count, not a failed result in a rebuilt report, not a reading in an export;
* ``gateway_models`` calls an engine down because of a lease *geliehen*, and
  one down without a lease — or whose restore failed — *unerreichbar*.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_batch as batch  # noqa: E402
import config  # noqa: E402
import headroom_lease as hl  # noqa: E402
import run_export  # noqa: E402
from mcp_atr import jobs  # noqa: E402
from mcp_atr import server as mcp_server  # noqa: E402
from run_layout import ENERGY_FILE, LEASE_FILE, page_files  # noqa: E402
from tests.test_ah_472_batch_preflight import MEDIEVAL, XIX  # noqa: E402
from tests.test_ah_473_headroom_lease import Clock, Gateway  # noqa: E402

TAKEN, GIVEN = "2026-11-02T09:14:05+00:00", "2026-11-02T11:52:40+00:00"


class Restoring(Gateway):
    """A gateway whose restore of party fails."""

    def __call__(self, method, path, body):
        if method == "DELETE":
            self.calls.append((method, path, body))
            return 200, {"state": "restoring", "pending": ["atr-party.service"],
                         "last_error": "atr-party.service: Job failed (exit 1)"}
        return super().__call__(method, path, body)


def _client(gw, clock=None):
    stamps = iter([TAKEN, GIVEN])
    return hl.HeadroomClient("http://gw", "k", request=gw, heartbeat_s=0,
                             clock=clock or Clock(), now=lambda: next(stamps))


def _pages(tmp_path, n=2):
    return [batch.PageRef(key=f"p{i}", path=tmp_path / f"p{i}.jpg", doc_id="d")
            for i in range(n)]


def _run(tmp_path, monkeypatch, gw, *, needs=(XIX,), during=None):
    """A batch of MEDIEVAL then XIX whose pass writes one page per model."""
    out = tmp_path / "out"

    def run_model(pages, model, run, out_root, *a, heartbeat=None, **k):
        if during:
            during(model)
        for p in pages:
            if heartbeat:
                heartbeat()
            batch._write_atomic(out_root / model / f"{p.key}.json", json.dumps(
                {"schema": batch.SCHEMA, "text": "Zeile", "model": model, "run": run}))
        return batch.ModelOutcome(model=model, done=len(pages))

    monkeypatch.setattr(batch, "run_model", run_model)
    client = _client(gw) if gw is not None else None
    report = batch.run_batch(_pages(tmp_path), [MEDIEVAL, XIX], "r1", out, lambda *a: None,
                             headroom=client, needs_headroom=list(needs))
    return report, out


# ── report.md ────────────────────────────────────────────────────────────────

def test_a_run_with_a_lease_names_engine_mib_and_span(tmp_path, monkeypatch):
    report, out = _run(tmp_path, monkeypatch, Gateway())
    md = batch.format_report(report)
    assert "## Headroom lease" in md
    section = md.split("## Headroom lease", 1)[1]
    assert f"`{XIX}` on card 1: freed `party` 11,858 MiB (11,858 MiB)" in section
    assert "2026-11-02 09:14 – 11:52 UTC" in section
    assert "zurückgeladen, vom Gateway bestätigt" in section
    assert "lease `L1`" in section
    assert MEDIEVAL not in section, "the model that fitted held no lease"
    record = json.loads((out / XIX / LEASE_FILE).read_text())
    assert record["outcome"] == "restored" and record["freed_mib"] == 11858
    assert (record["acquired_at"], record["released_at"]) == (TAKEN, GIVEN)
    assert not (out / MEDIEVAL / LEASE_FILE).exists()


def test_a_run_without_a_lease_has_no_section(tmp_path, monkeypatch):
    report, out = _run(tmp_path, monkeypatch, None, needs=())
    assert "Headroom lease" not in batch.format_report(report)
    assert not list(out.rglob(LEASE_FILE))


def test_a_model_that_fitted_after_all_has_no_section(tmp_path, monkeypatch):
    """The gateway may answer ``lease_id: null``: nothing was freed, nothing to report."""
    class Fits(Gateway):
        def __call__(self, method, path, body):
            if method == "POST" and path == "/headroom":
                self.calls.append((method, path, body))
                return 200, {"lease_id": None, "card": 1, "freed": [], "state": "fits"}
            return super().__call__(method, path, body)
    report, out = _run(tmp_path, monkeypatch, Fits())
    assert "Headroom lease" not in batch.format_report(report)
    assert not list(out.rglob(LEASE_FILE))


def test_a_failed_restore_is_a_failure_not_a_success(tmp_path, monkeypatch):
    report, _ = _run(tmp_path, monkeypatch, Restoring())
    section = batch.format_report(report).split("## Headroom lease", 1)[1]
    assert "**restoring**: Zurückladen fehlgeschlagen" in section
    assert "still down: atr-party.service — atr-party.service: Job failed (exit 1)" in section
    assert "vom Gateway bestätigt" not in section


def test_a_release_nobody_answered_is_unconfirmed(tmp_path, monkeypatch):
    class Down(Gateway):
        def __call__(self, method, path, body):
            if method == "DELETE":
                raise OSError("network gone")
            return super().__call__(method, path, body)
    report, _ = _run(tmp_path, monkeypatch, Down())
    section = batch.format_report(report).split("## Headroom lease", 1)[1]
    assert "**unconfirmed**" in section and "release failed: network gone" in section
    assert "vom Gateway bestätigt" not in section


def test_a_lease_lost_mid_run_is_expired_not_restored(tmp_path, monkeypatch):
    class Gone(Gateway):
        def __call__(self, method, path, body):
            if method == "DELETE":
                self.calls.append((method, path, body))
                return 410, {"detail": "lease L1 has ended (expired)"}
            return super().__call__(method, path, body)
    report, out = _run(tmp_path, monkeypatch, Gone(gone=True))
    section = batch.format_report(report).split("## Headroom lease", 1)[1]
    assert "**expired**" in section and "lost while reading" in section
    assert "vom Gateway bestätigt" not in section
    assert json.loads((out / XIX / LEASE_FILE).read_text())["outcome"] == "expired"


@pytest.mark.parametrize("restore, lost, released, want", [
    ({"state": "ended"}, "", True, "restored"),
    ({"state": "restoring"}, "", True, "restoring"),
    ({"state": "gone"}, "", True, "expired"),
    ({"state": "unconfirmed"}, "", True, "unconfirmed"),
    ({}, "", False, "held"),
    ({}, "lease L1 has ended", False, "expired"),
])
def test_the_outcome_follows_the_gateways_answer(restore, lost, released, want):
    lease = hl.HeadroomLease("L1", XIX, 1, [], client=None, restore=restore, lost=lost,
                             released=released)
    assert lease.outcome == want


# ── while the run runs, and rebuilt afterwards ───────────────────────────────

def test_job_status_sees_the_lease_while_the_model_runs(tmp_path, monkeypatch):
    seen = {}
    # run_progress reads <VLM_TEST_ROOT>/<run>; the batch writes to out/<model>,
    # so the run directory is "out" under tmp_path.
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path)

    def look(model):
        if model == XIX:
            seen.update(jobs.run_progress("out"))
    _run(tmp_path, monkeypatch, Gateway(), during=look)
    assert seen["leases"][XIX]["outcome"] == "held"
    assert seen["leases"][XIX]["acquired_at"] == TAKEN and not seen["leases"][XIX]["released_at"]
    assert MEDIEVAL not in seen["leases"]
    after = jobs.run_progress("out")
    assert after["leases"][XIX]["outcome"] == "restored"
    assert after["pages_written"] == {MEDIEVAL: 2, XIX: 2}, "lease.json is not a page"


def test_report_run_rebuilds_the_section_and_counts_no_record_as_a_page(tmp_path,
                                                                       monkeypatch):
    _, out = _run(tmp_path, monkeypatch, Restoring())
    (out / XIX / ENERGY_FILE).write_text(json.dumps({"unmeasured": "no meter"}))
    rebuilt = batch.report_from_outputs(out)
    by_model = {m.model: m for m in rebuilt.models}
    assert (by_model[XIX].done, by_model[XIX].failed) == (2, 0), \
        "energy.json and lease.json are records, not unreadable pages"
    assert by_model[XIX].lease["outcome"] == "restoring" and by_model[MEDIEVAL].lease is None
    md = batch.format_report(rebuilt)
    assert "**restoring**" in md and "11,858 MiB" in md


def test_no_record_is_a_page_anywhere(tmp_path, monkeypatch):
    _, out = _run(tmp_path, monkeypatch, Gateway())
    (out / XIX / ENERGY_FILE).write_text(json.dumps({"wh": 1.0}))
    assert [p.name for p in page_files(out / XIX)] == ["p0.json", "p1.json"]
    assert sorted(p.key for p in run_export.read_run(out)) == ["p0", "p0", "p1", "p1"]
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path)
    assert jobs.run_progress("out")["pages_written"][XIX] == 2


# ── gateway_models ───────────────────────────────────────────────────────────

def _probe(state="held", reachable=False):
    return {
        "health": {"engines": [
            {"name": "party", "url": "u", "reachable": reachable},
            {"name": "trocr", "url": "u", "reachable": False},
            {"name": "kraken", "url": "u", "reachable": True},
        ]},
        "leases": {"leases": [{
            "lease_id": "L1", "card": 1, "model": XIX, "holder": "lassberg-xix",
            "freed": [{"engine": "party", "unit": "atr-party.service", "mib": 11858}],
            "expires_at": 1_000_600.0, "state": state,
            "pending": ["atr-party.service"] if state == "restoring" else [],
            "last_error": "atr-party.service: failed" if state == "restoring" else ""}]},
    }


def test_an_engine_down_for_a_lease_is_lent_not_unreachable():
    probe = _probe()
    summary = mcp_server.lent_engines(probe, now=1_000_000.0)
    party, trocr, kraken = probe["health"]["engines"]
    assert party["state"] == "geliehen"
    assert party["lent"]["holder"] == "lassberg-xix" and party["lent"]["remaining_s"] == 600
    assert trocr["state"] == "unerreichbar" and "lent" not in trocr
    assert "state" not in kraken, "a reachable engine needs no word"
    assert summary["open"] == 1 and summary["leases"][0]["freed"] == ["party"]


def test_an_engine_whose_restore_failed_is_unreachable_with_the_reason():
    probe = _probe(state="restoring")
    mcp_server.lent_engines(probe, now=1_000_000.0)
    party = probe["health"]["engines"][0]
    assert party["state"] == "unerreichbar"
    assert party["lent"]["last_error"] == "atr-party.service: failed"


def test_a_gateway_without_headroom_marks_nothing():
    probe = _probe()
    probe["leases"] = {"status": 404, "body": "Not Found"}
    assert mcp_server.lent_engines(probe) == {}
    assert "state" not in probe["health"]["engines"][0]


def test_the_report_carries_the_leases(monkeypatch):
    monkeypatch.setattr(mcp_server, "gateway_probe", lambda: _probe())
    monkeypatch.setattr(mcp_server.atr_gpu, "headroom_report", lambda probe: {})
    report = mcp_server.gateway_report()
    assert report["leases"]["open"] == 1
    assert report["health"]["engines"][0]["state"] == "geliehen"


def test_the_probe_asks_for_the_leases():
    assert ("leases", "/headroom") in mcp_server.GATEWAY_PROBES


def test_a_lease_lost_mid_run_is_on_disk_before_the_model_ends(tmp_path, monkeypatch):
    """``job_status`` must see the loss while the model still reads — the card may
    already be full again, and that is the moment somebody looks."""
    seen = []

    def run_model(pages, model, run, out_root, *a, heartbeat=None, **k):
        heartbeat()
        seen.append(json.loads((out_root / model / LEASE_FILE).read_text()))
        return batch.ModelOutcome(model=model)

    monkeypatch.setattr(batch, "run_model", run_model)
    batch.run_batch(_pages(tmp_path), [XIX], "r1", tmp_path / "out", lambda *a: None,
                    headroom=_client(Gateway(gone=True)), needs_headroom=[XIX])
    assert seen[0]["outcome"] == "expired" and "expired" in seen[0]["lost"]
