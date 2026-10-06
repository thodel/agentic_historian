"""#537: a VLM reading must name the model that produced it.

``_run_vlm`` took a ``model`` argument, resolved it to ``get_primary_vlm()``
when absent, and then never passed it on — ``chat_vision`` knew only
``config.GPUSTACK_MODEL_VISION``. The local variable was dead after assignment.

That alone would be a dead parameter. What made it a defect is that both
production call sites stamped the record from the registry rather than from the
call:

    # orchestrator._recognize_page_ensemble
    text, score = _run_vlm(p)                       # qwen3.8-27b ran
    return RecognitionResult(engine="vlm", model_id=pick.model_id, ...)
                                        #  internvl3-8b-instruct was written

``model_id`` reaches ``pipeline.json``, ``catalogue.json`` and the published
page. The two names are not aliases: measured on the Inzigkofen ground truth
(AH-11, 07.09.2026) ``qwen3.8-27b`` reads at 27.7 % CER and ``internvl3-8b`` at
189.8 % — a collapse. The record named the collapse and the other one ran.

The shape of the fix is what these tests pin: ``_run_vlm`` returns the model it
asked for, and the callers write *that* rather than choosing a name of their
own. While a caller picks the name independently, it can be wrong again.

What is **not** claimed here: that the served model is the requested one.
``chat()`` returns only the text, so the gateway's own ``response.model`` never
reaches us. These tests pin planned-vs-requested, which is where the 162-point
gap was; requested-vs-served is a separate, currently unevidenced question.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))


def _capture(monkeypatch, dp, text="Wir Johans von Habspurg tuon kunt allen den"):
    """Replace the vision call and record which model it was asked for."""
    seen = {}

    def fake_chat_vision(**kwargs):
        seen.update(kwargs)
        return text

    monkeypatch.setattr(dp.gs, "chat_vision", fake_chat_vision)
    return seen


def _image(tmp_path):
    image = tmp_path / "page.jpg"
    image.write_bytes(b"not-read-by-the-mock")
    return image


class TestTheCallCarriesTheModel:

    def test_the_default_model_reaches_the_vision_call(self, monkeypatch, tmp_path):
        """The whole bug in one case: nothing named a model on the wire."""
        from agent_a import dual_pipeline as dp

        seen = _capture(monkeypatch, dp)
        monkeypatch.setattr(dp.config, "GPUSTACK_MODEL_VISION", "qwen3.8-27b")

        dp._run_vlm(_image(tmp_path))

        assert seen.get("model") == "qwen3.8-27b", (
            "the vision call names no model, so chat_vision falls back to its "
            "own default and the caller cannot know what ran"
        )

    def test_an_explicit_model_is_honoured(self, monkeypatch, tmp_path):
        """The argument has to do something. It did not."""
        from agent_a import dual_pipeline as dp
        from agent_a.models import VLMModel

        seen = _capture(monkeypatch, dp)
        other = VLMModel(name="Other", endpoint="https://example.invalid/v1",
                         model_id="qwen3vl-medieval-german-v3", api_key_env="K")

        dp._run_vlm(_image(tmp_path), model=other)

        assert seen.get("model") == "qwen3vl-medieval-german-v3", (
            "_run_vlm was asked for a specific model and ran another one"
        )

    def test_a_plain_string_model_is_accepted(self, monkeypatch, tmp_path):
        """Callers hold ids, not registry objects — ModelPick carries a str."""
        from agent_a import dual_pipeline as dp

        seen = _capture(monkeypatch, dp)
        dp._run_vlm(_image(tmp_path), model="some-vlm-id")

        assert seen.get("model") == "some-vlm-id"

    def test_the_default_is_the_configured_model_not_the_registry(
            self, monkeypatch, tmp_path):
        """Deliberate: the registry still names the model measured at 189.8 %.

        Defaulting to ``get_primary_vlm()`` would make the false record true by
        running the collapse. The configured model is what runs today and the
        better of the two; #538 is where the registry stops disagreeing.
        """
        from agent_a import dual_pipeline as dp

        seen = _capture(monkeypatch, dp)
        monkeypatch.setattr(dp.config, "GPUSTACK_MODEL_VISION", "configured-vlm")

        dp._run_vlm(_image(tmp_path))

        assert seen.get("model") == "configured-vlm"


class TestTheReadingReportsItsModel:

    def test_run_vlm_returns_the_model_it_asked_for(self, monkeypatch, tmp_path):
        from agent_a import dual_pipeline as dp

        _capture(monkeypatch, dp)
        monkeypatch.setattr(dp.config, "GPUSTACK_MODEL_VISION", "qwen3.8-27b")

        text, score, model_id = dp._run_vlm(_image(tmp_path))

        assert text.startswith("Wir Johans")
        assert score > 0.0
        assert model_id == "qwen3.8-27b", (
            "the caller has to learn the model from the call, not guess it"
        )

    def test_a_failed_call_still_names_what_was_attempted(
            self, monkeypatch, tmp_path):
        """A failure record with no model is as unciteable as a wrong one."""
        from agent_a import dual_pipeline as dp

        def boom(**kwargs):
            raise RuntimeError("gateway unreachable")

        monkeypatch.setattr(dp.gs, "chat_vision", boom)
        monkeypatch.setattr(dp.config, "GPUSTACK_MODEL_VISION", "qwen3.8-27b")

        text, score, model_id = dp._run_vlm(_image(tmp_path))

        assert (text, score) == ("", 0.0)
        assert model_id == "qwen3.8-27b"


class TestBothCallSitesWriteWhatRan:
    """The two places a VLM reading becomes a published record."""

    def test_the_ensemble_record_names_the_model_that_ran(
            self, monkeypatch, tmp_path):
        """orchestrator._recognize_page_ensemble stamped pick.model_id.

        The pick's id comes from ``ensemble._default_vlm_model_id()`` — the
        registry — while the run goes to the configured model. This is the case
        that put ``internvl3-8b-instruct`` on readings produced by
        ``qwen3.8-27b``.
        """
        import orchestrator as orch
        from agent_a import dual_pipeline as dp
        from agent_a import ensemble

        _capture(monkeypatch, dp)
        monkeypatch.setattr(dp.config, "GPUSTACK_MODEL_VISION", "qwen3.8-27b")
        monkeypatch.setattr(orch, "_gateway_registry", lambda: [])

        recognize = orch._recognize_fn_for_tests() if hasattr(
            orch, "_recognize_fn_for_tests") else None
        if recognize is None:                      # exercise the real wiring
            captured = {}

            def fake_recognize_ensemble(img, criteria, recognize_fn, **kw):
                captured["fn"] = recognize_fn
                return ensemble.EnsembleResult()

            monkeypatch.setattr(ensemble, "recognize_ensemble",
                                fake_recognize_ensemble)
            orch._recognize_page_ensemble(_image(tmp_path), object())
            recognize = captured["fn"]

        pick = ensemble.ModelPick("vlm", "internvl3-8b-instruct", 1.0)
        result = recognize(pick, _image(tmp_path))

        assert result.model_id == "qwen3.8-27b", (
            f"the reading is recorded as {result.model_id!r} but "
            "qwen3.8-27b produced it"
        )

    def test_the_dual_pipeline_record_names_the_model_that_ran(
            self, monkeypatch, tmp_path):
        """dual_pipeline._make_run_vlm read the registry for the same field."""
        from agent_a import dual_pipeline as dp

        _capture(monkeypatch, dp)
        monkeypatch.setattr(dp.config, "GPUSTACK_MODEL_VISION", "qwen3.8-27b")
        monkeypatch.setattr(dp, "_kraken_available", lambda: False)
        monkeypatch.setattr(dp, "_party_available", lambda: False)

        result = dp.transcribe_dual(
            _image(tmp_path), run_kraken=False, run_party=False,
            use_llm_reconcile=False)

        vlm = [r for r in result.recognitions if r.engine == "vlm"]
        assert vlm, "no VLM recognition was produced"
        assert vlm[0].model_id == "qwen3.8-27b", (
            f"the record says {vlm[0].model_id!r}; qwen3.8-27b ran"
        )
