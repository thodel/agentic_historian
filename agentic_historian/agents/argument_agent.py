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
import edition_text
import evidence as ev

__all__ = [
    "Answered",
    "Audit",
    "Binding",
    "BudgetExhausted",
    "Claim",
    "Plan",
    "QuestionIdRefused",
    "Result",
    "answer",
    "ask",
    "bind",
    "check_bindings",
    "format_discord",
    "page_url",
    "plan",
    "publish_files",
    "question_path",
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
    #: A holding the question names ("Königsfelder Urkunden" → kf), as the
    #: model read it; used as the filter when the caller set none (#812).
    bestand: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Binding:
    evidence_id: str
    quote: str
    ok: bool = False
    reason: str = ""                    # why a binding was rejected
    #: "literal" — the quote stands as written in the passage; "reading" — it
    #: stands in the passage's expanded reading layer, and ``source_span`` is
    #: the edition's own characters for it, both layers (#812).
    layer: str = ""
    source_span: str = ""
    #: The model's word-for-word rendering of the quote in today's German,
    #: written before its claim. Shown, never checked: it is how a reader sees
    #: at a glance whether the model read the source (#812, fifth tei run).
    gloss: str = ""
    #: Where the gloss reads a charter word in its modern sense against the
    #: glossary (``check_gloss``); the claim is then marked and kept out of
    #: the summary.
    gloss_flags: list[str] = field(default_factory=list)


@dataclass
class Claim:
    text: str
    bindings: list[Binding]
    #: The claim's place in the model's own list, which is how its summary
    #: refers to it ([n]); the answer renumbers the accepted ones (#812).
    number: int = 0

    @property
    def supported(self) -> bool:
        return any(b.ok for b in self.bindings)

    @property
    def flags(self) -> list[str]:
        """Every glossary contradiction in the glosses of its accepted quotes."""
        return [f for b in self.bindings if b.ok for f in b.gloss_flags]


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

def argument_model() -> str:
    """The model Agent F asks: ``ARGUMENT_MODEL``, else GPUStack's text role
    (``gpt-oss-120b``). A setting, so another model can be tried on the same
    question without a code change (#812, fifth tei run)."""
    return config.ARGUMENT_MODEL or config.GPUSTACK_MODEL_TEXT


def gpustack_llm(prompt: str, system: str, max_tokens: int) -> tuple[str, dict]:
    """The default model, :func:`argument_model`, on GPUStack."""
    from utils import gpustack_client as gs

    text = gs.chat(prompt, model=argument_model(), system=system,
                   max_tokens=max_tokens, temperature=0.2, agent_name="agent_f")
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


#: Words of medieval charter German (and their Latin) that a reader of today's
#: German misreads — every one from a misreading on tei (#812, fifth run:
#: "mit der bescheidenheit" as modesty, "gesun gen selmesse" as a healthy
#: self-knowledge). Shown to the plan (as search terms) and to the binding
#: stage (as meanings).
_GLOSSARY = """\
- bescheidenheit: Bestimmung, Bedingung ("mit der bescheidenheit, daz …" = unter
  der Bedingung, dass …), nicht Bescheidenheit
- jartzit, jarzit (lat. anniversarium): Jahrzeit, die jährliche Gedenkmesse für
  einen Verstorbenen
- began, begangen: (eine Jahrzeit) begehen, feiern — nicht beginnen
- selmesse: Seelmesse, Messe für das Seelenheil; gesungen: gesungen, nicht gesund
- spende: Armenspende, Verteilung von Brot oder Geld an Arme
- almuͦsen (lat. elemosina): fromme Gabe, auch das dafür gestiftete Geld oder Gut
- durch got, durch … sele heiles willen (lat. pro anima, pro remedio animae): um
  Gottes willen, um des Seelenheils willen — das Motiv einer Stiftung
- sele, sel, selen: Seele, Seelen — nicht selig, nicht heilig ("durch unserr
  vordern sele willen" = um der Seelen unserer Vorfahren willen)
- fùr uns bitten (lat. orare pro nobis): für uns beten — die Fürbitte, die eine
  Stiftung erwartet
- die wile: solange, während — nicht Wille
- koͧffen, gekoͧft, gechoͧffet: kaufen, gekauft
- siech: krank ("die siechen swestern" = die kranken Schwestern)
- pflegen ("dù der jartzit pfliget"): besorgen, verantwortlich sein für
- selig ("chùnig Fridrichs, seligen"): verstorben
- gelt, gülte, zins: Ertrag, Rente, Abgabe
- frowe: Herrin, auch Klosterfrau"""

#: How a search term must look, for the plan and for every reflection round:
#: on the fifth tei run the plan obeyed it and reflection, which had not been
#: told, searched "Seelenheil", "Ablass", "Purgatorium", "Zehnt" — all empty.
_TERMS_RULE = """\
Suchbegriffe in der Sprache und Schreibung der Quellen (frühneuhochdeutsch,
mittelhochdeutsch oder lateinisch, z. B. "almuosen", "elemosina", "jartzit",
"spende", "selmesse") — einzelne Wörter, keine Suchoperatoren. Die Volltextsuche
findet nur Wörter, die so in den Quellen stehen: keine heutigen Wörter
("Beerdigung", "Gottesdienst", "Seelenheil", "Ablass"), keine Namen von
Beständen oder Editionen ("Königsfelder Urkunden"). Wörter der Urkundensprache:
""" + _GLOSSARY

_PLAN_PROMPT = """Forschungsfrage: {question}

Zerlege die Frage in 2 bis 4 Teilfragen. Gib zu jeder Teilfrage bis zu drei
<<TERMS>>

Nenne den Zeitraum der Frage in Jahren, falls sie
einen hat, und die Quellsprachen. Nennt die Frage einen dieser Bestände, gib
seine Kennung an, sonst null:
{holdings}

{{"subquestions": [{{"text": "...", "terms": ["..."]}}],
 "period": {{"from": 1300, "to": 1399}} oder null,
 "languages": ["de", "la"],
 "bestand": "kf" oder null}}""".replace("<<TERMS>>", _TERMS_RULE)


def _holdings() -> dict[str, str]:
    """The holdings a question can name: the federation's full-text sources."""
    from knowledge_hub import mcp_registry as reg

    return {s.name: s.title for s in reg.sources_for_kind("fulltext")
            if not s.external}


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
        holdings = _holdings()
        data = _call(llm, audit, "plan", _PLAN_PROMPT.format(
            question=question,
            holdings="\n".join(f"- {k}: {v}" for k, v in sorted(holdings.items()))),
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
    named = str(data.get("bestand") or "").strip().lower() or None
    if named and named not in holdings:
        audit.events.append(f"plan: the model named holding {named!r}, which is "
                            "not one of the federation's; ignored")
        named = None
    # A period the caller set is a filter; the model's is a reading of the
    # question. The caller's wins.
    return Plan(question, subs, period=period or model_period, languages=langs,
                bestand=named)


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
        if found.indexed and not found.candidates:
            # The index has passages, none in the holding asked: that is an
            # empty answer, not a failed search (#812: ten such "failures"
            # marked a Königsfelden-only answer incomplete).
            return []
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
        it = ev.edition_item(h.source, h.doc_id, plain_snippet(h.snippet),
                             datierung=h.year, title=h.title)
        if it is not None:
            it.retrieval = {"score": round(float(h.score or 0.0), 4),
                            "scored_by": "fulltext"}
            items.append(it)
    if errors and not hits:
        raise LookupError("; ".join(errors))
    if errors:
        raise PartialRetrieval(items, errors)
    return items


#: The highlighting a search engine wraps around the matched term —
#: ``<b>Almosen</b>`` (EOS), ``<mark>elemosina</mark>`` (KF). Only these tags:
#: an edition uses angle brackets for its own purposes (``<…>`` for supplied
#: text), and removing every ``<…>`` would delete words from the source.
_HIGHLIGHT = re.compile(r"</?(?:b|mark|em|strong|i|u|span)(?:\s[^>]*)?>", re.IGNORECASE)


def plain_snippet(snippet: str) -> str:
    """A search snippet as the source's text: highlight tags out, entities
    decoded. Left in, ``<b>Almosen</b> Schaf ner`` would make every verbatim
    quote of the passage fail the binding check — a model quotes the words,
    not the markup."""
    import html

    return html.unescape(_HIGHLIGHT.sub("", snippet or ""))


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


# ── 3b. context: the passage in its document ─────────────────────────────────

#: ``fetch(source, doc_id) -> text | None``; raising is a recorded gap.
DocumentFetch = Callable[[str, str], "str | None"]


def fetch_document(source: str, doc_id: str) -> str | None:
    """The whole text of one edition document, from its federation source."""
    import asyncio

    from utils import mcp_client

    return asyncio.run(mcp_client.get_document_text(source, doc_id))


def _find_in(snippet: str, full: str) -> tuple[int, int] | None:
    """Where a search snippet stands in its document: exactly, else with the
    whitespace between its words loosened. ``None`` when it does not."""
    core = snippet.strip().strip("…").strip()
    if not core:
        return None
    i = full.find(core)
    if i != -1:
        return i, i + len(core)
    words = core.split()
    if not words:
        return None
    m = re.search(r"\s+".join(re.escape(w) for w in words), full)
    return (m.start(), m.end()) if m else None


def _window(full: str, start: int, end: int, chars: int) -> str:
    """``chars`` on each side of ``[start, end)``, cut at whitespace, marked."""
    a, b = max(0, start - chars), min(len(full), end + chars)
    if a > 0:
        sp = full.find(" ", a)
        a = sp + 1 if 0 <= sp < start else a
    if b < len(full):
        sp = full.rfind(" ", end, b)
        b = sp if sp > end else b
    return ("…" if a > 0 else "") + full[a:b].strip() + ("…" if b < len(full) else "")


def expand_context(ids: list[str], pool: dict, *, audit: Audit,
                   fetch: DocumentFetch | None = None,
                   cache: dict | None = None,
                   chars: int | None = None) -> list[str]:
    """Replace each sampled edition passage by a window of its whole document.

    A search snippet is ~200 characters; on the first run on tei that was too
    little to tell a dating formula or an endorsement from the text of a
    charter. For sources with a document tool (KF ``get_entry``, EOS
    ``get_document``) the snippet is located in the document and a window of
    ``ARGUMENT_CONTEXT_CHARS`` on each side becomes the passage — a new
    evidence item, carrying the old one's retrieval record, in the pool and in
    the sample. A document that cannot be fetched, or a snippet that does not
    stand in it, leaves the snippet as it was and says so in the audit.
    """
    from knowledge_hub import mcp_registry as reg

    fetch = fetch or fetch_document
    cache = {} if cache is None else cache
    chars = config.ARGUMENT_CONTEXT_CHARS if chars is None else chars
    out = []
    for eid in ids:
        it = pool.get(eid)
        source = (it.reading or {}).get("model_id") if it is not None else None
        if (it is None or it.reading.get("status") != ev.EDITION
                or (it.retrieval or {}).get("context") == "document"):
            out.append(eid)
            continue
        try:
            has_tool = bool(reg.get_source(source).document_tool)
        except KeyError:
            has_tool = False
        if not has_tool:
            out.append(eid)
            continue
        key = (source, it.doc_id)
        try:
            if key not in cache:
                cache[key] = fetch(source, it.doc_id)
            full = cache[key]
        except Exception as e:                # noqa: BLE001 — a gap, recorded
            cache[key] = None
            audit.events.append(f"context: {source} {it.doc_id} not fetched ({e})"[:300])
            out.append(eid)
            continue
        if not full:
            audit.events.append(f"context: {source} {it.doc_id}: no document "
                                "text; the snippet stands")
            out.append(eid)
            continue
        hit = _find_in(it.quote, full)
        if hit is None:
            audit.events.append(f"context: {source} {it.doc_id}: snippet not found "
                                "in the document; the snippet stands")
            out.append(eid)
            continue
        new = ev.edition_item(source, it.doc_id, _window(full, *hit, chars),
                              datierung=it.source.get("datierung"),
                              title=it.source.get("title"), url=it.catalogue_url)
        new.retrieval = {**(it.retrieval or {}), "context": "document",
                         "snippet_id": eid}
        pool.pop(eid, None)
        pool.setdefault(new.evidence_id, new)
        out.append(new.evidence_id)
    return list(dict.fromkeys(out))


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
    asked = {(c["retriever"], c["query"].casefold()) for c in coverage}
    for q in queries:
        for name, fn in retrievers.items():
            # Full text: each term and the edition's spellings of it
            # (`almuosen` → `almuͦsen`); the semantic search gets the question.
            asks = ([q["text"]] if name != "fulltext"
                    else [v for t in (q.get("terms") or [q["text"]])
                          for v in edition_text.search_variants(t)])
            for query in asks:
                if (name, query.casefold()) in asked:
                    continue                  # the same search, already in coverage
                asked.add((name, query.casefold()))
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
genau dem angezeigten Text dieser Passage an — Zeichen für Zeichen, in der
Sprache und Schreibung der Quelle, ohne Übersetzung oder Normalisierung.
Übersetzen darfst du in der Behauptung, nie im Zitat. Ein „…“ am Anfang oder
Ende einer Passage heisst nur, dass der Ausschnitt dort abgeschnitten ist; es
gehört nicht ins Zitat. Lässt du innerhalb eines Zitats etwas aus, setze „…“:
jedes Stück muss dann wörtlich und in derselben Reihenfolge in der Passage
stehen.

Behaupte nur, was das Zitat selbst sagt:
- Schreibe einer Quelle keinen Zweck und kein Motiv zu ("um … zu"), den sie
  nicht selbst nennt.
- Eine Datierungsformel ist ein Datum, kein Inhalt: "nach Misericordia",
  "Invocavit", "an sant Agten abende" nennen einen Tag, keine Barmherzigkeit.
- Zeigt eine Passage etwas anderes als gefragt (etwa wie etwas finanziert,
  nicht wie es begründet wird), sag das so, statt es passend zu machen.
- Achte auf den Bestand jeder Passage und schreibe einem Bestand nichts zu,
  was aus einem anderen stammt.
Die Edition trennt Wörter an aufgelösten Abkürzungen: "su per" ist "super",
"Bremgar ten" ist "Bremgarten", "gesun gen" ist "gesungen". Lies solche Silben
als ein Wort; im Zitat darfst du sie getrennt oder zusammen schreiben.

Übersetze jedes Zitat zuerst Wort für Wort ins heutige Deutsch ("gloss") und
formuliere erst dann die Behauptung; sie sagt nichts, was nicht in deiner
Übersetzung steht. Die Übersetzung wird neben dem Zitat gezeigt.
Wörter der Urkundensprache, die heute anders bedeuten:
<<GLOSSAR>>
{holdings}
Schreibe Behauptungen und Zusammenfassung in der Sprache der Forschungsfrage.
Jeder Satz der Zusammenfassung nennt die Behauptungen, die ihn tragen, als [1],
[2], … (in der Reihenfolge deiner Liste) und zitiert nicht; ein Satz ohne
Verweis wird nicht gezeigt.

{{"claims": [{{"evidence": [{{"id": "ev-…", "quote": "...", "gloss": "..."}}],
              "claim": "..."}}],
 "summary": "..."}}""".replace("<<GLOSSAR>>", _GLOSSARY)


def _where(it) -> str:
    """How a reader finds the passage: the source's own title (a shelfmark
    for KF) when it sent one, else the document id."""
    title = (it.source or {}).get("title")
    return (title or it.doc_id) + (f", {it.page}" if it.page else "")


def _describe(it) -> str:
    r, s = it.reading, it.source or {}
    bits = [_where(it)]
    if s.get("datierung"):
        bits.append(s["datierung"] + (" (unsicher)" if s.get("datierung_unsicher") else ""))
    if r.get("status") == ev.EDITION:
        bits.append(f"Edition {r.get('model_id')}")
    else:
        bits.append(f"Lesung {r.get('engine')}/{r.get('model_id')}: {r.get('status')}")
        if it.agreement == "contested":
            bits.append("Engines uneinig an dieser Stelle")
    return " · ".join(bits)


def _holding_context(items: list) -> str:
    """What the model needs to know about the holdings its passages come from
    (the registry's ``context``), one line each; empty when none says more."""
    from knowledge_hub import mcp_registry as reg

    lines = []
    for source in sorted({(it.reading or {}).get("model_id") for it in items
                          if (it.reading or {}).get("status") == ev.EDITION}):
        try:
            ctx = reg.get_source(source).context
        except KeyError:
            continue
        if ctx:
            lines.append(f"- Bestand {source}: {ctx}")
    return ("Zu den Beständen:\n" + "\n".join(lines) + "\n") if lines else ""


def _passages_block(items: list) -> str:
    """The passages as the model sees them: edition text in its reading layer
    (abbreviations resolved, #812), everything else as stored."""
    return "\n\n".join(
        f"[{it.evidence_id}] ({_describe(it)})\n{it.reading_text or it.quote}"
        for it in items)


#: An omission mark inside a quote, or a cut mark at its edge.
_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.)\s*")
#: An omission may leave out at most this much of the passage: a quote that
#: splices distant sentences together is not a quote.
OMISSION_MAX_CHARS = 600


def _trim_cut(quote: str) -> str:
    """The quote without the cut marks at its edges (``…`` / ``...``)."""
    q = quote.strip()
    while True:
        m = re.match(r"^(?:…|\.\.\.)\s*", q) or re.search(r"\s*(?:…|\.\.\.)$", q)
        if not m:
            return q
        q = (q[m.end():] if m.start() == 0 else q[:m.start()]).strip()


def _spans(piece: str, item) -> list[tuple[int, int, str]]:
    """Where ``piece`` stands in the item: ``(start, end, layer)``, literal
    occurrences first, then (edition text) those in the reading layer."""
    out, i = [], item.quote.find(piece)
    while i != -1:
        out.append((i, i + len(piece), "literal"))
        i = item.quote.find(piece, i + 1)
    if item.reading_text is not None:
        layer = edition_text.reading_layer(item.quote)
        out += [(a, b, "reading") for a, b in edition_text.locate(piece, layer, item.quote)]
    return out


def _bind_quote(bd: Binding, item, pool: dict, allowed: set[str]) -> None:
    """Check ``bd.quote`` against ``item`` and fill in ``ok``/``layer``/
    ``source_span`` or ``reason``."""
    pieces = [p for p in _ELLIPSIS.split(bd.quote) if p]
    if len(pieces) == 1:
        spans = _spans(bd.quote, item)
        if spans:
            start, end, layer = spans[0]
            bd.ok, bd.layer = True, layer
            bd.source_span = item.quote[start:end] if layer == "reading" else ""
            return
    bd.quote = " … ".join(pieces)          # one way of writing the omission
    if len(pieces) > 1 and any(len(p.split()) < 2 for p in pieces):
        bd.reason = "omission leaves a piece too short to check"
        return
    elif len(pieces) > 1:
        pos, first, layers = 0, None, set()
        for p in pieces:
            hit = next(((a, b, lay) for a, b, lay in sorted(_spans(p, item))
                        if a >= pos), None)
            if hit is None:
                break
            first = hit[0] if first is None else first
            pos = hit[1]
            layers.add(hit[2])
        else:
            if pos - first > OMISSION_MAX_CHARS:
                bd.reason = f"omission spans more than {OMISSION_MAX_CHARS} characters"
                return
            bd.ok = True
            bd.layer = "reading" if "reading" in layers else "literal"
            bd.source_span = item.quote[first:pos]
            return
    elsewhere = next((k for k in sorted(allowed)
                      if k != bd.evidence_id and k in pool and bd.quote in pool[k].quote),
                     None)
    bd.reason = (f"quote is in {elsewhere}, not in the cited passage"
                 if elsewhere else "quote not in the cited passage")


#: Charter words a gloss reads in their modern sense (#812, fifth and sixth
#: tei runs): ``(word, in the quote, wrong in the gloss, right in the gloss,
#: glossary meaning)``. A gloss is flagged only when it has the wrong reading
#: **and not** the right one — "Bedingung (bescheidenheit)" passes.
_FALSE_FRIENDS = (
    ("bescheidenheit", r"bescheidenheit", r"bescheidenheit",
     r"bedingung|bestimmung|auflage|vorbehalt", "Bedingung, Bestimmung"),
    ("began", r"\bbegan\b", r"beginn", r"begeh|beging|feier", "begehen, feiern"),
    ("sele", r"\bsel(?:e|en)?\b", r"heilig|selig", r"seel", "Seele"),
    ("die wile", r"\bdie wile\b", r"\bwille\b", r"solange|während|weile|zeit",
     "solange, während"),
    ("gesungen", r"gesun\s?gen", r"gesund", r"gesungen", "gesungen"),
    ("selmesse", r"selmesse", r"selbst", r"seel", "Seelmesse"),
)


def check_gloss(quote: str, gloss: str) -> list[str]:
    """Where ``gloss`` reads a word of ``quote`` against the glossary, one
    line each; empty when it does not, or when there is no gloss.

    Mechanical, like the binding check: it knows a handful of words and only
    whether the modern reading is there and the glossary's is not. It cannot
    say a gloss is right; it can say this one is wrong.
    """
    if not gloss:
        return []
    q = " ".join(quote.split()).lower()
    out = []
    for word, in_quote, wrong, right, meaning in _FALSE_FRIENDS:
        if not re.search(in_quote, q):
            continue
        m = re.search(rf"\w*(?:{wrong})\w*", gloss, re.IGNORECASE)
        if m and not re.search(right, gloss, re.IGNORECASE):
            out.append(f"„{word}“ als „{m.group()}“ übersetzt; Glossar: {meaning}")
    return out


def check_bindings(raw_claims: list, pool: dict,
                   allowed: set[str] | None = None) -> tuple[list[Claim], list[Claim]]:
    """Verify every binding mechanically; ``(supported, rejected)`` claims.

    A binding is accepted only when its id names an item the model was shown
    (``allowed``, default the whole pool) and its quote is a verbatim substring
    of that item's quote — or, for edition text, of its reading layer, apart
    from whitespace (``edition_text.locate``; the binding then records
    ``layer="reading"`` and the edition's own characters as ``source_span``).
    A rejected binding carries its reason:

    * ``unknown id`` — no such item
    * ``not shown`` — an item from the pool the model was not given
    * ``empty quote``
    * ``quote is in <id>, not in the cited passage`` — it exists, elsewhere
    * ``quote not in the cited passage``
    * ``omission leaves a piece too short to check`` / ``omission spans more
      than … characters`` — see below

    A quote is read as a scholar writes one: ``…`` at its start or end only
    marks that the shown passage was cut there and is dropped (a window of a
    document begins and ends with it, and models copy it); ``…`` inside it
    marks an omission — every piece must then stand verbatim, in order, in
    the same passage, and ``source_span`` carries the whole stretch with what
    was left out (#812, fourth tei run).

    A claim with no accepted binding is rejected as a whole. Nothing is
    repaired: a near-miss is a miss.
    """
    allowed = set(pool) if allowed is None else allowed
    ok_claims, bad_claims = [], []
    for number, raw in enumerate(raw_claims or [], 1):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("claim") or "").strip()
        bindings = []
        for b in raw.get("evidence") or []:
            if not isinstance(b, dict):
                continue
            eid = str(b.get("id") or "").strip()
            quote = _trim_cut(str(b.get("quote") or ""))
            bd = Binding(eid, quote, gloss=str(b.get("gloss") or "").strip())
            item = pool.get(eid)
            if item is None:
                bd.reason = "unknown id"
            elif eid not in allowed:
                bd.reason = "not shown"
            elif not quote:
                bd.reason = "empty quote"
            else:
                _bind_quote(bd, item, pool, allowed)
                if bd.ok:
                    bd.gloss_flags = check_gloss(bd.quote, bd.gloss)
            bindings.append(bd)
        if not text:
            continue
        claim = Claim(text, bindings, number)
        (ok_claims if claim.supported else bad_claims).append(claim)
    return ok_claims, bad_claims


