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
from utils.metrics import get_metrics

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
    """
    Token + GPU-time tracking based on per-run metrics.
    No USD — local GPUStack has no per-token cost.
    """
    m = get_metrics()
    runs = m.runs

    # Wall-clock time per agent
    agent_times = {}
    for r in runs:
        agent_times[r.agent] = agent_times.get(r.agent, 0) + r.wall_clock_ms

    # Token totals
    total_prompt = m.total_prompt_tokens()
    total_completion = m.total_completion_tokens()
    total_tokens = total_prompt + total_completion

    # File count from output dirs (for context)
    file_count = 0
    total_chars = 0
    for d in [config.TRANSCRIPTIONS_DIR, config.DESCRIPTIONS_DIR, config.OUTPUTS_DIR]:
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file() and f.stat().st_size > 0:
                    total_chars += f.stat().st_size
                    file_count += 1

    return {
        "session_id": m.session_id,
        "total_files_in_outputs": file_count,
        "total_chars_in_outputs": total_chars,
        "total_runs": len(runs),
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_tokens,
        "total_wall_clock_ms": m.total_wall_clock_ms(),
        "per_agent_wall_clock_ms": agent_times,
        "note": "GPUStack is local — no per-token cost. Track wall-clock and tokens for resource monitoring.",
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
    r = result
    tu = r.get('token_usage', {})

    md = (
        f"# Meta-Report — {r['generated_at']}\n\n"
        f"## GPU-Stack Tracking (kein USD — lokaler Stack)\n\n"
        f"Session: {tu.get('session_id', 'unbekannt')}\n"
        f"Runs: {tu.get('total_runs', 0)}\n"
        f"Prompt Tokens: {tu.get('total_prompt_tokens', 0):,}\n"
        f"Completion Tokens: {tu.get('total_completion_tokens', 0):,}\n"
        f"Total Tokens: {tu.get('total_tokens', 0):,}\n"
        f"Wall-Clock Time: {tu.get('total_wall_clock_ms', 0):,} ms\n"
    )

    # Per-agent wall-clock
    per_agent = tu.get('per_agent_wall_clock_ms', {})
    if per_agent:
        md += "\n### Per-Agent Wall-Clock\n"
        for agent, ms in per_agent.items():
            md += f"- {agent}: {ms:,} ms\n"

    # Storage
    storage = r.get('storage', {})
    md += (
        f"\n## Storage\n\n"
        f"Dateien in Outputs: {tu.get('total_files_in_outputs', 0)}\n"
        f"Total: {storage.get('total_mb', 0)} MB\n"
    )
    for name, size in storage.get('breakdown', {}).items():
        md += f"  - {name}: {size} MB\n"

    md += f"\n## Verbesserungsvorschläge\n\n{r.get('improvements', '—')}\n"

    config.META_REPORT_PATH.write_text(md, encoding="utf-8")
    # Reset metrics log after reporting
    config.META_LOG_PATH.write_text("[]", encoding="utf-8")
    logger.info(f"[Agent E] Report gespeichert: {config.META_REPORT_PATH}")