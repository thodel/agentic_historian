"""
agents/argument_agent.py — Agent F: questions answered from evidence (#812).

A question to the corpus — *"Wie wird Almosen in den Königsfelder Urkunden des
14. Jahrhunderts begründet?"* — gets an answer in which every claim points to a
passage a reader can open, and says what kind of text that passage is: a
reading a historian chose at Gate 2, an uncorrected recogniser output on a page
where the engines disagree, or an edition's text from the federation. The
evidence unit is ``evidence.py`` (#811); this module only argues with it.

The shape follows SPIRE (Pan et al., EMNLP 2026, arXiv:2605.30947;
``docs/SPIRE_COMPARISON.md``), in six stages, each a public function:

1. :func:`plan` — sub-questions, search terms, period, languages (one call)
2. :func:`retrieve` — own passages (``passage_find``) and the federation's full
   text (``mcp_client.search_fulltext``), every hit an ``EvidenceItem``
3. :func:`sample` — a balanced selection across holdings, centuries and
   languages; items outside the asked period are dropped *and counted*
4. :func:`bind` — the model states claims and cites evidence ids with quotes;
   :func:`check_bindings` then verifies every quote mechanically (one call)
5. :func:`represent` — the answer and its audit, rendered without a model
6. :func:`reflect` — what is missing; at most ``ARGUMENT_MAX_ROUNDS`` rounds of
   targeted retrieval and rebinding (one call per round)

Rules taken from this repository rather than from the paper:

* **A quote is checked, never repaired.** A binding whose quote is not a
  verbatim substring of the passage its id names is rejected and listed in the
  audit with the reason — including a quote that does stand in *another*
  passage, which is the failure that looks most like a success.
* **Quotes are in the original language.** A translated quote fails the check
  above by construction; the model is told to translate in the claim, not in
  the quote (SPIRE's self-translated Latin).
* **The period is checked without a model.** Agent B's ``Datierung`` against
  the period asked; an item outside it does not reach the binding stage, and
  an undated or ``unsicher`` one is marked as such (SPIRE's period conflation).
* **Gaps are results.** Every retriever call is recorded — asked, answered,
  failed — and ``complete`` is false when any failed or the budget stopped the
  run, as with Agent D's ``provenance`` (#634).
* **No model scores the answer.** Quality is measured from historians' review
  of the bindings (Gate 4, docs/SPIRE_COMPARISON.md), not by another model.
* **The budget is checked before every call**, tokens and calls, and a stop
  says which stage it skipped.

The model and the retrievers are parameters, so the whole flow runs offline in
the tests; the defaults talk to GPUStack, the passage index and the MCP
federation.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

import config
import evidence as ev

__all__ = [
    "Audit",
    "Binding",
    "BudgetExhausted",
    "Claim",
    "Plan",
    "Result",
    "ask",
    "bind",
    "check_bindings",
    "plan",
    "reflect",
    "render_markdown",
    "represent",
    "retrieve",
    "sample",
    "write_result",
]

#: ``llm(prompt, system, max_tokens) -> (text, usage)`` where usage may carry
#: ``prompt_tokens`` and ``completion_tokens``. Missing counts are unknown,
#: not zero.
LLM = Callable[[str, str, int], tuple[str, dict]]

#: ``retriever(query, *, bestand, limit) -> list[EvidenceItem]``. Raising is
#: allowed and recorded; it does not end the run.
Retriever = Callable[..., list]

MIN_SUBQUESTIONS, MAX_SUBQUESTIONS = 1, 4
MAX_TERMS = 3
MAX_GAPS = 3


# ── records ──────────────────────────────────────────────────────────────────

class BudgetExhausted(RuntimeError):
    """Raised by :meth:`Audit.charge` before a call the budget cannot cover."""


@dataclass
class Audit:
    """What the run cost and why it stopped (Agent E's view of Agent F)."""

    max_tokens: int = 0
    max_calls: int = 0
    calls: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    unknown_usage_calls: int = 0
    rounds: int = 0
    stopped_because: str = ""
    skipped: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def charge(self, stage: str) -> None:
        """Refuse a call the budget cannot take. Checked *before* calling."""
        if self.max_calls and len(self.calls) >= self.max_calls:
            reason = f"call budget ({self.max_calls}) reached"
        elif self.max_tokens and self.tokens >= self.max_tokens:
            reason = f"token budget ({self.max_tokens}) reached"
        else:
            return
        self.stopped_because = self.stopped_because or reason
        self.skipped.append(stage)
        raise BudgetExhausted(reason)

    def record(self, stage: str, usage: dict) -> None:
        p, c = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if isinstance(p, int) and isinstance(c, int):
            self.prompt_tokens += p
            self.completion_tokens += c
        else:
            self.unknown_usage_calls += 1
        self.calls.append({"stage": stage, "prompt_tokens": p, "completion_tokens": c})

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tokens"] = self.tokens
        return d


@dataclass
class Plan:
    question: str
    subquestions: list[dict]            # [{"id": "q1", "text": ..., "terms": [...]}]
    period: dict | None = None          # {"from": 1300, "to": 1399}
    languages: list[str] = field(default_factory=list)
    from_model: bool = True             # False: the fallback plan, said so

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Binding:
    evidence_id: str
    quote: str
    ok: bool = False
    reason: str = ""                    # why a binding was rejected


@dataclass
class Claim:
    text: str
    bindings: list[Binding]

    @property
    def supported(self) -> bool:
        return any(b.ok for b in self.bindings)


@dataclass
class Result:
    question_id: str
    question: str
    filters: dict
    plan: Plan
    pool: dict                          # evidence_id -> EvidenceItem
    sampled: list[str]
    dropped: list[dict]                 # [{"evidence_id", "reason"}]
    claims: list[Claim]
    rejected_claims: list[Claim]
    summary: str
    summary_rejected: str
    coverage: list[dict]
    gaps: list[dict]
    audit: Audit
    complete: bool
    created_at: str

    def to_dict(self) -> dict:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "filters": self.filters,
            "plan": self.plan.to_dict(),
            "sampled": self.sampled,
            "dropped": self.dropped,
            "claims": [asdict(c) for c in self.claims],
            "rejected_claims": [asdict(c) for c in self.rejected_claims],
            "summary": self.summary,
            "summary_rejected": self.summary_rejected,
            "coverage": self.coverage,
            "gaps": self.gaps,
            "audit": self.audit.to_dict(),
            "complete": self.complete,
            "created_at": self.created_at,
        }


