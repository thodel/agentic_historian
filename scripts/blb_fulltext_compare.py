#!/usr/bin/env python3
"""Is the BLB's full text a transcription or a machine reading? Measure it.

The Badische Landesbibliothek's Laßberg set carries an ALTO full text for 401
of 426 objects, producer not stated. A first glance at `K 2911,47` reads like a
careful diplomatic transcription of Laßberg's own hand — which no engine this
project has measured reads below 27 % CER. This script settles it the only way
that counts: against the pages for which *we* hold hand-corrected text.

For every ground-truth file ``done_K 2911,NN_<docId>__00M.xml`` under
``--gt-dir`` it fetches the BLB's ALTO for shelfmark ``K 2911,NN``, physical page
``M`` (through the METS cached by ``blb_lassberg_inventory.py``), and scores:

    BLB full text      against our ground truth
    our readings       against our ground truth   (the same pages, same scorer)
    BLB full text      against our best reading

CER as the project computes it: case, whitespace runs and punctuation ignored,
Levenshtein distance over the reference length. Standard library only; runs on
tei, where the BLB answers.

    python3 scripts/blb_fulltext_compare.py --oai-cache /tmp/blb \\
        --gt-dir agentic_historian/data/gt/eval-random \\
        --reading gemini-eval-random/gemini-3.8-flash \\
        --reading atr_corpus_qwen35_line/qwen3.5-4b-german-xix-v2
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "mets": "http://www.loc.gov/METS/",
    "mods": "http://www.loc.gov/mods/v3",
    "xlink": "http://www.w3.org/1999/xlink",
}
PAGE_NS = {"p": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"}
UA = "agentic_historian/blb-fulltext-compare (research; tei.dh.unibe.ch)"


# ── texts ────────────────────────────────────────────────────────────────────

def alto_text(data: bytes) -> str:
    """Lines of an ALTO file, words joined by spaces, hyphenation kept as is."""
    root = ET.fromstring(data)
    ns = re.match(r"\{([^}]+)\}", root.tag)
    a = {"a": ns.group(1)} if ns else {}
    lines = []
    for tl in root.iter(f"{{{a['a']}}}TextLine" if a else "TextLine"):
        words = []
        for el in tl:
            tag = el.tag.split("}")[-1]
            if tag == "String" and el.get("CONTENT") is not None:
                words.append(el.get("CONTENT"))
            elif tag == "HYP":
                words.append(el.get("CONTENT") or "-")
        if words:
            lines.append(" ".join(words))
    return "\n".join(lines)


def page_text(path: Path) -> str:
    """The text of a PAGE XML file, region TextEquiv first, lines as fallback —
    the same rule as `gt_score.read_pagexml`."""
    root = ET.parse(path).getroot()
    chunks = []
    for region in root.findall(".//p:TextRegion", PAGE_NS):
        direct = region.findall("p:TextEquiv/p:Unicode", PAGE_NS)
        if direct and (direct[0].text or "").strip():
            chunks.append(direct[0].text)
            continue
        for te in region.findall("p:TextLine/p:TextEquiv/p:Unicode", PAGE_NS):
            if (te.text or "").strip():
                chunks.append(te.text)
    return "\n".join(c.strip("\r\n") for c in chunks if c).strip()


_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalise(s: str) -> str:
    s = unicodedata.normalize("NFC", s).lower()
    s = _PUNCT.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str) -> float:
    r, h = normalise(reference), normalise(hypothesis)
    if not r:
        return float("nan")
    return levenshtein(r, h) / len(r)


# ── the BLB side ─────────────────────────────────────────────────────────────

def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def norm_shelf(s: str) -> str:
    return re.sub(r"\s*,\s*", ",", re.sub(r"\s+", " ", s.strip()))


def alto_pages_by_shelfmark(oai_cache: Path) -> dict[str, list[str]]:
    """shelfmark → ALTO hrefs in physical page order, from the cached OAI METS.

    The physical structMap's page divs carry ``fptr`` elements naming files in
    every group; the FULLTEXT group's file for a page is the one whose ID the
    page's fptr names. Order is the ``ORDER`` attribute, then document order.
    """
    out: dict[str, list[str]] = {}
    for f in sorted(glob.glob(str(oai_cache / "oai_mets_*.xml"))):
        root = ET.parse(f).getroot()
        for mets in root.findall(".//mets:mets", NS):
            shelf = [norm_shelf(e.text) for e in mets.findall(".//mods:shelfLocator", NS) if e.text]
            if not shelf:
                continue
            ft_files: dict[str, str] = {}
            for grp in mets.findall(".//mets:fileSec/mets:fileGrp", NS):
                if (grp.get("USE") or "").upper() in ("FULLTEXT", "ALTO"):
                    for fl in grp.findall("mets:file", NS):
                        loc = fl.find("mets:FLocat", NS)
                        href = loc.get(f"{{{NS['xlink']}}}href") if loc is not None else None
                        if fl.get("ID") and href:
                            ft_files[fl.get("ID")] = href
            pages = mets.findall(".//mets:structMap[@TYPE='PHYSICAL']//mets:div[@TYPE='page']", NS)
            ordered = sorted(pages, key=lambda d: int(d.get("ORDER") or 0))
            hrefs = []
            for d in ordered:
                href = ""
                for fp in d.findall("mets:fptr", NS):
                    if fp.get("FILEID") in ft_files:
                        href = ft_files[fp.get("FILEID")]
                        break
                hrefs.append(href)
            for s in shelf:
                out.setdefault(s, hrefs)
    return out


# ── main ─────────────────────────────────────────────────────────────────────

GT_NAME = re.compile(r"^done_(K \d+,\d+)_\d+__(\d+)\.xml$")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--oai-cache", default="/tmp/blb", help="directory blb_lassberg_inventory.py wrote")
    ap.add_argument("--gt-dir", required=True, help="ground-truth set with done_K 2911,NN_<docId>__00M.xml files")
    ap.add_argument("--vlm-root", default="agentic_historian/data/vlm_test")
    ap.add_argument("--reading", action="append", default=[],
                    help="<run>/<model> under --vlm-root whose <key>.txt to score too; repeatable")
    ap.add_argument("--holding", default="blb lassberg", help="the share's folder name for the BLB")
    ap.add_argument("--dump", help="directory to write the fetched BLB texts and the GT texts to")
    args = ap.parse_args()

    by_shelf = alto_pages_by_shelfmark(Path(args.oai_cache))
    if not by_shelf:
        print(f"Error: no cached METS under {args.oai_cache} — run blb_lassberg_inventory.py first",
              file=sys.stderr)
        return 2
    gt_files = sorted(Path(args.gt_dir).glob("*.xml"))
    targets = []
    for f in gt_files:
        m = GT_NAME.match(f.name)
        if m:
            targets.append((norm_shelf(m.group(1)), int(m.group(2)), f))
    if not targets:
        print(f"Error: no done_K NNNN,NN_… files under {args.gt_dir}", file=sys.stderr)
        return 2
    print(f"{len(targets)} ground-truth page(s) with a BLB shelfmark under {args.gt_dir}\n")

    rows = []
    for shelf, page, gt_path in targets:
        gt = page_text(gt_path)
        hrefs = by_shelf.get(shelf)
        row = {"shelf": shelf, "page": page, "gt_chars": len(gt), "blb": None, "readings": {}}
        if not hrefs:
            row["note"] = "shelfmark not in the BLB set"
        elif page > len(hrefs):
            row["note"] = f"BLB object has {len(hrefs)} page(s), no page {page}"
        elif not hrefs[page - 1]:
            row["note"] = "no full text for this page"
        else:
            try:
                blb = alto_text(fetch(hrefs[page - 1]))
            except Exception as exc:  # noqa: BLE001 — one page's failure is one row
                row["note"] = f"fetch failed: {exc}"
                blb = ""
            if blb:
                row["blb"] = {"chars": len(blb), "cer_vs_gt": cer(gt, blb), "text": blb}
        key = f"{args.holding}__{shelf}__page_{page:04d}"
        for rd in args.reading:
            p = Path(args.vlm_root) / rd / f"{key}.txt"
            if p.is_file():
                txt = p.read_text(encoding="utf-8")
                row["readings"][rd] = {"chars": len(txt), "cer_vs_gt": cer(gt, txt),
                                       "cer_vs_blb": cer(row["blb"]["text"], txt) if row["blb"] else float("nan")}
            else:
                row["readings"][rd] = None
        if args.dump:
            d = Path(args.dump); d.mkdir(parents=True, exist_ok=True)
            (d / f"{key}.gt.txt").write_text(gt, encoding="utf-8")
            if row["blb"]:
                (d / f"{key}.blb.txt").write_text(row["blb"]["text"], encoding="utf-8")
        rows.append(row)

    def pct(x):
        return "   —  " if x is None or x != x else f"{x:6.1%}"

    heads = ["shelfmark", "page", "GT chars", "BLB chars", "BLB vs GT"]
    for rd in args.reading:
        heads += [f"{rd.split('/')[0]} vs GT", f"{rd.split('/')[0]} vs BLB"]
    print("| " + " | ".join(heads) + " |")
    print("|" + "|".join("---:" if i else "---" for i in range(len(heads))) + "|")
    for r in rows:
        cells = [r["shelf"], str(r["page"]), str(r["gt_chars"]),
                 str(r["blb"]["chars"]) if r["blb"] else "—",
                 pct(r["blb"]["cer_vs_gt"]) if r["blb"] else (r.get("note") or "—")]
        for rd in args.reading:
            x = r["readings"].get(rd)
            cells += [pct(x["cer_vs_gt"]) if x else "—", pct(x["cer_vs_blb"]) if x else "—"]
        print("| " + " | ".join(cells) + " |")

    scored = [r["blb"]["cer_vs_gt"] for r in rows if r["blb"]]
    if scored:
        scored.sort()
        med = scored[len(scored) // 2]
        print(f"\nBLB full text against our ground truth: median {med:.1%} over {len(scored)} page(s), "
              f"range {scored[0]:.1%}–{scored[-1]:.1%}")
        print("Reading: a few percent means a human transcription (the residue is two people's "
              "conventions); ten percent and more on a correspondent's hand, or twenty-five and "
              "more on Laßberg's own, is a machine.")
    for rd in args.reading:
        vals = sorted(x["cer_vs_gt"] for r in rows for k, x in r["readings"].items() if k == rd and x)
        if vals:
            print(f"{rd}: median {vals[len(vals)//2]:.1%} vs GT over {len(vals)} page(s)")
    if args.dump:
        print(f"\ntexts written to {args.dump}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
