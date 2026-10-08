"""#539: the VLM is chosen from the source criteria, like every other engine.

Three of four engines had a script-, century- and language-aware selector.
The VLM had none: ``plan_models`` emitted one fixed pick with ``score=1.0`` —
a number that is not a match score but the absence of one — and the escalation
tail, which the feedback loop draws from when candidates disagree, held only
kraken and TrOCR. So if the VLM was the outlier, it stayed the outlier and the
ensemble bought agreement among the other two.

**Why criteria and not a static list.** The decision is the scholar's, and the
reason given for it is the one these tests pin hardest: the routing map (#146)
lets a historian correct script, language, century and document type on a page,
and that correction has to reach the VLM choice the way it already reaches
kraken's. A selector that read anything else would be a second classification
system drifting away from the first.

**What is deliberately NOT changed.** The measured baseline — the configured
GPUStack VLM, 27.7 % CER on Inzigkofen (AH-11) — still leads every plan, and no
criteria-selected gateway VLM joins the guaranteed front unless
``ENSEMBLE_VLM_IN_FRONT`` says so. #298 measured fusion voting the good reading
down, and #541's bench has not been run yet, so putting an unmeasured fine-tune
on every page would be exactly the trade #541 exists to prevent. The matches go
into the escalation tail, where they are reached when the candidates disagree
— which is the case where another opinion is worth its cost.

**And the one-letter trap.** The gateway's engine is ``vllm``; this
repository's GPUStack path is ``vlm``. A pick carrying the wrong one of those
reaches the wrong backend and a 400 (#540).
"""

from __future__ import annotations

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))


def _gateway(**kw):
    from agent_a.models import GatewayVLMModel

    return GatewayVLMModel(**kw)


#: Shaped like what refresh_vlm_registry builds from the gateway (#540).
REGISTRY = {
    "qwen3vl-medieval-german-v3": _gateway(
        model_id="qwen3vl-medieval-german-v3", name="medieval german v3",
        level="line", scripts=["Kurrent"], languages=["de"],
        centuries=[15, 16, 17]),
    "qwen3vl-german-xix-v2": _gateway(
        model_id="qwen3vl-german-xix-v2", name="german xix v2", level="line",
        scripts=["Kurrent"], languages=["de"], centuries=[19]),
    "qwen3vl-8b-hebrew": _gateway(
        model_id="qwen3vl-8b-hebrew", name="hebrew", level="page",
        scripts=["Sephardi"], languages=["he"], centuries=[]),
    "lightonocr-catmus-caroline": _gateway(
        model_id="lightonocr-catmus-caroline", name="catmus caroline",
        level="line", scripts=["Caroline minuscule"], languages=["la"],
        centuries=[10, 11, 12]),
}


def _criteria(**kw):
    from agent_a.model_selector import SourceCriteria

    return SourceCriteria(**kw)


class TestTheGatewayEntryScoresWithTheExistingScorer:
    """No second classification system — #539 asked for that explicitly."""

    def test_a_gateway_vlm_exposes_the_fields_score_model_reads(self):
        """``script``/``lang`` mirror what refresh_kraken_registry derives.

        The gateway reports ``scripts`` and ``languages`` as lists for every
        engine; the kraken registry already flattens them the same way. Doing it
        differently here would make a VLM and a kraken model of the same
        gateway row score differently.
        """
        m = REGISTRY["qwen3vl-medieval-german-v3"]
        assert m.script == "Kurrent"
        assert m.lang == "de"

    def test_several_scripts_flatten_the_way_the_kraken_registry_does(self):
        m = _gateway(model_id="x", name="x", scripts=["Kurrent", "Fraktur"],
                     languages=["de", "la"])
        assert m.script == "Kurrent, Fraktur"
        assert m.lang == "de"

    def test_an_entry_with_nothing_declared_still_scores(self):
        m = _gateway(model_id="x", name="x")
        assert m.script == "Latin"
        assert m.lang == "mul"

    def test_score_model_itself_accepts_a_gateway_vlm(self):
        """The reuse, asserted directly: the kraken scorer, unchanged."""
        from agent_a.model_selector import score_model

        match = score_model(REGISTRY["qwen3vl-medieval-german-v3"],
                            script="Kurrent", lang="de", century=16)
        assert match.score > 0.9
        assert "script" in match.matched_on
        assert "lang" in match.matched_on
        assert "century" in match.matched_on