# ── the model ────────────────────────────────────────────────────────────────

def gpustack_llm(prompt: str, system: str, max_tokens: int) -> tuple[str, dict]:
    """The default model: GPUStack's text role (``gpt-oss-120b``)."""
    from utils import gpustack_client as gs

    text = gs.chat_text(prompt, system=system, max_tokens=max_tokens,
                        temperature=0.2, agent_name="agent_f")
    return text, gs.last_usage()


def _call(llm: LLM, audit: Audit, stage: str, prompt: str, system: str) -> dict:
    """One budgeted model call that must return a JSON object."""
    from utils.llm_json import loads

    audit.charge(stage)
    try:
        text, usage = llm(prompt, system, config.ARGUMENT_CALL_MAX_TOKENS)
    except Exception as e:                    # becomes a recorded gap
        audit.record(stage, {})
        raise ValueError(f"model call failed: {e}") from e
    audit.record(stage, usage or {})
    data = loads(text)
    if not isinstance(data, dict):
        raise TypeError(f"{stage}: expected a JSON object")
    return data


# ── 1. plan ──────────────────────────────────────────────────────────────────

_PLAN_SYSTEM = (
    "Du planst eine Recherche in handschriftlichen Quellen (Schweiz, 14.–19. Jh.). "
    "Antworte nur mit einem JSON-Objekt."
)

_PLAN_PROMPT = """Forschungsfrage: {question}

Zerlege die Frage in 2 bis 4 Teilfragen. Gib zu jeder Teilfrage bis zu drei
Suchbegriffe in der Sprache und Schreibung der Quellen (frühneuhochdeutsch,
mittelhochdeutsch oder lateinisch, z. B. "almuosen", "elemosina"). Nenne den
Zeitraum der Frage in Jahren, falls sie einen hat, und die Quellsprachen.

{{"subquestions": [{{"text": "...", "terms": ["..."]}}],
 "period": {{"from": 1300, "to": 1399}} oder null,
 "languages": ["de", "la"]}}"""


def _year(v) -> int | None:
    try:
        y = int(v)
    except (TypeError, ValueError):
        return None
    return y if 500 <= y <= 2100 else None


