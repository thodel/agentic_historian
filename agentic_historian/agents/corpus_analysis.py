"""
agents/corpus_analysis.py — Agent D: Korpus-Analyse
Statistiken, Topic Modelling, Soziale Taxonomien, Care-Analyse, Voyant Link.

Fixes AH-38:
- Removed 5,000-char truncation: analysis runs on full corpus text
  (or 30k-char chunks if >100k chars, aggregated across chunks for LLM calls)
- total_tokens → total_words (was word count, not token count; renamed for clarity)
"""

import json
from pathlib import Path
from typing import Optional

import requests

from loguru import logger

import config
from utils import gpustack_client as gs
from utils import llm_json

SYSTEM = (
    "Du bist ein Digital Humanities Forscher, spezialisiert auf korpusbasierte "
    "Analyse spätmittelalterlicher Verwaltungsquellen (Schweiz, 14.–16. Jh.)."
)

LLM_CHUNK_SIZE = 30_000


def analyse_corpus(corpus_name: str, doc_ids: Optional[list[str]] = None) -> dict:
    """Korpusanalyse. Nutzt alle Transkriptionen wenn doc_ids None."""
    if doc_ids is None:
        doc_ids = [p.stem for p in config.TRANSCRIPTIONS_DIR.glob("*.txt")]

    transcriptions = {}
    for doc_id in doc_ids:
        path = config.TRANSCRIPTIONS_DIR / f"{doc_id}.txt"
        if path.exists():
            transcriptions[doc_id] = path.read_text(encoding="utf-8")

    if not transcriptions:
        logger.warning(f"[Agent D] Keine Transkriptionen für '{corpus_name}' gefunden.")
        return {}

    combined = "\n\n---\n\n".join(transcriptions.values())

    stats = _stats(combined)
    topics = _topics_large(combined)
    taxonomy = _taxonomy_large(combined)
    care = _care_analysis_large(combined)

    result = {
        "corpus_name": corpus_name,
        "doc_count": len(transcriptions),
        "total_chars": len(combined),
        "stats": stats,
        "topics": topics,
        "taxonomy": taxonomy,
        "care_analysis": care,
        # SEC-12 (#583): only upload the corpus text to Voyant (a third-party
        # service, via a publicly shareable link) when the operator has explicitly
        # enabled it for corpora cleared to publish. Off by default on an open
        # server so unpublished/licensed transcriptions do not leak.
        "voyant_url": _voyant_url(combined, corpus_name) if config.ENABLE_VOYANT_UPLOAD else "",
    }

    _save(corpus_name, result)
    logger.info(f"[Agent D] Fertig: {corpus_name}")
    return result


# ── Statistics ────────────────────────────────────────────────────────────────

def _stats(text: str) -> dict:
    words = text.split()
    word_freq = {}
    for w in words:
        w_clean = w.lower().strip(".,;:'\"§$%()[]{}–—")
        if w_clean and len(w_clean) > 2 and not w_clean.isdigit():
            word_freq[w_clean] = word_freq.get(w_clean, 0) + 1

    # EFnhd stopwords — high-frequency function words in Frühneuhochdeutsch.
    # Covers: articles, conjunctions, prepositions, pronouns, auxiliaries.
    # Excludes modern-only words (e.g. "vnd" is Middle High German variant of "und").
    # Source: based on D. B. H. J. stopword lists for EFnhd corpora; verified against
    # the most frequent tokens in Königsfelden register texts that carry no
    # semantic content for entity/frequency analysis.
    stopwords = {
        # Articles
        "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer",
        "einem", "einen",
        # Conjunctions
        "und", "oder", "aber", "sondern", "auch", "weder", "noch",
        # Prepositions
        "in", "von", "mit", "zu", "für", "auf", "aus", "nach", "bei",
        "an", "unter", "über", "durch", "vor", "hinter", "zwischen",
        "ohne", "um", "gegen", "seit", "bis",
        # Pronouns (personal + demonstrative)
        "ich", "du", "er", "sie", "es", "wir", "ihr", "mich", "dich",
        "sich", "uns", "euch", "mir", "dir", "ihm", "ihn", "ihr",
        "dieser", "diese", "dieses", "deren", "dessen", "derer",
        "der", "die", "das", "was", "wer", "wen", "wem", "wessen",
        # Auxiliaries + copula
        "ist", "sind", "war", "waren", "wird", "werden", "wurde",
        "wurden", "sein", "seine", "seiner", "hat", "haben", "hatte",
        "hatten", "kann", "kann", "können", "konnte", "muss", "müssen",
        "musste", "soll", "sollen", "sollte", "will", "wollen", "wollte",
        # Frequent adverbs / particles
        "nicht", "noch", "schon", "nur", "ja", "nein", "doch", "also",
        "so", "dann", "als", "wie", "wenn", "weil", "damit", "damit",
        # "vnd" = Middle High German spelling of "und"; keep it in the list
        "vnd",
        # Latinate filler common in early modern docs
        "item", "namm", "nam", "uff", "uff", "dann",
    }
    filtered = {w: c for w, c in word_freq.items() if w not in stopwords}
    top_words = sorted(filtered.items(), key=lambda x: -x[1])[:30]

    return {
        "total_words": len(words),
        "total_unique_words": len(word_freq),
        "total_chars": len(text),
        "top_words": dict(top_words),
    }


