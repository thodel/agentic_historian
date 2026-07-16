"""#272: multi-engine ensemble HTR with a disagreement-driven feedback loop.

Offline — engine execution is a mock recognize_fn; model planning uses stubbed
selectors; fusion + CER are the real modules. Run from the repo root:
    pytest agentic_historian/tests/test_ah_272_ensemble.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a import ensemble  # noqa: E402
from agent_a.ensemble import ModelPick, recognize_ensemble, plan_models, resolve_gateway_id  # noqa: E402


# The real ATR gateway registry shape (captured from asterAIx via /models, #277):
# kraken is keyed by Zenodo DOI, TrOCR by HF repo — both differ from the gateway id.
GATEWAY_REGISTRY = [
    {"id": "kraken-early_modern_german", "engine": "kraken",
     "hf_repo": None, "zenodo_id": "10.5281/zenodo.15030337"},
    {"id": "kraken-medieval_14_16", "engine": "kraken",
     "hf_repo": None, "zenodo_id": "10.5281/zenodo.13862096"},
    {"id": "trocr-kurrent-xvi-xvii", "engine": "trocr",
     "hf_repo": "dh-unibe/trocr-kurrent-XVI-XVII", "zenodo_id": None},
    {"id": "trocr-essoins-middle-latin", "engine": "trocr",
     "hf_repo": "dh-unibe/trocr-essoins-middle-latin", "zenodo_id": None},
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _rec_fn(text_map, fail=(), raise_on=()):
    """Mock recognize_fn: pick.model_id → canned text; None for `fail`; raises for
    `raise_on`."""
    def fn(pick, image):
        if pick.model_id in raise_on:
            raise RuntimeError("engine down")
        if pick.model_id in fail:
            return None
        return {"engine": pick.engine, "model_id": pick.model_id,
                "text": text_map.get(pick.model_id, ""), "error": ""}
    return fn


AGREE = "Wir Hans von Wiler tuend kund allen die disen brief ansehent"
# three texts that pairwise disagree a lot (CER well above 0.30)
D1 = "Wir Hans von Wiler tuend kund allen die disen brief ansehent"
D2 = "voellig andere zeichen xyz qrs mno abc def ghi jkl ohne jeden sinn"
D3 = "1234567890 !!! ??? ... zzz yyy xxx www vvv uuu ttt sss rrr qqq ppp"
D4 = "noch eine ganz verschiedene lesart mit voellig anderem inhalt hier"


def _picks(*ids):
    # engine label per id prefix, for readability
    def eng(i):
        return "kraken" if i.startswith("k") else "trocr" if i.startswith("t") else "vlm"
    return [ModelPick(eng(i), i, 1.0) for i in ids]


# ── plan_models: ≥3, engine diversity, ranked tail ───────────────────────────

def test_plan_models_spans_engines_and_interleaves(monkeypatch):
    from agent_a import model_selector as ms

    def fake_kraken(criteria, top_k=3, require_score_above=0.0):
        return [SimpleNamespace(model=SimpleNamespace(model_id=f"k{i}"), score=1.0 - i * 0.1)
                for i in range(top_k)]

    def fake_trocr(criteria, top_k=3, require_score_above=0.0):
        return [SimpleNamespace(model=SimpleNamespace(model_id=f"t{i}"), score=0.9 - i * 0.1)
                for i in range(top_k)]

    monkeypatch.setattr(ms, "select_kraken_model", fake_kraken)
    monkeypatch.setattr(ms, "select_tocr_model", fake_trocr)

    # vlm_model_id pinned so this test covers interleaving only (the real VLM id is
    # covered by test_plan_models_vlm_pick_uses_the_real_vlm_id).
    picks = plan_models("criteria", per_engine=3, vlm_model_id="vlm")
    engines = [p.engine for p in picks]
    ids = [p.model_id for p in picks]
    # first three guarantee diversity: VLM, best kraken, best trocr
    assert engines[:3] == ["vlm", "kraken", "trocr"]
    assert ids[:3] == ["vlm", "k0", "t0"]
    # tail interleaves the next-ranked models for the feedback loop
    assert ids[3:] == ["k1", "t1", "k2", "t2"]


# ── recognize_ensemble: at least min_engines run ─────────────────────────────

def test_runs_at_least_min_engines_and_high_agreement_no_loops():
    fn = _rec_fn({"vlm": AGREE, "k0": AGREE, "t0": AGREE, "k1": AGREE})
    r = recognize_ensemble("img", None, fn, picks=_picks("vlm", "k0", "t0", "k1"),
                            min_engines=3, max_loops=2, agreement_cer=0.30)
    assert len(r.recognitions) == 3          # only the initial batch
    assert r.loops == 0 and r.added == []
    assert r.text.startswith("Wir Hans von Wiler")   # fused consensus


def test_disagreement_triggers_feedback_loops_up_to_max():
    fn = _rec_fn({"vlm": D1, "k0": D2, "t0": D3, "k1": D4, "t1": D2})
    r = recognize_ensemble("img", None, fn,
                            picks=_picks("vlm", "k0", "t0", "k1", "t1"),
                            min_engines=3, max_loops=2, agreement_cer=0.30)
    # first 3 disagree → loop adds up to max_loops more (2), reaching 5 recognitions
    assert r.max_pairwise_cer > 0.30
    assert r.loops == 2
    assert len(r.recognitions) == 5
    assert [p.model_id for p in r.added] == ["k1", "t1"]


def test_agreement_reached_stops_the_loop_early():
    # first 3 disagree, but the 4th makes them... still disagree; instead test that
    # when the initial batch AGREES, no loop runs even with more models available.
    fn = _rec_fn({"vlm": AGREE, "k0": AGREE, "t0": AGREE, "k1": D2, "t1": D3})
    r = recognize_ensemble("img", None, fn,
                            picks=_picks("vlm", "k0", "t0", "k1", "t1"),
                            min_engines=3, max_loops=3, agreement_cer=0.30)
    assert r.loops == 0 and len(r.recognitions) == 3


def test_loop_stops_when_pool_exhausted():
    fn = _rec_fn({"vlm": D1, "k0": D2, "t0": D3})   # only 3 available, all disagree
    r = recognize_ensemble("img", None, fn, picks=_picks("vlm", "k0", "t0"),
                            min_engines=3, max_loops=5, agreement_cer=0.30)
    assert len(r.recognitions) == 3 and r.loops == 0   # nothing left to add


# ── robustness: failures are tolerated + backfilled ──────────────────────────

def test_failed_pick_is_backfilled_to_reach_min_engines():
    # k0 fails (returns None) → the initial phase pulls the next pool entry (k1)
    fn = _rec_fn({"vlm": AGREE, "t0": AGREE, "k1": AGREE}, fail=("k0",))
    r = recognize_ensemble("img", None, fn,
                            picks=_picks("vlm", "k0", "t0", "k1"),
                            min_engines=3, max_loops=2, agreement_cer=0.30)
    ran_ids = [p.model_id for p in r.ran]
    assert len(r.recognitions) == 3 and "k0" not in ran_ids and "k1" in ran_ids


def test_raising_engine_is_skipped_not_fatal():
    fn = _rec_fn({"vlm": AGREE, "t0": AGREE, "k1": AGREE}, raise_on=("k0",))
    r = recognize_ensemble("img", None, fn,
                            picks=_picks("vlm", "k0", "t0", "k1"),
                            min_engines=3, max_loops=2, agreement_cer=0.30)
    assert len(r.recognitions) == 3          # k0 raised, backfilled by k1


def test_empty_pool_returns_empty_result():
    r = recognize_ensemble("img", None, _rec_fn({}), picks=[], min_engines=3)
    assert r.recognitions == [] and r.text == "" and r.loops == 0


# ── #277: local model id → ATR gateway registry id ───────────────────────────

def test_trocr_hf_repo_maps_to_gateway_id():
    """The critical one: the local TrOCR id is an HF repo, which the gateway 404s
    (#21 strict resolver). It must resolve to the gateway's registry id."""
    pick = ModelPick("trocr", "dh-unibe/trocr-kurrent-XVI-XVII", 0.8)
    assert resolve_gateway_id(pick, GATEWAY_REGISTRY) == "trocr-kurrent-xvi-xvii"


def test_kraken_doi_maps_to_gateway_id():
    pick = ModelPick("kraken", "10.5281/zenodo.15030337", 1.0)
    assert resolve_gateway_id(pick, GATEWAY_REGISTRY) == "kraken-early_modern_german"


def test_already_a_gateway_id_passes_through():
    pick = ModelPick("trocr", "trocr-kurrent-xvi-xvii", 0.8)
    assert resolve_gateway_id(pick, GATEWAY_REGISTRY) == "trocr-kurrent-xvi-xvii"


def test_unknown_id_falls_back_to_raw():
    # an unlisted kraken DOI still resolves at the gateway as a raw Zenodo ref (#21)
    pick = ModelPick("kraken", "10.5281/zenodo.9999999", 1.0)
    assert resolve_gateway_id(pick, GATEWAY_REGISTRY) == "10.5281/zenodo.9999999"


def test_empty_registry_falls_back_to_raw():
    pick = ModelPick("trocr", "dh-unibe/trocr-kurrent-XVI-XVII", 0.8)
    assert resolve_gateway_id(pick, []) == "dh-unibe/trocr-kurrent-XVI-XVII"
    assert resolve_gateway_id(pick, None) == "dh-unibe/trocr-kurrent-XVI-XVII"


def test_local_trocr_ids_match_the_gateway_hf_repos():
    """Regression: models.py had 'dh-unibe/trozco-essoins-middle-latin' (typo), which
    matches no gateway entry → 404 even with mapping. Every local TrOCR model_id must
    resolve against the real registry."""
    from agent_a import models
    hf_repos = {m["hf_repo"] for m in GATEWAY_REGISTRY if m["hf_repo"]}
    local = [m.model_id for m in models.HF_MODELS.values()
             if m.model_id.startswith("dh-unibe/trocr-")]
    assert local, "no dh-unibe TrOCR models found locally"
    # the two we have registry fixtures for must map cleanly
    for mid in ("dh-unibe/trocr-kurrent-XVI-XVII", "dh-unibe/trocr-essoins-middle-latin"):
        assert mid in hf_repos
        assert resolve_gateway_id(ModelPick("trocr", mid), GATEWAY_REGISTRY).startswith("trocr-")


def test_plan_models_vlm_pick_uses_the_real_vlm_id(monkeypatch):
    from agent_a import model_selector as ms
    monkeypatch.setattr(ms, "select_kraken_model", lambda *a, **k: [])
    monkeypatch.setattr(ms, "select_tocr_model", lambda *a, **k: [])
    picks = plan_models("criteria")
    assert picks[0].engine == "vlm"
    assert picks[0].model_id != "vlm"          # the real registry id, not a placeholder


# ── wiring: the grouped pipeline uses the ensemble when the flag is on ────────

def test_group_pipeline_uses_ensemble_when_flag_on(tmp_path, monkeypatch):
    import config
    import orchestrator as orch
    from agent_a.ensemble import EnsembleResult

    monkeypatch.setattr(config, "ENABLE_ENSEMBLE_HTR", True)
    monkeypatch.setattr(orch, "DUAL_AVAILABLE", True)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "out")
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(config, "SOURCE_URL_BASE", "")
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)

    from agent_a.model_selector import RecognitionResult
    seen = []

    def fake_page_ensemble(img, criteria):
        seen.append(str(img))
        return EnsembleResult(
            recognitions=[RecognitionResult(engine="kraken", model_id="k0",
                                            text="gut lesbar hier", confidence=0.8)],
            text="gut lesbar hier", max_pairwise_cer=0.1, loops=0,
        )

    monkeypatch.setattr(orch, "_recognize_page_ensemble", fake_page_ensemble)

    import agents.text_recognition as ar
    import agents.source_description as br
    import agents.entity_agent as cr
    monkeypatch.setattr(ar, "save_transcription", lambda *a, **k: None)
    monkeypatch.setattr(br, "describe", lambda **k: {"source_description": "x", "source_json": {}})
    monkeypatch.setattr(cr, "extract_entities", lambda d, t: {})
    # the VLM-only path must NOT be used when the ensemble flag is on
    def _boom(img):
        raise AssertionError("VLM-only transcribe_image used despite ensemble flag")
    monkeypatch.setattr(ar, "transcribe_image", _boom)

    pages = [tmp_path / "p1.jpg", tmp_path / "p2.jpg"]
    for p in pages:
        p.write_bytes(b"\xff\xd8\xff")

    orch.run_full_pipeline_group("order-ens", [str(p) for p in pages])

    assert len(seen) == 2                      # ensemble ran once per page
    from runstate import RunState
    st = RunState.load("order-ens", path=tmp_path / "runs" / "order-ens.json")
    assert st.artifacts.get("recognitions")    # candidates persisted → publishable
    assert st.artifacts.get("a_meta", {}).get("source") == "grouped-ensemble"