def plan(question: str, *, llm: LLM, audit: Audit,
         period: dict | None = None) -> Plan:
    """Sub-questions and search terms. A plan the model fails to give falls
    back to the question itself as the only sub-question, marked as such."""
    try:
        data = _call(llm, audit, "plan", _PLAN_PROMPT.format(question=question),
                     _PLAN_SYSTEM)
    except BudgetExhausted:
        raise
    except (ValueError, TypeError) as e:
        audit.events.append(f"plan: no usable plan from the model ({e}); "
                            "the question is the only sub-question")
        return Plan(question, [{"id": "q1", "text": question, "terms": []}],
                    period=period, from_model=False)

    subs = []
    for raw in (data.get("subquestions") or [])[:MAX_SUBQUESTIONS]:
        text = str((raw or {}).get("text") or "").strip() if isinstance(raw, dict) else ""
        if not text:
            continue
        terms = [str(t).strip() for t in (raw.get("terms") or []) if str(t).strip()]
        subs.append({"id": f"q{len(subs) + 1}", "text": text, "terms": terms[:MAX_TERMS]})
    if len(subs) < MIN_SUBQUESTIONS:
        subs = [{"id": "q1", "text": question, "terms": []}]
        audit.events.append("plan: the model gave no sub-question; "
                            "the question is the only one")
    p = data.get("period") if isinstance(data.get("period"), dict) else None
    model_period = None
    if p and _year(p.get("from")) and _year(p.get("to")):
        model_period = {"from": _year(p["from"]), "to": _year(p["to"])}
    langs = [str(x) for x in (data.get("languages") or []) if str(x).strip()]
    # A period the caller set is a filter; the model's is a reading of the
    # question. The caller's wins.
    return Plan(question, subs, period=period or model_period, languages=langs)


# ── 2. retrieve ──────────────────────────────────────────────────────────────

def own_passages(query: str, *, bestand: str | None = None,
                 limit: int = 10) -> list:
    """Passages from our own corpus, each widened to its line and neighbours."""
    import passage_find

    found = passage_find.find(query, bestand=bestand, top_k=limit)
    items = []
    for hit in found.hits:
        row = hit.passage
        pipeline = _load_pipeline(row.get("doc_id") or "")
        if not pipeline:
            continue
        it = ev.item_from_passage_row(row, pipeline=pipeline,
                                      state=_load_state(row.get("doc_id") or ""),
                                      widen_lines=1)
        if it is not None:
            it.retrieval = {"score": round(float(hit.score), 4),
                            "scored_by": hit.scored_by}
            items.append(it)
    if not items and not found.hits:
        raise LookupError(found.why_empty() or "no passages")
    return items


class PartialRetrieval(RuntimeError):
    """Some sources answered and some did not: ``items`` are kept, ``errors``
    become the coverage row's error."""

    def __init__(self, items: list, errors: list[str]):
        super().__init__("; ".join(errors))
        self.items, self.errors = items, errors


def federation_fulltext(query: str, *, bestand: str | None = None,
                        limit: int = 10, call_tool=None) -> list:
    """Edition text from every federated source that offers full-text search.

    ``mcp_client.search_fulltext`` turns a source that fails into an empty
    list, so an unreachable federation would read as "nothing found". The
    errors are caught on the way through its ``call_tool`` hook instead: all
    sources failing raises, some failing raises :class:`PartialRetrieval`
    with what the others returned.
    """
    import asyncio

    from utils import mcp_client

    inner = call_tool or mcp_client._call_tool
    errors: list[str] = []

    async def watched(source, tool, arguments):
        try:
            return await inner(source, tool, arguments)
        except Exception as e:
            errors.append(f"{source.name}: {e}"[:200])
            raise

    hits = asyncio.run(mcp_client.search_fulltext(query, limit=limit,
                                                  call_tool=watched))
    items = []
    for h in hits:
        if bestand and h.source != bestand:
            continue
        it = ev.edition_item(h.source, h.doc_id, h.snippet)
        if it is not None:
            it.retrieval = {"score": round(float(h.score or 0.0), 4),
                            "scored_by": "fulltext"}
            items.append(it)
    if errors and not hits:
        raise LookupError("; ".join(errors))
    if errors:
        raise PartialRetrieval(items, errors)
    return items


def _load_pipeline(doc_id: str) -> dict | None:
    p = config.OUTPUTS_DIR / f"{doc_id}_pipeline.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _load_state(doc_id: str):
    from runstate import RunState

    try:
        return RunState.load(doc_id) if RunState.exists(doc_id) else None
    except (OSError, ValueError):
        return None


