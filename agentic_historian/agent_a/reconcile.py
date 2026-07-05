"""
agent_a/reconcile.py — Reconciliation of dual OCR/HTR outputs.

Takes:
  vlm_transcription:  str   — from VLM path (Agent A / InternVL3)
  kraken_transcription: str — from kraken path

Returns:
  reconciled_text: str — merged/corrected transcription
  diff_report:    dict — structural comparison of the two outputs
  confidence:     float — 0.0–1.0 agreement score

Strategy:
  - Character-level diff using difflib
  - LLM judges which version is more plausible for disagreements
  - Lines only in one source are marked with a prefix flag
  - Final text prefers the more confident source per segment
"""

import difflib
import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger

# System prompt used for the LLM reconciliation step
from utils import gpustack_client as gs
import config

# Exported so dual_pipeline can reuse it for multi-way merges
RECONCILE_SYSTEM = (
    "Du bist ein TCK-Redaktor (Text Critical Kernel) — ein hochqualifizierter "
    "Paläografie-Experte für Handschriften des 14.–16. Jahrhunderts. "
    "Deine Aufgabe: erstelle die bestmögliche Transkription aus zwei "
    "konkurrierenden Fassungen verschiedener HTR/OCR-Modelle.\n\n"
    "Arbeite als Reasoning-Modell mit ausreichendem Budget — denke laut nach "
    "(Chain-of-Thought), bevor du die endgültige Fassung ausgibst.\n\n"
    "REGELN:\n"
    "1. Vergleiche beide Fassungen Zeile für Zeile und Wort für Wort.\n"
    "2. Wähle die Fassung, die paleografisch plausibler ist: "
    "Abkürzungen korrekt aufgelöst, Ligaturen erhalten, keine modernen "
    "Ergänzungen.\n"
    "3. Bei Divergenzen: bevorzuge die Version, die der historischen "
    "Schrifttradition entspricht (Karolingische Minuskel, Textura, Kurrent).\n"
    "4. Erganze fehlende Zeilen aus der jeweils anderen Fassung. "
    "Markiere zugesetzte Zeilen mit [VLM] oder [KRkn] am Zeilenende.\n"
    "5. Bei unterschiedlicher Zeilenreihenfolge: folge der Struktur der "
    "paleografisch plausibleren Fassung.\n"
    "6. Antworte NUR mit der reconcilierten Transkription — keine Kommentare, "
    "keine Erklärungen, keine Markup-Tags ausser den Markern [VLM]/[KRkn].\n"
    "7. Wenn beide Fassungen unlesbar sind, nimm die kürzere und setze "
    "ein [?UNCLEAR] an die betroffene Stelle."
)

# Default max_tokens for reconciliation — uses the configured GPUSTACK budget
RECONCILE_DEFAULT_MAX_TOKENS = config.GPUSTACK_TEXT_MAX_TOKENS


@dataclass
class ReconciliationResult:
    reconciled: str
    vlm_only_lines: list[str]
    kraken_only_lines: list[str]
    agreement_score: float
    diff_lines: int
    method: str  # "llm" | "diff" | "vlm_preferred" | "kraken_preferred"


def _token_diff(a: str, b: str) -> float:
    """Return similarity ratio 0.0-1.0 between two strings."""
    return difflib.SequenceMatcher(None, a, b).ratio()


def _split_lines(text: str) -> list[str]:
    """Split into non-empty lines, strip whitespace."""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _build_diff_report(vlm_lines: list[str], kraken_lines: list[str]) -> dict:
    """Produce a structural diff report between two sets of lines."""
    matcher = difflib.SequenceMatcher(None, vlm_lines, kraken_lines)
    diffs = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            diffs.append({
                "type": tag,
                "vlm_range": [i1, i2],
                "kraken_range": [j1, j2],
                "vlm_text": vlm_lines[i1:i2],
                "kraken_text": kraken_lines[j1:j2],
            })
    return {"diff_opcodes": diffs, "vlm_total": len(vlm_lines), "kraken_total": len(kraken_lines)}


def _line_disagreement_ratio(vlm_lines: list[str], kraken_lines: list[str]) -> float:
    """
    Compute a line-level disagreement ratio 0.0–1.0.

    Unlike _token_diff (character-level similarity), this measures how many
    lines are substantively different between the two transcriptions, weighted
    by their position.  A score of 0.0 means identical; 1.0 means completely
    disjoint line sets.
    """
    matcher = difflib.SequenceMatcher(None, vlm_lines, kraken_lines)
    equal_lines = sum(
        len(vlm_lines[i1:i2])
        for tag, i1, i2, _, _ in matcher.get_opcodes()
        if tag == "equal"
    )
    total = max(len(vlm_lines), 1)
    return 1.0 - (equal_lines / total)


