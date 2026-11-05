"""An evidence unit that says which reading it quotes (#811).

The two real fixtures are the cases the module was built for:

* ``BAT_664_r_00027`` — one page, ten engines, published text = the kraken
  early-modern-german reading (*"Iuser femitlich geñs"*), TrOCR escriptmask
  reading the same words as *"unser frùntlich gruͦs"*, kraken catmus_caroline
  returning Hebrew. Verbatim from the outputs repo.
* ``saa-0428`` — a six-page cartulary whose published ``pipeline.json`` has 48
  candidates and no page for any of them, and a VLM that writes its own
  ``--- SEITE N ---`` lines inside a page.

The Gate-2 statuses are tested on a constructed RunState with exactly the keys
``path_compare`` and ``orchestrator._record_no_merge_vote`` write.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_811_evidence.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config
import evidence as ev
from runstate import ClosestReadingText, RunState

FIX = PKG / "tests" / "fixtures" / "evidence"

ESCRIPT = "trocr/trocr-medieval-escriptmask"
EMG = "kraken/kraken-early_modern_german"
EMG2 = "kraken/kraken-early-modern-german"
CAROLINE = "kraken/kraken-catmus_caroline"


@pytest.fixture(scope="module")
def bat():
    return json.loads((FIX / "BAT_664_r_00027.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def saa():
    return json.loads((FIX / "saa-0428.json").read_text(encoding="utf-8"))


@pytest.fixture
def bat_rs(bat):
    return ev.reading_set(bat["doc_id"], pipeline=bat)


# ── BAT_664: reading set from pipeline.json alone ────────────────────────────

def test_published_reading_is_the_auto_pick_it_equals(bat_rs):
    pub = bat_rs.get(ev.PUBLISHED)
    assert pub.status == ev.AUTO_SELECTED
    # the published text is byte-identical to both kraken emg readings; with no
    # Gate-2 record, equality is all that can be said, so both are the auto pick
    assert bat_rs.get(EMG).status == ev.AUTO_SELECTED
    assert bat_rs.get(EMG2).status == ev.AUTO_SELECTED
    assert bat_rs.get(ESCRIPT).status == ev.UNCORRECTED


def test_a_reading_in_the_wrong_script_is_set_aside_not_dropped(bat_rs):
    caroline = bat_rs.get(CAROLINE)
    assert caroline is not None                      # still visible
    assert caroline.set_aside == "wrong script"
    assert caroline not in bat_rs.candidates()


def test_band_and_source_come_from_the_run(bat_rs):
    assert bat_rs.band == "no_merge"
    assert bat_rs.max_pairwise_cer > config.ENSEMBLE_NO_MERGE_CER
    assert bat_rs.source["datierung"] == "27. Juni 1429"
    assert bat_rs.source["datierung_unsicher"] is False
    assert bat_rs.source["sprachen"][0] == "de"
    assert bat_rs.unattributed == 0


# ── items ────────────────────────────────────────────────────────────────────

def test_quote_is_verbatim_from_the_named_reading(bat_rs, bat):
    it = ev.item_for_span(bat_rs, ev.PUBLISHED, 0, 21)
    assert it.quote == bat["transcription"][0:21]
    # the source writes the tilde as a combining mark (n + U+0303), not as the
    # precomposed ñ a person types; verbatim means the source's code points
    assert it.quote == "Iuser femitlich gen\u0303s"
    assert it.reading == {"engine": "pipeline", "model_id": "published",
                          "status": ev.AUTO_SELECTED}
    assert it.match == "exact"
    assert it.anchor == "passage-0-21"               # #397's contract
    json.dumps(it.to_dict(), ensure_ascii=False)     # serialisable as is


def test_contested_span_names_how_the_others_read_it(bat_rs):
    it = ev.item_for_span(bat_rs, ev.PUBLISHED, 0, 21)
    assert it.agreement == "contested"
    alts = {a["model_id"]: a["text"] for a in it.alternatives}
    assert alts["trocr-medieval-escriptmask"] == "unser frùntlich gruͦs"
    assert alts["trocr-kurrent-xvi-xvii"] == "Vnser fründlich grus"
    # a set-aside reading is never offered as an alternative
    assert "kraken-catmus_caroline" not in alts


def test_a_reading_cannot_corroborate_itself(bat_rs):
    it = ev.item_for_span(bat_rs, ev.PUBLISHED, 0, 21)
    assert set(it.identical_readings) == {EMG, EMG2}
    alts = {a["model_id"] for a in it.alternatives}
    assert "kraken-early_modern_german" not in alts


def test_alternatives_stay_near_the_span_on_a_garbage_page(bat_rs):
    # One replaced block the length of the page made the "alternative" to
    # three words the whole VLM page; it must stay about the span's size.
    it = ev.item_for_span(bat_rs, ev.PUBLISHED, 0, 21)
    for a in it.alternatives:
        assert len(a["text"].split()) <= 3 + 2, a


def test_evidence_id_names_the_reading(bat_rs):
    a = ev.item_for_span(bat_rs, ev.PUBLISHED, 0, 21)
    b = ev.item_for_span(bat_rs, ev.PUBLISHED, 0, 21)
    c = ev.item_for_span(bat_rs, EMG, 0, 21)
    assert a.evidence_id == b.evidence_id
    assert a.evidence_id != c.evidence_id            # same words, other reading
    assert a.evidence_id.startswith("ev-") and len(a.evidence_id) == 15


@pytest.mark.parametrize("key,start,end", [
    (ev.PUBLISHED, 0, 10_000),        # past the end
    (ev.PUBLISHED, 5, 5),             # empty
    (ev.PUBLISHED, -1, 4),
    ("kraken/no-such-model", 0, 5),   # no such reading
])
def test_no_item_without_something_to_point_at(bat_rs, key, start, end):
    assert ev.item_for_span(bat_rs, key, start, end) is None


def test_blank_span_is_no_item():
    rs = ev.ReadingSet("d", "", [ev.Reading("vlm", "m", "ab   cd")])
    assert ev.item_for_span(rs, "vlm/m", 2, 5) is None


# ── locate_quote ─────────────────────────────────────────────────────────────

def test_quote_found_in_one_reading_and_not_another(bat_rs):
    items = ev.locate_quote(bat_rs, "unser frùntlich")
    assert [i.reading["model_id"] for i in items] == ["trocr-medieval-escriptmask"]
    assert items[0].quote == "unser frùntlich"


def test_normalised_match_returns_the_readings_own_characters(bat_rs):
    items = ev.locate_quote(bat_rs, "fruntlich")     # without the grave
    escript = [i for i in items if i.reading["model_id"] == "trocr-medieval-escriptmask"]
    assert len(escript) == 1
    assert escript[0].match == "normalised"
    assert escript[0].quote == "frùntlich"


def test_a_typed_precomposed_letter_finds_the_combining_one(bat_rs):
    # "geñs" typed with U+00F1; the readings carry n + U+0303
    items = ev.locate_quote(bat_rs, "femitlich ge\u00f1s")
    pub = [i for i in items if i.reading["model_id"] == "published"]
    assert len(pub) == 1 and pub[0].match == "normalised"
    assert pub[0].quote == "femitlich gen\u0303s"


def test_a_quote_never_cuts_a_letter_from_its_mark(bat_rs):
    # "gen" stands verbatim inside "gen̄s" (n + U+0303); the page says "geñ"
    items = ev.locate_quote(bat_rs, "femitlich gen")
    pub = [i for i in items if i.reading["model_id"] == "published"]
    assert len(pub) == 1
    assert pub[0].match == "normalised"
    assert pub[0].quote == "femitlich geñ"


def test_a_quote_no_reading_contains_gives_nothing(bat_rs):
    assert ev.locate_quote(bat_rs, "Burgermeister und Rat") == []
    assert ev.locate_quote(bat_rs, "   ") == []


def test_every_occurrence_is_its_own_item():
    rs = ev.ReadingSet("d", "", [ev.Reading("vlm", "m", "der brief ... der brief")])
    items = ev.locate_quote(rs, "der brief")
    assert [(i.char_start, i.char_end) for i in items] == [(0, 9), (14, 23)]
    assert len({i.evidence_id for i in items}) == 2


# ── agreement ────────────────────────────────────────────────────────────────

def test_agreed_needs_an_independent_reading():
    rs = ev.ReadingSet("d", "", [
        ev.Reading("vlm", "a", "Wir Hans von Wiler tuend kund allen"),
        ev.Reading("kraken", "b", "Wir Hans von Wyler tuend kund allen"),
    ])
    agreement, alts, _ = ev.span_agreement(rs, "vlm/a", 0, 8)    # "Wir Hans"
    assert agreement == "agreed" and alts == []
    agreement, alts, _ = ev.span_agreement(rs, "vlm/a", 13, 18)  # "Wiler"
    assert agreement == "contested"
    assert alts[0]["text"] == "Wyler"


def test_nothing_to_compare_is_single_not_agreed():
    rs = ev.ReadingSet("d", "", [
        ev.Reading("vlm", "a", "Wir Hans von Wiler"),
        ev.Reading("kraken", "b", "Wir Hans von Wiler"),     # identical text
    ])
    agreement, _alts, identical = ev.span_agreement(rs, "vlm/a", 0, 8)
    assert agreement == "single"
    assert identical == ["kraken/b"]


def test_a_reading_with_nothing_there_contests():
    rs = ev.ReadingSet("d", "", [
        ev.Reading("vlm", "a", "Wir Hans von Wiler tuend kund"),
        ev.Reading("kraken", "b", "Wir Hans tuend kund"),
    ])
    agreement, alts, _ = ev.span_agreement(rs, "vlm/a", 9, 18)   # "von Wiler"
    assert agreement == "contested"
    assert alts[0]["text"] == ""


# ── Gate 2 statuses from the run state ───────────────────────────────────────

PAGE = "BAT_664_r_00027.jpg"


def _state(*, chosen=None, combined_text=None, rejected=False):
    st = RunState(doc_id="BAT_664_r_00027")
    st.artifacts["recognitions"] = [
        {"engine": "trocr", "model_id": "trocr-medieval-escriptmask",
         "text": "unser frùntlich gruͦs vor liebe", "error": "", "page": PAGE},
        {"engine": "kraken", "model_id": "kraken-early_modern_german",
         "text": "Iuser femitlich geñs uõe liebẽ", "error": "", "page": PAGE},
        {"engine": "vlm", "model_id": "internvl3-8b-instruct",
         "text": "Infer fremdlichs grüe vor liebe", "error": "", "page": PAGE},
        {"engine": "kraken", "model_id": "broken", "text": "",
         "error": "HTTP 404", "page": PAGE},
    ]
    st.gate_decisions["gate2_auto"] = {PAGE: f"{PAGE}:{EMG}"}
    st.gate_decisions["gate2_context"] = {PAGE: {"max_pairwise_cer": 0.7}}
    if chosen:
        st.closest_reading = {
            "text": ClosestReadingText(combined_text or "unser frùntlich gruͦs vor liebe"),
            "chosen": [f"{PAGE}:{c}" for c in chosen],
            "combined": len(chosen) > 1,
            "status": "revisable_editorial_choice",
        }
    if rejected:
        st.artifacts["paths"] = {f"{PAGE}:{EMG}": "Iuser femitlich geñs uõe liebẽ"}
        st.gate_decisions["gate2_rejected"] = True
    return st


def test_without_a_decision_the_auto_pick_is_auto_selected():
    rs = ev.reading_set("BAT_664_r_00027", PAGE, state=_state())
    assert rs.get(EMG).status == ev.AUTO_SELECTED
    assert rs.get(ESCRIPT).status == ev.UNCORRECTED
    assert rs.get("kraken/broken") is None           # an error is not a reading
    assert rs.band == "no_merge" and rs.max_pairwise_cer == 0.7


def test_a_single_choice_is_historian_chosen_and_the_rest_not_chosen():
    rs = ev.reading_set("BAT_664_r_00027", PAGE, state=_state(chosen=[ESCRIPT]))
    assert rs.get(ESCRIPT).status == ev.HISTORIAN_CHOSEN
    assert rs.get(EMG).status == ev.NOT_CHOSEN      # the overruled auto pick
    assert rs.get("vlm/internvl3-8b-instruct").status == ev.NOT_CHOSEN
    assert rs.get(ev.COMBINED) is None


def test_a_combination_is_its_own_reading():
    fused = "unser frùntlich gruͦs vor liebe getrüwe"
    rs = ev.reading_set("BAT_664_r_00027", PAGE,
                        state=_state(chosen=[ESCRIPT, "vlm/internvl3-8b-instruct"],
                                     combined_text=fused))
    comb = rs.get(ev.COMBINED)
    assert comb.text == fused and comb.status == ev.HISTORIAN_COMBINED
    assert rs.get(ESCRIPT).status == ev.HISTORIAN_COMBINED
    assert rs.get(EMG).status == ev.NOT_CHOSEN
    # derived from the candidates, so it never corroborates them
    assert comb not in rs.candidates()


def test_rejecting_every_reading_leaves_none_chosen():
    rs = ev.reading_set("BAT_664_r_00027", PAGE, state=_state(rejected=True))
    assert {r.status for r in rs.readings} == {ev.NOT_CHOSEN}


def test_a_decision_on_another_page_does_not_leak():
    st = _state(chosen=[ESCRIPT])
    st.closest_reading["chosen"] = [f"other_page.jpg:{ESCRIPT}"]
    st.artifacts["recognitions"].append(
        {"engine": "trocr", "model_id": "trocr-medieval-escriptmask",
         "text": "anderer Text", "error": "", "page": "other_page.jpg"})
    rs = ev.reading_set("BAT_664_r_00027", PAGE, state=st)
    assert rs.get(ESCRIPT).status == ev.UNCORRECTED
    assert rs.get(ESCRIPT).text.startswith("unser")


def test_ground_truth_is_only_what_the_caller_supplies(bat):
    rs = ev.reading_set(bat["doc_id"], pipeline=bat,
                        ground_truth={"gt/transkribus": "Unser fründlich gruß vor liebe"})
    assert rs.get("gt/transkribus").status == ev.GROUND_TRUTH
    assert not any(r.status == ev.GROUND_TRUTH for r in rs.readings
                   if r.key != "gt/transkribus")


# ── saa-0428: several pages, no page on any candidate ────────────────────────

def test_seite_lines_written_by_the_vlm_are_not_pages(saa):
    pages = [p for p, _, _ in ev._published_sections(saa["transcription"])]
    assert "SEITE 1" not in pages and "SEITE 19" not in pages
    assert pages == ["e-codices_saa-0428_001r_large.jpg",
                     "e-codices_saa-0428_015v_large.jpg",
                     "e-codices_saa-0428_016r_large.jpg",
                     "e-codices_saa-0428_016v_large.jpg",
                     "e-codices_saa-0428_017r_large.jpg",
                     "e-codices_saa-0428_030r_large.jpg"]


def test_candidates_without_a_page_are_counted_not_guessed(saa):
    rs = ev.reading_set(saa["doc_id"], "e-codices_saa-0428_016r_large.jpg",
                        pipeline=saa)
    assert rs.unattributed == 48
    assert [r.key for r in rs.readings] == [ev.PUBLISHED]
    # nothing to tell a pick from a fusion, and nothing to measure
    assert rs.get(ev.PUBLISHED).status == ev.UNCORRECTED
    assert rs.band == "unmeasurable" and rs.max_pairwise_cer is None
    assert set(rs.source["sprachen"]) == {"de", "la"}


# ── passage_index rows ───────────────────────────────────────────────────────

def test_passage_row_reads_as_the_published_item(bat_rs, bat):
    row = {"doc_id": bat["doc_id"], "char_start": 0, "char_end": 21,
           "text": bat["transcription"][0:21]}
    it = ev.item_from_passage_row(row, pipeline=bat)
    assert it.evidence_id == ev.item_for_span(bat_rs, ev.PUBLISHED, 0, 21).evidence_id


def test_a_stale_passage_row_is_no_item(bat):
    row = {"doc_id": bat["doc_id"], "char_start": 0, "char_end": 21,
           "text": "unser frùntlich gruͦs"}
    assert ev.item_from_passage_row(row, pipeline=bat) is None


def test_passage_row_on_a_later_page_keeps_its_anchor(saa):
    full = saa["transcription"]
    start = full.index("Attætam")
    row = {"doc_id": saa["doc_id"], "char_start": start, "char_end": start + 7,
           "text": "Attætam"}
    it = ev.item_from_passage_row(row, pipeline=saa)
    assert it.page == "e-codices_saa-0428_016r_large.jpg"
    assert it.quote == "Attætam"
    assert it.char_start == 0                        # inside the page's reading
    assert it.anchor == f"passage-{start}-{start + 7}"   # in the whole text, #397
