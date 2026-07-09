#!/usr/bin/env python3
"""FastAPI micro-service for the SSRQ RDF-TTL corpus."""
import os, sqlite3
from fastapi import FastAPI, HTTPException, Query
from typing import Optional

DB = os.environ.get("SSRQ_DB", "/app/data/ssrq.db")
ALLOW_RAW_SQL = os.environ.get("ALLOW_RAW_SQL", "0") == "1"
app = FastAPI(title="SSRQ MCP", version="0.1.0")

def _db():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

@app.get("/mcp/ssrq/person/{ssrq_id}")
def get_person(ssrq_id: str):
    with _db() as conn:
        row = conn.execute("SELECT * FROM persons WHERE id=?", (ssrq_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Person {ssrq_id!r} not found")
        cols = [c[0] for c in conn.execute("SELECT * FROM persons LIMIT 0").description]
        return dict(zip(cols, row))

@app.get("/mcp/ssrq/org/{ssrq_id}")
def get_org(ssrq_id: str):
    with _db() as conn:
        row = conn.execute("SELECT * FROM orgs WHERE id=?", (ssrq_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Org {ssrq_id!r} not found")
        cols = [c[0] for c in conn.execute("SELECT * FROM orgs LIMIT 0").description]
        return dict(zip(cols, row))

@app.get("/mcp/ssrq/search")
def search(q: str = Query(...), type: Optional[str] = Query(None), limit: int = Query(10)):
    results = []
    seen = set()
    with _db() as conn:
        rows = conn.execute(
            "SELECT ni.ssrq_id, ni.name_text, ni.is_orig, p.uri, p.label, p.std_name "
            "FROM name_index ni JOIN persons p ON p.id = ni.ssrq_id "
            "WHERE ni.name_text LIKE ? ORDER BY ni.is_orig DESC LIMIT ?",
            (q.lower() + "%", limit)
        ).fetchall()
        for row in rows:
            if row[0] not in seen:
                seen.add(row[0])
                results.append({"ssrq_id": row[0], "name": row[1], "is_orig": bool(row[2]),
                                "uri": row[3], "label": row[4], "std_name": row[5]})
    return {"q": q, "type": type, "limit": limit, "count": len(results), "results": results}

@app.get("/mcp/ssrq/query")
def raw_sql(sql: str = Query(...)):
    if not ALLOW_RAW_SQL:
        raise HTTPException(403, "Set ALLOW_RAW_SQL=1")
    if not sql.strip().upper().startswith("SELECT"):
        raise HTTPException(400, "SELECT only")
    with _db() as conn:
        rows = conn.execute(sql).fetchall()
        cols = [d[0] for d in conn.execute("SELECT 1 LIMIT 0").description]
    return {"sql": sql, "rows": rows, "columns": cols, "count": len(rows)}

@app.get("/mcp/ssrq/")
def root():
    return {"status": "ok", "version": "0.1.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)