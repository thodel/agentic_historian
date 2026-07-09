#!/usr/bin/env python3
"""Test HBLS MCP search_bio / search_persons."""
import json, urllib.request, urllib.error, threading, queue, time

BASE = "http://127.0.0.1:8003"

def sse_session():
    """Open SSE, return (sid, msg_queue)."""
    q = queue.Queue()
    req = urllib.request.Request(
        f"{BASE}/sse",
        headers={"Accept": "text/event-stream", "Host": "127.0.0.1"},
    )
    resp = urllib.request.urlopen(req, timeout=30)

    def read_lines():
        try:
            # readline() is line-oriented, perfect for SSE
            while True:
                line = resp.readline()
                if not line:
                    break
                line = line.decode().rstrip()
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    try:
                        obj = json.loads(raw)
                        q.put(("json", obj))
                    except Exception:
                        q.put(("text", raw))
                elif line.startswith("event:"):
                    q.put(("event", line[6:].strip()))
        except Exception as e:
            q.put(("error", str(e)))
        finally:
            resp.close()
            q.put(("done", None))

    t = threading.Thread(target=read_lines, daemon=True)
    t.start()

    # Collect events until we have the session ID
    sid = None
    while True:
        try:
            tag, data = q.get(timeout=10)
            if tag == "json" and isinstance(data, dict) and data.get("event") == "endpoint":
                endpoint = data.get("data", "")
                if "session_id=" in endpoint:
                    sid = endpoint.split("session_id=")[1].split("&")[0]
                    break
            elif tag == "text" and "session_id=" in str(data):
                sid = str(data).split("session_id=")[1].split("&")[0]
                break
        except queue.Empty:
            break

    return sid, q

print("Getting SSE session...")
sid, q = sse_session()
print(f"SID: {sid}\n")

if not sid:
    print("FAIL: no session")
    exit(1)

def post_and_read(method, params=None):
    payload = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params or {}}).encode()
    try:
        req = urllib.request.Request(
            f"{BASE}/messages/?session_id={sid}",
            method="POST", data=payload,
            headers={"Content-Type": "application/json", "Host": "127.0.0.1"},
        )
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}

    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            tag, data = q.get(timeout=1)
            if tag == "error":
                return {"error": data}
            if tag == "done":
                return {"error": "stream closed"}
            if tag == "json" and isinstance(data, dict) and data.get("event") == "message":
                msg = data.get("message", {})
                d = msg.get("data", {})
                if isinstance(d, dict):
                    if "result" in d:
                        return d["result"]
                    elif "error" in d:
                        return {"error": d["error"]}
            elif tag == "text":
                pass  # skip plain text
        except queue.Empty:
            continue
    return {"error": "timeout"}

tests = [
    ("search_bio",     {"query": "Habsburg",  "limit": 5}),
    ("search_bio",     {"query": "Johann",    "limit": 5}),
    ("search_persons", {"query": "Miller",    "limit": 5}),
    ("search_persons", {"query": "Johann",    "limit": 5}),
    ("corpus_stats",   {}),
]

for method, params in tests:
    print(f"--- {method}({params}) ---")
    result = post_and_read(method, params)
    if isinstance(result, list):
        for r in result[:3]:
            print(f"  {r}")
        if len(result) > 3:
            print(f"  ... +{len(result)-3} more")
    elif isinstance(result, dict) and "error" in result:
        print(f"  ERROR: {result['error']}")
    else:
        print(f"  {result}")
    print()

print("Done.")