DEFAULT_RETRIEVERS: dict[str, Retriever] = {
    "passages": own_passages,
    "fulltext": federation_fulltext,
}


def retrieve(queries: list[dict], *, retrievers: dict[str, Retriever],
             pool: dict, coverage: list[dict], bestand: str | None = None,
             limit: int = 10) -> int:
    """Run every query through every retriever into ``pool``; return new items.

    ``queries`` are ``{"id", "text", "terms"}``. The semantic retriever gets
    the sub-question; the full-text one each term (or the text when there are
    none), because a keyword search for a whole sentence finds nothing.
    Every call leaves a row in ``coverage`` — including the failed ones.
    """
    added = 0
    for q in queries:
        for name, fn in retrievers.items():
            asks = ([q["text"]] if name != "fulltext"
                    else (q.get("terms") or [q["text"]]))
            for query in asks:
                row = {"subquestion": q["id"], "retriever": name, "query": query}
                try:
                    items = fn(query, bestand=bestand, limit=limit) or []
                except PartialRetrieval as e:
                    items = e.items
                    row.update(status="partial", error=str(e)[:300])
                except Exception as e:        # noqa: BLE001 — a gap, recorded
                    row.update(status="failed", hits=0, error=str(e)[:300])
                    coverage.append(row)
                    logger.warning(f"[agent_f] {name} {query!r}: {e}")
                    continue
                row.setdefault("status", "ok" if items else "empty")
                row["hits"] = len(items)
                coverage.append(row)
                for it in items:
                    it.retrieval = {**(it.retrieval or {}), "subquestion": q["id"],
                                    "retriever": name, "query": query}
                    if it.evidence_id not in pool:
                        pool[it.evidence_id] = it
                        added += 1
    return added


# ── 3. sample ────────────────────────────────────────────────────────────────

def _century_span(item) -> tuple[int, int] | None:
    from agent_a.model_selector import parse_century

    raw = (item.source or {}).get("datierung")
    if not raw:
        return None
    m = re.findall(r"\b(1[0-9]{3})\b", raw)
    if m:
        years = [int(y) for y in m]
        return min(years), max(years)
    c = parse_century(raw)
    return ((c - 1) * 100 + 1, c * 100) if c else None


def period_check(item, period: dict | None) -> str:
    """``inside`` / ``outside`` / ``undated`` / ``unchecked`` (no period asked)."""
    if not period:
        return "unchecked"
    span = _century_span(item)
    if span is None:
        return "undated"
    lo, hi = span
    return "inside" if (lo <= period["to"] and hi >= period["from"]) else "outside"


