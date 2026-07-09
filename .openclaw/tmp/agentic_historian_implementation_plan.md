# Agentic Historian — Implementation Plan
**Stand:** 6. Juli 2026 | **Status:** Entwurf zur Diskussion mit Tobias

---

## 1. Ausgangslage

Vier Corpus-Server sind bereits als MCP auf `tei.dh.unibe.ch` deployed:

| Server | Port | Inhalt | Personen | Deployment |
|---|---|---|---|---|
| **EOS MCP** | 8000 | HGB Basel (Span-Level) | 137 038 (HBLS-Merged) | ⚙️ existing |
| **KF MCP** | 8001 | Königsfelden Register | 5 260 | ⚙️ existing |
| **SSRQ MCP** | 8002 | Schweizer Sozialarchiv | 23 674 + 7 047 Orgs | ⚙️ today |
| **HLS MCP** | 8004 | Historisches Lexikon Schweiz | ≈ 150 000 | ⚙️ existing |
| **HBLS MCP** | 8003 | Historisches Basel-Lexikon | 19 707 | 🆕 in progress |

> **Korrektur:** HBLS wurde heute als Test-MCP (Port 8003) aufgesetzt, aber noch nicht produktiv deployed. Die statische Landing-Page (`hbls-landing.html`) ist noch nicht über nginx erreichbar — das Deployment Requires sudo-Zugang.

---

## 2. Architektur

```
                         ┌─────────────────────────────────────┐
                         │  OpenClaw Orchestrierungsschicht     │
                         │  (Hauptagent / sessions_spawn)       │
                         └──────────────────┬──────────────────┘
                                            │ parallel subagents
                    ┌───────────┬───────────┼───────────┬───────────────┐
                    ↓           ↓           ↓           ↓               ↓
            ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
            │  SSRQ    │ │   KF     │ │   EOS    │ │   HLS    │ │  HBLS    │
            │ MCP 8002 │ │ MCP 8001 │ │ MCP 8000 │ │ MCP 8004 │ │ MCP 8003 │
            └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘
                    │           │           │           │               │
                    └───────────┴───────────┴───────────┴───────────────┘
                                            │
                         ┌──────────────────┴──────────────────┐
                         │  Entity Resolver + Merger             │
                         │  - Deduplikation via GND/HLS ID      │
                         │  - Fuzzy Match (Name + Jahr overlap)  │
                         │  - Confidence Score + Source Attr.    │
                         └──────────────────┬──────────────────┘
                                            │
                         ┌──────────────────┴──────────────────┐
                         │  Unifizierte Antwort                 │
                         │  (Quellen-Attribution + Konfidenz)   │
                         └──────────────────────────────────────┘
```

### Transport-Layer
- Alle MCP-Server sprechen **HTTP + SSE** (Streaming) oder **streamable-http**
- OpenClaw ruft jeden MCP direkt via HTTP an (nicht über zentralen Proxy)
- `sessions_spawn runtime=subagent` pro Quelle → parallel, nicht sequentiell

### Entity-Resolution-Strategie (Merge-Layer)

```
Stufe 1 — Hohe Konfidenz:
  • GND-ID exakt überein (aus HLS/HBLS referencing)
  • HLS-ID überein (Königsfelden → HLS Crosswalk ist bereits bekannt)
  • HLS-ID überein (SSRQ TTL → HLS ID ist bereits für 162 KF-Personen bekannt)

Stufe 2 — Mittlere Konfidenz:
  • Name (normalisiert: ASCII-folding, Titel entfernt) + Jahr-Überlapp (>5 Jahre)
  • Gleicher Ort + überlappende Jahre (gleiche Wirkungsstätte)

Stufe 3 — Niedrige Konfidenz:
  • Nur Namensähnlichkeit (Levenshtein < 2, gleicher Vorname)
  • Als "möglicherweise identisch" markieren
```

---

## 3. MVP Meilensteine

### Phase 1 — Parallel Search (SSRQ + KF) ✅ Konzeptionell bereit
- **Was:** `sessions_spawn` zwei Subagents, einer pro MCP, parallele Suche
- **Output:** Ergebnisse von beiden Quellen, noch unmerged
- **Warum:** Schneller Gewinn, geringste Komplexität

### Phase 2 — EOS MCP in Parallel Search integrieren
- **Was:** EOS MCP (Port 8000) kennenlernen → welche Endpunkte/Parameter
- **Output:** Dreifach-Suche SSRQ + KF + EOS
- **Offene Frage:** EOS/HBLS Cross-Reference-Methodik (GND, Name + Jahr, Ort)

### Phase 3 — HBLS MCP produktiv deployen
- **Was:**
  1. Landing Page über nginx exposen (sudo für alias-Änderung)
  2. Container auf prod-Image umstellen (hbls-mcp:latest)
  3. MCP-Manifest korrekt setzen
