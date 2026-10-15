"""adjudicate.py — one reading per line out of three, with the print as a witness.

The candidates for a line are: **A** the line model's reading (it saw the line
crop, and its geometry is the page's), **B** TrOCR's reading of the same crop,
**C** the page model's reading, found by aligning its page text to A's lines,
and **P** the passage of the 1528 print the line corresponds to — which is a
*different witness*, set from a redaction of several protocols, not a reading
of this page. P decides between A, B and C where they differ; it never
replaces what the scribe wrote, and a line that all readings agree on is never
sent to the arbiter at all.

The arbiter is the project's text model (``gpustack_client.chat_text``,
gpt-oss-120b), called once per page with every contested line, answering JSON.
Its answer is checked — it must be about the right line, within a plausible
length of the candidates, not an invention — and a line it does not answer
falls back to A with ``basis="fallback"``. The result records, per line, which
candidates there were, what the print said, who decided and why, so a reviewer
in Transkribus can see a machine's choice for what it is.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from loguru import logger

from ruemlang import textalign

LLMFn = Callable[[str, str], str]      # (system, prompt) -> answer text

LABELS = ("A", "B", "C")


@dataclass
class LineCase:
    index: int
    candidates: dict = field(default_factory=dict)      # label -> text
    print_snippet: Optional[str] = None
    print_score: float = 0.0

    def available(self) -> dict:
        return {k: v for k, v in self.candidates.items() if v and v.strip()}


@dataclass
class Decision:
    index: int
    text: str
    basis: str              # consensus | agree_norm | llm:<label|mixed|print> | fallback | single
    confidence: float
    note: str = ""
    llm_used: bool = False
    candidates: dict = field(default_factory=dict)
    print_snippet: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def deterministic(case: LineCase, primary: str = "A") -> Optional[Decision]:
    """The decisions that need no arbiter."""
    avail = case.available()
    if not avail:
        return Decision(case.index, "", "empty", 0.0, "no reading from any model",
                        candidates=dict(case.candidates), print_snippet=case.print_snippet)
    if len(avail) == 1:
        (label, text), = avail.items()
        return Decision(case.index, _collapse(text), f"single:{label}", 0.5,
                        "only one model read this line",
                        candidates=dict(case.candidates), print_snippet=case.print_snippet)
    texts = {_collapse(t) for t in avail.values()}
    if len(texts) == 1:
        return Decision(case.index, texts.pop(), "consensus", 0.95, "all readings identical",
                        candidates=dict(case.candidates), print_snippet=case.print_snippet)
    norms = {textalign.normalize(t) for t in avail.values()}
    if len(norms) == 1:
        pick = avail.get(primary) or next(iter(avail.values()))
        return Decision(case.index, _collapse(pick), "agree_norm", 0.85,
                        "readings differ only in u/v, i/j/y, diacritics or spacing; "
                        f"line model ({primary}) kept",
                        candidates=dict(case.candidates), print_snippet=case.print_snippet)
    return None


SYSTEM = (
    "Du bist Editionsphilologe für frühneuhochdeutsche Handschriften (deutsche Kurrent, "
    "Bern 1528). Du entscheidest zwischen automatischen Lesungen einer Handschriftenzeile. "
    "Du antwortest ausschließlich mit JSON.")

PROMPT = """Für jede nummerierte Zeile liegen bis zu drei automatische Lesungen derselben Handschriftenzeile vor:
A = Zeilenmodell (sieht den Zeilenausschnitt), B = TrOCR (derselbe Ausschnitt), C = Seitenmodell (liest die ganze Seite, Zeile per Alignment zugeordnet).
P = die entsprechende Stelle im Druck von 1528 (Froschauer, "Handlung oder Acta gehaltner Disputation zuo Bernn"). P ist ein anderer Textzeuge: eine redigierte Druckfassung, nicht diese Handschrift. P kann in Wortlaut, Orthographie und Umfang abweichen oder (score niedrig) falsch zugeordnet sein.

