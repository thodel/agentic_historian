"""
test_ah_634_analysis_provenance.py — eine leere Liste ist nicht "nichts gefunden".

Agent D's three LLM analyses each caught their own failure and returned an empty
container, which is indistinguishable from a measured "nothing here" — and for a
care corpus, "no care instances" is a surprising result rather than a neutral
one. Worse, it compounded: a long corpus is cut into chunks, and a chunk that
never answered contributed its empty containers to the merge, so a run described
six sevenths of the corpus while claiming to describe all of it (#634).

The decided design (Owner, 13.10.2026):

  1. carry on marked, not fail loudly;
  2. a `provenance` block per analysis, with the **character range** of every
     chunk that did not answer;
  3. no vintage rule — the stored results are deleted rather than migrated, so a
     missing block simply means "not assessable";
  4. three lines in `report.md`.

Offline: `gs.chat_text` is stubbed, no network and no LLM.

Run: pytest agentic_historian/tests/test_ah_634_analysis_provenance.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                          # noqa: E402
from agents import corpus_analysis as ca               # noqa: E402


def _answers(*texts):
    """A `chat_text` stub that answers each call in turn."""
    answers = iter(texts)

    def _chat(*a, **k):
        try:
            return next(answers)
        except StopIteration:                           # pragma: no cover
            raise AssertionError("more calls than answers") from None

    return _chat


CARE = ('{"care_instances": [{"t": "i%d"}], "patterns": ["p%d"], '
        '"gender_aspects": ["g%d"]}')
NO_JSON = "Ich denke darüber nach, aber hier kommt kein Objekt."


def _long(chunks: int) -> str:
    """Text that really is cut into *chunks* pieces.

    The assertion is the point: the first version of this helper was ten
    characters past a chunk boundary and produced **one** span for
    ``_long(2)``, which left the test that wanted two chunks passing for the
    wrong reason. A fixture that silently builds the wrong shape makes every
    test that uses it vacuous.
    """
    step = ca.LLM_CHUNK_SIZE - ca.CHUNK_OVERLAP
    text = "x" * ((chunks - 1) * step + ca.LLM_CHUNK_SIZE // 2)
    assert len(ca._chunk_spans(text)) == chunks, (
        f"wanted {chunks} chunks, got {len(ca._chunk_spans(text))}")
    return text


# ── 1. three values: measured-empty, incomplete, not assessable ────────────

def test_a_measured_empty_result_says_it_is_complete(monkeypatch):
    """The reading that was impossible before: the model looked and found
    nothing. For a care corpus that is a finding, not a neutral blank."""
    monkeypatch.setattr(ca.gs, "chat_text", _answers(
        '{"care_instances": [], "patterns": [], "gender_aspects": []}'))
    out = ca._care_analysis_large("x" * 100)
    assert out["care_instances"] == []
    assert out[ca.PROVENANCE_KEY]["complete"] is True
    assert out[ca.PROVENANCE_KEY]["chunks_answered"] == 1
    assert out[ca.PROVENANCE_KEY]["failures"] == []


def test_a_failed_chunk_marks_the_result_incomplete(monkeypatch):
    monkeypatch.setattr(ca.gs, "chat_text", _answers(
        CARE % (1, 1, 1), NO_JSON, CARE % (3, 3, 3)))
    out = ca._care_analysis_large(_long(3))

    prov = out[ca.PROVENANCE_KEY]
    assert prov["complete"] is False
    assert prov["chunks_total"] == 3
    assert prov["chunks_answered"] == 2
    assert len(prov["failures"]) == 1


def test_what_the_chunks_that_answered_found_is_still_there(monkeypatch):
    """Carry on marked — the run stays usable (decision 1)."""
    monkeypatch.setattr(ca.gs, "chat_text", _answers(
        CARE % (1, 1, 1), NO_JSON, CARE % (3, 3, 3)))
    out = ca._care_analysis_large(_long(3))
    assert out["care_instances"] == [{"t": "i1"}, {"t": "i3"}]
    assert out["patterns"] == ["p1", "p3"]


def test_a_result_without_a_block_is_not_assessable_and_not_complete():
    """No vintage rule (decision 3): absence says "nobody knows", and that is
    all that has to be said about it."""
    assert ca.PROVENANCE_KEY not in {"care_instances"}
    line = ca._analysis_line("Topics", {"topics": [1, 2]}, "topics")
    assert "nicht beurteilbar" in line


def test_the_three_states_are_three_different_lines():
    complete = ca._analysis_line("Topics", {
        "topics": [1], ca.PROVENANCE_KEY: {
            "complete": True, "chunks_total": 1, "chunks_answered": 1,
            "failures": []}}, "topics")
    incomplete = ca._analysis_line("Topics", {
        "topics": [1], ca.PROVENANCE_KEY: {
            "complete": False, "chunks_total": 2, "chunks_answered": 1,
            "failures": [{"chunk": 2, "range": [29500, 59500]}]}}, "topics")
    unknown = ca._analysis_line("Topics", {"topics": [1]}, "topics")
    assert len({complete, incomplete, unknown}) == 3


# ── 2. the character range of every gap ───────────────────────────────────

def test_a_failure_carries_the_character_range_of_its_chunk(monkeypatch):
    """"Character 29 500–59 500 is missing" sends somebody to the passage;
    "a chunk failed" does not (decision 2)."""
    monkeypatch.setattr(ca.gs, "chat_text", _answers(CARE % (1, 1, 1), NO_JSON))
    out = ca._care_analysis_large(_long(2))

    [failure] = out[ca.PROVENANCE_KEY]["failures"]
    assert failure["chunk"] == 2
    start, end = failure["range"]
    assert start == ca.LLM_CHUNK_SIZE - ca.CHUNK_OVERLAP
    assert end > start


def test_the_ranges_cover_the_whole_text_and_overlap_as_designed():
    spans = ca._chunk_spans("y" * (3 * ca.LLM_CHUNK_SIZE))
    assert spans[0][0] == 0
    assert spans[-1][1] == 3 * ca.LLM_CHUNK_SIZE
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        assert prev_end - next_start == ca.CHUNK_OVERLAP


def test_a_short_text_is_one_span_over_its_whole_length():
    assert ca._chunk_spans("abc") == [(0, 3)]
    assert ca._chunk_spans("") == [(0, 0)]


def test_the_spans_and_the_chunks_agree():
    """`_chunks` is built from the spans, so a range always names the text that
    was actually sent."""
    text = "z" * (2 * ca.LLM_CHUNK_SIZE + 123)
    for chunk, (start, end) in zip(ca._chunks(text), ca._chunk_spans(text)):
        assert chunk == text[start:end]


def test_a_failure_names_the_error_class(monkeypatch):
    monkeypatch.setattr(ca.gs, "chat_text", _answers(NO_JSON))
    out = ca._topics_large("x" * 100)
    [failure] = out[ca.PROVENANCE_KEY]["failures"]
    assert "LlmJsonError" in failure["error"]


def test_a_transport_failure_is_recorded_like_any_other(monkeypatch):
    def _boom(*a, **k):
        raise TimeoutError("read timeout")

    monkeypatch.setattr(ca.gs, "chat_text", _boom)
    out = ca._topics_large("x" * 100)
    assert out[ca.PROVENANCE_KEY]["chunks_answered"] == 0
    assert "TimeoutError" in out[ca.PROVENANCE_KEY]["failures"][0]["error"]


# ── the keys are always there, so a consumer has one case fewer ───────────

def test_every_declared_key_is_present_even_when_nothing_answered(monkeypatch):
    monkeypatch.setattr(ca.gs, "chat_text", _answers(NO_JSON))
    out = ca._care_analysis_large("x" * 100)
    assert out["care_instances"] == []
    assert out["patterns"] == []
    assert out["gender_aspects"] == []
    # And the block is what distinguishes this from a measured empty result.
    assert out[ca.PROVENANCE_KEY]["complete"] is False
    assert out[ca.PROVENANCE_KEY]["chunks_answered"] == 0


@pytest.mark.parametrize("fn,keys", [
    ("_topics_large", ("topics",)),
    ("_taxonomy_large", ("taxonomies",)),
    ("_care_analysis_large",
     ("care_instances", "patterns", "gender_aspects")),
])
def test_each_analysis_declares_the_keys_it_answers_with(monkeypatch, fn, keys):
    monkeypatch.setattr(ca.gs, "chat_text", _answers(NO_JSON))
    out = getattr(ca, fn)("x" * 100)
    for key in keys:
        assert key in out, key


def test_our_provenance_wins_over_one_the_model_invented(monkeypatch):
    """A model answering with a `provenance` key has not produced provenance."""
    monkeypatch.setattr(ca.gs, "chat_text", _answers(
        '{"topics": [], "provenance": {"complete": true, "note": "alles gut"}}'))
    out = ca._topics_large("x" * 100)
    assert out[ca.PROVENANCE_KEY]["chunks_total"] == 1
    assert "note" not in out[ca.PROVENANCE_KEY]


# ── the per-chunk calls no longer swallow ─────────────────────────────────

def test_no_analysis_returns_an_empty_container_from_an_exception_handler():
    """The shape this issue is about. An empty list produced by a `except` is a
    claim about the corpus that nobody measured."""
    source = (PKG / "agents" / "corpus_analysis.py").read_text(encoding="utf-8")
    for empty in ('return {"topics": []}',
                  'return {"taxonomies": []}',
                  'return {"care_instances": [], "patterns": [], '
                  '"gender_aspects": []}'):
        assert empty not in source, empty


def test_the_failure_is_caught_in_exactly_one_place():
    """`_analyse_large` — the only place that knows which chunk it was."""
    source = (PKG / "agents" / "corpus_analysis.py").read_text(encoding="utf-8")
    block = source[source.index("def _analyse_large("):
                   source.index("def _topics_large(")]
    assert "except Exception" in block
    # And the per-chunk functions do not.
    for name in ("def _topics(", "def _taxonomy(", "def _care_analysis("):
        start = source.index(name)
        body = source[start:source.index("\n\n\n", start)]
        assert "except" not in body, name


def test_one_failed_chunk_does_not_end_the_whole_analysis(monkeypatch):
    """Not option B: a single timeout may not throw away twenty minutes."""
    monkeypatch.setattr(ca.gs, "chat_text", _answers(
        NO_JSON, CARE % (2, 2, 2), NO_JSON))
    out = ca._care_analysis_large(_long(3))
    assert out["care_instances"] == [{"t": "i2"}]
    assert out[ca.PROVENANCE_KEY]["chunks_answered"] == 1


# ── 4. the report ────────────────────────────────────────────────────────

def test_the_report_names_all_three_analyses(tmp_path, monkeypatch):
    """They did not appear in it at all, so it hid the result and the gap."""
    monkeypatch.setattr(ca.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ca, "corpus_out_dir", lambda name: tmp_path / name)

    ca._save("korpus", {
        "corpus_name": "korpus", "doc_count": 2, "total_chars": 1000,
        "stats": {"total_words": 180},
        "topics": {"topics": [1, 2, 3], ca.PROVENANCE_KEY: {
            "complete": True, "chunks_total": 1, "chunks_answered": 1,
            "failures": []}},
        "taxonomy": {"taxonomies": [1], ca.PROVENANCE_KEY: {
            "complete": True, "chunks_total": 1, "chunks_answered": 1,
            "failures": []}},
        "care_analysis": {"care_instances": [1] * 47, ca.PROVENANCE_KEY: {
            "complete": False, "chunks_total": 7, "chunks_answered": 6,
            "failures": [{"chunk": 3, "range": [88500, 118500],
                          "error": "LlmJsonError: …"}]}},
        "voyant_url": "",
    })

    report = (tmp_path / "korpus" / "report.md").read_text(encoding="utf-8")
    assert "Topics:** 3" in report
    assert "Taxonomien:** 1" in report
    assert "Care-Instanzen:** 47" in report


def test_the_report_says_when_an_analysis_is_incomplete(tmp_path, monkeypatch):
    """47 over a corpus where a section never answered is a number nobody
    should act on without knowing that."""
    monkeypatch.setattr(ca, "corpus_out_dir", lambda name: tmp_path / name)
    ca._save("korpus", {
        "doc_count": 1, "total_chars": 10, "stats": {},
        "topics": {}, "taxonomy": {},
        "care_analysis": {"care_instances": [1] * 47, ca.PROVENANCE_KEY: {
            "complete": False, "chunks_total": 7, "chunks_answered": 6,
            "failures": [{"chunk": 3, "range": [88500, 118500],
                          "error": "x"}]}},
        "voyant_url": "",
    })
    report = (tmp_path / "korpus" / "report.md").read_text(encoding="utf-8")
    assert "unvollständig" in report
    assert "6 von 7" in report
    assert "88500" in report and "118500" in report


def test_the_report_does_not_claim_coverage_it_cannot_assess(tmp_path,
                                                             monkeypatch):
    monkeypatch.setattr(ca, "corpus_out_dir", lambda name: tmp_path / name)
    ca._save("korpus", {
        "doc_count": 1, "total_chars": 10, "stats": {},
        "topics": {"topics": [1]}, "taxonomy": {}, "care_analysis": {},
        "voyant_url": "",
    })
    report = (tmp_path / "korpus" / "report.md").read_text(encoding="utf-8")
    assert "nicht beurteilbar" in report


def test_the_block_reaches_the_json_files_as_well(tmp_path, monkeypatch):
    """`care_analysis.json` is what a researcher opens; the marker has to be in
    it, not only in the report."""
    monkeypatch.setattr(ca, "corpus_out_dir", lambda name: tmp_path / name)
    block = {"complete": False, "chunks_total": 2, "chunks_answered": 1,
             "failures": [{"chunk": 2, "range": [29500, 59500], "error": "x"}]}
    ca._save("korpus", {
        "doc_count": 1, "total_chars": 10, "stats": {},
        "topics": {"topics": [], ca.PROVENANCE_KEY: block},
        "taxonomy": {"taxonomies": [], ca.PROVENANCE_KEY: block},
        "care_analysis": {"care_instances": [], ca.PROVENANCE_KEY: block},
        "voyant_url": "",
    })
    for name in ("topics", "taxonomy", "care_analysis"):
        stored = json.loads((tmp_path / "korpus" / f"{name}.json")
                            .read_text(encoding="utf-8"))
        assert stored[ca.PROVENANCE_KEY]["complete"] is False
        assert stored[ca.PROVENANCE_KEY]["failures"][0]["range"] == [29500, 59500]


# ── end to end ───────────────────────────────────────────────────────────

def test_a_whole_corpus_run_marks_the_gap(tmp_path, monkeypatch):
    """stats → topics → taxonomy → care, with one analysis losing a chunk."""
    monkeypatch.setattr(ca.config, "TRANSCRIPTIONS_DIR", tmp_path)
    monkeypatch.setattr(ca, "corpus_out_dir", lambda name: tmp_path / "out")
    monkeypatch.setattr(ca.config, "ENABLE_VOYANT_UPLOAD", False)
    (tmp_path / "doc-1.txt").write_text("unser fruntlich gruos " * 10,
                                        encoding="utf-8")
    # topics ok, taxonomy fails, care ok — one call each (short corpus).
    monkeypatch.setattr(ca.gs, "chat_text", _answers(
        '{"topics": [{"name": "Fürsorge"}]}',
        NO_JSON,
        '{"care_instances": [{"t": "i"}], "patterns": [], "gender_aspects": []}'))

    result = ca.analyse_corpus("korpus")
    assert result["topics"][ca.PROVENANCE_KEY]["complete"] is True
    assert result["taxonomy"][ca.PROVENANCE_KEY]["complete"] is False
    assert result["taxonomy"]["taxonomies"] == []
    assert result["care_analysis"]["care_instances"] == [{"t": "i"}]

    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "Topics:** 1" in report
    assert "unvollständig" in report


def test_the_token_floor_from_633_still_holds(monkeypatch):
    """The two issues meet here: a budget the model can finish in makes these
    failures rare, and the provenance makes the rare ones visible."""
    seen = {}

    def _chat(prompt, system=None, **kwargs):
        seen.update(kwargs)
        return '{"topics": []}'

    monkeypatch.setattr(ca.gs, "chat_text", _chat)
    ca._topics_large("x" * 100)
    assert seen["max_tokens"] == config.GPUSTACK_TEXT_MAX_TOKENS
