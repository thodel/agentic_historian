with open('/home/dh/agentic_historian/agentic_historian/orchestrator.py') as f:
    content = f.read()

# ── 1. Update _rerun_kraken_with_model_selection signature to accept source_json ─
old_sig = """def _rerun_kraken_with_model_selection(
    image_path: Path,
    source_description: str,
    lang: str = "de",
) -> dict:
    \"\"\"
    Phase-3-Step: Bild + Agent-B-Beschreibung → kraken-Modellauswahl → Remote OCR.

    Returns a dict with kraken_transcription, party_transcription,
    kraken_model, party_model, and any errors.
    \"\"\""""

new_sig = """def _rerun_kraken_with_model_selection(
    image_path: Path,
    source_description: str,
    lang: str = "de",
    source_json: Optional[dict] = None,
) -> dict:
    \"\"\"
    Phase-3-Step: Bild + Agent-B-Beschreibung → kraken-Modellauswahl → Remote OCR.

    Returns a dict with kraken_transcription, party_transcription,
    kraken_model, party_model, and any errors.
    \"\"\""""

if old_sig in content:
    content = content.replace(old_sig, new_sig)
    print("Signature updated OK")
else:
    print("Signature pattern not found!")
    idx = content.find("def _rerun_kraken_with_model_selection")
    print(repr(content[idx:idx+300]))

# ── 2. Update the internal SourceCriteria call inside _rerun_kraken_with_model_selection ─
old_criteria = "    criteria = SourceCriteria.from_agent_b(source_description)"
new_criteria = "    criteria = SourceCriteria.from_agent_b_and_json(source_description, source_json)"

if old_criteria in content:
    content = content.replace(old_criteria, new_criteria, 1)
    print("Internal SourceCriteria call updated OK")
else:
    print("Internal SourceCriteria pattern not found!")

# ── 3. Update Phase 3 call site to pass source_json ──────────────────────────
old_call = """                kraken_results = _rerun_kraken_with_model_selection(
                    image_path=img,
                    source_description=source_desc_text,
                    lang=lang,
                )"""

new_call = """                kraken_results = _rerun_kraken_with_model_selection(
                    image_path=img,
                    source_description=source_desc_text,
                    lang=lang,
                    source_json=ctx.description.get("source_json"),
                )"""

if old_call in content:
    content = content.replace(old_call, new_call, 1)
    print("Phase 3 call site updated OK")
else:
    print("Phase 3 call site pattern not found!")
    idx = content.find("source_desc_text = ctx.description.get")
    print(repr(content[idx:idx+300]))

# ── 4. Update the condition to also check source_json ────────────────────────
old_cond = "            if source_desc_text:"
new_cond = "            if source_desc_text or source_json:"

if old_cond in content:
    content = content.replace(old_cond, new_cond, 1)
    print("Phase 3 condition updated OK")
else:
    print("Phase 3 condition pattern not found!")

# ── 5. Update RunState block ────────────────────────────────────────────────
old_rstate = '''        state = RunState.load_or_new(doc_id)
        desc_text = (ctx.description or {}).get("source_description", "")
        if desc_text:
            crit = SourceCriteria.from_agent_b(desc_text)'''

new_rstate = '''        state = RunState.load_or_new(doc_id)
        desc_text = (ctx.description or {}).get("source_description", "")
        src_json = (ctx.description or {}).get("source_json")
        if desc_text or src_json:
            crit = SourceCriteria.from_agent_b_and_json(desc_text, src_json)'''

if old_rstate in content:
    content = content.replace(old_rstate, new_rstate, 1)
    print("RunState block updated OK")
else:
    print("RunState pattern not found!")
    idx = content.find("desc_text = (ctx.description")
    print(repr(content[idx:idx+300]))

# ── 6. Move SourceCriteria import to top-level (above the function def) ──────
# Currently it appears only inside the RunState block:
# "        from agent_a.model_selector import SourceCriteria"
# Change it to be at module level (after other imports)

old_inline_import = "        from agent_a.model_selector import SourceCriteria"
new_inline_import = "from agent_a.model_selector import SourceCriteria  # noqa: E402"

if old_inline_import in content:
    content = content.replace(old_inline_import, new_inline_import, 1)
    print("SourceCriteria import moved to module level OK")
else:
    # It might already be imported or use a different pattern
    print("Inline import pattern not found (may already be at top level)")

with open('/home/dh/agentic_historian/agentic_historian/orchestrator.py', 'w') as f:
    f.write(content)

# Verify syntax
import subprocess
result = subprocess.run(
    ["python3", "-m", "py_compile",
     "/home/dh/agentic_historian/agentic_historian/orchestrator.py"],
    capture_output=True, text=True
)
if result.returncode == 0:
    print("orchestrator.py syntax OK")
else:
    print(f"Syntax error: {result.stderr[:500]}")