class TestTheSelectorRanksByCriteria:

    def test_the_matching_fine_tune_wins(self, monkeypatch):
        from agent_a import models
        from agent_a.model_selector import select_vlm_model

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        picks = select_vlm_model(
            _criteria(script="Kurrent", lang="de", century=16), top_k=4)

        assert picks[0].model.model_id == "qwen3vl-medieval-german-v3"

    def test_the_century_separates_two_models_of_the_same_hand(self, monkeypatch):
        """Both are Kurrent/German; only the century tells them apart."""
        from agent_a import models
        from agent_a.model_selector import select_vlm_model

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)

        early = select_vlm_model(_criteria(script="Kurrent", lang="de",
                                           century=16), top_k=4)
        late = select_vlm_model(_criteria(script="Kurrent", lang="de",
                                          century=19), top_k=4)

        assert early[0].model.model_id == "qwen3vl-medieval-german-v3"
        assert late[0].model.model_id == "qwen3vl-german-xix-v2"

    def test_a_wrong_script_model_is_demoted_not_merely_unrewarded(
            self, monkeypatch):
        """The kraken scorer's rule, inherited: a mismatch is penalised."""
        from agent_a import models
        from agent_a.model_selector import select_vlm_model

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        picks = select_vlm_model(
            _criteria(script="Kurrent", lang="de", century=16), top_k=4)
        ids = [p.model.model_id for p in picks]

        assert ids.index("qwen3vl-8b-hebrew") > ids.index(
            "qwen3vl-medieval-german-v3")

    def test_top_k_bounds_the_list(self, monkeypatch):
        from agent_a import models
        from agent_a.model_selector import select_vlm_model

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        assert len(select_vlm_model(_criteria(script="Kurrent"), top_k=2)) == 2

    def test_an_empty_registry_selects_nothing(self, monkeypatch):
        """The offline default. Nothing changes until the gateway answers."""
        from agent_a import models
        from agent_a.model_selector import select_vlm_model

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", {})
        assert select_vlm_model(_criteria(script="Kurrent")) == []

    def test_every_match_says_why(self, monkeypatch):
        """A pick a scholar cannot interrogate is not a routing decision."""
        from agent_a import models
        from agent_a.model_selector import select_vlm_model

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        for pick in select_vlm_model(_criteria(script="Kurrent", lang="de",
                                               century=16), top_k=4):
            assert pick.reason, f"{pick.model.model_id} gives no reason"