#: Anything between quotation marks in a summary must be a bound quote. Plain
#: apostrophes are left out: "des Spitals' Rechte" is not a quotation.
_QUOTED = re.compile(r'"([^"]{4,})"|„([^“”"]{4,})[“”"]|“([^“”]{4,})”'
                     r"|«([^»]{4,})»|»([^«]{4,})«|‚([^‘’]{4,})[‘’]|‘([^‘’]{4,})’")

#: A sentence ends at . ! ? followed by space — enough for a model's summary.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def check_summary(summary: str, claims: list[Claim]) -> tuple[str, str]:
    """``(summary, reason)``: the summary, sentence by sentence, reduced to
    what the accepted claims carry.

    A sentence stands only when it cites at least one claim (``[n]``, the
    model's own numbering), every claim it cites was accepted, and anything it
    puts in quotation marks is a bound quote. Its references are renumbered to
    the answer's numbering. A claim whose gloss contradicts the glossary
    (``Claim.flags``) is shown, marked, and carries no summary sentence. Every
    other sentence is withheld, and ``reason``
    says how many and why. On the third run on tei the summary — no references
    at all — said the documents justify alms "as a God-ordered righteous
    duty", which no accepted claim said (#812).
    """
    summary = (summary or "").strip()
    if not summary:
        return "", ""
    shown = {c.number: i for i, c in enumerate(claims, 1)}
    flagged = {c.number for c in claims if c.flags}
    bound = [b.quote for c in claims for b in c.bindings if b.ok]
    kept, why = [], []
    for sentence in _SENTENCE.split(summary):
        sentence = sentence.strip()
        if not sentence:
            continue
        refs = [int(n) for n in re.findall(r"\[(\d+)\]", sentence)]
        quoted = [next(g for g in groups if g).strip()
                  for groups in _QUOTED.findall(sentence)]
        if not refs:
            why.append("cites no claim")
        elif any(n not in shown for n in refs):
            why.append(f"cites claim [{next(n for n in refs if n not in shown)}], "
                       "which was not accepted")
        elif any(n in flagged for n in refs):
            why.append(f"cites claim [{shown[next(n for n in refs if n in flagged)]}], "
                       "whose gloss contradicts the glossary")
        elif any(not any(q in b for b in bound) for q in quoted):
            why.append(f"quotes text no accepted binding carries: "
                       f"{next(q for q in quoted if not any(q in b for b in bound))[:80]!r}")
        else:
            kept.append(re.sub(r"\[(\d+)\]", lambda m: f"[{shown[int(m.group(1))]}]",
                               sentence))
            continue
    if not why:
        return " ".join(kept), ""
    reason = (f"{len(why)} sentence(s) withheld: " + "; ".join(dict.fromkeys(why)))
    return " ".join(kept), reason


