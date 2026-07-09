with open('/home/dh/agentic_historian/agentic_historian/agent_a/model_selector.py') as f:
    lines = f.readlines()

# Fix line 426 (0-indexed): docstring needs 8 spaces indentation
# Line 420: "    def select_kraken_model(" — add 4 more spaces
# Lines 421-425: "    criteria/    */    top_k/    require/    )" — add 4 more spaces
# Line 426: docstring first line — add 4 more spaces

# Find the line indices
for i, l in enumerate(lines):
    if 'def select_kraken_model' in l and i > 400:
        print(f"Found def at line {i+1}: {repr(l)}")
        # Fix this line (4 spaces -> 8 spaces)
        if l.startswith('    def select_kraken_model'):
            lines[i] = '        ' + l[4:]
        # Next 5 lines (indices i+1 to i+5): criteria, *, top_k, require, )
        for j in range(i+1, i+6):
            if lines[j].startswith('        '):
                lines[j] = '            ' + lines[j][8:]
            elif lines[j].startswith('    '):
                lines[j] = '            ' + lines[j][4:]
        # Docstring first line
        if i+6 < len(lines):
            print(f"Line {i+7}: {repr(lines[i+6])}")
            if lines[i+6].startswith('    '):
                lines[i+6] = '        ' + lines[i+6][4:]
        break

with open('/home/dh/agentic_historian/agentic_historian/agent_a/model_selector.py', 'w') as f:
    f.writelines(lines)

print("Written. Verifying...")
with open('/home/dh/agentic_historian/agentic_historian/agent_a/model_selector.py') as f:
    lines2 = f.readlines()
for i in range(418, 435):
    print(f"Line {i+1}: {repr(lines2[i])}")