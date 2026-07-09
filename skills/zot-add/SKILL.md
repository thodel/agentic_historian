---
name: "zot-add"
description: "Add a DOI paper/book to the dh_unibe Zotero collection"
---

# zot-add — Add DOI to Zotero

Add a journal article or book to the **dh_unibe** Zotero group library by DOI.

## Usage

```
zot-add <DOI>
```

Examples:
```
zot-add 10.17175/2026_001
zot-add https://doi.org/10.1038/nature12373
```

## How It Works

1. **Fetch metadata** — Resolves DOI via Crossref API (primary) or HTML scraping (fallback)
2. **Parse** — Extracts title, authors, journal, date, abstract, keywords
3. **Suggest collections** — Scores 3 best-matching collections from dh_unibe group based on keywords
4. **User picks** — Choose 1-3 or enter a custom collection key
5. **Upload** — POSTs to Zotero API: `https://api.zotero.org/groups/2386895/items`

## Collections Matching

The script matches item keywords against collection names using these rules:

| Keyword | Suggests collection containing |
|---------|-------------------------------|
| geschicht | Geschichtswissenschaften, Historisches, History |
| digital | Digital Humanities, digitale, Digital |
| text | Text, OCR, Text Mining |
| archiv | Archiv, Archive, Archives |
| mittelalter | Mittelalter, Medieval, Königsfelden |
| edition | Edition, Editions, TEI |
| sammlung | Sammlung, Collection |
| daten | Data, Daten, Metadata |
| ki | AI, KI, Machine Learning, Generative |
| sozial | Soziologie, Social |
| philosoph | Philosophie, Philosophy |

## Script Location

```bash
/home/dh/.openclaw/workspace/agentic_historian/add-z
```

## Zotero Credentials

- Group ID: `2386895` (dh_unibe)
- User ID: `534029`
- API Key: stored in script (or move to `~/.env`)

## Notes

- If no collections match, the item is added without a collection
- Author names are split on `, ` (last, first format from Crossref)
- Up to 10 subject keywords become Zotero tags
- The Zotero API sometimes returns a 404 on first POST — retry if needed