Regeln:
1. Wähle pro Zeile die Lesung, die die Handschrift am wahrscheinlichsten wiedergibt. Bevorzuge A und B bei Buchstabenformen (u/v, i/j/y, Kürzungen, Groß-/Kleinschreibung), weil sie die Zeile gesehen haben; C hilft bei Wortgrenzen und Wortwahl.
2. P nur zum Entscheiden zwischen vorhandenen Varianten und zum Reparieren offensichtlich verstümmelter Wörter heranziehen. Niemals den Druckwortlaut übernehmen, wo alle Lesungen übereinstimmend etwas anderes bieten. Nicht normalisieren, nicht modernisieren, keine Interpunktion ergänzen.
3. Eine Mischlesung (Wörter aus verschiedenen Kandidaten) ist erlaubt, wenn sie zeichenweise aus den Kandidaten stammt.
4. Erfinde nichts. Fehlt für eine Zeile jede brauchbare Lesung, gib die vollständigste Kandidatenlesung zurück und confidence <= 0.3.

Antworte als JSON-Array, ein Objekt pro Zeile, Felder: "i" (Zeilennummer), "text" (gewählte Lesung), "basis" ("A","B","C","mixed" oder "print"), "confidence" (0.0-1.0), "note" (kurz, deutsch, optional).

Zeilen:
{lines}
"""


def _format_cases(cases: list[LineCase]) -> str:
    chunks = []
    for c in cases:
        rows = [f"[{c.index}]"]
        for label in LABELS:
            t = c.candidates.get(label)
            rows.append(f"  {label}: {_collapse(t) if t else '—'}")
        if c.print_snippet:
            rows.append(f"  P (score {c.print_score:.2f}): {_collapse(c.print_snippet)}")
        else:
            rows.append("  P: — (keine Druckstelle gefunden)")
        chunks.append("\n".join(rows))
    return "\n".join(chunks)


_JSON_ARRAY = re.compile(r"\[.*\]", re.S)


def parse_answer(answer: str) -> dict[int, dict]:
    """The arbiter's JSON array as ``{index: item}``; tolerant of prose around it."""
    if not answer:
        return {}
    m = _JSON_ARRAY.search(answer)
    if not m:
        return {}
    try:
        items = json.loads(m.group())
    except json.JSONDecodeError:
        try:
            items = json.loads(m.group().replace("'", '"'))
        except json.JSONDecodeError:
            return {}
    out: dict[int, dict] = {}
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and "i" in it and "text" in it:
            try:
                out[int(it["i"])] = it
            except (TypeError, ValueError):
                continue
    return out


def _plausible(text: str, case: LineCase) -> tuple[bool, str]:
    """Length and character sanity: the answer must look like one of the readings."""
    avail = case.available()
    if not text.strip():
        return False, "empty answer"
    lens = [len(_collapse(t)) for t in avail.values()]
    lo, hi = 0.5 * min(lens), 1.6 * max(lens)
    if not (lo <= len(text) <= hi):
        return False, f"length {len(text)} outside [{lo:.0f}, {hi:.0f}]"
    best = max(textalign.similarity(text, t) for t in avail.values())
    if best < 0.5:
        return False, f"too far from every candidate (best similarity {best:.2f})"
    return True, ""


def arbitrate(cases: list[LineCase], llm: LLMFn, *, primary: str = "A",
              batch: int = 30) -> dict[int, Decision]:
    """LLM decisions for contested lines, in batches of ``batch`` per call."""
    out: dict[int, Decision] = {}
    for k in range(0, len(cases), batch):
        chunk = cases[k:k + batch]
        prompt = PROMPT.format(lines=_format_cases(chunk))
        try:
            answer = llm(SYSTEM, prompt)
        except Exception as exc:                       # noqa: BLE001 — degrade, do not abort the page
            logger.warning(f"[ruemlang] arbiter failed: {type(exc).__name__}: {exc}")
            answer = ""
        parsed = parse_answer(answer)
        for c in chunk:
            item = parsed.get(c.index)
            fallback = _collapse(c.candidates.get(primary) or next(iter(c.available().values())))
            if not item:
                out[c.index] = Decision(c.index, fallback, "fallback", 0.4,
                                        "arbiter gave no answer for this line", llm_used=True,
                                        candidates=dict(c.candidates), print_snippet=c.print_snippet)
                continue
            text = _collapse(str(item.get("text", "")))
            ok, why = _plausible(text, c)
            if not ok:
                out[c.index] = Decision(c.index, fallback, "fallback", 0.4,
                                        f"arbiter answer rejected: {why}", llm_used=True,
                                        candidates=dict(c.candidates), print_snippet=c.print_snippet)
                continue
            basis = str(item.get("basis") or "?")
            try:
                conf = float(item.get("confidence", 0.6))
            except (TypeError, ValueError):
                conf = 0.6
            out[c.index] = Decision(c.index, text, f"llm:{basis}", max(0.0, min(1.0, conf)),
                                    str(item.get("note") or ""), llm_used=True,
                                    candidates=dict(c.candidates), print_snippet=c.print_snippet)
    return out


