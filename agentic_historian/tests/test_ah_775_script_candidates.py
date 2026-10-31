"""#775 — Schrift as a ranked list with evidence, not one label.

Schrift drives model selection, and the same images were "Kursive", "Fraktur" and
"Gotische Textura" in three runs (#379). Agent B now returns up to three candidates
with a confidence and the letterforms behind each. These pin:

* the candidate list is validated: named entries only, confidence clamped to 0..1,
  best first, at most three; a malformed list is dropped, never repaired into a
  ranking nobody made; ``wert`` is the first candidate;
* a pin replaces the list: a historian's value is not one candidate among the
  model's;
* the prompt asks for the list in both paths (with and without a transcription);
* the model selector keeps a close runner-up in contention as a secondary script
  and ignores a distant one;
* Gate 1 opens on a 55/45 call between two scripts and not on a 90/10 one;
* readers of ``Schrift.wert`` alone run unchanged.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                     # noqa: E402
import uncertainty                                # noqa: E402
from agent_a import model_selector as ms          # noqa: E402
from agents import source_description as agent_b  # noqa: E402

TEXT = ("Wir Hans von Wiler tuend kund allen die disen brief ansehent "
        "oder hoerent lesen dass wir mit guotem willen")


def _schrift(*cands, wert=None):
    el = {"kandidaten": [{"schrift": s, "zuversicht": z, "merkmale": m}
                         for s, z, m in cands]}
    if wert:
        el["wert"] = wert
    return el


# ── validation ───────────────────────────────────────────────────────────────

def test_the_list_is_sorted_clamped_and_cut_to_three():
    el = {"kandidaten": [
        {"schrift": "Fraktur", "zuversicht": 0.2},
        {"schrift": "Kursive", "zuversicht": 1.7, "merkmale": ["langes s"]},
        {"schrift": "", "zuversicht": 0.9},                 # no name: dropped
        "Textura",                                           # not an object: dropped
        {"schrift": "Bastarda", "zuversicht": "viel"},       # no number: kept, unknown
        {"schrift": "Textura", "zuversicht": 0.1},
    ]}
    got = agent_b.script_candidates(el)
    assert [c["schrift"] for c in got] == ["Kursive", "Fraktur", "Textura"]
    assert got[0] == {"schrift": "Kursive", "zuversicht": 1.0, "merkmale": ["langes s"]}


def test_wert_becomes_the_first_candidate_and_no_list_is_no_list():
    sj = agent_b._rank_script({"Schrift": _schrift(("Bastarda", 0.6, []), ("Kursive", 0.3, []),
                                                   wert="Kursive")})
    assert sj["Schrift"]["wert"] == "Bastarda"
    plain = agent_b._rank_script({"Schrift": {"wert": "Kurrent", "kandidaten": "Kurrent"}})
    assert plain["Schrift"] == {"wert": "Kurrent"}


# ── through describe() ───────────────────────────────────────────────────────

RESPONSE = ('{"Schrift": {"wert": "Kursive", "kandidaten": ['
            '{"schrift": "Bastarda", "zuversicht": 0.55, "merkmale": ["Schleifen"]},'
            '{"schrift": "Kursive", "zuversicht": 0.45, "merkmale": []}]}}'
            "\n\n## Beschreibung\nText.")


@pytest.fixture
def vlm(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path / "d")
    (tmp_path / "d").mkdir()
    monkeypatch.setattr(agent_b, "_care_flag", lambda t: {"is_care_related": False})
    prompts = []

    def answer(prompt, *a, **k):
        prompts.append(prompt)
        return RESPONSE
    monkeypatch.setattr(agent_b.gs, "chat_vision", answer)
    monkeypatch.setattr(agent_b.gs, "chat_text", answer)
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    return prompts, str(img)


def test_describe_ranks_and_the_prompt_asks_for_the_list(vlm):
    prompts, img = vlm
    sj = agent_b.describe("d", TEXT, image_path=img, page_count=1)["source_json"]
    assert sj["Schrift"]["wert"] == "Bastarda"
    assert [c["schrift"] for c in sj["Schrift"]["kandidaten"]] == ["Bastarda", "Kursive"]
    assert '"kandidaten"' in prompts[0] and "zuversicht" in prompts[0]


def test_the_image_only_path_asks_for_the_list_too(vlm):
    prompts, img = vlm
    agent_b._describe_from_image("d", img, None, 1)
    assert '"kandidaten"' in prompts[0]


def test_a_pin_replaces_the_list(vlm):
    _, img = vlm
    sj = agent_b.describe("d", TEXT, image_path=img, pins={"script": "Textura"},
                          page_count=1)["source_json"]
    assert sj["Schrift"]["wert"] == "Textura" and "kandidaten" not in sj["Schrift"]


# ── model selection ──────────────────────────────────────────────────────────

def test_a_close_runner_up_stays_in_contention_a_distant_one_does_not():
    close = ms.SourceCriteria.from_source_json(
        {"Schrift": _schrift(("Bastarda", 0.55, []), ("Kursive", 0.45, []), wert="Bastarda")})
    far = ms.SourceCriteria.from_source_json(
        {"Schrift": _schrift(("Bastarda", 0.9, []), ("Kursive", 0.1, []), wert="Bastarda")})
    plain = ms.SourceCriteria.from_source_json({"Schrift": {"wert": "Bastarda"}})
    assert ms.normalise_scripts(close.script)[1:], "the runner-up is a secondary script"
    assert ms.normalise_scripts(far.script) == ms.normalise_scripts(plain.script)


def test_the_runner_ups_model_scores_as_secondary_not_as_a_mismatch():
    close = ms.SourceCriteria.from_source_json(
        {"Schrift": _schrift(("Textura", 0.5, []), ("Kurrent", 0.4, []), wert="Textura")})
    kurrent = next(m for m in ms.KRAKEN_MODELS.values()
                   if m.script and ms.normalise_script(m.script) == ms.normalise_script("Kurrent"))
    match = ms.score_model(kurrent, script=close.script)
    assert "script2" in match.matched_on and "script-mismatch" not in match.matched_on


# ── Gate 1 ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("top,second,opens", [(0.55, 0.45, True), (0.9, 0.1, False)])
def test_gate1_opens_on_a_close_call(top, second, opens):
    sj = {"Schrift": _schrift(("Bastarda", top, []), ("Kursive", second, []), wert="Bastarda")}
    assert uncertainty.agent_b_unsicher(sj) is opens


def test_a_single_label_without_unsicher_does_not_open_gate1():
    assert uncertainty.agent_b_unsicher({"Schrift": {"wert": "Bastarda"}}) is False
    assert uncertainty.script_margin({"Schrift": {"wert": "Bastarda"}}) is None
