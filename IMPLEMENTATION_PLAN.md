# Agentic Historian — Implementation Plan v4

**Status:** 2026-07-27
**Supersedes:** v3 (2026-07-16)
**Authors:** DH bot + Tobias

---

## Architecture

```
[User query]
       │
       ▼
┌─────────────────────────────────────────────────────────┐
│     OpenClaw orchestrator (sessions_spawn, parallel)     │
│         runtime="subagent", mode="run" × 4              │
└──────────┬───────────┬───────────┬───────────┬──────────┘
           │           │           │           │
    ┌──────▼──┐  ┌─────▼───┐ ┌────▼────┐ ┌────▼─────┐
    │  SSRQ   │  │   KF    │ │  EOS    │ │  HBLS    │
    │ :8002   │  │  :8001  │ │  :8000  │ │  :TBD    │
    │ 23 674  │  │  5 260  │ │ 137 038 │ │  ?       │
    │ persons │  │ persons │ │ persons │ │          │
    └─────┬───┘  └────┬────┘ └────┬────┘ └────┬─────┘
          │            │           │           │
          └────────────┴───────────┴───────────┘
                          │
               ┌──────────▼──────────┐
               │  Entity resolver    │
               │  merge(results)     │
               └──────────┬──────────┘
                          │
               ┌──────────▼──────────┐
               │  Unified response   │
               │  source attribution  │
               │  confidence scores  │
               └─────────────────────┘
```

---

## Current MCP Federation (2026-07-27) — Corrected

| Port | Source | Status | DB | Persons | Transport | Endpoint |
|------|--------|--------|----|---------|-----------|---------|
| 8002 | **SSRQ** | ✅ **NEU (2026-07-27)** | `ssrq_v6.db` | 23 674 | HTTP | `https://tei.dh.unibe.ch/mcp/ssrq/` |
| 8001 | **KF** | ✅ existed | `kf.db` | 5 260 | HTTP | `https://tei.dh.unibe.ch/mcp/kf/` |
| 8000 | **EOS (HGB)** | ✅ existed | `hgb.db` | 137 038 | SSE | `https://tei.dh.unibe.ch/mcp/eos/` |
| TBD | **HBLS** | 🔴 **NOCH NICHT GEBAUT** | — | — | — | — |

**Korrektur:** SSRQ wurde heute neu deployed. KF und EOS MCP existierten bereits. HBLS/eos_persons muss noch als MCP gebaut werden.

---

## HBLS — Was muss gebaut werden (Priorität 1)

### Warum HBLS fehlt

`github.com/thodel/eos_persons` ist ein GitHub-Repo mit `mcp_server/`, aber:
- Der MCP server läuft **nicht** auf einem Port
- Er ist weder in der Gateway-config noch in `mcp_registry.py` eingetragen
- Er muss lokal gebaut und deployed werden

### Was im Repo vorhanden ist

```
eos_persons/
├── mcp_server/
│   ├── server.py      # FastMCP, SSE transport
│   ├── db.py          # SQLite helpers
│   ├── build_db.py    # XML → SQLite build
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── requirements.txt
├── persons_resolved.json  # 137 038 merged person records
├── build_data.py
├── link_hbls_gnd.py
└── link_hls.py
```

### Schritte

1. **Repo klonen** (falls nicht vorhanden):
   ```bash
   git clone https://github.com/thodel/eos_persons.git ~/kf_data/eos_persons
   ```

2. **DB bauen** (einmalig, aus XML):
   ```bash
   cd ~/kf_data/eos_persons/mcp_server
   python build_db.py --xml ../hgb_full_*.xml --db hbls.db
   ```

3. **Server starten** (Port 8003):
   ```bash
   python server.py --db hbls.db --host 0.0.0.0 --port 8003
   ```

4. **nginx reverse proxy** für `https://tei.dh.unibe.ch/mcp/hbls/` → `localhost:8003`

5. **In `mcp_registry.py` eintragen** (bereits vorbereitet — `hbls` key existiert, `path` fehlt noch)

---

## mc p_registry.py — Aktueller Stand

Der `agentic_historian/knowledge_hub/mcp_registry.py` ist bereits vorbereitet:

```python
MCPSource(
    name="ssrq",
    title="SSRQ — Summary of Swiss Roman Law Queries",
    kinds=("person", "org"),
    path="ssrq",           # → https://tei.dh.unibe.ch/mcp/ssrq
    authority=True,
    transport="http",
    tool_map={"search_orgs": "org"},
),
MCPSource(
    name="kf",
    title="KF — Königsfelden register",
    kinds=("person", "place"),
    path="kf",
    transport="http",
),
MCPSource(
    name="eos",
    title="EOS — HGB Basel (documents & spans)",
    kinds=("person", "place", "org", "fulltext"),
    path="eos",
    authority=False,
    tool_map={"search_fulltext": "search_text"},
),
MCPSource(
    name="hbls",
    title="HBLS — Historisch-Biographisches Lexikon der Schweiz",
    kinds=("person"),
    path="hbls",           # ← fehlt noch Port/URL!
    authority=True,
    tool_map={"search_persons": "search"},
    adapter=_hbls_adapter,
),
```

