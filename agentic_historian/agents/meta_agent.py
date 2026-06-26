"""
agents/meta_agent.py — Agent E: Meta Agent
Ressourcen-Tracking, Fehlerprotokoll, Verbesserungsvorschläge.
"""

import json
from datetime import datetime
from pathlib import Path

from loguru import logger

import config
from utils import gpustack_client as gs

SYSTEM = (
    "Du bist ein Systemadministrator und Optimierungsexperte für eine "
    "KI-Pipeline. Analysiere Systemstatistiken und mache konkrete "
    "Verbesserungsvorschläge auf Deutsch."
)


def generate_report() -> dict:
    """Erstellt den Meta-Report."""
    logger.info("[Agent E] Generiere Meta-Report")

    # 1) Token-Verbrauch schätzen
    token_usage = _estimate_token_usage()

    # 2) Storage-Übersicht
    storage = _storage_overview()

    # 3) Fehler-Protokoll
    errors = _error_log()

    # 4) Verbesserungsvorschläge (LLM)
    improvements = _improvements(token_usage, storage, errors)

    result = {
        "generated_at": datetime.now().isoformat(),
        "token_usage": token_usage,
        "storage": storage,
        "errors": errors,
        "improvements": improvements,
    }

    # Speichern
    _save(result)
    logger.info("[Agent E] Meta-Report fertig")
    return result


def _estimate_token_usage() -> dict:
    """Schätzt Token-Verbrauch anhand der Ausgabedateien."""
    total_chars = 0
    file_count = 0
    for d in [config.TRANSCRIPTIONS_DIR, config.DESCRIPTIONS_DIR, config.OUTPUTS_DIR]:
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file() and f.stat().st_size > 0:
                    total_chars += f.stat().st_size
                    file_count += 1

    # Grobe Schätzung: 1 Token ≈ 4 Zeichen
    estimated_tokens = total_chars // 4
    # Kosten schätzen: ~$0.001/1K token (GPUStack — sehr grob)
    estimated_cost_usd = estimated_tokens / 1_000_000

    return {
        "total_files": file_count,
        "total_chars": total_chars,
        "estimated_tokens": estimated_tokens,
        "estimated_cost_usd": round(estimated_cost_usd, 4),
    }


def _storage_overview() -> dict:
    """Gesamtgrösse aller Ausgabe-Verzeichnisse."""
    dirs = [
        config.TRANSCRIPTIONS_DIR,
        config.DESCRIPTIONS_DIR,
        config.OUTPUTS_DIR,
        config.KH_DIR,
    ]
    total_mb = 0
    breakdown = {}
    for d in dirs:
        size_mb = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / (1024 * 1024)
        total_mb += size_mb
        breakdown[d.name] = round(size_mb, 3)

    return {"total_mb": round(total_mb, 3), "breakdown": breakdown}


def _error_log() -> list:
    log_path = config.META_LOG_PATH
    if log_path.exists():
        try:
            return json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _improvements(token_usage: dict, storage: dict, errors: list) -> str:
    prompt = (
        SYSTEM + "\n\n" +
        "Analysiere folgende Systemstatistiken und mache konkrete "
        "Verbesserungsvorschläge (auf Deutsch):\n\n" +
        f"Token-Nutzung: {json.dumps(token_usage, indent=2)}\n\n"
        f"Storage: {json.dumps(storage, indent=2)}\n\n"
        f"Fehler: {json.dumps(errors, indent=2)}\n\n" +
        "Antworte mit maximal 5 konkreten, umsetzbaren Vorschlägen."
    )
    try:
        return gs.chat_text(prompt, system=None, max_tokens=800)
    except Exception as e:
        logger.warning(f"[Agent E] Verbesserungsvorschläge fehlgeschlagen: {e}")
        return "—"


def _save(result: dict):
    """Speichert Meta-Report und Log."""
    # Markdown
    r = result
    md = (
        f"# Meta-Report — {r['generated_at']}\n\n"
        f"## Token-Nutzung\n\n"
        f"- Dateien: {r['token_usage']['total_files']}\n"
        f"- Geschätzte Tokens: {r['token_usage']['estimated_tokens']:,}\n"
        f"- Geschätzte Kosten: ${r['token_usage']['estimated_cost_usd']}\n\n"
        f"## Storage\n\n"
        f"- Gesamt: {r['storage']['total_mb']} MB\n"
    )
    for name, size in r['storage']['breakdown'].items():
        md += f"  - {name}: {size} MB\n"

    md += f"\n## Verbesserungsvorschläge\n\n{r['improvements']}\n"

    config.META_REPORT_PATH.write_text(md, encoding="utf-8")
    config.META_LOG_PATH.write_text("[]", encoding="utf-8")
    logger.info(f"[Agent E] Report gespeichert: {config.META_REPORT_PATH}")