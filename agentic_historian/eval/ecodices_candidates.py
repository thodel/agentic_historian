"""Which e-codices volumes to measure Agent B and the hand against (#779, #773).

``ecodices_bench fetch`` takes ids. Choosing 30 of e-codices' ~2900 volumes by
hand is where a benchmark goes quietly wrong: the volumes one happens to know
are the famous ones, which are the ones a model is most likely to have read
about. This step chooses from the catalogue itself, deterministically, and
writes down why each volume was taken.

1. **Discover ids** from e-codices' OAI-PMH interface (``ListIdentifiers``,
   with resumption tokens), or from a file of ids (``--ids-file``) when the
   endpoint answers otherwise than expected.
2. **Inspect** up to ``max_inspect`` of them: the IIIF manifest (page count,
   the TEI it links) and the TEI (date, hands, script, licence), cached under
   ``<dir>/_candidates/<id>/`` so a second run costs nothing. Preferred
   prefixes (``saa`` — archive holdings, closest to our records) are inspected
   first, the rest in an even spread over the sorted list.
3. **Select** ``n``: only volumes with a date and at least ``min_pages`` pages
   (the hand needs pages to compare); round-robin over centuries so no century
   fills the set; within one, volumes with several catalogued hands first, then
   one hand, then hands unstated; preferred prefixes before others at each
   tier; ties by id. ``must`` volumes (``saa-0428``, which Agent B already
   described, #375) come first.
4. **Write** ``candidates.tsv`` (every inspected volume, its data and whether it
   was taken) and ``selected.txt``, and copy each selected volume's manifest and
   TEI into ``<dir>/<id>/`` — the layout ``fetch`` leaves, so ``run``, ``score``
   and ``hand_consistency validate`` go on from there.

The OAI base URL and the manifest URL are e-codices' as documented; this
project's cloud sessions cannot reach e-codices, so the first run on tei is
where they meet the real service. A wrong guess fails loudly, and
``--oai-url`` / ``--ids-file`` route around it.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

OAI_URL = "https://www.e-codices.unifr.ch/oai/oai.php"
OAI = "{http://www.openarchives.org/OAI/2.0/}"
ID_RE = re.compile(r"^[a-z]{2,6}-[0-9a-z][0-9a-z_.-]*$")
MUST = ("saa-0428",)
PREFER = ("saa",)


def vol_id_of(identifier: str) -> str | None:
    """``oai:e-codices.unifr.ch:saa-0428`` (or a URL ending in it) → ``saa-0428``."""
    tail = re.split(r"[:/]", (identifier or "").strip().rstrip("/"))[-1].lower()
    return tail if ID_RE.match(tail) else None


def list_ids_oai(get: Callable[[str], bytes], base: str = OAI_URL,
                 max_pages: int = 200) -> list[str]:
    """Every volume id the OAI-PMH interface lists, following resumption tokens."""
    ids: list[str] = []
    url = f"{base}?verb=ListIdentifiers&metadataPrefix=oai_dc"
    for page in range(max_pages):
        root = ET.fromstring(get(url))
        err = root.find(f"{OAI}error")
        if err is not None:
            raise RuntimeError(f"OAI-PMH error {err.get('code')}: {(err.text or '').strip()}")
        for h in root.iter(f"{OAI}header"):
            if h.get("status") == "deleted":
                continue
            vid = vol_id_of(h.findtext(f"{OAI}identifier") or "")
            if vid:
                ids.append(vid)
        token = root.find(f".//{OAI}resumptionToken")
        size = token.get("completeListSize") if token is not None else None
        print(f"OAI page {page + 1}: {len(ids)} id(s) so far"
              + (f" of {size}" if size else ""), file=sys.stderr)
        if token is None or not (token.text or "").strip():
            if size and size.isdigit() and int(size) > len(set(ids)):
                print(f"OAI: the server announced {size} records but the listing ended at "
                      f"{len(set(ids))}; it may be incomplete (--ids-file routes around it)",
                      file=sys.stderr)
            break
        url = f"{base}?verb=ListIdentifiers&resumptionToken={quote(token.text.strip(), safe='')}"
    return sorted(set(ids))


def inspection_order(ids: Iterable[str], max_inspect: int,
                     prefer: Iterable[str] = PREFER, must: Iterable[str] = MUST) -> list[str]:
    """Which ids to inspect: ``must``, then preferred prefixes, then an even spread of the rest."""
    ids = sorted(set(ids) | set(must))
    prefer = tuple(prefer)
    first = [i for i in must if i in ids]
    pref = [i for i in ids if i.split("-")[0] in prefer and i not in first]
    rest = [i for i in ids if i not in first and i not in pref]
    room = max(0, max_inspect - len(first) - len(pref))
    if room and rest:
        step = len(rest) / min(room, len(rest))
        rest = [rest[int(k * step)] for k in range(min(room, len(rest)))]
    else:
        rest = []
    return (first + pref + rest)[:max(max_inspect, len(first))]


@dataclass
class Candidate:
    id: str
    pages: int
    date_from: int | None
    date_to: int | None
    hands: int | None
    families: str
    licence: str
    published: str

    @property
    def century(self) -> int | None:
        return None if self.date_from is None else self.date_from // 100 + 1


def inspect(vol_id: str, cache: Path, get: Callable[[str], bytes]) -> Candidate | None:
    """Manifest and TEI of one volume (cached); None when either cannot be had."""
    from eval import ecodices_bench as eb

    vdir = cache / vol_id
    vdir.mkdir(parents=True, exist_ok=True)
    man_path, tei_path = vdir / "manifest.json", vdir / "tei.xml"
    try:
        if not man_path.exists():
            man_path.write_bytes(get(eb.IIIF_MANIFEST.format(id=vol_id)))
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
        if not tei_path.exists():
            url = eb.tei_url(manifest)
            if not url:
                return None
            tei_path.write_bytes(get(url))
        ref = eb.parse_tei(tei_path.read_bytes(), vol_id)
    except Exception as exc:  # noqa: BLE001 — one volume's failure is named, not fatal
        print(f"{vol_id}: not inspected ({type(exc).__name__}: {exc})", file=sys.stderr)
        return None
    return Candidate(id=vol_id, pages=len(eb.canvases(manifest)), date_from=ref.date_from,
                     date_to=ref.date_to, hands=ref.hands,
                     families=",".join(ref.script_families), licence=ref.licence or "",
                     published=ref.published or "")


def select(cands: Iterable[Candidate], n: int = 30, min_pages: int = 10,
           prefer: Iterable[str] = PREFER, must: Iterable[str] = MUST) -> list[str]:
    """``n`` ids: ``must`` first, then round-robin over centuries (see the module doc)."""
    prefer = tuple(prefer)
    cands = [c for c in cands if c.century is not None and c.pages >= min_pages]
    chosen = [c.id for c in sorted(cands, key=lambda c: c.id) if c.id in set(must)]

    def rank(c: Candidate):
        tier = 0 if (c.hands or 0) > 1 else 1 if c.hands == 1 else 2
        return (tier, 0 if c.id.split("-")[0] in prefer else 1, c.id)

    by_century: dict[int, list[Candidate]] = {}
    for c in cands:
        if c.id not in chosen:
            by_century.setdefault(c.century, []).append(c)
    queues = [sorted(v, key=rank) for _, v in sorted(by_century.items())]
    while len(chosen) < n and any(queues):
        for q in queues:
            if q and len(chosen) < n:
                chosen.append(q.pop(0).id)
    return chosen[:n]


def candidates(bench_dir: Path, *, get: Callable[[str], bytes], n: int = 30,
               max_inspect: int = 300, min_pages: int = 10, ids: list[str] | None = None,
               oai_url: str = OAI_URL, prefer: Iterable[str] = PREFER,
               must: Iterable[str] = MUST, max_failures: int = 10) -> list[str]:
    """Discover, inspect, select, write; returns the selected ids.

    After ``max_failures`` volumes in a row could not be read, inspection stops:
    that is the server refusing, not 300 bad volumes. What was read is cached;
    a later run continues from there.
    """
    bench_dir.mkdir(parents=True, exist_ok=True)
    cache = bench_dir / "_candidates"
    pool = ids if ids is not None else list_ids_oai(get, oai_url)
    order = inspection_order(pool, max_inspect, prefer, must)
    print(f"{len(pool)} id(s) listed; inspecting {len(order)}", file=sys.stderr)
    cands: list[Candidate] = []
    failed = 0
    for i in order:
        c = inspect(i, cache, get)
        if c:
            cands.append(c)
            failed = 0
        elif not (cache / i / "manifest.json").exists():
            failed += 1
            if max_failures and failed >= max_failures:
                print(f"stopped after {failed} volumes in a row could not be read — e-codices "
                      f"is refusing; {len(cands)} read and cached, run again later",
                      file=sys.stderr)
                break
    chosen = select(cands, n, min_pages, prefer, must)
    taken = set(chosen)
    rows = ["id\tselected\tpages\tcentury\tdate_from\tdate_to\thands\tfamilies\tlicence\tpublished"]
    for c in sorted(cands, key=lambda c: c.id):
        rows.append("\t".join(str(v) if v is not None else "" for v in (
            c.id, "yes" if c.id in taken else "", c.pages, c.century, c.date_from, c.date_to,
            c.hands, c.families, c.licence, c.published)))
    (bench_dir / "candidates.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (bench_dir / "selected.txt").write_text("\n".join(chosen) + "\n", encoding="utf-8")
    for vid in chosen:
        dest = bench_dir / vid
        dest.mkdir(exist_ok=True)
        for name in ("manifest.json", "tei.xml"):
            shutil.copyfile(cache / vid / name, dest / name)
    known = [c for c in cands if c.id in taken and c.hands is not None]
    print(f"selected {len(chosen)} of {len(cands)} inspected; hands known for {len(known)} "
          f"({sum(1 for c in known if c.hands > 1)} with several)", file=sys.stderr)
    return chosen
