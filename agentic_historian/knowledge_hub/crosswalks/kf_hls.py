"""kf_hls.py — Königsfelden persons → HLS articles, rebuilt and verifiable (#830).

    python -m knowledge_hub.crosswalks.kf_hls \\
        --people koenigsfelden-data/data/registers/people.xml \\
        --cache build/hls_cache --out knowledge_hub/crosswalks/kf_hls_id_map.json

The edition's person register (Staatsarchiv-Aargau/koenigsfelden-data,
``data/registers/people.xml``) carries no HLS idno for any of its 5 260 persons.
The crosswalk of October 2026 (``~/.openclaw/tmp/kf_hls_id_map.json``, 162
persons) was not at ``~/.openclaw.pre-migration/tmp/`` on tei after the move
to Hermes, and it recorded no method. This rebuilds it so that every row says
why it is there:

1. **Candidates** — every human on Wikidata with an HLS id (P902): those dated
   before 1720 and those with no date at all. Wikidata only proposes; a
   candidate is kept when the KF forename (by variant: Hans = Johann(es) …)
   and a KF surname token, or the regnal number (``Albrecht II.``), agree with
   one of its names, and its Wikidata dates do not contradict KF's.
2. **Verification against HLS itself** — the article's title (``Manesse,
   Rüdiger VII.``) and the life dates of its lead (``*``, ``✝︎``, ``Erstmals
   erwähnt``, ``erwähnt 1336–1360``). HLS has the last word; Wikidata's dates
   are not used for the verdict.
3. **Verdict per KF person:**

   * ``high`` — the whole forename sequence and a surname token agree with the
     HLS title (rulers are titled by forename — «Albrecht I.», «Sigismund» —
     and then the family must stand in the article's lead), **and** the
     death year or the birth year agrees (±1; ±2 where either side says «um»).
     Exactly one such article.
   * ``medium`` — the names agree as for ``high``; no year to compare, but KF's
     first attestation lies in the article's window (birth + 10 … death, or
     first … last mention ± 10; one-sided windows 60 years). Exactly one
     candidate survived at all, the name occurs once in the register (six
     «Heinrich Schmid» the editors kept apart are not one HLS article), and
     no other KF person claims the article.
   * nothing — every other case, among them two articles that both fit
     (``ambiguous``, listed with their ids) and every contradiction (KF dead
     before HLS's birth, attested before birth, death years apart).

   ``high`` is meant for ``owl:sameAs``; ``medium`` for an identifier with its
   confidence, never ``sameAs`` (#830).

Network: query.wikidata.org (two SPARQL queries) and hls-dhs-dss.ch (one
article per candidate, cached under ``--cache``, one request a second).
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

NS = "{http://www.tei-c.org/ns/1.0}"
XMLID = "{http://www.w3.org/XML/1998/namespace}id"
HLS_URL = "https://hls-dhs-dss.ch/de/articles/{}/"
SPARQL = "https://query.wikidata.org/sparql"
UA = "agentic-historian-crosswalk/1.0 (research; KF-HLS)"

#: Words that are never a surname token: particles, titles, offices.
PARTICLES = frozenset(["von", "vom", "de", "der", "die", "den", "des", "zu", "zum", "zur", "d", "la", "le", "du", "van", "und", "gen", "genannt", "dit", "dite", "of", "in", "an", "am", "auf", "aus", "graf", "grafin", "graefin", "herzog", "herzogin", "konig", "konigin", "koenig", "koenigin", "freiherr", "ritter", "markgraf", "landgraf", "pfalzgraf", "furst", "fuerst", "bischof", "abt", "abtissin", "aebtissin", "kaiser", "kaiserin", "erzherzog", "erzherzogin", "truchsessen", "truchsess", "herren", "herr", "frau", "frauen", "papst", "schultheiss", "junker", "edelknecht", "meister", "der", "jungere", "altere", "jüngere", "ältere"])

#: Forename variants → one key. A name not listed is its own key.
VARIANTS = """
johann johannes hans jean johan hanns henmann hemmann hensli ioannes jan jehan
heinrich henri heinz heini henricus enrico
konrad conrad kuno cuno chuonrat chunrat conradus kunrat cuonrat
rudolf ruodolf rudolph rodolphe rudolfus
ulrich ulric uolrich udalrich ueli uli
elisabeth elsbeth elisabetha isabella isabelle elsa elisabet elizabeth
katharina katherina catherine katharine catharina katrin
margaretha margarethe margarete margareta marguerite margarita
friedrich frederic fridrich fridericus friderich
albrecht albert albertus
leopold lupold luitpold liupold leupold
wilhelm guillaume wilhelmus
peter pierre petrus peterman petermann
jakob jacob jacques jacobus
niklaus nikolaus nicolas niclaus claus nicolaus
burkhard burkart burchard burkard
walter walther gautier
hugo hug hugues
diebold theobald thiebaud thibaut
ludwig louis ludovicus
anna anne
klara clara claire
beatrix beatrice
sophie sophia
georg george georges jorg joerg
bernhard bernard
egloff egolf
otto otton
hartmann hartman
werner wernher
berchtold berthold bertold
gottfried gotfrid
rudiger ruediger rudger
kaspar caspar gaspard
christoph christophe christophorus
dorothea dorothee
magdalena madeleine
verena verene
gilg aegidius egidius gilles
jost jodocus josse
""".strip().splitlines()
VKEY: dict[str, str] = {}
for _line in VARIANTS:
    _w = _line.split()
    for _x in _w:
        VKEY.setdefault(_x, _w[0])

_ORD = re.compile(r"(?<![A-Za-zÀ-ž])([IVX]{1,5})\.?(?![A-Za-zÀ-ž])")
_YEAR = re.compile(r"(\d{3,4})")


def fold(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


def tokens(s: str | None) -> list[str]:
    return [t for t in re.split(r"[^a-z]+", fold(s)) if t]


def ordinals(s: str | None) -> set[str]:
    return set(_ORD.findall(s or ""))


def fkeys(s: str | None) -> tuple[str, ...]:
    """The forename sequence as variant keys: «Hans Jakob» → ('johann', 'jakob')."""
    return tuple(VKEY.get(t, t) for t in tokens(s) if t not in PARTICLES)


def surname_tokens(s: str | None) -> set[str]:
    return {t for t in tokens(s) if len(t) >= 3 and t not in PARTICLES and t not in VKEY}


def first_year(s: str | None) -> int | None:
    m = _YEAR.search(s or "")
    return int(m.group(1)) if m else None


# ── KF ───────────────────────────────────────────────────────────────────────

@dataclass
class KFPerson:
    id: str
    forename: str
    surname: str
    gen: str
    main: str
    occupation: str
    birth: str
    death: str
    first: int | None

    @property
    def fk(self) -> tuple[str, ...]:
        return fkeys(self.forename or self.main.split(" von ")[0])

    @property
    def stoks(self) -> set[str]:
        rest = self.main
        for t in (self.forename or self.main.split(" von ")[0]).split():
            rest = rest.replace(t, " ", 1)
        return (surname_tokens(self.surname) | surname_tokens(rest)) - set(tokens(self.forename))

    @property
    def name_key(self) -> tuple:
        return self.fk, tuple(sorted(self.stoks))

    @property
    def ords(self) -> set[str]:
        return ordinals(self.gen + ".") if self.gen else set()


def load_kf(path: Path) -> list[KFPerson]:
    out = []
    for p in ET.parse(path).getroot().iter(f"{NS}person"):
        full = p.find(f"{NS}persName[@type='full']")
        fn = sn = gen = ""
        if full is not None:
            # some entries nest their names by language: <persName xml:lang="de">
            fn = next((x.text.strip() for x in full.iter(f"{NS}forename") if x.text), "")
            sn = " ".join(x.text.strip() for x in full.iter(f"{NS}surname") if x.text)
            gen = next((x.text.strip() for x in full.iter(f"{NS}genName") if x.text), "")
        ev = p.find(f"{NS}event")
        out.append(KFPerson(
            id=p.get(XMLID, ""), forename=fn, surname=sn, gen=gen,
            main=(p.findtext(f"{NS}persName[@type='main']") or "").strip(),
            occupation=(p.findtext(f"{NS}occupation") or "").strip(),
            birth=(p.findtext(f"{NS}birth") or "").strip(),
            death=(p.findtext(f"{NS}death") or "").strip(),
            first=first_year(ev.get("when-custom")) if ev is not None else None))
    return out


# ── Wikidata candidates ──────────────────────────────────────────────────────

Q_DATED = """
SELECT ?item ?hls ?label ?b ?d ?fl (GROUP_CONCAT(DISTINCT ?alias; separator="|") AS ?aliases) WHERE {
  ?item wdt:P902 ?hls ; wdt:P31 wd:Q5 .
  OPTIONAL { ?item wdt:P569 ?bd . BIND(YEAR(?bd) AS ?b) }
  OPTIONAL { ?item wdt:P570 ?dd . BIND(YEAR(?dd) AS ?d) }
  OPTIONAL { ?item wdt:P1317 ?fd . BIND(YEAR(?fd) AS ?fl) }
  FILTER( (BOUND(?d) && ?d < 1720) || (!BOUND(?d) && BOUND(?b) && ?b < 1660)
          || (!BOUND(?d) && !BOUND(?b) && BOUND(?fl) && ?fl < 1700) )
  OPTIONAL { ?item rdfs:label ?label FILTER(LANG(?label)="de") }
  OPTIONAL { ?item skos:altLabel ?alias FILTER(LANG(?alias) IN ("de","fr","la")) }
} GROUP BY ?item ?hls ?label ?b ?d ?fl
"""
Q_UNDATED = """
SELECT ?item ?hls ?label (GROUP_CONCAT(DISTINCT ?alias; separator="|") AS ?aliases) WHERE {
  ?item wdt:P902 ?hls ; wdt:P31 wd:Q5 .
  FILTER NOT EXISTS { ?item wdt:P569 [] } FILTER NOT EXISTS { ?item wdt:P570 [] }
  FILTER NOT EXISTS { ?item wdt:P1317 [] }
  OPTIONAL { ?item rdfs:label ?label FILTER(LANG(?label)="de") }
  OPTIONAL { ?item skos:altLabel ?alias FILTER(LANG(?alias) IN ("de","fr","la")) }
} GROUP BY ?item ?hls ?label
"""


@dataclass
class WDPerson:
    q: str
    hls: set = field(default_factory=set)
    names: set = field(default_factory=set)
    b: set = field(default_factory=set)
    d: set = field(default_factory=set)
    fl: set = field(default_factory=set)


def sparql_csv(query: str) -> str:
    req = urllib.request.Request(SPARQL, data=urllib.parse.urlencode({"query": query}).encode(),
                                 headers={"Accept": "text/csv", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read().decode("utf-8")


def parse_wd(*csv_texts: str) -> list[WDPerson]:
    by: dict[str, WDPerson] = {}
    for text in csv_texts:
        for r in csv.DictReader(io.StringIO(text)):
            q = r["item"].rsplit("/", 1)[1]
            e = by.setdefault(q, WDPerson(q))
            e.hls.add(r["hls"].strip().zfill(6))
            for n in [r.get("label") or ""] + (r.get("aliases") or "").split("|"):
                if n.strip():
                    e.names.add(n.strip())
            for k in ("b", "d", "fl"):
                if r.get(k):
                    getattr(e, k).add(int(r[k]))
    return list(by.values())


def _near(a: int | None, ys: Iterable[int], tol: int) -> bool:
    return a is not None and any(abs(a - y) <= tol for y in ys)


def name_fits(p: KFPerson, name: str) -> bool:
    """Wikidata's names are loose: the first forename and a surname token or
    the regnal number. The strict test is against the HLS title."""
    fk = fkeys(name)
    if not fk or not p.fk or fk[0] != p.fk[0]:
        return False
    return bool(p.stoks & set(tokens(name))) or bool(p.ords and p.ords & ordinals(name))


def wd_contradicts(p: KFPerson, e: WDPerson) -> bool:
    dy, by = first_year(p.death), first_year(p.birth)
    loose = lambda s: bool(re.match(r"(vor|nach|um|ante|post|zwischen)\b", s, re.IGNORECASE))
    if dy and e.d and not loose(p.death) and not _near(dy, e.d, 3):
        return True
    if by and e.b and not _near(by, e.b, 5):
        return True
    return bool(p.first and e.b and p.first < min(e.b) - 5)


def candidates(kf: list[KFPerson], wd: list[WDPerson]) -> dict[str, list[WDPerson]]:
    index: dict[str, list[WDPerson]] = defaultdict(list)
    for e in wd:
        for k in {fkeys(n)[0] for n in e.names if fkeys(n)}:
            index[k].append(e)
    out = {}
    for p in kf:
        if not p.fk:
            continue
        cs = [e for e in index.get(p.fk[0], ())
              if any(name_fits(p, n) for n in e.names) and not wd_contradicts(p, e)]
        if cs:
            out[p.id] = cs
    return out


# ── HLS ──────────────────────────────────────────────────────────────────────

@dataclass
class HLSArticle:
    id: str
    title: str
    lead: str
    birth: int | None = None
    death: int | None = None
    first: int | None = None
    last: int | None = None
    birth_approx: bool = False
    death_approx: bool = False

    @property
    def surname_part(self) -> str:
        return self.title.split(",", 1)[0] if "," in self.title else self.title

    @property
    def forename_part(self) -> str:
        return self.title.split(",", 1)[1] if "," in self.title else self.title


#: HLS's lead: ``∗︎`` birth (U+2217, or ``*``), ``✝︎`` death, ``❒︎`` burial,
#: ``∞︎`` marriage. The variation selector U+FE0E follows each sign.
_MARK = re.compile(r"([∗*✝❒∞])︎?")
_YR = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")
_APPROX = re.compile(r"\b(um|vor|nach|zwischen|vermutlich|wahrscheinlich|spätestens|frühestens|"
                     r"ca|Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|"
                     r"November|Dezember)\b|\?|/", re.IGNORECASE)
_MENTION = re.compile(r"erw(ähnt|\.)|belegt|bezeugt|erstmals|letztmals|nachweisbar|urkundlich", re.IGNORECASE)
_SENTENCE_END = re.compile(r"\s?\.\s+(?=[A-ZÄÖÜ])")


def _segments(head: str) -> tuple[dict[str, str], str]:
    """Split the head at the signs: {sign: text after it}, and the text before
    the first sign joined with what lies between signs (the mentions)."""
    parts = _MARK.split(head)
    rest, segs = parts[0], {}
    for sign, text in zip(parts[1::2], parts[2::2]):
        if sign in "❒∞":            # burial, marriage: the life dates are behind us
            break
        if sign in "∗*✝" and sign not in segs:
            m = _YR.search(text[:60])
            if m:
                segs[sign] = text[:m.end()]
                rest += " " + text[m.end():]
                continue
        rest += " " + text
    return segs, rest


def parse_hls(hid: str, page: str) -> HLSArticle | None:
    t = re.search(r"<title>(.*?)</title>", page, re.DOTALL)
    d = re.search(r'<meta name="description" content="(.*?)"', page, re.DOTALL)
    if not t or not d:
        return None
    lead = html.unescape(d.group(1))
    a = HLSArticle(hid, html.unescape(t.group(1)).strip(), lead)
    m = _SENTENCE_END.search(lead)
    head = lead[:m.start()] if m and m.start() > 8 else lead[:200]
    head = re.split(r"\b(?:Sohn|Tochter|Aus)\b", head, maxsplit=1)[0]
    segs, rest = _segments(head)
    for sign, attr in (("∗", "birth"), ("*", "birth"), ("✝", "death")):
        if sign in segs and getattr(a, attr) is None:
            seg = segs[sign]
            setattr(a, attr, int(_YR.findall(seg)[-1]))
            setattr(a, attr + "_approx", bool(_APPROX.search(seg)))
    if _MENTION.search(rest):
        rest = re.sub(r"\d{1,2}\.\d{1,2}\.(?=\d{3,4})", "", rest)    # 17.10.1497 → 1497
        years = [int(y) for y in _YR.findall(rest)]
        if years:
            a.first = min(years)
            single_open = re.search(r"\b(ab|erstmals)\b", rest, re.IGNORECASE) and len(years) == 1
            a.last = None if single_open else max(years)
    return a


def fetch_hls(hid: str, cache: Path, *, get: Callable[[str], str] | None = None,
              delay: float = 1.0) -> HLSArticle | None:
    f = cache / f"{hid}.html"
    if not f.exists():
        if get is None:
            # curl, not urllib: hls-dhs-dss.ch answers urllib with 403 and curl
            # with the page (17.11.2026); one request a second, honest agent.
            def get(url: str) -> str:
                return subprocess.run(["curl", "-sSfL", "--max-time", "60", "-A", UA, url],
                                      check=True, capture_output=True, text=True).stdout
            time.sleep(delay)
        cache.mkdir(parents=True, exist_ok=True)
        f.write_text(get(HLS_URL.format(hid)), encoding="utf-8")
    return parse_hls(hid, f.read_text(encoding="utf-8"))


# ── the verdict ──────────────────────────────────────────────────────────────

def names_agree(p: KFPerson, a: HLSArticle) -> str | None:
    """How the names agree, or None. The forename sequence must be equal."""
    hf = tuple(k for k in fkeys(re.sub(_ORD, " ", a.forename_part)))
    # HLS titles of rulers carry no comma («Albrecht II.»): compare the forenames only
    if (not hf or hf != p.fk) and not ("," not in a.title and hf[:len(p.fk)] == p.fk):
        return None
    if "," not in a.title:
        # «Albrecht I.», «Sigismund»: HLS names rulers by forename; the family
        # has to stand in the article's lead
        if not p.stoks & set(tokens(a.lead[:400])):
            return None
        return "regnal number" if p.ords and p.ords & ordinals(a.title) else "family in HLS lead"
    if p.stoks & set(tokens(a.surname_part)):
        return "surname"
    return None


def _bare_forename_with_both_years(p: KFPerson, a: HLSArticle) -> bool:
    hf = fkeys(re.sub(_ORD, " ", a.forename_part))
    exact = lambda s: bool(s) and not re.match(r"(um|vor|nach|ante|post|zwischen)\b", s, re.IGNORECASE) \
        and "/" not in s and "?" not in s
    return (hf[:len(p.fk)] == p.fk and bool(p.fk) and exact(p.birth) and exact(p.death)
            and a.birth == first_year(p.birth) and a.death == first_year(p.death)
            and not a.birth_approx and not a.death_approx)


def verdict(p: KFPerson, a: HLSArticle) -> tuple[str, list[str]]:
    """('high'|'window'|'name'|'reject', basis)."""
    how = names_agree(p, a)
    if how is None and "," not in a.title and _bare_forename_with_both_years(p, a):
        # «Sigismund»: the lead names his father, not the family; forename and
        # both life years, exact on both sides, are the evidence instead
        return "high", ["forename", "birth and death year"]
    if how is None:
        return "reject", ["names differ"]
    basis = ["forename", how]
    kd, kb = first_year(p.death), first_year(p.birth)
    k_loose_d = bool(re.match(r"(vor|nach|um|ante|post|zwischen)\b", p.death, re.IGNORECASE))
    k_before = bool(re.match(r"(vor|ante)\b", p.death, re.IGNORECASE))
    k_um_b = bool(re.match(r"(um|vor|nach)\b", p.birth, re.IGNORECASE))
    # contradictions first
    if kd and a.death and not k_loose_d and abs(kd - a.death) > (2 if a.death_approx else 1):
        return "reject", basis + [f"death {kd} ≠ HLS {a.death}"]
    if kd and a.death and k_before and a.death > kd:
        return "reject", basis + [f"HLS death {a.death} after KF «{p.death}»"]
    if kb and a.birth and abs(kb - a.birth) > (5 if (k_um_b or a.birth_approx) else 1):
        return "reject", basis + [f"birth {kb} ≠ HLS {a.birth}"]
    if p.first and a.birth and p.first < a.birth:
        return "reject", basis + [f"attested {p.first} before HLS birth {a.birth}"]
    if kd and a.first and kd < a.first:
        return "reject", basis + [f"KF death {kd} before HLS first mention {a.first}"]
    # agreement
    tol_d = 2 if (k_loose_d or a.death_approx) else 1
    if kd and a.death and abs(kd - a.death) <= tol_d and not (k_before and a.death > kd):
        return "high", basis + ["death year"]
    if kb and a.birth and abs(kb - a.birth) <= (2 if (k_um_b or a.birth_approx) else 1):
        return "high", basis + ["birth year"]
    year = p.first or kd
    if year:
        lo = (a.birth + 10) if a.birth else ((a.first - 10) if a.first else None)
        hi = a.death if a.death else ((a.last + 10) if a.last else None)
        if lo is None and hi is not None:
            lo = hi - 60
        if hi is None and lo is not None:
            hi = lo + 60
        if lo is not None and lo <= year <= hi:
            return "window", basis + [f"KF {'attested' if p.first else 'death'} {year} in HLS {lo}–{hi}"]
        if lo is not None:
            return "reject", basis + [f"KF {year} outside HLS {lo}–{hi}"]
    return "name", basis + ["no date to compare"]


def build(kf: list[KFPerson], wd: list[WDPerson], article: Callable[[str], HLSArticle | None],
          *, log=print) -> dict:
    cands = candidates(kf, wd)
    log(f"{len(cands)} KF persons with Wikidata candidates, "
        f"{len({h for cs in cands.values() for e in cs for h in e.hls})} HLS articles to read")
    by_id = {p.id: p for p in kf}
    name_count = Counter(p.name_key for p in kf)
    persons, ambiguous, rejected = {}, {}, Counter()
    for pid, cs in sorted(cands.items()):
        p = by_id[pid]
        results = []
        for e in cs:
            for hid in sorted(e.hls):
                a = article(hid)
                if a is None:
                    rejected["article unreadable"] += 1
                    continue
                v, basis = verdict(p, a)
                if v == "reject":
                    rejected[basis[-1].split(" ")[0] if basis else "?"] += 1
                    continue
                results.append((v, basis, a, e))
        high = [r for r in results if r[0] == "high"]
        if len(high) == 1:
            pick, conf = high[0], "high"
        elif len(high) > 1:
            ambiguous[pid] = sorted({r[2].id for r in high})
            continue
        elif len(results) == 1 and results[0][0] == "window":
            if name_count[p.name_key] > 1:
                # the editors kept several persons of this name apart; a window
                # cannot tell which one HLS means
                ambiguous[pid] = [results[0][2].id]
                rejected["name not unique in register"] += 1
                continue
            pick, conf = results[0], "medium"
        else:
            if len(results) > 1:
                ambiguous[pid] = sorted({r[2].id for r in results})
            continue
        v, basis, a, e = pick
        persons[pid] = {
            "hls_id": a.id, "confidence": conf, "basis": basis,
            "kf_name": p.main, "kf_life": "–".join(x for x in (p.birth, p.death) if x) or None,
            "kf_first_attested": p.first,
            "hls_title": a.title,
            "hls_life": {k: v for k, v in (("birth", a.birth), ("death", a.death),
                                            ("first", a.first), ("last", a.last)) if v},
            "wikidata": e.q,
        }
    # one HLS article named by several KF persons: the register's own duplicates
    # (Hans/Henmann Waldmann) or a wrong pick — say so, keep both
    shared = defaultdict(list)
    for pid, r in persons.items():
        shared[r["hls_id"]].append(pid)
    for hid, pids in shared.items():
        if len(pids) > 1 and any(persons[x]["confidence"] == "medium" for x in pids):
            # a medium claim on an article another KF person also claims: drop it
            for pid in [x for x in pids if persons[x]["confidence"] == "medium"]:
                ambiguous[pid] = [hid]
                del persons[pid]
            pids = [x for x in pids if x in persons]
        if len(pids) > 1:
            for pid in pids:
                persons[pid]["same_hls_as"] = [x for x in pids if x != pid]
    return {"persons": persons, "ambiguous": ambiguous, "rejected": dict(rejected)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--people", type=Path, required=True, help="koenigsfelden-data registers/people.xml")
    ap.add_argument("--people-commit", default=None, help="the koenigsfelden-data commit, for the record")
    ap.add_argument("--wd-csv", type=Path, nargs="*", default=None,
                    help="saved SPARQL answers (CSV) instead of asking Wikidata")
    ap.add_argument("--cache", type=Path, default=Path("build/hls_cache"))
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    kf = load_kf(args.people)
    if args.wd_csv:
        wd = parse_wd(*(f.read_text(encoding="utf-8") for f in args.wd_csv))
    else:
        wd = parse_wd(sparql_csv(Q_DATED), sparql_csv(Q_UNDATED))
    res = build(kf, wd, lambda hid: fetch_hls(hid, args.cache), log=lambda m: print(m, file=sys.stderr))
    conf = Counter(r["confidence"] for r in res["persons"].values())
    doc = {
        "_meta": {
            "what": "Königsfelden persons (koenigsfelden-data people.xml) → HLS article ids",
            "built": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "people_xml": {"repo": "Staatsarchiv-Aargau/koenigsfelden-data", "commit": args.people_commit,
                           "persons": len(kf)},
            "candidates": "Wikidata P902, humans dated before 1720 or undated",
            "wikidata_persons": len(wd),
            "verified_against": "hls-dhs-dss.ch article title and lead dates",
            "method": "knowledge_hub/crosswalks/kf_hls.py (module docstring)",
            "counts": {"high": conf["high"], "medium": conf["medium"],
                       "ambiguous": len(res["ambiguous"])},
            "use": "high → owl:sameAs HLS; medium → identifier with confidence, never sameAs (#830)",
        },
        **res,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    print(json.dumps(doc["_meta"]["counts"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