def bind(question: str, items: list, pool: dict, *, llm: LLM,
         audit: Audit) -> tuple[list[Claim], list[Claim], str, str]:
    """Claims from the sampled items, checked. One call."""
    data = _call(llm, audit, "bind",
                 _BIND_PROMPT.format(question=question, passages=_passages_block(items),
                                     holdings=_holding_context(items)),
                 _BIND_SYSTEM)
    ok, bad = check_bindings(data.get("claims") or [], pool,
                             allowed={it.evidence_id for it in items})
    summary, why = check_summary(str(data.get("summary") or ""), ok)
    if why:
        audit.events.append(f"bind: summary — {why}")
    return ok, bad, summary, why


# ── 6. reflect ───────────────────────────────────────────────────────────────

_REFLECT_PROMPT = """Forschungsfrage: {question}
Teilfragen: {subs}
Gestützte Behauptungen:
{claims}
Suchen ohne Treffer oder fehlgeschlagen: {gaps}

Was fehlt, um die Frage zu beantworten? Gib höchstens drei gezielte Suchanfragen
— oder keine, wenn die Behauptungen die Frage tragen. Jede mit bis zu drei
<<TERMS>>

{{"gaps": [{{"text": "...", "terms": ["..."], "why": "..."}}]}}""".replace("<<TERMS>>", _TERMS_RULE)


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
        fetch: DocumentFetch | None = None,
        now: str | None = None) -> Result:
    """Answer ``question`` from evidence. Never raises for a model or retriever
    failure: what could not be done is in the result."""
    model_note = "" if llm else f"model: {argument_model()}"
    llm = llm or gpustack_llm
    retrievers = DEFAULT_RETRIEVERS if retrievers is None else retrievers
    audit = Audit(max_tokens=config.ARGUMENT_MAX_TOKENS if max_tokens is None else max_tokens,
                  max_calls=config.ARGUMENT_MAX_CALLS if max_calls is None else max_calls)
    max_rounds = config.ARGUMENT_MAX_ROUNDS if max_rounds is None else max_rounds
    size = config.ARGUMENT_SAMPLE_SIZE if sample_size is None else sample_size
    if model_note:
        audit.events.append(model_note)
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

    # A holding the caller set is a filter; one the question names is used
    # when the caller set none, and said (#812: "Königsfelder Urkunden" was
    # answered from Basel's Grundbuch).
    scope = bestand or p.bestand
    if p.bestand and not bestand:
        audit.events.append(f"holding from the question: {p.bestand}")
    documents: dict = {}

    try:
        retrieve(p.subquestions, retrievers=retrievers, pool=pool,
                 coverage=coverage, bestand=scope)
        while True:
            sampled, dropped = sample(pool, p, size=size)
            if not sampled:
                audit.events.append("no evidence to argue from")
                break
            sampled = expand_context(sampled, pool, audit=audit, fetch=fetch,
                                     cache=documents)
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
                             coverage=coverage, bestand=scope)
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


