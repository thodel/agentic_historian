"""#774 — the typical page, not the first, goes to the VLM; and the untypical one too.

A page chosen by its position is worse placed than one somebody chose with
judgement (Ferilli & Piscopo, CEUR Vol-4304): the reference the palaeographer
picked by eye was near the centre, and the change at the end of the volume was
invisible against it. These pin:

* with synthetic features, the most typical page (smallest mean distance to the
  others) and the least typical one are chosen — not page 1 — deterministically;
* all pages alike: there is no untypical page; too few readable pages: the spread;
* a long order is measured on a bounded pool, not on every page;
* real image features find the page whose lines sit differently;
* the roles reach the prompt, the result, every element's ``basis`` and the
  published "Grundlage";
* the order pipeline and the e-codices bench use the choice when asked, and the
  default stays the spread of #771 until the bench has measured it.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                     # noqa: E402
import page_sample                                # noqa: E402
from agents import source_description as agent_b  # noqa: E402
from element_basis import basis_line              # noqa: E402
from page_features import image_features          # noqa: E402

TEXT = ("Wir Hans von Wiler tuend kund allen die disen brief ansehent "
        "oder hoerent lesen dass wir mit guotem willen")


def _features(vectors):
    calls = []

    def f(i):
        calls.append(i)
        if vectors[i] is None:
            raise OSError("unreadable")
        return vectors[i]
    f.calls = calls
    return f


# Ten pages around a centre: page 6 is the centre, page 9 far from all.
VECTORS = [[1.0, 0.0], [0.9, 0.2], [1.1, -0.1], [0.8, 0.1], [1.2, 0.1], [1.0, 0.3],
           [1.0, 0.1], [0.9, -0.1], [1.1, 0.2], [5.0, 4.0]]


# ── the choice ───────────────────────────────────────────────────────────────

def test_the_typical_and_the_untypical_page_are_chosen_not_page_one():
    got = page_sample.choose(10, 4, "typical", features=_features(VECTORS))
    roles = dict(got)
    assert roles[6] == page_sample.TYPICAL and roles[9] == page_sample.UNTYPICAL
    assert roles.get(0) != page_sample.TYPICAL
    assert len(got) == 4 and [i for i, _ in got] == sorted(i for i, _ in got)
    assert page_sample.choose(10, 4, "typical", features=_features(VECTORS)) == got


def test_all_pages_alike_have_no_untypical_page():
    got = dict(page_sample.choose(6, 3, "typical", features=_features([[1.0, 1.0]] * 6)))
    assert list(got.values()).count(page_sample.TYPICAL) == 1
    assert page_sample.UNTYPICAL not in got.values()


def test_too_few_readable_pages_fall_back_to_the_spread():
    vectors = [None, None, [1.0], None, [2.0], None]
    assert page_sample.choose(6, 3, "typical", features=_features(vectors)) == [
        (i, page_sample.SAMPLE) for i in page_sample.spread(6, 3)]


def test_an_unreadable_page_is_left_out_of_the_measurement():
    vectors = list(VECTORS)
    vectors[6] = None
    roles = dict(page_sample.choose(10, 4, "typical", features=_features(vectors)))
    assert 6 not in roles and roles[9] == page_sample.UNTYPICAL


def test_a_long_order_is_measured_on_a_bounded_pool():
    f = _features([[float(i % 7), 0.0] for i in range(600)])
    page_sample.choose(600, 5, "typical", features=f)
    assert len(f.calls) == page_sample.TYPICALITY_POOL
    assert f.calls[0] == 0 and f.calls[-1] == 599


def test_spread_is_untouched():
    assert page_sample.choose(200, 5) == [(i, page_sample.SAMPLE)
                                          for i in page_sample.spread(200, 5)]


# ── real image features ──────────────────────────────────────────────────────

def _page(path, pitch, ink=60):
    from PIL import Image, ImageDraw
    im = Image.new("L", (600, 800), 235)
    draw = ImageDraw.Draw(im)
    for y in range(60, 740, pitch):
        draw.rectangle([60, y, 540, y + pitch // 3], fill=ink)
    im.save(path)
    return path


def test_real_features_find_the_page_whose_lines_sit_differently(tmp_path):
    pages = [_page(tmp_path / f"p{i:02d}.png", 24 + (i % 2)) for i in range(7)]
    pages.insert(4, _page(tmp_path / "p_other.png", 60))
    roles = dict(page_sample.choose(len(pages), 3, "typical",
                                    features=lambda i: image_features(pages[i])))
    assert roles[4] == page_sample.UNTYPICAL
    assert roles.get(4) != page_sample.TYPICAL


# ── into Agent B and onto the page ───────────────────────────────────────────

@pytest.fixture
def vlm(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path / "d")
    (tmp_path / "d").mkdir()
    monkeypatch.setattr(agent_b, "_care_flag", lambda t: {"is_care_related": False})
    prompts = []

    def vision(prompt, image_source=None, **kw):
        prompts.append(prompt)
        return '{"Schrift": {"wert": "Bastarda"}}\n\n## B\nText.'
    monkeypatch.setattr(agent_b.gs, "chat_vision", vision)
    pages = []
    for name in ("p001.jpg", "p050.jpg", "p100.jpg", "p199.jpg"):
        p = tmp_path / name
        p.write_bytes(b"\xff\xd8\xff" + name.encode())
        pages.append(str(p))
    return prompts, pages


def test_the_roles_reach_the_prompt_the_result_the_basis_and_the_page(vlm):
    prompts, pages = vlm
    roles = {"p050.jpg": "typisch", "p199.jpg": "untypisch"}
    out = agent_b.describe("d", TEXT, image_path=pages, page_count=200, page_roles=roles)
    assert "Bild 2 ist die typischste Seite" in prompts[0]
    assert "Bild 4 ist die untypischste Seite" in prompts[0]
    assert out["page_roles"] == roles
    assert out["source_json"]["Schrift"]["basis"]["rollen"] == roles
    assert basis_line(out["source_json"]).endswith("(typisch p050.jpg; untypisch p199.jpg)")


def test_without_roles_the_prompt_is_the_spread_of_771(vlm):
    prompts, pages = vlm
    out = agent_b.describe("d", TEXT, image_path=pages, page_count=200)
    assert "regelmaessigem Abstand" in prompts[0] and "typischste" not in prompts[0]
    assert "page_roles" not in out


def test_the_order_pipeline_uses_the_choice_when_asked(tmp_path, monkeypatch):
    import orchestrator as orch
    from utils import publish_github
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "out")
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(config, "AGENT_B_PAGES", 3)
    monkeypatch.setattr(config, "AGENT_B_PAGE_CHOICE", "typical")
    monkeypatch.setattr(config, "AGENT_B_CACHE", False)
    monkeypatch.setattr(orch, "DUAL_AVAILABLE", False)
    monkeypatch.setattr(publish_github, "is_enabled", lambda: False)
    monkeypatch.setattr(orch.agent_a, "transcribe_image",
                        lambda img: {"transcription": TEXT, "qa_score": 0.8})
    monkeypatch.setattr(orch.agent_c, "extract_entities", lambda *a, **k: {"entities": []})
    pages = []
    for i in range(1, 11):
        p = tmp_path / f"page_{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff" + bytes([i]))
        pages.append(str(p))
    by_name = {Path(p).name: VECTORS[i] for i, p in enumerate(pages)}
    import page_features
    monkeypatch.setattr(page_features, "image_features", lambda path: by_name[Path(path).name])
    seen = {}

    def describe(doc_id, transcription, image_path=None, page_count=None, page_roles=None):
        seen.update(images=[Path(p).name for p in image_path], roles=page_roles)
        return {"source_json": {}, "source_description": "x"}
    monkeypatch.setattr(orch.agent_b, "describe", describe)
    orch.run_full_pipeline_group("order", pages, publish=False)
    assert seen["roles"] == {"page_7.jpg": "typisch", "page_10.jpg": "untypisch"}
    assert set(seen["roles"]) <= set(seen["images"])


def test_the_default_is_still_the_spread():
    assert config.AGENT_B_PAGE_CHOICE == "spread"


def test_the_bench_measures_the_typical_choice(tmp_path, monkeypatch):
    from eval import ecodices_bench as eb
    monkeypatch.setattr(config, "AGENT_B_PAGES", 3)
    manifest = {"sequences": [{"canvases": [
        {"images": [{"resource": {"service": {"@id": f"https://img/x/{i}"}}}]}
        for i in range(10)]}]}
    vol = tmp_path / "saa-x"
    vol.mkdir()
    (vol / "tei.xml").write_text("<TEI/>")
    (vol / "manifest.json").write_text(json.dumps(manifest))
    got = []

    def describe(doc_id, images, page_count, page_roles=None):
        got.append((sorted(Path(i).name for i in images), page_roles))
        return {"source_json": {}}

    def features(path):
        return VECTORS[int(Path(path).stem.split("-")[1])]

    eb.run(tmp_path, describe=describe, runs=1, pages="typical",
           get=lambda url: b"\xff\xd8\xff", features=features)
    names, roles = got[0]
    assert roles == {"page-0006.jpg": "typisch", "page-0009.jpg": "untypisch"}
    record = json.loads((vol / "runs" / "00.json").read_text())
    assert record["page_roles"] == roles


def test_a_feature_with_a_large_scale_does_not_outvote_the_others():
    """Feature 0 splits the pages 6:4 at a scale of 100 and says nothing about a
    hand; feature 1 is small and sets page 3 apart. Unscaled, the four pages at
    100 would be "untypical"; z-scored, page 3 is."""
    vectors = [[100.0 if i >= 6 else 0.0, 0.5 if i == 3 else 0.0] for i in range(10)]
    roles = dict(page_sample.choose(10, 3, "typical", features=_features(vectors)))
    assert roles[3] == page_sample.UNTYPICAL


@pytest.mark.parametrize("pitch", [24, 60])
def test_the_line_pitch_is_measured(tmp_path, pitch):
    """The pitch is the third-last feature, as a share of the page height."""
    value = image_features(_page(tmp_path / "p.png", pitch))[-3]
    assert value == pytest.approx(pitch / 800, abs=0.006)


# ── the measurement: one page choice against another, side by side (#774) ────

FIX = PKG / "eval" / "fixtures" / "ecodices"


def _sj(schrift, datierung):
    return {"Schrift": {"wert": schrift}, "Datierung": {"wert": datierung},
            "Schreiber": {"wert": None}, "Beschreibstoff": {"wert": None}}


def _bench(root, pages, volumes):
    """A scored bench directory: ``volumes`` maps id -> (fixture, [runs])."""
    root.mkdir()
    (root / "meta.json").write_text(json.dumps({"runs": 3, "pages": pages}))
    for vol, (fixture, runs) in volumes.items():
        (root / vol / "runs").mkdir(parents=True)
        (root / vol / "tei.xml").write_bytes((FIX / f"{fixture}.xml").read_bytes())
        for r, sj in enumerate(runs):
            (root / vol / "runs" / f"{r:02d}.json").write_text(json.dumps({"source_json": sj}))
    return root


def test_compare_puts_two_page_choices_side_by_side(tmp_path):
    """saa-x (paper, Bastarda/Kursive, 1450–1500): the spread names three scripts
    and one hit; typical names Bastarda thrice. csg-x is only in the spread run
    and must not count in the summary — but still appears in the per-volume table."""
    from eval import ecodices_bench as eb
    spread = _bench(tmp_path / "spread", "spread", {
        "saa-x": ("paper-two-hands", [_sj("Bastarda", "15. Jh."), _sj("Kursive", "15. Jh."),
                                      _sj("Textura", "13. Jh.")]),
        "csg-x": ("parchment-gothic", [_sj("Textura", "14. Jh.")] * 3)})
    typical = _bench(tmp_path / "typical", "typical", {
        "saa-x": ("paper-two-hands", [_sj("Bastarda", "15. Jh.")] * 3)})
    report = eb.compare([spread, typical])
    assert "- Bände in allen Läufen: **1**" in report
    assert "| Element | Trefferquote `spread` | Stabilität `spread` | Trefferquote `typical` " \
           "| Stabilität `typical` |" in report
    assert "| Schrift | 67% | 33% | 100% | 100% |" in report, "csg-x's three hits do not count"
    assert "| Datierung | 67% | 67% | 100% | 100% |" in report
    assert "## Bilanz `typical` gegen `spread`, je Band" in report
    assert "| Schrift | 1 | 0 | 0 | 1 | 0 |" in report
    assert "| Datierung | 1 | 0 | 0 | 1 | 0 |" in report
    assert "| Schreiber | 0 | 0 | 0 | 0 | 0 |" in report, "nothing answered: no balance"
    assert "| `saa-x` | hit 2, miss 1 · 33% | hit 3 · 100% | hit 2, miss 1 · 67% | " \
           "hit 3 · 100% |" in report
    assert "| `csg-x` | hit 3 · 100% | – | hit 3 · 100% | – |" in report
    assert (spread / "report.md").exists() and (typical / "scores.json").exists(), \
        "compare scores each directory afresh"


def test_compare_from_the_command_line_names_the_columns_and_writes_the_file(tmp_path):
    from eval import ecodices_bench as eb
    runs = {"saa-x": ("paper-two-hands", [_sj("Bastarda", "15. Jh.")] * 2)}
    a, b = _bench(tmp_path / "a", "first", runs), _bench(tmp_path / "b", "typical", runs)
    out = tmp_path / "compare.md"
    assert eb.main(["compare", str(a), str(b), "--label", "Seite 1", "--label", "typisch",
                    "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "Trefferquote `Seite 1` | Stabilität `Seite 1` | Trefferquote `typisch`" in text
    assert "| Schrift | 0 | 1 | 0 | 0 | 0 |" in text, "the same runs: equal, no change"
    assert text.startswith("# Agent B: Seitenwahl im Vergleich (#774)")
