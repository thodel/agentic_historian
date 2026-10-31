"""#772 — every element of a source description says what it rests on.

Agent B is shown page 1 (``orchestrator.py`` passes ``pages[0]``) and its prompt
asks "Ist eine oder sind mehrere Hände erkennbar?". "Eine Hand" read off page 1
of a 200-page register was stored and published like a statement about the
register. These pin:

* every element with a value carries ``quelle`` and ``basis`` (pages seen, of how
  many, and for the text elements whether the transcription was read);
* a pinned element keeps ``quelle: historiker`` (#147);
* the elements that describe the whole volume (Schreiber, Blätter, Lagen,
  Einband) made from one page of several are marked unsicher and say so, and a
  one-page document is not;
* the prompt tells the model how many pages it sees;
* the published table shows the basis beside the value.

Offline — the VLM is mocked.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                     # noqa: E402
from agents import source_description as agent_b  # noqa: E402
from element_basis import basis_line              # noqa: E402

TEXT = ("Wir Hans von Wiler tuend kund allen die disen brief ansehent "
        "oder hoerent lesen dass wir mit guotem willen")
RESPONSE = ('{"Schrift": {"wert": "Kurrent"}, "Schreiber": {"wert": "eine Hand"}, '
            '"Inhalt": "Kaufbrief", "Einband": null, "Datierung": {"wert": "16. Jh."}}'
            "\n\n## Beschreibung\nEine Urkunde in Kurrentschrift.")


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path / "descriptions")
    (tmp_path / "descriptions").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(agent_b, "_care_flag",
                        lambda t: {"is_care_related": False, "care_context": "",
                                   "care_types": [], "beteiligte": []})


@pytest.fixture
def img(tmp_path):
    p = tmp_path / "f001r.jpg"
    p.write_bytes(b"\xff\xd8\xff")
    return str(p)


@pytest.fixture
def vlm(monkeypatch):
    prompts = []

    def vision(prompt, image_source=None, system=None, **kw):
        prompts.append(prompt)
        return RESPONSE
    monkeypatch.setattr(agent_b.gs, "chat_vision", vision)
    monkeypatch.setattr(agent_b.gs, "chat_text", lambda *a, **k: RESPONSE)
    return prompts


def test_every_element_with_a_value_carries_its_source_and_basis(vlm, img):
    sj = agent_b.describe("d1", TEXT, image_path=img, page_count=200)["source_json"]
    assert sj["Schrift"]["quelle"] == "vlm"
    assert sj["Schrift"]["basis"] == {"seiten": ["f001r.jpg"], "von": 200}
    assert sj["Inhalt"] == {"wert": "Kaufbrief", "quelle": "vlm",
                            "basis": {"seiten": ["f001r.jpg"], "von": 200,
                                      "transkription": True}}
    assert sj["Einband"] is None, "no value, no claim, nothing to ground"


def test_a_volume_claim_from_one_page_of_many_is_held_to_it(vlm, img):
    sj = agent_b.describe("d1", TEXT, image_path=img, page_count=200)["source_json"]
    hand = sj["Schreiber"]
    assert hand["unsicher"] is True
    assert hand["notiz"] == "beobachtet auf 1 von 200 Seite(n); fuer den Band nicht geprueft"
    assert "unsicher" not in sj["Schrift"], "Schrift is a property of the page"


def test_a_one_page_document_is_not_flagged(vlm, img):
    sj = agent_b.describe("d1", TEXT, image_path=img, page_count=1)["source_json"]
    assert "unsicher" not in sj["Schreiber"]


def test_an_unknown_page_count_is_flagged_not_assumed_one(vlm, img):
    sj = agent_b.describe("d1", TEXT, image_path=img)["source_json"]
    assert sj["Schreiber"]["notiz"].startswith("beobachtet auf 1 von ? Seite(n)")


def test_a_pinned_element_stays_the_historians(vlm, img):
    sj = agent_b.describe("d1", TEXT, image_path=img, pins={"script": "Bastarda"},
                          page_count=200)["source_json"]
    assert sj["Schrift"]["wert"] == "Bastarda"
    assert sj["Schrift"]["quelle"] == "historiker"
    assert sj["Schrift"]["basis"] == {"historiker": True}


def test_the_prompt_says_how_many_pages_are_shown(vlm, img):
    agent_b.describe("d1", TEXT, image_path=img, page_count=200)
    assert "Du siehst 1 Seite(n) von 200 dieses Dokuments" in vlm[0]


def test_the_basis_line_and_the_published_table(vlm, img):
    from utils import publish_github
    sj = agent_b.describe("d1", TEXT, image_path=img, page_count=200)["source_json"]
    assert basis_line(sj) == "Beschreibung aus 1 von 200 Seite(n)"
    assert publish_github._basis_note(sj["Schreiber"]) == (
        " _(beobachtet auf 1 von 200 Seite(n); fuer den Band nicht geprueft)_")
    assert publish_github._basis_note(sj["Schrift"]) == ""
    md = (Path(config.DESCRIPTIONS_DIR) / "d1.md").read_text(encoding="utf-8")
    assert "Beschreibung aus 1 von 200 Seite(n)" in md


def test_the_orchestrator_passes_the_page_count(monkeypatch, tmp_path):
    import orchestrator as orch
    seen = {}

    def fake(doc_id, transcription, image_path=None, page_count=None):
        seen["page_count"] = page_count
        return {"source_json": {}}
    monkeypatch.setattr(orch.agent_b, "describe", fake)
    monkeypatch.setattr("description_cache.load", lambda key: None)
    monkeypatch.setattr("description_cache.store", lambda key, d: None)
    pages = [tmp_path / f"p{i}.jpg" for i in range(3)]
    for p in pages:
        p.write_bytes(b"x")
    orch._describe_cached("d", "text", pages, str(pages[0]), None)
    assert seen["page_count"] == 3
