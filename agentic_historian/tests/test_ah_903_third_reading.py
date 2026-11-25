"""#903 — the epoch's Qwen as a third, combined reading: the measuring tool.

Pinned offline, with a fake gateway: which pages a plan selects, that every arm
reads the same lines, what the Qwen is asked and with which hint, that a run
resumes without paying twice and retries what failed, and what the report
computes — corpus CER, document bootstrap, paired differences, anchoring.

Run from the repo root.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from eval import third_reading as tr  # noqa: E402

NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2019-07-15"


def _image():
    """The real Pillow, resolved at run time. ``test_ah_108`` installs a
    ``MagicMock`` PIL into ``sys.modules`` while pytest collects; a module-level
    import binds to it and writes images that are not images (as in
    ``test_ah_741_upload_size._pil``). ``third_reading`` imports PIL inside its
    functions for the same reason."""
    for name in [n for n in list(sys.modules) if n == "PIL" or n.startswith("PIL.")]:
        if type(sys.modules[name]).__module__.startswith("unittest.mock"):
            del sys.modules[name]
    from PIL import Image
    return Image


def _page_xml(image: str, lines: list[tuple[str, tuple[int, int, int, int] | None]]) -> str:
    out = []
    for i, (text, box) in enumerate(lines):
        coords = ""
        if box:
            x0, y0, x1, y1 = box
            coords = (f'<Coords points="{x0},{y0} {x1},{y0} {x1},{y1} {x0},{y1}"/>'
                      f'<Baseline points="{x0},{y1 - 2} {x1},{y1 - 2}"/>')
        out.append(f'<TextLine id="l{i}">{coords}<TextEquiv><Unicode>{text}</Unicode></TextEquiv></TextLine>')
    return (f'<?xml version="1.0" encoding="UTF-8"?><PcGts xmlns="{NS}"><Page imageFilename="{image}" '
            f'imageWidth="200" imageHeight="100"><TextRegion id="r1">{"".join(out)}</TextRegion></Page></PcGts>')


def _write_page(d: Path, stem: str, lines, size=(200, 100), xml_dir: str | None = None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    _image().new("RGB", size, "white").save(d / f"{stem}.jpg")
    xd = d / xml_dir if xml_dir else d
    xd.mkdir(parents=True, exist_ok=True)
    p = xd / f"{stem}.xml"
    p.write_text(_page_xml(f"{stem}.jpg", lines), encoding="utf-8")
    return p


REFS = ["Dem Edlen Herrn", "zu Sanct Gallen", "unserm lieben"]
BOXES = [(0, 0, 50, 10), (0, 20, 120, 30), (10, 40, 190, 52)]


def _set(tmp: Path, docs=("d1", "d2"), pages_per_doc=1) -> Path:
    root = tmp / "pages"
    for d in docs:
        for k in range(pages_per_doc):
            _write_page(root / d, f"p{k}", list(zip(REFS, BOXES)))
    return root


MODELS = {
    "kr": {"id": "kr", "engine": "kraken", "level": "page"},
    "tr": {"id": "tr", "engine": "trocr", "level": "line"},
    "qline": {"id": "qline", "engine": "vllm", "level": "line", "prompt": "Read it."},
    "qpage": {"id": "qpage", "engine": "vllm", "level": "page", "prompt": "Read the page."},
}


class FakeGateway:
    """kraken reads every line right, TrOCR drops the last character, the Qwen
    answers by width for C and copies its hint for D and D2."""

    def __init__(self, fail_arm: str | None = None, short: bool = False):
        self.calls: list[tuple] = []
        self.fail_arm = fail_arm
        self.short = short
        self.truth: dict[tuple, str] = {}

    def models(self):
        return MODELS

    def segment(self, image, name):
        self.calls.append(("segment", name))
        return [{"order": i, "bbox": list(b), "baseline": [[b[0], b[3]], [b[2], b[3]]]}
                for i, b in enumerate(BOXES)]

    def recognize(self, image, name, model, lines):
        self.calls.append(("recognize", model, [ln["bbox"] for ln in lines]))
        if self.fail_arm == model:
            raise RuntimeError("engine down")
        by_box = {tuple(b): r for b, r in zip(BOXES, REFS)}
        texts = [by_box.get(tuple(ln["bbox"]), "?") for ln in lines]
        if model == "tr":
            texts = [t[:-1] for t in texts]
        if self.short:
            texts = texts[:-1]
        return {"lines": [{"order": i, "text": t} for i, t in enumerate(texts)],
                "segmented_by": "given"}

    def chat(self, model, image, mime, prompt, max_tokens):
        from io import BytesIO
        w, h = _image().open(BytesIO(image)).size
        self.calls.append(("chat", model, w * h, mime, prompt, max_tokens))
        if self.fail_arm == "chat":
            raise RuntimeError("vLLM gone")
        if "<reading n=\"1\">" in prompt:
            text = prompt.split('<reading n="1">\n', 1)[1].split("\n</reading>", 1)[0]
        elif "<reading>" in prompt:
            text = prompt.split("<reading>\n", 1)[1].split("\n</reading>", 1)[0]
        else:
            text = f"w{w}"
        return {"text": text, "finish": "stop", "prompt_tokens": 10, "completion_tokens": 3}


def _plan(tmp: Path, **kw) -> Path:
    s = {"name": "s", "pages": "pages", "kraken": "kr", "trocr": "tr", "qwen": "qline", **kw}
    p = tmp / "plan.json"
    p.write_text(json.dumps({"sets": [s]}), encoding="utf-8")
    return p


def _args(plan, out, **kw):
    ns = tr.build_parser().parse_args([str(plan), "--out", str(out)])
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _run(tmp, gw, **kw):
    logs = []
    tr.run(_args(tmp / "plan.json", tmp / "run", **kw), gateway_factory=lambda: gw, log=logs.append)
    return json.loads((tmp / "run" / "report.json").read_text()), logs


def _arm(rep, level, arm, set_name="s"):
    g = next(g for g in rep["groups"] if g["level"] == level and g["set"] == set_name)
    return next(a for a in g["arms"] if a["arm"] == arm)


# ── the plan and the set ─────────────────────────────────────────────────────

@pytest.mark.parametrize("sets, msg", [
    ([], "no sets"),
    ([{"name": "a", "pages": "x", "kraken": "k", "trocr": "t"}], "without 'qwen'"),
    ([{"name": "a", "pages": "x", "kraken": "k", "trocr": "t", "qwen": "q"}] * 2, "twice"),
    ([{"name": "a", "pages": "x", "kraken": "k", "trocr": "t", "qwen": "q", "years": [1550, 1450]}],
     "years must be"),
])
def test_a_plan_that_cannot_be_measured_is_refused(tmp_path, sets, msg):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"sets": sets}))
    with pytest.raises(tr.PlanError, match=msg):
        tr.load_plan(p)


def test_the_document_is_the_directory_without_a_csv(tmp_path):
    _set(tmp_path, docs=("a", "b"), pages_per_doc=2)
    pages = tr.load_set({"name": "s", "pages": "pages"}, tmp_path)
    assert [(p.key, p.doc) for p in pages] == [
        ("a/p0.xml", "a"), ("a/p1.xml", "a"), ("b/p0.xml", "b"), ("b/p1.xml", "b")]
    assert pages[0].reference == "\n".join(REFS)
    assert pages[0].image.name == "p0.jpg"


def test_years_keep_the_closed_interval_from_the_csv(tmp_path):
    _set(tmp_path, docs=("a", "b", "c"))
    (tmp_path / "docs.csv").write_text(
        "page,doc,year\na/p0.xml,M1,1449\nb/p0.xml,M2,1450\nc/p0.xml,M3,1550\n")
    pages = tr.load_set({"name": "s", "pages": "pages", "docs": "docs.csv",
                         "years": [1450, 1550]}, tmp_path)
    assert [(p.doc, p.year) for p in pages] == [("M2", 1450), ("M3", 1550)]


def test_a_date_filter_without_dates_stops(tmp_path):
    _set(tmp_path)
    with pytest.raises(tr.PlanError, match="years"):
        tr.load_set({"name": "s", "pages": "pages", "years": [1450, 1550]}, tmp_path)


def test_the_csv_is_the_selection(tmp_path):
    _set(tmp_path, docs=("a", "b"))
    (tmp_path / "docs.csv").write_text("page,doc,year\nb/p0.xml,M2,\n")
    pages = tr.load_set({"name": "s", "pages": "pages", "docs": "docs.csv"}, tmp_path)
    assert [(p.key, p.doc, p.year) for p in pages] == [("b/p0.xml", "M2", None)]


def test_the_image_is_found_one_level_up_as_transkribus_exports_it(tmp_path):
    xml = _write_page(tmp_path / "pages", "p0", list(zip(REFS, BOXES)), xml_dir="page")
    page = tr.read_page("s", tmp_path / "pages", xml, "d", None)
    assert page.image == tmp_path / "pages" / "p0.jpg"


def test_a_transcribed_line_without_a_box_is_counted_not_read(tmp_path):
    xml = _write_page(tmp_path / "pages", "p0", [("eins", BOXES[0]), ("zwei", None), ("", BOXES[2])])
    page = tr.read_page("s", tmp_path / "pages", xml, "d", None)
    assert [ln.text for ln in page.lines] == ["eins"]
    assert page.skipped_lines == 1


def test_a_sample_spreads_over_documents():
    pages = [tr.GtPage("s", f"{d}/{k}", d, None, Path("x"), Path("y"), [])
             for d in ("a", "b", "c") for k in range(3)]
    picked = tr.spread(pages, 4)
    assert sorted({p.doc for p in picked}) == ["a", "b", "c"]
    assert len(picked) == 4
    assert tr.spread(pages, None) == pages


def test_fit_scales_down_to_the_budget_and_never_up():
    big = _image().new("RGB", (2000, 1000))
    small = tr.fit(big, 200_000)
    assert small.width * small.height <= 200_000
    assert abs(small.width / small.height - 2) < 0.05
    tiny = _image().new("RGB", (10, 10))
    assert tr.fit(tiny, 200_000).size == (10, 10)


# ── the models ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("over, msg", [
    ({"qwen": "nope"}, "not on the gateway"),
    ({"qwen": "qpage"}, "need a line model"),
    ({"kraken": "tr"}, "trocr model, not kraken"),
    ({"qwen_page": "qline"}, "not a page model"),
])
def test_the_models_are_checked_against_the_gateway(over, msg):
    spec = {"name": "s", "kraken": "kr", "trocr": "tr", "qwen": "qline", **over}
    with pytest.raises(tr.PlanError, match=msg):
        tr.resolve(spec, MODELS, ("line",))


# ── line level ───────────────────────────────────────────────────────────────

def test_every_arm_reads_the_ground_truths_lines(tmp_path):
    _set(tmp_path, docs=("d1",))
    _plan(tmp_path)
    gw = FakeGateway()
    rep, _ = _run(tmp_path, gw, level="line")
    rec = [c for c in gw.calls if c[0] == "recognize"]
    assert [c[1] for c in rec] == ["kr", "tr"], "one page call per engine"
    assert all(c[2] == [list(b) for b in BOXES] for c in rec)
    chats = [c for c in gw.calls if c[0] == "chat"]
    assert len(chats) == 9, "C, D and D2 for each of three lines"
    assert {c[1] for c in chats} == {"qline"}
    assert all(c[2] <= tr.PIXELS["line"] and c[3] == "image/png" and c[5] == 512 for c in chats)
    assert not any(c[0] == "segment" for c in gw.calls)
    assert _arm(rep, "line", "A1")["cer"] == 0.0
    assert _arm(rep, "line", "A2")["cer"] == round(3 / sum(len(r) for r in REFS), 4)


def test_c_is_the_models_own_prompt_and_d_carries_the_fused_hint(tmp_path):
    _set(tmp_path, docs=("d1",))
    _plan(tmp_path)
    gw = FakeGateway()
    _run(tmp_path, gw, level="line")
    prompts = [c[4] for c in gw.calls if c[0] == "chat"]
    assert prompts.count("Read it.") == 3
    hinted = [p for p in prompts if "<reading>" in p]
    assert len(hinted) == 3 and all(p.startswith("Read it.\n\n") for p in hinted)
    fused = tr.fuse_texts({"kraken": REFS[0], "trocr": REFS[0][:-1]})
    assert tr.hint_prompt("Read it.", fused) in hinted
    both = [p for p in prompts if '<reading n="1">' in p]
    assert tr.hints_prompt("Read it.", REFS[1], REFS[1][:-1]) in both


def test_b_and_b3_are_computed_not_read(tmp_path):
    _set(tmp_path, docs=("d1",))
    _plan(tmp_path)
    rep, _ = _run(tmp_path, FakeGateway(), level="line")
    b = _arm(rep, "line", "B")
    assert b["answered"] == 3 and b["sec"] == 0
    assert _arm(rep, "line", "B3")["answered"] == 3


def test_d_that_copies_a_right_hint_keeps_it(tmp_path):
    _set(tmp_path, docs=("d1", "d2"))
    _plan(tmp_path)
    rep, _ = _run(tmp_path, FakeGateway(), level="line")
    g = rep["groups"][0]
    # The fake D copies its hint; the hint is B, and B is whatever the vote made.
    b, d = _arm(rep, "line", "B"), _arm(rep, "line", "D")
    assert d["cer"] == b["cer"]
    anc = g["anchoring"]["D"]
    assert anc["hint_right"] + anc["hint_wrong"] == 6
    assert anc["broke"] == 0 and anc["fixed"] == 0
    assert anc["copied"] == anc["hint_wrong"]
    # D2 copies reading 1, kraken's, which is always right here.
    assert g["anchoring"]["D2"]["hint_right"] == 6 and g["anchoring"]["D2"]["kept"] == 6


def test_a_second_run_pays_for_nothing(tmp_path):
    _set(tmp_path, docs=("d1",))
    _plan(tmp_path)
    rep1, _ = _run(tmp_path, FakeGateway(), level="line")
    gw = FakeGateway()
    rep2, _ = _run(tmp_path, gw, level="line")
    assert gw.calls == []
    assert rep1["groups"] == rep2["groups"]


def test_a_new_hint_wording_is_read_anew_and_nothing_else(tmp_path, monkeypatch):
    _set(tmp_path, docs=("d1",))
    _plan(tmp_path)
    _run(tmp_path, FakeGateway(), level="line")
    monkeypatch.setattr(tr, "HINT_VERSION", "903-v99")
    gw = FakeGateway()
    _run(tmp_path, gw, level="line")
    prompts = [c[4] for c in gw.calls if c[0] == "chat"]
    assert len(prompts) == 6 and "Read it." not in prompts
    assert not any(c[0] == "recognize" for c in gw.calls)


def test_a_failed_call_is_no_reading_and_is_asked_again(tmp_path):
    _set(tmp_path, docs=("d1",))
    _plan(tmp_path)
    rep, logs = _run(tmp_path, FakeGateway(fail_arm="chat"), level="line")
    assert _arm(rep, "line", "C")["answered"] == 0
    assert _arm(rep, "line", "C")["cer"] == 1.0, "no answer scores as an empty one"
    assert any("vLLM gone" in m for m in logs)
    gw = FakeGateway()
    rep, _ = _run(tmp_path, gw, level="line")
    assert len([c for c in gw.calls if c[0] == "chat"]) == 9
    assert not any(c[0] == "recognize" for c in gw.calls)
    assert _arm(rep, "line", "C")["answered"] == 3


def test_an_engine_that_failed_holds_the_qwen_arms_back(tmp_path):
    _set(tmp_path, docs=("d1",))
    _plan(tmp_path)
    gw = FakeGateway(fail_arm="tr")
    rep, logs = _run(tmp_path, gw, level="line")
    assert not any(c[0] == "chat" for c in gw.calls)
    assert _arm(rep, "line", "A2")["answered"] == 0
    assert _arm(rep, "line", "B")["answered"] == 0
    assert any("engine failed" in m for m in logs)


def test_an_engine_that_answers_other_lines_is_an_error(tmp_path):
    _set(tmp_path, docs=("d1",))
    _plan(tmp_path)
    rep, logs = _run(tmp_path, FakeGateway(short=True), level="line")
    assert _arm(rep, "line", "A1")["answered"] == 0
    assert any("2 lines back for 3 given" in m for m in logs)


# ── page level ───────────────────────────────────────────────────────────────

def test_page_level_segments_once_and_a_line_model_reads_those_lines(tmp_path):
    _set(tmp_path, docs=("d1",))
    _plan(tmp_path)
    gw = FakeGateway()
    rep, _ = _run(tmp_path, gw, level="page")
    assert [c[0] for c in gw.calls].count("segment") == 1
    rec = [c for c in gw.calls if c[0] == "recognize"]
    assert [c[2] for c in rec] == [[list(b) for b in BOXES]] * 2
    assert len([c for c in gw.calls if c[0] == "chat"]) == 9
    assert _arm(rep, "page", "A1")["cer"] == 0.0
    assert _arm(rep, "page", "C")["answered"] == 1
    assert _arm(rep, "page", "D")["cer"] == _arm(rep, "page", "B")["cer"]


def test_a_page_model_reads_the_whole_page_at_the_page_budget(tmp_path):
    root = tmp_path / "pages" / "d1"
    _write_page(root, "p0", list(zip(REFS, BOXES)), size=(3000, 2000))
    _plan(tmp_path, qwen_page="qpage")
    gw = FakeGateway()
    rep, _ = _run(tmp_path, gw, level="page")
    chats = [c for c in gw.calls if c[0] == "chat"]
    assert len(chats) == 3 and {c[1] for c in chats} == {"qpage"}
    assert all(c[2] <= tr.PIXELS["page"] and c[3] == "image/jpeg" and c[5] == 4096 for c in chats)
    d = next(c[4] for c in chats if "<reading>" in c[4])
    hint = "\n".join(tr.fuse_texts({"kraken": r, "trocr": r[:-1]}) for r in REFS)
    assert d == tr.hint_prompt("Read the page.", hint)
    assert _arm(rep, "page", "D")["answered"] == 1


def test_both_levels_keep_their_readings_apart(tmp_path):
    _set(tmp_path, docs=("d1",))
    _plan(tmp_path)
    rep, _ = _run(tmp_path, FakeGateway(), level="both")
    assert sorted((g["set"], g["level"]) for g in rep["groups"]) == [("s", "line"), ("s", "page")]
    md = (tmp_path / "run" / "report.md").read_text()
    assert "## s · line" in md and "## s · page" in md


def test_dry_run_reads_nothing(tmp_path, capsys):
    _set(tmp_path)
    _plan(tmp_path)
    called = []
    assert tr.run(_args(tmp_path / "plan.json", tmp_path / "run", dry_run=True),
                  gateway_factory=lambda: called.append(1), log=print) == 0
    assert called == [] and not (tmp_path / "run").exists()
    assert "s: 2 pages, 2 documents, 6 lines" in capsys.readouterr().out


def test_main_says_what_is_wrong_with_the_plan(tmp_path, capsys):
    (tmp_path / "plan.json").write_text(json.dumps({"sets": []}))
    assert tr.main([str(tmp_path / "plan.json"), "--out", str(tmp_path / "r"), "--dry-run"]) == 2
    assert "no sets" in capsys.readouterr().err


# ── the numbers ──────────────────────────────────────────────────────────────

def _item(doc, ref, **texts):
    it = tr.Item("s", "line", f"{doc}/{ref}", doc, ref)
    it.texts = {a: None for a in tr.ARMS} | texts
    return it


def test_corpus_cer_is_edits_over_reference_characters():
    items = [_item("a", "abcd", A1="abcd"), _item("b", "ab", A1="xb")]
    c = tr.corpus(items, "A1")
    assert c["cer"] == round(1 / 6, 4) and c["chars"] == 6
    assert c["wer"] == 0.5
    assert tr.corpus([_item("a", "abc")], "A1")["cer"] == 1.0


def test_whitespace_is_collapsed_case_and_punctuation_are_not():
    assert tr.errors("Dem  Edlen\nHerrn", "Dem Edlen Herrn")[0] == 0
    assert tr.errors("dem Edlen Herrn.", "Dem Edlen Herrn")[0] == 2


def test_the_bootstrap_resamples_documents_and_pairs_the_arms():
    items = [_item(d, "abcdefghij", B="abcdefghij", D="abcdefghiX") for d in "abcdef"]
    bs = tr.bootstrap(items, ("B", "D"), pairs=(("D", "B"),), n=200)
    assert bs["documents"] == 6
    assert bs["cer"]["B"] == [0.0, 0.0] and bs["cer"]["D"] == [0.1, 0.1]
    assert bs["diff"]["D-B"] == {"ci": [0.1, 0.1], "p_better": 0.0}
    assert tr.bootstrap(items[:1], ("B",))["note"].startswith("fewer than two")


def test_the_bootstrap_is_reproducible():
    items = [_item(d, "abcd", B="abXd" if d in "ab" else "abcd") for d in "abcdefgh"]
    assert tr.bootstrap(items, ("B",), n=100) == tr.bootstrap(items, ("B",), n=100)


def test_anchoring_splits_by_whether_the_hint_was_right():
    def it(ref, hint, d):
        x = _item("a", ref, D=d)
        x.hint, x.hints = hint, [hint, hint]
        return x
    items = [it("Herrn", "Herrn", "Herrn"),     # right, kept
             it("Herrn", "Herrn", "Herren"),    # right, broke
             it("Herrn", "Hern", "Hern"),       # wrong, copied
             it("Herrn", "Hern", "Herrn"),      # wrong, fixed
             it("Herrn", "Hern", "Hxrxn")]      # wrong, worse
    s = tr.anchoring(items)["D"]
    assert (s["hint_right"], s["kept"], s["broke"]) == (2, 1, 1)
    assert (s["hint_wrong"], s["copied"], s["fixed"], s["better"], s["worse"]) == (3, 1, 1, 1, 1)
    assert s["copy_rate_when_wrong"] == round(1 / 3, 4)
    assert s["break_rate_when_right"] == 0.5


def test_the_interval_widens_when_documents_differ():
    items = [_item(d, "abcdefghij", B="abcdefghij" if d in "abc" else "XXXXXfghij") for d in "abcdef"]
    lo, hi = tr.bootstrap(items, ("B",), n=300)["cer"]["B"]
    assert lo < 0.25 < hi, "resampling documents must move the corpus CER"


def test_a_segmented_line_with_nothing_to_crop_does_not_hold_the_page(tmp_path):
    _set(tmp_path, docs=("d1",))
    _plan(tmp_path)

    class NoBox(FakeGateway):
        def segment(self, image, name):
            lines = super().segment(image, name)
            return lines + [{"order": 3, "bbox": [500, 500, 600, 510]}]

    gw = NoBox()
    rep, _ = _run(tmp_path, gw, level="page")
    assert len([c for c in gw.calls if c[0] == "chat"]) == 9, "nothing sent for the empty line"
    assert _arm(rep, "page", "C")["answered"] == 1
    gw2 = NoBox()
    _run(tmp_path, gw2, level="page")
    assert gw2.calls == [], "the empty line was recorded, not left to be asked again"


def test_a_long_line_is_fitted_to_the_line_budget(tmp_path):
    big = (0, 100, 2900, 500)
    _write_page(tmp_path / "pages" / "d1", "p0", [("eine lange Zeile", big)], size=(3000, 2000))
    _plan(tmp_path)
    gw = FakeGateway()
    _run(tmp_path, gw, level="line")
    chats = [c for c in gw.calls if c[0] == "chat"]
    assert chats and all(tr.PIXELS["line"] * 0.95 <= c[2] <= tr.PIXELS["line"] for c in chats)


# ── the HTTP calls ───────────────────────────────────────────────────────────

def test_the_gateway_sends_the_lines_the_key_and_the_image():
    import httpx
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/models":
            return httpx.Response(200, json={"models": [{"id": "kr", "engine": "kraken"}]})
        if path == "/recognize":
            return httpx.Response(200, json={"lines": [{"order": 0, "text": "x"}]})
        if path == "/segment":
            return httpx.Response(200, json={"lines": [{"order": 0, "bbox": [0, 0, 1, 1]}]})
        if path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"content": "Herrn"},
                                                          "finish_reason": "length"}],
                                             "usage": {"prompt_tokens": 7, "completion_tokens": 2}})
        return httpx.Response(404, text="no")

    gw = tr.Gateway("http://gw/", "k3y", transport=httpx.MockTransport(handler))
    assert list(gw.models()) == ["kr"]
    lines = [{"order": 0, "bbox": [1, 2, 3, 4]}]
    gw.recognize(b"img", "p.jpg", "kr", lines)
    body = seen[-1].content.decode("latin-1")
    assert 'name="lines"' in body and json.dumps(lines) in body and 'name="model"' in body
    assert gw.segment(b"img", "p.jpg") == [{"order": 0, "bbox": [0, 0, 1, 1]}]
    assert 'name="mode"' in seen[-1].content.decode("latin-1")
    out = gw.chat("q", b"\x89PNG", "image/png", "Read it.", 99)
    assert out == {"text": "Herrn", "finish": "length", "prompt_tokens": 7, "completion_tokens": 2}
    sent = json.loads(seen[-1].content)
    assert sent["model"] == "q" and sent["max_tokens"] == 99 and sent["temperature"] == 0.0
    content = sent["messages"][0]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1] == {"type": "text", "text": "Read it."}
    assert all(r.headers["X-API-Key"] == "k3y" for r in seen)


def test_a_gateway_error_is_raised_with_its_status():
    import httpx
    gw = tr.Gateway("http://gw", None, transport=httpx.MockTransport(
        lambda r: httpx.Response(503, text="vLLM starting")))
    with pytest.raises(RuntimeError, match="503 vLLM starting"):
        gw.recognize(b"img", "p.jpg", "kr", [])


# ── what the vote does ───────────────────────────────────────────────────────

def test_no_merge_takes_the_more_confident_reading_not_the_first():
    """Readings too far apart to blend: the vote takes one verbatim, the most
    confident. Without confidences that is whichever came first (25.11.2026)."""
    far = {"kraken": "Dem Edlen", "trocr": "wolgeborn vnnd vesten herren"}
    modes = tr.Counter()
    assert tr.fuse_texts(far, {"kraken": 0.2, "trocr": 0.9}, modes) == far["trocr"]
    assert modes == tr.Counter({"no-merge:trocr": 1})
    assert tr.fuse_texts(far, {"kraken": 0.9, "trocr": 0.2}) == far["kraken"]


def test_the_modes_say_vote_single_and_empty():
    modes = tr.Counter()
    tr.fuse_texts({"kraken": "Dem Edlen Herrn", "trocr": "Dem Edlen Herm"}, None, modes)
    tr.fuse_texts({"kraken": "Dem Edlen Herrn", "trocr": ""}, None, modes)
    tr.fuse_texts({"kraken": "", "trocr": " "}, None, modes)
    assert modes == tr.Counter({"vote": 1, "single:kraken": 1, "empty": 1})


class ConfidentGateway(FakeGateway):
    """TrOCR reads every line differently from kraken, and is the surer one."""

    def recognize(self, image, name, model, lines):
        res = super().recognize(image, name, model, lines)
        for ln in res["lines"]:
            if model == "tr":
                ln["text"], ln["confidence"] = "ganz andere Wörter hier", 0.9
            else:
                ln["confidence"] = 0.3
        return res


def test_the_hint_and_b_follow_the_engines_confidences(tmp_path):
    _set(tmp_path, docs=("d1",))
    _plan(tmp_path)
    gw = ConfidentGateway()
    rep, _ = _run(tmp_path, gw, level="line")
    hinted = [c[4] for c in gw.calls if c[0] == "chat" and "<reading>" in c[4]]
    assert hinted and all("ganz andere Wörter hier" in p for p in hinted)
    g = rep["groups"][0]
    assert g["fusion"]["B"] == {"no-merge:trocr": 3}
    assert _arm(rep, "line", "B")["cer"] == _arm(rep, "line", "A2")["cer"]
    md = (tmp_path / "run" / "report.md").read_text()
    assert "What the vote did, per fused line: B no-merge:trocr 3" in md


def test_the_run_keeps_fusion_quiet_and_gives_the_log_back(tmp_path):
    from loguru import logger
    seen = []
    sink = logger.add(lambda m: seen.append(m.record["name"]), level="INFO")
    try:
        _set(tmp_path, docs=("d1",))
        _plan(tmp_path)
        _run(tmp_path, ConfidentGateway(), level="line")
        assert "fusion" not in seen
        tr.fuse_texts({"kraken": "Dem Edlen", "trocr": "wolgeborn vnnd vesten herren"})
        assert "fusion" in seen, "the log is back on after the run"
    finally:
        logger.remove(sink)
