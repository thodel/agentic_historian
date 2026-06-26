"""Agent C — Entity Extraction via LLM.

Uses a local LLM (via GPUStack VLM endpoint) for NER to extract:
  PERSON, PLACE, ORG, CARE_ACTOR, ROLE, DATE
Links entities to the Knowledge Hub and Wikidata/GND where possible.
"""

import os
import logging
import json
import requests
from pathlib import Path
from typing import Optional

from knowledge_hub.hub import get_hub

logger = logging.getLogger(__name__)

VLM_ENDPOINT = os.getenv("VLM_ENDPOINT", "https://gpustack.unibe.ch/v1")
VLM_API_KEY = os.getenv("VLM_API_KEY", "")
VLM_MODEL = os.getenv("VLM_MODEL", "internvl3-8b-instruct")

ENTITY_TYPES = ["PERSON", "PLACE", "ORG", "CARE_ACTOR", "ROLE", "DATE"]


def _call_llm(prompt: str) -> str:
    """Call the GPUStack VLM for entity extraction."""
    payload = {
        "model": VLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,
    }
    headers = {"Authorization": f"Bearer {VLM_API_KEY}", "Content-Type": "application/json"}
    response = requests.post(f"{VLM_ENDPOINT}/chat/completions", json=payload, headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def extract_entities(text: str, historical_context: str = "") -> list[dict]:
    """Extract named entities from a transcription using the local VLM.

    Uses NER4all prompting strategy: provide historical context and a historian
    persona to improve extraction quality on historical documents.
    """
    hub = get_hub()
    entity_list = ", ".join(ENTITY_TYPES)
    persona = (
        "You are an experienced historian specialising in early modern European documents. "
        "You pay attention to names, places, dates, institutions, and care-related roles. "
        "Spelling variants and incomplete names are common in historical texts — extract what is most likely intended."
    )
    context_block = f"\nHistorical context:\n{historical_context}\n" if historical_context else ""
    prompt = (
        f"{persona}{context_block}\n"
        f"Extract all entities of type: {entity_list} from the following text.\n"
        "Return ONLY a valid JSON array of objects with keys: type, value.\n"
        "Example output: [{\"type\": \"PERSON\", \"value\": \"Maria Müller\"}, {\"type\": \"DATE\", \"value\": \"1843\"}]\n\n"
        f"Text:\n{text[:4000]}\n"
    )
    raw = _call_llm(prompt)
    try:
        entities = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Entity extraction returned non-JSON, falling back to empty list.")
        entities = []
    # Enrich with hub lookups
    for ent in entities:
        if ent["type"] == "PERSON":
            match = hub.find_person(ent["value"])
            if match:
                ent["wikidata_id"] = match.wikidata_id
                ent["gnd_id"] = match.gnd_id
        elif ent["type"] == "PLACE":
            match = hub.find_place(ent["value"])
            if match:
                ent["wikidata_id"] = match.wikidata_id
                ent["gnd_id"] = match.gnd_id
    return entities


def process_transcription(
    transcription_path: Path,
    output_path: Optional[Path] = None,
    historical_context: str = "",
) -> dict:
    """Read a transcription file, extract entities, and optionally save results."""
    text = transcription_path.read_text(encoding="utf-8")
    entities = extract_entities(text, historical_context=historical_context)
    result = {"source": str(transcription_path), "entities": entities}
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Saved entity extraction: {output_path}")
    return result

