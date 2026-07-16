"""The kraken model table must not offer a segmentation model as an HTR model.

`early_medieval_latin` pointed at zenodo.19222213 = RP_Segmenter.mlmodel
(`model_type: segmentation`, no codec — verified on the ATR host 2026-07-16 by
loading it with kraken.lib.vgsl). A layout segmenter listed as Caroline minuscule
Latin recognition. Every request 500s with
    AttributeError: 'TorchVGSLModel' object has no attribute 'codec'

It was dropped from the gateway registry (serving-atr-inference#31), and a live
re-run still called it: the ensemble plans from THIS table and sends the raw
Zenodo DOI, which passes straight through per #21. Removing it from the registry
only stopped it being advertised — not requested. Hence this table, and this test.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_no_segmenter_doi.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.models import KRAKEN_MODELS  # noqa: E402

# Verified segmentation-only on the host — never valid for recognition.
SEGMENTATION_ONLY_DOIS = {"10.5281/zenodo.19222213"}


def test_no_segmentation_model_is_offered_for_recognition():
    assert KRAKEN_MODELS, "model table empty — this guard would pass vacuously"

    offending = {
        key: m.model_id
        for key, m in KRAKEN_MODELS.items()
        if m.model_id in SEGMENTATION_ONLY_DOIS
    }
    assert not offending, (
        f"{offending} are segmentation models with no codec; every kraken "
        f"recognition request against them 500s. See serving-atr-inference#30."
    )


def test_the_bad_entry_is_gone_by_name():
    assert "early_medieval_latin" not in KRAKEN_MODELS


def test_the_real_models_survive():
    """The removal must not take working models with it."""
    for key in ("catmus_medieval", "medieval_charters"):
        assert key in KRAKEN_MODELS, key
