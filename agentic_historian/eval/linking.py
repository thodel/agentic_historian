"""
eval/linking.py — system-aware linking agreement and miss diagnosis.

Ported from outremer (`evaluation/metrics.py`, `evaluation/diagnose.py`;
outremer #89), where it was learned the hard way: two published linking
baselines there (0.6479, then 0.6761) were measurement artefacts, not
measurements. The first could never count a hit because the fixture read an id
field the candidates did not carry; the second counted Wikidata adjudications
against an authority linker that can never propose a QID. Both would have
been caught by the two rules this module enforces:

1. **Every id names its system** (``gnd:…``, ``hls:…``, ``wikidata:…``,
   ``hub:…``), and a pair is judged only against the system that could have
   proposed it. A miss counted against the wrong system is fabricated.
2. **Agreement is reported next to the null linker** — what a pipeline that
   proposes nothing would score on the same gold. Reject-dominated gold makes
   silence look good (outremer, 2026-07-30: null 0.873 against a measured
   0.891 — one pair of 55 was the linker's whole demonstrated value), so
   ``lift_over_null`` is the number that carries the signal.

What the gold is here. Gate 3 (`entity_review.py`) lets a historian pick one
of the offered candidates or "kein Link" for an entity. That choice is an
**editorial decision among the candidates offered**, not independently
established truth — the same boundary `closest_reading` draws for text, and
the reason this module never scores a transcription. A confirmed link is an
accepted pair for each system id it carries; "kein Link" rejects the offered
candidates and nothing else. The pipeline's own proposal survives the review
in ``pipeline_link`` (`apply_entity_link` stashes it before overwriting), so
prediction and gold come out of the same entities file.

Why a miss gets a cause. "Linking is broken" is not actionable; in outremer the
taxonomy below turned it into "extraction dropped four chroniclers" (#42):

    not_extracted     — no entity with that mention in the document's output
    no_candidates     — extracted, but nothing proposed and no candidate kept
    ranked_below_top  — the adjudicated id was among the candidates, not chosen
    candidate_missing — candidates exist, the adjudicated id is not among them

The first is the extractor's problem; the other three belong to the linker
(recall, ranking, candidate generation).

Run from the repo root::

    python -m eval.linking                      # data/outputs/*_entities.json
    python -m eval.linking --entities-dir DIR --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

SYSTEMS: tuple[str, ...] = ("gnd", "hls", "wikidata", "hub")

#: entity field → system. ``hub_id`` is the hub's own key and only counts as a
#: proposal when no authority id accompanies it (a hub record that carries a
#: GND id is judged as that GND link).
_ID_FIELDS = (("gnd", "gnd"), ("hls", "hls"), ("wikidata", "wikidata"))

HUMAN_METHODS = frozenset({"human_confirmed", "human_none"})

CAUSES = ("not_extracted", "no_candidates", "ranked_below_top", "candidate_missing")


# ── names and ids ─────────────────────────────────────────────────────────────

def norm_mention(text: str) -> str:
    """The linker's own name normalisation, so eval and production agree on
    which spellings are one mention."""
    from utils.entity_resolver import _norm_name

    return _norm_name(text or "")


def system_of(pair_id: str) -> str:
    """The system an id belongs to; raises on an id that does not say."""
    system, sep, rest = str(pair_id).partition(":")
    if not sep or system not in SYSTEMS or not rest:
        raise ValueError(
            f"id {pair_id!r} does not name its system (expected one of "
            f"{', '.join(s + ':…' for s in SYSTEMS)}) — a pair without a system "
            "cannot be judged against anything"
        )
    return system


def split_pairs_by_system(pairs: Iterable[tuple[str, str]]) -> dict[str, list[tuple[str, str]]]:
    """Group (mention, id) pairs by the system that could have proposed them."""
    out: dict[str, list[tuple[str, str]]] = {s: [] for s in SYSTEMS}
    for mention, pair_id in pairs:
        out[system_of(pair_id)].append((mention, pair_id))
    return out


def ids_of(fields: dict) -> set[str]:
    """The system-qualified ids an entity (or a stashed proposal) carries."""
    ids: set[str] = set()
    for field, system in _ID_FIELDS:
        value = fields.get(field)
        if value not in (None, ""):
            ids.add(f"{system}:{value}")
    hub_id = fields.get("hub_id")
    if not ids and hub_id not in (None, ""):
        ids.add(f"hub:{hub_id}")
    return ids


def pipeline_ids(ent: dict) -> set[str]:
    """What the pipeline proposed for this entity, before any human touched it.

    ``pipeline_link`` is the stash Gate 3 leaves; an entity never reviewed
    still carries its proposal in place. A reviewed entity without a stash
    (output older than the stash) has no recoverable proposal, and claiming one
    from the human's choice would score the historian against herself.
    """
    stash = ent.get("pipeline_link")
    if isinstance(stash, dict):
        return ids_of(stash)
    if ent.get("link_method") in HUMAN_METHODS:
        return set()
    return ids_of(ent)


def candidate_ids(ent: dict) -> set[str]:
    """Every id the pipeline considered for this entity: its proposal plus the
    candidates it kept (``candidates`` from the linker, ``offered_candidates``
    from the review card)."""
    ids = set(pipeline_ids(ent))
    for key in ("candidates", "offered_candidates"):
        for c in ent.get(key) or []:
            if isinstance(c, str) and c:
                ids.add(c)
    return ids


# ── gold from Gate 3 ──────────────────────────────────────────────────────────

Pairs = list[tuple[str, str]]


def gold_from_entities(entities: Iterable[dict]) -> tuple[Pairs, Pairs]:
    """(accepted, rejected) pairs from the historian's Gate 3 decisions.

    A confirmed link accepts each system id the chosen candidate carries. "kein
    Link" rejects exactly the candidates that were offered — a historian who
    declined three candidates has said nothing about a fourth.
    """
    accepted: list[tuple[str, str]] = []
    rejected: list[tuple[str, str]] = []
    for ent in entities:
        mention = (ent.get("text") or ent.get("normalised") or "").strip()
        if not mention:
            continue
        method = ent.get("link_method")
        if method == "human_confirmed":
            accepted.extend((mention, pid) for pid in sorted(ids_of(ent)))
        elif method == "human_none":
            rejected.extend((mention, pid) for pid in ent.get("rejected_candidates") or [])
    return accepted, rejected


# ── agreement ────────────────────────────────────────────────────────────────

def _proposals_by_mention(entities: Iterable[dict]) -> dict[str, set[str]]:
    by: dict[str, set[str]] = defaultdict(set)
    for ent in entities:
        key = norm_mention(ent.get("text") or ent.get("normalised") or "")
        if key:
            by[key] |= pipeline_ids(ent)
    return by


def null_reference(n_accepted: int, n_rejected: int, agreement: float, accept_hit: int) -> dict:
    """What a linker proposing nothing scores on this gold, and the lift over it."""
    total = n_accepted + n_rejected
    null_agreement = (n_rejected / total) if total else 0.0
    return {
        "null_agreement": round(null_agreement, 4),
        "lift_over_null": round(agreement - null_agreement, 4),
        "accept_rate": round(accept_hit / n_accepted, 4) if n_accepted else 0.0,
        "negative_share": round(n_rejected / total, 4) if total else 0.0,
    }


def _agreement(proposed: dict[str, set[str]], accepted, rejected) -> dict:
    accept_hit = sum(1 for m, pid in accepted if pid in proposed.get(norm_mention(m), set()))
    accept_miss = len(accepted) - accept_hit
    reject_hit = sum(1 for m, pid in rejected if pid in proposed.get(norm_mention(m), set()))
    reject_avoided = len(rejected) - reject_hit
    reviewed = len(accepted) + len(rejected)
    agreement = (accept_hit + reject_avoided) / reviewed if reviewed else 0.0
    return {
        "reviewed_pairs": reviewed,
        "accept_hit": accept_hit,
        "accept_miss": accept_miss,
        "reject_hit": reject_hit,
        "reject_avoided": reject_avoided,
        "agreement": round(agreement, 4),
        **null_reference(len(accepted), len(rejected), agreement, accept_hit),
    }


def linking_agreement(entities: list[dict], accepted: list[tuple[str, str]],
                      rejected: list[tuple[str, str]]) -> dict[str, dict]:
    """Agreement per system, each judged only against the pairs of its system,
    plus ``combined`` over all reviewed pairs. Systems without gold are omitted."""
    proposed = _proposals_by_mention(entities)
    acc_by, rej_by = split_pairs_by_system(accepted), split_pairs_by_system(rejected)
    result: dict[str, dict] = {}
    for system in SYSTEMS:
        if acc_by[system] or rej_by[system]:
            result[system] = _agreement(proposed, acc_by[system], rej_by[system])
    if accepted or rejected:
        result["combined"] = _agreement(proposed, accepted, rejected)
    return result


# ── diagnosis ────────────────────────────────────────────────────────────────

def diagnose(entities: list[dict], accepted: Pairs, *, doc_id: str = "") -> list[dict]:
    """One row per accepted pair: ``HIT`` or the cause of the miss."""
    by_mention: dict[str, list[dict]] = defaultdict(list)
    for ent in entities:
        key = norm_mention(ent.get("text") or ent.get("normalised") or "")
        if key:
            by_mention[key].append(ent)

    rows = []
    for mention, pair_id in accepted:
        ents = by_mention.get(norm_mention(mention), [])
        proposed: set[str] = set().union(*(pipeline_ids(e) for e in ents)) if ents else set()
        considered: set[str] = set().union(*(candidate_ids(e) for e in ents)) if ents else set()
        if pair_id in proposed:
            cause = "HIT"
        elif not ents:
            cause = "not_extracted"
        elif not considered:
            cause = "no_candidates"
        elif pair_id in considered:
            cause = "ranked_below_top"
        else:
            cause = "candidate_missing"
        rows.append({"doc_id": doc_id, "mention": mention, "accepted_id": pair_id,
                     "cause": cause, "proposed": sorted(proposed)})
    return rows


# ── over a directory of outputs ──────────────────────────────────────────────

def load_entities(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ents = data.get("entities", []) if isinstance(data, dict) else data
    return [e for e in ents if isinstance(e, dict)]


def evaluate_dir(entities_dir: Path) -> dict:
    """Agreement and diagnosis over every ``*_entities.json`` under *entities_dir*."""
    all_entities: list[dict] = []
    accepted: list[tuple[str, str]] = []
    rejected: list[tuple[str, str]] = []
    rows: list[dict] = []
    documents = 0
    for path in sorted(entities_dir.rglob("*_entities.json")):
        doc_id = path.stem.removesuffix("_entities")
        ents = load_entities(path)
        acc, rej = gold_from_entities(ents)
        documents += 1
        all_entities.extend(ents)
        accepted.extend(acc)
        rejected.extend(rej)
        rows.extend(diagnose(ents, acc, doc_id=doc_id))
    causes: dict[str, int] = defaultdict(int)
    for r in rows:
        causes[r["cause"]] += 1
    return {
        "documents": documents,
        "gold": {"accepted": len(accepted), "rejected": len(rejected)},
        "agreement": linking_agreement(all_entities, accepted, rejected),
        "causes": dict(causes),
        "misses": [r for r in rows if r["cause"] != "HIT"],
    }


def format_report(report: dict) -> str:
    gold = report["gold"]
    lines = [f"documents: {report['documents']}   gold: {gold['accepted']} accepted / "
             f"{gold['rejected']} rejected pairs"]
    agreement = report["agreement"]
    if not agreement:
        lines.append("no Gate 3 decisions found — nothing to judge against")
        return "\n".join(lines)
    header = f"{'system':<10}{'pairs':>6}{'agreement':>11}{'null':>7}{'lift':>8}"
    lines.append(header + "  acc hit/miss  rej hit/avoided")
    for system, r in agreement.items():
        row = (f"{system:<10}{r['reviewed_pairs']:>6}{r['agreement']:>11.4f}"
               f"{r['null_agreement']:>7.3f}{r['lift_over_null']:>+8.4f}")
        row += f"  {r['accept_hit']:>3}/{r['accept_miss']:<5}"
        row += f"  {r['reject_hit']:>3}/{r['reject_avoided']}"
        lines.append(row)
    if report["causes"]:
        causes = ", ".join(f"{k} {v}" for k, v in sorted(report["causes"].items()))
        lines.append("causes: " + causes)
    for r in report["misses"]:
        lines.append(f"  {r['doc_id'][:28]:<28} {r['mention'][:26]:<26} "
                     f"{r['accepted_id']:<22} {r['cause']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--entities-dir", default=None,
                    help="directory of <doc_id>_entities.json files (default: data/outputs)")
    ap.add_argument("--json", dest="json_out", default=None, help="write the full report here")
    args = ap.parse_args(argv)
    if args.entities_dir:
        entities_dir = Path(args.entities_dir)
    else:
        import config

        entities_dir = Path(config.DATA_DIR) / "outputs"
    report = evaluate_dir(entities_dir)
    print(format_report(report))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
