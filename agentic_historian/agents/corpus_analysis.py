"""
agents/corpus_analysis.py — Agent D: Korpus-Analyse
Statistiken, Topic Modelling, Soziale Taxonomien, Care-Analyse, Voyant Link.

Fixes AH-38:
- Removed 5,000-char truncation: analysis runs on full corpus text
  (or 30k-char chunks if >100k chars, aggregated across chunks for LLM calls)
- total_tokens → total_words (was word count, not token count; renamed for clarity)
"""

import json
from pathlib import Path
from typing import Optional

import requests

from loguru import logger

import config
from utils import gpustack_client as gs

SYSTEM = (
    "Du bist ein Digital Humanities Forscher, spezialisiert auf korpusbasierte "
    "Analyse spätmittelalterlicher Verwaltungsquellen (Schweiz, 14.–16. Jh.)."
)

LLM_CHUNK_SIZE = 30_000


def analyse_corpus(corpus_name: str, doc_ids: Optional[list[str]] = None) -> dict:
    """Korpusanalyse. Nutzt alle Transkriptionen wenn doc_ids None."""
    if doc_ids is None:
        doc_ids = [p.stem for p in config.TRANSCRIPTIONS_DIR.glob("*.txt")]

    transcriptions = {}
    for doc_id in doc_ids:
        path = config.TRANSCRIPTIONS_DIR / f"{doc_id}.txt"
        if path.exists():
            transcriptions[doc_id] = path.read_text(encoding="utf-8")

    if not transcriptions:
        logger.warning(f"[Agent D] Keine Transkriptionen für '{corpus_name}' gefunden.")
        return {}

    combined = "\n\n---\n\n".join(transcriptions.values())

    stats = _stats(combined)
    topics = _topics_large(combined)
    taxonomy = _taxonomy_large(combined)
    care = _care_analysis_large(combined)

    result = {
        "corpus_name": corpus_name,
        "doc_count": len(transcriptions),
        "total_chars": len(combined),
        "stats": stats,
        "topics": topics,
        "taxonomy": taxonomy,
        "care_analysis": care,
        "voyant_url": _voyant_url(combined, corpus_name),
    }

    _save(corpus_name, result)
    logger.info(f"[Agent D] Fertig: {corpus_name}")
    return result


# ── Statistics ────────────────────────────────────────────────────────────────

def _stats(text: str) -> dict:
    words = text.split()
    word_freq = {}
    for w in words:
        w_clean = w.lower().strip(".,;:'\"§$%()[]{}–—")
        if w_clean and len(w_clean) > 2 and not w_clean.isdigit():
            word_freq[w_clean] = word_freq.get(w_clean, 0) + 1

    stopwords = {
        "der", "die", "das", "und", "in", "von", "mit", "zu", "den", "für",
        "ist", "auf", "nicht", "auch", "es", "an", "werden", "aus", "nach",
        "bei", "eine", "einer", "einem", "einen", "als", "noch", "wird",
        "sich", "nur", "hat", "dass", "oder", "aber", "unter", "über",
        "durch", "sein", "seiner", "wurde", "wurden", "nach", "vom",
    }
    filtered = {w: c for w, c in word_freq.items() if w not in stopwords}
    top_words = sorted(filtered.items(), key=lambda x: -x[1])[:30]

    return {
        "total_words": len(words),
        "total_unique_words": len(word_freq),
        "total_chars": len(text),
        "top_words": dict(top_words),
    }


# ── Chunking (fixes 5k-char truncation) ─────────────────────────────────────

def _chunks(text: str, size: int = LLM_CHUNK_SIZE) -> list[str]:
    if len(text) <= size:
        return [text]
    overlap = 500
    result = []
    start = 0
    while start < len(text):
        result.append(text[start:start + size])
        start = start + size - overlap
    return result


def _merge_json_results(results: list[dict], key: str) -> dict:
    merged = {}
    for r in results:
        if isinstance(r, dict) and key in r:
            sub = r[key]
            if isinstance(sub, list):
                merged.setdefault(key, []).extend(sub)
            elif isinstance(sub, dict):
                merged.setdefault(key, {}).update(sub)
    return merged