- **Output:** HBLS MCP öffentlich erreichbar unter Port 8003

### Phase 4 — Entity Resolution Layer bauen
- **Was:** Merger-Service, der die Ergebnisse aller 4 MCPS zusammenführt
- **Output:** Pro Person: kanonischer Name, IDs (HLS, GND, lokal), Konfidenz, Quellen
- **Algorithmen:** Hash-Tabelle (exakte IDs) → Fuzzy-Match (Name+Jahr)

### Phase 5 — Unified Person Record
- **Was:** Antwortformat definieren und implementieren
- **Output:** Strukturierte Antwort mit Source-Attribution + Konfidenz-Score

---

## 4. Datenquellen im Detail

### SSRQ (Port 8002)
- **DB:** 23,674 Personen, 7,047 Organisationen, 138,298 Name-Varianten
- **Königsfelden-Verbindung:** 162 KF-Personen mit HLS-IDs via SSRQ TTL
- **Tool:** `ssrq search <name>`, `ssrq person <id>`
- **Ausbaupotenzial:** Orgs sind recherchierbar

### KF (Port 8001)
- **DB:** 5,260 Personen (IDs: per000089 – per030180)
- **Besonderheit:** HLS-ID in `hls_id` Feld — aber **alle 5,260 sind NULL**
- **Bekannte Kreuzreferenz:** 162 KF↔HLS IDs aus SSRQ TTL ableitbar
- **Tool:** `kf__search_persons`, `kf__get_person`, `kf__get_entries_for_person`

### EOS / HGB (Port 8000)
- **DB:** 75,447 Dokumente, 893,303 Spans ( roh)
- **Merged HBLS:** 137,038 Personen aus HGB Basel (ca. 1400–1700)
- **Schema:** `n` (Name), `v` (Varianten), `y` (Jahr-Bereich), `c` ( Erwähnungen), `d` ( Dossiers), `hls`, `wd` (Links)
- **Cross-Links:** 809/137,038 mit HLS-Link (0.6%), 768 mit Wikidata/GND

### HLS MCP (Port 8004)
- **Inhalt:** Historisches Lexikon der Schweiz — noch nicht untersucht (Port 8004 antwortet noch nicht)
- **Ausstehende Arbeit:** Endpunkte und Datenformat verstehen

### HBLS MCP (Port 8003)
- **Daten:** 18,244 Artikel, 19,707 Personen, 3,718 Bio-Artikel
- **Landing:** Korrekte Stats sind bereits im MCP implementiert, aber noch nicht über nginx erreichbar
- **DB:** `hbls.db` (79 MB)

---

## 5. Konkrete nächste Schritte

### Sofort (diese Woche)
1. **HBLS nginx-Problem lösen** — sudo-Zugang für nginx-Alias-Änderung oder chown der statischen Datei
2. **HLS MCP (Port 8004) untersuchen** — welche Tools/Endpunkte sind verfügbar?
3. **EOS MCP Interface dokumentieren** — Inputs/Outputs der bestehenden MCP-Tools

### Kurzfristig
4. **Phase 1 Probe:** SSRQ + KF Parallel-Suche als Proof-of-Concept
5. **EOS MCP verstehen** — Was liefert `search_persons`? Was ist die DB-Qualität?
6. **Entity-Resolution-Design** entwerfen (Tobias abstimmen)

### Mittelfristig
7. **HBLS MCP in Produktion** überführen
8. **Merger-Layer** implementieren
9. **Unified Response Format** festlegen

---

## 6. Offene Fragen

| # | Frage | Wer | Priorität |
|---|---|---|---|
| 1 | Port 8004 (HLS MCP): Welche Endpunkte/Tools? | noch unbekannt | 🔴 hoch |
| 2 | EOS MCP: Suche nach Person → liefert was genau? Welche IDs? | noch unbekannt | 🔴 hoch |
| 3 | HBLS↔HLS: Wie ist die ID-Verbindung? (HBLS hat HLS-Links?) | Tobias | 🟡 medium |
| 4 | Welche ID ist die "Goldene Referenz"? (HLS-ID, GND, oder lokal?) | Abstimmung | 🟡 medium |
| 5 | Wie soll das einheitliche Antwort-Format aussehen? | Tobias | 🟡 medium |

---

## 7. Technische Constraints

- **OpenClaw** ist der Orchestrierungs-Layer — keine separate Orchestrierungs-App nötig
- **Parallelisierung** über `sessions_spawn runtime=subagent` (kein Threading nötig)
- **Keine neue DB** — alle Daten bleiben in ihren bestehenden Quellen
- **HBLS MCP** nutzt `/mcp/hbls/landing` als internen Endpunkt → nginx muss Proxy-Pass haben
- **Credentials für SwitchDrive** sind in `.env.gpustack` — nicht in diesem Stack relevant