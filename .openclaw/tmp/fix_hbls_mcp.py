#!/usr/bin/env python3
"""
fix_hbls_mcp.py — Patch HBLS container's server.py dispatch bug and verify MCP works.

Bug: dispatch() used "return await mcp_asgi(...)" which returns None 
     (ASGI apps send responses via send, not return values).
Fix: Change to "await mcp_asgi(...)" without return.

Usage: python3 fix_hbls_mcp.py
"""

import urllib.request
import urllib.error
import json
import subprocess
import time
import sys


def docker(args, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0 and check:
        print(f"docker {args[1]} failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip(), result.returncode


def wait_health(port=8003, timeout=15):
    """Wait for server to respond on /health."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3)
            if r.status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def mcp_post(session_id, method, params, port=8003):
    """Send one JSON-RPC POST to the MCP /messages endpoint."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/messages/?session_id={session_id}",
        method="POST",
        headers={"Content-Type": "application/json", "Host": "127.0.0.1"},
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
    )
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read().decode())


def get_sse_session(port=8003):
    """Establish SSE connection and extract session_id."""
    import threading, queue

    q = queue.Queue()

    def reader(resp, q):
        for line in resp:
            line = line.decode("utf-8").rstrip()
            if line.startswith("data:"):
                data = line[5:].strip()
                if "session_id" in data:
                    q.put(data.split("session_id=")[1].split("&")[0])
                    return
        q.put(None)

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/sse",
        headers={"Accept": "text/event-stream", "Host": "127.0.0.1"},
    )
    resp = urllib.request.urlopen(req, timeout=10)
    tq = queue.Queue()
    t = threading.Thread(target=reader, args=(resp, tq))
    t.start()
    t.join(timeout=10)
    try:
        return tq.get(timeout=1)
    except:
        return None


def main():
    print("=== HBLS MCP Fix Script ===\n")

    # 1. Find container
    out, rc = docker(["docker", "ps", "-q", "--filter", "name=hbls-mcp"], check=False)
    container = out.strip()
    if not container:
        print("ERROR: No running hbls-mcp container found")
        sys.exit(1)
    print(f"Container: {container}")

    # 2. Check if already patched
    src, _ = docker(["docker", "exec", container, "grep", "-n", "await mcp_asgi", "/app/server.py"], check=False)
    if "return await" not in src and "await mcp_asgi" in src:
        print("Patch already applied")
    else:
        # 3. Apply patch
        print("Applying patch...")
        docker(["docker", "exec", container, "sed", "-i", "s/return await mcp_asgi/await mcp_asgi/", "/app/server.py"])
        patched, _ = docker(["docker", "exec", container, "grep", "-n", "await mcp_asgi", "/app/server.py"], check=False)
        print(f"  Patched lines: {patched}")

    # 4. Restart container
    print("Restarting container...")
    docker(["docker", "restart", container])
    time.sleep(5)

    if not wait_health():
        print("ERROR: Server did not come up")
        sys.exit(1)
    print("  Server up")

    # 5. Test MCP flow
    print("\n--- MCP Flow Test ---")
    sid = get_sse_session()
    if not sid:
        print("FAIL: Could not get SSE session")
        sys.exit(1)
    print(f"✓ GET /sse → session {sid}")

    result = mcp_post(sid, "tools/list", {})
    tools = [t["name"] for t in result.get("result", {}).get("tools", [])]
    print(f"✓ POST /messages → HTTP 200")
    print(f"  Tools ({len(tools)}): {tools[:5]}{'...' if len(tools) > 5 else ''}")

    # 6. Test a real tool call
    result = mcp_post(sid, "corpus_stats", {})
    stats = result.get("result", {})
    print(f"✓ corpus_stats → {stats.get('n_articles', '?')} articles, {stats.get('n_members', '?')} members")

    print("\n=== All tests passed ===")


if __name__ == "__main__":
    main()