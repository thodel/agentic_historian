"""
ingest.py — SwitchDrive → pipeline ingestion orchestration (#33).

UI-agnostic core: the Discord bot (and the future Ad-Fontes web front end) call
these functions, so ingestion logic lives outside bot.py and any UI is a thin
shell over the same core.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

import config
from orchestrator import run_full_pipeline_group


def run_switchdrive_orders(parent: Optional[str] = None,
                           reprocess: bool = False) -> dict:
    """Process each SwitchDrive subfolder under ``parent`` as ONE multi-page order.

    Each immediate subfolder is an order; if there are none, ``parent`` itself is
    treated as a single order (loose images directly in it). Already-processed
    orders are skipped unless ``reprocess`` is set. Each order is staged, run
    through the grouped pipeline, marked processed, and its staging dir cleaned up.

    Returns ``{"done": [...], "skipped": [...], "empty": [...], "errors": [...]}``.
    A failing order is recorded and never stops the batch.
    """
    from utils import switchdrive

    parent = parent or config.SWITCHDRIVE_REMOTE_DIR
    orders = switchdrive.list_subdirs(parent) or [parent]
    already = set() if reprocess else switchdrive.load_processed()
    res: dict[str, list] = {"done": [], "skipped": [], "empty": [], "errors": []}

    for order in orders:
        order_id = order.strip("/").replace("/", "__")
        if order_id in already:
            res["skipped"].append(order_id)
            continue
        staging = config.HOT_FOLDER / "_orders" / order_id
        try:
            files = switchdrive.pull_folder(order, staging, recursive=True)
            if not files:
                res["empty"].append(order_id)
                continue
            doc_id = Path(order.rstrip("/")).name or order_id
            run_full_pipeline_group(doc_id, files)
            switchdrive.mark_processed(order_id)
            res["done"].append(f"{doc_id} ({len(files)}p)")
        except Exception as e:
            logger.exception(f"[ingest] order {order_id} failed")
            res["errors"].append(f"{order_id}: {e}")
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return res


# ═══════════════════════════════════════════════════════════════════════════════
# Selective reprocessing (#226, P1-B2)
# ═══════════════════════════════════════════════════════════════════════════════
#
# After a human correction (e.g. /route pins the right script) or a partial run,
# re-run only the affected stages — not the whole pipeline — by driving the
# document's RunState: invalidate the changed criteria (per the #145 matrix),
# then resume(), reusing done stages' artifacts. The stage runners bind to the
# real agent/HTR implementations (not duplicated here); they are injectable so
# the orchestration is fully offline-testable.

# A runner takes the RunState and returns a runstate.StageResult.
Runner = Callable[[object], object]


def build_stage_runners(state) -> dict:
    """Public wrapper for auto-resume in gate views (#227)."""
    return _default_runners(state)


def _default_runners(state) -> dict:
    """Bind each STAGE to its real implementation, reusing the agent + two-pronged
    HTR functions rather than reimplementing them.

    - ``agent_b`` / ``agent_c`` recompute from the *current* transcription — the
      common reprocess after a Gate-2 path pick or a transcription edit.
    - ``model_select`` / ``kraken`` / ``reconcile`` re-run the two-pronged HTR
      from the persisted source image. They need DUAL HTR available and the
      ``image_path`` + ``vlm_transcription`` that ``run_full_pipeline`` persisted
      (#226); if any is missing they return an explicit error StageResult so the
      caller sees *why* rather than getting a silent no-op.
    ``vlm`` and ``agent_d`` have no runner here (a criteria change never re-runs
    the VLM, and corpus analysis is lazily re-run on the next /agent_d).
    """
    from runstate import StageResult
    from agents import source_description as agent_b, entity_agent as agent_c
    doc_id = state.doc_id

    def _describe(st):
        txt = st.artifacts.get("transcription", "")
        desc = agent_b.describe(doc_id=doc_id, transcription=txt)
        excerpt = desc.get("source_description", "")[:120] if isinstance(desc, dict) else ""
        return StageResult(artifact=desc, agent="B", excerpt=excerpt, decision="re-described")

    def _entities(st):
        txt = st.artifacts.get("transcription", "")
        ents = agent_c.extract_entities(doc_id, txt)
        n = len((ents or {}).get("entities", [])) if isinstance(ents, dict) else 0
        return StageResult(artifact=ents, agent="C", decision=f"{n} entities")

    def _model_select(st):
        crit = {k: st.criteria.get(k) for k in ("script", "lang", "century", "document_type")}
        return StageResult(artifact=crit, agent="model_select", decision=f"criteria={crit}")

    def _kraken(st):
        try:
            from agent_a import transcribe_dual
        except Exception as e:                        # DUAL HTR package unavailable
            return StageResult(error=f"dual HTR unavailable: {e}")
        img = st.artifacts.get("image_path")
        if not img:
            return StageResult(error="no persisted image_path — re-run from source")
        desc = st.artifacts.get("description") or {}
        desc_text = desc.get("source_description", "") if isinstance(desc, dict) else ""
        dual = transcribe_dual(img, source_description=desc_text, run_vlm=False,
                               run_kraken=True, run_party=True, use_llm_reconcile=True)
        a_meta = dict(st.artifacts.get("a_meta") or {})
        a_meta["kraken_transcription"] = dual.kraken_transcription
        a_meta["party_transcription"] = dual.party_transcription
        st.artifacts["a_meta"] = a_meta
        recs = list(st.artifacts.get("recognitions") or [])
        for r in getattr(dual, "recognitions", []) or []:
            if r not in recs:
                recs.append(r)
        st.artifacts["recognitions"] = recs
        return StageResult(artifact=dual.kraken_transcription, agent="kraken",
                           excerpt=(dual.kraken_transcription or "")[:120], decision="kraken re-run")

    def _reconcile(st):
        try:
            from agent_a.reconcile import reconcile
        except Exception as e:
            return StageResult(error=f"reconcile unavailable: {e}")
        vlm = st.artifacts.get("vlm_transcription") or st.artifacts.get("transcription", "")
        kraken = (st.artifacts.get("a_meta") or {}).get("kraken_transcription", "")
        if not kraken:
            return StageResult(artifact=st.artifacts.get("transcription", ""),
                               agent="reconcile", decision="no kraken text — kept current")
        rec = reconcile(vlm, kraken)
        st.artifacts["transcription"] = rec.reconciled
        return StageResult(artifact=rec.reconciled, agent="reconcile", excerpt=rec.reconciled[:120],
                           decision=f"reconciled ({rec.method}) agree={rec.agreement_score:.2f}")

    return {
        "model_select": _model_select,
        "kraken": _kraken,
        "reconcile": _reconcile,
        "agent_b": _describe,
        "agent_c": _entities,
    }


def _default_export(doc_id: str) -> None:
    """Re-derive <doc_id>_pipeline.json from the (now updated) RunState."""
    from orchestrator import _save_pipeline_result, PipelineContext
    _save_pipeline_result(doc_id, PipelineContext(doc_id), from_runstate=True)


def _default_publish(doc_id: str, source_url: Optional[str]) -> None:
    """Re-publish the doc (respects ENABLE_GITHUB_PUBLISH; non-fatal)."""
    from orchestrator import _publish_outputs
    _publish_outputs(doc_id, source_url)


def _run_resume(state, runners, export, publish) -> dict:
    """Resume the given RunState, then re-export + publish iff something ran
    cleanly. Shared by reprocess() and resume_pending()."""
    from runstate import ERROR, STAGES
    rmap = runners if runners is not None else _default_runners(state)
    ran = state.resume(rmap, stop_on_error=True)
    state.save()                                       # persist regardless of outcome
    errors = [s for s in ran if state.stage_status.get(s) == ERROR]
    skipped = [s for s in STAGES if s not in ran]

    published = False
    if ran and not errors:
        (export or _default_export)(state.doc_id)
        (publish or _default_publish)(state.doc_id, state.source_url)
        published = True
    return {"ran": ran, "skipped": skipped, "errors": errors, "published": published}


def reprocess(doc_id: str,
              fields: Optional[list[str]] = None,
              stages: Optional[list[str]] = None,
              *,
              runners: Optional[dict] = None,
              export: Optional[Callable] = None,
              publish: Optional[Callable] = None) -> dict:
    """Re-run only the stages affected by a change, reusing everything else.

    ``fields`` — changed criteria (``["script"]``, …); each is ``invalidate()``-d
    so the #145 matrix marks the right stages dirty (the pinned value already on
    the RunState is what the re-run sees). ``stages`` — explicit stage names to
    force dirty. After resume(): re-export pipeline.json and re-fire the publish
    hook. Returns ``{ran, skipped, errors, published}``.
    """
    from runstate import RunState, DIRTY
    state = RunState.load(doc_id)
    for f in (fields or []):
        state.invalidate(f)                            # value=None → mark dirty, keep pin
    for s in (stages or []):
        if s in state.stage_status:
            state.stage_status[s] = DIRTY
    return _run_resume(state, runners, export, publish)


def resume_pending(doc_id: str,
                   *,
                   runners: Optional[dict] = None,
                   export: Optional[Callable] = None,
                   publish: Optional[Callable] = None) -> dict:
    """Finish a partially-run doc: run only its pending/dirty stages. A fully-done
    doc returns ``ran: []`` (and does not re-publish)."""
    from runstate import RunState
    state = RunState.load(doc_id)
    return _run_resume(state, runners, export, publish)
