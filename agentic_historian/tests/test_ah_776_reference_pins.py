"""#776 — historian pins become a growing reference collection.

A pinned script was used for that document and then forgotten: the next
document in the same hand was guessed from scratch. These pin:

* a script pin at Gate 1 files the document as an exemplar — label, who, when,
  what the VLM had said, and the features of its pages; a re-pin replaces it;
  a pin without an image on this host is not an exemplar and says so;
* the collection fills retroactively from existing ``human_overrides``;
* a new document gets a suggestion that names the exemplar document, beside
  Agent B's script; a contradiction is marked, and opens Gate 1 only with
  REFERENCE_PINS_GATE;
* leave-one-out counts how often the suggestion names the pinned label and how
  often the VLM did.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                     # noqa: E402
import reference_pins as rp       # noqa: E402
import uncertainty                # noqa: E402
from runstate import RunState     # noqa: E402

#: Two hands, as page vectors: "Bastarda" near (0, 0), "Kurrent" near (10, 10).
HAND = {"bast": [0.0, 0.0], "kurr": [10.0, 10.0], "kurx": [13.0, 13.0]}


def _images(tmp_path, doc, hand, n=3, jitter=0.1):
    """Page files whose name says the hand; the fake features read the name."""
    out = []
    for i in range(n):
        p = tmp_path / f"{doc}-{hand}-{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff")
        out.append(str(p))
    return out


def features(path):
    doc, hand, i = Path(path).stem.rsplit("-", 2)
    x, y = HAND[hand]
    return [x + 0.1 * int(i), y - 0.1 * int(i)]


@pytest.fixture
def coll(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return rp.ReferenceCollection(tmp_path / "reference_pins.jsonl")


def _state(doc, images, pins=(), vlm=None):
    st = RunState(doc_id=doc)
    st.artifacts["page_images"] = images
    if vlm:
        st.artifacts["description"] = {"source_json": {"Schrift": {"wert": vlm}}}
    for value in pins:
        st.human_overrides.append({"field": "script", "value": value, "user": "th", "ts": "t"})
    return st


def test_a_pin_files_the_document_as_an_exemplar(tmp_path, coll):
    st = _state("doc-a", _images(tmp_path, "a", "bast"))
    ex = rp.add_from_pin(st, "Bastarda", vlm_label="Kurrent", user="th", collection=coll,
                         features=features)
    assert ex.label == "Bastarda" and ex.vlm_label == "Kurrent" and ex.user == "th"
    assert len(ex.vectors) == 3 and ex.pages[0] == "a-bast-0.jpg"
    reread = rp.ReferenceCollection(coll.path)
    assert reread.exemplars["doc-a"].label == "Bastarda", "on disk, not only in memory"


def test_a_repin_replaces_the_exemplar(tmp_path, coll):
    st = _state("doc-a", _images(tmp_path, "a", "bast"))
    rp.add_from_pin(st, "Bastarda", collection=coll, features=features)
    rp.add_from_pin(st, "Textualis", collection=coll, features=features)
    assert len(coll) == 1 and coll.exemplars["doc-a"].label == "Textualis"


def test_a_pin_without_an_image_here_is_no_exemplar(coll):
    st = _state("doc-x", ["/nowhere/p1.jpg"])
    assert rp.add_from_pin(st, "Bastarda", collection=coll, features=features) is None
    assert len(coll) == 0


def test_the_collection_fills_from_existing_overrides(tmp_path, coll):
    states = [
        _state("a", _images(tmp_path, "a", "bast"), pins=["Kursive", "Bastarda"], vlm="Fraktur"),
        _state("b", _images(tmp_path, "b", "kurr"), pins=["Kurrent"]),
        _state("c", ["/nowhere.jpg"], pins=["Kurrent"]),
        _state("d", _images(tmp_path, "d", "kurr")),
    ]
    counts = rp.backfill(states, collection=coll, features=features)
    assert counts == {"added": 2, "no_image": 1, "no_pin": 1}
    assert coll.exemplars["a"].label == "Bastarda", "the last pin wins"
    assert coll.exemplars["a"].vlm_label == "Fraktur"


def test_a_new_document_gets_the_nearest_label_and_its_exemplar(tmp_path, coll):
    rp.backfill([_state("a", _images(tmp_path, "a", "bast"), pins=["Bastarda"]),
                 _state("b", _images(tmp_path, "b", "kurr"), pins=["Kurrent"])],
                collection=coll, features=features)
    new = [features(p) for p in _images(tmp_path, "n", "kurr")]
    sugg = coll.suggest(new)
    assert sugg.label == "Kurrent" and sugg.exemplar == "b"
    assert [r["vorbild"] for r in sugg.ranking] == ["b", "a"]


def test_the_suggestion_stands_beside_agent_b_and_marks_a_contradiction(tmp_path, coll,
                                                                       monkeypatch):
    rp.backfill([_state("b", _images(tmp_path, "b", "kurr"), pins=["Kurrent"])],
                collection=coll, features=features)
    desc = {"doc_id": "n", "source_json": {"Schrift": {"wert": "Bastarda"}}}
    ref = rp.attach(desc, _images(tmp_path, "n", "kurr"), collection=coll, features=features)
    assert ref["wert"] == "Kurrent" and ref["vorbild"] == "b" and ref["widerspruch"] is True
    assert desc["source_json"]["Schrift"]["wert"] == "Bastarda", "beside, never instead"

    monkeypatch.setattr(config, "REFERENCE_PINS_GATE", False)
    assert uncertainty.agent_b_unsicher(desc["source_json"]) is False
    monkeypatch.setattr(config, "REFERENCE_PINS_GATE", True)
    assert uncertainty.agent_b_unsicher(desc["source_json"]) is True


def test_agreement_is_no_contradiction(tmp_path, coll, monkeypatch):
    rp.backfill([_state("b", _images(tmp_path, "b", "kurr"), pins=["Kurrent"])],
                collection=coll, features=features)
    desc = {"doc_id": "n", "source_json": {"Schrift": {"wert": "Kurrent"}}}
    assert rp.attach(desc, _images(tmp_path, "n", "kurr"), collection=coll,
                     features=features)["widerspruch"] is False
    monkeypatch.setattr(config, "REFERENCE_PINS_GATE", True)
    assert uncertainty.agent_b_unsicher(desc["source_json"]) is False


def test_an_empty_collection_suggests_nothing(tmp_path, coll):
    desc = {"source_json": {"Schrift": {"wert": "Bastarda"}}}
    assert rp.attach(desc, _images(tmp_path, "n", "bast"), collection=coll,
                     features=features) is None
    assert "referenz" not in desc["source_json"]["Schrift"]


def test_leave_one_out_counts_the_suggestion_against_the_vlm(tmp_path, coll):
    states = [_state(f"b{i}", _images(tmp_path, f"b{i}", "bast"), pins=["Bastarda"],
                     vlm="Kursive" if i else "Bastarda") for i in range(3)]
    states += [_state(f"k{i}", _images(tmp_path, f"k{i}", "kurr"), pins=["Kurrent"],
                      vlm="Kurrent") for i in range(2)]
    rp.backfill(states, collection=coll, features=features)
    result = rp.leave_one_out(coll)
    assert result["suggested"] == 5 and result["suggestion_hits"] == 5
    assert (result["vlm_answered"], result["vlm_hits"]) == (5, 3)
    row = next(r for r in result["rows"] if r["doc_id"] == "k0")
    assert row["vorbild"] == "k1", "the exemplar is named, and it is never the document itself"


def test_a_pin_at_gate1_files_an_exemplar(tmp_path, coll, monkeypatch):
    import routing_card
    monkeypatch.setattr(rp, "vectors_for", lambda images, f=None: ([[0.0, 0.0]], ["p.jpg"]))
    monkeypatch.setattr(rp, "ReferenceCollection", lambda path=None: coll)
    st = _state("g", [str(tmp_path / "g.jpg")])
    (tmp_path / "g.jpg").write_bytes(b"\xff")
    st.criteria["script"] = "Kursive"
    routing_card.apply_criteria_change(st, "script", "Bastarda")
    assert coll.exemplars["g"].label == "Bastarda"
    assert coll.exemplars["g"].vlm_label == "Kursive", "what the model had said before the pin"


def test_a_failing_collection_never_costs_the_pin(tmp_path, monkeypatch):
    import routing_card

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(rp, "add_from_pin", boom)
    st = _state("g", [])
    routing_card.apply_criteria_change(st, "script", "Bastarda")
    assert st.criteria["script"] == "Bastarda"


def test_the_nearest_exemplar_of_a_label_is_the_one_named(tmp_path, coll):
    """Two Kurrent exemplars, one close and one further off: the suggestion names
    the close one — it is the evidence a reader would want to look at."""
    rp.backfill([_state("far", _images(tmp_path, "far", "kurx"), pins=["Kurrent"]),
                 _state("near", _images(tmp_path, "near", "kurr"), pins=["Kurrent"]),
                 _state("a", _images(tmp_path, "a", "bast"), pins=["Bastarda"])],
                collection=coll, features=features)
    sugg = coll.suggest([features(p) for p in _images(tmp_path, "n", "kurr")])
    assert sugg.label == "Kurrent" and sugg.exemplar == "near"
