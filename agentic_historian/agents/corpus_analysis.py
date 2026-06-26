"""
agents/corpus_analysis.py — Agent D: Corpus Analysis
Statistiken, Topic Modelling, Soziale Taxonomien, Care-Analyse, Voyant Link.
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

    # 2) Topics
    topics = _topics(combined)

    # 3) Soziale Taxonomien
    taxonomy = _taxonomy(combined)

    # 4) Care-Analyse
    care = _care_analysis(combined)

    result = {
        "corpus_name": corpus_name,
        "doc_count": len(transcriptions),
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


def _stats(text: str) -> dict:
    words = text.split()
    word_freq = {}
    for w in words:
        w_clean = w.lower().strip(".,;:!?\"'")
        word_freq[w_clean] = word_freq.get(w_clean, 0) + 1

    # Top 20 häufige Wörter (ohne Stoppwörter)
    stopwords = {"der", "die", "das", "und", "in", "von", "mit", "zu", "den", "für",
                 "ist", "auf", "nicht", "auch", "es", "an", "werden", "aus", "nach"}
    filtered = {w: c for w, c in word_freq.items() if w not in stopwords and len(w) > 2}
    top_words = sorted(filtered.items(), key=lambda x: -x[1])[:20]

    return {
        "total_tokens": len(words),
        "unique_tokens": len(word_freq),
        "top_words": dict(top_words),
    }


def _topics(text: str) -> dict:
    prompt = (
        SYSTEM + "\n\n" +
        "Identifiziere 5–10 Topics in diesem Textkorpus. "
        "Gib pro Topic einen Namen und die zugehörigen Schlüsselwörter.\n\n" +
        text[:5000] + "\n\n" +
        "Antworte als JSON: {topics: [{name, keywords: []}]}"
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=1000)
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
        text[:5000] + "\n\n" +
        "Antworte als JSON: {taxonomies: [{term, count, contexts: []}]}"
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=1000)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Agent D] Taxonomy fehlgeschlagen: {e}")
        return {"taxonomies": []}


def _care_analysis(text: str) -> dict:
    prompt = (
        SYSTEM + "\n\n" +
        "Analysiere Care-relevante Inhalte in diesem Korpus: "
        "Care-Instanzen, Arrangements, Entlohnung, Gender, Lebensläufe.\n\n" +
        text[:5000] + "\n\n" +
        "Antworte als JSON: {care_instances: [], patterns: [], gender_aspects: []}"
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=1000)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Agent D] Care-Analyse fehlgeschlagen: {e}")
        return {"care_instances": [], "patterns": [], "gender_aspects": []}


def _voyant_url(text: str, corpus_name: str) -> str:
    """Erstellt Voyant-Tools-Link mit eingebettetem Korpus."""
    try:
        corpus_id = corpus_name.lower().replace(" ", "_")
        # Kurzfassung: nur erste 50k Zeichen
        snippet = text[:50000]
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
    out_dir = config.OUTPUTS_DIR / f"corpus_{corpus_name.replace(' ', '_')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "stats.json").write_text(
        json.dumps(result.get("stats", {}), ensure_ascii=False, indent=2), encoding="utf-8"
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
        f"**Dokumente:** {result.get('doc_count', 0)}\n\n"
        f"## Statistik\n\n"
        f"- Tokens: {result.get('stats', {}).get('total_tokens', 0)}\n"
        f"- Eindeutige Tokens: {result.get('stats', {}).get('unique_tokens', 0)}\n\n"
        f"## Voyant Link\n\n"
        f"{result.get('voyant_url', '—')}\n"
    )
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    logger.info(f"[Agent D] Ergebnisse gespeichert: {out_dir}")