#!/usr/bin/env python3
with open('/home/dh/.openclaw/workspace/agentic_historian/orchestrator.py', 'rb') as f:
    data = f.read()
# Find the location and add a blank line after _append_errors_to_log
target = b'_append_errors_to_log(doc_id, ctx.errors)\n#'
replacement = b'_append_errors_to_log(doc_id, ctx.errors)\n\n#'
data = data.replace(target, replacement)
with open('/home/dh/.openclaw/workspace/agentic_historian/orchestrator.py', 'wb') as f:
    f.write(data)
print('Done')