def sample(pool: dict, plan_: Plan, *, size: int) -> tuple[list[str], list[dict]]:
    """A balanced, deterministic selection; ``(sampled ids, dropped)``.

    Outside the asked period → dropped with that reason. The rest are taken
    round-robin over sub-questions, and within one over groups of (holding,
    century, language), best score first, so one prolific holding or one
    well-indexed document cannot fill the sample alone. What does not fit is
    dropped with ``"sample full"`` — counted, so an answer can say how much it
    did not look at.
    """
    dropped: list[dict] = []
    eligible = []
    for eid, it in pool.items():
        check = period_check(it, plan_.period)
        it.retrieval = {**(it.retrieval or {}), "period": check}
        if check == "outside":
            dropped.append({"evidence_id": eid, "reason": "outside the period asked"})
        else:
            eligible.append(it)

    def group(it) -> tuple:
        span = _century_span(it)
        langs = (it.source or {}).get("sprachen") or []
        return ((it.source or {}).get("bestand") or it.reading.get("model_id") or "",
                span[0] // 100 if span else -1, langs[0] if langs else "")

    by_sub: dict[str, dict[tuple, list]] = defaultdict(lambda: defaultdict(list))
    for it in sorted(eligible, key=lambda i: (-(i.retrieval or {}).get("score", 0.0),
                                              i.evidence_id)):
        by_sub[(it.retrieval or {}).get("subquestion", "q1")][group(it)].append(it)

    queues = {s: [list(g) for _, g in sorted(groups.items())]
              for s, groups in sorted(by_sub.items())}
    picked: list[str] = []
    while len(picked) < size and any(any(g) for g in queues.values()):
        for sub in sorted(queues):
            groups = queues[sub]
            k = next((i for i, g in enumerate(groups) if g), None)
            if k is None:
                continue
            picked.append(groups[k].pop(0).evidence_id)
            groups.append(groups.pop(k))          # that group goes to the back
            if len(picked) >= size:
                break
    left = {it.evidence_id for it in eligible} - set(picked)
    dropped += [{"evidence_id": e, "reason": "sample full"} for e in sorted(left)]
    return picked, dropped


# ── 4. bind ──────────────────────────────────────────────────────────────────

_BIND_SYSTEM = (
    "Du argumentierst als Historiker ausschliesslich aus den vorgelegten Passagen. "
    "Antworte nur mit einem JSON-Objekt."
)

_BIND_PROMPT = """Forschungsfrage: {question}

Passagen (Kennung, Herkunft, Text):
{passages}

Beantworte die Frage in Behauptungen. Jede Behauptung stützt sich auf mindestens
eine Passage. Für jede Stütze gibst du die Kennung und ein wörtliches Zitat aus
genau dieser Passage an — Zeichen für Zeichen, in der Sprache und Schreibung der
Quelle, ohne Übersetzung, Normalisierung oder Auslassung. Übersetzen darfst du in
der Behauptung, nie im Zitat. Behaupte nichts, was keine Passage trägt. Eine
Zusammenfassung verweist auf Behauptungen als [1], [2], … und zitiert nicht.

{{"claims": [{{"claim": "...", "evidence": [{{"id": "ev-…", "quote": "..."}}]}}],
 "summary": "..."}}"""


def _describe(it) -> str:
    r, s = it.reading, it.source or {}
    bits = [it.doc_id + (f", {it.page}" if it.page else "")]
    if s.get("datierung"):
        bits.append(s["datierung"] + (" (unsicher)" if s.get("datierung_unsicher") else ""))
    if r.get("status") == ev.EDITION:
        bits.append(f"Edition {r.get('model_id')}")
    else:
        bits.append(f"Lesung {r.get('engine')}/{r.get('model_id')}: {r.get('status')}")
        if it.agreement == "contested":
            bits.append("Engines uneinig an dieser Stelle")
    return " · ".join(bits)


def _passages_block(items: list) -> str:
    return "\n\n".join(f"[{it.evidence_id}] ({_describe(it)})\n{it.quote}"
                       for it in items)


def check_bindings(raw_claims: list, pool: dict,
                   allowed: set[str] | None = None) -> tuple[list[Claim], list[Claim]]:
    """Verify every binding mechanically; ``(supported, rejected)`` claims.

    A binding is accepted only when its id names an item the model was shown
    (``allowed``, default the whole pool) and its quote is a verbatim substring
    of that item's quote. A rejected binding carries its reason:

    * ``unknown id`` — no such item
    * ``not shown`` — an item from the pool the model was not given
    * ``empty quote``
    * ``quote is in <id>, not in the cited passage`` — it exists, elsewhere
    * ``quote not in the cited passage``

    A claim with no accepted binding is rejected as a whole. Nothing is
    repaired: a near-miss is a miss.
    """
    allowed = set(pool) if allowed is None else allowed
    ok_claims, bad_claims = [], []
    for raw in raw_claims or []:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("claim") or "").strip()
        bindings = []
        for b in raw.get("evidence") or []:
            if not isinstance(b, dict):
                continue
            eid, quote = str(b.get("id") or "").strip(), str(b.get("quote") or "")
            bd = Binding(eid, quote)
            item = pool.get(eid)
            if item is None:
                bd.reason = "unknown id"
            elif eid not in allowed:
                bd.reason = "not shown"
            elif not quote.strip():
                bd.reason = "empty quote"
            elif quote in item.quote:
                bd.ok = True
            else:
                elsewhere = next((k for k in sorted(allowed)
                                  if k != eid and k in pool and quote in pool[k].quote),
                                 None)
                bd.reason = (f"quote is in {elsewhere}, not in the cited passage"
                             if elsewhere else "quote not in the cited passage")
            bindings.append(bd)
        if not text:
            continue
        claim = Claim(text, bindings)
        (ok_claims if claim.supported else bad_claims).append(claim)
    return ok_claims, bad_claims


#: Anything between quotation marks in a summary must be a bound quote. Plain
#: apostrophes are left out: "des Spitals' Rechte" is not a quotation.
_QUOTED = re.compile(r'"([^"]{4,})"|„([^“”"]{4,})[“”"]|«([^»]{4,})»|»([^«]{4,})«'
                     r"|‚([^‘’]{4,})[‘’]")


