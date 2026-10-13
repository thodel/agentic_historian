"""
test_ah_633_corpus_analysis.py — Agent D's merge, its dedup and its parsing.

Three mechanical defects (#633), all in `agents/corpus_analysis.py`:

  1. `_merge_json_results` kept **one** key, so a corpus over `LLM_CHUNK_SIZE`
     lost `patterns` and `gender_aspects` from the care analysis entirely — and
     the single-chunk path skipped the merge, so the output *shape* depended on
     the corpus size.
  2. `_chunks` overlaps by 500 characters on purpose and the merge concatenated
     the lists, so a finding on a seam was counted twice.
  3. The three calls passed `max_tokens=2000`, below the floor `config.py`
     introduced for this reasoning model, and parsed the answer with a bare
     `json.loads` — which a reasoning preamble makes fail routinely.

Offline: `gs.chat_text` is stubbed, no network and no LLM.

Run: pytest agentic_historian/tests/test_ah_633_corpus_analysis.py -v
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
from utils import llm_json                             # noqa: E402


# ── 1. every key survives the merge ────────────────────────────────────────

def test_the_merge_keeps_every_key_not_one():
    """The bug: `_care_analysis` answers with three keys and the merge was
    called with one, so two vanished for any corpus over 30k characters."""
    merged = ca._merge_json_results([
        {"care_instances": ["a"], "patterns": ["p1"], "gender_aspects": ["g1"]},
        {"care_instances": ["b"], "patterns": ["p2"], "gender_aspects": ["g2"]},
    ])
    assert merged["care_instances"] == ["a", "b"]
    assert merged["patterns"] == ["p1", "p2"]
    assert merged["gender_aspects"] == ["g1", "g2"]


def test_a_long_care_analysis_keeps_all_three_keys(monkeypatch):
    """End to end over three chunks — the shape a researcher actually gets."""
    answers = iter([
        '{"care_instances": [{"t": "i1"}], "patterns": ["p1"], "gender_aspects": ["g1"]}',
        '{"care_instances": [{"t": "i2"}], "patterns": ["p2"], "gender_aspects": ["g2"]}',
        '{"care_instances": [{"t": "i3"}], "patterns": ["p3"], "gender_aspects": ["g3"]}',
    ])
    monkeypatch.setattr(ca.gs, "chat_text", lambda *a, **k: next(answers))

    out = ca._care_analysis_large("x" * (2 * ca.LLM_CHUNK_SIZE + 10))
    assert set(out) == {"care_instances", "patterns", "gender_aspects",
                        ca.PROVENANCE_KEY}       # + the provenance block (#634)
    assert len(out["care_instances"]) == 3
    assert out["patterns"] == ["p1", "p2", "p3"]
    assert out["gender_aspects"] == ["g1", "g2", "g3"]


def test_the_shape_does_not_depend_on_the_corpus_size(monkeypatch):
    """One chunk used to bypass the merge entirely, so two corpora were not
    comparable: a short one kept three keys, a long one kept one."""
    answer = ('{"care_instances": [{"t": "i"}], "patterns": ["p"], '
              '"gender_aspects": ["g"]}')
    monkeypatch.setattr(ca.gs, "chat_text", lambda *a, **k: answer)

    short = ca._care_analysis_large("x" * 100)
    long = ca._care_analysis_large("x" * (2 * ca.LLM_CHUNK_SIZE + 10))
    assert set(short) == set(long)


def test_a_single_chunk_goes_through_the_merge_as_well(monkeypatch):
    """The key set alone does not show the bypass — the dedup does. A model
    that repeats itself inside one answer must not be reported twice either."""
    monkeypatch.setattr(ca.gs, "chat_text", lambda *a, **k: (
        '{"patterns": ["Almosen an Witwen", "almosen  an witwen"]}'))
    out = ca._care_analysis_large("x" * 100)        # one chunk
    assert len(out["patterns"]) == 1


def test_a_chunk_that_answered_nothing_does_not_remove_the_others_keys(monkeypatch):
    """Today a failed chunk contributes its empty containers. They must not
    erase what the chunks that did answer found (the honesty of the empty
    result itself is #634)."""
    answers = iter(['{"care_instances": [{"t": "i1"}], "patterns": ["p1"]}',
                    'Ich denke nach, aber hier kommt kein JSON'])
    monkeypatch.setattr(ca.gs, "chat_text", lambda *a, **k: next(answers))

    out = ca._care_analysis_large("x" * (ca.LLM_CHUNK_SIZE + 10))
    assert out["care_instances"] == [{"t": "i1"}]
    assert out["patterns"] == ["p1"]


def test_dicts_are_updated_and_scalars_keep_the_first_answer():
    merged = ca._merge_json_results([
        {"counts": {"a": 1}, "corpus": "A"},
        {"counts": {"b": 2}, "corpus": "B"},
    ])
    assert merged["counts"] == {"a": 1, "b": 2}
    # Not the last: the answer would then depend on chunk order.
    assert merged["corpus"] == "A"


def test_a_chunk_result_that_is_not_a_dict_is_skipped():
    merged = ca._merge_json_results([{"topics": ["t"]}, None, "kaputt", 42])
    assert merged == {"topics": ["t"]}


def test_an_empty_list_of_results_merges_to_nothing():
    assert ca._merge_json_results([]) == {}


# ── 2. the overlap is not counted twice ───────────────────────────────────

def test_a_finding_in_the_overlap_is_reported_once():
    """`_chunks` overlaps by 500 characters so a finding on a seam is not lost;
    the merge has to be the other half of that deal."""
    same = {"term": "arme lüt", "count": 3, "contexts": ["c"]}
    merged = ca._merge_json_results([{"taxonomies": [same]},
                                     {"taxonomies": [dict(same)]}])
    assert merged["taxonomies"] == [same]


def test_duplicates_are_recognised_despite_whitespace_and_case():
    merged = ca._merge_json_results([
        {"patterns": ["Almosen an Witwen"]},
        {"patterns": ["almosen  an   witwen"]},
    ])
    assert len(merged["patterns"]) == 1


def test_two_findings_that_differ_in_any_field_both_stay():
    """Merging those would be a judgement about whether the model meant the
    same thing twice. The safe direction is to keep both."""
    merged = ca._merge_json_results([
        {"taxonomies": [{"term": "arme lüt", "count": 3}]},
        {"taxonomies": [{"term": "arme lüt", "count": 2}]},
    ])
    assert len(merged["taxonomies"]) == 2


def test_an_unhashable_finding_does_not_break_the_merge():
    """Entries are dicts and lists, so the key cannot be the entry itself."""
    merged = ca._merge_json_results([
        {"topics": [{"name": "Fürsorge", "keywords": ["almosen", "spital"]}]},
        {"topics": [{"name": "Fürsorge", "keywords": ["almosen", "spital"]}]},
        {"topics": [{"name": "Fürsorge", "keywords": ["spital"]}]},
    ])
    assert len(merged["topics"]) == 2


def test_a_duplicate_across_three_chunks_is_still_one_entry():
    merged = ca._merge_json_results([{"patterns": ["p"]}] * 3)
    assert merged["patterns"] == ["p"]


def test_the_order_of_first_appearance_is_kept():
    """A researcher reading the file should see them as the corpus produced
    them, not re-sorted by a set."""
    merged = ca._merge_json_results([
        {"patterns": ["erste", "zweite"]},
        {"patterns": ["zweite", "dritte"]},
    ])
    assert merged["patterns"] == ["erste", "zweite", "dritte"]


# ── 3. the budget and the parsing ─────────────────────────────────────────

def test_the_calls_use_the_configured_token_floor(monkeypatch):
    """They passed 2000, below the floor `config.py` set for this model with
    the note that content comes back null otherwise."""
    seen = {}

    def _chat(prompt, system=None, **kwargs):
        seen.update(kwargs)
        return '{"topics": []}'

    monkeypatch.setattr(ca.gs, "chat_text", _chat)
    ca._topics("text")
    assert seen["max_tokens"] == config.GPUSTACK_TEXT_MAX_TOKENS
    assert config.GPUSTACK_TEXT_MAX_TOKENS >= 4096


def test_no_call_passes_a_literal_token_budget():
    """A number written here cannot follow the floor when it moves."""
    source = (PKG / "agents" / "corpus_analysis.py").read_text(encoding="utf-8")
    assert "max_tokens=2000" not in source
    assert source.count("config.GPUSTACK_TEXT_MAX_TOKENS") >= 1


@pytest.mark.parametrize("answer", [
    '{"topics": [{"name": "Fürsorge"}]}',
    'Ich denke nach …\n\n{"topics": [{"name": "Fürsorge"}]}',
    '```json\n{"topics": [{"name": "Fürsorge"}]}\n```',
    '{"topics": [{"name": "Fürsorge"}]}\n\nSo weit meine Analyse.',
])
def test_a_reasoning_preamble_or_a_fence_still_parses(monkeypatch, answer):
    """The normal case for a reasoning model, and a bare `json.loads` fails on
    three of these four."""
    monkeypatch.setattr(ca.gs, "chat_text", lambda *a, **k: answer)
    assert ca._topics("text") == {"topics": [{"name": "Fürsorge"}]}


def test_an_answer_with_no_json_raises_rather_than_looking_like_a_finding(
        monkeypatch):
    """This returned `{"topics": []}` from its own exception handler, which is
    the same thing a measured "no topics here" looks like. Since #634 the
    per-chunk call raises and `_analyse_large` records the gap."""
    monkeypatch.setattr(ca.gs, "chat_text", lambda *a, **k: "Ich bin unsicher.")
    with pytest.raises(llm_json.LlmJsonError):
        ca._topics("text")


def test_a_json_array_is_refused_rather_than_returned(monkeypatch):
    """The prompts ask for an object. Handing back the array's first element
    would put the wrong keys into the merged result — `name` where `taxonomies`
    belonged — and look like an answer while doing it."""
    monkeypatch.setattr(ca.gs, "chat_text", lambda *a, **k: '[{"name": "x"}]')
    with pytest.raises(llm_json.LlmJsonError):
        ca._taxonomy("text")
    # And the corpus result therefore carries no stray key — only the empty
    # list and the provenance that says why it is empty (#634).
    out = ca._taxonomy_large("text")
    assert "name" not in out
    assert out["taxonomies"] == []


def test_all_three_analyses_go_through_the_same_call(monkeypatch):
    """One place where the budget and the parsing are decided, not three."""
    monkeypatch.setattr(ca.gs, "chat_text", lambda *a, **k: '{"x": []}')
    for fn in (ca._topics, ca._taxonomy, ca._care_analysis):
        assert fn("text") == {"x": []}

    source = (PKG / "agents" / "corpus_analysis.py").read_text(encoding="utf-8")
    assert source.count("gs.chat_text(") == 1


# ── the shared JSON extractor ─────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', '{"a": 1}'),
    ('vor {"a": 1} nach', '{"a": 1}'),
    ('```json\n{"a": [1, 2]}\n```', '{"a": [1, 2]}'),
    ('{"a": {"b": 2}} tail', '{"a": {"b": 2}}'),
])
def test_the_extractor_finds_the_first_balanced_object(text, expected):
    assert llm_json.extract_balanced_json(text) == expected


