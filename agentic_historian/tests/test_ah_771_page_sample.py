"""#771 — Agent B describes from a sample of the pages, not from page 1.

Agent B was handed ``pages[0]`` and nothing else, so script and dating (which
drive model selection), hands, layout and material all came from page 1, which
in a copy book is often the cover, the index or a later addition. These pin:

* the sample: k pages at even intervals, deterministic, first and last always
  in, every page when there are no more than k, page 1 alone for k = 1;
* the vision call carries several images in order, scaled down when there are
  several and untouched when there is one;
* a multi-page order hands Agent B the sample, not ``pages[0]``;
* Agent B says what it saw: ``pages_seen``, every element's ``basis``, the
  prompt; a backend that refuses several images gets page 1, and the result
  says one page, not five;
* the cost (pages, seconds, tokens) is in the result;
* a description made from five pages is cached apart from one made from page 1;
* the published "Grundlage" names the pages.
"""

import base64
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                     # noqa: E402
import description_cache as dc                    # noqa: E402
import page_sample                                # noqa: E402
from agents import source_description as agent_b  # noqa: E402
from element_basis import basis_line              # noqa: E402
from utils import gpustack_client as gs           # noqa: E402

TEXT = ("Wir Hans von Wiler tuend kund allen die disen brief ansehent "
        "oder hoerent lesen dass wir mit guotem willen")
RESPONSE = ('{"Schrift": {"wert": "Bastarda"}, "Schreiber": {"wert": "zwei Hände"}}'
            "\n\n## Beschreibung\nText.")


# ── the sample ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [6, 7, 12, 50, 199, 200, 1001])
@pytest.mark.parametrize("k", [2, 3, 5, 9])
def test_the_sample_is_even_and_holds_first_and_last(n, k):
    got = page_sample.spread(n, k)
    assert len(got) == min(n, k) and got[0] == 0 and got[-1] == n - 1
    assert got == sorted(set(got)), "distinct and in document order"
    gaps = [b - a for a, b in zip(got, got[1:])]
    assert max(gaps) - min(gaps) <= 1, f"not even: {gaps}"
    assert page_sample.spread(n, k) == got, "deterministic"


def test_few_pages_are_all_shown_and_k1_is_page_one():
    assert page_sample.spread(3, 5) == [0, 1, 2]
    assert page_sample.spread(200, 1) == [0]
    assert page_sample.spread(0, 5) == []
    assert page_sample.spread(200, 5) == [0, 50, 100, 149, 199]


# ── the vision call ──────────────────────────────────────────────────────────

def _jpeg(path, size):
    from PIL import Image
    Image.new("RGB", size, "white").save(path, format="JPEG")
    return str(path)


def test_several_images_go_in_order_into_one_message(tmp_path):
    a, b = _jpeg(tmp_path / "a.jpg", (40, 40)), _jpeg(tmp_path / "b.jpg", (50, 50))
    parts = gs._build_messages("p", None, [a, b])[0]["content"]
    assert [p["type"] for p in parts] == ["text", "image_url", "image_url"]
    assert parts[1]["image_url"]["url"] == gs.image_url(a)
    assert parts[2]["image_url"]["url"] == gs.image_url(b)
    single = gs._build_messages("p", None, a)[0]["content"]
    assert [p["type"] for p in single] == ["text", "image_url"]


def test_scaling_shrinks_a_large_page_and_leaves_a_small_one(tmp_path):
    from PIL import Image
    big, small = _jpeg(tmp_path / "big.jpg", (3200, 2400)), _jpeg(tmp_path / "s.jpg", (800, 600))
    url = gs.image_url(big, max_px=1600)
    with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as im:
        assert im.size == (1600, 1200)
    assert gs.image_url(small, max_px=1600) == gs.image_url(small)
    assert gs.image_url(big) == f"data:image/jpeg;base64,{gs.encode_image(big)}"


def test_the_token_counts_are_kept_per_call():
    gs._record_usage(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=900,
                                                           completion_tokens=120)))
    assert gs.last_usage() == {"prompt_tokens": 900, "completion_tokens": 120}
    gs._record_usage(SimpleNamespace(usage=None))
    assert gs.last_usage() == {}


# ── Agent B ──────────────────────────────────────────────────────────────────

@pytest.fixture
def vlm(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path / "d")
    (tmp_path / "d").mkdir()
    monkeypatch.setattr(agent_b, "_care_flag", lambda t: {"is_care_related": False})
    calls = []
    state = {"refuse_several": False}

    def vision(prompt, image_source=None, system=None, **kw):
        calls.append({"prompt": prompt, "images": image_source, **kw})
        if state["refuse_several"] and isinstance(image_source, list):
            raise RuntimeError("At most 1 image(s) may be provided in one request")
        return RESPONSE
    monkeypatch.setattr(agent_b.gs, "chat_vision", vision)
    monkeypatch.setattr(agent_b.gs, "chat_text", lambda *a, **k: RESPONSE)
    pages = []
    for i in (1, 50, 100, 150, 200):
        p = tmp_path / f"p{i:03d}.jpg"
        p.write_bytes(b"\xff\xd8\xff" + bytes([i]))
        pages.append(str(p))
    return calls, pages, state


