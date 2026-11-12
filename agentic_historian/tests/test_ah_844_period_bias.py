"""#844 — Agent B's image path: a period floor, and the material following the date.

The third e-codices run (tei, 7.11.2026: 30 volumes x 3 runs, the text block,
``--pages spread``) showed three things about Agent B's image-only path:

* it never dated below the 13th century — all 11 volumes before 1100 came out
  13th-15th c.; Datierung hit 7 of 90 runs (8 %), stable in 88 %;
* it never named Carolingian minuscule, although the prompt gives it as an
  example;
* the writing material followed its dating, not the leaf: all 7 parchment
  volumes it called "Papier" it had dated to the 15th c., and the 2 paper
  volumes it called "Pergament" it had dated too early (Beschreibstoff 67 %).

What is pinned here, offline (the VLM is a fake; no gateway, no e-codices):

* the production prompt is unchanged, byte for byte, unless a variant is asked
  for — by ``AGENT_B_IMAGE_PROMPT`` or by ``ecodices_bench run --prompt``;
* the ``material-first`` variant asks for the substrate from visible evidence
  before script and date, forbids inferring it from the dating, and keeps
  Carolingian minuscule a named possibility;
* every run records its variant, the score is split by it, and a run written
  before the field existed counts as the default prompt;
* scoring the old runs again (#842's century spans) calls no model;
* one of our own volumes (``reference.json`` + ``pages/``) runs and scores like
  an e-codices volume, so the variant is measured on our ground truth too.

Not here: the external reader of #747 (``external_atr``) as a describer. It
transcribes — one page, a fixed prompt file, page text back — and has no way to
answer Agent B's prompt; see docs/EVALUATION_HARNESS.md.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config
import description_cache as dc
from agents import source_description as agent_b
from agents.source_heuristic import MANUSCRIPT_SYSTEM
from eval import ecodices_bench as eb

FIX = PKG / "eval" / "fixtures" / "ecodices"
DEGENERATE = "uuuu uuuu uuuu uuuu\nuuuuuuuuuuuu\niuuuuuie uuuu"   # as in test_ah_301
VLM = ('{"Beschreibstoff": {"wert": "Pergament"}, "Schrift": {"wert": "Karolingische Minuskel"}, '
       '"Datierung": {"wert": "9. Jh."}}\n\n## B\nText.')


@pytest.fixture(autouse=True)
def _offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path / "descriptions")
    monkeypatch.setattr(config, "AGENT_B_IMAGE_PROMPT", "default")
    (tmp_path / "descriptions").mkdir()


@pytest.fixture
def pages(tmp_path):
    out = []
    for i in range(3):
        p = tmp_path / f"page-{i:04d}.jpg"
        p.write_bytes(b"\xff\xd8\xff" + bytes([i]))
        out.append(str(p))
    return out


def _capture(monkeypatch):
    sent = []

    def vision(prompt, image_source=None, **kw):
        sent.append(prompt)
        return VLM
    monkeypatch.setattr(agent_b.gs, "chat_vision", vision)
    return sent


def _before_844(images, pins, page_count, page_roles):
    """The image-only prompt as ``_describe_from_image`` built it before #844, verbatim."""
    return (
        f"{MANUSCRIPT_SYSTEM}\n\n"
        f"{agent_b._pin_constraint(pins)}"
        f"{agent_b._basis_instruction(len(images), page_count, agent_b._roles_instruction(images, page_roles))}"
        "Die maschinelle Transkription dieses Dokuments ist unbrauchbar "
        "(Wiederholungskollaps) und wird dir daher NICHT vorgelegt.\n"
        "Beschreibe die Quelle AUSSCHLIESSLICH anhand des Bildes.\n\n"
        "Am wichtigsten sind Schrift (z.B. Kurrent, Bastarda, Karolingische "
        "Minuskel) und Jahrhundert — sie steuern die Modellauswahl fuer einen "
        "erneuten Transkriptionsversuch.\n\n"
        "Rate NICHT den Textinhalt. Was du im Bild nicht siehst, ist null.\n\n"
        "Antworte ZUERST mit einem JSON-Objekt (Schema unten), DANN mit einem "
        "kurzen Fliesstext in Markdown.\n\n"
        "JSON-Schema (16 Elemente):\n"
        + json.dumps(agent_b.SIXTEEN_ELEMENT_SCHEMA, indent=2, ensure_ascii=False)
        + "\n\nRegeln:\n"
        "- Verwende KEINE Markdown-Codefences um das JSON.\n"
        "- Nicht beobachtbare Felder: null bzw. leerer String/[].\n"
        "- Unsichere Angaben mit \" (unsicher)\" kennzeichnen.\n"
        + agent_b.SCRIPT_CANDIDATES_RULE
    )


