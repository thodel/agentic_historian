"""#300: no-merge band — don't majority-vote when candidates are too divergent.

When max pairwise CER > ENSEMBLE_NO_MERGE_CER (default 0.35), the ensemble must
select the best single candidate by model_score, not blend them.

Run from repo root:
    pytest agentic_historian/tests/test_ah_300_no_merge.py -v
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.model_selector import RecognitionResult
import ensemble
import fusion


# ── fixtures ─────────────────────────────────────────────────────────────────

def _rec(engine, model_id, text, model_score=0.0):
    """Build a RecognitionResult for testing."""
    return RecognitionResult(
        engine=engine, model_id=model_id, text=text,
        model_score=model_score, confidence=0.5,
    )


# The BAT_664 fixture from the issue: 4 candidates, max pairwise CER ~70%.
# TrOCR has the real Early New High German reading; the others are garbage.
BAT664_TROCR      = "Vnser fründlich grus vor liebe getrune von der stösse wyse so"
BAT664_VLM        = "Infer fremdlichs grüe vor liebe gerninre Von der koffe wer So"
BAT664_KRAKEN_CM  = "duser feunilite grus vor liebe gerrmreuon de scosse roepse"
BAT664_KRAKEN_MCC = "aInfer fommlurg deuis For lb grninv (Dan de Rof) avar Coda Gnit"

# High model scores reflect that trocr-kurrent-xvi-xvii is the right model for
# a Kurrent XVI–XVII source (Agent B confirmed). Others score poorly.
TROCR_SCORE  = 0.82
VLM_SCORE    = 0.35
KRAKEN_SCORE = 0.05


# ── no-merge band — ensemble.py ─────────────────────────────────────────────

class TestEnsembleNoMergeBand:
    """ensemble.recognize_ensemble must not merge above the CER threshold."""

    def _run_no_merge(self, cands, agreement_cer=0.30):
        """Run recognize_ensemble with a mock recognize_fn that returns our cands."""
        calls = []

        def mock_recognize_fn(pick, image):
            for c in cands:
                if c.engine == pick.engine:
                    calls.append(pick)
                    return c
            calls.append(pick)
            return None

        # Pass empty criteria — model_selector scoring comes from the pool scores
        from agent_a.model_selector import SourceCriteria
        criteria = SourceCriteria()
        result = ensemble.recognize_ensemble(
            image=None,
            criteria=criteria,
            recognize_fn=mock_recognize_fn,
            min_engines=1,
            agreement_cer=agreement_cer,
        )
        return result

    def test_no_merge_above_threshold_returns_best_by_model_score(self):
        """At ~70% CER the result must be byte-identical to the best candidate."""
        cands = [
            _rec("trocr", "trocr-kurrent-xvi-xvii", BAT664_TROCR,  model_score=TROCR_SCORE),
            _rec("vlm",   "internvl3-8b-instruct",  BAT664_VLM,   model_score=VLM_SCORE),
            _rec("kraken","catmus_medieval",         BAT664_KRAKEN_CM, model_score=KRAKEN_SCORE),
            _rec("kraken","mccatmus",                BAT664_KRAKEN_MCC, model_score=KRAKEN_SCORE),
        ]
        result = self._run_no_merge(cands)
        assert result.text == BAT664_TROCR, (
            f"Expected TrOCR text unchanged; got: {result.text!r}"
        )

    def test_no_merge_ranks_by_model_score_not_length(self):
        """A long garbage candidate must NOT win over a short good one."""
        good_short = _rec("trocr", "trocr-kurrent-xvi-xvii",
                          "Vnser fründlich grus", model_score=0.82)
        bad_long = _rec("kraken", "catmus_medieval",
                        "a very long garbage candidate " * 20, model_score=0.05)
        result = self._run_no_merge([good_short, bad_long])
        assert result.text == "Vnser fründlich grus", (
            "Short good candidate should win despite shorter length"
        )

    def test_merge_below_threshold_produces_vote_output(self):
        """At low CER the ensemble should still merge normally."""
        # Two very similar candidates — CER should be near 0
        c1 = _rec("trocr", "trocr-kurrent-xvi-xvii",
                  "Vnser fründlich grus vor liebe", model_score=0.82)
        c2 = _rec("trocr", "trocr-kurrent-xvi-xvii-2",
                  "Vnser fründlich grus vor liebe", model_score=0.80)
        result = self._run_no_merge([c1, c2])
        # FusionResult.strategy should be "vote" (normal merge)
        assert result.text, "Should produce fused text"

    def test_selection_provenance_carries_no_merge_metadata(self):
        """Selection provenance must record that no-merge triggered, and the CER."""
        cands = [
            _rec("trocr", "trocr-kurrent-xvi-xvii", BAT664_TROCR,  model_score=TROCR_SCORE),
            _rec("kraken","catmus_medieval",         BAT664_KRAKEN_CM, model_score=KRAKEN_SCORE),
        ]
        result = self._run_no_merge(cands)
        assert len(result.provenance) == 1
        span = result.provenance[0]
        assert span.source == "selection", f"Expected source='selection', got {span.source!r}"
        # Check that no-merge attributes are attached
        assert hasattr(span, "max_pairwise_cer"), "span must have max_pairwise_cer"
        assert span.max_pairwise_cer > 0.35, "CER should exceed threshold"

    def test_empty_recognitions_handled_gracefully(self):
        """Empty recognitions list returns zero-length text, no crash."""
        result = self._run_no_merge([])
        assert result.text == ""

    def test_single_candidate_returns_text_without_checking_cer(self):
        """With one candidate, CER check is skipped and that candidate is returned."""
        c = _rec("trocr", "trocr-kurrent-xvi-xvii", "only candidate", model_score=0.9)
        result = self._run_no_merge([c])
        assert result.text == "only candidate"


# ── no-merge band — fusion.fuse() ──────────────────────────────────────────

class TestFusionNoMergeBand:
    """fusion.fuse must also respect the no-merge band when called directly."""

    def test_fuse_skips_merge_above_threshold_picks_best_model_score(self):
        """fusion.fuse at high CER must select, not blend."""
        recs = [
            {"engine": "trocr", "model_id": "trocr-kurrent-xvi-xvii",
             "text": BAT664_TROCR, "error": "", "model_score": TROCR_SCORE},
            {"engine": "vlm",   "model_id": "internvl3-8b-instruct",
             "text": BAT664_VLM, "error": "", "model_score": VLM_SCORE},
            {"engine": "kraken","model_id": "catmus_medieval",
             "text": BAT664_KRAKEN_CM, "error": "", "model_score": KRAKEN_SCORE},
            {"engine": "kraken","model_id": "mccatmus",
             "text": BAT664_KRAKEN_MCC, "error": "", "model_score": KRAKEN_SCORE},
        ]
        result = fusion.fuse(recs)
        assert result.text == BAT664_TROCR, (
            f"Expected TrOCR text; got: {result.text!r}"
        )

    def test_fuse_below_threshold_merges_normally(self):
        """At low CER, fusion still performs majority-vote as before."""
        recs = [
            {"engine": "a", "model_id": "m1", "text": "hello world", "error": "", "model_score": 0.5},
            {"engine": "b", "model_id": "m2", "text": "hello world", "error": "", "model_score": 0.5},
        ]
        result = fusion.fuse(recs)
        assert result.text == "hello world"

    def test_fuse_single_candidate_returns_single_source(self):
        """Single candidate must be returned without CER check."""
        recs = [{"engine": "trocr", "model_id": "m1", "text": "only", "error": ""}]
        result = fusion.fuse(recs)
        assert result.text == "only"
        assert result.n_candidates == 1

    def test_fuse_selection_provenance_on_no_merge(self):
        """When no-merge triggers, provenance source must be 'selection'."""
        recs = [
            {"engine": "trocr", "model_id": "m1", "text": BAT664_TROCR,
             "error": "", "model_score": TROCR_SCORE},
            {"engine": "kraken","model_id": "m2", "text": BAT664_KRAKEN_CM,
             "error": "", "model_score": KRAKEN_SCORE},
        ]
        result = fusion.fuse(recs)
        assert len(result.provenance) == 1
        assert result.provenance[0].source == "selection"


# ── ENSEMBLE_NO_MERGE_CER config ───────────────────────────────────────────

class TestConfig:
    def test_config_has_no_merge_cer(self, monkeypatch):
        """ENSEMBLE_NO_MERGE_CER must be importable from config."""
        import config
        assert hasattr(config, "ENSEMBLE_NO_MERGE_CER")
        assert 0.0 < config.ENSEMBLE_NO_MERGE_CER <= 1.0

    def test_default_threshold_is_035(self, monkeypatch):
        """Default threshold must be 0.35 as specified in the issue."""
        import config
        # Check the in-process value (may have been overridden in other tests)
        assert config.ENSEMBLE_NO_MERGE_CER == 0.35
