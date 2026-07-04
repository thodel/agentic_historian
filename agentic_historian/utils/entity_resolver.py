"""
utils/entity_resolver.py — cross-source entity resolver/merger (KH-2, #88).

Takes the flat ``list[PersonResult]`` that the MCP federation returns
(utils/mcp_client.py) and clusters records that refer to the same real person,
producing merged ``ResolvedEntity`` objects with a qualitative confidence label
and full source attribution.

Resolution strategy (IMPLEMENTATION_PLAN.md → Entity Resolution Strategy):
  - shared authority id (GND / HLS / Wikidata)            → high  → merge
  - same normalised name + overlapping life dates (±25y)  → high  → merge
  - same normalised name, neither side has dates          → medium→ merge (flag)
  - surname match + (forename match | variant) + overlap  → medium→ merge (flag)
  - otherwise                                             → keep separate

Confidence labels are qualitative (high/medium/low), not calibrated
probabilities — consistent with the labels Agent C already uses.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from pydantic import BaseModel, Field

from utils.mcp_client import PersonResult

# Nobiliary / toponymic particles dropped when normalising names.
_PARTICLES = {"von", "van", "de", "di", "da", "der", "zu", "zer", "im", "am",
              "the", "of", "und"}

_RANK = {"high": 3, "medium": 2, "low": 1}


class ResolvedEntity(BaseModel):
    """One real person, merged from ≥1 source ``PersonResult``s."""
    name: str
    sources: list[str] = Field(default_factory=list)
    confidence: str = "high"          # high | medium | low  (single-member = high)
    forename: Optional[str] = None
    surname: Optional[str] = None
    life_dates: Optional[str] = None
    occupation: Optional[str] = None
    hls_id: Optional[int] = None
    gnd_id: Optional[str] = None
    wikidata_id: Optional[str] = None
    variants: list[str] = Field(default_factory=list)
    members: list[PersonResult] = Field(default_factory=list)
    needs_review: bool = False        # true for medium/low merges


# ── normalisation helpers ────────────────────────────────────────────────────

def _fold(s: str) -> str:
    """Lowercase + strip accents."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _norm_name(name: str) -> str:
    """Fold, drop particles, collapse whitespace/punctuation."""
    folded = _fold(name)
    tokens = [t for t in re.split(r"[^\wäöü]+", folded) if t and t not in _PARTICLES]
    return " ".join(tokens)


def _parse_dates(s: Optional[str]) -> Optional[tuple[int, int]]:
    """Parse '1300–1370' / 'fl. 1348' / 'um 1350' → (lo, hi); None if no year."""
    if not s:
        return None
    years = [int(y) for y in re.findall(r"\b(\d{3,4})\b", s)]
    if not years:
        return None
    return (min(years), max(years))


def _dates_overlap(a: Optional[tuple[int, int]], b: Optional[tuple[int, int]],
                   tol: int = 25) -> Optional[bool]:
    """True/False if both sides have dates; None if either is unknown."""
    if a is None or b is None:
        return None
    return a[0] - tol <= b[1] and b[0] - tol <= a[1]


# ── pairwise match ───────────────────────────────────────────────────────────

def _shared_authority_id(p: PersonResult, q: PersonResult) -> bool:
    for attr in ("gnd_id", "hls_id", "wikidata_id"):
        pv, qv = getattr(p, attr), getattr(q, attr)
        if pv and qv and pv == qv:
            return True
    return False


def _variant_set(p: PersonResult) -> set[str]:
    return {_norm_name(v) for v in ([p.name, *p.variants]) if v}


def match(p: PersonResult, q: PersonResult) -> Optional[str]:
    """Return the confidence label for merging p and q, or None to keep apart."""
    if _shared_authority_id(p, q):
        return "high"

    np_, nq = _norm_name(p.name), _norm_name(q.name)
    overlap = _dates_overlap(_parse_dates(p.life_dates), _parse_dates(q.life_dates))

    # Same normalised full name.
    if np_ and np_ == nq:
        if overlap is True:
            return "high"
        if overlap is None:          # name matches, no date info either side
            return "medium"
        return None                  # same name but incompatible dates → different people

    # Surname + (forename or variant) + overlapping dates.
    if p.surname and q.surname and _fold(p.surname) == _fold(q.surname):
        forename_ok = (
            (p.forename and q.forename and _fold(p.forename) == _fold(q.forename))
            or bool(_variant_set(p) & _variant_set(q))
        )
        if forename_ok and overlap is True:
            return "medium"
    return None


# ── clustering (union-find over pairwise matches) ────────────────────────────

def resolve(persons: list[PersonResult]) -> list[ResolvedEntity]:
    """Cluster PersonResults into ResolvedEntities."""
    n = len(persons)
    parent = list(range(n))
    edge_conf: dict[int, str] = {}   # root → strongest label seen in its cluster

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int, conf: str) -> None:
        ri, rj = find(i), find(j)
        best = conf
        if ri != rj:
            best = max((edge_conf.get(ri, ""), edge_conf.get(rj, ""), conf),
                       key=lambda c: _RANK.get(c, 0))
            parent[ri] = rj
        else:
            best = max((edge_conf.get(rj, ""), conf), key=lambda c: _RANK.get(c, 0))
        edge_conf[find(j)] = best

    for i in range(n):
        for j in range(i + 1, n):
            conf = match(persons[i], persons[j])
            if conf:
                union(i, j, conf)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    return [_merge([persons[i] for i in idxs], edge_conf.get(root))
            for root, idxs in clusters.items()]


def _merge(members: list[PersonResult], cluster_conf: Optional[str]) -> ResolvedEntity:
    conf = "high" if len(members) == 1 else (cluster_conf or "medium")

    def first(attr):
        for m in members:
            if getattr(m, attr):
                return getattr(m, attr)
        return None

    variants: list[str] = []
    for m in members:
        for v in [m.name, *m.variants]:
            if v and v not in variants:
                variants.append(v)
    # canonical name: the longest surface form (usually the most complete)
    name = max((m.name for m in members if m.name), key=len, default="")

    return ResolvedEntity(
        name=name,
        sources=sorted({m.source for m in members}),
        confidence=conf,
        forename=first("forename"),
        surname=first("surname"),
        life_dates=first("life_dates"),
        occupation=first("occupation"),
        hls_id=first("hls_id"),
        gnd_id=first("gnd_id"),
        wikidata_id=first("wikidata_id"),
        variants=variants,
        members=members,
        needs_review=(conf in ("medium", "low") and len(members) > 1),
    )
