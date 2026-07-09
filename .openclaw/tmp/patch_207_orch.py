with open('/home/dh/agentic_historian/agentic_historian/orchestrator.py') as f:
    content = f.read()

# ── 1. Phase 3: add source_json param + use from_agent_b_and_json ─────────────
# Find:
#     source_desc_text = ctx.description.get("source_description", "")
#     if source_desc_text:
#         kraken_results = _rerun_kraken_with_model_selection(
#             image_path=img,
#             source_description=source_desc_text,
#             lang=lang,
#         )
# Replace with:
#     source_desc_text = ctx.description.get("source_description", "")
#     source_json = ctx.description.get("source_json")
#     if source_desc_text or source_json:
#         criteria = SourceCriteria.from_agent_b_and_json(source_desc_text, source_json)
#         kraken_results = _rerun_kraken_with_model_selection(
#             image_path=img,
#             source_description=source_desc_text,
#             lang=lang,
#             criteria=criteria,
#         )

old_phase3 = """            source_desc_text = ctx.description.get("source_description", "")
            if source_desc_text:
                kraken_results = _rerun_kraken_with_model_selection(
                    image_path=img,
                    source_description=source_desc_text,
                    lang=lang,
                )"""

new_phase3 = """            source_desc_text = ctx.description.get("source_description", "")
            source_json = ctx.description.get("source_json")
            if source_desc_text or source_json:
                criteria = SourceCriteria.from_agent_b_and_json(source_desc_text, source_json)
                kraken_results = _rerun_kraken_with_model_selection(
                    image_path=img,
                    source_description=source_desc_text,
                    lang=lang,
                    criteria=criteria,
                )"""

if old_phase3 in content:
    content = content.replace(old_phase3, new_phase3)
    print("Phase 3 block patched OK")
else:
    print("Phase 3 pattern not found!")
    idx = content.find("source_desc_text = ctx.description")
    print(repr(content[idx:idx+400]))

# ── 2. RunState block: use from_agent_b_and_json instead of from_agent_b ──────
# We also need to import SourceCriteria earlier so it's available in Phase 3.
# Currently SourceCriteria is imported only inside the RunState block.
# Move that import to the top-level imports section.

# First fix the RunState criteria block:
old_rstate = '''        state = RunState.load_or_new(doc_id)
        desc_text = (ctx.description or {}).get("source_description", "")
        if desc_text:
            crit = SourceCriteria.from_agent_b(desc_text)
            for k, v in {
                "script": crit.script,
                "lang": crit.lang or lang,
                "century": crit.century,
                "document_type": crit.document_type,
            }.items():'''

new_rstate = '''        state = RunState.load_or_new(doc_id)
        desc_text = (ctx.description or {}).get("source_description", "")
        src_json = (ctx.description or {}).get("source_json")
        if desc_text or src_json:
            crit = SourceCriteria.from_agent_b_and_json(desc_text, src_json)
            for k, v in {
                "script": crit.script,
                "lang": crit.lang or lang,
                "century": crit.century,
                "document_type": crit.document_type,
            }.items():'''

if old_rstate in content:
    content = content.replace(old_rstate, new_rstate)
    print("RunState block patched OK")
else:
    print("RunState pattern not found!")
    idx = content.find("desc_text = (ctx.description")
    print(repr(content[idx:idx+400]))

# ── 3. Move SourceCriteria import to top-level (needed in Phase 3) ─────────────
# Currently: "from agent_a.model_selector import SourceCriteria" is only inside
# the RunState block. Add it to the top-level imports.
old_import = "from agent_a.model_selector import select_kraken_model, SourceCriteria"
new_import = "from agent_a.model_selector import select_kraken_model, SourceCriteria"

# Check if it's already at top level
if "from agent_a.model_selector import SourceCriteria" in content:
    print("SourceCriteria import already at top level")
else:
    # It's only inside the RunState block — replace that specific occurrence
    # The import in the RunState block is:
    # "from agent_a.model_selector import SourceCriteria"
    # (no select_kraken_model, only SourceCriteria)
    old_inline_import = "        from agent_a.model_selector import SourceCriteria"
    new_inline_import = "from agent_a.model_selector import SourceCriteria  # noqa: E402"
    if old_inline_import in content:
        content = content.replace(old_inline_import, new_inline_import, 1)
        print("SourceCriteria import moved to top level OK")
    else:
        print("Inline import pattern not found!")
        # Just check what's there
        idx = content.find("from agent_a.model_selector import SourceCriteria")
        print(repr(content[idx:idx+80]))

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
    print(f"Syntax error: {result.stderr}")