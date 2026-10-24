"""#714 — what the Transkribus account sees, read-only, from a session.

On 23/24.10. it took a round to learn that the 552 corrected pages are in
36479, not in the configured upload target 2536466, and the comparison script
then matched 56 of 731 titles because 36479 writes BLB shelfmarks with spaces.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import transkribus as tk                   # noqa: E402
from mcp_atr import jobs                   # noqa: E402

COLS = [{"colId": 36479, "colName": "Lassberg", "nrOfDocuments": 731},
        {"colId": 2536466, "colName": "Lassberg_All", "nrOfDocuments": 1},
        {"colId": 2313234, "colName": "Bern_Reformation", "nrOfDocuments": 12}]
DOCS = [{"docId": 703839, "title": "done_BLB_K 3123 8 3", "nrOfPages": 6, "status": 0},
        {"docId": 1, "title": "done_BLB_K 2911,47", "nrOfPages": 4},
        {"docId": 138126, "title": "done_lassberg-letter-1404", "nrOfPages": 2},
        {"docId": 703732, "title": "TRAINING_VALIDATION_SET_Lassberg_base", "nrOfPages": 40}]


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(tk, "login", lambda session=None: "SID")
    monkeypatch.setattr(tk, "list_collections", lambda sid, session=None: COLS)
    monkeypatch.setattr(tk, "collection_documents", lambda colid, sid, session=None: DOCS)


@pytest.mark.parametrize("title,key", [
    ("done_BLB_K 3123 8 4", "K3123-8-4"), ("done_BLB_K 2911,47", "K2911-47"),
    ("K 2911, 65-67 Brief", "K2911-65-67"), ("BLB_K_2912_3", "K2912-3"),
    ("done_lassberg-letter-1404", None), ("TRAINING_VALIDATION_SET_Lassberg_base", None)])
def test_a_shelfmark_reads_the_same_in_every_spelling(title, key):
    assert tk.shelfmark_of(title) == key
    if key:
        assert tk.shelf_key(key.replace("K", "K ").replace("-", ",", 1)) == key


def test_collections_are_filtered_by_name(api):
    out = jobs.transkribus_collections_job(name="lassberg")
    assert out["visible"] == 3 and out["matching"] == 2
    assert [c["colId"] for c in out["collections"]] == [36479, 2536466]


def test_a_collection_lists_its_documents_with_their_shelfmarks(api):
    out = jobs.transkribus_collections_job(collection="36479", limit=2)
    assert out["colName"] == "Lassberg" and out["documents"] == 4 and out["with_shelfmark"] == 2
    assert out["pages"] == 52 and len(out["rows"]) == 2
    assert out["rows"][0] == {"docId": 703839, "title": "done_BLB_K 3123 8 3", "pages": 6,
                              "status": 0, "uploaded": None, "shelfmark": "K3123-8-3"}


def test_an_invisible_collection_is_named_as_such(api):
    with pytest.raises(jobs.JobError) as err:
        jobs.transkribus_collections_job(collection="99")
    assert "not visible" in str(err.value)


@pytest.mark.parametrize("kw", [dict(collection="36479; x"), dict(name=""), dict(limit=0),
                                dict(offset=-1), dict(name="x" * 65)])
def test_arguments_are_checked_before_any_call(kw):
    with pytest.raises(jobs.JobError):
        jobs.transkribus_collections_job(**kw)