def reconcile(
    vlm_transcription: str,
    kraken_transcription: str,
    *,
    use_llm: bool = True,
    max_tokens: int = 0,
) -> ReconciliationResult:
    """
    Reconcile two transcriptions (VLM vs kraken).

    Returns ReconciliationResult with the merged text and metadata.

    max_tokens: tokens to pass to the LLM.  If 0 (default), uses
    RECONCILE_DEFAULT_MAX_TOKENS from config (GPUSTACK_TEXT_MAX_TOKENS).
    The reconciliation prompt is evaluated by GPUSTACK_MODEL_TEXT (the
    configured reasoning model).
    """
    vlm_lines    = _split_lines(vlm_transcription)
    kraken_lines = _split_lines(kraken_transcription)

    if max_tokens <= 0:
        max_tokens = RECONCILE_DEFAULT_MAX_TOKENS

    if not vlm_transcription.strip():
        return ReconciliationResult(
            reconciled=kraken_transcription,
            vlm_only_lines=[],
            kraken_only_lines=kraken_lines,
            agreement_score=0.0,
            diff_lines=len(kraken_lines),
            method="kraken_preferred",
        )

    if not kraken_transcription.strip():
        return ReconciliationResult(
            reconciled=vlm_transcription,
            vlm_only_lines=vlm_lines,
            kraken_only_lines=[],
            agreement_score=0.0,
            diff_lines=len(vlm_lines),
            method="vlm_preferred",
        )

    # Line-level disagreement score (primary metric — more stable than raw token diff)
    disagreement = _line_disagreement_ratio(vlm_lines, kraken_lines)
    agreement = 1.0 - disagreement
    diff_report = _build_diff_report(vlm_lines, kraken_lines)
    n_diff = len(diff_report["diff_opcodes"])

    logger.info(
        f"[reconcile] VLM vs kraken — agreement: {agreement:.2f}, "
        f"disagreement: {disagreement:.2f}, diff_blocks: {n_diff}"
    )

    # Short-circuit: high agreement → take VLM version
    if agreement >= 0.95:
        return ReconciliationResult(
            reconciled=vlm_transcription,
            vlm_only_lines=[],
            kraken_only_lines=[],
            agreement_score=agreement,
            diff_lines=n_diff,
            method="vlm_preferred",
        )

    # Low agreement + LLM available → use GPUSTACK_MODEL_TEXT reasoning model
    if use_llm and n_diff > 0:
        prompt = (
            f"{RECONCILE_SYSTEM}\n\n"
            f"=== FASSUNG 1 (VLM/InternVL3) ===\n{vlm_transcription}\n\n"
            f"=== FASSUNG 2 (kraken) ===\n{kraken_transcription}\n\n"
            f"=== RECONCILIERTE FASSUNG ==="
        )
        try:
            reconciled = gs.chat_text(
                prompt,
                model=config.GPUSTACK_MODEL_TEXT,  # explicit reasoning model
                system=None,
                max_tokens=max_tokens,
                temperature=0.3,
            ).strip()
            return ReconciliationResult(
                reconciled=reconciled,
                vlm_only_lines=[
                    ln for ln in vlm_lines
                    if ln not in kraken_lines
                ],
                kraken_only_lines=[
                    ln for ln in kraken_lines
                    if ln not in vlm_lines
                ],
                agreement_score=agreement,
                diff_lines=n_diff,
                method="llm",
            )
        except Exception as e:
            logger.warning(f"[reconcile] LLM reconciliation failed: {e}")

    # Fallback: diff-based pick per line (prefer VLM)
    reconciled_lines = []
    matcher = difflib.SequenceMatcher(None, vlm_lines, kraken_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            reconciled_lines.extend(vlm_lines[i1:i2])
        elif tag in ("replace",):
            # VLM wins on ties — take VLM lines, flag missing kraken lines
            reconciled_lines.extend(vlm_lines[i1:i2])
        elif tag == "delete":
            reconciled_lines.extend(vlm_lines[i1:i2])
        elif tag == "insert":
            reconciled_lines.extend(f"[KRkn+] {ln}" for ln in kraken_lines[j1:j2])

    return ReconciliationResult(
        reconciled="\n".join(reconciled_lines),
        vlm_only_lines=[ln for ln in vlm_lines if ln not in kraken_lines],
        kraken_only_lines=[ln for ln in kraken_lines if ln not in vlm_lines],
        agreement_score=agreement,
        diff_lines=n_diff,
        method="diff_fallback",
    )


def reconcile_markdown(result: ReconciliationResult, doc_id: str) -> str:
    """Render reconciliation result as a readable Markdown report."""
    md = (
        f"# Reconciled Transcription: {doc_id}\n\n"
        f"**Method:** {result.method}\n"
        f"**Agreement score:** {result.agreement_score:.2f}\n"
        f"**Diff blocks:** {result.diff_lines}\n\n"
    )
    if result.vlm_only_lines:
        md += f"### VLM-only lines ({len(result.vlm_only_lines)})\n"
        for ln in result.vlm_only_lines:
            md += f"> {ln}\n"
        md += "\n"
    if result.kraken_only_lines:
        md += f"### kraken-only lines ({len(result.kraken_only_lines)})\n"
        for ln in result.kraken_only_lines:
            md += f"> [KRkn] {ln}\n"
        md += "\n"
    md += (
        "---\n\n"
        "## Reconciled Text\n\n"
        f"{result.reconciled}\n"
    )
    return md