class TestTheScholarCanMoveTheChoice:
    """The reason this was decided at all: criteria are correctable (#146).

    The routing map lets a historian pin script, language, century and document
    type. These assert that a correction reaches the VLM plan — not just the
    selector in isolation, but the list the orchestrator emits and the scholar
    reads back.
    """

    def _plan(self, monkeypatch, criteria, in_front=0):
        from agent_a import ensemble, models
        import config

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        monkeypatch.setattr(config, "ENSEMBLE_VLM_IN_FRONT", in_front)
        return ensemble.plan_models(criteria, per_engine=3)

    def test_changing_the_century_changes_which_vlm_is_queued(self, monkeypatch):
        early = self._plan(monkeypatch,
                           _criteria(script="Kurrent", lang="de", century=16))
        late = self._plan(monkeypatch,
                          _criteria(script="Kurrent", lang="de", century=19))

        def first_gateway_vlm(picks):
            return next(p.model_id for p in picks if p.engine == "vllm")

        assert first_gateway_vlm(early) == "qwen3vl-medieval-german-v3"
        assert first_gateway_vlm(late) == "qwen3vl-german-xix-v2"

    def test_changing_the_script_changes_it_too(self, monkeypatch):
        kurrent = self._plan(monkeypatch, _criteria(script="Kurrent", lang="de"))
        caroline = self._plan(monkeypatch,
                              _criteria(script="Caroline minuscule", lang="la"))

        def first_gateway_vlm(picks):
            return next(p.model_id for p in picks if p.engine == "vllm")

        assert first_gateway_vlm(kurrent) == "qwen3vl-medieval-german-v3"
        assert first_gateway_vlm(caroline) == "lightonocr-catmus-caroline"

    def test_the_plan_the_orchestrator_emits_carries_the_vlm_and_its_score(
            self, monkeypatch):
        """``_emit_model_select`` is what the scholar reads back."""
        import orchestrator as orch
        from agent_a import models
        import config

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        monkeypatch.setattr(config, "ENSEMBLE_VLM_IN_FRONT", 1)

        emitted = {}

        def fake_emit(on_phase, doc_id, phase, agent, output=None, decision=None):
            emitted["output"] = output or []
            emitted["decision"] = decision or ""

        monkeypatch.setattr(orch, "_emit", fake_emit)
        orch._emit_model_select(
            None, "doc-1",
            _criteria(script="Kurrent", lang="de", century=16), label="phase-3")

        joined = " ".join(emitted["output"])
        assert "vllm/qwen3vl-medieval-german-v3" in joined, (
            "the criteria-selected VLM does not appear in the plan the scholar "
            f"reads: {joined}"
        )
        assert "score=" in joined


class TestTheRoutingCardShowsWhatTheScholarChanged:
    """Influence a scholar cannot see is not influence.

    The card (#146) is where a historian pins script, language, century and
    document type, and it showed exactly one consequence: the kraken model. A
    criteria-driven VLM choice that never appears there would let the scholar
    move it and never learn that they had — which is the half of
    "scholar-in-the-loop" that makes it a loop.
    """

    def _state(self, **criteria):
        from runstate import RunState

        state = RunState(doc_id="doc-1")
        state.criteria = {"script": None, "lang": None, "century": None,
                          "document_type": None, **criteria}
        return state

    def test_the_card_names_the_selected_vlm(self, monkeypatch):
        from agent_a import models
        import routing_card

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        card = routing_card.render_card(
            self._state(script="Kurrent", lang="de", century=16))

        assert "qwen3vl-medieval-german-v3" in card, (
            f"the scholar cannot see which VLM their criteria selected:\n{card}"
        )

    def test_changing_a_criterion_changes_the_line_on_the_card(self, monkeypatch):
        from agent_a import models
        import routing_card

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        medieval = routing_card.render_card(
            self._state(script="Kurrent", lang="de", century=16))
        modern = routing_card.render_card(
            self._state(script="Kurrent", lang="de", century=19))

        assert "qwen3vl-medieval-german-v3" in medieval
        assert "qwen3vl-german-xix-v2" in modern
        assert medieval != modern

    def test_the_card_still_names_the_kraken_model(self, monkeypatch):
        """The VLM line is added beside it, not instead of it."""
        from agent_a import models
        import routing_card

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        card = routing_card.render_card(
            self._state(script="Kurrent", lang="de", century=16))

        assert "HTR-Modell" in card

    def test_no_gateway_means_no_vlm_line_rather_than_an_empty_one(
            self, monkeypatch):
        """Offline the registry is empty; an empty row would be noise."""
        from agent_a import models
        import routing_card

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", {})
        card = routing_card.render_card(
            self._state(script="Kurrent", lang="de", century=16))

        assert "VLM" not in card
        assert "HTR-Modell" in card

    def test_a_broken_registry_does_not_break_the_card(self, monkeypatch):
        """The card is a human's only view of the routing; it must render."""
        import routing_card
        from agent_a import model_selector

        def boom(*a, **kw):
            raise RuntimeError("registry exploded")

        monkeypatch.setattr(model_selector, "select_vlm_model", boom)
        card = routing_card.render_card(self._state(script="Kurrent"))

        assert "doc-1" in card and "HTR-Modell" in card


