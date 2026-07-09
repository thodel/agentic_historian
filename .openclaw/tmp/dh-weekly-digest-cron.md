# Weekly DH Digest — Cron Setup

## Feeds to monitor
feeds = [
  "https://reviewsindh.pubpub.org/rss.xml",
  "https://academic.oup.com/rss/site_5447/3308.xml",
  "https://zfdg.de/zfdg_beitraege.xml",
  "https://digitalhumanitiesnow.org/feed/",
  "https://export.arxiv.org/rss/cs.DL",
  "https://export.arxiv.org/rss/cs.IR",
  "https://www.google.com/alerts/feeds/12326514175916915572/8059671739515192766",
]

## Delivery
- Target: DM to Tobias (817396581317738546)
- Channel: discord
- Announce mode
- Schedule: Sundays 16:00 Berlin/CET (Europe/Berlin)

## Prompt for agent
"""
You are curating a weekly DH article digest for Tobias Hodel.

Tobias's core research interests:
- Medieval / early modern history (esp. 14th–17th c. Swiss-adjacent)
- HTR / OCR of historical manuscripts (uses Kraken, eScriptorium)
- Digital scholarly editions (TEI-encoded, CIDOC-CRM mapping)
- Entity extraction and knowledge graph construction (named entities, prosopography)
- FAIR data workflows for historical source corpora
- Königsfelden project (medieval monastery/administrative records, Zurich/Bern)

Fetch the latest from these RSS feeds:
- https://reviewsindh.pubpub.org/rss.xml
- https://academic.oup.com/rss/site_5447/3308.xml
- https://zfdg.de/zfdg_beitraege.xml
- https://digitalhumanitiesnow.org/feed/
- https://export.arxiv.org/rss/cs.DL
- https://export.arxiv.org/rss/cs.IR
- https://www.google.com/alerts/feeds/12326514175916915572/8059671739515192766  (Digital Studies / champ numérique)
- https://www.google.com/alerts/feeds/12326514175916915572/2170710756314080150  (DHQ: Digital Humanities Quarterly)
- https://www.google.com/alerts/feeds/12326514175916915572/4303239240162766721  (Open Humanities Data)
- https://www.google.com/alerts/feeds/12326514175916915572/14639773446571128045  (magazén)
- https://api.episciences.org/api/feed/rss/jdmdh  (Journal of Data Mining & Digital Humanities)
- https://api.episciences.org/api/feed/rss/transformations  (Transformations: A DARIAH Journal)
- https://link.springer.com/search.rss?facet-journal-id=42803&facet-content-type=Article  (Springer)
- https://journal.code4lib.org/feed  (Code4Lib Journal)
- https://submission.wavehills.org/index.php/cdh/gateway/plugin/WebFeedGatewayPlugin/rss  (Critical Digital Humanities)
- https://journals.openedition.org/histoiremesure/backend?format=rssdocuments  (Histoire & mesure)
- https://raco.cat/index.php/ActaHistorica/gateway/plugin/WebFeedGatewayPlugin/rss  (Acta historica et archaeologica mediaevalia)

Filter to articles published or announced in the past 7 days (based on pubDate).

For each article, produce:
- **Title & Journal** (bold the title, italicize the journal)
- **DOI** — raw DOI string only, no prefix, no markdown hyperlink (e.g. `10.1093/llc/fqad012`). If no DOI, use direct link as bare URL `<https://...>` for Discord suppression.
- **Relevance Score (1–5):**
  - 5 = Directly relevant (same period, same method, or Tobias is a co-author)
  - 4 = Very relevant (close method or corpus match to his interests)
  - 3 = Somewhat relevant (adjacent topic, useful to know)
  - 2 = Tangentially relevant (DH but not medieval/HTR/digital editions)
  - 1 = Skim only (general DH, unlikely to be useful)
- **Authors**
- **One-Sentence Summary:** The core argument or finding — write 3–4 sentences that capture the what, why, and how.
- **Actionable Reason:** Why Tobias should care; be specific (e.g., "Uses Kraken for gothic cursive manuscripts," "Applies CIDOC-CRM to medieval administrative records," "Similar entity-linking pipeline for historical prosopography").

Rules:
- Max 5 items per source
- Skip items with relevance score 1
- Skip items older than 7 days
- arXiv items: add arXiv ID; filter to DH-relevant keywords (manuscript, OCR, HTR, Kraken, eScriptorium, medieval, early modern, historical, text analysis, NLP, named entity, prosopography, TEI, digital edition, FAIR, CIDOC-CRM, knowledge graph, paleography, philology)
- Format: Discord-friendly compact markdown. No tables.

**ALWAYS send a digest — never skip sending entirely.** If no articles with score 2 or higher were found, send a short message: "📬 DH Weekly Digest — nothing relevant published this week. Monitoring continues; next digest Sunday." No need to list empty feeds.

Deliver as a direct message to Discord user ID 817396581317738546.
"""