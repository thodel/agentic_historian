"""#779 — Agent B against the catalogue: e-codices descriptions as ground truth.

Agent B has no ground truth. What is measured about it is instability: the same
images were described as "Kursive", "Fraktur" and "Gotische Textura" in three
runs (#379). Whether a change (#771 more pages, #775 a ranked script, #776
historian prototypes) makes the description better cannot be said, only that it
makes it different.

e-codices publishes, for nearly every volume, the holding library's scholarly
description as TEI P5 (``msDesc``) and the images over IIIF. The cataloguer saw
the whole volume, which is exactly what Agent B, shown one page, cannot: the
number of hands is the clearest case.

Three phases, deliberately separate (as in ``vlm_bench``)
--------------------------------------------------------
``fetch`` needs the network and ``run`` the VLM; ``score`` needs neither. The
expensive halves run once, on tei — this project's cloud sessions cannot reach
e-codices.ch (31.10.2026: empty replies and 502) — and every judgement lives in
``score``, which CI tests.

* ``fetch``: per volume the TEI and the IIIF manifest, saved under
  ``<dir>/<id>/``. The licence is read from each TEI file's ``availability``
  rather than assumed for the collection: CC BY 3.0 and the Public Domain Mark
  both occur. The TEI is used to measure and is not republished.
* ``run``: Agent B describes each volume from page images only — never from the
  TEI — R times, ``--pages first`` (what production sends today, ``pages[0]``)
  or ``--pages spread`` (k pages at even intervals, #771). It uses Agent B's
  image-only path, the one production takes when the transcription is unusable;
  Schrift, Datierung, Schreiber and Beschreibstoff are read off the page in both.
* ``score``: per element hit / miss / not in the catalogue, and the stability of
  each element over the R runs.

What a number here is not
-------------------------
* **Contamination.** A model may have read these descriptions in training; a hit
  on a long-published volume proves less than one on a new one. The report
  splits by the year the description was published when the TEI gives it.
* **Book hands, not chancery.** e-codices is mostly codices; our corpus is mostly
  administrative records. Archive holdings (``saa-``, ``sbe-``…) are closest.
* **Script families, not names.** A catalogue's "Textualis formata" and Agent B's
  "gotische Buchschrift" are the same family; the comparison is on families
  (``SCRIPT_FAMILIES``), and a description naming none is "not in the catalogue",
  never a miss.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional
from xml.etree import ElementTree as ET

TEI = "{http://www.tei-c.org/ns/1.0}"
IIIF_MANIFEST = "https://www.e-codices.unifr.ch/metadata/iiif/{id}/manifest.json"
ELEMENTS = ("Schrift", "Datierung", "Schreiber", "Beschreibstoff")

#: Script families, by the words catalogues and Agent B use for them, in the
#: languages e-codices descriptions are written in (de, fr, it, en, la). Order
#: matters: the first family whose word occurs wins for Agent B's single label,
#: so the specific names come before the generic ones.
SCRIPT_FAMILIES: dict[str, tuple[str, ...]] = {
    "kurrent": ("kurrent",),
    "fraktur": ("fraktur",),
    "bastarda": ("bastard",),
    "humanistisch": ("humanist", "antiqua", "umanistic"),
    "karolingisch": ("karoling", "carolin", "caroline"),
    "beneventanisch": ("beneventan",),
    "insular": ("insular",),
    "rotunda": ("rotunda",),
    "textualis": ("textualis", "textura", "gotische buchschrift", "gothique livresque",
                  "littera textualis", "gotica libraria"),
    "kursive": ("kursiv", "cursiv", "corsiv", "notula", "kanzleischrift", "chancery"),
    "gotisch": ("gotisch", "gothique", "gotica", "gothic"),
}

#: The gothic families. "gotisch" alone is the generic name: a catalogue that says
#: only "gotische Minuskel" is not contradicted by Agent B's "Textura", nor the
#: other way round. Two different specific names still differ.
GOTHIC = {"textualis", "kursive", "bastarda", "rotunda", "fraktur", "gotisch"}


def script_matches(answer: str, catalogue: list[str]) -> bool:
    """Agent B's family against the catalogue's, with "gotisch" as the generic one."""
    if answer in catalogue:
        return True
    specific = [f for f in catalogue if f != "gotisch"]
    if answer == "gotisch":
        return bool(catalogue) and all(f in GOTHIC for f in catalogue)
    return answer in GOTHIC and "gotisch" in catalogue and not any(f in GOTHIC for f in specific)


MATERIALS = {"perg": "pergament", "chart": "papier", "mixed": "gemischt"}


# ── the catalogue side ───────────────────────────────────────────────────────

@dataclass
class Reference:
    """What the catalogue says about one volume, as far as score() compares it."""

    id: str
    licence: Optional[str] = None
    published: Optional[str] = None
    script_text: str = ""
    script_families: list = field(default_factory=list)
    hands: Optional[int] = None
    date_from: Optional[int] = None
    date_to: Optional[int] = None
    material: Optional[str] = None
    leaves: Optional[str] = None


def _text(el) -> str:
    return " ".join(" ".join(el.itertext()).split()) if el is not None else ""


def families(text: str) -> list[str]:
    """Every script family a text names, in SCRIPT_FAMILIES order."""
    low = (text or "").lower()
    return [fam for fam, words in SCRIPT_FAMILIES.items() if any(w in low for w in words)]


def _year(value: Optional[str]) -> Optional[int]:
    m = re.match(r"-?\d{3,4}", value or "")
    return int(m.group()) if m else None


def parse_tei(data: bytes, vol_id: str) -> Reference:
    """The catalogue's word on script, hands, date and material. Missing is None."""
    root = ET.fromstring(data)
    ref = Reference(id=vol_id)
    avail = root.find(f".//{TEI}publicationStmt/{TEI}availability")
    if avail is not None:
        lic = avail.find(f"{TEI}licence")
        ref.licence = (lic.get("target") if lic is not None and lic.get("target")
                       else _text(lic) or avail.get("status"))
    pub = root.find(f".//{TEI}publicationStmt/{TEI}date")
    if pub is not None:
        ref.published = pub.get("when") or _text(pub)
    ms = root.find(f".//{TEI}msDesc")
    if ms is None:
        return ref
    hand_desc = ms.find(f".//{TEI}handDesc")
    script_desc = ms.find(f".//{TEI}scriptDesc")
    ref.script_text = " ".join(t for t in (_text(script_desc), _text(hand_desc)) if t)
    for note in ms.iter(f"{TEI}handNote"):
        if note.get("script"):
            ref.script_text += f" {note.get('script')}"
    ref.script_families = families(ref.script_text)
    if hand_desc is not None:
        if (hand_desc.get("hands") or "").isdigit():
            ref.hands = int(hand_desc.get("hands"))
        elif hand_desc.findall(f"{TEI}handNote"):
            ref.hands = len(hand_desc.findall(f"{TEI}handNote"))
    orig = ms.find(f".//{TEI}origDate")
    if orig is not None:
        ref.date_from = _year(orig.get("notBefore") or orig.get("when") or orig.get("from"))
        ref.date_to = _year(orig.get("notAfter") or orig.get("when") or orig.get("to"))
    support = ms.find(f".//{TEI}supportDesc")
    if support is not None and support.get("material"):
        ref.material = MATERIALS.get(support.get("material"), support.get("material"))
    extent = ms.find(f".//{TEI}extent")
    ref.leaves = _text(extent) or None
    return ref


