"""dodis_wikidata.py — Dodis persons → Wikidata, GND, HLS, VIAF (#830).

    python -m knowledge_hub.crosswalks.dodis_wikidata \\
        --out knowledge_hub/crosswalks/dodis_wikidata.json

Dodis persons carry only their Dodis URI (``dodis.ch/P<id>``), which Human
Lineage does not resolve; the Human Lineage layer could not reach them.
Wikidata records the Dodis person ID as P701 — on 17.11.2026 for 7 693 humans,
7 585 of them with a GND number, 3 494 with an HLS ID, 7 604 with VIAF. This
reads those statements once and writes them as a crosswalk; ``dodis.py`` turns
it into ``owl:sameAs``.

Rules:

* a Dodis ID that Wikidata gives to two or more items is **ambiguous** and gets
  nothing (listed with the items);
* per scheme only a single value is taken; two GND numbers on one item are
  left out for that scheme and counted;
* the Wikidata item itself must be a human (P31 Q5).

Wikidata is CC0: the links go into the default graph with the holdings.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

SPARQL = "https://query.wikidata.org/sparql"
UA = "agentic-historian-crosswalk/1.0 (research; Dodis-Wikidata)"
SCHEMES = ("gnd", "hls", "viaf")

QUERY = """
SELECT ?item ?dodis ?gnd ?hls ?viaf WHERE {
  ?item wdt:P701 ?dodis ; wdt:P31 wd:Q5 .
  OPTIONAL { ?item wdt:P227 ?gnd }
  OPTIONAL { ?item wdt:P902 ?hls }
  OPTIONAL { ?item wdt:P214 ?viaf }
}
"""


def sparql_csv(query: str = QUERY) -> str:
    req = urllib.request.Request(SPARQL, data=urllib.parse.urlencode({"query": query}).encode(),
                                 headers={"Accept": "text/csv", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read().decode("utf-8")


def _pid(dodis: str) -> str:
    """Wikidata writes ``P38323``; ``dodis.py`` keys persons by ``38323``."""
    d = dodis.strip()
    return d[1:] if d[:1] in ("P", "p") and d[1:].isdigit() else d


def build(rows: Iterable[dict]) -> dict:
    items: dict[str, dict] = defaultdict(lambda: {"dodis": set(), **{s: set() for s in SCHEMES}})
    for r in rows:
        q = r["item"].rsplit("/", 1)[-1]
        items[q]["dodis"].add(_pid(r["dodis"]))
        for s in SCHEMES:
            if r.get(s):
                items[q][s].add(r[s].strip())
    by_pid: dict[str, list[str]] = defaultdict(list)
    for q, e in items.items():
        for pid in e["dodis"]:
            by_pid[pid].append(q)
    persons, ambiguous, multi = {}, {}, Counter()
    for pid, qs in sorted(by_pid.items(), key=lambda kv: (len(kv[0]), kv[0])):
        if len(qs) > 1:
            ambiguous[pid] = sorted(qs)
            continue
        q = qs[0]
        row = {"wikidata": q}
        for s in SCHEMES:
            vals = items[q][s]
            if len(vals) == 1:
                row[s] = next(iter(vals)).zfill(6) if s == "hls" else next(iter(vals))
            elif len(vals) > 1:
                multi[s] += 1
        persons[pid] = row
    return {"persons": persons, "ambiguous": ambiguous, "multi_valued_left_out": dict(multi)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, help="a saved SPARQL answer instead of asking Wikidata")
    ap.add_argument("--queried", default=None, help="when the CSV was fetched (ISO), for the record")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    text = args.csv.read_text(encoding="utf-8") if args.csv else sparql_csv()
    res = build(csv.DictReader(io.StringIO(text)))
    p = res["persons"]
    doc = {"_meta": {
        "what": "Dodis person IDs → Wikidata item, GND, HLS, VIAF (Wikidata P701, P227, P902, P214)",
        "queried": args.queried or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Wikidata (CC0), query in knowledge_hub/crosswalks/dodis_wikidata.py",
        "counts": {"persons": len(p), **{s: sum(s in r for r in p.values()) for s in SCHEMES},
                   "ambiguous": len(res["ambiguous"])},
        "use": "dodis.py: owl:sameAs on each (#830); ambiguous Dodis IDs get nothing",
    }, **res}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(doc["_meta"]["counts"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