def _cite(it, quote: str, source_span: str = "", gloss: str = "",
          flags: list[str] | None = None) -> list[str]:
    r, s = it.reading, it.source or {}
    where = _where(it)
    link = f"[{where}]({it.catalogue_url})" if it.catalogue_url else where
    head = f"- `{it.evidence_id}` „{quote}“ — {link}"
    if source_span and source_span != quote:
        label = ("Im Zusammenhang, mit dem Ausgelassenen" if "…" in quote
                 else "Edition, beide Schichten")
        head += f"\n  {label}: „{source_span}“"
    if gloss:
        head += f"\n  Übersetzung (Modell, ungeprüft): „{gloss}“"
    for f in flags or []:
        head += f"\n  ⚠️ Fraglich gelesen: {f}"
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
    if res.plan.bestand and not res.filters.get("bestand"):
        out += [(f"Bestand aus der Frage gelesen: {res.plan.bestand} "
                 "(mit `--bestand` gesetzt hätte er Vorrang)"), ""]
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
        out.append(f"{i}. {c.text}" + (" ⚠️ *Übersetzung widerspricht dem Glossar — "
                                         "nicht in der Zusammenfassung*" if c.flags else ""))
        for b in c.bindings:
            if b.ok:
                out += ["   " + ln for ln in _cite(res.pool[b.evidence_id], b.quote,
                                                  b.source_span, b.gloss,
                                                  b.gloss_flags)]
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