def check_summary(summary: str, claims: list[Claim]) -> tuple[str, str]:
    """``(summary, "")`` or ``("", reason)``: a summary that quotes anything not
    bound in an accepted claim, or cites a claim number that does not exist,
    is withheld — the claims stand on their own."""
    summary = (summary or "").strip()
    if not summary:
        return "", ""
    bound = [b.quote for c in claims for b in c.bindings if b.ok]
    for groups in _QUOTED.findall(summary):
        q = next(g for g in groups if g)
        if not any(q.strip() in b for b in bound):
            return "", f"quotes text no accepted binding carries: {q.strip()[:80]!r}"
    for n in re.findall(r"\[(\d+)\]", summary):
        if not 1 <= int(n) <= len(claims):
            return "", f"cites claim [{n}], which does not exist"
    return summary, ""


def bind(question: str, items: list, pool: dict, *, llm: LLM,
         audit: Audit) -> tuple[list[Claim], list[Claim], str, str]:
    """Claims from the sampled items, checked. One call."""
    data = _call(llm, audit, "bind",
                 _BIND_PROMPT.format(question=question, passages=_passages_block(items)),
                 _BIND_SYSTEM)
    ok, bad = check_bindings(data.get("claims") or [], pool,
                             allowed={it.evidence_id for it in items})
    summary, why = check_summary(str(data.get("summary") or ""), ok)
    if why:
        audit.events.append(f"bind: summary withheld — {why}")
    return ok, bad, summary, why


# ── 6. reflect ───────────────────────────────────────────────────────────────

_REFLECT_PROMPT = """Forschungsfrage: {question}
Teilfragen: {subs}
Gestützte Behauptungen:
{claims}
Suchen ohne Treffer oder fehlgeschlagen: {gaps}

Was fehlt, um die Frage zu beantworten? Gib höchstens drei gezielte Suchanfragen
(mit Suchbegriffen in der Schreibung der Quellen) — oder keine, wenn die
Behauptungen die Frage tragen.

{{"gaps": [{{"text": "...", "terms": ["..."], "why": "..."}}]}}"""


def reflect(question: str, plan_: Plan, claims: list[Claim], coverage: list[dict],
            *, llm: LLM, audit: Audit, round_no: int) -> list[dict]:
    """Targeted follow-up queries, or none. One call."""
    misses = sorted({f"{c['retriever']}:{c['query']}" for c in coverage
                     if c["status"] != "ok"})
    data = _call(llm, audit, f"reflect-{round_no}", _REFLECT_PROMPT.format(
        question=question,
        subs="; ".join(s["text"] for s in plan_.subquestions),
        claims="\n".join(f"[{i}] {c.text}" for i, c in enumerate(claims, 1)) or "(keine)",
        gaps=", ".join(misses) or "(keine)"), _PLAN_SYSTEM)
    gaps = []
    for raw in (data.get("gaps") or [])[:MAX_GAPS]:
        if not isinstance(raw, dict) or not str(raw.get("text") or "").strip():
            continue
        gaps.append({"id": f"r{round_no}g{len(gaps) + 1}",
                     "text": str(raw["text"]).strip(),
                     "terms": [str(t).strip() for t in (raw.get("terms") or [])
                               if str(t).strip()][:MAX_TERMS],
                     "why": str(raw.get("why") or "").strip()})
    return gaps


# ── the whole run ────────────────────────────────────────────────────────────