# ── (1) the prompt ───────────────────────────────────────────────────────────

def _cases(pages):
    return [(pages[0], None, None, None), (pages[0], {}, 12, None), (pages, None, 30, None),
            (pages, {"century": 15, "script": "Bastarda"}, 30,
             {"page-0000.jpg": "typisch", "page-0002.jpg": "untypisch"})]


def test_the_default_prompt_is_byte_identical_to_the_one_before(monkeypatch, pages):
    sent = _capture(monkeypatch)
    for images, pins, n, roles in _cases(pages):
        result = agent_b._describe_from_image("d", images, pins, n, roles)
        imgs = images if isinstance(images, list) else [images]
        assert sent[-1] == _before_844(imgs, pins, n, roles)
        assert result["prompt_variant"] == "default"


def test_the_variant_asks_for_the_material_from_the_leaf_before_script_and_date(monkeypatch, pages):
    sent = _capture(monkeypatch)
    result = agent_b._describe_from_image("d", pages, None, 30, None, prompt_variant="material-first")
    prompt = sent[-1]
    assert result["prompt_variant"] == "material-first"
    assert prompt != _before_844(pages, None, 30, None)
    focus = prompt[prompt.index("Gehe in dieser Reihenfolge"):prompt.index("Rate NICHT")]
    material, script, date = (focus.index("1. Beschreibstoff"), focus.index("2. Schrift"),
                              focus.index("3. Jahrhundert"))
    assert material < script < date, "substrate first, then script, the date last"
    for evidence in ("Haar- und Fleischseite", "Haarfollikeln", "Durchscheinigkeit",
                     "Kettlinien", "Ripplinien", "Wasserzeichen"):
        assert evidence in focus[material:script], evidence
    assert "NICHT aus deiner Datierung" in focus
    assert "Karolingische Minuskel" in focus[script:date], "the early hand stays a named option"
    assert "Die Datierung folgt der Schrift, nicht umgekehrt" in focus
    # everything around the focus is the production prompt's
    before = _before_844(pages, None, 30, None)
    assert prompt.startswith(before[:before.index("Am wichtigsten")])
    assert prompt.endswith(before[before.index("Rate NICHT"):])


def test_the_config_chooses_the_variant_and_an_unknown_one_is_the_default(monkeypatch, pages):
    sent = _capture(monkeypatch)
    monkeypatch.setattr(config, "AGENT_B_IMAGE_PROMPT", "material-first")
    # production's way in: a degenerate transcription sends describe() to the image path
    result = agent_b.describe("d", DEGENERATE, image_path=pages[0])
    assert result["prompt_variant"] == "material-first" and "1. Beschreibstoff" in sent[-1]
    monkeypatch.setattr(config, "AGENT_B_IMAGE_PROMPT", "materiel-first")
    result = agent_b._describe_from_image("d", pages[0], None)
    assert result["prompt_variant"] == "default", "a typo is not a measurement of the variant"
    assert sent[-1] == _before_844([pages[0]], None, None, None)


def test_the_bench_offers_exactly_the_agents_variants():
    assert eb.PROMPT_CHOICES == agent_b.IMAGE_PROMPT_VARIANTS
    assert eb.DEFAULT_PROMPT == agent_b.IMAGE_PROMPT_VARIANTS[0] == "default"


