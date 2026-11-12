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
  TEI — R times, ``--pages first`` (page 1 alone, what production sent before #771)
  or ``--pages spread`` (k pages at even intervals, what it sends since). It uses Agent B's
  image-only path, the one production takes when the transcription is unusable;
  Schrift, Datierung, Schreiber and Beschreibstoff are read off the page in both.
* ``score``: per element hit / miss / not in the catalogue, and the stability of
  each element over the R runs. It reads only files, so the runs of an earlier
  day are scored again under today's rules (#842's century spans) for nothing.
* ``compare``: two scored directories side by side — the same volumes with
  ``--pages spread`` and with ``--pages typical`` (#774) — with a per-volume
  balance of which choice is steadier and hits more often.

Prompt variants and our own volumes (#844)
------------------------------------------
``run --prompt material-first`` describes with the image prompt's variant
(``source_description.IMAGE_PROMPT_VARIANTS``) into ``runs/<variant>-NN.json``
beside the default's ``runs/NN.json``; every run records its ``prompt``, and
``score`` reports each variant on its own and side by side. A run written before
the field existed is the default prompt, which was the only one.

A volume directory with ``reference.json`` and ``pages/`` instead of ``tei.xml``
and a manifest is one of our own: the reference is written by a historian, the
pages are local images. ``run`` and ``score`` treat it like an e-codices volume,
so a variant is measured on our ground truth with the same commands.

``run --describe external --model <id>`` sends the same prompt and pages to a
second reader over an OpenAI-compatible API (``eval/ecodices_external.py``):
only the model changes, so the dating floor can be laid at the model's door or
the prompt's.

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
#: The image prompt a run was made with when it does not say: the only one there
#: was before #844 (``source_description.IMAGE_PROMPT_VARIANTS``).
DEFAULT_PROMPT = "default"

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
    if pub is None:
        # e-codices gives no publicationStmt/date; its editionStmt dates the
        # conversion to TEI (saa-0428: 2008-03-22) — online at least since then.
        pub = root.find(f".//{TEI}editionStmt//{TEI}date")
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


def parse_reference(data: dict, vol_id: str) -> Reference:
    """A reference written by hand for one of our own volumes (#844).

    ``{"date_from": 1528, "date_to": 1528, "material": "papier",
    "script": "Kanzleikursive", "hands": 2}``; anything left out is not judged.
    ``material`` takes the TEI's ``perg``/``chart``/``mixed`` or the German words.
    """
    material = str(data.get("material") or "").strip().lower() or None
    if material:
        material = MATERIALS.get(material) or material_of(material) or material
    script = str(data.get("script") or "")
    return Reference(id=vol_id, published=data.get("published"),
                     script_text=script, script_families=families(script),
                     hands=int(data["hands"]) if data.get("hands") is not None else None,
                     date_from=_year(str(data.get("date_from") or "")),
                     date_to=_year(str(data.get("date_to") or data.get("date_from") or "")),
                     material=material)


def reference_of(vdir: Path) -> Reference:
    """The catalogue's TEI for an e-codices volume, ``reference.json`` for one of ours."""
    if (vdir / "tei.xml").exists():
        return parse_tei((vdir / "tei.xml").read_bytes(), vdir.name)
    return parse_reference(json.loads((vdir / "reference.json").read_text(encoding="utf-8")),
                           vdir.name)


def volumes(bench_dir: Path) -> list[Path]:
    """The volume directories of a bench: e-codices (``tei.xml``) or ours (``reference.json``)."""
    return sorted(p for p in bench_dir.iterdir()
                  if (p / "tei.xml").exists() or (p / "reference.json").exists())


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def local_pages(vdir: Path) -> list[Path]:
    """The page images of one of our own volumes, ``<vol>/pages/``, in name order."""
    return sorted(p for p in (vdir / "pages").iterdir()
                  if p.suffix.lower() in IMAGE_SUFFIXES) if (vdir / "pages").is_dir() else []


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
    "Mitte 15. Jh.", "Ende 15. Jh.", "14./15. Jh.", "12.–16. Jh."
    """
    low = (text or "").lower()
    years = [int(y) for y in re.findall(r"(?<!\d)(1\d{3}|[5-9]\d{2})(?!\d)", low)]
    if years:
        lo, hi = min(years), max(years)
        if len(years) == 1 and re.search(r"\b(um|ca\.?|circa|vers|circa)\b", low):
            return lo - 10, hi + 10
        return lo, hi
    cents: list[int] = []
    # a span written out: "16. oder 17. Jahrhundert", "13. Jh. - 14. Jh." (third
    # e-codices run, 7.11.2026: read as the second century alone)
    m = re.search(r"(\d{1,2})\s*\.?\s*(?:(?:jh|jahrh)\w*\.?\s*)?(?:[/–-]|oder|bis|und)\s*"
                  r"(\d{1,2})\s*\.?\s*(?:jh|jahrh)", low)
    if not m:
        m = re.search(r"(\d{1,2})\s*\.?\s*(?:[/–-]\s*(\d{1,2})\s*\.?\s*)?(?:jh|jahrh)", low)
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


#: The key under which the stability of the script's Top-2 set is reported (#775).
TOP2 = "Schrift_top2"


def top2_set(source_json: dict) -> Optional[str]:
    """The script families of the first two candidates (#775), as one answer.

    ``kandidaten[:2]`` by family, order-free ("bastarda+kursive"), so that two
    runs that swap first and second place still agree. None when the run gives
    no candidate that names a family — like a missing ``wert``, that lowers the
    stability rather than being left out of it.
    """
    el = source_json.get("Schrift") if isinstance(source_json, dict) else None
    cands = el.get("kandidaten") if isinstance(el, dict) else None
    if not isinstance(cands, list):
        return None
    fams = set()
    for cand in cands[:2]:
        text = cand.get("schrift") if isinstance(cand, dict) else cand
        found = families(str(text or ""))
        if found:
            fams.add(found[0])
    return "+".join(sorted(fams)) if fams else None


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
    reward: dict = field(default_factory=dict)        # term -> mean reward, + brier (#782)
    date_to: int | None = None                        # the catalogue's latest year (#844)
    #: "Datierung verdict/Beschreibstoff verdict" -> runs (#844): does the
    #: material miss where the dating misses?
    date_and_material: dict = field(default_factory=dict)
    script_modes: dict = field(default_factory=dict)  # "wert"/"top2" -> most common answer (#775)


def _stability(answers: list) -> Optional[float]:
    """Share of the runs giving the most common answer; None when none answered."""
    given = [a for a in answers if a is not None]
    return round(Counter(given).most_common(1)[0][1] / len(answers), 3) if given else None


def _mode(answers: list) -> Optional[str]:
    given = [a for a in answers if a is not None]
    return Counter(given).most_common(1)[0][0] if given else None


def score_volume(ref: Reference, runs: list[dict]) -> VolumeScore:
    vs = VolumeScore(id=ref.id, runs=len(runs), published=ref.published, date_to=ref.date_to)
    judged = [judge(ref, sj) for sj in runs]
    vs.date_and_material = dict(Counter(f"{j['Datierung']}/{j['Beschreibstoff']}"
                                        for j in judged))
    for element in ELEMENTS:
        verdicts = Counter(j[element] for j in judged)
        vs.judgements[element] = dict(verdicts)
        vs.stability[element] = _stability([_answer(element, sj) for sj in runs])
    vs.stability[TOP2] = _stability([top2_set(sj) for sj in runs])
    vs.script_modes = {"wert": _mode([_answer("Schrift", sj) for sj in runs]),
                       "top2": _mode([top2_set(sj) for sj in runs])}
    from eval import ecodices_reward              # lazy: it imports this module
    vs.reward = ecodices_reward.summarise_runs(ref, runs)
    return vs


def summarise_reward(scores: list[VolumeScore]) -> dict:
    """Mean of each volume's mean reward per term, and the mean Brier score (#782)."""
    out = {}
    with_reward = [vs.reward for vs in scores if vs.reward]
    if not with_reward:
        return out
    for term in (*ELEMENTS, "Schrift_brier", "format", "total"):
        vals = [r[term] for r in with_reward if term in r]
        out[term] = round(sum(vals) / len(vals), 4) if vals else None
    briers = [r["brier"] for r in with_reward if r.get("brier") is not None]
    out["brier"] = round(sum(briers) / len(briers), 4) if briers else None
    return out


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
    stab = [vs.stability[TOP2] for vs in scores if vs.stability.get(TOP2) is not None]
    out[TOP2] = {"mean_stability": round(sum(stab) / len(stab), 3) if stab else None,
                 "volumes": len(stab)}
    return out


#: A volume the catalogue dates no later than this is "early" (#844): the third
#: run dated all 11 of them to the 13th-15th century.
EARLY = 1100


def summarise_period_bias(scores: list[VolumeScore]) -> dict:
    """The two findings of #844 as numbers: dating of early volumes, and whether
    the material misses where the dating misses."""
    early, pairs, n_early = Counter(), Counter(), 0
    for vs in scores:
        pairs.update(vs.date_and_material)
        if vs.date_to is not None and vs.date_to <= EARLY:
            n_early += 1
            early.update(vs.judgements.get("Datierung", {}))
    judged = early[HIT] + early[MISS] + early[NO_ANSWER]
    material_miss = sum(n for k, n in pairs.items() if k.endswith(f"/{MISS}"))
    return {"early_volumes": n_early, "early_dating_hit": early[HIT],
            "early_dating_judged": judged,
            "early_dating_hit_rate": round(early[HIT] / judged, 3) if judged else None,
            "material_miss": material_miss,
            "material_miss_with_dating_miss": pairs.get(f"{MISS}/{MISS}", 0)}


def _period_lines(scores: list[VolumeScore]) -> list[str]:
    p = summarise_period_bias(scores)
    rate = "–" if p["early_dating_hit_rate"] is None else f"{p['early_dating_hit_rate']:.0%}"
    return ["## Epochen-Bias (#844)", "",
            (f"- Datierung der Bände bis {EARLY} (Katalog): {p['early_dating_hit']} von "
             f"{p['early_dating_judged']} Läufen getroffen ({rate}), {p['early_volumes']} Bände."),
            (f"- Beschreibstoff falsch: {p['material_miss']} Läufe, davon "
             f"{p['material_miss_with_dating_miss']} mit falscher Datierung."), ""]


def _reward_lines(scores: list[VolumeScore]) -> list[str]:
    r = summarise_reward(scores)
    if not r:
        return []
    fmt = lambda v: "–" if v is None else f"{v:+.3f}"  # noqa: E731
    lines = ["## Belohnung (#782)", "",
             "| Term | mittlere Belohnung |", "|---|---:|"]
    lines += [f"| {t} | {fmt(r.get(t))} |" for t in (*ELEMENTS, "Schrift_brier", "format", "total")]
    brier = "–" if r.get("brier") is None else f"{r['brier']:.3f}"
    lines += ["", f"Brier-Score der Schrift-Zuversicht: **{brier}** (0 = perfekt kalibriert, "
              "1 = immer mit voller Sicherheit falsch). Treffer +1, Fehler −1, keine Antwort "
              "−0,5, nicht im Katalog 0; eine Datierung zählt voll bis 50 Jahre Breite.", ""]
    return lines


def _pct(v: Optional[float]) -> str:
    return "–" if v is None else f"{v:.0%}"


def _top2_lines(scores: list[VolumeScore], summary: dict) -> list[str]:
    """Schrift as a Top-2 set of candidates against the single label (#775)."""
    top2 = summary.get(TOP2) or {}
    if not top2.get("volumes"):
        return []
    lines = ["## Schrift: Einzeletikett gegen Top-2-Menge (#775)", "",
             f"Stabilität der Top-2-Menge (`kandidaten[:2]`, nach Familie, ohne Reihenfolge): "
             f"**{_pct(top2['mean_stability'])}** über {top2['volumes']} Bände; "
             f"Einzeletikett `wert`: **{_pct(summary['Schrift']['mean_stability'])}**. "
             "Ein Lauf ohne Kandidaten senkt die Top-2-Stabilität wie ein Lauf ohne `wert` "
             "die des Etiketts.", "",
             "| Band | Etikett (häufigste) | stabil | Top-2 (häufigste) | stabil |",
             "|---|---|---:|---|---:|"]
    for vs in scores:
        m = vs.script_modes or {}
        lines.append(f"| `{vs.id}` | {m.get('wert') or '–'} | "
                     f"{_pct(vs.stability.get('Schrift'))} | {m.get('top2') or '–'} | "
                     f"{_pct(vs.stability.get(TOP2))} |")
    return lines + [""]


def format_report(scores: list[VolumeScore], meta: dict, prompt: str = DEFAULT_PROMPT) -> str:
    summary = summarise(scores)
    title = "# Agent B gegen die Katalogbeschreibungen von e-codices (#779)"
    lines = [title + (f" — Prompt `{prompt}`" if prompt != DEFAULT_PROMPT else ""), "",
             f"- Bände: **{len(scores)}**, Läufe je Band: {meta.get('runs')}, "
             f"Seiten: `{meta.get('pages')}`",
             f"- Agent-B-Konfiguration: {meta.get('agent_b', '—')}",
             f"- Prompt des Bildpfads: `{prompt}`"
             + (f", Modell `{meta['model']}`" if meta.get("model") else ""), "",
             "| Element | Treffer | Fehler | keine Antwort | nicht im Katalog | Trefferquote | Stabilität |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for element in ELEMENTS:
        s = summary[element]
        rate = "–" if s["hit_rate"] is None else f"{s['hit_rate']:.0%}"
        stab = "–" if s["mean_stability"] is None else f"{s['mean_stability']:.0%}"
        lines.append(f"| {element} | {s['hit']} | {s['miss']} | {s['no_answer']} | "
                     f"{s['not_in_catalogue']} | {rate} | {stab} |")
    lines += ["",
              "Trefferquote über die Läufe, die der Katalog beurteilen kann; «keine Antwort» "
              "zählt als nicht getroffen. Stabilität: Anteil der Läufe mit der häufigsten "
              "Antwort, im Mittel über die Bände.",
              "",
              *_period_lines(scores),
              *_top2_lines(scores, summary),
              *_reward_lines(scores),
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


def run_prompt(record: dict) -> str:
    """The image prompt one run was made with; a run older than #844 used the default."""
    return str(record.get("prompt") or DEFAULT_PROMPT)


def score_by_prompt(bench_dir: Path) -> dict[str, list[VolumeScore]]:
    """Every volume's runs, judged per image-prompt variant (#844), the default first.

    Reads ``tei.xml`` or ``reference.json`` and ``runs/*.json`` only: no network,
    no model. Each variant's runs are a volume's runs of their own, so the
    stability of one is never diluted by the other's answers.
    """
    groups: dict[str, list[VolumeScore]] = {}
    for vol in volumes(bench_dir):
        records = [json.loads(r.read_text(encoding="utf-8"))
                   for r in sorted((vol / "runs").glob("*.json"))] if (vol / "runs").is_dir() else []
        if not records:
            continue
        by: dict[str, list[dict]] = {}
        for rec in records:
            by.setdefault(run_prompt(rec), []).append(rec.get("source_json") or {})
        ref = reference_of(vol)
        for prompt, runs in by.items():
            groups.setdefault(prompt, []).append(score_volume(ref, runs))
    order = sorted(groups, key=lambda v: (v != DEFAULT_PROMPT, v))
    return {v: groups[v] for v in order}


def meta_for(meta: dict, prompt: str) -> dict:
    """The run settings of one variant: its own entry in ``variants`` over the top level."""
    return {**meta, **((meta.get("variants") or {}).get(prompt) or {})}


def format_comparison(groups: dict[str, list[VolumeScore]]) -> str:
    """The variants side by side: hit rate (stability) per element, and #844's two numbers."""
    names = list(groups)

    def pct(v):
        return "–" if v is None else f"{v:.0%}"

    lines = ["# Agent B gegen e-codices: Prompt-Varianten im Vergleich (#844)", "",
             "| | " + " | ".join(f"`{n}`" for n in names) + " |",
             "|---|" + "---:|" * len(names),
             "| Bände | " + " | ".join(str(len(groups[n])) for n in names) + " |",
             "| Läufe | " + " | ".join(str(sum(vs.runs for vs in groups[n])) for n in names) + " |"]
    summaries = {n: summarise(groups[n]) for n in names}
    for element in ELEMENTS:
        lines.append(f"| {element} | " + " | ".join(
            f"{pct(summaries[n][element]['hit_rate'])} "
            f"({pct(summaries[n][element]['mean_stability'])})" for n in names) + " |")
    bias = {n: summarise_period_bias(groups[n]) for n in names}
    lines.append(f"| Datierung bis {EARLY} | " + " | ".join(
        f"{bias[n]['early_dating_hit']}/{bias[n]['early_dating_judged']}" for n in names) + " |")
    lines.append("| Beschreibstoff falsch (davon mit falscher Datierung) | " + " | ".join(
        f"{bias[n]['material_miss']} ({bias[n]['material_miss_with_dating_miss']})"
        for n in names) + " |")
    lines += ["", ("Trefferquote (Stabilität) je Element; die Bände sind nur dann dieselben, "
                   "wenn die Zeile «Bände» gleich ist. Je Variante folgt der vollständige Bericht."),
              ""]
    return "\n".join(lines) + "\n"


def score(bench_dir: Path, out: Path | None = None) -> tuple[list[VolumeScore], str]:
    """Judge every run in ``bench_dir`` and write ``report.md`` and ``scores.json``.

    ``out`` is where they go (default: ``bench_dir``), so scoring an old run again
    under new rules can leave its first report in place. Several prompt variants
    give one report each, after a table of all of them side by side; the returned
    scores are the default prompt's, or the first variant's when it has none.
    """
    meta_path = bench_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    groups = score_by_prompt(bench_dir)
    reports = [format_report(scores, meta_for(meta, prompt), prompt)
               for prompt, scores in groups.items()]
    if len(groups) > 1:
        report = format_comparison(groups) + "\n" + "\n".join(reports)
    else:
        report = reports[0] if reports else format_report([], meta)
    first = next(iter(groups.values()), [])
    out = out or bench_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "scores.json").write_text(json.dumps(
        {"summary": summarise(first), "reward": summarise_reward(first),
         "period_bias": summarise_period_bias(first),
         "volumes": [asdict(s) for s in first],
         "by_prompt": {prompt: {"summary": summarise(scores),
                                "reward": summarise_reward(scores),
                                "period_bias": summarise_period_bias(scores),
                                "volumes": [asdict(s) for s in scores]}
                       for prompt, scores in groups.items()}},
        indent=2, ensure_ascii=False), encoding="utf-8")
    return first, report


# ── compare: one bench against another (#774) ───────────────────────────────

def _cell(vs: Optional[VolumeScore], element: str) -> str:
    """``hit 2, miss 1 · 67%`` for a volume's element, or ``–`` for a volume not run."""
    if vs is None:
        return "–"
    j = vs.judgements.get(element, {})
    verdicts = ", ".join(f"{k} {v}" for k, v in sorted(j.items())) or "–"
    return f"{verdicts} · {_pct(vs.stability.get(element))}"


def compare(dirs: list[Path], labels: Optional[list[str]] = None) -> str:
    """Two or more bench directories side by side: the same volumes, other pages (#774).

    Each directory is scored again (no network, no VLM; with several prompt
    variants, the default prompt's runs, as ``score`` returns them), the columns carry the
    ``pages`` of its ``meta.json`` unless ``labels`` names them, and the summary
    counts only the volumes that every directory has run, so that a volume
    missing from one run does not tilt the other.
    """
    cols = []
    for i, d in enumerate(dirs):
        meta_path = d / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        label = (labels[i] if labels and i < len(labels) and labels[i]
                 else str(meta.get("pages") or d.name))
        cols.append((label, meta, {vs.id: vs for vs in score(d)[0]}))
    common = sorted(set.intersection(*(set(by_id) for _, _, by_id in cols)))
    sums = [summarise([by_id[v] for v in common]) for _, _, by_id in cols]
    rewards = [summarise_reward([by_id[v] for v in common]) for _, _, by_id in cols]
    lines = ["# Agent B: Seitenwahl im Vergleich (#774)", "",
             "- Läufe: " + " · ".join(f"`{lab}` ({d}, {meta.get('runs', '?')} Läufe je Band, "
                                     f"{len(by_id)} Bände)"
                                     for d, (lab, meta, by_id) in zip(dirs, cols)),
             f"- Bände in allen Läufen: **{len(common)}**; nur sie zählen unten.", "",
             "| Element | " + " | ".join(f"Trefferquote `{lab}` | Stabilität `{lab}`"
                                         for lab, _, _ in cols) + " |",
             "|---|" + "---:|---:|" * len(cols)]
    for element in ELEMENTS:
        cells = [f"{_pct(sm[element]['hit_rate'])} | {_pct(sm[element]['mean_stability'])}"
                 for sm in sums]
        lines.append(f"| {element} | " + " | ".join(cells) + " |")
    lines.append("| Schrift, Top-2-Menge (#775) | " + " | ".join(
        f"– | {_pct(sm[TOP2]['mean_stability'])}" for sm in sums) + " |")
    lines.append("| Belohnung `total` (#782) | " + " | ".join(
        ("– | –" if r.get("total") is None else f"{r['total']:+.3f} | –") for r in rewards) + " |")
    lines += ["", "Trefferquote und Stabilität wie im Score-Report je Lauf, über die "
              "gemeinsamen Bände.", ""]
    if len(cols) >= 2 and common:
        a, b = cols[0], cols[1]
        lines += [f"## Bilanz `{b[0]}` gegen `{a[0]}`, je Band", "",
                  "| Element | stabiler | gleich | weniger stabil | mehr Treffer | "
                  "weniger Treffer |", "|---|---:|---:|---:|---:|---:|"]
        for element in ("Schrift", "Datierung", "Schreiber", "Beschreibstoff"):
            up = same = down = more = fewer = 0
            for v in common:
                sa, sb = a[2][v].stability.get(element), b[2][v].stability.get(element)
                if sa is not None or sb is not None:
                    sa, sb = sa or 0.0, sb or 0.0
                    up, same, down = up + (sb > sa), same + (sb == sa), down + (sb < sa)
                ha = a[2][v].judgements.get(element, {}).get(HIT, 0)
                hb = b[2][v].judgements.get(element, {}).get(HIT, 0)
                more, fewer = more + (hb > ha), fewer + (hb < ha)
            lines.append(f"| {element} | {up} | {same} | {down} | {more} | {fewer} |")
        lines += ["", "Stabilität je Band verglichen; «mehr Treffer»: mehr Läufe des Bandes "
                  "treffen den Katalog.", ""]
    lines += ["## Je Band: Schrift und Datierung", "",
              "| Band | " + " | ".join(f"Schrift `{lab}`" for lab, _, _ in cols) + " | "
              + " | ".join(f"Datierung `{lab}`" for lab, _, _ in cols) + " |",
              "|---|" + "---|" * (2 * len(cols))]
    every = sorted(set.union(*(set(by_id) for _, _, by_id in cols)))
    for v in every:
        cells = [_cell(by_id.get(v), "Schrift") for _, _, by_id in cols]
        cells += [_cell(by_id.get(v), "Datierung") for _, _, by_id in cols]
        lines.append(f"| `{v}` | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


# ── fetch and run: on tei ────────────────────────────────────────────────────

def _label(label) -> str:
    """A IIIF label as text: v2 string or ``{"@value"}`` list, v3 language map."""
    if isinstance(label, str):
        return label
    if isinstance(label, dict):
        return " ".join(str(v) for vals in label.values()
                        for v in (vals if isinstance(vals, list) else [vals]))
    if isinstance(label, list):
        return " ".join(_label(x.get("@value") if isinstance(x, dict) else x) for x in label)
    return ""


def canvas_entries(manifest: dict) -> list[tuple[str, str]]:
    """``(image URL at ≤1600 px, canvas label)`` of a IIIF 2 or 3 manifest, in page order."""
    out = []
    for seq in manifest.get("sequences") or []:                       # v2
        for canvas in seq.get("canvases") or []:
            for image in canvas.get("images") or []:
                service = (image.get("resource") or {}).get("service") or {}
                sid = service.get("@id") or service.get("id")
                if sid:
                    out.append((f"{sid.rstrip('/')}/full/!1600,1600/0/default.jpg",
                                _label(canvas.get("label"))))
    for canvas in manifest.get("items") or []:                         # v3
        for page in canvas.get("items") or []:
            for anno in page.get("items") or []:
                body = anno.get("body") or {}
                services = body.get("service") or []
                sid = (services[0].get("id") or services[0].get("@id")) if services else None
                if sid:
                    out.append((f"{sid.rstrip('/')}/full/!1600,1600/0/default.jpg",
                                _label(canvas.get("label"))))
    return out


def canvases(manifest: dict) -> list[str]:
    """Image URLs of a IIIF Presentation 2 or 3 manifest, in page order, at ≤1600 px."""
    return [url for url, _ in canvas_entries(manifest)]


#: Canvas labels that name the binding, not a written page. e-codices puts the
#: front cover first (29 of 30 volumes, 6.11.2026), so "page 1" was the cover.
NOT_TEXT = re.compile(
    r"\b(?:(?:front|back|upper|lower|inside\s+(?:front|back))\s+cover|cover|binding|spine|"
    r"(?:head|tail|fore|front|top|bottom)[\s-]*edge|edges?|paste-?\s?downs?|"
    r"colou?r\s*(?:chart|checker|card|profile)|\w*profil(?:e)?|qp\s+card|scale|ruler|"
    r"casing|open\s+view|^\s*(?:head|tail)\s*$|"
    r"(?:vorder|rück|rueck|hinter)deckel|einband|buchrücken|rücken|schnitt|spiegel|farbkarte|"
    r"reliure|plat\s+(?:supérieur|inférieur)|contre-?plat|dos|tranche)\b",
    re.IGNORECASE)


#: A foliated or paginated leaf of the text block: "1r", "12v", "f. 1r", "fol. 3",
#: "p. 3", "S. 5", "7". Flyleaves are lettered or in roman numerals ("A", "Ir").
FOLIO = re.compile(r"^\s*(?:(?:f|fol|p|pag|s|seite|page)\.?\s*)?0*[1-9]\d*\s*[rv]?\s*(?:[\s(].*)?$",
                   re.IGNORECASE)


def body_indices(manifest: dict) -> list[int]:
    """Indices of the text block: written pages that carry a folio or page number.

    The first run on the text pages (7.11.2026) still met flyleaves: rebound
    parchment codices with paper endleaves and a librarian's note in Kurrent —
    Agent B read "Papier", "Kurrent", "19. Jh." for Carolingian volumes. Falls
    back to ``text_indices`` when the labels number fewer than half of them.
    """
    entries = canvas_entries(manifest)
    text = text_indices(manifest)
    body = [i for i in text if FOLIO.match(entries[i][1] or "")]
    return body if len(body) >= max(1, len(text) // 2) else text


def text_indices(manifest: dict) -> list[int]:
    """Indices of the canvases that are written pages, not the binding.

    Falls back to every canvas when the labels leave none (or there are none).
    """
    entries = canvas_entries(manifest)
    idx = [i for i, (_, label) in enumerate(entries) if not NOT_TEXT.search(label or "")]
    return idx or list(range(len(entries)))


def tei_url(manifest: dict) -> Optional[str]:
    """The TEI description a manifest links in ``seeAlso``, if it does."""
    see = manifest.get("seeAlso") or []
    for item in see if isinstance(see, list) else [see]:
        url = item if isinstance(item, str) else (item.get("@id") or item.get("id") or "")
        if url.endswith(".xml") or "tei" in url.lower():
            return url
    return None


def pick_pages(n: int, how: str, k: Optional[int] = None) -> list[int]:
    """Indices of the pages to show: ``first`` (before #771) or ``spread`` (#771).

    ``spread`` is the production sample, ``page_sample.spread``, so the bench
    measures what Agent B is actually shown.
    """
    import config
    import page_sample
    return page_sample.spread(n, 1 if how == "first" else (k or config.AGENT_B_PAGES))


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


def run_name(r: int, prompt: str = DEFAULT_PROMPT) -> str:
    """``runs/NN.json`` for the default prompt (as before #844), ``<variant>-NN.json`` else."""
    return f"{r:02d}.json" if prompt == DEFAULT_PROMPT else f"{prompt}-{r:02d}.json"


def run(bench_dir: Path, *, describe: Callable[[str, list, int], dict], runs: int = 3,
        pages: str = "first", get: Callable[[str], bytes] | None = None, features=None,
        prompt: str = DEFAULT_PROMPT) -> None:
    """Agent B on each fetched volume, R times, from page images only.

    ``describe`` gets the list of pages shown: one for ``first``, k at even
    intervals for ``spread`` (#771), and for ``typical`` the most and the least
    typical page among them (#774), measured on up to
    ``page_sample.TYPICALITY_POOL`` pages; then it also gets ``page_roles``.
    ``features`` replaces ``page_features.image_features`` in the tests.

    ``prompt`` is the image prompt's variant (#844): another than the default is
    passed to ``describe`` as ``prompt_variant`` and its runs go to
    ``runs/<variant>-NN.json``, beside the default's. Each run records the
    variant ``describe`` says it used. One of our own volumes (``reference.json``,
    ``pages/``) is shown its local pages; ``get`` is only for e-codices.
    """
    import page_sample

    for vdir in volumes(bench_dir):
        if (vdir / "manifest.json").exists():
            manifest = json.loads((vdir / "manifest.json").read_text(encoding="utf-8"))
            urls = canvases(manifest)
            n_pages = len(urls)
            text = body_indices(manifest)          # not the binding, not the flyleaves

            def page(i: int, vdir=vdir, urls=urls) -> Path:
                path = vdir / f"page-{i:04d}.jpg"
                if not path.exists():
                    path.write_bytes(get(urls[i]))
                return path
        else:
            local = local_pages(vdir)
            if not local:
                print(f"{vdir.name}: no page images in pages/ — skipped", file=sys.stderr)
                continue
            n_pages, text = len(local), list(range(len(local)))

            def page(i: int, local=local) -> Path:
                return local[i]

        roles: dict[str, str] = {}
        if pages == "typical":
            import config

            if features is None:
                from page_features import image_features as features
            chosen = [(text[j], role) for j, role in page_sample.choose(
                len(text), config.AGENT_B_PAGES, "typical",
                features=lambda j, text=text, page=page: features(page(text[j])))]
            images = [page(i) for i, _ in chosen]
            roles = {page(i).name: role for i, role in chosen if role != page_sample.SAMPLE}
        else:
            images = [page(text[j]) for j in pick_pages(len(text), pages)]
        (vdir / "runs").mkdir(exist_ok=True)
        for r in range(runs):
            out = vdir / "runs" / run_name(r, prompt)
            if out.exists():
                continue
            extra = {"page_roles": roles} if roles else {}
            if prompt != DEFAULT_PROMPT:
                extra["prompt_variant"] = prompt
            tag = "" if prompt == DEFAULT_PROMPT else f"-{prompt}"
            result = describe(f"{vdir.name}{tag}-r{r}", [str(p) for p in images], n_pages,
                              **extra)
            seen = result.get("pages_seen") or [p.name for p in images]
            # what the reading cost and whether it was cut off, when the reader
            # says (a second reader on a metered API, #844)
            about = {k: result[k] for k in ("model", "cost", "truncated") if k in result}
            out.write_text(json.dumps({"source_json": result.get("source_json") or {},
                                       "pages_shown": [p.name for p in images],
                                       "pages_seen": seen, "page_roles": roles,
                                       "prompt": result.get("prompt_variant") or prompt,
                                       **about},
                                      ensure_ascii=False, indent=2), encoding="utf-8")


USER_AGENT = "agentic-historian-bench/1.0 (research; University of Bern, Digital Humanities)"
RETRY_STATUS = {429, 500, 502, 503, 504}


class PoliteGetter:
    """GET with a pause between requests and retries on 429/5xx.

    e-codices answered a burst of 300 manifest requests (6.11.2026) with 503
    and 400 after the first: a run of the bench is a guest on a library's
    server. ``delay`` seconds between any two requests; a retryable status
    waits ``Retry-After`` if given, else ``backoff`` (5, 20, 60 s), then gives
    up and raises — the caller names the volume and goes on.
    """

    def __init__(self, delay: float = 1.0, backoff=(5, 20, 60), sleep=None, client=None):
        import time
        self.delay, self.backoff = delay, tuple(backoff)
        self._sleep, self._now = sleep or time.sleep, time.monotonic
        self._client, self._last = client, None

    def _wait(self, seconds: float) -> None:
        if seconds > 0:
            self._sleep(seconds)

    def __call__(self, url: str) -> bytes:
        import httpx
        if self._client is None:
            self._client = httpx.Client(timeout=60, follow_redirects=True,
                                        headers={"User-Agent": USER_AGENT})
        for attempt in range(len(self.backoff) + 1):
            if self._last is not None:
                self._wait(self.delay - (self._now() - self._last))
            try:
                r = self._client.get(url)
            except httpx.TransportError:
                if attempt == len(self.backoff):
                    raise
                self._last = self._now()
                self._wait(self.backoff[attempt])
                continue
            self._last = self._now()
            if r.status_code in RETRY_STATUS and attempt < len(self.backoff):
                ra = r.headers.get("Retry-After", "")
                self._wait(min(float(ra), 300) if ra.isdigit() else self.backoff[attempt])
                continue
            r.raise_for_status()
            return r.content
        raise AssertionError("unreachable")


_GETTER: Optional[PoliteGetter] = None


def _http_get(url: str) -> bytes:
    global _GETTER
    if _GETTER is None:
        _GETTER = PoliteGetter()
    return _GETTER(url)


def _agent_b_image_only(doc_id: str, images, page_count: int, page_roles=None,
                        prompt_variant=None) -> dict:
    from agents import source_description as agent_b
    return agent_b._describe_from_image(doc_id, images, None, page_count, page_roles,
                                        prompt_variant=prompt_variant) or {}


#: ``source_description.IMAGE_PROMPT_VARIANTS``, repeated so that ``--help`` and
#: ``score`` import no agent; a test holds the two equal.
PROMPT_CHOICES = ("default", "material-first")


def write_meta(bench_dir: Path, prompt: str, settings: dict) -> dict:
    """``meta.json``: one entry per prompt variant under ``variants`` (#844).

    The top level stays what it was before #844 — the default prompt's settings,
    or the first variant's in a directory without them — so a report of the
    default does not take the pages or runs of a variant run afterwards.
    """
    path = bench_dir / "meta.json"
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if prompt == DEFAULT_PROMPT or not meta:
        meta.update(settings)
    meta.setdefault("variants", {})[prompt] = settings
    path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between two requests to e-codices (default 1)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidates", help="choose the volumes from the catalogue and fetch them "
                                          "(network; see eval/ecodices_candidates.py)")
    c.add_argument("dir", type=Path)
    c.add_argument("--n", type=int, default=30)
    c.add_argument("--max-inspect", type=int, default=300,
                   help="volumes whose manifest and TEI are read to choose from")
    c.add_argument("--min-pages", type=int, default=10)
    c.add_argument("--prefer", action="append", default=None, metavar="PREFIX",
                   help="id prefixes inspected and taken first (default: saa)")
    c.add_argument("--ids-file", type=Path, default=None,
                   help="choose among these ids instead of listing e-codices over OAI-PMH")
    c.add_argument("--oai-url", default=None)
    c.add_argument("--max-failures", type=int, default=10,
                   help="stop after this many volumes in a row could not be read "
                        "(the server is refusing; what was read stays cached)")
    f = sub.add_parser("fetch", help="TEI + IIIF manifest per volume (network)")
    f.add_argument("dir", type=Path)
    f.add_argument("ids", nargs="*", help="e-codices ids, e.g. saa-0428 csg-1072")
    f.add_argument("--ids-from", type=Path, default=None,
                   help="a file of ids, one per line (e.g. selected.txt from candidates)")
    f.add_argument("--tei", action="append", default=[], metavar="ID=URL",
                   help="TEI URL for a volume whose manifest links none")
    r = sub.add_parser("run", help="Agent B on every fetched volume (VLM)")
    r.add_argument("dir", type=Path)
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--pages", choices=("first", "spread", "typical"), default="first")
    r.add_argument("--describe", choices=("agent-b", "external"), default="agent-b",
                   help="agent-b: the gateway VLM, as production; external: the same prompt "
                        "and pages to an OpenAI-compatible API (GEMINI_BASE_URL/GEMINI_API_KEY, "
                        "#844) — run it into a directory of its own")
    r.add_argument("--model", default=None, help="model id for --describe external")
    r.add_argument("--reasoning", default=None,
                   help="reasoning effort for --describe external (default GEMINI_REASONING)")
    r.add_argument("--prompt", choices=PROMPT_CHOICES, default=None,
                   help="the image prompt's variant (#844); default: AGENT_B_IMAGE_PROMPT, "
                        "i.e. 'default' unless set. Runs of another variant than 'default' "
                        "go to runs/<variant>-NN.json beside the default's")
    s = sub.add_parser("score", help="judge the runs against the TEI (no network, no model)")
    s.add_argument("dir", type=Path)
    s.add_argument("--out", type=Path, default=None,
                   help="write report.md and scores.json here instead of into DIR "
                        "(to score an old run again and keep its first report)")
    cp = sub.add_parser("compare", help="two or more scored runs side by side, e.g. "
                                        "--pages spread against --pages typical (#774)")
    cp.add_argument("dirs", type=Path, nargs="+")
    cp.add_argument("--label", action="append", default=None, metavar="NAME",
                    help="column name per directory, in order (default: its meta.json pages)")
    cp.add_argument("--out", type=Path, default=None, help="write the comparison here as well")
    args = ap.parse_args(argv)
    global _GETTER
    _GETTER = PoliteGetter(delay=args.delay)

    if args.cmd == "candidates":
        from eval import ecodices_candidates as ec
        ids = None
        if args.ids_file:
            ids = [ln.strip() for ln in args.ids_file.read_text(encoding="utf-8").splitlines()
                   if ln.strip() and not ln.startswith("#")]
        chosen = ec.candidates(args.dir, get=_http_get, n=args.n, max_inspect=args.max_inspect,
                               min_pages=args.min_pages, ids=ids,
                               oai_url=args.oai_url or ec.OAI_URL,
                               prefer=tuple(args.prefer or ec.PREFER),
                               max_failures=args.max_failures)
        print("\n".join(chosen))
        return 0
    if args.cmd == "fetch":
        tei = dict(item.split("=", 1) for item in args.tei)
        ids = list(args.ids)
        if args.ids_from:
            ids += [ln.strip() for ln in args.ids_from.read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.startswith("#")]
        if not ids:
            ap.error("fetch needs ids or --ids-from")
        args.dir.mkdir(parents=True, exist_ok=True)
        fetch(ids, args.dir, get=_http_get, tei_urls=tei)
        return 0
    if args.cmd == "run":
        import config
        config.DESCRIPTIONS_DIR = args.dir / "_descriptions"      # not into production
        config.DESCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
        from agents.source_description import _sampling, image_prompt_variant
        prompt = image_prompt_variant(args.prompt)
        if args.describe == "external":
            if not args.model:
                ap.error("--describe external needs --model")
            from eval import ecodices_external
            describe = ecodices_external.describer(args.model, reasoning=args.reasoning)
            settings = {"runs": args.runs, "pages": args.pages, "prompt": prompt,
                        **describe.settings}
        else:
            describe = _agent_b_image_only
            settings = {"runs": args.runs, "pages": args.pages, "agent_b": _sampling(),
                        "prompt": prompt, "model": config.GPUSTACK_MODEL_VISION}
        write_meta(args.dir, prompt, settings)
        run(args.dir, describe=describe, runs=args.runs, pages=args.pages,
            get=_http_get, prompt=prompt)
        return 0
    if args.cmd == "compare":
        report = compare(args.dirs, labels=args.label)
        if args.out:
            args.out.write_text(report, encoding="utf-8")
        print(report)
        return 0
    _, report = score(args.dir, out=args.out)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