# ── Agent B's side ───────────────────────────────────────────────────────────

def _wert(el) -> str:
    if isinstance(el, dict):
        return str(el.get("wert") or "")
    return "" if el is None else str(el)


_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9,
          "x": 10, "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15, "xvi": 16,
          "xvii": 17, "xviii": 18, "xix": 19, "xx": 20}


def date_range(text: str) -> Optional[tuple[int, int]]:
    """A year range from Agent B's Datierung. None when it names no date.

    "1450", "1450–1460", "um 1450" (±10), "15. Jh.", "XV. Jh.", "1. Hälfte 15. Jh.",
    "Mitte 15. Jh.", "Ende 15. Jh.", "14./15. Jh."
    """
    low = (text or "").lower()
    years = [int(y) for y in re.findall(r"(?<!\d)(1\d{3}|[5-9]\d{2})(?!\d)", low)]
    if years:
        lo, hi = min(years), max(years)
        if len(years) == 1 and re.search(r"\b(um|ca\.?|circa|vers|circa)\b", low):
            return lo - 10, hi + 10
        return lo, hi
    cents: list[int] = []
    m = re.search(r"(\d{1,2})\s*\.?\s*(?:/\s*(\d{1,2})\s*\.?\s*)?(?:jh|jahrh)", low)
    if m:
        cents = [int(m.group(1))] + ([int(m.group(2))] if m.group(2) else [])
    if not cents:
        roman = re.search(r"\b([xvi]{1,5})\s*\.?\s*(?:jh|jahrh|s\.|siècle|sec)", low)
        if roman and roman.group(1) in _ROMAN:
            cents = [_ROMAN[roman.group(1)]]
    if not cents:
        return None
    lo, hi = (min(cents) - 1) * 100 + 1, max(cents) * 100
    if len(set(cents)) == 1:
        if re.search(r"(1\.|erste)\s*h[äa]lfte", low):
            hi = lo + 49
        elif re.search(r"(2\.|zweite)\s*h[äa]lfte", low):
            lo = lo + 50
        elif "mitte" in low:
            lo, hi = lo + 25, lo + 74
        elif re.search(r"\b(anfang|beginn|frühes|frühe)\b", low):
            hi = lo + 32
        elif re.search(r"\b(ende|spätes|späte)\b", low):
            lo = hi - 32
    return lo, hi