def question_id(question: str, filters: dict, when: str) -> str:
    raw = json.dumps([question, filters, when], ensure_ascii=False, sort_keys=True)
    return "q-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def ask(question: str, *, bestand: str | None = None,
        year_from: int | None = None, year_to: int | None = None,
        llm: LLM | None = None, retrievers: dict[str, Retriever] | None = None,
        max_tokens: int | None = None, max_calls: int | None = None,
        max_rounds: int | None = None, sample_size: int | None = None,
        now: str | None = None) -> Result:
    """Answer ``question`` from evidence. Never raises for a model or retriever
    failure: what could not be done is in the result."""
    llm = llm or gpustack_llm
    retrievers = DEFAULT_RETRIEVERS if retrievers is None else retrievers
    audit = Audit(max_tokens=config.ARGUMENT_MAX_TOKENS if max_tokens is None else max_tokens,
                  max_calls=config.ARGUMENT_MAX_CALLS if max_calls is None else max_calls)
    max_rounds = config.ARGUMENT_MAX_ROUNDS if max_rounds is None else max_rounds
    size = config.ARGUMENT_SAMPLE_SIZE if sample_size is None else sample_size
    when = now or datetime.now(UTC).isoformat(timespec="seconds")
    period = ({"from": year_from or 0, "to": year_to or 9999}
              if (year_from or year_to) else None)
    filters = {"bestand": bestand, "from": year_from, "to": year_to}

    pool: dict = {}
    coverage: list[dict] = []
    claims: list[Claim] = []
    rejected: list[Claim] = []
    summary = summary_rejected = ""
    gaps_all: list[dict] = []
    sampled: list[str] = []
    dropped: list[dict] = []

    try:
        p = plan(question, llm=llm, audit=audit, period=period)
    except BudgetExhausted:
        p = Plan(question, [{"id": "q1", "text": question, "terms": []}],
                 period=period, from_model=False)

    try:
        retrieve(p.subquestions, retrievers=retrievers, pool=pool,
                 coverage=coverage, bestand=bestand)
        while True:
            sampled, dropped = sample(pool, p, size=size)
            if not sampled:
                audit.events.append("no evidence to argue from")
                break
            try:
                claims, rejected, summary, summary_rejected = bind(
                    question, [pool[e] for e in sampled], pool, llm=llm, audit=audit)
            except (ValueError, TypeError) as e:
                audit.events.append(f"bind: no usable answer from the model ({e})")
            if audit.rounds >= max_rounds:
                break
            audit.rounds += 1
            try:
                gaps = reflect(question, p, claims, coverage, llm=llm, audit=audit,
                               round_no=audit.rounds)
            except (ValueError, TypeError) as e:
                audit.events.append(f"reflect: no usable answer ({e})")
                break
            if not gaps:
                break
            gaps_all += gaps
            added = retrieve(gaps, retrievers=retrievers, pool=pool,
                             coverage=coverage, bestand=bestand)
            if not added:
                audit.events.append(f"round {audit.rounds}: the follow-up "
                                    "queries found nothing new")
                break
    except BudgetExhausted as e:
        audit.events.append(f"stopped: {e}")

    complete = (not audit.stopped_because
                and not any(c["status"] in ("failed", "partial") for c in coverage)
                and p.from_model)
    return Result(question_id=question_id(question, filters, when),
                  question=question, filters=filters, plan=p, pool=pool,
                  sampled=sampled, dropped=dropped, claims=claims,
                  rejected_claims=rejected, summary=summary,
                  summary_rejected=summary_rejected, coverage=coverage,
                  gaps=gaps_all, audit=audit, complete=complete, created_at=when)


# ── 5. represent ─────────────────────────────────────────────────────────────

_STATUS_DE = {
    ev.GROUND_TRUTH: "Referenztranskription",
    ev.HISTORIAN_CHOSEN: "an Gate 2 gewählt",
    ev.HISTORIAN_COMBINED: "an Gate 2 kombiniert",
    ev.AUTO_SELECTED: "automatisch gewählt, ungeprüft",
    ev.AUTO_FUSED: "automatisch fusioniert, ungeprüft",
    ev.NOT_CHOSEN: "an Gate 2 nicht gewählt",
    ev.UNCORRECTED: "ungeprüfte Lesung",
    ev.EDITION: "Editionstext",
}


_DROP_DE = {
    "outside the period asked": "ausserhalb des gefragten Zeitraums",
    "sample full": "Auswahl voll",
}


def _cite(it, quote: str) -> list[str]:
    r, s = it.reading, it.source or {}
    where = it.doc_id + (f", {it.page}" if it.page else "")
    link = f"[{where}]({it.catalogue_url})" if it.catalogue_url else where
    head = f"- `{it.evidence_id}` „{quote}“ — {link}"
    status = _STATUS_DE.get(r.get("status"), r.get("status") or "")
    if r.get("status") == ev.EDITION:
        meta = [f"{status} ({r.get('model_id')})"]
    elif r.get("engine") == "pipeline":
        meta = ["veröffentlichte Transkription", status]
    else:
        meta = [f"{r.get('engine')}/{r.get('model_id')}", status]
    if s.get("datierung"):
        meta.append(s["datierung"] + (" (Datierung unsicher)"
                                      if s.get("datierung_unsicher") else ""))
    period = (it.retrieval or {}).get("period")
    if period == "undated":
        meta.append("undatiert")
    lines = [head, "  " + " · ".join(m for m in meta if m)]
    if it.agreement == "contested":
        alts = "; ".join(f"{a['model_id']}: „{a['text']}“" for a in it.alternatives[:4])
        lines.append(f"  ⚠️ Engines uneinig an dieser Stelle — {alts}")
    elif it.agreement == "single" and r.get("status") != ev.EDITION:
        lines.append("  Keine unabhängige zweite Lesung zum Vergleich.")
    return lines


