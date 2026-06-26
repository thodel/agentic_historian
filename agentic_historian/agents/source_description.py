"""
agents/source_description.py — Agent B: Wissenschaftliche Quellenerschliessung
Klassifikation, Keywords, Care-Flag, Inhaltsbeschreibung.
"""

import json
from pathlib import Path
from typing import Optional

from loguru import logger

import config
from utils import gpustack_client as gs

SYSTEM = (
    "Du bist ein historisch geschulter Wissenschaftler, spezialisiert auf "
    "spätmittelalterliche Verwaltungsquellen (Schweiz, 14.–16. Jh.). "
    "Deine Aufgabe: vollständige Quellenerschliessung nach archiwissenschaftlichen "
    "Standards. Sei präzise, nutze kontrolliertes Vokabular."
)


def describe(doc_id: str, transcription: str, image_path: Optional[str] = None) -> dict:
    """
    Erstellt eine vollständige Quellenbeschreibung.
    Nutzt Agent A's Transkription + optional das Originalbild.
    """
    logger.info(f"[Agent B] Verarbeite: {doc_id}")

    # 1) Klassifikation
    klassifikation = _klassifikation(transcription)

    # 2) Inhaltliche Beschreibung
    inhalt = _inhalt(transcription)

    # 3) Visuelle Beschreibung (nur mit Bild)
    visuell = _visuell(image_path, transcription) if image_path else "—"

    # 4) Formale Analyse
    formal = _formal(transcription)

    # 5) Care-Flag
    care = _care_flag(transcription)

    result = {
        "doc_id": doc_id,
        "klassifikation": klassifikation,
        "inhalt": inhalt,
        "visuell": visuell,
        "formal": formal,
        "care_flag": care,
    }

    # Speichern
    _save(doc_id, result)
    logger.info(f"[Agent B] Fertig: {doc_id}")
    return result


def _klassifikation(transcription: str) -> dict:
    prompt = (
        SYSTEM + "\n\n" +
        "Klassifiziere dieses Dokument:\n\n" + transcription[:3000] + "\n\n" +
        "Antworte als JSON mit: {type, sprache, schrift_periode, bemerkungen}"
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=500)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Agent B] Klassifikation fehlgeschlagen: {e}")
        return {"type": "unbekannt", "sprache": "unbekannt", "schrift_periode": "unbekannt", "bemerkungen": ""}


def _inhalt(transcription: str) -> dict:
    prompt = (
        SYSTEM + "\n\n" +
        "Beschreibe den Inhalt dieses Dokuments:\n\n" + transcription[:3000] + "\n\n" +
        "Antworte als JSON mit: {zusammenfassung, personen, orte, daten, "
        "verwaltungshandlungen, keywords}"
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=1000)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Agent B] Inhalt fehlgeschlagen: {e}")
        return {"zusammenfassung": "", "personen": [], "orte": [], "daten": [], "verwaltungshandlungen": [], "keywords": []}


def _visuell(image_path: str, transcription: str = "") -> str:
    prompt = (
        "Beschreibe das äussere Erscheinungsbild des Dokuments: "
        "Schrifttyp, Layout, Tintenqualität, Siegel, Wasserzeichen, Beschädigungen. "
        "Antworte auf Deutsch."
    )
    try:
        return gs.chat_vision(prompt=prompt, image_source=image_path, max_tokens=500)
    except Exception as e:
        logger.warning(f"[Agent B] Visuell fehlgeschlagen: {e}")
        return "—"


def _formal(transcription: str) -> dict:
    prompt = (
        SYSTEM + "\n\n" +
        "Formale Analyse:\n\n" + transcription[:3000] + "\n\n" +
        "Antworte als JSON mit: {dialektraum, formularphraseologie, bemerkenswortes_vokabular}"
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=800)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Agent B] Formal fehlgeschlagen: {e}")
        return {"dialektraum": "", "formularphraseologie": "", "bemerkenswertes_vokabular": []}


def _care_flag(transcription: str) -> dict:
    prompt = (
        SYSTEM + "\n\n" +
        "Prüfe ob dieses Dokument Care-relevante Inhalte hat.\n\n" +
        transcription[:3000] + "\n\n" +
        "Antworte als JSON: {is_care_related: bool, care_context: str, "
        "care_types: [string], beteiligte: [string]}"
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=600)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Agent B] Care-Flag fehlgeschlagen: {e}")
        return {"is_care_related": False, "care_context": "", "care_types": [], "beteiligte": []}


def _save(doc_id: str, result: dict):
    """Speichert Quellenbeschreibung als Markdown."""
    out_path = config.DESCRIPTIONS_DIR / f"{doc_id}.md"
    r = result

    md = (
        f"# Quellenbeschreibung: {doc_id}\n\n"
        f"## Klassifikation\n\n"
        f"- **Typ:** {r['klassifikation'].get('type', '—')}\n"
        f"- **Sprache:** {r['klassifikation'].get('sprache', '—')}\n"
        f"- **Schriftperiode:** {r['klassifikation'].get('schrift_periode', '—')}\n"
        f"- **Bemerkungen:** {r['klassifikation'].get('bemerkungen', '—')}\n\n"
        f"## Inhalt\n\n"
        f"**Zusammenfassung:** {r['inhalt'].get('zusammenfassung', '—')}\n\n"
        f"**Personen:** {', '.join(r['inhalt'].get('personen', []) or ['—'])}\n"
        f"**Orte:** {', '.join(r['inhalt'].get('orte', []) or ['—'])}\n"
        f"**Daten:** {', '.join(r['inhalt'].get('daten', []) or ['—'])}\n"
        f"**Verwaltungshandlungen:** {', '.join(r['inhalt'].get('verwaltungshandlungen', []) or ['—'])}\n\n"
        f"## Care-Flag\n\n"
        f"- **Care-relevant:** {'Ja' if r['care_flag'].get('is_care_related') else 'Nein'}\n"
        f"- **Kontext:** {r['care_flag'].get('care_context', '—')}\n\n"
        f"## Formale Analyse\n\n"
        f"- **Dialektraum:** {r['formal'].get('dialektraum', '—')}\n"
        f"- **Bemerkenswertes Vokabular:** {', '.join(r['formal'].get('bemerkenswertes_vokabular', []) or ['—'])}\n"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)