_WORDS = {"eine": 1, "ein": 1, "einer": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5}


def hand_count(text: str) -> Optional[str]:
    """``"1"``, ``"2"``…, ``">1"`` for "mehrere", or None when it says nothing countable."""
    low = (text or "").lower()
    if re.search(r"\bmehrere\b|\bverschiedene\b", low):
        return ">1"
    m = re.search(r"\b(\d+|eine?r?|zwei|drei|vier|fünf)\s+(hand|hände|haende|schreiber)", low)
    if m:
        word = m.group(1)
        return str(int(word) if word.isdigit() else _WORDS[word])
    return None


def material_of(text: str) -> Optional[str]:
    low = (text or "").lower()
    has_p, has_c = "pergament" in low, "papier" in low
    if has_p and has_c:
        return "gemischt"
    return "pergament" if has_p else "papier" if has_c else None


# ── score ────────────────────────────────────────────────────────────────────

HIT, MISS, NO_REF, NO_ANSWER = "hit", "miss", "not in catalogue", "no answer"


def judge(ref: Reference, source_json: dict) -> dict[str, str]:
    """Hit / miss / not in the catalogue / no answer, per element, for one run."""
    out = {}
    script = families(_wert(source_json.get("Schrift")))
    if not ref.script_families:
        out["Schrift"] = NO_REF
    elif not script:
        out["Schrift"] = NO_ANSWER
    else:
        out["Schrift"] = HIT if script_matches(script[0], ref.script_families) else MISS
    dates = date_range(_wert(source_json.get("Datierung")))
    if ref.date_from is None or ref.date_to is None:
        out["Datierung"] = NO_REF
    elif dates is None:
        out["Datierung"] = NO_ANSWER
    else:
        out["Datierung"] = HIT if dates[0] <= ref.date_to and ref.date_from <= dates[1] else MISS
    hands = hand_count(_wert(source_json.get("Schreiber")))
    if ref.hands is None:
        out["Schreiber"] = NO_REF
    elif hands is None:
        out["Schreiber"] = NO_ANSWER
    else:
        out["Schreiber"] = HIT if hands == str(ref.hands) or (hands == ">1" and ref.hands > 1) else MISS
    material = material_of(_wert(source_json.get("Beschreibstoff")))
    if ref.material is None:
        out["Beschreibstoff"] = NO_REF
    elif material is None:
        out["Beschreibstoff"] = NO_ANSWER
    else:
        out["Beschreibstoff"] = HIT if material == ref.material else MISS
    return out


def _answer(element: str, source_json: dict) -> Optional[str]:
    """The normalised answer whose agreement across runs is the stability."""
    value = _wert(source_json.get(element))
    if element == "Schrift":
        fams = families(value)
        return fams[0] if fams else None
    if element == "Datierung":
        r = date_range(value)
        return f"{r[0]}-{r[1]}" if r else None
    if element == "Schreiber":
        return hand_count(value)
    return material_of(value)


@dataclass
class VolumeScore:
    id: str
    runs: int
    judgements: dict = field(default_factory=dict)    # element -> Counter of verdicts
    stability: dict = field(default_factory=dict)     # element -> share agreeing with the mode
    published: Optional[str] = None


def score_volume(ref: Reference, runs: list[dict]) -> VolumeScore:
    vs = VolumeScore(id=ref.id, runs=len(runs), published=ref.published)
    for element in ELEMENTS:
        verdicts = Counter(judge(ref, sj)[element] for sj in runs)
        vs.judgements[element] = dict(verdicts)
        answers = [_answer(element, sj) for sj in runs]
        given = [a for a in answers if a is not None]
        vs.stability[element] = (round(Counter(given).most_common(1)[0][1] / len(runs), 3)
                                 if given else None)
    return vs


