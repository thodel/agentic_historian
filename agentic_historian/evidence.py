"""
evidence.py — an evidence unit that says which reading it quotes (#811).

A quotation from a 15th-century page is only as good as the reading it comes
from. On BAT_664 the published transcription begins *"Iuser femitlich geñs"*;
TrOCR's escriptmask model, on the same page, reads *"unser frùntlich gruͦs"*. A
system that cites the first as "the source says" cites a recogniser, not the
source. SPIRE (arXiv:2605.30947, docs/SPIRE_COMPARISON.md) binds every claim to
an evidence id, but it works on edited text and can assume the text is right.
Here that is the open question, so the evidence unit carries the answer to it:

* **which reading** — ``engine/model_id`` is part of the evidence id, because
  character offsets only mean something inside one text;
* **what a person did with that reading** — :data:`STATUSES`, derived from the
  Gate-2 record, never guessed;
* **whether the engines agree on this span** — not on the page, on the words
  quoted (:func:`span_agreement`);
* **the page's band** — merge or no-merge (``ENSEMBLE_NO_MERGE_CER``), or
  unmeasurable when fewer than two readings are usable;
* **the source** — Agent B's ``Datierung`` (with its ``unsicher`` flag) and
  ``Sprache`` as a set, and the holding.

Rules, each of them a refusal rather than a repair:

* No item without a verbatim quote. If the text is not in the named reading at
  the named offsets, :func:`item_for_span` returns ``None``; there is no fuzzy
  fallback. :func:`locate_quote` may *find* a quote through the orthographic
  normalisation of ``ruemlang/textalign.py`` (u/v, i/j/y, diacritics), but the
  item always carries the reading's own characters, and ``match`` says how it
  was found.
* A historian's choice is the closest available reading, not ground truth
  (*Quality without ground truth*, README). ``historian_chosen`` says what
  happened at Gate 2; it does not say the reading is right.
* Nothing here reads the preference log. That log holds comparisons and no text
  (#332), and stays that way; the text comes from the run state and the
  published ``pipeline.json``.
* A quote is the original language. Translation is not this module's business.

What a reading's status can be, strongest editorial fact first:

==================== =========================================================
``ground_truth``     a reference transcription the caller supplies (imported
                     GT, e.g. Transkribus or OAI); never derived from a run
``historian_chosen`` the historian chose exactly this reading at Gate 2
``historian_combined`` the historian chose to combine several readings: the
                     fused text, and each reading that went into it
``auto_selected``    the pipeline's automatic pick, no human decision
``auto_fused``       the pipeline's fused text, no human decision
``not_chosen``       offered at Gate 2 and not chosen (or every reading rejected)
``uncorrected``      nobody chose or declined it
==================== =========================================================
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

import config

__all__ = [
    "COMBINED",
    "EDITION",
    "PUBLISHED",
    "STATUSES",
    "EvidenceItem",
    "Reading",
    "ReadingSet",
    "edition_item",
    "evidence_id",
    "item_for_span",
    "item_from_passage_row",
    "locate_quote",
    "reading_set",
    "span_agreement",
]

GROUND_TRUTH = "ground_truth"
HISTORIAN_CHOSEN = "historian_chosen"
HISTORIAN_COMBINED = "historian_combined"
AUTO_SELECTED = "auto_selected"
AUTO_FUSED = "auto_fused"
NOT_CHOSEN = "not_chosen"
UNCORRECTED = "uncorrected"
#: Text quoted from another project's edition through the MCP federation
#: (#812): an editor's transcription, not one of our readings. It has no Gate 2,
#: no candidates and no band; what it has is somebody else's editorial
#: responsibility, which is why it is its own status and not ``ground_truth``.
EDITION = "edition"

#: Strongest editorial fact first; see the module docstring. ``edition`` stands
#: apart: it is not a reading this pipeline produced.
STATUSES = (GROUND_TRUTH, HISTORIAN_CHOSEN, HISTORIAN_COMBINED, AUTO_SELECTED,
            AUTO_FUSED, NOT_CHOSEN, UNCORRECTED, EDITION)

#: The reading key of the published transcription (``pipeline.json``'s
#: ``transcription``, one page's section of it). Not an engine: it is derived
#: from the candidates, so it is never counted as corroborating them.
PUBLISHED = "pipeline/published"

#: The reading key of a Gate-2 combination (``RunState.closest_reading`` with
#: ``combined``). Derived as well.
COMBINED = "gate2/combined"

_DERIVED_ENGINES = {"pipeline", "gate2"}

# ``--- <page> ---`` before each page of a grouped order (orchestrator
# ``_ensemble_pass``). A single-document run writes none. ``--- SEITE N ---`` is
# not one of them: the VLM prompt asks the model to separate pages that way, so
# it appears *inside* a page's text — saa-0428 carries it within the section of
# ``e-codices_saa-0428_001r_large.jpg`` — and splitting on it would cut a page in
# two and attribute its second half to a page that does not exist.
_PAGE_HEADER = re.compile(r"^--- (?!SEITE \d+ ---)(.+?) ---[ \t]*$", re.MULTILINE)

_TOKEN = re.compile(r"\S+")


# ── readings ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Reading:
    """One text of one page, and what a person did with it."""

    engine: str
    model_id: str
    text: str
    status: str = UNCORRECTED
    #: Where this text starts inside the published transcription, for the
    #: published reading only. It is what makes a catalogue anchor possible:
    #: ``passage-<start>-<end>`` (#397) counts in the whole transcription.
    base_offset: int | None = None
    #: Why this reading cannot be a reading of the page at all (wrong script,
    #: no alphabet, impossible length — ``ensemble.implausible_reading``).
    #: Such a reading stays visible and is kept out of every comparison.
    set_aside: str = ""

    @property
    def key(self) -> str:
        return f"{self.engine}/{self.model_id}" if self.model_id else self.engine

    @property
    def derived(self) -> bool:
        return self.engine in _DERIVED_ENGINES

    def public(self) -> dict:
        out = {"engine": self.engine, "model_id": self.model_id,
               "status": self.status}
        if self.set_aside:
            out["set_aside"] = self.set_aside
        return out


@dataclass
class ReadingSet:
    """Every reading of one page that this run can account for.

    ``unattributed`` counts candidates that exist but cannot be assigned to a
    page: today's ``pipeline.json`` does not serialise ``RecognitionResult.page``,
    so a multi-page order read from it alone has candidates and no pages for
    them. They are counted, not guessed into a page.
    """

    doc_id: str
    page: str
    readings: list[Reading] = field(default_factory=list)
    band: str = "unmeasurable"          # "merge" | "no_merge" | "unmeasurable"
    max_pairwise_cer: float | None = None
    source: dict = field(default_factory=dict)
    unattributed: int = 0

    def get(self, key: str) -> Reading | None:
        for r in self.readings:
            if r.key == key:
                return r
        return None

    def candidates(self) -> list[Reading]:
        """Engine readings that may be compared: not derived, not set aside."""
        return [r for r in self.readings if not r.derived and not r.set_aside]


# ── the evidence item ────────────────────────────────────────────────────────

@dataclass
class EvidenceItem:
    evidence_id: str
    doc_id: str
    page: str
    reading: dict
    char_start: int
    char_end: int
    quote: str
    match: str                          # "exact" | "normalised"
    agreement: str                      # "agreed" | "contested" | "single"
    alternatives: list[dict]
    identical_readings: list[str]
    page_band: str
    max_pairwise_cer: float | None
    source: dict
    anchor: str | None = None
    catalogue_url: str | None = None
    #: Filled by the consumer (#812): sub-question, tier, score.
    retrieval: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def evidence_id(doc_id: str, page: str, reading_key: str,
                start: int, end: int) -> str:
    """A short id that is the same for the same place in the same reading.

    Short because a model has to copy it when it binds a claim (#812); stable
    because it is a hash of exactly the fields that name the place, so a rebuild
    yields the same id and a different reading of the same words does not.
    """
    raw = json.dumps([doc_id, page, reading_key, int(start), int(end)],
                     ensure_ascii=False, separators=(",", ":"))
    return "ev-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


# ── building a reading set ───────────────────────────────────────────────────

def _published_sections(text: str) -> list[tuple[str, int, int]]:
    """``(page, start, end)`` of each page in a published transcription.

    No headers → one section named ``""``: a single-document run.
    """
    text = text or ""
    heads = list(_PAGE_HEADER.finditer(text))
    if not heads:
        return [("", 0, len(text))] if text.strip() else []
    out = []
    for i, m in enumerate(heads):
        start = m.end() + 1 if text[m.end():m.end() + 1] == "\n" else m.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        # the separator line between pages belongs to neither
        while end > start and text[end - 1] == "\n":
            end -= 1
        out.append((m.group(1), start, end))
    return out


def _source_meta(doc_id: str, pipeline: dict | None) -> dict:
    """Agent B's date and languages, and the holding — what a period check and
    a balanced sample need (#812). Missing fields are ``None``, never invented."""
    sj = (((pipeline or {}).get("description") or {}).get("source_json") or {})

    def _elem(name):
        v = sj.get(name)
        if isinstance(v, dict):
            return v.get("wert") or None, v.get("unsicher")
        return (str(v) if v else None), None

    datierung, unsicher = _elem("Datierung")
    sprache, _ = _elem("Sprache")
    from agent_a.model_selector import normalise_langs
    from agents.entity_agent import bestand_from_doc_id

    return {"datierung": datierung, "datierung_unsicher": unsicher,
            "sprache": sprache,
            "sprachen": normalise_langs(sprache) if sprache else [],
            "bestand": bestand_from_doc_id(doc_id)}


def _label_page(label: str) -> str:
    return label.split(":", 1)[0] if ":" in label else ""


def _label_key(label: str) -> str:
    return label.split(":", 1)[1] if ":" in label else label


def _field(r, name):
    return (r.get(name) if isinstance(r, dict) else getattr(r, name, None))


def _gate2_for_page(state, page: str, pages_known: set[str]):
    """``(chosen_keys, combined, rejected, closest_text, auto_key)`` for a page.

    Gate-2 labels are ``<page>:<engine>/<model>`` (#313), or a bare key on an
    older record. A bare label belongs to the page only when the document has a
    single page — otherwise it cannot be attributed and is not used.
    """
    if state is None:
        return [], False, False, None, None
    gate = getattr(state, "gate_decisions", {}) or {}
    single = len(pages_known) <= 1

    def _on_page(label: str) -> bool:
        lp = _label_page(label)
        return lp == page or (not lp and single) or (not page and single)

    cr = getattr(state, "closest_reading", None) or {}
    chosen = [_label_key(c) for c in (cr.get("chosen") or []) if _on_page(c)]
    combined = bool(cr.get("combined")) and bool(chosen)
    closest_text = str(cr.get("text")) if (chosen and cr.get("text")) else None
    paths = (getattr(state, "artifacts", {}) or {}).get("paths") or {}
    rejected = bool(gate.get("gate2_rejected")) and any(_on_page(p) for p in paths)
    auto_all = gate.get("gate2_auto") or {}
    auto_label = auto_all.get(page or "_") or (
        next(iter(auto_all.values())) if (single and len(auto_all) == 1) else None)
    auto_key = _label_key(auto_label) if auto_label else None
    return chosen, combined, rejected, closest_text, auto_key


def reading_set(doc_id: str, page: str = "", *, state=None,
                pipeline: dict | None = None,
                ground_truth: dict[str, str] | None = None) -> ReadingSet:
    """Every reading of ``page`` that the run state and ``pipeline.json`` hold.

    ``state`` is the document's ``RunState`` (or ``None``): it has the
    candidates *with* their page (``artifacts["recognitions"]``, and the Gate-2
    ``paths`` of no-merge pages), the automatic pick and the historian's
    decision. ``pipeline`` is the published ``pipeline.json``: the published
    transcription, Agent B's description and — without pages — the candidates.
    ``ground_truth`` maps a reading key to a reference transcription of this
    page that the caller has imported; it is the only way a reading becomes
    ``ground_truth``.

    For a single-page document ``page`` may be left empty: the image name the
    Gate-2 labels carry and the absent header of the published text then refer
    to the same page.
    """
    pipeline = pipeline or {}
    artifacts = (getattr(state, "artifacts", {}) or {}) if state is not None else {}

    # ── candidates, with the page they belong to ─────────────────────────────
    def _from_recognitions(recs) -> list[tuple[str, str, str, str]]:
        return [(_field(r, "page") or "", _field(r, "engine") or "",
                 _field(r, "model_id") or "", _field(r, "text") or "")
                for r in recs or [] if not _field(r, "error")]

    # (page, engine, model, text). The run state knows pages; pipeline.json is
    # the fallback when there is no state, and does not.
    raw = _from_recognitions(artifacts.get("recognitions"))
    for label, text in (artifacts.get("paths") or {}).items():
        eng, _, mid = _label_key(label).partition("/")
        raw.append((_label_page(label), eng, mid, text or ""))
    if not raw:
        raw = _from_recognitions(pipeline.get("recognitions"))
    raw = [x for x in raw if x[1] and x[3].strip()]

    sections = _published_sections(pipeline.get("transcription") or
                                   artifacts.get("transcription") or "")
    pages_known = {p for p, *_ in raw if p} | {p for p, *_ in sections if p}
    single = len(pages_known) <= 1

    def _belongs(p: str) -> bool:
        return p == page or (single and (not p or not page))

    unattributed = 0
    cands: dict[str, str] = {}
    for p, eng, mid, text in raw:
        if _belongs(p):
            cands.setdefault(f"{eng}/{mid}" if mid else eng, text)
        elif not p:
            unattributed += 1

    chosen, combined, rejected, closest_text, auto_key = _gate2_for_page(
        state, page, pages_known)
    decided = bool(chosen) or rejected

    # ── statuses ─────────────────────────────────────────────────────────────
    readings: list[Reading] = []
    published = next(((p, s, e) for p, s, e in sections if _belongs(p)), None)
    pub_text = None
    if published is not None:
        full = pipeline.get("transcription") or artifacts.get("transcription") or ""
        pub_text = full[published[1]:published[2]]

    for key, text in cands.items():
        if key in chosen:
            status = HISTORIAN_COMBINED if combined else HISTORIAN_CHOSEN
        elif decided:
            status = NOT_CHOSEN
        elif key == auto_key or (auto_key is None and pub_text is not None
                                 and text == pub_text):
            status = AUTO_SELECTED
        else:
            status = UNCORRECTED
        eng, _, mid = key.partition("/")
        readings.append(Reading(eng, mid, text, status))

    if combined and closest_text:
        readings.append(Reading("gate2", "combined", closest_text,
                                HISTORIAN_COMBINED))
    if pub_text is not None and pub_text.strip():
        engine_texts = [r.text for r in readings if not r.derived]
        if closest_text and pub_text == closest_text:
            status = HISTORIAN_COMBINED if combined else HISTORIAN_CHOSEN
        elif pub_text in engine_texts:
            status = AUTO_SELECTED
        elif engine_texts:
            status = AUTO_FUSED
        else:
            # No candidate of this page is known (saa-0428 from pipeline.json
            # alone), so whether the pipeline picked or fused cannot be said.
            status = UNCORRECTED
        readings.append(Reading("pipeline", "published", pub_text, status,
                                base_offset=published[1]))
    for key, text in (ground_truth or {}).items():
        eng, _, mid = key.partition("/")
        readings.append(Reading(eng or "gt", mid, text, GROUND_TRUTH))

    source = _source_meta(doc_id, pipeline)
    readings = _set_aside(readings, source.get("sprachen") or [])
    band, spread = _band(state, page, readings, single)
    return ReadingSet(doc_id=doc_id, page=page, readings=readings, band=band,
                      max_pairwise_cer=spread, source=source,
                      unattributed=unattributed)


def _set_aside(readings: list[Reading], langs: list[str]) -> list[Reading]:
    """Mark engine readings that cannot be readings of this page (#358, #421)."""
    from agent_a.ensemble import implausible_reading

    texts = [r.text for r in readings if not r.derived]
    lang = langs[0] if langs else None
    out = []
    for r in readings:
        why = "" if (r.derived or r.status == GROUND_TRUTH) else \
            implausible_reading(r.text, texts, lang)
        out.append(Reading(r.engine, r.model_id, r.text, r.status,
                           r.base_offset, why) if why else r)
    return out


def _band(state, page: str, readings: list[Reading],
          single: bool) -> tuple[str, float | None]:
    """The page's band, from the recorded spread when there is one (#332's
    context is what the selector actually saw), else measured the way
    ``ensemble._max_pairwise_cer`` measures it."""
    usable = [r.text for r in readings if not r.derived and not r.set_aside
              and r.status != GROUND_TRUTH]
    spread = None
    if state is not None:
        ctx_all = (getattr(state, "gate_decisions", {}) or {}).get("gate2_context") or {}
        ctx = ctx_all.get(page or "_") or (
            next(iter(ctx_all.values())) if (single and len(ctx_all) == 1) else None)
        if ctx and ctx.get("max_pairwise_cer") is not None:
            spread = float(ctx["max_pairwise_cer"])
    if spread is None:
        if len(usable) < 2:
            return "unmeasurable", None
        from eval.metrics import cer
        spread = 0.0
        for i, a in enumerate(usable):
            for b in usable[i + 1:]:
                spread = max(spread, cer(a, b), cer(b, a))
    band = "no_merge" if spread > config.ENSEMBLE_NO_MERGE_CER else "merge"
    return band, round(spread, 4)


# ── agreement over a span ────────────────────────────────────────────────────

def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def _aligned(src_text: str, start: int, end: int,
             other_text: str) -> tuple[str, str] | None:
    """``(source span widened to whole tokens, the other reading's text there)``.

    Token alignment as in ``path_compare.compute_disagreements``, but with the
    offsets kept, so the other reading's text is cut from the other reading
    rather than rebuilt from tokens. A span that cuts a word is widened to the
    whole word: readings disagree in words, not in half-words.

    Inside a *replaced* block the two readings share no token, so there is no
    word-to-word correspondence to follow. The span's share of the block is cut
    proportionally from the other side — on BAT_664 a garbage reading is one
    replaced block the length of the page, and taking it whole made the
    "alternative" to three words the entire page. The proportional slice is
    enough to see *that* and *roughly how* the readings differ; it is not a
    claim about which word corresponds to which. ``None`` when the span holds
    no token.
    """
    src = _tokens(src_text)
    idx = [i for i, (_, s, e) in enumerate(src) if s < end and e > start]
    if not idx:
        return None
    first, last = idx[0], idx[-1]
    oth = _tokens(other_text)
    sm = SequenceMatcher(None, [t for t, _, _ in src], [t for t, _, _ in oth],
                         autojunk=False)
    hit: list[int] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            hit.extend(j1 + k - i1 for k in range(max(i1, first), min(i2, last + 1)))
        elif tag == "replace" and i1 <= last and i2 > first:
            n_src, n_oth = i2 - i1, j2 - j1
            lo = max(first, i1) - i1
            hi = min(last + 1, i2) - i1
            o_lo = j1 + (lo * n_oth) // n_src
            o_hi = j1 + -(-hi * n_oth // n_src)                 # ceil
            hit.extend(range(o_lo, max(o_hi, o_lo + 1)))
        elif tag == "insert" and first < i1 <= last:
            hit.extend(range(j1, j2))
    span = src_text[src[first][1]:src[last][2]]
    if not hit:
        return span, ""
    lo, hi = min(hit), max(hit)
    if (hi - lo + 1) <= (last - first + 1) + _WINDOW_SLACK:
        return span, other_text[oth[lo][1]:oth[hi][2]]
    return span, _narrow(span, other_text, oth[lo:hi + 1])


#: How many tokens wider than the span an aligned window may be before it is
#: narrowed. A short common word ("so", "da", "vnd") is enough for the matcher
#: to anchor on, and the block before it is then lopsided: on BAT_664 three
#: words of one reading faced 21 of another.
_WINDOW_SLACK = 2


def _narrow(span: str, other_text: str, window: list[tuple[str, int, int]]) -> str:
    """The stretch of ``window`` that best matches ``span``, on token bounds.

    ``rapidfuzz``'s partial alignment, as ``ruemlang/textalign.py`` uses it, but
    only inside the window the token alignment allowed — never the whole page,
    where a repeated formula would match somewhere else.
    """
    from rapidfuzz import fuzz

    w_start, w_end = window[0][1], window[-1][2]
    text = other_text[w_start:w_end]
    al = fuzz.partial_ratio_alignment(span, text)
    if al is None or al.dest_end <= al.dest_start:
        return text
    s, e = w_start + al.dest_start, w_start + al.dest_end
    toks = [t for t in window if t[2] > s and t[1] < e] or window
    return other_text[toks[0][1]:toks[-1][2]]


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def span_agreement(rs: ReadingSet, reading_key: str, start: int,
                   end: int) -> tuple[str, list[dict], list[str]]:
    """``(agreement, alternatives, identical_readings)`` for one span.

    Compared against every engine reading of the page except the quoted one,
    derived texts and set-aside readings. A reading whose **whole text** is
    identical to the quoted one is listed in ``identical_readings`` and does not
    count as agreement: on BAT_664 ``kraken-early_modern_german`` and
    ``kraken-early-modern-german`` are byte-identical — one model under two ids,
    or the published copy of a candidate — and a reading cannot corroborate
    itself.

    * ``contested`` — at least one other reading reads the span differently
      (an empty alternative means it has nothing there);
    * ``agreed`` — at least one *independent* reading, and all of them read the
      span the same, whitespace aside;
    * ``single`` — nothing independent to compare with. Not agreement (#367).
    """
    src = rs.get(reading_key)
    if src is None:
        return "single", [], []
    alternatives: list[dict] = []
    identical: list[str] = []
    independent = 0
    for other in rs.candidates():
        if other.key == src.key:
            continue
        if other.text == src.text:
            identical.append(other.key)
            continue
        got = _aligned(src.text, start, end, other.text)
        if got is None:
            continue
        span, alt = got
        independent += 1
        if not _same(span, alt):
            alternatives.append({"engine": other.engine, "model_id": other.model_id,
                                 "status": other.status, "text": alt})
    if independent == 0:
        return "single", [], identical
    return ("contested" if alternatives else "agreed"), alternatives, identical


# ── items ────────────────────────────────────────────────────────────────────

def item_for_span(rs: ReadingSet, reading_key: str, start: int, end: int, *,
                  match: str = "exact") -> EvidenceItem | None:
    """The evidence item for ``[start, end)`` of one reading, or ``None``.

    ``None`` when the reading does not exist, the offsets fall outside it, or
    the span is blank — there is then nothing anybody could point at (#466).
    """
    r = rs.get(reading_key)
    if r is None or start < 0 or end > len(r.text) or end <= start:
        return None
    quote = r.text[start:end]
    if not quote.strip():
        return None
    agreement, alternatives, identical = span_agreement(rs, reading_key, start, end)
    anchor = url = None
    if r.base_offset is not None:
        from passage_find import anchor_for, catalogue_url
        anchor = anchor_for({"char_start": r.base_offset + start,
                             "char_end": r.base_offset + end})
        url = catalogue_url(rs.doc_id, anchor=anchor)
    return EvidenceItem(
        evidence_id=evidence_id(rs.doc_id, rs.page, r.key, start, end),
        doc_id=rs.doc_id, page=rs.page, reading=r.public(),
        char_start=start, char_end=end, quote=quote, match=match,
        agreement=agreement, alternatives=alternatives,
        identical_readings=identical, page_band=rs.band,
        max_pairwise_cer=rs.max_pairwise_cer, source=dict(rs.source),
        anchor=anchor, catalogue_url=url)


def _occurrences(haystack: str, needle: str) -> list[int]:
    out, i = [], haystack.find(needle)
    while i != -1 and needle:
        out.append(i)
        i = haystack.find(needle, i + 1)
    return out


def locate_quote(rs: ReadingSet, quote: str) -> list[EvidenceItem]:
    """Every place in every reading of the page where ``quote`` stands.

    Exact first. Only in a reading where it does not stand verbatim is the
    orthographic normalisation of ``ruemlang/textalign.py`` tried — u/v, i/j/y,
    ß/ss, diacritics, case and punctuation folded — and the item then carries
    that reading's own characters, with ``match="normalised"``. Every
    occurrence is returned: formulae repeat on a page, and two places are two
    pieces of evidence. Set-aside readings are not searched.
    """
    quote = quote or ""
    if not quote.strip():
        return []
    items: list[EvidenceItem] = []
    for r in rs.readings:
        if r.set_aside:
            continue
        spans = [(i, i + len(quote)) for i in _occurrences(r.text, quote)
                 if not _cuts_letter(r.text, i, i + len(quote))]
        how = "exact"
        if not spans:
            spans = _normalised_spans(r.text, quote)
            how = "normalised"
        for s, e in spans:
            it = item_for_span(rs, r.key, s, e, match=how)
            if it is not None:
                items.append(it)
    return items


def _normalised_spans(text: str, quote: str) -> list[tuple[int, int]]:
    from ruemlang.textalign import normalize, normalize_with_map
    needle = normalize(quote)
    if not needle:
        return []
    hay, idx = normalize_with_map(text)
    out = []
    for i in _occurrences(hay, needle):
        end = idx[i + len(needle) - 1] + 1
        while end < len(text) and unicodedata.combining(text[end]):
            end += 1                                   # the mark belongs to the letter
        out.append((idx[i], end))
    return out


def _cuts_letter(text: str, start: int, end: int) -> bool:
    """True when ``[start, end)`` separates a letter from its combining mark.

    The readings write many abbreviation marks as combining characters — BAT_664
    has ``gen̄s`` as ``n`` + U+0303 — so an exact ``"gen"`` occurs inside it, and
    is not what the page says. Such a hit is left to the normalised search,
    which finds it and keeps the mark.
    """
    return ((end < len(text) and unicodedata.combining(text[end]) != 0)
            or (0 < start < len(text) and unicodedata.combining(text[start]) != 0))


def item_from_passage_row(row: dict, *, pipeline: dict, state=None,
                          widen_lines: int = 0) -> EvidenceItem | None:
    """A ``passage_index`` row (#395) read as an evidence item.

    The index counts offsets in the published transcription, so the row maps to
    the published reading of the page whose section holds it. ``None`` when the
    row no longer matches that text — an index older than the transcription it
    points into — rather than an item quoting what the page no longer says.

    A row is one mention — *almosen*, *spital* — which locates a passage but
    is not one. ``widen_lines`` takes the line holding the mention and that many
    lines on each side, inside the same page, so an argument (#812) quotes what
    the page says about the word rather than the word.
    """
    start, end = row.get("char_start"), row.get("char_end")
    doc_id = row.get("doc_id") or pipeline.get("doc_id") or ""
    full = pipeline.get("transcription") or ""
    if start is None or end is None or not full:
        return None
    if row.get("text") and full[start:end] != row["text"]:
        return None
    for page, s, e in _published_sections(full):
        if s <= start and end <= e:
            rs = reading_set(doc_id, page, state=state, pipeline=pipeline)
            a, b = start - s, end - s
            if widen_lines > 0:
                a, b = _widen_to_lines(full[s:e], a, b, widen_lines)
            return item_for_span(rs, PUBLISHED, a, b)
    return None


def _widen_to_lines(text: str, start: int, end: int, n: int) -> tuple[int, int]:
    """``[start, end)`` grown to whole lines, plus ``n`` lines on each side."""
    a = text.rfind("\n", 0, start) + 1
    for _ in range(n):
        if a == 0:
            break
        a = text.rfind("\n", 0, a - 1) + 1
    b = text.find("\n", end)
    b = len(text) if b == -1 else b
    for _ in range(n):
        if b >= len(text):
            break
        nxt = text.find("\n", b + 1)
        b = len(text) if nxt == -1 else nxt
    # trailing blank lines are not part of the passage
    while b > a and text[b - 1] in "\n \t":
        b -= 1
    return a, b


def edition_item(source: str, doc_id: str, text: str, *,
                 title: str | None = None, url: str | None = None,
                 datierung: str | None = None) -> EvidenceItem | None:
    """An evidence item for text an edition returned through the federation.

    The whole ``text`` is the quote — it is what the source said, and there is
    no reading of ours to cut it from. The id names the source, so the same
    snippet from two editions is two pieces of evidence. ``agreement`` is
    ``single`` and ``page_band`` ``unmeasurable``: nothing was compared, and
    saying anything else would claim a check that did not happen.
    """
    text = text or ""
    if not text.strip() or not source:
        return None
    key = f"edition/{source}"
    return EvidenceItem(
        evidence_id=evidence_id(f"{source}:{doc_id}", "", key, 0, len(text)),
        doc_id=doc_id, page="",
        reading={"engine": "edition", "model_id": source, "status": EDITION},
        char_start=0, char_end=len(text), quote=text, match="exact",
        agreement="single", alternatives=[], identical_readings=[],
        page_band="unmeasurable", max_pairwise_cer=None,
        source={"datierung": datierung, "datierung_unsicher": None,
                "sprache": None, "sprachen": [], "bestand": source,
                "title": title},
        anchor=None, catalogue_url=url)