def decide_page(cases: list[LineCase], llm: Optional[LLMFn], *,
                primary: str = "A", batch: int = 30) -> list[Decision]:
    """Every line decided: deterministically where possible, by the arbiter
    otherwise, by ``primary`` when there is no arbiter."""
    decisions: dict[int, Decision] = {}
    contested: list[LineCase] = []
    for c in cases:
        d = deterministic(c, primary=primary)
        if d is not None:
            decisions[c.index] = d
        else:
            contested.append(c)
    if contested:
        if llm is None:
            for c in contested:
                pick = c.candidates.get(primary) or next(iter(c.available().values()))
                decisions[c.index] = Decision(c.index, _collapse(pick), f"no_llm:{primary}", 0.5,
                                              "contested, no arbiter configured",
                                              candidates=dict(c.candidates),
                                              print_snippet=c.print_snippet)
        else:
            decisions.update(arbitrate(contested, llm, primary=primary, batch=batch))
    return [decisions[c.index] for c in cases]


def build_cases(line_texts: list[str], trocr_texts: list[Optional[str]],
                page_text: str, reference: Optional[textalign.Reference],
                page_hint: Optional[tuple[int, int]] = None) -> tuple[list[LineCase], dict]:
    """Cases for one page from the three readings and the print.

    ``line_texts`` are A in geometry order; ``trocr_texts`` B, already paired to
    the same lines (same segmentation, so same order and count — a mismatch is
    aligned by content); ``page_text`` is C's whole page.
    """
    n = len(line_texts)
    if len(trocr_texts) != n:
        anchor = line_texts if any(line_texts) else [t or "" for t in trocr_texts]
        pairing = textalign.align_line_lists(anchor, [t or "" for t in trocr_texts]) \
            if any(line_texts) else list(range(min(n, len(trocr_texts)))) + [None] * max(0, n - len(trocr_texts))
        trocr_texts = [trocr_texts[j] if j is not None else None for j in pairing]
    # The text each line is known by: A's reading, or B's where A has none.
    # Aligning C against empty strings pairs nothing (the first run on tei,
    # 15.10. 14:28: 117 lines, every one of them "single").
    known = [(line_texts[i] or trocr_texts[i] or "") for i in range(n)]
    c_lines = textalign.split_page_text(page_text)
    pairing = textalign.align_line_lists(known, c_lines) if c_lines and any(known) else [None] * n
    page_c = [c_lines[j] if j is not None else None for j in pairing]

    passage = None
    if reference is not None:
        probe = "\n".join(t for t in known if t) or page_text
        passage = reference.locate(probe, pages=page_hint)
    cases: list[LineCase] = []
    for i in range(n):
        cands = {"A": line_texts[i] or "", "B": trocr_texts[i] or "", "C": page_c[i] or ""}
        snippet, score = (None, 0.0)
        if passage is not None:
            probe_line = known[i] or page_c[i] or ""
            snippet, score = textalign.Reference.snippet(probe_line, passage)
        cases.append(LineCase(index=i, candidates=cands, print_snippet=snippet, print_score=score))
    meta = {"passage": None if passage is None else {
        "start": passage.start, "end": passage.end, "score": round(passage.score, 3),
        "weak": passage.weak, "pages": passage.pages,
        "text": passage.text if not passage.weak else f"<anchor window, {len(passage.text)} chars>"},
        "page_hint": page_hint, "page_model_lines": len(c_lines),
        "page_model_paired": sum(1 for p in page_c if p),
        "lines_with_print": sum(1 for c in cases if c.print_snippet)}
    return cases, meta


def default_llm(system: str, prompt: str) -> str:
    """The project's text model through GPUStack — gpt-oss-120b by default."""
    from utils import gpustack_client as gs
    import config
    budget = int(getattr(config, "RUEMLANG_ARBITER_MAX_TOKENS", 12000))
    return gs.chat_text(prompt, system=system, max_tokens=budget, agent_name="ruemlang")