def test_a_variant_does_not_share_the_description_cache_with_the_default(monkeypatch, pages):
    default = dc.content_key(pages, shown=pages)
    monkeypatch.setattr(config, "AGENT_B_IMAGE_PROMPT", "material-first")
    assert dc.content_key(pages, shown=pages) not in ("", default)
    monkeypatch.setattr(config, "AGENT_B_IMAGE_PROMPT", "default")
    assert dc.content_key(pages, shown=pages) == default


def test_the_bench_passes_the_variant_to_agent_bs_real_image_path(monkeypatch, pages):
    sent = _capture(monkeypatch)
    out = eb._agent_b_image_only("v-r0", pages, 30, prompt_variant="material-first")
    assert out["prompt_variant"] == "material-first" and "1. Beschreibstoff" in sent[-1]
    out = eb._agent_b_image_only("v-r0", pages, 30)
    assert out["prompt_variant"] == "default" and sent[-1] == _before_844(pages, None, 30, None)


# ── the run records its variant ──────────────────────────────────────────────

MANIFEST = {"sequences": [{"canvases": [
    {"label": f"{i + 1}r", "images": [{"resource": {"service": {"@id": f"https://img/x/{i}"}}}]}
    for i in range(6)]}]}


def _fetched(root: Path, vol="saa-x", tei="paper-two-hands"):
    (root / vol).mkdir(parents=True)
    (root / vol / "manifest.json").write_text(json.dumps(MANIFEST))
    (root / vol / "tei.xml").write_bytes((FIX / f"{tei}.xml").read_bytes())


def test_each_run_records_its_variant_beside_the_defaults(tmp_path):
    bench = tmp_path / "bench"
    _fetched(bench)
    calls = []

    def describe(doc_id, images, page_count, **kw):
        calls.append((doc_id, kw.get("prompt_variant")))
        return {"source_json": {"Schrift": {"wert": "Bastarda"}},
                "prompt_variant": kw.get("prompt_variant") or "default"}

    get = lambda url: b"\xff\xd8\xff"
    eb.run(bench, describe=describe, runs=2, pages="spread", get=get)
    eb.run(bench, describe=describe, runs=2, pages="spread", get=get, prompt="material-first")
    eb.run(bench, describe=describe, runs=2, pages="spread", get=get, prompt="material-first")
    assert calls == [("saa-x-r0", None), ("saa-x-r1", None),
                     ("saa-x-material-first-r0", "material-first"),
                     ("saa-x-material-first-r1", "material-first")], "and nothing twice"
    runs = bench / "saa-x" / "runs"
    assert sorted(p.name for p in runs.iterdir()) == [
        "00.json", "01.json", "material-first-00.json", "material-first-01.json"]
    assert json.loads((runs / "00.json").read_text())["prompt"] == "default"
    assert json.loads((runs / "material-first-01.json").read_text())["prompt"] == "material-first"


def test_meta_keeps_the_defaults_settings_and_one_entry_per_variant(tmp_path):
    old = {"runs": 3, "pages": "spread", "agent_b": {"temperature": 0.0}}
    (tmp_path / "meta.json").write_text(json.dumps(old))         # the third run's, before #844
    meta = eb.write_meta(tmp_path, "material-first", {"runs": 3, "pages": "spread",
                                                      "prompt": "material-first"})
    assert {k: meta[k] for k in old} == old, "the default's report keeps its settings"
    assert meta["variants"]["material-first"]["prompt"] == "material-first"
    assert eb.meta_for(meta, "material-first")["prompt"] == "material-first"
    assert "prompt" not in eb.meta_for(meta, "default")


# ── score: split by variant; the old runs again, without a model ─────────────

def _sj(schrift=None, datierung=None, stoff=None, schreiber=None):
    return {"Schrift": {"wert": schrift}, "Datierung": {"wert": datierung},
            "Schreiber": {"wert": schreiber}, "Beschreibstoff": {"wert": stoff}}


def _write_runs(vdir: Path, answers, prompt=None):
    (vdir / "runs").mkdir(exist_ok=True)
    for r, sj in enumerate(answers):
        rec = {"source_json": sj}
        if prompt is not None:
            rec["prompt"] = prompt
        (vdir / "runs" / eb.run_name(r, prompt or "default")).write_text(json.dumps(rec))