def test_a_brace_inside_a_string_does_not_end_the_object():
    """What the private copy got wrong: it counted braces inside string values.

    The brace is deliberately **unbalanced** — a balanced ``{sic}`` happens to
    cancel out, so it proves nothing about whether strings are respected.
    """
    text = '{"care_context": "Spital } zu Thun", "n": 1}'
    assert llm_json.extract_balanced_json(text) == text
    assert json.loads(llm_json.extract_balanced_json(text))["n"] == 1

    opening = '{"care_context": "Spital { zu Thun", "n": 1}'
    assert llm_json.extract_balanced_json(opening) == opening


def test_an_escaped_quote_does_not_end_the_string():
    """With a brace behind the escape: mishandling the escape ends the string
    early, and the brace then closes the object two fields too soon."""
    text = '{"q": "er sagte \\"}\\" dazu", "n": 2}'
    assert llm_json.extract_balanced_json(text) == text
    assert json.loads(llm_json.extract_balanced_json(text))["n"] == 2


def test_a_trailing_backslash_in_a_string_is_not_an_escape_of_the_quote():
    text = '{"p": "C:\\\\", "n": 3}'
    assert json.loads(llm_json.extract_balanced_json(text))["n"] == 3


def test_an_object_after_a_closing_bracket_is_still_the_answer():
    """Only ``[`` before the brace means "array element". A preamble that ends
    in ``]`` — a citation, a list the model wrote in prose — does not."""
    assert llm_json.extract_balanced_json('[Analyse] {"topics": []}') == '{"topics": []}'
    assert llm_json.extract_balanced_json('siehe [1] {"a": 1}') == '{"a": 1}'