# ── #284: breadth (pool depth) + per-candidate, page-attributed exports ──────

def test_per_engine_controls_pool_depth(monkeypatch):
    from agent_a import model_selector as ms
    monkeypatch.setattr(ms, "select_kraken_model",
                        lambda c, top_k=3, **k: [SimpleNamespace(model=SimpleNamespace(model_id=f"k{i}"), score=1.0)
                                                 for i in range(top_k)])
    monkeypatch.setattr(ms, "select_tocr_model",
                        lambda c, top_k=3, **k: [SimpleNamespace(model=SimpleNamespace(model_id=f"t{i}"), score=0.9)
                                                 for i in range(top_k)])
    # pool = 1 VLM + per_engine kraken + per_engine trocr
    assert len(plan_models("c", per_engine=3, vlm_model_id="vlm")) == 7
    assert len(plan_models("c", per_engine=5, vlm_model_id="vlm")) == 11


def test_more_loops_yield_more_transcriptions():
    """Start with 3, keep adding while they disagree — max_loops is the dial."""
    ids = ["vlm", "k0", "t0", "k1", "t1", "k2", "t2"]
    texts = {i: t for i, t in zip(ids, [D1, D2, D3, D4, D2, D3, D1])}  # all disagree
    r = recognize_ensemble("img", None, _rec_fn(texts), picks=_picks(*ids),
                           min_engines=3, max_loops=4, agreement_cer=0.30)
    assert r.loops == 4 and len(r.recognitions) == 7      # 3 initial + 4 added


def test_recognition_export_filename_is_page_attributed():
    from utils.publish_github import _recognition_filename
    r = {"page": "BAT_664_r_00027.jpg", "engine": "trocr", "model_id": "trocr-kurrent-xvi-xvii"}
    assert _recognition_filename(r) == "recognitions/BAT_664_r_00027/trocr-trocr-kurrent-xvi-xvii.txt"


def test_recognition_export_flattens_slashes_in_model_id():
    from utils.publish_github import _recognition_filename
    # a raw Zenodo DOI must not create stray directories
    r = {"page": "p1.jpg", "engine": "kraken", "model_id": "10.5281/zenodo.7516057"}
    assert _recognition_filename(r) == "recognitions/p1/kraken-10.5281_zenodo.7516057.txt"


def test_recognition_export_without_page_still_works():
    from utils.publish_github import _recognition_filename
    assert _recognition_filename({"engine": "vlm", "model_id": "internvl3-8b-instruct"}) \
        == "recognitions/vlm-internvl3-8b-instruct.txt"