# ── Chunking (fixes 5k-char truncation) ─────────────────────────────────────

#: How far consecutive chunks overlap, so a finding on a seam is not lost. The
#: merge removes the duplicate that this produces (#633).
CHUNK_OVERLAP = 500

#: Reserved key in an analysis result. Ours wins on a collision: a model that
#: invents a ``provenance`` key has not produced provenance.
PROVENANCE_KEY = "provenance"


def _chunk_spans(text: str, size: int = LLM_CHUNK_SIZE) -> list[tuple[int, int]]:
    """``[(start, end), …]`` over *text*.

    The spans rather than only the substrings, because a chunk that does not
    answer has to be locatable afterwards: "character 88 500–118 500 is
    missing" sends somebody to the passage, "a chunk failed" does not (#634).
    """
    if len(text) <= size:
        return [(0, len(text))]
    spans = []
    start = 0
    while start < len(text):
        spans.append((start, min(start + size, len(text))))
        start = start + size - CHUNK_OVERLAP
    return spans


def _chunks(text: str, size: int = LLM_CHUNK_SIZE) -> list[str]:
    return [text[start:end] for start, end in _chunk_spans(text, size)]


def _entry_key(entry) -> str:
    """A stable key for one finding, so the chunk overlap is not counted twice.

    ``_chunks`` overlaps by 500 characters on purpose — a care instance sitting
    on a seam would otherwise be lost — but the merge used to ``.extend`` the
    lists with no comparison, so a finding in the overlap was reported **twice**
    and every count over a long corpus came out high (#633).

    The key is the entry itself, canonically dumped, lowercased and with its
    whitespace collapsed: the same passage quoted by the same model in two
    chunks produces the same entry, and that is the duplicate worth removing.
    Two findings that differ in any field stay — merging those would be a
    judgement about whether the model meant the same thing twice, which this
    function is in no position to make.
    """
    try:
        text = json.dumps(entry, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(entry)
    return " ".join(text.lower().split())


def _merge_json_results(results: list[dict]) -> dict:
    """Merge every key of every chunk's result, de-duplicated.

    It used to take **one** key and drop the rest. ``_care_analysis`` answers
    with three (``care_instances``, ``patterns``, ``gender_aspects``) and was
    merged on ``care_instances``, so for any corpus over ``LLM_CHUNK_SIZE`` the
    other two vanished from ``care_analysis.json`` without a word (#633).

    Lists concatenate (minus duplicates), dicts update, and a scalar keeps the
    first chunk that had one — a scalar per chunk is not something to add up,
    and picking the last would mean the answer depended on chunk order.
    """
    merged: dict = {}
    seen: dict[str, set] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        for key, value in result.items():
            if isinstance(value, list):
                bucket = merged.setdefault(key, [])
                known = seen.setdefault(key, set())
                for entry in value:
                    entry_key = _entry_key(entry)
                    if entry_key in known:
                        continue
                    known.add(entry_key)
                    bucket.append(entry)
            elif isinstance(value, dict):
                merged.setdefault(key, {}).update(value)
            else:
                merged.setdefault(key, value)
    return merged


def _analyse_large(text: str, analyse, keys: tuple[str, ...],
                   label: str) -> dict:
    """Run one per-chunk analysis over the whole text, merged and **marked**.

    Through the same merge whether there is one chunk or seven. The single-chunk
    case used to return ``results[0]`` unmerged, which made the *shape* of the
    output depend on the corpus size (#633).

    This is also the one place that catches a chunk's failure, and that is the
    point of #634. The per-chunk functions used to swallow their own exception
    and return an empty container, which left two readings indistinguishable:
    "the model read the corpus and found nothing" — a result, and for a care
    corpus a surprising one — and "the call never came back". Worse, the empty
    containers were merged in, so a run with one failed chunk out of seven
    described **six sevenths** of the corpus while claiming to describe all of
    it, and nothing recorded the gap.

    So every result carries a ``provenance`` block: how many chunks there were,
    how many answered, whether that is all of them, and the character range of
    each one that did not. ``complete: true`` with an empty list therefore means
    *measured, nothing found*; ``complete: false`` means *incomplete*; and a
    result with no block at all is simply not assessable — which is all that
    needs saying about the records written before this (owner decision,
    13.10.2026: they are deleted rather than migrated).

    *keys* are the keys this analysis answers with, in order. They are filled in
    empty when no chunk answered, so a consumer does not have to handle both a
    missing key and an empty one — the provenance already says which it is — and
    ``keys[0]`` is the list the report counts.
    """
    spans = _chunk_spans(text)
    results: list[dict] = []
    failures: list[dict] = []
    for number, (start, end) in enumerate(spans, start=1):
        try:
            results.append(analyse(text[start:end]))
        except Exception as e:  # noqa: BLE001 — one chunk may not end the run
            logger.warning(f"[Agent D] {label}, Abschnitt {number}/{len(spans)} "
                           f"(Zeichen {start}–{end}) fehlgeschlagen: {e}")
            failures.append({"chunk": number, "range": [start, end],
                             "error": f"{type(e).__name__}: {e}"})

    merged = _merge_json_results(results)
    for key in keys:
        merged.setdefault(key, [])
    merged[PROVENANCE_KEY] = {
        "chunks_total": len(spans),
        "chunks_answered": len(results),
        "complete": not failures,
        "failures": failures,
    }
    return merged


def _topics_large(text: str) -> dict:
    return _analyse_large(text, _topics, ("topics",), "Topics")


def _taxonomy_large(text: str) -> dict:
    return _analyse_large(text, _taxonomy, ("taxonomies",), "Taxonomie")


def _care_analysis_large(text: str) -> dict:
    return _analyse_large(
        text, _care_analysis,
        ("care_instances", "patterns", "gender_aspects"), "Care-Analyse")


# ── Per-chunk LLM calls ──────────────────────────────────────────────────────

def _ask(prompt: str, what: str) -> dict:
    """One analysis call: a budget the model can finish in, and a tolerant parse.

    Two things this fixes, both already written down in this repository (#633):

    * ``max_tokens`` comes from ``config.GPUSTACK_TEXT_MAX_TOKENS``. These calls
      passed **2000**, below the floor ``config.py`` introduced for exactly this
      model with the note "it spends tokens on reasoning_content before emitting
      content. Give text calls a generous budget or content comes back null".
    * the answer goes through ``llm_json.loads``, because a reasoning model puts
      prose or a fence around the object and ``json.loads`` on the raw answer
      therefore fails routinely rather than exceptionally.

    Raises on failure. The caller decides what an un-answered chunk means, and
    today each one still returns its empty container, which reads as a finding.
    That is #634, decided and waiting on this change: the result carries a
    ``provenance`` block with the chunks that answered and the character range
    of each one that did not.
    """
    answer = gs.chat_text(prompt, system=None,
                          max_tokens=config.GPUSTACK_TEXT_MAX_TOKENS)
    # Always a dict: `llm_json.loads` either parses an object literal or raises,
    # and it refuses an array element outright — a model answering
    # `[{"name": …}]` has given the wrong shape and must not be handed back as
    # a plausible answer object. So no isinstance check here; one would be dead
    # code, and dead code is untestable by definition.
    return llm_json.loads(answer, what=what)


# The three per-chunk analyses. None of them catches its own failure any more:
# an empty container returned from an exception handler is indistinguishable
# from a measured "nothing here" (#634). `_analyse_large` catches, records the
# chunk and its character range, and carries on.

def _topics(text: str) -> dict:
    prompt = SYSTEM + "\n\nIdentifiziere 5–10 Topics mit Namen und Schlüsselwörtern.\n\n" + text + "\n\nAntworte als JSON: {topics: [{name, keywords: []}]}"
    return _ask(prompt, "Topics-Antwort")


def _taxonomy(text: str) -> dict:
    prompt = SYSTEM + "\n\nAnalysiere soziale Taxonomien: arme lüt, erbar lüt, Bürger, Hintersässe, Juden, Zigeuner, Vaganten, gesellen, Knecht, Magd, Witwe, Waise.\n\n" + text + "\n\nAntworte als JSON: {taxonomies: [{term, count, contexts: []}]}"
    return _ask(prompt, "Taxonomie-Antwort")


def _care_analysis(text: str) -> dict:
    prompt = SYSTEM + "\n\nAnalysiere Care-relevante Inhalte: Care-Instanzen, Arrangements, Entlohnung, Gender, Lebensläufe.\n\n" + text + "\n\nAntworte als JSON: {care_instances: [], patterns: [], gender_aspects: []}"
    return _ask(prompt, "Care-Analyse-Antwort")


# ── Voyant + Persistence ─────────────────────────────────────────────────────

def _analysis_line(label: str, result, key: str) -> str:
    """One line of the report: what the analysis found, and whether it is all.

    The report named only documents, characters, words and the Voyant link —
    the three LLM analyses did not appear in it **at all**, so it hid both the
    result and any gap in it (#634). A number alone would be the other half of
    the same problem: "47 Care-Instanzen" over a corpus where one section never
    answered is a number nobody should act on without knowing that.
    """
    entries = (result or {}).get(key)
    count = len(entries) if isinstance(entries, (list, dict)) else 0
    provenance = (result or {}).get(PROVENANCE_KEY)
    if not isinstance(provenance, dict):
        # No block: written before #634, or by a caller that bypassed
        # `_analyse_large`. Not assessable, and said so rather than implied.
        return f"- **{label}:** {count} _(Abdeckung nicht beurteilbar)_"
    if provenance.get("complete"):
        return f"- **{label}:** {count}"
    answered = provenance.get("chunks_answered", 0)
    total = provenance.get("chunks_total", 0)
    gaps = ", ".join(
        f"{f.get('range', ['?', '?'])[0]}–{f.get('range', ['?', '?'])[1]}"
        for f in provenance.get("failures") or [])
    return (f"- **{label}:** {count} — **unvollständig**: {answered} von "
            f"{total} Abschnitten beantwortet"
            + (f" (fehlt: Zeichen {gaps})" if gaps else ""))


def _voyant_url(text: str, corpus_name: str) -> str:
    """Upload the corpus text to Voyant and return the shareable session URL.

    Uses the **Trombone API** (``POST <voyant>/trombone``), which is Voyant's
    documented programmatic ingest and returns JSON. It creates a corpus and hands
    back ``corpus.metadata.id``; the shareable link is ``<voyant>/?corpus=<id>``.

    Why not ``POST /?text=``: that hits the JSP UI shell, which on the self-hosted
    Voyant 2.4 (tei) answers **HTTP 500** — verified live 2026-07-17:

        POST /voyant/?text=   → 500  (org.apache.jasper.JasperException)
        POST /voyant/trombone → 200  {"corpus": {"metadata": {"id": "..."}}}

    The UI shell is for humans typing into a box; Trombone is the API. This was the
    root cause behind #29 (and the earlier /Corpus endpoint that never existed).
    """
    try:
        base = config.VOYANT_API_URL.rstrip("/")
        resp = requests.post(
            base + "/trombone",
            data={"tool": "corpus.CorpusMetadata", "input": text[:50_000]},
            timeout=60,
        )
        resp.raise_for_status()
        cid = (resp.json().get("corpus", {}).get("metadata", {}) or {}).get("id")
        if cid:
            return f"{base}/?corpus={cid}"
        logger.warning("[Agent D] Voyant/Trombone: no corpus id in response")
    except Exception as e:
        logger.warning(f"[Agent D] Voyant-Link fehlgeschlagen: {e}")
    return ""


def _voyant_corpus_exists(corpus_id: str) -> bool:
    """True if Trombone can retrieve the corpus by id — i.e. it persisted.

    Stronger than "a link string was built": it confirms the corpus is actually
    stored and fetchable, not that CorpusMetadata merely echoed an id. This is the
    limit of what a server-side check can prove, though — it CANNOT verify the
    browser dashboard renders, which on tei is currently broken by a Voyant
    subpath-mount issue (#315), independent of this code.
    """
    try:
        base = config.VOYANT_API_URL.rstrip("/")
        resp = requests.post(
            base + "/trombone",
            data={"tool": "corpus.CorpusMetadata", "corpus": corpus_id},
            timeout=30,
        )
        resp.raise_for_status()
        got = (resp.json().get("corpus", {}).get("metadata", {}) or {}).get("id")
        return got == corpus_id
    except Exception as e:
        logger.warning(f"[Agent D] Voyant corpus retrieve failed: {e}")
        return False


def verify_voyant(sample_text: str = "Dis ist ein kurzer Beispieltext für Voyant.") -> dict:
    """Live smoke check for the Voyant export (#29).

    Creates a sample corpus and then **retrieves it back by id**, so ``ok`` means
    the corpus persisted and is fetchable — not merely that a ``?corpus=`` string
    was built. Run on-host:

        python -c "from agents.corpus_analysis import verify_voyant as v; print(v())"

    IMPORTANT — what this does NOT prove: that the ``?corpus=`` link renders the
    Voyant **dashboard** in a browser. That is a client-side SPA concern no
    server-side check can reach, and on tei it is currently broken by a Voyant
    subpath-mount issue (#315). A green ``ok`` here confirms the API contract; the
    interactive link is gated on that deployment fix. Do not read ``ok=True`` as
    "a human can open this."

    Returns ``{ok, url, endpoint, retrievable, reason}`` (never raises).
    """
    endpoint = config.VOYANT_API_URL.rstrip("/") + "/"
    url = _voyant_url(sample_text, "verify")
    has_link = bool(url) and "corpus=" in url
    cid = url.rsplit("corpus=", 1)[-1] if has_link else ""
    retrievable = _voyant_corpus_exists(cid) if cid else False
    ok = has_link and retrievable
    if not has_link:
        reason = "no ?corpus= link — Trombone unreachable or contract changed"
    elif not retrievable:
        reason = "link built but corpus not retrievable by id — did not persist"
    else:
        reason = ("corpus created and retrievable via API "
                  "(NB: browser dashboard rendering is not checked — see #315)")
    return {
        "ok": ok,
        "url": url,
        "endpoint": endpoint,
        "retrievable": retrievable,
        "reason": reason,
    }


def corpus_out_dir(corpus_name: str) -> Path:
    """Output directory for a corpus analysis, guaranteed inside OUTPUTS_DIR.

    ``corpus_name`` comes from the ``/agent_d`` slash command; a name like
    ``x/../../../y`` would otherwise climb out of ``OUTPUTS_DIR`` when :func:`_save`
    calls ``mkdir(parents=True)`` (#574). Spaces stay allowed (they become
    underscores); only a name that resolves outside OUTPUTS_DIR is refused.
    """
    safe = (corpus_name or "").replace(" ", "_")
    out = config.OUTPUTS_DIR / f"corpus_{safe}"
    if not out.resolve().is_relative_to(config.OUTPUTS_DIR.resolve()):
        raise ValueError(
            f"unsafe corpus name {corpus_name!r}: resolves outside the outputs directory")
    return out


def _save(corpus_name: str, result: dict):
    out = corpus_out_dir(corpus_name)
    out.mkdir(parents=True, exist_ok=True)

    (out / "stats.json").write_text(json.dumps(result["stats"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "topics.json").write_text(json.dumps(result["topics"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "taxonomy.json").write_text(json.dumps(result["taxonomy"], ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "care_analysis.json").write_text(json.dumps(result["care_analysis"], ensure_ascii=False, indent=2), encoding="utf-8")

    s = result["stats"]
    analyses = "\n".join([
        _analysis_line("Topics", result.get("topics"), "topics"),
        _analysis_line("Taxonomien", result.get("taxonomy"), "taxonomies"),
        _analysis_line("Care-Instanzen", result.get("care_analysis"),
                       "care_instances"),
    ])
    (out / "report.md").write_text(
        f"# Korpus-Analyse: {corpus_name}\n\n"
        f"**Dokumente:** {result['doc_count']}  **Zeichen:** {result['total_chars']:,}  "
        f"**Wörter:** {s.get('total_words', 0):,}\n\n"
        f"## Analysen\n\n{analyses}\n\n"
        f"## Voyant\n\n{result.get('voyant_url', '—')}\n",
        encoding="utf-8",
    )
    logger.info(f"[Agent D] Gespeichert: {out}")