def test_an_object_inside_an_array_is_refused():
    assert llm_json.extract_balanced_json('[{"name": "x"}]') == ""
    assert llm_json.extract_balanced_json('[ {"name": "x"} ]') == ""


@pytest.mark.parametrize("text", ["", "kein json", "{broken", None])
def test_nothing_parseable_yields_the_empty_string(text):
    assert llm_json.extract_balanced_json(text) == ""


def test_loads_names_what_it_was_parsing_and_quotes_what_came_back():
    """"Expecting value: line 1 column 1" in a log says nothing about which
    call failed, and these calls fail often enough that the log is where the
    diagnosis happens."""
    with pytest.raises(llm_json.LlmJsonError) as caught:
        llm_json.loads("Ich denke noch nach", what="Topics-Antwort")
    assert "Topics-Antwort" in str(caught.value)
    assert "Ich denke noch nach" in str(caught.value)


def test_loads_reports_a_broken_object_differently_from_a_missing_one():
    with pytest.raises(llm_json.LlmJsonError, match="unlesbar"):
        llm_json.loads('{"a": }', what="X")
    with pytest.raises(llm_json.LlmJsonError, match="kein JSON-Objekt"):
        llm_json.loads("nichts", what="X")


def test_the_error_is_a_value_error_so_existing_handlers_catch_it():
    assert issubclass(llm_json.LlmJsonError, ValueError)