# ── publishing and Discord ───────────────────────────────────────────────────

#: Where answers live in the outputs repo; Pages serves ``docs/``, so the page
#: is ``<catalogue>/questions/<question_id>/``.
QUESTIONS_PREFIX = "docs/questions"

_QID = re.compile(r"^q-[0-9a-f]{10}$")

#: Discord's limit is 2000; the margin is for the mention and the link.
_DISCORD_LIMIT = 1900


class QuestionIdRefused(ValueError):
    """A question id that may not become a path in the outputs repo."""


def question_path(question_id: str) -> str:
    """``docs/questions/<question_id>`` — or a refusal. The id is made here
    (:func:`question_id`), so anything else reaching this is a defect."""
    if not _QID.match(question_id or ""):
        raise QuestionIdRefused(f"not a question id: {question_id!r}")
    return f"{QUESTIONS_PREFIX}/{question_id}"


def page_url(question_id: str) -> str | None:
    """The published page, or None without a configured catalogue."""
    from passage_find import catalogue_base

    base = catalogue_base()
    return f"{base}/questions/{question_id}/" if base else None


def publish_files(res: Result) -> dict[str, bytes]:
    """The files one answer publishes, keyed by their path in the outputs repo.

    ``index.md`` is the answer page. Pages renders it through Jekyll, and Liquid
    would read a ``{{`` or ``{%`` in a transcription or in the model's wording
    as template syntax — so the body is wrapped in ``{% raw %}``, with every
    ``{%`` inside it written as ``{&#37;`` so nothing can close the block. The title is
    JSON-quoted, which is valid YAML, so a question with a colon or a quote
    does not break the front matter. ``audit.json`` and ``pool.json`` sit next
    to it; the page links them.
    """
    prefix = question_path(res.question_id)
    files = represent(res)
    title = res.question if len(res.question) <= 120 else res.question[:117] + "…"
    # Only `{% endraw %}` could end the raw block early, and Liquid tolerates
    # any whitespace inside a tag, so every `{%` is written as an entity: the
    # page shows the same characters and Liquid sees no tag.
    body = files["answer.md"].replace("{%", "{&#37;")
    page = "\n".join([
        "---", "layout: default", f"title: {json.dumps('Frage: ' + title, ensure_ascii=False)}",
        "---", "",
        "{% raw %}", body,
        "Dateien: [audit.json](audit.json) · [pool.json](pool.json)",
        "{% endraw %}", ""])
    return {f"{prefix}/index.md": page.encode("utf-8"),
            f"{prefix}/audit.json": files["audit.json"].encode("utf-8"),
            f"{prefix}/pool.json": files["pool.json"].encode("utf-8")}