def test_agent_b_sends_the_sample_and_says_so(vlm):
    calls, pages, _ = vlm
    out = agent_b.describe("d", TEXT, image_path=pages, page_count=200)
    assert calls[0]["images"] == pages
    assert calls[0]["image_max_px"] == config.AGENT_B_PAGE_MAX_PX
    assert "5 Seite(n) von 200" in calls[0]["prompt"]
    assert "regelmaessigem Abstand" in calls[0]["prompt"]
    names = [Path(p).name for p in pages]
    assert out["pages_seen"] == names
    assert out["source_json"]["Schrift"]["basis"]["seiten"] == names
    assert out["cost"]["seiten"] == 5 and "sekunden" in out["cost"]


def test_a_backend_that_refuses_several_images_gets_page_one_and_it_is_said(vlm):
    calls, pages, state = vlm
    state["refuse_several"] = True
    out = agent_b.describe("d", TEXT, image_path=pages, page_count=200)
    assert [c["images"] for c in calls] == [pages, pages[0]]
    assert out["pages_seen"] == [Path(pages[0]).name]
    assert out["source_json"]["Schrift"]["basis"]["seiten"] == [Path(pages[0]).name]
    assert out["source_json"]["Schreiber"]["unsicher"] is True, "one page holds volume claims (#772)"


def test_one_page_is_sent_as_before(vlm):
    calls, pages, _ = vlm
    agent_b.describe("d", TEXT, image_path=pages[0], page_count=200)
    assert calls[0]["images"] == pages[0] and "image_max_px" not in calls[0]
    assert "regelmaessigem Abstand" not in calls[0]["prompt"]


def test_the_image_only_path_takes_the_sample_too(vlm):
    calls, pages, _ = vlm
    out = agent_b._describe_from_image("d", pages, None, 200)
    assert calls[0]["images"] == pages and out["pages_seen"] == [Path(p).name for p in pages]


# ── the order pipeline ───────────────────────────────────────────────────────

def test_a_multi_page_order_hands_agent_b_the_sample_not_page_one(tmp_path, monkeypatch):
    import orchestrator as orch
    from utils import publish_github
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "out")
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(config, "AGENT_B_PAGES", 5)
    monkeypatch.setattr(config, "AGENT_B_CACHE", False)
    monkeypatch.setattr(orch, "DUAL_AVAILABLE", False)
    monkeypatch.setattr(publish_github, "is_enabled", lambda: False)
    monkeypatch.setattr(orch.agent_a, "transcribe_image",
                        lambda img: {"transcription": TEXT, "qa_score": 0.8})
    monkeypatch.setattr(orch.agent_c, "extract_entities",
                        lambda *a, **k: {"entities": []})
    seen = {}

    def describe(doc_id, transcription, image_path=None, page_count=None, **kw):
        seen.update(images=image_path, page_count=page_count)
        return {"source_json": {}, "source_description": "x"}
    monkeypatch.setattr(orch.agent_b, "describe", describe)
    pages = []
    for i in range(1, 13):
        p = tmp_path / f"page_{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff" + bytes([i]))
        pages.append(str(p))
    orch.run_full_pipeline_group("order", list(reversed(pages)), publish=False)
    assert [Path(p).name for p in seen["images"]] == [
        f"page_{i}.jpg" for i in (1, 4, 7, 9, 12)], "natural order, spread, first and last"
    assert seen["page_count"] == 12


# ── cache and publication ────────────────────────────────────────────────────

def test_five_pages_and_page_one_are_cached_apart(tmp_path):
    pages = []
    for i in range(3):
        p = tmp_path / f"p{i}.jpg"
        p.write_bytes(bytes([i]))
        pages.append(p)
    plain = dc.content_key(pages)
    assert dc.content_key(pages, shown=[pages[0]]) == plain, "single page: key as before #771"
    assert dc.content_key(pages, shown=pages) != plain
    assert dc.content_key(pages, shown=pages) == dc.content_key(pages, shown=pages)


def test_the_published_grundlage_names_the_pages():
    sj = {"Schrift": {"wert": "Bastarda",
                      "basis": {"seiten": ["p001.jpg", "p100.jpg", "p200.jpg"], "von": 200}}}
    assert basis_line(sj) == "Beschreibung aus 3 von 200 Seite(n): p001.jpg, p100.jpg, p200.jpg"
    many = {"Schrift": {"wert": "x", "basis": {"seiten": [f"{i}.jpg" for i in range(10)],
                                               "von": 300}}}
    assert basis_line(many).endswith("7.jpg, …")
    one = {"Schrift": {"wert": "x", "basis": {"seiten": ["p1.jpg"], "von": 200}}}
    assert basis_line(one) == "Beschreibung aus 1 von 200 Seite(n)"