def test_score_reports_each_variant_on_its_own_and_side_by_side(tmp_path):
    _fetched(tmp_path)                               # paper, 1450-1470
    vdir = tmp_path / "saa-x"
    _write_runs(vdir, [_sj("Bastarda", "13. Jh.", "Pergament")] * 3)                # before #844
    _write_runs(vdir, [_sj("Bastarda", "15. Jh.", "Papier")] * 2 + [_sj("Bastarda", "15. Jh.")],
                prompt="material-first")
    (tmp_path / "meta.json").write_text(json.dumps({"runs": 3, "pages": "spread"}))
    scores, report = eb.score(tmp_path)
    assert [s.runs for s in scores] == [3], "the default's runs, not mixed with the variant's"
    data = json.loads((tmp_path / "scores.json").read_text())
    assert list(data["by_prompt"]) == ["default", "material-first"]
    assert data["summary"] == data["by_prompt"]["default"]["summary"]
    assert data["by_prompt"]["default"]["summary"]["Datierung"]["hit_rate"] == 0.0
    var = data["by_prompt"]["material-first"]["summary"]
    assert var["Datierung"]["hit_rate"] == 1.0
    assert var["Beschreibstoff"]["mean_stability"] == pytest.approx(0.667), \
        "a variant's stability is its own runs'"
    assert "Prompt-Varianten im Vergleich (#844)" in report
    assert "| Datierung | 0% (100%) | 100% (100%) |" in report
    assert "| Beschreibstoff falsch (davon mit falscher Datierung) | 3 (3) | 0 (0) |" in report
    assert "— Prompt `material-first`" in report


def test_the_old_runs_are_scored_again_without_a_model_or_the_network(tmp_path, monkeypatch):
    """runs/ecodices-2026-11 predates #844 and #842: no ``prompt`` in its runs or meta."""
    def boom(*a, **k):
        raise AssertionError("re-scoring called out")
    for target, name in ((agent_b.gs, "chat"), (agent_b.gs, "chat_vision"),
                         (agent_b.gs, "chat_text"), (agent_b, "_vision_call"),
                         (agent_b, "_describe_from_image"), (eb, "_http_get"),
                         (eb, "_agent_b_image_only")):
        monkeypatch.setattr(target, name, boom)
    import httpx
    import openai
    monkeypatch.setattr(httpx, "Client", boom)
    monkeypatch.setattr(openai, "OpenAI", boom)

    bench = tmp_path / "ecodices-2026-11"
    _fetched(bench)                                   # paper, 1450-1470
    _write_runs(bench / "saa-x", [_sj("Bastarda", "14. Jh. - 15. Jh.", "Papier")] * 3)
    (bench / "meta.json").write_text(json.dumps({"runs": 3, "pages": "spread",
                                                 "agent_b": {"temperature": 0.0}}))
    (bench / "report.md").write_text("the third run's report")
    out = tmp_path / "rescored"
    assert eb.main(["score", str(bench), "--out", str(out)]) == 0
    assert (bench / "report.md").read_text() == "the third run's report", "kept"
    data = json.loads((out / "scores.json").read_text())
    assert list(data["by_prompt"]) == ["default"], "an old run is the default prompt's"
    assert data["summary"]["Datierung"]["hit"] == 3, "#842: '14. Jh. - 15. Jh.' is 1301-1500"
    assert "Prompt des Bildpfads: `default`" in (out / "report.md").read_text()