def summarise(scores: list[VolumeScore]) -> dict:
    """Per element: hits, misses, no answers over all runs that the catalogue can judge."""
    out = {}
    for element in ELEMENTS:
        total = Counter()
        for vs in scores:
            total.update(vs.judgements.get(element, {}))
        judged = total[HIT] + total[MISS] + total[NO_ANSWER]
        stab = [vs.stability[element] for vs in scores if vs.stability.get(element) is not None]
        out[element] = {
            "hit": total[HIT], "miss": total[MISS], "no_answer": total[NO_ANSWER],
            "not_in_catalogue": total[NO_REF],
            "hit_rate": round(total[HIT] / judged, 3) if judged else None,
            "mean_stability": round(sum(stab) / len(stab), 3) if stab else None,
        }
    return out


def format_report(scores: list[VolumeScore], meta: dict) -> str:
    summary = summarise(scores)
    lines = ["# Agent B gegen die Katalogbeschreibungen von e-codices (#779)", "",
             f"- Bände: **{len(scores)}**, Läufe je Band: {meta.get('runs')}, "
             f"Seiten: `{meta.get('pages')}`",
             f"- Agent-B-Konfiguration: {meta.get('agent_b', '—')}", "",
             "| Element | Treffer | Fehler | keine Antwort | nicht im Katalog | Trefferquote | Stabilität |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for element, s in summary.items():
        rate = "–" if s["hit_rate"] is None else f"{s['hit_rate']:.0%}"
        stab = "–" if s["mean_stability"] is None else f"{s['mean_stability']:.0%}"
        lines.append(f"| {element} | {s['hit']} | {s['miss']} | {s['no_answer']} | "
                     f"{s['not_in_catalogue']} | {rate} | {stab} |")
    lines += ["",
              "Trefferquote über die Läufe, die der Katalog beurteilen kann; «keine Antwort» "
              "zählt als nicht getroffen. Stabilität: Anteil der Läufe mit der häufigsten "
              "Antwort, im Mittel über die Bände.",
              "",
              "**Vorbehalte.** Die Beschreibungen können im Training der Modelle gewesen sein "
              "(siehe Jahr der Veröffentlichung je Band); e-codices ist vorwiegend "
              "Buchhandschrift; Schrift wird auf Familienebene verglichen.",
              "", "## Je Band", "",
              "| Band | veröffentlicht | Schrift | Datierung | Schreiber | Beschreibstoff |",
              "|---|---|---|---|---|---|"]
    for vs in scores:
        cells = []
        for element in ELEMENTS:
            j = vs.judgements.get(element, {})
            cells.append(", ".join(f"{k} {v}" for k, v in sorted(j.items())) or "–")
        lines.append(f"| `{vs.id}` | {vs.published or '–'} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def score(bench_dir: Path) -> tuple[list[VolumeScore], str]:
    """Read ``<dir>/<id>/tei.xml`` and ``runs/*.json``, judge, and write the report."""
    meta_path = bench_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    scores = []
    for vol in sorted(p for p in bench_dir.iterdir() if (p / "tei.xml").exists()):
        runs = [json.loads(r.read_text(encoding="utf-8")).get("source_json") or {}
                for r in sorted((vol / "runs").glob("*.json"))]
        if not runs:
            continue
        scores.append(score_volume(parse_tei((vol / "tei.xml").read_bytes(), vol.name), runs))
    report = format_report(scores, meta)
    (bench_dir / "report.md").write_text(report, encoding="utf-8")
    (bench_dir / "scores.json").write_text(json.dumps(
        {"summary": summarise(scores), "volumes": [asdict(s) for s in scores]},
        indent=2, ensure_ascii=False), encoding="utf-8")
    return scores, report


# ── fetch and run: on tei ────────────────────────────────────────────────────

def canvases(manifest: dict) -> list[str]:
    """Image URLs of a IIIF Presentation 2 or 3 manifest, in page order, at ≤1600 px."""
    urls = []
    for seq in manifest.get("sequences") or []:                       # v2
        for canvas in seq.get("canvases") or []:
            for image in canvas.get("images") or []:
                service = (image.get("resource") or {}).get("service") or {}
                sid = service.get("@id") or service.get("id")
                if sid:
                    urls.append(f"{sid.rstrip('/')}/full/!1600,1600/0/default.jpg")
    for canvas in manifest.get("items") or []:                         # v3
        for page in canvas.get("items") or []:
            for anno in page.get("items") or []:
                body = anno.get("body") or {}
                services = body.get("service") or []
                sid = (services[0].get("id") or services[0].get("@id")) if services else None
                if sid:
                    urls.append(f"{sid.rstrip('/')}/full/!1600,1600/0/default.jpg")
    return urls


def tei_url(manifest: dict) -> Optional[str]:
    """The TEI description a manifest links in ``seeAlso``, if it does."""
    see = manifest.get("seeAlso") or []
    for item in see if isinstance(see, list) else [see]:
        url = item if isinstance(item, str) else (item.get("@id") or item.get("id") or "")
        if url.endswith(".xml") or "tei" in url.lower():
            return url
    return None


def pick_pages(n: int, how: str, k: int = 5) -> list[int]:
    """Indices of the pages to show: ``first`` (production today) or ``spread`` (#771)."""
    if n <= 0:
        return []
    if how == "first":
        return [0]
    if n <= k:
        return list(range(n))
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def fetch(ids: Iterable[str], bench_dir: Path, *, get: Callable[[str], bytes],
          tei_urls: Optional[dict] = None) -> list[str]:
    """TEI and manifest per volume; returns the ids fetched. A volume without TEI is skipped, said."""
    done = []
    for vol_id in ids:
        vdir = bench_dir / vol_id
        vdir.mkdir(parents=True, exist_ok=True)
        manifest = json.loads(get(IIIF_MANIFEST.format(id=vol_id)))
        (vdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        url = (tei_urls or {}).get(vol_id) or tei_url(manifest)
        if not url:
            print(f"{vol_id}: the manifest links no TEI and none was given — skipped",
                  file=sys.stderr)
            continue
        (vdir / "tei.xml").write_bytes(get(url))
        ref = parse_tei((vdir / "tei.xml").read_bytes(), vol_id)
        print(f"{vol_id}: {len(canvases(manifest))} pages, licence {ref.licence or 'not stated'}")
        done.append(vol_id)
    return done


def run(bench_dir: Path, *, describe: Callable[[str, str, int], dict], runs: int = 3,
        pages: str = "first", get: Callable[[str], bytes]) -> None:
    """Agent B on each fetched volume, R times, from page images only."""
    if pages != "first":
        # Agent B takes one image today; several are #771. Measuring "spread" with
        # one of its pages would be a third configuration nobody runs.
        raise SystemExit("--pages spread needs Agent B to take several images (#771)")
    for vdir in sorted(p for p in bench_dir.iterdir() if (p / "tei.xml").exists()):
        urls = canvases(json.loads((vdir / "manifest.json").read_text(encoding="utf-8")))
        shown = pick_pages(len(urls), pages)
        images = []
        for i in shown:
            path = vdir / f"page-{i:04d}.jpg"
            if not path.exists():
                path.write_bytes(get(urls[i]))
            images.append(path)
        (vdir / "runs").mkdir(exist_ok=True)
        for r in range(runs):
            out = vdir / "runs" / f"{r:02d}.json"
            if out.exists():
                continue
            result = describe(f"{vdir.name}-r{r}", str(images[0]), len(urls))
            out.write_text(json.dumps({"source_json": result.get("source_json") or {},
                                       "pages_shown": [p.name for p in images]},
                                      ensure_ascii=False, indent=2), encoding="utf-8")


def _http_get(url: str) -> bytes:
    import httpx
    r = httpx.get(url, timeout=60, follow_redirects=True)
    r.raise_for_status()
    return r.content


def _agent_b_image_only(doc_id: str, image: str, page_count: int) -> dict:
    from agents import source_description as agent_b
    return agent_b._describe_from_image(doc_id, image, None, page_count) or {}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="TEI + IIIF manifest per volume (network)")
    f.add_argument("dir", type=Path)
    f.add_argument("ids", nargs="+", help="e-codices ids, e.g. saa-0428 csg-1072")
    f.add_argument("--tei", action="append", default=[], metavar="ID=URL",
                   help="TEI URL for a volume whose manifest links none")
    r = sub.add_parser("run", help="Agent B on every fetched volume (VLM)")
    r.add_argument("dir", type=Path)
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--pages", choices=("first", "spread"), default="first")
    s = sub.add_parser("score", help="judge the runs against the TEI (no network)")
    s.add_argument("dir", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "fetch":
        tei = dict(item.split("=", 1) for item in args.tei)
        args.dir.mkdir(parents=True, exist_ok=True)
        fetch(args.ids, args.dir, get=_http_get, tei_urls=tei)
        return 0
    if args.cmd == "run":
        import config
        config.DESCRIPTIONS_DIR = args.dir / "_descriptions"      # not into production
        config.DESCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
        from agents.source_description import _sampling
        (args.dir / "meta.json").write_text(json.dumps(
            {"runs": args.runs, "pages": args.pages, "agent_b": _sampling()}), encoding="utf-8")
        run(args.dir, describe=_agent_b_image_only, runs=args.runs, pages=args.pages, get=_http_get)
        return 0
    _, report = score(args.dir)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