class TestThePlanKeepsTheMeasuredBaselineInFront:
    """#541 has not run. Nothing unmeasured goes on every page yet."""

    def _plan(self, monkeypatch, in_front=0, criteria=None):
        from agent_a import ensemble, models
        import config

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        monkeypatch.setattr(config, "ENSEMBLE_VLM_IN_FRONT", in_front)
        return ensemble.plan_models(
            criteria or _criteria(script="Kurrent", lang="de", century=16),
            per_engine=3)

    def test_the_configured_gpustack_vlm_still_leads(self, monkeypatch):
        import config

        picks = self._plan(monkeypatch)
        assert picks[0].engine == "vlm"
        assert picks[0].model_id == config.GPUSTACK_MODEL_VISION

    def test_by_default_no_gateway_vlm_is_in_the_guaranteed_front(
            self, monkeypatch):
        """Front = the first three: baseline VLM, best kraken, best TrOCR."""
        picks = self._plan(monkeypatch, in_front=0)
        assert [p.engine for p in picks[:3]] == ["vlm", "kraken", "trocr"]

    def test_the_flag_puts_the_top_match_in_the_front(self, monkeypatch):
        """One config change, once the bench says it is worth it."""
        picks = self._plan(monkeypatch, in_front=1)
        front = [p.engine for p in picks[:4]]
        assert "vllm" in front
        assert picks[0].engine == "vlm", "the measured baseline still leads"

    def test_the_default_is_off(self):
        import config

        assert config.ENSEMBLE_VLM_IN_FRONT == 0, (
            "an unmeasured fine-tune would run on every page; #541's bench has "
            "not been run, and #298 measured fusion voting the good reading down"
        )


