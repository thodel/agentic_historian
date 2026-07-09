import sys
sys.path.insert(0, '/app')
import db as db_module
db_module.set_db_path('/data/hbls.db')

from starlette.testclient import TestClient
from server import app

with TestClient(app, raise_server_exceptions=False) as client:
    # Get session via SSE
    with client.stream("GET", "/sse", headers={"Host": "127.0.0.1"}) as resp:
        sid = None
        for line in resp.iter_lines():
            l = line.decode().rstrip()
            if "session_id=" in l:
                sid = l.split("session_id=")[1].split("&")[0]
                break
    print(f"SID: {sid}")

    if not sid:
        print("FAIL: no sid")
    else:
        # search_bio Habsburg
        r = client.post(f"/messages/?session_id={sid}",
            json={"jsonrpc":"2.0","id":1,"method":"search_bio","params":{"query":"Habsburg","limit":3}},
            headers={"Content-Type":"application/json"})
        print(f"search_bio Habsburg: HTTP {r.status_code}")
        print(f"  Body: {r.text[:400]}")

        # search_persons Miller
        r = client.post(f"/messages/?session_id={sid}",
            json={"jsonrpc":"2.0","id":2,"method":"search_persons","params":{"query":"Miller","limit":3}},
            headers={"Content-Type":"application/json"})
        print(f"\nsearch_persons Miller: HTTP {r.status_code}")
        print(f"  Body: {r.text[:400]}")

        # corpus_stats
        r = client.post(f"/messages/?session_id={sid}",
            json={"jsonrpc":"2.0","id":3,"method":"corpus_stats","params":{}},
            headers={"Content-Type":"application/json"})
        print(f"\ncorpus_stats: HTTP {r.status_code}")
        print(f"  Body: {r.text[:400]}")