def render_markdown(res: Result) -> str:
    """The answer as a page a historian reads: claims, their passages with
    what kind of text each is, what was not found, and what it cost."""
    out = [f"# {res.question}", ""]
    f = {k: v for k, v in res.filters.items() if v}
    if f:
        out += ["Filter: " + ", ".join(f"{k} = {v}" for k, v in f.items()), ""]
    if not res.complete:
        out += ["> **Unvollständig.** "
                + ("; ".join(filter(None, [
                    res.audit.stopped_because,
                    "nicht jede Suche hat geantwortet"
                    if any(c["status"] in ("failed", "partial")
                           for c in res.coverage) else "",
                    "kein Plan vom Modell" if not res.plan.from_model else "",
                ])) or "siehe Audit"), ""]
    if res.summary:
        out += ["## Zusammenfassung", "", res.summary, ""]
    out += ["## Behauptungen", ""]
    if not res.claims:
        out += ["Keine Behauptung liess sich aus den gefundenen Passagen belegen.", ""]
    for i, c in enumerate(res.claims, 1):
        out.append(f"{i}. {c.text}")
        for b in c.bindings:
            if b.ok:
                out += ["   " + ln for ln in _cite(res.pool[b.evidence_id], b.quote)]
        out.append("")
    out += ["## Was nicht trägt oder fehlt", ""]
    n_bad = sum(1 for c in res.claims for b in c.bindings if not b.ok)
    n_bad += sum(len(c.bindings) for c in res.rejected_claims)
    out.append(f"- Verworfene Belege: {n_bad}; verworfene Behauptungen: "
               f"{len(res.rejected_claims)} (Gründe in `audit.json`)")
    if res.summary_rejected:
        out.append(f"- Zusammenfassung zurückgehalten: {res.summary_rejected}")
    reasons = defaultdict(int)
    for d in res.dropped:
        reasons[d["reason"]] += 1
    for why, n in sorted(reasons.items()):
        out.append(f"- Nicht vorgelegt ({_DROP_DE.get(why, why)}): {n}")
    misses = [c for c in res.coverage if c["status"] != "ok"]
    for c in misses:
        out.append(f"- Suche {c['retriever']} „{c['query']}“: {c['status']}"
                   + (f" — {c['error']}" if c.get("error") else ""))
    out += ["", "## Audit", "",
            "- Teilfragen: " + "; ".join(s["text"] for s in res.plan.subquestions),
            f"- Passagen gefunden: {len(res.pool)}, vorgelegt: {len(res.sampled)}",
            f"- Modellaufrufe: {len(res.audit.calls)}, Tokens: {res.audit.tokens}"
            + (f" (+{res.audit.unknown_usage_calls} Aufruf(e) ohne Zählung)"
               if res.audit.unknown_usage_calls else ""),
            f"- Reflexionsrunden: {res.audit.rounds}"]
    if res.audit.skipped:
        out.append(f"- Ausgelassen wegen Budget: {', '.join(res.audit.skipped)}")
    out += [f"- {e}" for e in res.audit.events]
    out += ["", ("_Eine an Gate 2 gewählte Lesung ist die nächstliegende verfügbare, "
                 "keine Ground Truth. Die Behauptungen sind vom Modell formuliert; "
                 "geprüft ist nur, dass jedes Zitat wörtlich in der genannten Passage "
                 "steht._"), ""]
    return "\n".join(out)


def represent(res: Result) -> dict:
    """``{"answer.md", "audit.json", "pool.json"}`` — the files of one answer."""
    return {
        "answer.md": render_markdown(res),
        "audit.json": json.dumps(res.to_dict(), ensure_ascii=False, indent=2),
        "pool.json": json.dumps({k: v.to_dict() for k, v in res.pool.items()},
                                ensure_ascii=False, indent=2),
    }


def write_result(res: Result, out_dir: Path) -> Path:
    """Write the three files under ``out_dir/<question_id>/``."""
    d = Path(out_dir) / res.question_id
    d.mkdir(parents=True, exist_ok=True)
    for name, body in represent(res).items():
        (d / name).write_text(body, encoding="utf-8")
    return d