def _topics_large(text: str) -> dict:
    cs = _chunks(text)
    results = [_topics(c) for c in cs]
    return results[0] if len(results) == 1 else _merge_json_results(results, "topics")


def _taxonomy_large(text: str) -> dict:
    cs = _chunks(text)
    results = [_taxonomy(c) for c in cs]
    return results[0] if len(results) == 1 else _merge_json_results(results, "taxonomies")


def _care_analysis_large(text: str) -> dict:
    cs = _chunks(text)
    results = [_care_analysis(c) for c in cs]
    return results[0] if len(results) == 1 else _merge_json_results(results, "care_instances")


# ── Per-chunk LLM calls ──────────────────────────────────────────────────────

def _topics(text: str) -> dict:
    prompt = SYSTEM + "\n\nIdentifiziere 5–10 Topics mit Namen und Schlüsselwörtern.\n\n" + text + "\n\nAntworte als JSON: {topics: [{name, keywords: []}]}"
    try:
        return json.loads(gs.chat_text(prompt, system=None, max_tokens=2000))
    except Exception as e:
        logger.warning(f"[Agent D] Topics fehlgeschlagen: {e}")
        return {"topics": []}


def _taxonomy(text: str) -> dict:
    prompt = SYSTEM + "\n\nAnalysiere soziale Taxonomien: arme lüt, erbar lüt, Bürger, Hintersässe, Juden, Zigeuner, Vaganten, gesellen, Knecht, Magd, Witwe, Waise.\n\n" + text + "\n\nAntworte als JSON: {taxonomies: [{term, count, contexts: []}]}"
    try:
        return json.loads(gs.chat_text(prompt, system=None, max_tokens=2000))
    except Exception as e:
        logger.warning(f"[Agent D] Taxonomy fehlgeschlagen: {e}")
        return {"taxonomies": []}


def _care_analysis(text: str) -> dict:
    prompt = SYSTEM + "\n\nAnalysiere Care-relevante Inhalte: Care-Instanzen, Arrangements, Entlohnung, Gender, Lebensläufe.\n\n" + text + "\n\nAntworte als JSON: {care_instances: [], patterns: [], gender_aspects: []}"
    try:
        return json.loads(gs.chat_text(prompt, system=None, max_tokens=2000))
    except Exception as e:
        logger.warning(f"[Agent D] Care-Analyse fehlgeschlagen: {e}")
        return {"care_instances": [], "patterns": [], "gender_aspects": []}


# ── Voyant + Persistence ─────────────────────────────────────────────────────

def _voyant_url(text: str, corpus_name: str) -> str:
    try:
        corpus_id = corpus_name.lower().replace(" ", "_")
        resp = requests.post(
            config.VOYANT_API_URL + "/Corpus",
            data={"content": text[:50_000], "contentId": corpus_id},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("url", "")
    except Exception as e:
        logger.warning(f"[Agent D] Voyant-Link fehlgeschlagen: {e}")
    return ""


def _save(corpus_name: str, result: dict):
    safe = corpus_name.replace(" ", "_")
    out = config.OUTPUTS_DIR / f"corpus_{safe}"
    out.mkdir(parents=True, exist_ok=True)

    (out / "stats.json").write_text(json.dumps(result["stats"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "topics.json").write_text(json.dumps(result["topics"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "taxonomy.json").write_text(json.dumps(result["taxonomy"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "care_analysis.json").write_text(json.dumps(result["care_analysis"], ensure_ascii=False, indent=2), encoding="utf-8")

    s = result["stats"]
    (out / "report.md").write_text(
        f"# Korpus-Analyse: {corpus_name}\n\n"
        f"**Dokumente:** {result['doc_count']}  **Zeichen:** {result['total_chars']:,}  "
        f"**Wörter:** {s.get('total_words', 0):,}\n\n"
        f"## Voyant\n\n{result.get('voyant_url', '—')}\n",
        encoding="utf-8",
    )
    logger.info(f"[Agent D] Gespeichert: {out}")