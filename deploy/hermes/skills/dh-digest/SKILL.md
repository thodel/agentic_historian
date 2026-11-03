---
name: dh-digest
description: Weekly digest of new DH articles for Tobias — rate the items that digest_fetch.py collected, never fetch feeds yourself.
version: 1.0.0
author: thodel
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [research, report, cron]
    requires_toolsets: [terminal]
    config:
      - key: DIGEST_ITEMS
        description: The JSON that digest_fetch.py wrote in the same job
        default: /home/hermes/.hermes/cron/digest_items.json
    blueprint:
      schedule: "0 16 * * 0"
      deliver: discord
      prompt: Produce the DH weekly digest as the skill says. First run `python3 ${HERMES_SKILL_DIR}/scripts/digest_fetch.py --days 7 --out ~/.hermes/cron/digest_items.json`, then read that file and rate it. Always output a digest.
---

# DH weekly digest

Sunday 16:00 Europe/Zurich (the OpenClaw job ran on Europe/Berlin and timed
out five times at "model-call-started": seventeen feeds fetched by the agent
inside a 300 s budget; #799). Here the fetching is a script and the agent only
rates.

## Procedure

1. Run `python3 ${HERMES_SKILL_DIR}/scripts/digest_fetch.py --days 7 --out ${DIGEST_ITEMS}`.
   It writes `{"items": [...], "errors": [...]}` — at most 5 items per source,
   newest first, each with `source`, `title`, `link`, `published`, `authors`,
   `summary`.
2. Read the file. If `errors` is not empty, the digest ends with one line
   naming the sources that could not be read — a silent gap is the failure
   this skill exists to avoid.
3. Rate every item for Tobias Hodel's interests:
   - medieval and early modern history, 14th–17th c., Swiss-adjacent;
   - HTR/OCR of historical manuscripts (Kraken, eScriptorium, VLM-based reading);
   - digital scholarly editions (TEI, CIDOC-CRM);
   - entity extraction, prosopography, knowledge graphs;
   - FAIR workflows for historical source corpora;
   - the Königsfelden project (monastery and administrative records, Zurich/Bern).
4. Write the digest.

## Rating

5 directly relevant (same period, same method, or Tobias is a co-author) ·
4 very relevant (close method or corpus) · 3 somewhat (adjacent, useful to
know) · 2 tangential (DH, but not medieval/HTR/editions) · 1 skim only. Drop
score 1. arXiv items: keep only those matching manuscript, OCR, HTR, Kraken,
eScriptorium, medieval, early modern, historical, text analysis, NLP, named
entity, prosopography, TEI, digital edition, FAIR, CIDOC-CRM, knowledge graph,
paleography, philology.

## Output

Discord-friendly, compact, no tables, ordered by score then date. Per item:

```
**Title** — *Journal/Source*
DOI: 10.xxxx/yyyy        ← raw DOI only; without one, the link as <https://…>
Score: 4 · Authors
Summary: one sentence, the core finding.
Why: one specific sentence why Tobias should care.
```

arXiv items add the arXiv id. **Always output a digest.** With nothing at
score ≥ 2: `📬 DH Weekly Digest — nothing relevant published this week.
Monitoring continues; next digest Sunday.` The reply is delivered as is.

## Pitfalls

- Do not call `web_fetch` on the feeds; the script already did, with a
  timeout per feed. Fetch an article page only to resolve a missing DOI, and
  at most five times per run.
- Four of the sources are Google Alert feeds tied to Tobias' Google account;
  when they appear under `errors` every week, say so once in the digest.
