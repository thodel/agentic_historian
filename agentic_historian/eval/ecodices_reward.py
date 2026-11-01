"""#782 — a reward for Agent B from the e-codices comparison, calibration included.

``ecodices_bench.judge`` already scores a description against the catalogue
without a person and without an LLM judge (#779). This turns that verdict into a
number per element, the shape TRL's ``GRPOTrainer`` takes as a reward function,
and the number #771, #775 and #776 are measured by before anything is trained.

The trap a plain hit rate leaves open
-------------------------------------
A hit rate rewards the commonest label: whoever says "gotisch" or "15. Jh."
whenever unsure hits often. Since #775 Agent B states a confidence for its
script, so the reward also scores **calibration** — a Brier term on the first
candidate's confidence: a wrong script at 0.9 costs more than one at 0.4. A
single label with no confidence counts as a full claim (p = 1): saying nothing
about certainty is not a way out of the term.

The other ways to game it, and what closes them:

* **Abstaining.** No answer costs ``NO_ANSWER`` (−0.5): less than a wrong answer,
  so an honest "unknown" beats a guess that misses, but more than nothing, so
  silence is not the safe strategy.
* **Wide dates.** "12.–16. Jh." overlaps almost every catalogue date. A hit is
  worth ``min(1, DATE_SHARP_YEARS / width)``: a half century scores 1, a century
  0.5, five centuries 0.1.
* **Broken output.** A completion that is not JSON, or lacks one of the 16
  elements, scores ``FORMAT_PENALTY`` in total, whatever it says.

Elements the catalogue says nothing about score 0: the catalogue cannot make
Agent B right or wrong there.
"""

from __future__ import annotations

import json
from dataclasses import fields
from typing import Callable, Optional

from eval.ecodices_bench import (
    HIT, MISS, NO_ANSWER, NO_REF, Reference, date_range, judge, _wert,
)

ELEMENT_REWARD = {HIT: 1.0, MISS: -1.0, NO_ANSWER: -0.5, NO_REF: 0.0}
NO_ANSWER_REWARD = ELEMENT_REWARD[NO_ANSWER]
#: A dated hit this many years wide or narrower scores in full; wider is scaled down.
DATE_SHARP_YEARS = 50
#: The whole completion scores this when it is not the 16-element JSON.
FORMAT_PENALTY = -1.0
#: Weights of the terms in ``total``; the TRL functions carry the same ones.
WEIGHTS = {"Schrift": 1.0, "Schrift_brier": 1.0, "Datierung": 1.0, "Schreiber": 1.0,
           "Beschreibstoff": 1.0}


def first_confidence(source_json: dict) -> float:
    """The first script candidate's confidence (#775); a bare label claims 1.0."""
    el = source_json.get("Schrift") if isinstance(source_json, dict) else None
    cands = el.get("kandidaten") if isinstance(el, dict) else None
    if isinstance(cands, list) and cands and isinstance(cands[0], dict):
        p = cands[0].get("zuversicht")
        if isinstance(p, (int, float)):
            return min(1.0, max(0.0, float(p)))
    return 1.0


def well_formed(source_json) -> bool:
    from agents.source_heuristic import HANDSCHRIFTEN_ELEMENTS
    return isinstance(source_json, dict) and all(e in source_json for e in HANDSCHRIFTEN_ELEMENTS)


def reward(source_json, ref: Reference) -> dict[str, float]:
    """One term per element, plus ``Schrift_brier`` and ``format``.

    ``format`` is 0 for a well-formed description and ``FORMAT_PENALTY``
    otherwise; then every other term is 0, because there is nothing to judge.
    """
    if not well_formed(source_json):
        return {**{k: 0.0 for k in WEIGHTS}, "format": FORMAT_PENALTY}
    verdict = judge(ref, source_json)
    out = {element: ELEMENT_REWARD[v] for element, v in verdict.items()}
    if verdict["Datierung"] == HIT:
        lo, hi = date_range(_wert(source_json.get("Datierung")))
        out["Datierung"] = round(min(1.0, DATE_SHARP_YEARS / max(1, hi - lo + 1)), 4)
    if verdict["Schrift"] in (HIT, MISS):
        hit = 1.0 if verdict["Schrift"] == HIT else 0.0
        out["Schrift_brier"] = round(-(first_confidence(source_json) - hit) ** 2, 4)
    else:
        out["Schrift_brier"] = 0.0
    out["format"] = 0.0
    return out


def total(terms: dict[str, float], weights: Optional[dict] = None) -> float:
    w = WEIGHTS if weights is None else weights
    return round(terms.get("format", 0.0) + sum(w[k] * terms.get(k, 0.0) for k in w), 4)


# ── TRL ──────────────────────────────────────────────────────────────────────

def _text_of(completion) -> str:
    """A TRL completion: a string, or a conversation whose last turn is the answer."""
    if isinstance(completion, str):
        return completion
    if isinstance(completion, list) and completion:
        last = completion[-1]
        content = last.get("content") if isinstance(last, dict) else last
        if isinstance(content, list):         # multimodal parts
            return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return str(content or "")
    return ""


def parse_completion(completion) -> Optional[dict]:
    """The 16-element JSON at the start of an Agent B answer, or None."""
    from utils import llm_json
    text = _text_of(completion)
    start = text.find("{")
    if start < 0:
        return None
    body = llm_json.extract_balanced_json(text[start:])
    try:
        return json.loads(body) if body else None
    except (json.JSONDecodeError, ValueError):
        return None


def _reference(value) -> Reference:
    if isinstance(value, Reference):
        return value
    names = {f.name for f in fields(Reference)}
    return Reference(**{k: v for k, v in dict(value).items() if k in names})


def trl_reward_funcs() -> tuple[list[Callable], list[float]]:
    """``(reward_funcs, reward_weights)`` for ``GRPOTrainer``.

    Each function has TRL's signature ``f(completions, reference, **kwargs)`` and
    returns one float per completion; ``reference`` is a dataset column holding
    the catalogue's ``Reference`` (as a dict). The format penalty rides on the
    first function, so a broken completion is penalised once, not once per term.
    """
    def make(term: str, with_format: bool):
        def f(completions, reference, **kwargs):
            out = []
            for completion, ref in zip(completions, reference):
                terms = reward(parse_completion(completion), _reference(ref))
                out.append(terms[term] + (terms["format"] if with_format else 0.0))
            return out
        f.__name__ = f"ecodices_{term.lower()}"
        return f

    funcs = [make(term, i == 0) for i, term in enumerate(WEIGHTS)]
    return funcs, [WEIGHTS[t] for t in WEIGHTS]


# ── for the bench report ─────────────────────────────────────────────────────

def summarise_runs(ref: Reference, runs: list[dict]) -> dict:
    """Mean reward per term over a volume's runs, and the Brier score of the script."""
    if not runs:
        return {}
    terms = [reward(sj, ref) for sj in runs]
    out = {k: round(sum(t[k] for t in terms) / len(terms), 4) for k in (*WEIGHTS, "format")}
    out["total"] = round(sum(total(t) for t in terms) / len(terms), 4)
    judged = [t["Schrift_brier"] for t, sj in zip(terms, runs)
              if well_formed(sj) and judge(ref, sj)["Schrift"] in (HIT, MISS)]
    out["brier"] = round(-sum(judged) / len(judged), 4) if judged else None
    return out
