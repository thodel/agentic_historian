#!/usr/bin/env python3
"""Inventory of the Badische Landesbibliothek's digitised Laßberg material.

The BLB's digital collections carry a Laßberg set (OAI-PMH set ``blbknajvl``,
"Joseph von Laßberg"). This script harvests that set, reads every record's
METS/MODS, and answers three questions against the shelfmark folders the
project's share holds (``blb lassberg/K 2911,NN``):

1. Which digitised objects exist at the BLB — shelfmark, title, date, pages.
2. Which of them carry a **full text** (a METS file group ``FULLTEXT``/``ALTO``
   /``TEI``), and what produced it — an HTR engine or a person.
3. Which shelfmarks the BLB has that the share does not (new images), and the
   other way round.

Standard library only, because it runs on tei where the project's venv is not
the shell's Python. Raw OAI pages are cached under ``--out`` so a second run
re-reads nothing; the BLB blocks datacentre addresses, so this runs on tei.

    python3 scripts/blb_lassberg_inventory.py \\
        --share-folders /tmp/blb_share_folders.txt --out /tmp/blb \\
        --sample-fulltext 2
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = "https://digital.blb-karlsruhe.de"
OAI = f"{BASE}/oai"
SET = "blbknajvl"
NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "mets": "http://www.loc.gov/METS/",
    "mods": "http://www.loc.gov/mods/v3",
    "xlink": "http://www.w3.org/1999/xlink",
    "dv": "http://dfg-viewer.de/",
}
UA = "agentic_historian/blb-inventory (research; tei.dh.unibe.ch)"


def get(url: str, *, retries: int = 3, timeout: int = 120) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{url}: {last}")


def harvest(out: Path, prefix: str) -> list[Path]:
    """All OAI pages of the set as files; resumable by presence."""
    out.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    token: str | None = None
    n = 0
    while True:
        path = out / f"oai_{prefix}_{n:03d}.xml"
        if path.exists():
            data = path.read_bytes()
        else:
            q = ({"verb": "ListRecords", "resumptionToken": token} if token
                 else {"verb": "ListRecords", "metadataPrefix": prefix, "set": SET})
            data = get(f"{OAI}?{urllib.parse.urlencode(q)}")
            path.write_bytes(data)
        pages.append(path)
        root = ET.fromstring(data)
        err = root.find("oai:error", NS)
        if err is not None:
            raise RuntimeError(f"OAI error on page {n}: {err.get('code')}: {err.text}")
        tok = root.find(".//oai:resumptionToken", NS)
        token = (tok.text or "").strip() if tok is not None else ""
        if not token:
            break
        n += 1
    return pages


def text(el, path: str) -> str:
    e = el.find(path, NS)
    return (e.text or "").strip() if e is not None and e.text else ""


def parse_record(rec) -> dict | None:
    header = rec.find("oai:header", NS)
    ident = text(header, "oai:identifier")
    if header is not None and header.get("status") == "deleted":
        return {"oai_id": ident, "deleted": True}
    mets = rec.find(".//mets:mets", NS)
    if mets is None:
        return {"oai_id": ident, "no_mets": True}
    mods = mets.find(".//mods:mods", NS)
    title = text(mods, "mods:titleInfo/mods:title") if mods is not None else ""
    shelf = [e.text.strip() for e in mods.findall(".//mods:shelfLocator", NS)
             if e is not None and e.text] if mods is not None else []
    date = ""
    if mods is not None:
        for p in ("mods:originInfo/mods:dateCreated", "mods:originInfo/mods:dateIssued"):
            date = date or text(mods, p)
    vl_id = ""
    for e in (mods.findall("mods:identifier", NS) if mods is not None else []):
        if (e.get("type") or "").lower() in ("vl", "vls", "local") and e.text:
            vl_id = e.text.strip()
    m = re.search(r"[:/](\d+)$", ident)
    vl_id = vl_id or (m.group(1) if m else "")
    groups: dict[str, int] = {}
    fulltext_urls: list[str] = []
    for grp in mets.findall(".//mets:fileSec/mets:fileGrp", NS):
        use = (grp.get("USE") or "").upper()
        files = grp.findall("mets:file", NS)
        groups[use] = len(files)
        if use in ("FULLTEXT", "ALTO", "TEI", "TXT"):
            for f in files:
                loc = f.find("mets:FLocat", NS)
                href = loc.get(f"{{{NS['xlink']}}}href") if loc is not None else None
                if href:
                    fulltext_urls.append(href)
    phys = mets.findall(".//mets:structMap[@TYPE='PHYSICAL']//mets:div[@TYPE='page']", NS)
    pages = len(phys) or max((n for u, n in groups.items() if u in ("DEFAULT", "MAX", "MIN")), default=0)
    owner = text(mets, ".//dv:rights/dv:owner")
    presentation = text(mets, ".//dv:links/dv:presentation")
    return {"oai_id": ident, "vl_id": vl_id, "title": title, "shelfmarks": shelf,
            "date": date, "pages": pages, "file_groups": groups,
            "fulltext": bool(fulltext_urls), "fulltext_urls": fulltext_urls[:3],
            "owner": owner, "presentation": presentation}


def norm_shelf(s: str) -> str:
    """``K 2911, 47`` / ``K 2911,47 `` → ``K 2911,47``; keeps sub-numbers."""
    s = re.sub(r"\s+", " ", s.strip())
    s = re.sub(r"\s*,\s*", ",", s)
    return s


def series_of(shelf: str) -> str:
    m = re.match(r"(K \d+)", shelf)
    return m.group(1) if m else shelf.split(",")[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="/tmp/blb", help="cache and report directory")
    ap.add_argument("--share-folders", help="file with one share folder name per line "
                                            "(ls of 'blb lassberg')")
    ap.add_argument("--prefix", default="mets", help="OAI metadataPrefix (mets)")
    ap.add_argument("--sample-fulltext", type=int, default=0,
                    help="download this many full-text files and print their head")
    args = ap.parse_args()
    out = Path(args.out)

    pages = harvest(out, args.prefix)
    records: list[dict] = []
    for p in pages:
        root = ET.fromstring(p.read_bytes())
        for rec in root.findall(".//oai:record", NS):
            r = parse_record(rec)
            if r:
                records.append(r)
    live = [r for r in records if not r.get("deleted") and not r.get("no_mets")]
    (out / "inventory.json").write_text(json.dumps(records, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    with (out / "inventory.tsv").open("w", encoding="utf-8") as fh:
        fh.write("vl_id\tshelfmark\tdate\tpages\tfulltext\tfile_groups\ttitle\tpresentation\n")
        for r in sorted(live, key=lambda r: (series_of(norm_shelf(r["shelfmarks"][0])) if r["shelfmarks"] else "~",
                                            r["shelfmarks"][0] if r["shelfmarks"] else "")):
            fh.write("\t".join([r["vl_id"], "; ".join(norm_shelf(s) for s in r["shelfmarks"]),
                                r["date"], str(r["pages"]), "yes" if r["fulltext"] else "no",
                                ",".join(f"{k}:{v}" for k, v in sorted(r["file_groups"].items())),
                                r["title"].replace("\t", " "), r["presentation"]]) + "\n")

    print(f"OAI set {SET}: {len(records)} record(s) on {len(pages)} page(s); "
          f"{len(live)} with METS, {sum(1 for r in records if r.get('deleted'))} deleted, "
          f"{sum(1 for r in records if r.get('no_mets'))} without METS")
    total_pages = sum(r["pages"] for r in live)
    with_ft = [r for r in live if r["fulltext"]]
    print(f"pages (physical structMap): {total_pages}")
    print(f"with a full-text file group: {len(with_ft)} of {len(live)}")
    uses = collections.Counter(u for r in live for u in r["file_groups"])
    print("file groups seen:", dict(uses))
    by_series = collections.Counter(series_of(norm_shelf(r['shelfmarks'][0])) if r["shelfmarks"] else "(no shelfmark)"
                                    for r in live)
    print("by series:", dict(by_series.most_common()))

    if args.share_folders:
        share = {norm_shelf(ln) for ln in Path(args.share_folders).read_text(encoding="utf-8").splitlines() if ln.strip()}
        blb = {}
        for r in live:
            for s in r["shelfmarks"]:
                # Anchor records without files carry the same shelfmark as the
                # object with the pages; keep the one with pages.
                k = norm_shelf(s)
                if k not in blb or r["pages"] > blb[k]["pages"]:
                    blb[k] = r
        # a share folder "K 2911,47" matches a BLB shelfmark "K 2911,47" or a sub-item "K 2911,47,1"
        def in_blb(folder: str) -> bool:
            return folder in blb or any(k.startswith(folder + ",") for k in blb)
        new_at_blb = sorted((k for k in blb if k not in share and not any(k.startswith(f + ",") for f in share)),
                            key=lambda k: (series_of(k), [int(x) if x.isdigit() else x for x in re.findall(r"\d+|\D+", k)]))
        only_share = sorted(f for f in share if not in_blb(f))
        print(f"\nshare folders: {len(share)}  ·  BLB shelfmarks: {len(blb)}")
        print(f"at the BLB and not in the share: {len(new_at_blb)}  "
              f"({sum(blb[k]['pages'] for k in new_at_blb)} pages)")
        for k in new_at_blb[:60]:
            r = blb[k]
            print(f"  {k:<14} {r['pages']:>4} p  {'FT' if r['fulltext'] else '  '}  {r['date']:<22} {r['title'][:70]}")
        if len(new_at_blb) > 60:
            print(f"  … {len(new_at_blb) - 60} more in inventory.tsv")
        print(f"in the share and not at the BLB: {len(only_share)}")
        for f in only_share[:20]:
            print("  ", f)
        (out / "new_at_blb.txt").write_text("\n".join(new_at_blb) + "\n", encoding="utf-8")

    if args.sample_fulltext:
        shown = 0
        for r in with_ft:
            for u in r["fulltext_urls"][:1]:
                try:
                    data = get(u)
                except RuntimeError as exc:
                    print(f"\n[fulltext] {u}: {exc}")
                    continue
                head = data[:4000].decode("utf-8", errors="replace")
                print(f"\n[fulltext] {r['shelfmarks']} {u}")
                prod = re.findall(r"<(?:softwareName|processingSoftware|softwareCreator)[^>]*>([^<]{0,80})", head)
                print("  producer:", prod[:3] or "(not stated in header)")
                strings = re.findall(r'CONTENT="([^"]{1,60})"', head)
                print("  first words:", " ".join(strings[:25]) if strings else re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", head))[:300])
                shown += 1
            if shown >= args.sample_fulltext:
                break
        if not with_ft:
            print("\n[fulltext] no record carries a full-text file group")
    print(f"\nreport: {out}/inventory.tsv  (and inventory.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
