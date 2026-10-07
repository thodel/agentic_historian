"""#538: there may not be two answers to "which VLM is the primary one".

``VLM_MODELS`` was a hand-maintained dict with one entry, and
``get_primary_vlm()`` returned "the first available" — which could only ever be
that one. On 08.09.2026 (``f197be4``) ``config.GPUSTACK_MODEL_VISION`` moved
from ``internvl3-8b-instruct`` to ``qwen3.8-27b``, because the measurement the
day before put internvl3-8b at 189.8 % CER against qwen3.8-27b's 27.7 % on the
Inzigkofen set (AH-11). The registry was not moved with it and went on calling
the collapse *"Primary VLM. Strong on historical handwriting."*

Two truths in one repository, and #537 is what happened when both were read:
the run went to config, the record was stamped from the registry.

The fix is structural rather than a corrected value. ``get_primary_vlm()``
*reads* the configured id, so it cannot contradict it — a second hand-edit can
no longer create a second answer. The registry keeps what it is good for,
describing models, and loses the one thing it was wrong about: deciding which
one runs.

``KRAKEN_MODELS`` carries a comment about exactly this failure mode, one layer
down: 28 of its 43 entries described a different model than their DOI loaded,
and the gateway registry was ported from here. A hand-maintained table that
names something authoritative drifts. This is the same lesson applied to the
VLM row.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))


class TestThePrimaryVlmIsTheConfiguredOne:

    def test_the_registry_cannot_disagree_with_config(self):
        """The drift itself, as it stands today."""
        import config
        from agent_a import models

        assert models.get_primary_vlm().model_id == config.GPUSTACK_MODEL_VISION, (
            "the registry names a different primary VLM than the one the "
            "pipeline actually calls; this is the disagreement that put the "
            "wrong model name on every reading (#537)"
        )

    def test_it_follows_config_wherever_config_goes(self, monkeypatch):
        """Pinning the current value would only delay the next drift."""
        from agent_a import models

        monkeypatch.setattr(models.config, "GPUSTACK_MODEL_VISION",
                            "qwen3vl-medieval-german-v3")
        assert models.get_primary_vlm().model_id == "qwen3vl-medieval-german-v3"

        monkeypatch.setattr(models.config, "GPUSTACK_MODEL_VISION", "anything-else")
        assert models.get_primary_vlm().model_id == "anything-else"

    def test_a_configured_model_the_registry_never_heard_of_still_resolves(
            self, monkeypatch):
        """A deployment may set GPUSTACK_MODEL_VISION to anything.

        The registry is a description, not a gate. Refusing an unknown id would
        make a hand-maintained table able to break a deployment — the failure
        this issue is about, with the sign flipped.
        """
        from agent_a import models

        monkeypatch.setattr(models.config, "GPUSTACK_MODEL_VISION", "not-in-the-table")
        primary = models.get_primary_vlm()

        assert primary.model_id == "not-in-the-table"
        assert primary.supports_vision is True
        assert primary.endpoint, "a resolved VLM needs an endpoint to be usable"

    def test_a_described_model_keeps_its_description(self, monkeypatch):
        """When the registry does know the id, its metadata is used."""
        from agent_a import models

        known = next(m for m in models.VLM_MODELS.values())
        monkeypatch.setattr(models.config, "GPUSTACK_MODEL_VISION", known.model_id)
        primary = models.get_primary_vlm()

        assert primary.model_id == known.model_id
        assert primary.name == known.name
        assert primary.max_tokens == known.max_tokens


class TestTheRegistryStopsPraisingTheCollapse:

    def test_internvl3_is_not_described_as_the_primary_vlm(self):
        """It was measured at 189.8 % CER. The entry called it strong."""
        from agent_a import models

        entry = next((m for m in models.VLM_MODELS.values()
                      if m.model_id == "internvl3-8b-instruct"), None)
        if entry is None:                       # removed entirely is also fine
            return
        text = f"{entry.description} {entry.name}".lower()
        assert "primary vlm" not in text, (
            "the registry still calls internvl3-8b the primary VLM; it is not, "
            "and the measurement says it should not be"
        )
        assert "189.8" in entry.description or "189,8" in entry.description, (
            "an entry kept for the record has to carry the measurement that "
            "retired it, or the next reader re-adopts it"
        )

    def test_the_configured_model_is_described(self):
        """What actually runs should be the row with the metadata on it."""
        import config
        from agent_a import models

        ids = {m.model_id for m in models.VLM_MODELS.values()}
        assert config.GPUSTACK_MODEL_VISION in ids, (
            f"{config.GPUSTACK_MODEL_VISION} runs every page and the registry "
            "does not describe it"
        )
