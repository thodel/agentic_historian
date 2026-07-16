"""
ensemble.py — iterative multi-engine HTR ensemble for one page (#272).

Grouped/multi-page orders used to be VLM-only (`run_full_pipeline_group`), which
is how u-17__ ended up with a page of "uuuu": the VLM repetition-collapsed and no
other engine ran. This module runs **≥ min_engines recognition processes per
page** — VLM + best kraken + best TrOCR — and, when the candidates **disagree**
(max pairwise CER above a threshold), expands the ensemble with the next-ranked
kraken/TrOCR model and re-compares. Several loops may run (bounded by max_loops).
All candidates are fused (``fusion.fuse``) and kept as ``RecognitionResult``s so
every engine's output survives to publishing (#238) and the eval harness.

Backend-agnostic by design: engine execution is an injected
``recognize_fn(pick, image) -> RecognitionResult`` so the whole module is
offline-testable. The real backend is ``KrakenHTTPClient.transcribe(image,
model_id)`` (kraken *and* TrOCR via ``/ocr`` auto-segment, #25) plus GPUStack for
the VLM — wired by the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger


@dataclass
class ModelPick:
    """One (engine, model) the ensemble may run."""
    engine: str            # "vlm" | "kraken" | "trocr" | "party"
    model_id: str
    score: float = 0.0


@dataclass
class EnsembleResult:
    recognitions: list = field(default_factory=list)   # list[RecognitionResult|dict]
    text: str = ""                                     # fused best-fit
    provenance: list = field(default_factory=list)
    loops: int = 0                                     # feedback loops executed
    max_pairwise_cer: float = 0.0                      # final disagreement measure
    ran: list = field(default_factory=list)            # ModelPicks actually run
    added: list = field(default_factory=list)          # ModelPicks the loop added


RecognizeFn = Callable[[ModelPick, Any], Any]          # (pick, image) -> RecognitionResult


# ── model planning (ranked pool spanning engines) ────────────────────────────

def resolve_gateway_id(pick: ModelPick, registry) -> str:
    """Map a pick's LOCAL model id to the id the ATR gateway accepts (#277).

    The local registry (agent_a/models.py) identifies kraken models by Zenodo DOI
    and TrOCR models by HF repo, but the gateway has its own ids (``kraken-…`` /
    ``trocr-…``). A raw Zenodo DOI still resolves there (#21 accepts raw refs), but
    an **HF repo does not — it 404s** — so TrOCR picks must be mapped or the
    ensemble silently degrades to VLM+kraken.

    ``registry`` is the gateway's model list (``KrakenHTTPClient.list_models()``:
    dicts with ``id`` / ``engine`` / ``hf_repo`` / ``zenodo_id``). Matching against
    it is authoritative and self-correcting — no naming convention is assumed.
    Falls back to the raw id when the registry is unavailable or has no match.
    """
    mid = pick.model_id
    for m in registry or []:
        if not isinstance(m, dict):
            continue
        if mid in (m.get("id"), m.get("hf_repo"), m.get("zenodo_id")):
            return m.get("id") or mid
    return mid


def _default_vlm_model_id() -> str:
    try:
        from agent_a import models as _models
        return _models.get_primary_vlm().model_id
    except Exception:                                   # pragma: no cover — defensive
        return "vlm"


def plan_models(criteria, *, per_engine: int = 3,
                vlm_model_id: Optional[str] = None) -> list[ModelPick]:
    """Ordered pool of picks. The front guarantees **engine diversity** — VLM,
    the best kraken, the best TrOCR (≥3 when models exist) — and the tail is the
    next-ranked kraken/TrOCR models interleaved, which the feedback loop draws
    from. Model selection reuses the script/century-aware selectors."""
    from agent_a.model_selector import select_kraken_model, select_tocr_model

    vlm_model_id = vlm_model_id or _default_vlm_model_id()
    kraken = select_kraken_model(criteria, top_k=per_engine)
    trocr = select_tocr_model(criteria, top_k=per_engine)

    picks: list[ModelPick] = [ModelPick("vlm", vlm_model_id, 1.0)]
    if kraken:
        picks.append(ModelPick("kraken", kraken[0].model.model_id, float(kraken[0].score)))
    if trocr:
        picks.append(ModelPick("trocr", trocr[0].model.model_id, float(trocr[0].score)))

    rest_k = [ModelPick("kraken", m.model.model_id, float(m.score)) for m in kraken[1:]]
    rest_t = [ModelPick("trocr", m.model.model_id, float(m.score)) for m in trocr[1:]]
    for i in range(max(len(rest_k), len(rest_t))):
        if i < len(rest_k):
            picks.append(rest_k[i])
        if i < len(rest_t):
            picks.append(rest_t[i])
    return picks


# ── disagreement measure ──────────────────────────────────────────────────────

def _text_of(r) -> tuple[str, str]:
    """(text, error) from a RecognitionResult object or a plain dict."""
    if isinstance(r, dict):
        return r.get("text", "") or "", r.get("error", "") or ""
    return getattr(r, "text", "") or "", getattr(r, "error", "") or ""


def _max_pairwise_cer(recognitions: list) -> float:
    """Max pairwise CER across the usable (non-empty, error-free) candidates —
    the disagreement signal that drives the feedback loop. <2 candidates → 0."""
    from eval.metrics import cer
    texts = [t for t, e in (_text_of(r) for r in recognitions) if t.strip() and not e]
    if len(texts) < 2:
        return 0.0
    worst = 0.0
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            worst = max(worst, cer(a, b), cer(b, a))   # symmetric
    return worst


# ── the ensemble ──────────────────────────────────────────────────────────────

def recognize_ensemble(image, criteria, recognize_fn: RecognizeFn, *,
                       min_engines: int = 3, max_loops: int = 2,
                       agreement_cer: float = 0.30, llm_fn=None,
                       per_engine: int = 3,
                       picks: Optional[list] = None) -> EnsembleResult:
    """Run ≥ ``min_engines`` recognitions on one page, then keep adding the next
    ranked model while the candidates disagree (max pairwise CER >
    ``agreement_cer``), up to ``max_loops`` extra loops. Fuse all candidates.

    ``recognize_fn(pick, image)`` returns a RecognitionResult (or None / raises on
    failure — both are tolerated; a failed pick is skipped and, during the initial
    phase, backfilled from the pool so we still reach ``min_engines`` usable runs).
    """
    from fusion import fuse

    pool = list(picks) if picks is not None else plan_models(criteria, per_engine=per_engine)
    recognitions: list = []
    ran: list = []
    added: list = []

    def _run(pick) -> bool:
        try:
            res = recognize_fn(pick, image)
        except Exception as e:                          # a backend blew up
            logger.warning(f"[ensemble] {pick.engine}/{pick.model_id} failed: {e}")
            return False
        if res is None:
            return False
        recognitions.append(res)
        ran.append(pick)
        return True

    idx = 0
    # 1) initial batch — run until min_engines usable recognitions (backfill on failure)
    while len(recognitions) < min_engines and idx < len(pool):
        _run(pool[idx])
        idx += 1

    # 2) feedback loop — expand while the candidates disagree
    loops = 0
    max_cer = _max_pairwise_cer(recognitions)
    while max_cer > agreement_cer and loops < max_loops and idx < len(pool):
        pick = pool[idx]
        idx += 1
        loops += 1
        if _run(pick):
            added.append(pick)
        max_cer = _max_pairwise_cer(recognitions)
        logger.info(f"[ensemble] loop {loops}: added {pick.engine}/{pick.model_id}, "
                    f"max pairwise CER now {max_cer:.2%}")

    fr = fuse(recognitions, llm_fn=llm_fn)
    return EnsembleResult(
        recognitions=recognitions, text=fr.text, provenance=fr.provenance,
        loops=loops, max_pairwise_cer=max_cer, ran=ran, added=added,
    )