class TestTheEscalationTailFinallyReachesAVlm:
    """The defect #539 names: the tail held only kraken and TrOCR."""

    def _tail(self, monkeypatch):
        from agent_a import ensemble, models
        import config

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        monkeypatch.setattr(config, "ENSEMBLE_VLM_IN_FRONT", 0)
        picks = ensemble.plan_models(
            _criteria(script="Kurrent", lang="de", century=16), per_engine=3)
        return picks[3:]

    def test_the_tail_contains_a_vlm(self, monkeypatch):
        tail = self._tail(monkeypatch)
        assert any(p.engine == "vllm" for p in tail), (
            "the escalation tail still draws only from kraken and TrOCR, so a "
            "VLM that is the outlier stays the outlier"
        )

    def test_the_tail_still_contains_the_other_engines(self, monkeypatch):
        tail = self._tail(monkeypatch)
        assert any(p.engine == "kraken" for p in tail)
        assert any(p.engine == "trocr" for p in tail)

    def test_the_best_vlm_match_comes_before_the_worse_ones(self, monkeypatch):
        tail = self._tail(monkeypatch)
        vlms = [p.model_id for p in tail if p.engine == "vllm"]
        assert vlms[0] == "qwen3vl-medieval-german-v3"

    def test_a_vlm_that_matches_nothing_is_not_planned(self, monkeypatch):
        """Observed in the first end-to-end run of this change.

        With Caroline/Latin criteria the Kurrent fine-tune scored 0.00 — the
        script-mismatch penalty, clamped — and was still queued in the tail. It
        would be reached on deep escalation and cost 30–60 s of GPU for a model
        whose own metadata says it does not fit, and #298 measured a bad
        candidate making the *fused* text worse, not just its own row.

        Unlike kraken, excluding it risks nothing: the measured baseline VLM
        always runs, so "no VLM at all" cannot happen. ``select_kraken_model``
        keeps zero-scoring models for exactly the opposite reason — there,
        dropping them could leave the page with no kraken.
        """
        from agent_a import ensemble, models
        import config

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        monkeypatch.setattr(config, "ENSEMBLE_VLM_IN_FRONT", 0)
        picks = ensemble.plan_models(
            _criteria(script="Caroline minuscule", lang="la", century=11),
            per_engine=4)

        vlms = [(p.model_id, p.score) for p in picks
                if p.engine == ensemble.ENGINE_GATEWAY_VLM]
        assert vlms, "the matching Caroline model was dropped too"
        assert vlms[0][0] == "lightonocr-catmus-caroline"
        assert all(score > 0.0 for _mid, score in vlms), (
            f"a VLM scoring 0.00 against these criteria is queued anyway: {vlms}"
        )

    def test_the_baseline_vlm_is_never_dropped_by_that_rule(self, monkeypatch):
        """It carries score 1.0 by construction, but assert it, not assume it."""
        from agent_a import ensemble, models
        import config

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        monkeypatch.setattr(config, "ENSEMBLE_VLM_IN_FRONT", 0)
        picks = ensemble.plan_models(
            _criteria(script="Nothing", lang="xx", century=3), per_engine=3)

        assert picks[0].engine == ensemble.ENGINE_VLM
        assert picks[0].model_id == config.GPUSTACK_MODEL_VISION

    def test_no_model_appears_twice_in_the_plan(self, monkeypatch):
        from agent_a import ensemble, models
        import config

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        monkeypatch.setattr(config, "ENSEMBLE_VLM_IN_FRONT", 1)
        picks = ensemble.plan_models(
            _criteria(script="Kurrent", lang="de", century=16), per_engine=3)

        seen = [(p.engine, p.model_id) for p in picks]
        assert len(seen) == len(set(seen)), (
            f"a model is planned twice, so the ensemble would run it twice "
            f"and fuse it against itself: {seen}"
        )


class TestTheEngineNameIsSettledOnce:
    """One letter decides between two backends. It is named, not spelled."""

    def test_the_two_engine_names_are_constants(self):
        from agent_a import ensemble

        assert ensemble.ENGINE_VLM == "vlm"
        assert ensemble.ENGINE_GATEWAY_VLM == "vllm"

    def test_a_gateway_vlm_pick_carries_the_gateway_engine(self, monkeypatch):
        from agent_a import ensemble, models
        import config

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", REGISTRY)
        monkeypatch.setattr(config, "ENSEMBLE_VLM_IN_FRONT", 1)
        picks = ensemble.plan_models(
            _criteria(script="Kurrent", lang="de", century=16), per_engine=3)

        gateway = [p for p in picks if p.model_id.startswith(("qwen3vl", "lighton"))]
        assert gateway, "no gateway VLM was planned"
        for p in gateway:
            assert p.engine == "vllm", (
                f"{p.model_id} carries engine={p.engine!r}; with 'vlm' it would "
                "go to GPUStack, which does not serve it"
            )

    def test_the_engine_decides_the_endpoint(self):
        from agent_a import kraken_client, ensemble

        assert kraken_client.endpoint_for(ensemble.ENGINE_GATEWAY_VLM) == "/recognize"
        assert ensemble.ENGINE_VLM not in kraken_client.OCR_ENGINES


