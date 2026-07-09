# HBLS MCP — Implementierungsplan & Status

## Status: ✅ ABGESCHLOSSEN

### Datenquellen

| Datei | Inhalt | Umfang | Status |
|---|---|---|---|
| `HBLS_band_01..08.pdf` | HBLS-Scans (biblio.unibe.ch) | 8 Bände, ~6.6 GB | ✅ Auf Server (`/home/dh/hbls_data/`) |
| `hbls_articles.json` | Extrahierte Artikel voller Text | 20,243 Artikel, 70.8 MB | ✅ Erstellt via `extract_hbls.py` |
| `hbls_web.json` | Artikel-Index (Snippets, Members, PDF-URLs) | 18,244 Artikel | ✅ Bereits vorhanden |
| `hbls.db` | SQLite + FTS5 Datenbank | 78 MB | ✅ Erstellt (`/home/dh/hbls_data/hbls.db`) |

### DB-Schema (hbls.db)
- `articles`: 18,244 rows (headword, volume, page, snippet, article_text, pdf_url)
- `members`: 19,707 rows (given, birth_year, death_year, member_n → article_id)
- `fts_articles`: FTS5 over headword + article_text

### Tools (hbls_*)

| Tool | Beschreibung |
|---|---|
| `hbls_corpus_stats` | Totals, Volume-Verteilung, Textgrösse |
| `hbls_search(query, limit)` | FTS5-Suche über Headword + Artikeltext |
| `hbls_get_article(headword, volume)` | Einzelartikel + Familienmitglieder |
| `hbls_get_article_by_page(volume, page)` | Artikel nach Band+Seite |
| `hbls_list_volume(volume, limit, offset)` | Alle Artikel eines Bandes |
| `hbls_get_family_members(headword, volume)` | Personen eines Familienartikels |
| `hbls_search_persons(query, limit)` | Personensuche nach Vornamen |
| `hbls_get_pdf_url(headword, volume, page)` | PDF-Seiten-URL |

### Deployment

| Komponente | Status |
|---|---|
| Docker Container `hbls-mcp` | ✅ Läuft auf Port 8003 |
| `http://tei.dh.unibe.ch:8003/sse` | ✅ Erreichbar |
| OpenClaw Config (`mcp.servers.hbls`) | ✅ Eingetragen |
| `build_hbls_db.py` | ✅ Funktionsfähig |
| `server.py` (FastMCP) | ✅ Läuft |

### Dateien

```
/home/dh/hbls_data/
  HBLS_band_01.pdf .. 08.pdf   (6.6 GB)
  hbls_articles.json           (70.8 MB, 20,243 Artikel)
  hbls.db                      (78 MB, SQLite + FTS5)

/home/dh/hbls_mcp/
  server.py                    FastMCP server
  db.py                        Query helpers
  build_hbls_db.py             JSON → SQLite
  requirements.txt             mcp[cli]>=1.0.0, lxml>=5.2.0
  Dockerfile                   python:3.12-slim
  docker-compose.yml           Port 8003
  README.md                    Tool-Referenz
```

### Phase 6: Artikel-Kategorisierung ✅

HBLS-Artikel wurden in 4 Kategorien eingeteilt (analog zu HLS):

| Kategorie | Anzahl | Beschreibung |
|---|---|---|
| `tem` (Themen) | 9,708 | Sachartikel ohne Personenbezug |
| `bio` (Personen) | 3,718 | Artikel über Einzelpersonen |
| `geo` (Orte & Räume) | 2,510 | Geografische Artikel |
| `fam` (Familien) | 2,308 | Familienregister mit Geburts-/Todesdaten |

**Methode:**
- `fam`: Mitglieder mit ≥2 Geburts- und ≥2 Todesjahren (oder ≥3 Mitglieder mit je birth+death)
- `bio`: Artikel mit Mitgliedern aber ohne vollständige birth+death Daten
- `geo`: Headword = Kanton-Kürzel (BE, ZH, SG…) oder 2–4 Buchstaben all-caps oder Place-Suffix (-ALP, -BERG, -TAL…) oder Orte-Indikatoren in Content
- `tem`: Default für alle übrigen

**Known limitations:** AARE (Fluss) = bio statt geo; einzelne Fehlkategorisierungen möglich. Kategorisierung ist heuristisch, keine manuelle Validierung.
- **nginx-Config**: Port 8003 bisher direkt über Docker. Bei Bedarf nginx-Proxy einrichten.

---

*Erstellt: 2026-07-03 | Abgeschlossen: 2026-07-03*