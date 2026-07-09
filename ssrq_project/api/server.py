#!/usr/bin/env python3
"""
FastAPI micro-service for the SSRQ RDF-TTL corpus.

Endpoints
---------
GET /person/{ssrq_id}
    Return the full person record (JSON).

GET /org/{ssrq_id}
    Return the full organisation record.

GET /search
    q=<name>          -> fuzzy name-search (uses the `name_index` table)
    type=person|org   -> optional filter

GET /query
    sql=<SQL>          -> **dangerous** - only enabled when you set the env var
                         `ALLOW_RAW_SQL=1`.  Use for ad-hoc debugging.

All endpoints return JSON and appropriate HTTP status codes.
"""

import os
import sqlite3
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from typing import Optional

# ----------------------------------------------------------------------
# Configuration - change only if you move the DB
DB_PATH = os.getenv("SSRQ_DB", "/app/data/ssrq.db")
# ----------------------------------------------------------------------

app = FastAPI(title="SSRQ MCP", version="0.1.0")


def _db() -> sqlite3.Connection:
    """Open a read-only connection (WAL works fine for many concurrent readers)."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/person/{ssrq_id}")
def get_person(ssrq_id: str):
    """Return a single person record."""
    with _db() as con:
        cur = con.execute(
            "SELECT * FROM persons WHERE id = ?",
            (ssrq_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Person not found")
        return dict(row)


@app.get("/org/{ssrq_id}")
def get_org(ssrq_id: str):
    """Return a single organisation record."""
    with _db() as con:
        cur = con.execute(
            "SELECT * FROM orgs WHERE id = ?",
            (ssrq_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Org not found")
        return dict(row)


@app.get("/search")
def search(
    q: str = Query(..., description="Name fragment to search for"),
    type: Optional[str] = Query(
        None,
        regex="^(person|org)$",
        description="Optional filter - restrict to persons or orgs",
    ),
    limit: int = Query(20, ge=1, le=500),
):
    """Fuzzy name search on the pre-built name_index."""
    with _db() as con:
        if type == "person":
            sql = """
                SELECT p.id, p.std_name, p.forename, p.surname, p.label
                FROM name_index ni
                JOIN persons p ON p.id = ni.ssrq_id
                WHERE ni.name_text LIKE ?
                GROUP BY p.id
                ORDER BY COUNT(*) DESC
                LIMIT ?
            """
        elif type == "org":
            sql = """
                SELECT o.id, o.std_name, o.label
                FROM name_index ni
                JOIN orgs o ON o.id = ni.ssrq_id
                WHERE ni.name_text LIKE ?
                GROUP BY o.id
                ORDER BY COUNT(*) DESC
                LIMIT ?
            """
        else:  # both
            sql = """
                SELECT id, std_name, label
                FROM (
                    SELECT p.id   AS id,
                           p.std_name,
                           p.label
                    FROM name_index ni
                    JOIN persons p ON p.id = ni.ssrq_id
                    WHERE ni.name_text LIKE ?
                    UNION ALL
                    SELECT o.id,
                           o.std_name,
                           o.label
                    FROM name_index ni
                    JOIN orgs o ON o.id = ni.ssrq_id
                    WHERE ni.name_text LIKE ?
                )
                GROUP BY id
                ORDER BY COUNT(*) DESC
                LIMIT ?
            """

        param = f"%{q}%"
        args = (param, param, limit) if type is None else (param, limit)
        cur = con.execute(sql, args)
        results = [dict(row) for row in cur.fetchall()]
        return {"query": q, "type": type or "any", "results": results}


@app.get("/query")
def raw_sql(sql: str = Query(..., description="Raw SELECT statement (DEBUG ONLY)")):
    """Execute an arbitrary SELECT - **enable only in safe dev**."""
    if os.getenv("ALLOW_RAW_SQL") != "1":
        raise HTTPException(
            status_code=403,
            detail="Raw SQL execution - set ALLOW_RAW_SQL=1 to enable",
        )
    with _db() as con:
        try:
            cur = con.execute(sql)
            rows = [dict(row) for row in cur.fetchall()]
            return JSONResponse(content=rows)
        except sqlite3.Error as e:
            raise HTTPException(status_code=400, detail=str(e))


@app.get("/")
def root():
    """Simple health-check."""
    return {"status": "ok", "version": "0.1.0"}