class TestThePickReachesTheRunner:
    """Until now the baseline pick's id was deliberately not sent (#537).

    The reason was that the registry disagreed with config, so honouring the
    pick would have swapped a 27.7 % model for a 189.8 % one. #538 removed the
    disagreement, so the plan can now be obeyed — which is what makes a
    criteria-driven selection mean anything at all.
    """

    def test_the_pick_is_what_gets_requested(self, monkeypatch, tmp_path):
        """The pick's id must differ from the config default, or this proves
        nothing: with them equal the test passes whether the pick is obeyed or
        silently ignored."""
        import orchestrator as orch
        from agent_a import dual_pipeline as dp, ensemble

        seen = {}
        monkeypatch.setattr(dp.gs, "chat_vision",
                            lambda **kw: seen.update(kw) or "Wir Johans")
        monkeypatch.setattr(dp.config, "GPUSTACK_MODEL_VISION", "the-config-one")
        monkeypatch.setattr(orch, "_gateway_registry", lambda: [])

        captured = {}

        def fake_recognize_ensemble(img, criteria, recognize_fn, **kw):
            captured["fn"] = recognize_fn
            return ensemble.EnsembleResult()

        monkeypatch.setattr(ensemble, "recognize_ensemble", fake_recognize_ensemble)

        image = tmp_path / "p1.jpg"
        image.write_bytes(b"x")
        orch._recognize_page_ensemble(image, _criteria())

        result = captured["fn"](
            ensemble.ModelPick("vlm", "the-planned-one", 1.0), image)

        assert seen.get("model") == "the-planned-one", (
            f"the plan said 'the-planned-one' and {seen.get('model')!r} ran; a "
            "criteria-driven selection the runner ignores is not a selection"
        )
        assert result.model_id == "the-planned-one"

    def test_a_gateway_vlm_pick_goes_to_recognize(self, monkeypatch, tmp_path):
        import orchestrator as orch
        from agent_a import ensemble, kraken_client

        posted = []

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, image, model, engine):
                posted.append((kraken_client.endpoint_for(engine), model))
                return kraken_client.KrakenResult(
                    text="Wir Johans", confidence=0.6, model_used=model,
                    service_version="1")

        monkeypatch.setattr(orch, "_gateway_registry", lambda: [])
        monkeypatch.setattr("agent_a.kraken_client.KrakenHTTPClient",
                            lambda *a, **kw: _Client())

        captured = {}

        def fake_recognize_ensemble(img, criteria, recognize_fn, **kw):
            captured["fn"] = recognize_fn
            return ensemble.EnsembleResult()

        monkeypatch.setattr(ensemble, "recognize_ensemble", fake_recognize_ensemble)

        image = tmp_path / "p1.jpg"
        image.write_bytes(b"x")
        orch._recognize_page_ensemble(image, _criteria())

        result = captured["fn"](
            ensemble.ModelPick("vllm", "qwen3vl-medieval-german-v3", 0.9), image)

        assert posted == [("/recognize", "qwen3vl-medieval-german-v3")]
        assert result.engine == "vllm"


class TestNothingChangesWithoutAGateway:
    """The offline and gateway-down case must behave exactly as before."""

    def test_an_empty_registry_leaves_the_plan_as_it_was(self, monkeypatch):
        from agent_a import ensemble, models
        import config

        monkeypatch.setattr(models, "VLM_GATEWAY_MODELS_LIVE", {})
        monkeypatch.setattr(config, "ENSEMBLE_VLM_IN_FRONT", 1)
        picks = ensemble.plan_models(
            _criteria(script="Kurrent", lang="de", century=16), per_engine=3)

        assert [p.engine for p in picks[:3]] == ["vlm", "kraken", "trocr"]
        assert not any(p.engine == "vllm" for p in picks)

    def test_a_selector_failure_does_not_lose_the_plan(self, monkeypatch):
        """A broken VLM registry must not cost kraken and TrOCR their run."""
        from agent_a import ensemble
        from agent_a import model_selector

        def boom(*a, **kw):
            raise RuntimeError("registry exploded")

        monkeypatch.setattr(model_selector, "select_vlm_model", boom)
        picks = ensemble.plan_models(_criteria(script="Kurrent"), per_engine=3)

        assert picks[0].engine == "vlm"
        assert any(p.engine == "kraken" for p in picks)
