#!/usr/bin/env python3
"""Inject HBLS nginx location blocks into tei.dh.unibe.ch config.

Fixes: POST to /mcp/hbls/messages/ returns 405 Method Not Allowed.
Root cause: no nginx location blocks existed for HBLS (port 8003).
"""

import re, sys, subprocess, datetime

CONFIG_PATH = "/etc/nginx/sites-available/tei.dh.unibe.ch"

NEW_LOCATIONS = """
# ── HBLS MCP ────────────────────────────────────────
location /mcp/hbls/sse {
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding on;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass http://127.0.0.1:8003/sse;
}
location /mcp/hbls/messages {
    proxy_http_version 1.1;
    proxy_set_header Connection '';
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding on;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_pass http://127.0.0.1:8003/messages;
}
"""

def main():
    backup_suffix = f".bak.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"

    with open(CONFIG_PATH) as f:
        original = f.read()

    if "/mcp/hbls/sse" in original:
        print("HBLS location blocks already exist — nothing to do.")
        sys.exit(0)

    # Insert just before the catch-all `location /` in the main server block
    marker = "\n    location / {\n"
    insertion = NEW_LOCATIONS + marker

    new_config = original.replace(marker, insertion, 1)

    # Backup
    backup_path = CONFIG_PATH + backup_suffix
    with open(backup_path, "w") as f:
        f.write(original)
    print(f"Backup written: {backup_path}")

    with open(CONFIG_PATH, "w") as f:
        f.write(new_config)
    print(f"Config written: {CONFIG_PATH}")

    # Test & reload
    result = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    print(f"nginx -t: {result.stdout}{result.stderr}")
    if result.returncode == 0:
        subprocess.run(["nginx", "-s", "reload"])
        print("nginx reloaded.")
    else:
        print("ERROR — not reloading. Fix config manually.")
        sys.exit(1)

if __name__ == "__main__":
    main()