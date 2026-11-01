"""#473 — the batch takes the card it needs under a lease the gateway holds.

The gateway side (serving-atr-inference, ``POST /headroom``) frees and restores;
these pin the client:

* at the preflight, before the share is walked, a model that does not fit is
  asked about with a dry run: one the gateway can make room for runs, one it
  cannot is dropped then — not after 24 minutes of listing (#472);
* the lease is taken right before the model's pass and released after it, also
  when the pass raises; the other models run without one;
* the heartbeat comes from the page loop and throttles itself; a lease the
  gateway no longer knows is noted and never stops the reading;
* a model the gateway refuses at its turn is abandoned with the reason and the
  others still run;
* ``--no-headroom`` leaves the card alone: the model is dropped as before.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_batch as batch  # noqa: E402
import headroom_lease as hl  # noqa: E402
from tests.test_ah_472_batch_preflight import (  # noqa: E402
    MEDIEVAL, XIX, entry, roomy, too_small, unlisted)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Gateway:
    """``(method, path, body) -> (status, payload)``, recording every call."""

    def __init__(self, *, refuse=False, gone=False):
        self.calls, self.refuse, self.gone = [], refuse, gone

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if method == "POST" and path == "/headroom":
            if self.refuse:
                return 409, {"detail": {"reason": "6106 MiB short. Nothing was unloaded.",
                                        "shortfall_mib": 6106}}
            freed = [{"engine": "party", "unit": "atr-party.service", "mib": 11858}]
            if body.get("dry_run"):
                return 200, {"lease_id": None, "card": 1, "freed": freed, "state": "would-free"}
            return 200, {"lease_id": "L1", "card": 1, "freed": freed, "state": "held"}
        if path.endswith("/renew"):
            return (410, {"detail": "lease L1 has ended (expired)"}) if self.gone else (200, {})
        if method == "DELETE":
            return 200, {"state": "ended"}
        return 500, {}


def _client(gw, clock=None, heartbeat_s=300):
    return hl.HeadroomClient("http://gw", "k", request=gw, heartbeat_s=heartbeat_s,
                             clock=clock or Clock())


# ── the client ───────────────────────────────────────────────────────────────

def test_a_lease_is_granted_and_says_what_was_freed():
    lease = _client(Gateway()).acquire(XIX, holder="r1")
    assert lease.lease_id == "L1" and "party 11,858 MiB" in lease.line()


def test_a_refusal_carries_the_gateways_reason():
    with pytest.raises(hl.HeadroomRefused, match="6106 MiB short"):
        _client(Gateway(refuse=True)).acquire(XIX)


def test_a_gateway_without_headroom_is_named():
    with pytest.raises(hl.HeadroomRefused, match="no /headroom"):
        _client(lambda *a: (404, {"detail": "Not Found"})).check(XIX)


def test_an_unreachable_gateway_is_a_no_at_the_preflight():
    def down(*a):
        raise OSError("connection refused")
    with pytest.raises(hl.HeadroomRefused, match="could not be asked"):
        _client(down).check(XIX)


def test_the_heartbeat_throttles_itself():
    gw, clock = Gateway(), Clock()
    lease = _client(gw, clock).acquire(XIX)
    for t in (10, 100, 299):
        clock.t = t
        lease.beat()
    assert not [c for c in gw.calls if c[1].endswith("/renew")]
    clock.t = 301
    lease.beat()
    lease.beat()
    assert len([c for c in gw.calls if c[1].endswith("/renew")]) == 1


def test_a_lost_lease_is_noted_and_never_raises():
    gw, clock = Gateway(gone=True), Clock()
    lease = _client(gw, clock).acquire(XIX)
    clock.t = 400
    lease.beat()
    assert "expired" in lease.lost
    clock.t = 800
    lease.beat()                                  # no second renew of a lost lease
    assert len([c for c in gw.calls if c[1].endswith("/renew")]) == 1


def test_a_failing_release_does_not_raise():
    def broken(method, path, body):
        if method == "DELETE":
            raise OSError("network gone")
        return Gateway()(method, path, body)
    lease = _client(broken).acquire(XIX)
    lease.release()
    assert lease.released


# ── the batch ────────────────────────────────────────────────────────────────

def _pages(tmp_path, n=3):
    return [batch.PageRef(key=f"p{i}", path=tmp_path / f"p{i}.jpg", doc_id="d")
            for i in range(n)]


def test_the_lease_wraps_exactly_the_models_pass(tmp_path, monkeypatch):
    gw, clock = Gateway(), Clock()
    client = _client(gw, clock, heartbeat_s=0)
    seen = []

    def run_model(pages, model, *a, heartbeat=None, **k):
        seen.append((model, heartbeat is not None))
        if heartbeat:
            for _ in pages:
                clock.t += 1
                heartbeat()
        return batch.ModelOutcome(model=model)

    monkeypatch.setattr(batch, "run_model", run_model)
    batch.run_batch(_pages(tmp_path), [MEDIEVAL, XIX], "r1", tmp_path, lambda *a: None,
                    headroom=client, needs_headroom=[XIX])
    assert seen == [(MEDIEVAL, False), (XIX, True)]
    verbs = [(m, p) for m, p, _ in gw.calls]
    assert verbs[0] == ("POST", "/headroom") and verbs[-1] == ("DELETE", "/headroom/L1")
    assert verbs.count(("POST", "/headroom/L1/renew")) == 3, "one beat per page"


def test_the_lease_is_released_when_the_pass_raises(tmp_path, monkeypatch):
    gw = Gateway()

    def boom(*a, **k):
        raise RuntimeError("the share went away")
    monkeypatch.setattr(batch, "run_model", boom)
    with pytest.raises(RuntimeError):
        batch.run_batch(_pages(tmp_path), [XIX], "r1", tmp_path, lambda *a: None,
                        headroom=_client(gw), needs_headroom=[XIX])
    assert gw.calls[-1][:2] == ("DELETE", "/headroom/L1")


def test_a_refusal_at_its_turn_abandons_that_model_only(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(batch, "run_model",
                        lambda pages, model, *a, **k: ran.append(model) or batch.ModelOutcome(model))
    report = batch.run_batch(_pages(tmp_path), [XIX, MEDIEVAL], "r1", tmp_path,
                             lambda *a: None, headroom=_client(Gateway(refuse=True)),
                             needs_headroom=[XIX])
    assert ran == [MEDIEVAL]
    assert report.models[0].aborted.startswith("no headroom: 6106 MiB short")


def test_the_page_loop_beats(tmp_path):
    beats = []
    pages = _pages(tmp_path, 4)
    for p in pages:
        p.path.write_bytes(b"\xff\xd8\xff")
    batch.run_model(pages, XIX, "r1", tmp_path, lambda path, model: SimpleNamespace(
        text="Zeile", lines=["Zeile"], truncated=False, recognition_ms=1, raw={}),
        heartbeat=lambda: beats.append(1), retries=0, concurrency=1)
    assert len(beats) == 4


# ── the preflight and the CLI ────────────────────────────────────────────────

def test_only_a_model_that_exists_and_does_not_fit_is_leasable():
    pf = batch.Preflight([batch.ModelVerdict(m.model, m) for m in
                          (too_small(), roomy(), unlisted())])
    assert pf.leasable == [XIX]


@pytest.fixture
def cli(monkeypatch, tmp_path):
    submitted = {}

    def record(pages, models, *a, **k):
        submitted.update(models=list(models), needs=list(k.get("needs_headroom") or []),
                         headroom=k.get("headroom"))
        return batch.BatchReport(run="r1", out_root=tmp_path, pages=len(pages))

    from utils import nextcloud
    monkeypatch.setattr(nextcloud, "is_remote_source", lambda s: True)
    monkeypatch.setattr(nextcloud, "remote_source_root", lambda s: "Lassberg")

    class Share:
        def __init__(self, *a, **k):
            self.hits = self.misses = self.source_bytes = 0

        def list_pages(self, *a, **k):
            submitted["walked"] = True
            return ["Lassberg/doc/0001.jpg"]

    monkeypatch.setattr(nextcloud, "WebdavPageSource", Share)
    monkeypatch.setattr(batch, "run_batch", record)
    monkeypatch.setattr(batch, "gateway_recogniser", lambda *a, **k: object())
    monkeypatch.setattr(batch, "write_report", lambda report: tmp_path / "report.md")
    monkeypatch.setattr(batch, "format_report", lambda report: "")

    def invoke(headrooms, gateway, **over):
        by_model = {h.model: h for h in headrooms}
        monkeypatch.setattr(batch, "preflight", lambda models, *a, **k: batch.Preflight(
            [batch.ModelVerdict(model=m, headroom=by_model[m]) for m in models]))
        real = hl.HeadroomClient
        monkeypatch.setattr(hl, "HeadroomClient",
                            lambda *a, **k: real("http://gw", "k", request=gateway))
        args = SimpleNamespace(
            source="dav:Lassberg", models=",".join(by_model), run="r1",
            out_root=str(tmp_path / "out"), limit=None, sample=None, seed=1,
            concurrency=1, retries=0, cache_dir=str(tmp_path / "cache"),
            no_listing_cache=False, dry_run=False, no_preflight=False, no_headroom=False)
        for key, value in over.items():
            setattr(args, key, value)
        return entry().atr_batch(args), submitted

    return invoke


def test_a_model_the_gateway_can_make_room_for_runs_under_a_lease(cli, capsys):
    gw = Gateway()
    code, sub = cli([too_small(), roomy()], gw)
    assert code == 0 and sub["models"] == [XIX, MEDIEVAL] and sub["needs"] == [XIX]
    assert gw.calls == [("POST", "/headroom", {"model": XIX, "dry_run": True})], \
        "only a dry run at the preflight — the lease comes at the model's turn"
    assert "would free party 11858 MiB" in capsys.readouterr().out


def test_a_model_the_gateway_cannot_make_room_for_never_reaches_the_share(cli, capsys):
    code, sub = cli([too_small()], Gateway(refuse=True))
    assert code == 1 and "walked" not in sub
    assert "no headroom either — 6106 MiB short" in capsys.readouterr().err


def test_no_headroom_leaves_the_card_alone(cli):
    gw = Gateway()
    code, sub = cli([too_small(), roomy()], gw, no_headroom=True)
    assert sub["models"] == [MEDIEVAL] and sub["needs"] == [] and gw.calls == []


def test_the_flag_exists_and_defaults_to_asking():
    parse = entry().build_parser().parse_args
    assert parse(["atr-batch", "--source", "x", "--models", "m", "--no-headroom"]).no_headroom
    assert parse(["atr-batch", "--source", "x", "--models", "m"]).no_headroom is False