def test_the_two_findings_of_844_are_numbers_in_the_report(tmp_path):
    early = tmp_path / "early"
    early.mkdir()
    (early / "reference.json").write_text(json.dumps(
        {"date_from": 820, "date_to": 850, "material": "perg", "script": "karolingische Minuskel"}))
    _write_runs(early, [_sj("Textualis", "15. Jh.", "Papier")] * 2
                + [_sj("Karolingische Minuskel", "9. Jh.", "Pergament")])
    late = tmp_path / "late"
    late.mkdir()
    (late / "reference.json").write_text(json.dumps(
        {"date_from": 1450, "date_to": 1470, "material": "chart"}))
    _write_runs(late, [_sj("Bastarda", "15. Jh.", "Pergament")])
    scores, report = eb.score(tmp_path)
    p = eb.summarise_period_bias(scores)
    assert p == {"early_volumes": 1, "early_dating_hit": 1, "early_dating_judged": 3,
                 "early_dating_hit_rate": 0.333, "material_miss": 3,
                 "material_miss_with_dating_miss": 2}
    assert "Datierung der Bände bis 1100 (Katalog): 1 von 3 Läufen getroffen (33%), 1 Bände." \
        in report
    assert "Beschreibstoff falsch: 3 Läufe, davon 2 mit falscher Datierung." in report


# ── our own volumes: reference.json and pages/ ──────────────────────────────

def test_a_reference_written_by_hand_is_read_like_the_catalogue():
    ref = eb.parse_reference({"date_from": 1528, "material": "papier", "script": "Kanzleikursive",
                              "hands": 2}, "bern-1528")
    assert (ref.date_from, ref.date_to, ref.material, ref.hands) == (1528, 1528, "papier", 2)
    assert ref.script_families == ["kursive"]
    assert eb.parse_reference({"material": "Pergament"}, "x").material == "pergament"
    assert eb.parse_reference({"material": "perg"}, "x").material == "pergament"
    blank = eb.parse_reference({}, "x")
    assert (blank.date_from, blank.material, blank.hands, blank.script_families) == (
        None, None, None, [])


def test_our_own_volume_runs_and_scores_through_the_cli_with_the_variant(tmp_path, monkeypatch):
    bench = tmp_path / "agentb-gt"
    vol = bench / "bern-1528"
    (vol / "pages").mkdir(parents=True)
    for i in range(7):
        (vol / "pages" / f"p{i:02d}.jpg").write_bytes(b"\xff\xd8\xff" + bytes([i]))
    (vol / "pages" / "notes.txt").write_text("not a page")
    (vol / "reference.json").write_text(json.dumps(
        {"date_from": 1528, "date_to": 1528, "material": "papier", "script": "Kanzleikursive"}))
    sent = []

    def vision(prompt, image_source=None, **kw):
        sent.append((prompt, [Path(p).name for p in image_source]
                     if isinstance(image_source, list) else [Path(image_source).name]))
        return ('{"Beschreibstoff": {"wert": "Papier"}, "Schrift": {"wert": "Kanzleikursive"}, '
                '"Datierung": {"wert": "16. Jh."}}\n\n## B\nText.')
    monkeypatch.setattr(agent_b.gs, "chat_vision", vision)
    monkeypatch.setattr(eb, "_http_get", lambda url: pytest.fail("our pages are local"))
    monkeypatch.setattr(config, "AGENT_B_PAGES", 3)

    assert eb.main(["run", str(bench), "--runs", "2", "--pages", "spread",
                    "--prompt", "material-first"]) == 0
    assert len(sent) == 2 and all("1. Beschreibstoff" in p for p, _ in sent)
    assert sent[0][1] == ["p00.jpg", "p03.jpg", "p06.jpg"], "spread over the local pages"
    assert sorted(p.name for p in (vol / "runs").iterdir()) == [
        "material-first-00.json", "material-first-01.json"]
    meta = json.loads((bench / "meta.json").read_text())
    assert meta["variants"]["material-first"]["prompt"] == "material-first"
    assert meta["variants"]["material-first"]["model"] == config.GPUSTACK_MODEL_VISION
    assert not (config.DATA_DIR / "descriptions" / "bern-1528-material-first-r0.json").exists(), \
        "the bench's descriptions stay in the bench"

    assert eb.main(["score", str(bench)]) == 0
    data = json.loads((bench / "scores.json").read_text())
    s = data["by_prompt"]["material-first"]["summary"]
    assert (s["Schrift"]["hit"], s["Datierung"]["hit"], s["Beschreibstoff"]["hit"]) == (2, 2, 2)
