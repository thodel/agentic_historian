"""
agents/corpus_analysis.py — Agent D: Korpus-Analyse
Statistiken, Topic Modelling, Soziale Taxonomien, Care-Analyse, Voyant Link.

Fixes AH-38:
- Removed 5,000-char truncation: analysis runs on full corpus text
  (or 50,000-char chunk if >100k chars, aggregated across chunks for LLM calls)
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

# LLM chunk size: ~30k chars ≈ 8–10k tokens for historical German text
LLM_CHUNK_SIZE = 30_000


def analyse_corpus(corpus_name: str, doc_ids: Optional[list[str]] = None) -> dict:
    """
    Führt eine vollständige Korpusanalyse durch.
    Wenn doc_ids None: alle Transkriptionen im data/transcriptions/ verwenden.
    """
    logger.info(f"[Agent D] Korpusanalyse: {corpus_name}")

    # Sammle Transkriptionen
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

    # 1) Statistiken
    stats = _stats(combined)

    # 2) Topics (chunked if large corpus)
    topics = _topics_large(combined)

    # 3) Soziale Taxonomien
    taxonomy = _taxonomy_large(combined)

    # 4) Care-Analyse
    care = _care_analysis_large(combined)

    result = {
        "corpus_name": corpus_name,
        "doc_count": len(transcriptions),
        "total_chars": len(combined),
        "stats": stats,
        "topics": topics,
        "taxonomy": taxonomy,
        "care_analysis": care,
    }

    # 5) Voyant Link
    result["voyant_url"] = _voyant_url(combined, corpus_name)

    _save(corpus_name, result)
    logger.info(f"[Agent D] Fertig: {corpus_name}")
    return result


# ── Statistics ────────────────────────────────────────────────────────────────

def _stats(text: str) -> dict:
    """Full corpus word frequency — no truncation."""
    words = text.split()
    word_freq = {}
    for w in words:
        w_clean = w.lower().strip(".,;:'\"§$%()[]{}–—")
        if w_clean:
            word_freq[w_clean] = word_freq.get(w_clean, 0) + 1

    # Top 30 häufige Wörter (ohne Stoppwörter)
    stopwords = {
        "der", "die", "das", "und", "in", "von", "mit", "zu", "den", "für",
        "ist", "auf", "nicht", "auch", "es", "an", "werden", "aus", "nach",
        "mit", "bei", "eine", "einer", "einem", "einen", "als", "noch", "wird",
        "sich", "nur", "hat", "dass", "oder", "aber", "unter", "über", "durch",
    }
    filtered = {w: c for w, c in word_freq.items()
                if w not in stopwords and len(w) > 2 and not w.isdigit()}
    top_words = sorted(filtered.items(), key=lambda x: -x[1])[:30]

    return {
        "total_words": len(words),          # was incorrectly labelled "tokens" before
        "total_unique_words": len(word_freq),
        "total_chars": len(text),
        "top_words": dict(top_words),
    }


# ── Chunked LLM analysis (fixes 5,000-char truncation) ───────────────────────

def _chunks(text: str, chunk_size: int = LLM_CHUNK_SIZE) -> list[str]:
    """Split text into overlapping chunks (overlap = 500 chars to avoid splitting mid-sentence)."""
    if len(text) <= chunk_size:
        return [text]

    overlap = 500
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def _merge_json_results(results: list[dict], key: str) -> dict:
    """Merge list of JSON results from chunk analysis into one."""
    merged = {}
    for r in results:
        if isinstance(r, dict) and key in r:
            sub = r[key]
            if isinstance(sub, list):
                if key not in merged:
                    merged[key] = []
                merged[key].extend(sub)
            elif isinstance(sub, dict):
                if key not in merged:
                    merged[key] = {}
                merged[key].update(sub)
    return merged


def _topics_large(text: str) -> dict:
    chunks = _chunks(text)
    results = []
    for i, chunk in enumerate(chunks):
        logger.info(f"[Agent D] Topics — chunk {i+1}/{len(chunks)}")
        results.append(_topics(chunk))
    if len(results) == 1:
        return results[0]
    # Merge all chunk topics
    return _merge_json_results(results, "topics")


def _taxonomy_large(text: str) -> dict:
    chunks = _chunks(text)
    results = []
    for i, chunk in enumerate(chunks):
        logger.info(f"[Agent D] Taxonomy — chunk {i+1}/{len(chunks)}")
        results.append(_taxonomy(chunk))
    if len(results) == 1:
        return results[0]
    return _merge_json_results(results, "taxonomies")


def _care_analysis_large(text: str) -> dict:
    chunks = _chunks(text)
    results = []
    for i, chunk in enumerate(chunks):
        logger.info(f"[Agent D] Care — chunk {i+1}/{len(chunks)}")
        results.append(_care_analysis(chunk))
    if len(results) == 1:
        return results[0]
    return _merge_json_results(results, "care_instances")


# ── Individual LLM calls (per chunk) ─────────────────────────────────────────

def _topics(text: str) -> dict:
    prompt = (
        SYSTEM + "\n\n" +
        "Identifiziere 5–10 Topics in diesem Textkorpus. "
        "Gib pro Topic einen Namen und die zugehörigen Schlüsselwörter.\n\n" +
        text + "\n\n" +
        "Antworte als JSON: {topics: [{name, keywords: []}]}"
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=2000)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Agent D] Topics fehlgeschlagen: {e}")
        return {"topics": []}


def _taxonomy(text: str) -> dict:
    prompt = (
        SYSTEM + "\n\n" +
        "Analysiere die Verwendung sozialer Kategorien/Taxonomien in diesem Korpus. "
        "Achte besonders auf: arme lüt, erbar lüt, Bürger, Hintersässe, Juden, Zigeuner, "
        "Vaganten, gesellen, Knecht, Magd, Witwe, Waise.\n\n" +
        text + "\n\n" +
        "Antworte als JSON: {taxonomies: [{term, count, contexts: []}]}"
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=2000)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Agent D] Taxonomy fehlgeschlagen: {e}")
        return {"taxonomies": []}


def _care_analysis(text: str) -> dict:
    prompt = (
        SYSTEM + "\n\n" +
        "Analysiere Care-relevante Inhalte in diesem Korpus: "
        "Care-Instanzen, Arrangements, Entlohnung, Gender, Lebensläufe.\n\n" +
        text + "\n\n" +
        "Antworte als JSON: {care_instances: [], patterns: [], gender_aspects: []}"
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=2000)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Agent D] Care-Analyse fehlgeschlagen: {e}")
        return {"care_instances": [], "patterns": [], "gender_aspects": []}


# ── Voyant + Persistence ──────────────────────────────────────────────────────

def _voyant_url(text: str, corpus_name: str) -> str:
    """Erstellt Voyant-Tools-Link mit eingebettetem Korpus (bis 50k Zeichen)."""
    try:
        corpus_id = corpus_name.lower().replace(" ", "_")
        snippet = text[:50_000]          # Voyant limit; first 50k is representative
        resp = requests.post(
            config.VOYANT_API_URL + "/Corpus",
            data={"content": snippet, "contentId": corpus_id},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("url", "")
    except Exception as e:
        logger.warning(f"[Agent D] Voyant-Link fehlgeschlagen: {e}")
    return ""


def _save(corpus_name: str, result: dict):
    """Speichert Korpus-Analyse."""
    safe_name = corpus_name.replace(" ", "_")
    out_dir = config.OUTPUTS_DIR / f"corpus_{safe_name}"
    out_dir.mkdir(parents=True, exist_ok=True)

    stats = result.get("stats", {})
    (out_dir / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "topics.json").write_text(
        json.dumps(result.get("topics", {}), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "taxonomy.json").write_text(
        json.dumps(result.get("taxonomy", {}), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "care_analysis.json").write_text(
        json.dumps(result.get("care_analysis", {}), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md = (
        f"# Korpus-Analyse: {corpus_name}\n\n"
        f"**Dokumente:** {result.get('doc_count', 0)}\n"
        f"**Zeichen:** {result.get('total_chars', 0):,}\n"
        f"**Wörter:** {stats.get('total_words', 0):,}\n\n"
        f"## Statistik\n\n"
        f"- Wörter: {stats.get('total_words', 0):,}\n"
        f"- Eindeutige Wörter: {stats.get('total_unique_words', 0):,}\n\n"
        f"## Voyant Link\n\n"
        f"{result.get('voyant_url', '—')}\n"
    )
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    logger.info(f"[Agent D] Ergebnisse gespeichert: {out_dir}")