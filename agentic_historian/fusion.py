"""
fusion.py — multi-engine HTR fusion (#237, P2-4).

Fuse the N per-engine candidates (VLM / kraken / TrOCR / PARTY — the
``RecognitionResult`` list from #234) into one best-fit transcription **without
hallucination**:

  1. **align** the candidate token sequences (pivot = the longest; difflib),
  2. **majority-vote** per aligned position → a consensus draft + a disagreement
     map (positions where the engines diverge, with each engine's reading),
  3. **LLM arbitration scoped to the disagreements only** — consensus tokens are
     byte-preserved, so the LLM can never rewrite text the engines already agree
     on; it only resolves the contested slots.

Returns the fused text plus per-span **provenance** (which engines / vote / LLM
backed each span). ``FUSION_STRATEGY=llm_merge`` is a simpler escape hatch that
just asks the LLM to merge the raw texts (for comparison).

The LLM seam (``llm_fn``) is injectable so the whole module is offline-testable.
Whitespace is normalised in the fused output (the raw per-engine texts are kept
verbatim in ``recognitions``); layout-preserving fusion is a v2 concern.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from loguru import logger

Candidate = tuple[str, str]          # (engine_label, text)
LLMFn = Callable[[str], str]


@dataclass
class Span:
    text: str
    source: str                       # "consensus" | "vote" | "llm" | "single"
    backers: list[str] = field(default_factory=list)


@dataclass
class FusionResult:
    text: str = ""
    provenance: list[Span] = field(default_factory=list)
    arbitrated: int = 0               # number of disagreement slots the LLM decided
    strategy: str = "vote"
    n_candidates: int = 0


# ── candidate extraction ─────────────────────────────────────────────────────

def _candidates(recognitions) -> list[Candidate]:
    """(label, text) for each usable recognition — non-empty text, no error.

    Accepts ``RecognitionResult`` objects or plain dicts (as in pipeline.json).
    """
    out: list[Candidate] = []
    for r in recognitions or []:
        if isinstance(r, dict):
            label, text, err = r.get("engine", "?"), r.get("text", ""), r.get("error", "")
        else:
            label, text, err = getattr(r, "engine", "?"), getattr(r, "text", ""), getattr(r, "error", "")
        if text and text.strip() and not err:
            out.append((label or "?", text))
    return out


def _candidates_with_confidence(recognitions) -> list[tuple[str, str, float]]:
    """(label, text, confidence) for each usable recognition.

    ``_candidates`` drops confidence, which is all the ranking signal fuse has:
    unlike the ensemble (#300), fuse never sees the ModelPicks, so it cannot rank
    by how well each model matches the source. Confidence is weaker — engines do
    not calibrate it comparably (TrOCR reports a flat 0.95) — but at high
    disagreement ANY single real reading beats a blend of mostly-noise.
    """
    out: list[tuple[str, str, float]] = []
    for r in recognitions or []:
        if isinstance(r, dict):
            label, text, err = r.get("engine", "?"), r.get("text", ""), r.get("error", "")
            conf = r.get("confidence", 0.0) or 0.0
        else:
            label, text, err = (getattr(r, "engine", "?"), getattr(r, "text", ""),
                                getattr(r, "error", ""))
            conf = getattr(r, "confidence", 0.0) or 0.0
        if text and text.strip() and not err:
            out.append((label or "?", text, float(conf)))
    return out


def _max_pairwise_cer_of(cands: list[Candidate]) -> float:
    """Max pairwise CER across candidate texts — the disagreement measure."""
    from eval.metrics import cer
    texts = [t for _, t in cands]
    if len(texts) < 2:
        return 0.0
    return max(
        cer(a, b, ignore_case=False, ignore_whitespace=False, ignore_punctuation=False)
        for i, a in enumerate(texts) for b in texts[i + 1:]
    )


# ── alignment + voting (pivot-anchored) ──────────────────────────────────────

def _align_columns(cands: list[Candidate]) -> tuple[list[dict], list[str]]:
    """Align every candidate's tokens to the longest one (pivot) and return
    per-pivot-position columns ``{label: token}`` plus the label order.

    'replace' folds the candidate's replacement into the first pivot column;
    'delete' marks "" (the candidate omits that token); 'insert' appends the
    extra tokens to the previous column (v1 handling of pivot-relative inserts).
    """
    labels = [lbl for lbl, _ in cands]
    toks = {lbl: text.split() for lbl, text in cands}
    pivot = max(cands, key=lambda c: len(c[1].split()))[0]
    ptok = toks[pivot]
    cols: list[dict] = [{pivot: t} for t in ptok]

    for lbl in labels:
        if lbl == pivot:
            continue
        sm = difflib.SequenceMatcher(None, ptok, toks[lbl], autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    cols[i1 + k][lbl] = toks[lbl][j1 + k]
            elif tag == "replace":
                cols[i1][lbl] = " ".join(toks[lbl][j1:j2])
                for k in range(i1 + 1, i2):
                    cols[k][lbl] = ""
            elif tag == "delete":
                for k in range(i1, i2):
                    cols[k][lbl] = ""
            elif tag == "insert" and i1 > 0:
                cols[i1 - 1][lbl] = (cols[i1 - 1].get(lbl, "") + " "
                                     + " ".join(toks[lbl][j1:j2])).strip()
    return cols, labels


def _vote(col: dict, labels: list[str]) -> tuple[Optional[str], list[str], bool]:
    """Vote on one column. Returns (winning_token, backers, is_disagreement).

    A strict majority of the candidates that weighed in wins. No majority (a tie
    or a genuine split) → disagreement (winner None, to be arbitrated).
    """
    present = {lbl: col[lbl] for lbl in labels if lbl in col}
    if not present:
        return "", [], False
    tally: dict[str, list[str]] = {}
    for lbl, tok in present.items():
        tally.setdefault(tok, []).append(lbl)
    best_tok, backers = max(tally.items(), key=lambda kv: len(kv[1]))
    if len(backers) * 2 > len(present):          # strict majority
        return best_tok, backers, False
    return None, [], True


# ── LLM arbitration (batched, scoped to disagreements) ───────────────────────

_ARBITRATE_SYSTEM = (
    "Du bist ein paläographisches Schlichtungsmodul. Für jede nummerierte "
    "Konfliktstelle in der Transkription geben mehrere HTR-Engines "
    "unterschiedliche Lesarten. Wähle je Stelle die plausibelste Lesart "
    "(historische Handschrift 14.–16. Jh.). Antworte AUSSCHLIESSLICH mit JSON: "
    '{"choices": {"0": "…", "1": "…"}}. Nur die gewählte Lesart je Stelle.'
)


def _arbitrate(slots: list[dict], llm_fn: LLMFn) -> dict[int, str]:
    """Ask the LLM to resolve the disagreement slots in one call.

    ``slots`` = [{idx, options: {label: token}, context}]. Returns {idx: choice}.
    On any failure, returns {} (caller falls back deterministically).
    """
    if not slots:
        return {}
    lines = [_ARBITRATE_SYSTEM, "", "Konfliktstellen:"]
    for s in slots:
        opts = "; ".join(f"{lbl}={tok!r}" for lbl, tok in s["options"].items())
        lines.append(f'[{s["idx"]}] Kontext: …{s["context"]}… | Optionen: {opts}')
    try:
        raw = llm_fn("\n".join(lines))
        text = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        a, b = text.find("{"), text.rfind("}")
        data = json.loads(text[a:b + 1]) if a != -1 and b > a else {}
        choices = data.get("choices", data) if isinstance(data, dict) else {}
        return {int(k): str(v) for k, v in choices.items()}
    except Exception as e:
        logger.warning(f"[fusion] arbitration failed, using deterministic fallback: {e}")
        return {}


# ── public API ───────────────────────────────────────────────────────────────

def fuse(recognitions, llm_fn: Optional[LLMFn] = None, strategy: str = "vote",
         no_merge_cer: Optional[float] = None) -> FusionResult:
    """Fuse engine candidates into a best-fit transcription.

    ``strategy="vote"`` (default): align → majority-vote → arbitrate disagreements.
    ``strategy="llm_merge"``: ask the LLM to merge the raw texts (simpler).

    **No-merge band (#300):** above ``no_merge_cer`` the candidates are NOT fused —
    the highest-confidence one is returned verbatim. Voting assumes engines err
    independently around a shared signal; at real disagreement there is none, and
    the vote returns noise. Measured on BAT_664 at 70% CER the fused text was worse
    than the best single engine. The ensemble applies a stronger version of this
    rule (it ranks by source-match score, which fuse never sees) and short-circuits
    before calling fuse — this guard is the safety net for the direct callers, i.e.
    run_full_pipeline's Phase 3 under ENABLE_MULTI_ENGINE_FUSION.
    """
    if no_merge_cer is None:
        try:
            import config
            no_merge_cer = float(getattr(config, "ENSEMBLE_NO_MERGE_CER", 0.35))
        except Exception:                              # pragma: no cover — defensive
            no_merge_cer = 0.35

    cands = _candidates(recognitions)
    if not cands:
        return FusionResult(strategy=strategy, n_candidates=0)
    if len(cands) == 1:
        lbl, text = cands[0]
        return FusionResult(text=text, provenance=[Span(text, "single", [lbl])],
                            strategy=strategy, n_candidates=1)

    llm = llm_fn or _default_llm
    if strategy == "llm_merge":
        # Out of scope for the no-merge band (#300): that band fixes majority
        # VOTING, which returns noise when most candidates are noise. llm_merge is
        # a different mechanism — an LLM reading the raw texts can plausibly pick
        # the good one — so it is left alone rather than short-circuited on a
        # hunch. If it turns out to degrade the same way, that wants its own
        # evidence and its own issue.
        return _llm_merge(cands, llm)

    # No-merge band (#300) — vote strategy only.
    max_cer = _max_pairwise_cer_of(cands)
    if max_cer > no_merge_cer:
        scored = _candidates_with_confidence(recognitions)
        if scored:
            lbl, text, conf = max(scored, key=lambda c: c[2])
            logger.info(f"[fusion] no-merge: max pairwise CER {max_cer:.1%} > "
                        f"{no_merge_cer:.1%} — selected {lbl} verbatim (conf "
                        f"{conf:.2f}); not blended")
            return FusionResult(
                text=text,
                provenance=[Span(text, f"no-merge (CER {max_cer:.1%})", [lbl])],
                strategy=f"{strategy}+no-merge", n_candidates=len(cands))

    cols, labels = _align_columns(cands)

    # First pass: vote every column; collect disagreement slots for one LLM call.
    decided: list[Optional[str]] = [None] * len(cols)
    sources: list[str] = [""] * len(cols)
    backers_l: list[list[str]] = [[] for _ in cols]
    slots: list[dict] = []
    for k, col in enumerate(cols):
        tok, backers, disagree = _vote(col, labels)
        if not disagree:
            decided[k] = tok
            sources[k] = "consensus" if len(backers) == len([l for l in labels if l in col]) else "vote"
            backers_l[k] = backers
        else:
            ctx_toks = [d for d in decided[max(0, k - 3):k] if d]
            slots.append({"idx": k, "options": {l: col[l] for l in labels if l in col},
                          "context": " ".join(ctx_toks)})

    choices = _arbitrate(slots, llm) if slots else {}
    for s in slots:
        k = s["idx"]
        opts = s["options"]
        chosen = choices.get(k)
        if chosen is not None and chosen != "":
            decided[k], sources[k], backers_l[k] = chosen, "llm", []
        elif chosen == "":
            decided[k], sources[k], backers_l[k] = "", "llm", []
        else:
            # deterministic fallback: the reading backed by the most engines
            tally: dict[str, list[str]] = {}
            for l, t in opts.items():
                tally.setdefault(t, []).append(l)
            best, bk = max(tally.items(), key=lambda kv: len(kv[1]))
            decided[k], sources[k], backers_l[k] = best, "vote", bk

    # Reconstruct text + merge consecutive same-source tokens into provenance spans.
    out_tokens = [t for t in decided if t]
    fused = " ".join(out_tokens)
    prov: list[Span] = []
    for k, tok in enumerate(decided):
        if not tok:
            continue
        if prov and prov[-1].source == sources[k] and sources[k] != "llm" and prov[-1].backers == backers_l[k]:
            prov[-1].text += " " + tok
        else:
            prov.append(Span(tok, sources[k], backers_l[k]))
    return FusionResult(text=fused, provenance=prov, arbitrated=len(slots),
                        strategy="vote", n_candidates=len(cands))


def _llm_merge(cands: list[Candidate], llm: LLMFn) -> FusionResult:
    lines = ["Mehrere HTR-Engines haben dieselbe Seite transkribiert. Erzeuge die "
             "beste konsolidierte Transkription. Bevorzuge Lesarten, die mehrere "
             "Engines teilen. Gib NUR die Transkription aus.", ""]
    for lbl, text in cands:
        lines.append(f"### {lbl}\n{text}")
    try:
        text = llm("\n".join(lines)).strip()
    except Exception as e:
        logger.warning(f"[fusion] llm_merge failed: {e}")
        text = max(cands, key=lambda c: len(c[1]))[1]      # fallback: longest
    return FusionResult(text=text, provenance=[Span(text, "llm", [l for l, _ in cands])],
                        strategy="llm_merge", n_candidates=len(cands))


def _default_llm(prompt: str) -> str:
    from utils import gpustack_client as gs
    return gs.chat_text(prompt, system=None, max_tokens=4096)