@dataclass
class Answered:
    """One answered question and where it went."""

    result: Result
    local_dir: Path
    page_url: str | None = None
    commit_url: str | None = None
    #: Why there is no commit, when there is none — said, not inferred.
    not_published: str = ""


def answer(question: str, *, bestand: str | None = None,
           year_from: int | None = None, year_to: int | None = None,
           publish: bool = False, out_dir: Path | None = None,
           **kwargs) -> Answered:
    """:func:`ask`, written locally, and — when asked and switched on —
    committed to the outputs repo. Publishing never fails the answer: the
    local files are there either way, and ``not_published`` says why a
    commit is missing."""
    res = ask(question, bestand=bestand, year_from=year_from, year_to=year_to,
              **kwargs)
    local = write_result(res, out_dir or config.OUTPUTS_DIR / "questions")
    done = Answered(res, local)
    if not publish:
        done.not_published = "nicht angefordert"
        return done
    from utils import publish_github

    if not publish_github.is_enabled():
        done.not_published = "Publizieren ist aus (ENABLE_GITHUB_PUBLISH=false)"
        return done
    done.commit_url = publish_github.publish_question(res.question_id,
                                                      publish_files(res))
    if done.commit_url:
        done.page_url = page_url(res.question_id)
    else:
        done.not_published = "Commit fehlgeschlagen oder schon aktuell (siehe Log)"
    return done


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _short_status(it) -> str:
    r = it.reading
    s = _STATUS_DE.get(r.get("status"), r.get("status") or "")
    if r.get("status") == ev.EDITION:
        s = f"{s} {r.get('model_id')}"
    if it.agreement == "contested":
        s += ", Engines uneinig"
    return s