**Was fehlt:** `hbls.path` muss auf den neuen Port zeigen + nginx muss `/mcp/hbls` forwarden.

---

## Meilensteine

### M1 · SSRQ + KF Parallel-Suche (sofort umsetzbar) ✅ Infrastruktur da

- `search_parallel.py` schreiben (oder `entity_resolver.py` erweitern)
- `sessions_spawn` mit 2 subagents: SSRQ + KF
- Merge nach: GND/HLS ID match → name + date overlap → fuzzy match
- Timeout: 8 000 ms pro source

**Bestehende Dateien:**
- `agentic_historian/knowledge_hub/hub.py`
- `agentic_historian/knowledge_hub/mcp_registry.py`
- `agentic_historian/utils/mcp_client.py`

---

### M2 · EOS (HGB :8000) hinzufügen

- HGB的工具: `search_persons`, `get_document`, `search_text`, `get_persons_in_year_range`
- HGB hat **137 038 persons** → aggressive Filterung nach Jahr nötig
- `tool_map={"search_fulltext": "search_text"}` ist bereits in registry

---

### M3 · HBLS MCP bauen und deployen (neue Arbeit)

1. Repo klonen nach `~/kf_data/eos_persons`
2. `build_db.py` ausführen (Daten aus `persons_resolved.json` + XML Quellen)
3. `server.py` auf Port 8003 starten
4. nginx: `/mcp/hbls` → `localhost:8003/sse`
5. `mcp_registry.py` aktualisieren: `path="hbls"`, `transport="sse"`
6. Test: `curl https://tei.dh.unibe.ch/mcp/hbls/sse`

---

### M4 · Cross-Source Entity Resolution Engine

**Merge-Strategie (Priorität):**

```
1. Authority ID match       → GND / HLS / Wikidata QID (high confidence)
2. Exact name + date overlap → same normalised name + year ranges ≥1yr (medium)
3. Fuzzy name + geo + time  → Soundex + location + ±10yr (low)
4. First-name variant map   → Johann ↔ Hans, Maria ↔ Marie
```

**Datenstruktur:**
```python
@dataclass
class MergedPerson:
    canonical_name: str
    all_names: list[str]
    authority_ids: dict[str, str]   # {gnd, hls, wikidata, ssrq, kf, eos, hbls}
    year_from: int | None
    year_to: int | None
    occupations: list[str]
    locations: list[str]
    sources: list[str]
    confidence: float               # 0.0–1.0
    raw_records: dict[str, dict]    # für Audit/Debug
```

---

### M5 · Agent C Integration

- `entity_agent.py`: `search_persons` → `search_all` ersetzen
- Für jede entity mention: Top candidates von SSRQ + KF + EOS + HBLS
- "Kein Link" bleibt; Klick auf Kandidat schreibt den Link

---

## Offene Fragen (M6)

| Frage | Optionen |
|-------|----------|
| Port 8004 (HLS direkt) | Neben HBLS exposing, oder reicht HBLS? |
| Write-back | Neue Person in einem Source → in Hub schreiben? Unter welchen Bedingungen? |
| SSRQ Orgs | Braucht die Parallel-Suche auch einen Org-Pfad? (7 047 Orgs in SSRQ) |
| Performance | 4 × subagent spawning hat Latenzkosten; Timeout pro Source (default 8s) |

---

## Nächster konkreter Schritt

**Branch:** `feat/ah-287-parallel-search` (von `main`)

1. `knowledge_hub/search_parallel.py` schreiben
2. Zwei subagents spawnen: SSRQ + KF
3. Merge-Logik mit ID match first, dann name+date fuzzy
4. Integration in `entity_agent.py`
5. Test: `"Johann von Erdingen"` returned Ergebnisse aus beiden Quellen

**Bestehende Infrastruktur, die genutzt werden kann:**
- `mcp_registry.py` (SSRQ + KF sind bereits eingetragen)
- `utils/mcp_client.py` (async HTTP client über die KH federation)
- `entity_resolver.py` (existiert bereits)

---

## Phase-Übersicht (aktualisiert)

| Phase | Inhalt | Status |
|-------|--------|--------|
| 0 | GitHub setup & exec approvals | ✅ |
| 1 | Scaffold & Discord bot | ✅ |
| 2 | Knowledge Hub (MCP federation) | ✅ |
| 3 | OCR (HTR): VLM path | ✅ |
| 4 | Source description | 🔨 |
| 5 | Entity extraction (NER) | 🔨 |
| 6 | Corpus analysis | 🔨 |
| 7 | Meta agent | 🔨 |
| 8 | Hot folder integration | 🔄 |
| 9 | Multi-source federated search | ✅ |
| 10 | HBLS MCP integration | 🔴 |
| 11 | Unified entity resolution & API | ⬜ |

*Legende:* ✅ done · 🔨 known correctness bugs · 🔄 in progress · ⬜ not started · 🔴 blocked (prerequisite missing)

---

*Erstellt: 2026-07-27 | Version: 4*