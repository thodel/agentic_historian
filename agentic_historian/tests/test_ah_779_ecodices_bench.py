"""#779 — Agent B against e-codices' catalogue descriptions.

``fetch`` and ``run`` need the network and the VLM and run on tei; everything
that judges lives in ``score`` and is pinned here, offline, on synthetic TEI in
e-codices' shape (``eval/fixtures/ecodices``):

* the TEI parser reads licence, publication date, script families, number of
  hands (``@hands`` or counted ``handNote``), date range and material;
* Agent B's free text is normalised: script family, a year range from
  "15. Jh."/"Mitte 15. Jh."/"um 1450", a hand count, a material;
* a run is judged per element as hit, miss, no answer, or not in the catalogue —
  a catalogue that names no script cannot make Agent B wrong;
* "gotisch" is the generic family: it neither beats nor loses to "Textura";
* stability is the share of runs giving the most common answer;
* the report and scores.json come out of a bench directory;
* ``run`` shows Agent B the first page only (as production does) and never the
  TEI; ``spread`` is refused until #771.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from eval import ecodices_bench as eb  # noqa: E402

FIX = PKG / "eval" / "fixtures" / "ecodices"


def _tei(name):
    return (FIX / f"{name}.xml").read_bytes()


def _sj(schrift=None, datierung=None, schreiber=None, stoff=None):
    return {"Schrift": {"wert": schrift}, "Datierung": {"wert": datierung},
            "Schreiber": {"wert": schreiber}, "Beschreibstoff": {"wert": stoff}}


# ── the catalogue side ───────────────────────────────────────────────────────

def test_the_tei_is_read_for_what_score_compares():
    ref = eb.parse_tei(_tei("paper-two-hands"), "saa-x")
    assert ref.licence == "https://creativecommons.org/licenses/by/3.0/"
    assert ref.published == "2026-03-01"
    assert ref.script_families == ["bastarda", "kursive"]
    assert (ref.hands, ref.date_from, ref.date_to) == (2, 1450, 1470)
    assert ref.material == "papier" and ref.leaves == "II + 210 Bl."


def test_hands_are_counted_from_handnotes_and_when_is_both_ends():
    ref = eb.parse_tei(_tei("parchment-gothic"), "csg-x")
    assert ref.hands == 1 and (ref.date_from, ref.date_to) == (1320, 1320)
    assert ref.material == "pergament" and ref.script_families == ["textualis", "gotisch"]
    assert ref.licence == "https://creativecommons.org/publicdomain/mark/1.0/"


# ── Agent B's side ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("15. Jh.", (1401, 1500)), ("14./15. Jh.", (1301, 1500)),
    ("1. Hälfte 15. Jh.", (1401, 1450)), ("Mitte 15. Jh.", (1426, 1475)),
    ("Ende 15. Jh.", (1468, 1500)), ("um 1450", (1440, 1460)),
    ("1450–1460", (1450, 1460)), ("XV. Jh.", (1401, 1500)), ("unbekannt", None),
])
def test_a_dating_becomes_a_year_range(text, expected):
    assert eb.date_range(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("eine Hand", "1"), ("auf der gezeigten Seite eine Hand", "1"),
    ("zwei Hände", "2"), ("mehrere Hände", ">1"), ("Haupthand mit Nachträgen", None),
])
def test_a_hand_count(text, expected):
    assert eb.hand_count(text) == expected


# ── judging ──────────────────────────────────────────────────────────────────

def test_a_run_is_judged_per_element():
    ref = eb.parse_tei(_tei("paper-two-hands"), "saa-x")
    j = eb.judge(ref, _sj("Bastarda", "Mitte 15. Jh.", "eine Hand", "Papier"))
    assert j == {"Schrift": eb.HIT, "Datierung": eb.HIT, "Schreiber": eb.MISS,
                 "Beschreibstoff": eb.HIT}
    j = eb.judge(ref, _sj("Karolingische Minuskel", "12. Jh.", "mehrere Hände", None))
    assert j == {"Schrift": eb.MISS, "Datierung": eb.MISS, "Schreiber": eb.HIT,
                 "Beschreibstoff": eb.NO_ANSWER}


def test_a_catalogue_that_names_nothing_cannot_make_agent_b_wrong():
    ref = eb.Reference(id="x")
    assert set(eb.judge(ref, _sj("Textura", "15. Jh.", "eine Hand", "Papier")).values()) == {
        eb.NO_REF}


@pytest.mark.parametrize("answer,catalogue,hit", [
    ("textualis", ["gotisch"], True),        # catalogue generic, answer specific
    ("gotisch", ["textualis", "gotisch"], True),
    ("kursive", ["textualis", "gotisch"], False),   # two specific names differ
    ("karolingisch", ["gotisch"], False),
])
def test_gotisch_is_the_generic_family(answer, catalogue, hit):
    assert eb.script_matches(answer, catalogue) is hit


def test_stability_is_the_share_of_the_most_common_answer():
    ref = eb.parse_tei(_tei("paper-two-hands"), "saa-x")
    runs = [_sj("Bastarda", "15. Jh."), _sj("Gotische Textura"), _sj("Bastarda", "15. Jh.")]
    vs = eb.score_volume(ref, runs)
    assert vs.stability["Schrift"] == pytest.approx(0.667)
    assert vs.stability["Datierung"] == pytest.approx(0.667), "a run that says nothing is not stable"
    assert vs.judgements["Schrift"] == {eb.HIT: 2, eb.MISS: 1}
    assert vs.stability["Schreiber"] is None, "no run answered"


def test_score_writes_the_report_and_the_numbers(tmp_path):
    for name, vol in (("paper-two-hands", "saa-x"), ("parchment-gothic", "csg-x")):
        (tmp_path / vol / "runs").mkdir(parents=True)
        (tmp_path / vol / "tei.xml").write_bytes(_tei(name))
        for r, sj in enumerate([_sj("Bastarda", "15. Jh.", "zwei Hände", "Papier"),
                                _sj("Textura", "14. Jh.", "eine Hand", "Pergament")]):
            (tmp_path / vol / "runs" / f"{r:02d}.json").write_text(json.dumps({"source_json": sj}))
    (tmp_path / "meta.json").write_text(json.dumps({"runs": 2, "pages": "first"}))
    scores, report = eb.score(tmp_path)
    assert [s.id for s in scores] == ["csg-x", "saa-x"]
    summary = json.loads((tmp_path / "scores.json").read_text())["summary"]
    assert summary["Schreiber"] == {"hit": 2, "miss": 2, "no_answer": 0, "not_in_catalogue": 0,
                                    "hit_rate": 0.5, "mean_stability": 0.5}
    assert "| Schreiber | 2 | 2 | 0 | 0 | 50% | 50% |" in report
    assert "| `saa-x` | 2026-03-01 |" in report


# ── run: first page, never the TEI ───────────────────────────────────────────

MANIFEST_V2 = {"seeAlso": "https://e-codices.ch/xml/tei_published/x_CMDCH.xml",
               "sequences": [{"canvases": [
                   {"images": [{"resource": {"service": {"@id": f"https://img/x/{i}"}}}]}
                   for i in range(12)]}]}


def test_the_manifest_gives_pages_and_the_tei():
    assert eb.canvases(MANIFEST_V2)[0] == "https://img/x/0/full/!1600,1600/0/default.jpg"
    assert len(eb.canvases(MANIFEST_V2)) == 12
    assert eb.tei_url(MANIFEST_V2) == "https://e-codices.ch/xml/tei_published/x_CMDCH.xml"
    assert eb.pick_pages(12, "first") == [0]
    assert eb.pick_pages(12, "spread") == [0, 3, 6, 8, 11]


def test_fetch_then_run_shows_agent_b_the_first_page_and_not_the_tei(tmp_path):
    got = []

    def get(url):
        got.append(url)
        if url.endswith("manifest.json"):
            return json.dumps(MANIFEST_V2).encode()
        if url.endswith(".xml"):
            return _tei("paper-two-hands")
        return b"\xff\xd8\xff"

    assert eb.fetch(["saa-x"], tmp_path, get=get) == ["saa-x"]
    seen = []

    def describe(doc_id, image, page_count):
        seen.append((image, page_count))
        assert b"<TEI" not in Path(image).read_bytes()
        return {"source_json": _sj("Bastarda")}

    eb.run(tmp_path, describe=describe, runs=2, get=get)
    assert [Path(i).name for i, _ in seen] == ["page-0000.jpg", "page-0000.jpg"]
    assert {n for _, n in seen} == {12}
    assert sorted(p.name for p in (tmp_path / "saa-x" / "runs").iterdir()) == ["00.json", "01.json"]
    with pytest.raises(SystemExit, match="#771"):
        eb.run(tmp_path, describe=describe, runs=1, pages="spread", get=get)