def format_discord(done: Answered, *, mention: str = "") -> list[str]:
    """The answer as Discord messages, each under the limit.

    A digest, not the page: each claim with its first accepted quote and what
    kind of text the quote is. The page (or, unpublished, the local path) has
    the rest. Pure, so it is tested without a Discord client.
    """
    res = done.result
    head = [f"{mention} 📜 **{_clip(res.question, 200)}**".strip()]
    if not res.complete:
        head.append("⚠️ **Unvollständig** — "
                    + (res.audit.stopped_because
                       or "nicht jede Suche hat geantwortet oder kein Plan vom Modell"))
    if not res.claims:
        head.append("Keine Behauptung liess sich aus den gefundenen Passagen belegen.")

    blocks = []
    for i, c in enumerate(res.claims, 1):
        ok = [b for b in c.bindings if b.ok]
        it = res.pool[ok[0].evidence_id]
        where = _where(it)
        more = f" (+{len(ok) - 1} Beleg(e))" if len(ok) > 1 else ""
        blocks.append(f"**{i}.** {_clip(c.text, 300)}\n"
                      f"> „{_clip(ok[0].quote, 200)}“ — {where} · "
                      f"{_short_status(it)}{more}")

    tail = [(f"Passagen: {len(res.pool)} gefunden, {len(res.sampled)} vorgelegt · "
             f"verworfen: {len(res.rejected_claims)} Behauptung(en) · "
             f"{len(res.audit.calls)} Modellaufruf(e), {res.audit.tokens} Tokens")]
    if done.page_url:
        tail.append(f"Ganze Antwort mit allen Belegen: {done.page_url}")
    elif done.commit_url:
        tail.append(f"Publiziert: {done.commit_url}")
    else:
        tail.append(f"Nicht publiziert ({done.not_published}); lokal unter "
                    f"`{done.local_dir.name}`.")

    messages, current = [], "\n".join(head)
    for block in blocks + ["\n".join(tail)]:
        if len(current) + 2 + len(block) > _DISCORD_LIMIT:
            messages.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    messages.append(current)
    return [m[:_DISCORD_LIMIT] for m in messages]
