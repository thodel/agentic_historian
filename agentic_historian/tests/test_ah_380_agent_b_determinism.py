"""#380 (B1/B2/B3): the same manuscript must be described the same way.

Three runs of saa-0428 over identical images produced "Kursivschrift (Fraktur)",
then `fraktur`, then "Gothische Textura mit Rubrizierung" — three different script
FAMILIES, three different winning model pools, and QA flat at 0.36/0.41/0.40
(#379). Every downstream A/B was measuring the describer's variance rather than
the change under test.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_380_agent_b_determinism.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config             # noqa: E402
import description_cache as dc   # noqa: E402
import orchestrator       # noqa: E402
from agents import source_description as sd   # noqa: E402


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "AGENT_B_CACHE", True)
    return tmp_path


# ── B1: sampling is pinned ───────────────────────────────────────────────────

def test_the_default_is_deterministic():
    assert config.AGENT_B_TEMPERATURE == 0.0


def test_every_description_call_carries_the_sampling_params(monkeypatch):
    """One shared dict, so a new call site cannot quietly omit it.

    A real reading, not a stub: a degenerate transcription takes the #276 refusal
    and spends no LLM call at all, so a short placeholder would make this test pass
    for the wrong reason.
    """
    seen = []
    monkeypatch.setattr(sd.gs, "chat_vision",
                        lambda *a, **k: seen.append(k) or "## Beschreibung\ntext")
    monkeypatch.setattr(sd.gs, "chat_text",
                        lambda *a, **k: seen.append(k) or "## Beschreibung\ntext")
    sd.describe(doc_id="d", transcription="unser fründtlich grus vor liebe getrüwe von der stösse wegen so da sint zwüschent Henin Rost und Cunraten nefen darumb nu der selben vast", image_path=None)
    assert seen, "no LLM call was made"
    assert all(k.get("temperature") == 0.0 for k in seen), seen


def test_a_seed_is_sent_only_when_configured(monkeypatch):
    """Backends may ignore a seed; temperature 0 does the real work, so the seed
    must not be fabricated when unset."""
    monkeypatch.setattr(config, "AGENT_B_SEED", None)
    assert "seed" not in sd._sampling()
    monkeypatch.setattr(config, "AGENT_B_SEED", 42)
    assert sd._sampling()["seed"] == 42


def test_the_params_are_recorded_in_the_description(monkeypatch):
    """A published document should state how its description was produced."""
    monkeypatch.setattr(sd.gs, "chat_text", lambda *a, **k: "## Beschreibung\ntext")
    out = sd.describe(doc_id="d", transcription="unser fründtlich grus vor liebe getrüwe von der stösse wegen so da sint zwüschent Henin Rost und Cunraten nefen darumb nu der selben vast", image_path=None)
    assert out.get("sampling", {}).get("temperature") == 0.0


# ── B2: the cache keys on content ────────────────────────────────────────────

def _page(tmp_path, name, data=b"\x01\x02\x03"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_the_same_bytes_are_described_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(orchestrator.agent_b, "describe",
                        lambda **k: calls.append(k) or {"source_description": "x",
                                                        "source_json": {}})
    page = _page(tmp_path, "p.jpg")
    for _ in range(2):
        orchestrator._describe_cached("d", "text", [page], str(page))
    assert len(calls) == 1, "the second run re-described identical pages"


def test_different_bytes_are_described_again(tmp_path, monkeypatch):
    """The cache must key on CONTENT — reusing across sources would be a bug."""
    calls = []
    monkeypatch.setattr(orchestrator.agent_b, "describe",
                        lambda **k: calls.append(k) or {"source_description": "x",
                                                        "source_json": {}})
    orchestrator._describe_cached("d", "t", [_page(tmp_path, "a.jpg", b"\x01")], None)
    orchestrator._describe_cached("d", "t", [_page(tmp_path, "b.jpg", b"\x02")], None)
    assert len(calls) == 2


def test_page_order_does_not_change_the_key(tmp_path):
    a, b = _page(tmp_path, "a.jpg", b"\x01"), _page(tmp_path, "b.jpg", b"\x02")
    assert dc.content_key([a, b]) == dc.content_key([b, a])


def test_an_unreadable_page_misses_rather_than_colliding(tmp_path):
    """An unkeyable input must not share a key with another document."""
    assert dc.content_key([tmp_path / "missing.jpg"]) == ""
    assert dc.load("") is None


def test_invalidation_forces_a_fresh_description(tmp_path, monkeypatch):
    """A cached description is a cached MISTAKE too — the way out must work."""
    calls = []
    monkeypatch.setattr(orchestrator.agent_b, "describe",
                        lambda **k: calls.append(k) or {"source_description": "x",
                                                        "source_json": {}})
    page = _page(tmp_path, "p.jpg")
    orchestrator._describe_cached("d", "t", [page], None)
    assert dc.invalidate(dc.content_key([page])) is True
    orchestrator._describe_cached("d", "t", [page], None)
    assert len(calls) == 2


def test_the_flag_disables_the_cache(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(config, "AGENT_B_CACHE", False)
    monkeypatch.setattr(orchestrator.agent_b, "describe",
                        lambda **k: calls.append(k) or {"source_description": "x",
                                                        "source_json": {}})
    page = _page(tmp_path, "p.jpg")
    for _ in range(2):
        orchestrator._describe_cached("d", "t", [page], None)
    assert len(calls) == 2


def test_a_cache_write_failure_does_not_fail_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(dc, "store", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    monkeypatch.setattr(orchestrator.agent_b, "describe",
                        lambda **k: {"source_description": "x", "source_json": {}})
    with pytest.raises(OSError):
        dc.store("k", {"a": 1})            # the stub raises …
    # … and the real one swallows it
    monkeypatch.undo()
    monkeypatch.setattr(config, "DATA_DIR", Path("/nonexistent-root-xyz"))
    dc.store("k", {"a": 1})                # must not raise


# ── B3: a Gate-1 correction outranks a re-description ────────────────────────

def _criteria(**kw):
    from agent_a.model_selector import SourceCriteria
    return SourceCriteria(**kw)


def test_a_pinned_field_overrides_the_fresh_description(tmp_path):
    from runstate import RunState
    st = RunState(doc_id="d-pin")
    st.invalidate("script", value="kurrent", user="anna")
    st.save()

    ctx = SimpleNamespace(description={})
    out = orchestrator._apply_gate1_pins(
        _criteria(script="textura", century=15, lang="de"), "d-pin", ctx)
    assert out.script == "kurrent"


def test_the_superseded_value_is_kept_as_provenance(tmp_path):
    """It says something about the describer's reliability, which #380 wants to
    measure — dropping it would discard the evidence."""
    from runstate import RunState
    st = RunState(doc_id="d-prov")
    st.invalidate("script", value="kurrent", user="anna")
    st.save()

    ctx = SimpleNamespace(description={})
    orchestrator._apply_gate1_pins(_criteria(script="textura"), "d-prov", ctx)
    assert ctx.description["superseded_by_gate1"]["script"] == "textura"


def test_an_unpinned_field_is_left_to_agent_b(tmp_path):
    from runstate import RunState
    st = RunState(doc_id="d-partial")
    st.invalidate("script", value="kurrent", user="anna")
    st.save()

    ctx = SimpleNamespace(description={})
    out = orchestrator._apply_gate1_pins(
        _criteria(script="textura", century=15), "d-partial", ctx)
    assert out.century == 15


def test_a_pinned_language_leads_the_language_set(tmp_path):
    """#378 ranks by langs[0]; a pin that did not reorder the set would be
    overridden by the very next scoring step."""
    from runstate import RunState
    st = RunState(doc_id="d-lang")
    st.invalidate("lang", value="la", user="anna")
    st.save()

    ctx = SimpleNamespace(description={})
    out = orchestrator._apply_gate1_pins(
        _criteria(lang="de", langs=["de", "la"]), "d-lang", ctx)
    assert out.lang == "la" and out.langs[0] == "la" and "de" in out.langs


def test_no_pins_leaves_the_criteria_untouched(tmp_path):
    ctx = SimpleNamespace(description={})
    c = _criteria(script="textura", century=15, lang="de")
    assert orchestrator._apply_gate1_pins(c, "d-none", ctx) is c


def test_a_broken_runstate_never_costs_the_run(monkeypatch):
    monkeypatch.setattr("runstate.RunState.load_or_new",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    c = _criteria(script="textura")
    assert orchestrator._apply_gate1_pins(c, "d", SimpleNamespace(description={})) is c


# ── the wiring, not just the helpers ─────────────────────────────────────────

def test_pass_two_actually_applies_the_gate1_pins(tmp_path, monkeypatch):
    """A revert probe removed the `_apply_gate1_pins` call from _criteria_rerun and
    every B3 test above still passed — they exercise the helper, never its use. Same
    gap that #375's page-language tests had."""
    from runstate import RunState
    monkeypatch.setattr(orchestrator.agent_a, "save_transcription", lambda *a, **k: None)

    st = RunState(doc_id="d-wire-pin")
    st.invalidate("script", value="kurrent", user="anna")
    st.save()

    seen = []
    monkeypatch.setattr(orchestrator, "_recognize_page_ensemble",
                        lambda img, crit: seen.append(getattr(crit, "script", None))
                        or SimpleNamespace(recognitions=[], text="t", loops=0,
                                           max_pairwise_cer=0.0, usable=2,
                                           no_merge=False))
    page = tmp_path / "p.jpg"
    page.write_bytes(b"\x00")
    ctx = SimpleNamespace(
        transcription="x", a_meta={"qa_score": 0.5}, errors=[], recognitions=[],
        description={"source_description": "Gothische Textura, deutsch",
                     "source_json": {"Schrift": {"wert": "Gothische Textura"},
                                     "Sprache": {"wert": "Deutsch"},
                                     "Datierung": {"wert": "15. Jahrhundert"}}})

    orchestrator._criteria_rerun([page], ctx, "d-wire-pin", None,
                                 avg_qa=0.5, source_tag="t")
    assert seen and seen[0] == "kurrent", (
        f"pass 2 ignored the Gate-1 pin and used Agent B's value: {seen}")


def test_the_pipeline_routes_descriptions_through_the_cache():
    """Guards the call site: both describe() calls must go through _describe_cached,
    or one pipeline silently keeps re-describing."""
    src = Path(orchestrator.__file__).read_text(encoding="utf-8")
    assert src.count("_describe_cached(") >= 3          # def + both call sites
    assert "agent_b.describe(\n                doc_id=doc_id," not in src
