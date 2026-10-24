#!/usr/bin/env python3
"""What the Transkribus collection already holds of the BLB's Laßberg set.

The BLB's ALTO full text is character-identical to this project's hand-corrected
Transkribus pages where both exist (measured 2026-10-23: 0.0 % on five of six
pages, 2.8 % on the sixth). So before any BLB object becomes a *new* Transkribus
document, the question is which of the 426 are already there. This lists the
collection's documents, reads the shelfmark out of each title, and sets the two
against each other.

Runs with the project's venv from the package directory, because it imports the
project's `transkribus` module (login from the environment, never argv):

    cd /home/dh/agentic_historian/agentic_historian
    ../.venv/bin/python3 ../scripts/transkribus_vs_blb.py --inventory /tmp/blb/inventory.tsv
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

# The package directory: the working directory when it holds config.py (the
# documented way to run this), else the repository layout beside this file.
_cwd = Path.cwd()
sys.path.insert(0, str(_cwd if (_cwd / "config.py").is_file()
                       else Path(__file__).resolve().parents[1] / "agentic_historian"))

import config                     # noqa: E402
import transkribus as tk          # noqa: E402

# The series, then one or more numbers separated by comma, dash or space.
# Transkribus titles write the BLB's "K 3123,8,4" as "done_BLB_K 3123 8 4"
# (24.10.: 675 of 731 titles in 36479 did not match the comma-only form).
SHELF = re.compile(r"K[\s_]?(\d{4})(?:[,\s]\s*|_)(\d+(?:\s*[-,\s]\s*\d+)*)")


def norm(shelf: str) -> str:
    """One key for every spelling: ``K 3123,8,4``, ``K 3123 8 4`` → ``K3123-8-4``."""
    return "K" + "-".join(re.findall(r"\d+", shelf or ""))


def shelf_of(title: str) -> str | None:
    m = SHELF.search(title or "")
    return norm(m.group(0)) if m else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--inventory", required=True, help="inventory.tsv that oai-gt-import --step inventory wrote (ATR_DATASETS_DIR/_oai/blb-lassberg/)")
    ap.add_argument("--collection", default=None, help="Transkribus collection id (default TRANSKRIBUS_COLLECTION)")
    ap.add_argument("--out", default="/tmp/blb/transkribus_vs_blb.tsv")
    args = ap.parse_args()

    try:
        sid = tk.login()
        cols = tk.list_collections(sid)
    except tk.TranskribusError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    # Which collections to read: the one named, else every collection whose
    # name says Laßberg. TRANSKRIBUS_COLLECTION names the *upload* target, and
    # on 2026-10-23 that one (Lassberg_All) held 0 documents while the 552
    # corrected pages lived elsewhere — the name in the config is not the
    # collection the ground truth is in.
    if args.collection:
        chosen = [c for c in cols if str(c.get("colId")) == str(args.collection)]
    else:
        chosen = [c for c in cols if re.search(r"la(ss|ß)berg", str(c.get("colName", "")), re.I)]
    print(f"collections visible: {len(cols)}; Laßberg collections: {len(chosen)}")
    for c in chosen:
        print(f"  {c.get('colId')}  {c.get('colName')!r}  documents: {c.get('nrOfDocuments', '?')}")
    if not chosen:
        print("Error: no collection chosen — pass --collection", file=sys.stderr)
        return 2
    docs: list[dict] = []
    for c in chosen:
        try:
            these = tk.collection_documents(str(c.get("colId")), sid)
        except tk.TranskribusError as exc:
            print(f"  {c.get('colId')}: {exc}")
            continue
        for d in these:
            d["_colId"] = c.get("colId")
        docs += these
        print(f"  {c.get('colId')}: {len(these)} document(s) listed")
    colid = ",".join(str(c.get("colId")) for c in chosen)
    print(f"documents in total: {len(docs)}")
    if docs:
        print("fields of a document record:", sorted(docs[0].keys())[:30])

    tk_by_shelf: dict[str, list[dict]] = collections.defaultdict(list)
    untagged = []
    for d in docs:
        s = shelf_of(d.get("title", ""))
        if s:
            tk_by_shelf[s].append(d)
        else:
            untagged.append(d)
    print(f"documents with a K-shelfmark in the title: {sum(len(v) for v in tk_by_shelf.values())} "
          f"({len(tk_by_shelf)} distinct shelfmarks); without: {len(untagged)}")
    for d in untagged[:5]:
        print("   e.g.", d.get("docId"), repr(d.get("title"))[:70])

    blb: dict[str, dict] = {}
    with open(args.inventory, encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            for s in (row["shelfmark"] or "").split("; "):
                s = norm(s.strip()) if s.strip() else ""
                if s and (s not in blb or int(row["pages"] or 0) > int(blb[s]["pages"] or 0)):
                    blb[s] = row
    print(f"BLB inventory: {len(blb)} shelfmark(s)")

    both = sorted(s for s in blb if s in tk_by_shelf)
    only_blb = sorted((s for s in blb if s not in tk_by_shelf),
                      key=lambda s: [int(x) if x.isdigit() else x for x in re.findall(r"\d+|\D+", s)])
    only_tk = sorted(s for s in tk_by_shelf if s not in blb)
    pages_only_blb = sum(int(blb[s]["pages"] or 0) for s in only_blb)
    ft_only_blb = sum(1 for s in only_blb if blb[s]["fulltext"] == "yes")
    print(f"\nin both: {len(both)}")
    print(f"at the BLB, not in Transkribus: {len(only_blb)}  ({pages_only_blb} pages, {ft_only_blb} with full text)")
    print(f"in Transkribus, not at the BLB: {len(only_tk)}  {only_tk[:10]}")
    print("\nby series, BLB-only:")
    ser = collections.Counter(s.split(",")[0] for s in only_blb)
    for k, v in ser.most_common():
        print(f"  {k}: {v}")
    print("\nfirst BLB-only objects:")
    for s in only_blb[:25]:
        r = blb[s]
        print(f"  {s:<14} {r['pages']:>4} p  {'FT' if r['fulltext'] == 'yes' else '  '}  {r['date']:<22} {r['title'][:60]}")
    print("\nin both — Transkribus pages vs BLB pages (first 15):")
    for s in both[:15]:
        tkp = sum(int(d.get("nrOfPages") or 0) for d in tk_by_shelf[s])
        print(f"  {s:<14} transkribus {tkp:>3} p in {len(tk_by_shelf[s])} doc(s)  ·  BLB {blb[s]['pages']:>3} p")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("shelfmark\twhere\tblb_pages\tblb_fulltext\tblb_vl_id\ttranskribus_docids\ttranskribus_pages\ttitle\n")
        for s in sorted(set(blb) | set(tk_by_shelf)):
            where = "both" if s in blb and s in tk_by_shelf else ("blb" if s in blb else "transkribus")
            r = blb.get(s, {})
            tds = tk_by_shelf.get(s, [])
            fh.write("\t".join([s, where, r.get("pages", ""), r.get("fulltext", ""), r.get("vl_id", ""),
                                ",".join(f"{d.get('_colId')}/{d.get('docId')}" for d in tds),
                                str(sum(int(d.get("nrOfPages") or 0) for d in tds)),
                                (r.get("title") or (tds[0].get("title") if tds else "")).replace("\t", " ")]) + "\n")
    json.dump({"both": both, "only_blb": only_blb, "only_transkribus": only_tk},
              open(Path(args.out).with_suffix(".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nreport: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
