# DH Journal RSS Feeds

## ✅ Working Feeds

### Journals (peer-reviewed articles)
| Journal | RSS URL | Notes |
|---------|---------|-------|
| **Reviews in Digital Humanities** | https://reviewsindh.pubpub.org/rss.xml | PubPub, peer-reviewed DH project reviews |
| **Zeitschrift für digitale Geisteswissenschaften (ZfdG)** | https://zfdg.de/zfdg_beitraege.xml | German-language DH journal, OJS-style |
| **DSH: Digital Scholarship in the Humanities** (Oxford) | https://academic.oup.com/rss/site_5447/3308.xml | Confirmed working 2026-06-30 |
| **Digital Studies / Le champ numérique** | https://www.google.com/alerts/feeds/12326514175916915572/8059671739515192766 | Google Alert proxy (site:digitalstudies.org/article/id/) |
| **DHQ: Digital Humanities Quarterly** | https://www.google.com/alerts/feeds/12326514175916915572/2170710756314080150 | Google Alert proxy (site:dhq.digitalhumanities.org/vol/) |
| **Open Humanities Data** | https://www.google.com/alerts/feeds/12326514175916915572/4303239240162766721 | Google Alert proxy (site:openhumanitiesdata.metajnl.com/articles/) |
| **Journal of Data Mining & Digital Humanities (JdMDH)** | https://api.episciences.org/api/feed/rss/jdmdh | Episciences API, confirmed working, has DOIs |
| **Transformations: A DARIAH Journal** | https://api.episciences.org/api/feed/rss/transformations | Episciences API, confirmed working |
| **Springer** (journal-id=42803) | https://link.springer.com/search.rss?facet-journal-id=42803&facet-content-type=Article | Springer search feed — confirmed working 2026-06-30 |
| **Code4Lib Journal** | https://journal.code4lib.org/feed | Library infra & software, confirmed working 2026-06-30 |
| **Critical Digital Humanities** | https://submission.wavehills.org/index.php/cdh/gateway/plugin/WebFeedGatewayPlugin/rss | OJS WebFeed RDF feed, confirmed working 2026-06-30 |
| **Histoire & mesure** | https://journals.openedition.org/histoiremesure/backend?format=rssdocuments | OpenEdition backend RSS (bypasses Anubis), confirmed working 2026-06-30 |
| **Acta historica et archaeologica mediaevalia** (AHAM) | https://raco.cat/index.php/ActaHistorica/gateway/plugin/WebFeedGatewayPlugin/rss | OJS WebFeed RDF, UB Barcelona medieval history, confirmed working 2026-06-30 |

### News & Aggregation
| Source | RSS URL | Notes |
|--------|---------|-------|
| **Digital Humanities Now** | https://digitalhumanitiesnow.org/feed/ | Community-curated DH news, ACE editorial |

### Preprints (arXiv)
| Category | RSS URL | Notes |
|----------|---------|-------|
| **cs.DL** (Digital Libraries) | https://export.arxiv.org/rss/cs.DL | DH-adjacent IR/digitization work |
| **cs.IR** (Information Retrieval) | https://export.arxiv.org/rss/cs.IR | Text retrieval, NLP for historical texts |

## ❌ Known Broken / Unavailable
- **J-STAGE journals** (JADH, KJDH) — no RSS on J-STAGE platform
- **Cultural Analytics** (SAGE) — blocked by Anubis
- **Cambridge UP** (the feed Tobias shared) — 503 Cloudflare, server-side issue
- **Humanités numériques** (Openedition) — blocked by Anubis

## 📋 Key DH Journals (ADHO-affiliated, no RSS)
These are major journals that sadly lack working RSS:
- Humanités numériques - https://journals.openedition.org/revuehn/
- Journal of the TEI - https://journal.tei-c.org/
- JADH (Japan) - https://www.jstage.jst.go.jp/browse/jadh
- KJDH (Korea) - https://accesson.kr/kjdh
- JoDADH (Taiwan) - https://tadh.org.tw/en/jodadh/

## 💡 Recommendations for Reviewer Workflow
1. **Primary reads**: Reviews in DH + ZfdG + DSH + Digital Studies + DHQ (all via feeds above) + DH Now
2. **Preprint monitoring**: arXiv cs.DL + cs.IR filtered by keywords (text analysis, OCR, NLP, corpus, historical)
3. **Zotero group libraries** may complement journal RSS for early signals

## Cron Jobs Using These Feeds
- DH Weekly Digest (Sundays 16:00 CET/Berlin) → DM to 817396581317738546

## Last verified: 2026-06-30