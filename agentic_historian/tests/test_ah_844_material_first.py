"""#844 — the writing material is read off the leaf, before script and date.

The third e-codices run (7.–8.11.2026) showed the material following the
dating: every parchment volume called "Papier" had been dated to the 15th
century, both paper volumes called "Pergament" too early. So the image prompt
asks for the substrate first, from visible evidence, and says that material is
never inferred from the date — in the image-only path and in the production
path that sends a transcription with the pages.

To measure the change on volumes of our own, and not only on e-codices (the
issue's scope), the bench takes a volume directory with ``reference.json`` and
its page images in place of ``tei.xml`` and a IIIF manifest.

Offline — the VLM is mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_844_material_first.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                     # noqa: E402
from agents import source_description as agent_b  # noqa: E402
from eval import ecodices_bench as eb             # noqa: E402

ANSWER = ('{"Beschreibstoff": {"wert": "Pergament", "notiz": "Follikelporen"}, '
          '"Schrift": {"wert": "Karolingische Minuskel"}, "Datierung": {"wert": "10. Jh."}}'
          "\n\n## B\nText.")
GOOD_TEXT = ("Wir Hans von Wiler tuend kund allen die disen brief ansehent "
             "oder hoerent lesen dass wir mit guotem willen")


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path / "descriptions")
    (tmp_path / "descriptions").mkdir()
    monkeypatch.setattr(agent_b, "_care_flag", lambda t: {"flag": False})


@pytest.fixture
def vlm(monkeypatch, tmp_path):
    prompts = []

    def answer(prompt, *a, **k):
        prompts.append(prompt)
        return ANSWER
    monkeypatch.setattr(agent_b.gs, "chat_vision", answer)
    monkeypatch.setattr(agent_b.gs, "chat_text", answer)
    img = tmp_path / "page.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    return prompts, str(img)


# ── the prompt ───────────────────────────────────────────────────────────────

def test_material_comes_first_and_never_from_the_date(vlm):
    prompts, img = vlm
    agent_b._describe_from_image("d", img, None, 1)
    p = prompts[0]
    material, script = p.index("1. BESCHREIBSTOFF"), p.index("Schrift (z.B. Kurrent")
    assert material < script, "the substrate is asked for before script and century"
    assert "NIE aus der Datierung" in p and "Follikelporen" in p and "Wasserzeichen" in p
    assert "Kett- und Rippenlinien" in p, "paper's evidence is named, not only parchment's"


def test_the_sent_prompt_is_the_one_the_bench_can_reuse(vlm):
    """A second reader on the bench (#844) sends what production sends."""
    prompts, img = vlm
    agent_b._describe_from_image("d", [img, img], None, 12)
    assert prompts[0] == agent_b.image_only_prompt([img, img], None, 12)


def test_the_production_path_with_pages_gets_the_same_rule(vlm):
    prompts, img = vlm
    agent_b.describe("d", GOOD_TEXT, image_path=img, page_count=1)
    assert agent_b.MATERIAL_FIRST_RULE in prompts[0]
    assert prompts[0].index("1. BESCHREIBSTOFF") < prompts[0].index("<<<TRANSKRIPTION")


def test_without_a_page_there_is_nothing_to_read_the_material_off(vlm):
    prompts, _ = vlm
    agent_b.describe("d", GOOD_TEXT)
    assert "1. BESCHREIBSTOFF" not in prompts[0]


# ── volumes of our own on the bench ─────────────────────────────────────────

def test_a_reference_of_our_own_is_read_like_a_tei():
    ref = eb.parse_reference({"script": "Kanzleikursive", "hands": 2, "date": 1528,
                              "material": "Papier", "published": "2025"}, "av-1443")
    assert (ref.script_families, ref.hands) == (["kursive"], 2)
    assert (ref.date_from, ref.date_to, ref.material) == (1528, 1528, "papier")
    assert eb.parse_reference({"date_from": 1300, "date_to": 1350, "material": "perg"},
                              "x").material == "pergament"
    bare = eb.parse_reference({}, "x")
    assert bare.script_families == [] and bare.hands is None and bare.material is None
    assert bare.date_from is None, "a key left out is not in the catalogue, never a miss"


def test_run_and_score_take_our_own_pages_without_the_network(tmp_path):
    vol = tmp_path / "av-1443"
    vol.mkdir()
    (vol / "reference.json").write_text(json.dumps({"date": 1528, "material": "papier"}))
    for i in (3, 1, 2):
        (vol / f"page-{i:04d}.jpg").write_bytes(b"\xff\xd8\xff")
    shown = []

    def describe(doc_id, images, page_count, **extra):
        shown.append((images, page_count))
        return {"source_json": {"Datierung": {"wert": "1528"}, "Beschreibstoff": {"wert": "Papier"}}}

    def no_network(url):
        raise AssertionError(f"fetched {url}")

    eb.run(tmp_path, describe=describe, runs=1, pages="first", get=no_network)
    assert shown == [([str(vol / "page-0001.jpg")], 3)]
    scores, report = eb.score(tmp_path)
    assert scores[0].judgements["Datierung"] == {eb.HIT: 1}
    assert scores[0].judgements["Beschreibstoff"] == {eb.HIT: 1}
    assert scores[0].judgements["Schrift"] == {eb.NO_REF: 1}
    assert "`av-1443`" in report


def test_the_run_keeps_what_the_reader_says_it_cost(tmp_path):
    vol = tmp_path / "v"
    vol.mkdir()
    (vol / "reference.json").write_text("{}")
    (vol / "page-0001.jpg").write_bytes(b"\xff\xd8\xff")
    eb.run(tmp_path, describe=lambda *a, **k: {"source_json": {}, "model": "m", "truncated": True,
                                              "cost": {"prompt_tokens": 9}},
           runs=1, get=None)
    saved = json.loads((vol / "runs" / "00.json").read_text())
    assert (saved["model"], saved["truncated"], saved["cost"]) == ("m", True, {"prompt_tokens": 9})
