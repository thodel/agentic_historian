#!/usr/bin/env python3
"""
FastAPI micro-service for the SSRQ RDF-TTL corpus.

Endpoints
---------
GET /mcp/ssrq/person/{ssrq_id}
    Return the full person record (JSON).

GET /mcp/ssrq/org/{ssrq_id}
    Return the full organisation record.

GET /mcp/ssrq/search
    q=<name>          -> fuzzy name-search (uses the `name_index` table)
    type=person|org   -> optional filter

GET /mcp/ssrq/query
    sql=<SQL>          -> **dangerous** - only enabled when you set the env var
                         `ALLOW_RAW_SQL=1`.  Use for ad-hoc debugging.

GET /mcp/ssrq/
    Health check.

All endpoints return JSON and appropriate HTTP status codes.
"""

import os
import sqlite3
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from typing import Optional

# ----------------------------------------------------------------------
DB = os.environ.get("SSRQ_DB", "/app/data/ssrq.db")
ALLOW_RAW_SQL = os.environ.get("ALLOW_RAW_SQL", "0") == "1"

app = FastAPI(title="SSRQ MCP", version="0.1.0")

def _db() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

# ----------------------------------------------------------------------
@app.get("/mcp/ssrq/person/{ssrq_id}")
def get_person(ssrq_id: str):
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM persons WHERE id=?", (ssrq_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Person {ssrq_id!r} not found")
        cols = [c[0] for c in conn.execute("SELECT * FROM persons LIMIT 0).description]
        return dict(zip(cols, row))

@app.get("/mcp/ssrq/org/{ssrq_id}")
def get_org(ssrq_id: str):
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM orgs WHERE id=?", (ssrq_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Org {ssrq_id!r} not found")
        cols = [c[0] for c in conn.execute("SELECT * FROM orgs LIMIT 0).description"]
        return dict(zip(cols, row))

@app.get("/mcp/ssrq/search")
def search(
    q: str = Query(..., min_length=1, max_length=200),
    type: Optional[str] = Query(None, regex="^(person|org)$"),
    limit: int = Query(10, ge=1, le=100),
):
    offset = 0
    results = []
    seen = set()
    with _db() as conn:
        base_q = (
            "SELECT ni.ssrq_id, ni.name_text, ni.is_orig, p.uri, p.label, p.std_name "
            "FROM name_index ni "
            "JOIN persons p ON p.id = ni.ssrq_id "
            "WHERE ni.name_text LIKE ? ORDER BY ni.is_orig DESC, ni.name_text LIMIT ?"
        )
        params = [q.lower() + "%", limit]
        for row in conn.execute(base_q, params):
            sid = row[0]
            if sid in seen:
                continue
            seen.add(sid)
            results.append({
                "ssrq_id": row[0],
                "name": row[1],
                "is_orig": bool(row[2]),
                "uri": row[3],
                "label": row[4],
                "std_name": row[5],
            })
        if len(results) < limit and len(q) >= 3:
            extra_q = (
                "SELECT id, uri, label, std_name FROM persons "
                "WHERE std_name LIKE ? OR label LIKE ? "
                "ORDER BY std_name LIMIT ?"
            )
            params = ["%" + q + "%", "%" + q + "%", limit - len(results)]
            for row in conn.execute(extra_q, params):
                sid = row[0]
                if sid in seen:
                    continue
                seen.add(sid)
                results.append({
                    "ssrq_id": row[0],
                    "uri": row[1],
                    "label": row[2],
                    "std_name": row[3],
                    "is_orig": None,
                    "name": row[3] or row[2] or "",
                })
    return {"q": q, "type": type, "limit": limit, "count": len(results), "results": results}

@app.get("/mcp/ssrq/query")
def raw_sql(sql: str = Query(..., description="Raw SELECT statement (DEBUG ONLY)")):
    if not ALLOW_RAW_SQL:
        raise HTTPException(403, "Raw SQL is disabled. Set ALLOW_RAW_SQL=1 to enable.")
    if not sql.strip().upper().startswith("SELECT"):
        raise HTTPException(400, "Only SELECT statements are allowed.")
    with _db() as conn:
        try:
            rows = conn.execute(sql).fetchall()
            cols = [d[0] for d in conn.execute("SELECT 1 LIMIT 0).description]
        except Exception as e:
            raise HTTPException(400, str(e))
    return {"sql": sql, "rows": rows, "columns": cols, "count": len(rows)}

@app.get("/mcp/ssrq/")
def root():
    return {"status": "ok", "version": "0.1.0", "db": DB}

# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)