def test_there_is_one_extractor_and_agent_b_uses_it():
    """`source_description` carried a private copy; a fourth caller would have
    written a fourth one."""
    source = (PKG / "agents" / "source_description.py").read_text(encoding="utf-8")
    assert "def _extract_balanced_json" not in source
    assert "llm_json" in source


def test_agent_b_still_splits_the_description_from_its_json():
    """`_parse_response` uses the extracted text's **length** to find where the
    markdown starts, so it needs the string and not the parsed object."""
    from agents import source_description as sd

    raw = ('{"Datierung": {"wert": "15. Jahrhundert"}}\n\n'
           '## Beschreibung\n\nEin Band in Ledereinband.')
    source_json, description_md = sd._parse_response(raw)
    assert source_json["Datierung"]["wert"] == "15. Jahrhundert"
    assert description_md.startswith("## Beschreibung")
    assert "Ledereinband" in description_md


def test_agent_b_still_parses_a_care_flag_out_of_a_reasoning_answer(monkeypatch):
    """The shared helper has to be at least as good as the copy it replaced."""
    from agents import source_description as sd

    monkeypatch.setattr(sd.gs, "chat_text", lambda *a, **k: (
        'Lass mich überlegen … {"is_care_related": true, "care_context": "Spital"}'))
    stored = sd._care_flag("text")
    assert stored["is_care_related"] is True
    assert stored["classified"] is True
