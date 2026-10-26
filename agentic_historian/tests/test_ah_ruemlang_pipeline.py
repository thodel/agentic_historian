"""The Rümlang test pipeline, offline: PAGE XML geometry, print alignment, the
decision rules, the plan from a ``fulldoc`` shape — nothing here talks to
Transkribus, the gateway or GPUStack."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from ruemlang import adjudicate, pagexml, textalign          # noqa: E402
from ruemlang import transkribus_pages as tp                 # noqa: E402


# ── pagexml ──────────────────────────────────────────────────────────────────

def _lines():
    return [
        {"order": 0, "text": "Die erst schlußred", "bbox": [100, 100, 900, 160],
         "baseline": [[100, 150], [900, 152]], "regions": ["r1"]},
        {"order": 1, "text": "und der selben gründ", "bbox": [100, 170, 880, 230],
         "baseline": [[100, 220], [880, 222]], "regions": ["r1"]},
        {"order": 2, "text": "marginal", "bbox": [950, 120, 1100, 160], "regions": ["r2"]},
        {"order": 3, "text": "baseline only", "baseline": [[100, 300], [700, 302]]},
        {"order": 4, "text": "lost", "bbox": None},
    ]


def test_pagexml_places_lines_and_reports_the_unplaceable():
    xml, dropped = pagexml.build_pagexml("p.jpg", 1200, 1600, _lines())
    assert xml.startswith('<?xml version="1.0"')
    back = pagexml.parse_lines(xml)
    assert [l["text"] for l in back][:3] == ["Die erst schlußred", "und der selben gründ", "marginal"]
    assert len(back) == 4 and dropped == [{"index": 4, "text": "lost", "reason": "no bbox and no baseline"}]
    assert back[0]["bbox"] == [100, 100, 900, 160]
    assert back[0]["baseline"] == [(100, 150), (900, 152)]
    # the baseline-only line got a box around its baseline
    bl = [l for l in back if l["text"] == "baseline only"][0]
    assert bl["bbox"][1] < 300 < bl["bbox"][3]
    assert "readingOrder {index:0;}" in back[0]["custom"]
    assert xml.count("<TextRegion") == 3


def test_pagexml_scales_and_clips():
    xml, _ = pagexml.build_pagexml("p.jpg", 2400, 3200, _lines()[:1], scale=2.0)
    back = pagexml.parse_lines(xml)
    assert back[0]["bbox"] == [200, 200, 1800, 320]
    xml, _ = pagexml.build_pagexml("p.jpg", 500, 500, _lines()[:1])
    back = pagexml.parse_lines(xml)
    assert back[0]["bbox"][2] == 499


def test_custom_attribute_has_no_braces_in_values():
    lines = [{"order": 0, "text": "x", "bbox": [0, 0, 10, 10],
              "custom": {"atr": {"basis": "llm:{mixed}", "conf": "0.80"}}}]
    xml, _ = pagexml.build_pagexml("p.jpg", 100, 100, lines)
    assert "atr {basis:llm-(mixed); conf:0.80;}" in xml
    assert 'xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"' in xml
    assert "ReadingOrder" not in xml and "schemaLocation" not in xml


# ── textalign ────────────────────────────────────────────────────────────────

PRINT = """Die heylig christenlich kilch, deren eynig houpt Christus, ist
uß dem wort gottes geborn, im selben belybt sy und hört nit die
stimm eines frömbden. Die kilch Christi machet nitt gesatz und bott on Gottes wort.
Deßhalb all menschensatzungen, so man nempt der kilchen bott, uns nit
wyter bindend, dann sy in göttlichem wort gegründt und botten sind.
Christus ist unnser eynige wyßheyt, gerechtigkeyt, erlösung
und bezalung für aller wellt sünd. Deßhalb ein andern
verdienst der säligkeit unnd gnuogthuon für die sünd bekennen, ist Christum
verlöugnen. Das der lyb und das bluot Christi wäsenlich und liplich in
dem brot der dancksagung empfangen werd, mag mit biblischer geschrifft
nit bybracht werden."""

OTHER = """So vil hab ich, lieben brueder, vom ban zuo erlüterung wellen sagen,
damit der pfarrer seche sampt andern, das sy uß disem ort keinen besonderen
oder anderen gewalt oder houpt bewären mögen. Vierter Tag, Donnerstag, den 9. Januar."""


def test_normalize_folds_the_1528_spellings():
    assert textalign.normalize("zů") == textalign.normalize("zuo") == textalign.normalize("zu")
    assert textalign.normalize("vnnd") == textalign.normalize("unnd")
    assert textalign.normalize("Wyßheyt") == textalign.normalize("wissheit")
    n, idx = textalign.normalize_with_map("Gött- es")
    assert n == "gottes" and idx == [0, 1, 2, 3, 6, 7]


def test_reference_locates_a_manuscript_page_and_its_lines():
    ref = textalign.Reference(OTHER + "\n\n" + PRINT + "\n\n" + OTHER)
    page = ["die kilch christi machet nit gsatz vnd bott on gottes wort",
            "deshalb all menschen satzungen so man nempt der kilchen bott vns nit",
            "wyter bindend dann sy in gotlichem wort gegrundt vnd botten sind"]
    passage = ref.locate("\n".join(page))
    assert passage is not None and passage.score > 0.7
    assert "machet nitt gesatz" in passage.text and "botten sind" in passage.text
    snippet, score = ref.snippet(page[1], passage)
    assert snippet is not None and snippet.startswith("Deßhalb all menschensatzungen")
    assert score > 0.7


def test_reference_refuses_an_unrelated_page():
    ref = textalign.Reference(PRINT)
    assert ref.locate("Item es soll niemand by nacht uff der gassen gon one liecht") is None


def test_align_line_lists_survives_a_merged_line():
    a = ["die kilch christi machet nit gsatz", "vnd bott on gottes wort", "deshalb all menschen",
         "satzungen so man nempt"]
    c = ["die kilch christi machet nit gsatz vnd bott on gottes wort", "deshalb all menschen",
         "satzungen so man nempt"]
    pairing = textalign.align_line_lists(a, c)
    assert pairing[2] == 1 and pairing[3] == 2
    assert pairing[0] == 0 or pairing[1] == 0


# ── adjudicate ───────────────────────────────────────────────────────────────

def test_deterministic_rules():
    c = adjudicate.LineCase(0, {"A": "vnd bott on gottes wort", "B": "vnd bott  on gottes wort", "C": ""})
    assert adjudicate.deterministic(c).basis == "consensus"
    c = adjudicate.LineCase(1, {"A": "vnd bott on gottes wort", "B": "und bott on gottes wort", "C": None})
    d = adjudicate.deterministic(c)
    assert d.basis == "agree_norm" and d.text == "vnd bott on gottes wort"
    c = adjudicate.LineCase(2, {"A": "vnd bott on gottes wort", "B": "", "C": ""})
    assert adjudicate.deterministic(c).basis == "single:A"
    c = adjudicate.LineCase(3, {"A": "vnd bott on gottes wort", "B": "vnd bott on gotz wort", "C": ""})
    assert adjudicate.deterministic(c) is None


def test_arbiter_answer_is_checked_and_falls_back():
    cases = [adjudicate.LineCase(3, {"A": "vnd bott on gottes wort", "B": "vnd bott on gotz wort", "C": ""},
                                 print_snippet="und bott on Gottes wort", print_score=0.9),
             adjudicate.LineCase(4, {"A": "deshalb all menschen satzungen", "B": "deshalb alle mensche satzung"}),
             adjudicate.LineCase(5, {"A": "wyter bindend", "B": "wytter bindent"})]
    seen = {}

    def llm(system, prompt):
        seen["prompt"] = prompt
        return ('Hier: [{"i": 3, "text": "vnd bott on gottes wort", "basis": "A", "confidence": 0.9},'
                ' {"i": 4, "text": "x", "basis": "mixed", "confidence": 0.9}]')

    out = adjudicate.decide_page(cases, llm)
    assert "P (score 0.90): und bott on Gottes wort" in seen["prompt"]
    assert out[0].basis == "llm:A" and out[0].text == "vnd bott on gottes wort" and out[0].llm_used
    assert out[1].basis == "fallback" and out[1].text == "deshalb all menschen satzungen"
    assert "rejected" in out[1].note
    assert out[2].basis == "fallback" and "no answer" in out[2].note


def test_decide_page_without_arbiter_keeps_the_line_model():
    cases = [adjudicate.LineCase(0, {"A": "a b c", "B": "a b d", "C": "a b e"})]
    out = adjudicate.decide_page(cases, None)
    assert out[0].basis == "no_llm:A" and out[0].text == "a b c"


def test_build_cases_pairs_page_model_and_print():
    ref = textalign.Reference(PRINT)
    a = ["die kilch christi machet nit gsatz vnd bott on gottes wort",
         "deshalb all menschen satzungen so man nempt der kilchen bott vns nit",
         "wyter bindend dann sy in gotlichem wort gegrundt vnd botten sind"]
    b = ["die kilch christi machet nit gsatz vnd bott on gotes wort",
         "deshalb all menschen satzungen so man nempt der kilchen bott vns nit",
         "wyter bindend dan sy in gotlichem wort gegrundt vnd botten sind"]
    page_text = "\n".join(a[:2]) + "\nwyter bindend dann sy in gotlichem wort gegrundt und botten sind\n"
    cases, meta = adjudicate.build_cases(a, b, page_text, ref)
    assert meta["passage"]["score"] > 0.7 and meta["page_model_paired"] == 3
    assert cases[1].candidates["C"] == a[1]
    assert cases[1].print_snippet and cases[1].print_snippet.startswith("Deßhalb")
    decisions = adjudicate.decide_page(cases, None)
    assert [d.basis for d in decisions] == ["no_llm:A", "consensus", "no_llm:A"]


# ── transkribus_pages ────────────────────────────────────────────────────────

FULLDOC = {
    "md": {"docId": 77, "title": "Rümlang Protokoll"},
    "pageList": {"pages": [
        {"pageId": 1, "pageNr": 1, "url": "https://files/x1.jpg", "width": 2000, "height": 3000,
         "tsList": {"transcripts": [{"tsId": 11, "status": "NEW", "nrOfRegions": 0, "nrOfLines": 0,
                                     "url": "https://files/x1.xml"}]}},
        {"pageId": 2, "pageNr": 2, "url": "https://files/x2.jpg", "width": 2000, "height": 3000,
         "tsList": {"transcripts": [{"tsId": 22, "status": "IN_PROGRESS", "nrOfRegions": 2,
                                     "nrOfLines": 31, "nrOfTranscribedLines": 31, "nrOfWords": 210,
                                     "url": "https://files/x2.xml"},
                                    {"tsId": 21, "status": "NEW", "nrOfRegions": 0, "nrOfLines": 0}]}},
        {"pageId": 3, "pageNr": 3, "url": "https://files/x3.jpg", "width": 2000, "height": 3000,
         "tsList": {"transcripts": [{"tsId": 33, "status": "NEW", "nrOfRegions": 1, "nrOfLines": 0}]}},
        {"pageId": 4, "pageNr": 4, "url": "https://files/x4.jpg", "tsList": {"transcripts": []}},
    ]},
}


class FakeTs(tp.TkSession):
    """A session answering from a dict of url -> json; records posts."""

    def __init__(self, answers):
        super().__init__(session=None)
        self.answers = answers
        self.posts = []
        self.kind, self.value = "sid", "S"

    def get_json(self, url, what, **kw):
        for key, val in self.answers.items():
            if url.endswith(key):
                return val
        raise tp.tk.TranskribusError(f"{what}: 404")

    def post(self, url, **kw):
        self.posts.append((url, kw))
        class R:
            status_code = 200
            text = "{}"
            def json(self): return {"tsId": 999}
        return R()


FULLDOC["pageList"]["pages"][2]["tsList"]["transcripts"][0].update({"nrOfLines": 12, "nrOfTranscribedLines": 0})


def test_pages_of_document_reads_the_latest_transcript():
    ts = FakeTs({"/collections/5/77/fulldoc": FULLDOC})
    pages = tp.pages_of_document(ts, 5, 77)
    assert [p.key for p in pages] == ["doc77_p0001", "doc77_p0002", "doc77_p0003", "doc77_p0004"]
    assert [tp.is_unsegmented(p) for p in pages] == [True, False, False, True]
    assert [tp.is_untranscribed(p) for p in pages] == [False, False, True, False]
    assert [tp.wanted(p) for p in pages] == [True, False, True, True]
    assert [tp.wanted(p, include_untranscribed=False) for p in pages] == [True, False, False, True]
    assert pages[1].nr_lines == 31 and pages[1].ts_id == 22 and pages[1].doc_title == "Rümlang Protokoll"
    assert pages[3].ts_id is None and pages[3].width is None
    again = tp.PageInfo.from_dict(pages[1].to_dict())
    assert again == pages[1]
    # a plan written before the field existed still reads
    old = pages[1].to_dict(); del old["nr_transcribed_lines"]
    assert tp.PageInfo.from_dict(old).nr_transcribed_lines == 0


def test_find_collection_is_fold_insensitive_and_refuses_ambiguity():
    cols = [{"colId": 1, "colName": "Ruemlang Disputation"}, {"colId": 2, "colName": "Laßberg"},
            {"colId": 3, "colName": "RÜMLANG test"}]
    ts = FakeTs({"/collections/list": cols})
    assert tp.find_collection(ts, "laßberg")["colId"] == 2
    assert tp.find_collection(ts, "3")["colId"] == 3
    with pytest.raises(tp.tk.TranskribusError, match="2 collections match"):
        tp.find_collection(ts, "Rümlang")
    with pytest.raises(tp.tk.TranskribusError, match="no collection matches"):
        tp.find_collection(ts, "Bullinger")


def test_collection_pages_filters_documents():
    ts = FakeTs({"/collections/5/list": [{"docId": 77, "title": "R"}, {"docId": 78, "title": "H"}],
                 "/collections/5/77/fulldoc": FULLDOC})
    pages = tp.collection_pages(ts, 5, docs=[77])
    assert len(pages) == 4 and all(p.docid == 77 for p in pages)


def test_post_pagexml_sends_status_parent_and_xml_body():
    ts = FakeTs({})
    answer = tp.post_pagexml(ts, 5, 77, 3, "<PcGts/>", status="in_progress", note="n", parent_ts=33)
    assert answer == {"tsId": 999}
    url, kw = ts.posts[0]
    assert url.endswith("/collections/5/77/3/text")
    assert kw["params"]["status"] == "IN_PROGRESS" and kw["params"]["parent"] == "33"
    assert kw["data"] == b"<PcGts/>" and kw["headers"]["Content-Type"].startswith("application/xml")
    with pytest.raises(tp.tk.TranskribusError, match="unknown transcript status"):
        tp.post_pagexml(ts, 1, 2, 3, "<x/>", status="MAYBE")


def test_tksession_attaches_the_credential_it_got(monkeypatch):
    calls = []

    class S:
        def get(self, url, **kw): calls.append(("get", url, kw)); return None
        def post(self, url, **kw): calls.append(("post", url, kw)); return None

    ts = tp.TkSession(session=S()); ts.kind, ts.value = "sid", "ABC"
    ts.get("u", params={"a": "1"})
    assert calls[-1][2]["params"] == {"a": "1", "JSESSIONID": "ABC"} and "headers" not in calls[-1][2]
    ts.kind, ts.value = "bearer", "TOK"
    ts.post("u", data=b"x")
    assert calls[-1][2]["headers"] == {"Authorization": "Bearer TOK"} and "params" not in calls[-1][2]


def test_tksession_falls_back_to_oidc_when_legacy_login_is_refused(monkeypatch):
    import config
    monkeypatch.setattr(config, "_get", lambda k, d="": {"TRANSKRIBUS_USER": "u",
                                                        "TRANSKRIBUS_PASSWORD": "p"}.get(k, d))
    monkeypatch.setattr(tp.tk, "login", lambda *a, **k: (_ for _ in ()).throw(tp.tk.TranskribusError("401")))

    class R:
        status_code = 200
        def json(self): return {"access_token": "TOK"}

    class S:
        def post(self, url, **kw):
            assert url == tp.OIDC_TOKEN_URL and kw["data"]["username"] == "u"
            return R()

    ts = tp.TkSession(session=S()).authenticate()
    assert (ts.kind, ts.value, ts.source) == ("bearer", "TOK", "OIDC password grant")


def test_reference_uses_anchor_hint_and_weak_windows():
    text = ("[Druck 1528 S. 1]\n" + OTHER + "\n[Druck 1528 S. 2]\n" + PRINT + "\n[Druck 1528 S. 3]\n" + OTHER)
    ref = textalign.Reference(text)
    assert ref.page_spans[2][0] < ref.page_spans[3][0]
    page = ["die kilch christi machet nit gsatz vnd bott on gottes wort",
            "deshalb all menschen satzungen so man nempt der kilchen bott vns nit"]
    p = ref.locate("\n".join(page), pages=(2, 2))
    assert p is not None and not p.weak and p.pages == (2, 2)
    # unrelated text, but a hint: the window comes back weak, and only a line that
    # really matches gets a snippet
    weak = ref.locate("Item es soll niemand by nacht uff der gassen gon one liecht noch gewer", pages=(2, 2), margin=0)
    assert weak is not None and weak.weak and weak.pages == (2, 2)
    assert ref.snippet("niemand by nacht uff der gassen", weak) == (None, 0.0) or ref.snippet("niemand by nacht uff der gassen", weak)[0] is None
    snip, score = ref.snippet("deshalb all menschen satzungen so man nempt der kilchen bott", weak)
    assert snip and snip.startswith("Deßhalb") and score > 0.72


# ── ocr_reference ────────────────────────────────────────────────────────────

ALTO = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#"><Layout><Page><PrintSpace><TextBlock>
<TextLine><String CONTENT="Die"><Glyph CONTENT="D"/><Glyph CONTENT="i"/></String><SP/>
<String CONTENT="erst"/><SP/><String CONTENT="Schluszred."/></TextLine>
<TextLine><String CONTENT="und"/><String CONTENT="zů"/></TextLine>
</TextBlock></PrintSpace></Page></Layout></alto>"""


def test_ocr_reference_reads_alto_words_and_page_lines(tmp_path):
    from ruemlang import ocr_reference
    (tmp_path / "p021.xml").write_text(ALTO, encoding="utf-8")
    xml, _ = pagexml.build_pagexml("p.jpg", 1200, 1600, _lines()[:2])
    (tmp_path / "p022.xml").write_text(xml, encoding="utf-8")
    (tmp_path / "canvases.tsv").write_text("21\tIIIr\t1x1\tu\n22\tIIIv\t1x1\tu\n", encoding="utf-8")
    assert ocr_reference.lines_of(tmp_path / "p021.xml") == ["Die erst Schluszred.", "und zů"]
    text = ocr_reference.build(tmp_path, tmp_path / "canvases.tsv")
    assert "[e-rara 30973277 canvas 21 IIIr]\nDie erst Schluszred.\nund zů\n" in text
    assert "[e-rara 30973277 canvas 22 IIIv]\nDie erst schlußred\nund der selben gründ" in text


# ── pipeline CLI ─────────────────────────────────────────────────────────────

def test_cli_accepts_shared_options_on_either_side_of_the_subcommand():
    from ruemlang import pipeline
    a = pipeline.build_parser().parse_args(["run", "--limit", "3", "--dry-run", "--pages", "61,70-72"])
    b = pipeline.build_parser().parse_args(["--limit", "3", "run", "--dry-run"])
    assert a.limit == b.limit == 3 and a.command == b.command == "run" and a.dry_run and b.dry_run
    assert pipeline._page_selection(a.pages) == {61, 70, 71, 72}
    assert pipeline._page_selection(None) is None
    c = pipeline.build_parser().parse_args(["plan", "--dry-run"])
    assert c.collection == pipeline.DEFAULT_COLLECTION and c.doc is None


def test_adjudicate_and_xml_fall_back_to_trocr_geometry(tmp_path, monkeypatch):
    """Line model failed at the gateway, TrOCR read the page: the page is still
    decided (single:B) and placed with TrOCR's line geometry."""
    from ruemlang import pipeline
    import json
    root = tmp_path / "run"; (root / "pages").mkdir(parents=True)
    page = tp.PageInfo(colid=5, docid=77, doc_title="R", page_nr=61, page_id=1, image_url="https://f/p61.jpg",
                       width=1200, height=1600, ts_id=9, status="NEW", nr_regions=0, nr_lines=0, nr_words=0,
                       xml_url=None)
    tp.write_plan(root / "plan.json", {"colId": 5, "colName": "T"}, [page])
    d = root / "pages" / page.key; d.mkdir()
    (d / "rec_trocr-kurrent-xvi-xvii.json").write_text(json.dumps({
        "model": "trocr-kurrent-xvi-xvii", "text": "a\nb", "scale_to_original": 1.0,
        "lines": [{"order": 0, "text": "die kilch christi", "bbox": [10, 10, 500, 60]},
                  {"order": 1, "text": "machet nit gsatz", "bbox": [10, 70, 480, 120]}]}), encoding="utf-8")
    (d / "rec_qwen3vl-medieval-german-v3.error.json").write_text("{}", encoding="utf-8")
    args = pipeline.build_parser().parse_args(["--out", str(root), "adjudicate", "--no-llm"])
    assert pipeline.cmd_adjudicate(args) == 0
    adj = json.loads((d / "adjudicated.json").read_text(encoding="utf-8"))
    assert adj["meta"]["geometry_from"] == "B" and adj["meta"]["models_present"] == ["B"]
    assert [x["basis"] for x in adj["decisions"]] == ["single:B", "single:B"]
    args = pipeline.build_parser().parse_args(["--out", str(root), "xml"])
    assert pipeline.cmd_xml(args) == 0
    back = pagexml.parse_lines((d / "page.xml").read_text(encoding="utf-8"))
    assert [l["text"] for l in back] == ["die kilch christi", "machet nit gsatz"]
    assert back[0]["bbox"] == [10, 10, 500, 60]
    assert '"geometry_from": "B"' in (d / "page.xml").read_text(encoding="utf-8")


def test_build_cases_pairs_page_model_against_trocr_when_line_model_is_missing():
    """What the first tei run showed: A absent, B and C present, every line 'single'."""
    b = ["die kilch christi machet nit gsatz vnd bott on gotes wort",
         "deshalb all menschen satzungen so man nempt der kilchen bott vns nit",
         "wyter bindend dan sy in gotlichem wort gegrundt vnd botten sind"]
    page_text = "\n".join(["die kilch christi machet nit gsatz vnd bott on gottes wort", b[1],
                           "wyter bindend dann sy in gotlichem wort gegrundt und botten sind"])
    cases, meta = adjudicate.build_cases([""] * 3, b, page_text, textalign.Reference(PRINT))
    assert meta["page_model_paired"] == 3
    assert [sorted(c.available()) for c in cases] == [["B", "C"]] * 3
    assert meta["passage"] is not None and meta["lines_with_print"] == 3
    decisions = adjudicate.decide_page(cases, None, primary="B")
    assert [d.basis for d in decisions] == ["no_llm:B", "consensus", "no_llm:B"]


def test_cer_and_eval_guard(tmp_path):
    from ruemlang import pipeline
    assert pipeline._cer("abc def", "abc  def") == 0.0
    assert pipeline._cer("abcd", "abxd") == 0.25
    assert pipeline._cer("", "x") is None
    root = tmp_path / "r"; root.mkdir()
    (root / "plan.json").write_text('{"eval": true, "collection": {}, "pages": []}', encoding="utf-8")
    args = pipeline.build_parser().parse_args(["--out", str(root), "upload", "--dry-run"])
    with pytest.raises(SystemExit, match="eval run"):
        pipeline.cmd_upload(args)
    args = pipeline.build_parser().parse_args(["eval", "--pages", "12-14", "--limit", "2"])
    assert args.command == "eval" and args.limit == 2


# ── fourth reader, ground-truth export ───────────────────────────────────────

def test_build_cases_with_a_fourth_reader_and_prompt_naming_it():
    a = ["die kilch christi machet nit gsatz", "vnd bott on gottes wort"]
    b = ["die kilch christi machet nit gsatz", "vnd bott on gotes wort"]
    d = ["die kilch christi machet nit gsatz", "vnd bott on gottes wort"]
    cases, _ = adjudicate.build_cases(a, b, "", None, extra_texts=d)
    assert [sorted(c.available()) for c in cases] == [["A", "B", "D"], ["A", "B", "D"]]
    assert adjudicate.deterministic(cases[0]).basis == "consensus"
    prompt = adjudicate.render_prompt(cases)
    assert "D = Kraken-Modell" in prompt and '"D"' in prompt and "bis zu 4" in prompt
    # without D the prompt does not mention it
    cases3, _ = adjudicate.build_cases(a, b, "", None)
    assert "D" not in cases3[0].candidates
    p3 = adjudicate.render_prompt(cases3)
    assert "Kraken" not in p3 and "bis zu 3" in p3
    # a D with a different line count is aligned by content
    cases_m, _ = adjudicate.build_cases(a, b, "", None, extra_texts=[d[1]])
    assert cases_m[1].candidates["D"] == d[1] and cases_m[0].candidates["D"] == ""
    out = adjudicate.decide_page(cases, None, primary="D")
    assert out[1].basis == "no_llm:D" and out[1].text == d[1]


def test_cli_extra_model_primary_and_anchor_files_exist():
    from ruemlang import pipeline
    a = pipeline.build_parser().parse_args(["adjudicate", "--extra-model", "kraken-x", "--primary", "D"])
    assert a.extra_model == "kraken-x" and a.primary == "D"
    b = pipeline.build_parser().parse_args(["adjudicate"])
    assert b.extra_model is None and b.primary == "A"
    for path in pipeline.ANCHORS_BY_DOC.values():
        assert path.exists(), path


def test_gt_export_drops_empty_lines_and_repoints_the_image():
    from ruemlang import gt_export
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"><Page imageFilename="x.jpg" imageWidth="100" imageHeight="200">
<TextRegion id="r"><Coords points="0,0 1,1"/>
<TextLine id="a"><Coords points="0,0 1,1"/><TextEquiv><Unicode>vnd bott</Unicode></TextEquiv></TextLine>
<TextLine id="b"><Coords points="0,0 1,1"/><TextEquiv><Unicode></Unicode></TextEquiv></TextLine>
<TextLine id="c"><Coords points="0,0 1,1"/></TextLine>
</TextRegion></Page></PcGts>"""
    out, stats = gt_export.clean_pagexml(xml, "p0003.jpg")
    assert stats == {"lines": 3, "kept": 1, "chars": 8, "width": 100, "height": 200}
    assert 'imageFilename="p0003.jpg"' in out and 'id="b"' not in out and 'id="c"' not in out
    assert 'xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"' in out
    assert gt_export._pages("3-5,8") == {3, 4, 5, 8}
    seg, stats = gt_export.clean_pagexml(xml, "p0003.jpg", keep_empty=True)
    # for a segmenter every line is ground truth, transcribed or not
    assert stats["lines"] == 3 and all(f'id="{i}"' in seg for i in "abc")
    assert gt_export.build_parser().parse_args(
        ["--doc", "1", "--pages", "3-60", "--tree", "t", "--segmentation"]).segmentation


def test_train_request_is_a_finetune_the_trainer_accepts():
    from ruemlang import train_kraken
    import argparse
    args = argparse.Namespace(model_id="kraken-bern-hertwig-v1", hf_repo="dh-unibe/x",
                              train_project="P_train", base_model="kraken-cursive_inzigkofen",
                              batch_size=16, epochs=60, lrate=1e-4)
    body = train_kraken.request_body(args)
    assert body["engine"] == "kraken" and body["base_model"] == "kraken-cursive_inzigkofen"
    assert "P_train" in body["notes"] and "Hertwig" not in body["notes"]
    assert body["datasets"][0]["train_projects"] == ["P_train"] and "eval_projects" not in body["datasets"][0]
    assert body["params"]["resize"] == "union" and body["params"]["schedule"] == "cosine"
    # ~2000 lines * 0.9 / 16 * 60 epochs is far above the trainer's fine-tune floor of 500 steps
    assert 2000 * 0.9 / body["params"]["batch_size"] * body["params"]["epochs"] > 500


# ── line-matched CER ─────────────────────────────────────────────────────────

def test_linecer_ignores_order():
    from ruemlang import linecer
    gt = [([0, 0, 100, 20], "erste zeile"), ([0, 30, 100, 50], "zweite zeile")]
    hyp = [([0, 31, 100, 51], "zweite zeile"), ([0, 1, 100, 21], "erste zeile")]
    assert linecer.matched_cer(gt, hyp)["cer"] == 0.0


def test_linecer_joins_split_lines_and_counts_misses_and_extras():
    from ruemlang import linecer
    gt = [([0, 0, 200, 20], "ab cd"), ([0, 30, 200, 50], "ef")]
    hyp = [([110, 0, 200, 20], "cd"), ([0, 0, 100, 20], "ab"), ([0, 100, 200, 120], "xyz")]
    m = linecer.matched_cer(gt, hyp)
    # line 1 joined left to right is exact; line 2 missed (2); "xyz" inserted (3)
    assert (m["errors"], m["gt_chars"], m["missed_lines"], m["unmatched_chars"]) == (5, 7, 1, 3)


def test_linecer_needs_half_a_line_of_overlap():
    from ruemlang import linecer
    assert linecer.assign([[0, 0, 100, 20]], [[0, 15, 100, 35]]) == [None]
    assert linecer.assign([[0, 0, 100, 20]], [[0, 8, 100, 28]]) == [0]


def test_lines_of_another_segmentation_are_paired_by_position():
    import ruemlang.pipeline as pl
    geom = {"lines": [{"bbox": [0, 0, 200, 20]}, {"bbox": [0, 30, 200, 50]}, {"bbox": [0, 60, 200, 80]}]}
    # two pieces of line 1 (right one first), nothing on line 2, line 3 whole
    rec = {"lines": [{"text": "zwei", "bbox": [110, 1, 200, 21]}, {"text": "eins", "bbox": [0, 0, 100, 20]},
                     {"text": "drei", "bbox": [0, 61, 200, 81]}]}
    assert pl._paired_by_position(geom, rec) == ["eins zwei", "", "drei"]
    unboxed = {"lines": [{"text": t, "bbox": None} for t in "ab"]}
    assert pl._paired_by_position(geom, unboxed) == ["a", "b"]


# ── survey / prior version (T) ───────────────────────────────────────────────

def test_first_text_version_is_what_a_human_started_from():
    from ruemlang import transkribus_pages as tp
    v = lambda ts, n, tool="": {"tsId": ts, "transcribed_lines": n, "toolName": tool, "status": "x"}
    # newest first: corrected ← HTR ← layout only
    hist = [v(3, 40), v(2, 38, "PyLaia"), v(1, 0)]
    assert tp.first_text_version(hist)["tsId"] == 2
    assert tp.first_text_version([v(2, 40), v(1, 0)]) is None      # typed from scratch
    assert tp.first_text_version([v(1, 0)]) is None


def test_ranges_compress_page_lists():
    from ruemlang import pipeline
    assert pipeline._ranges([8, 3, 4, 5, 10, 11]) == "3-5,8,10-11"
    assert pipeline._ranges([]) == ""


def test_gt_export_needs_pages_or_status():
    from ruemlang import gt_export
    args = gt_export.build_parser().parse_args(["--doc", "1", "--tree", "t"])
    with pytest.raises(SystemExit):
        gt_export.export(args)


def test_train_request_takes_several_projects_and_a_trained_base():
    from ruemlang import train_kraken
    import argparse
    args = argparse.Namespace(model_id="kraken-bern-combined-v1", hf_repo="dh-unibe/x",
                              train_project=["A_train", "B_train,C_train"],
                              base_model="trained:kraken-bern-hertwig-v2",
                              batch_size=16, epochs=150, lrate=5e-4)
    body = train_kraken.request_body(args)
    assert body["datasets"][0]["train_projects"] == ["A_train", "B_train", "C_train"]
    assert body["base_model"].endswith("/kraken-bern-hertwig-v2/kraken-bern-hertwig-v2.mlmodel")
    assert body["base_model"].startswith(train_kraken.TRAINED_ROOT)
    with pytest.raises(SystemExit):
        train_kraken.resolve_base("trained:../../etc")


# ── reading on the page's own lines (A V 1447, 25.10.) ───────────────────────

_TK_PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"><Page imageFilename="x.jpg" imageWidth="200" imageHeight="100">
<TextRegion id="r1" custom="readingOrder {index:0;}"><Coords points="0,0 200,0 200,100 0,100"/>
<TextLine id="l1" custom="readingOrder {index:0;}"><Coords points="10,10 190,12 190,30 10,28"/><Baseline points="10,26 190,28"/>
<Word id="w1"><Coords points="10,10 50,10 50,30 10,30"/><TextEquiv><Unicode>alt</Unicode></TextEquiv></Word>
<TextEquiv><Unicode>alt eins</Unicode></TextEquiv></TextLine>
<TextLine id="l2"><Coords points="10,40 190,40 190,60 10,60"/><Baseline points="10,56 190,56"/><TextEquiv><Unicode>alt zwei</Unicode></TextEquiv></TextLine>
<TextEquiv><Unicode>alt eins
alt zwei</Unicode></TextEquiv></TextRegion></Page></PcGts>"""


def test_parse_lines_carries_polygon_and_region():
    lines = pagexml.parse_lines(_TK_PAGE)
    assert lines[0]["points"] == [(10, 10), (190, 12), (190, 30), (10, 28)]
    assert lines[0]["region"] == "r1" and lines[1]["baseline"] == [(10, 56), (190, 56)]


def test_new_texts_go_into_transkribus_own_layout():
    out, changed = pagexml.replace_line_texts(_TK_PAGE, ["neu eins", None])
    assert changed == 1
    lines = pagexml.parse_lines(out)
    assert [l["text"] for l in lines] == ["neu eins", "alt zwei"]
    assert lines[0]["points"] == [(10, 10), (190, 12), (190, 30), (10, 28)], "geometry untouched"
    assert 'id="w1"' not in out, "word text would contradict the new line"
    assert "readingOrder {index:0;}" in out and 'id="r1"' in out
    assert "neu eins\nalt zwei" in out, "the region's own TextEquiv follows its lines"
    with pytest.raises(ValueError):
        pagexml.replace_line_texts(_TK_PAGE, ["only one"])


def test_an_empty_reading_does_not_replace_text():
    from ruemlang import pipeline
    assert pipeline._blanks_a_page(_TK_PAGE, ["", None])
    assert pipeline._blanks_a_page(_TK_PAGE, [" ", ""])
    assert not pipeline._blanks_a_page(_TK_PAGE, ["neu", ""]), "one line read is a reading"
    blank = _TK_PAGE.replace("alt eins", "").replace("alt zwei", "").replace(">alt<", "><")
    assert not pipeline._blanks_a_page(blank, ["", ""]), "nothing to lose on an empty page"


def test_page_lines_are_scaled_to_the_image_sent(tmp_path):
    from ruemlang import pipeline, transkribus_pages as tp
    p = tp.PageInfo(1, 2, "t", 5, 1, "u", 200, 100, 9, "IN_PROGRESS", 1, 2, 4, "x", 2)
    (tmp_path / "source.xml").write_text(_TK_PAGE, encoding="utf-8")
    lines = pipeline._page_lines(None, p, tmp_path, scale=2.0)      # sent at half size
    assert lines[0]["baseline"] == [[5.0, 13.0], [95.0, 14.0]]
    assert lines[0]["boundary"][1] == [95.0, 6.0] and lines[0]["regions"] == ["r1"]
    assert lines[1]["order"] == 1
    empty = tp.PageInfo(1, 2, "t", 6, 1, "u", 200, 100, 9, "NEW", 0, 0, 0, None, 0)
    assert pipeline._page_lines(None, empty, tmp_path, scale=1.0) is None


def test_machine_pages_leave_out_corrected_and_hand_edited(monkeypatch, capsys):
    from ruemlang import pipeline, transkribus_pages as tp
    mk = lambda nr, status: tp.PageInfo(1, 2, "t", nr, nr, "u", 1, 1, nr, status, 1, 3, 9, "x", 3)
    pages = [mk(1, "IN_PROGRESS"), mk(2, "IN_PROGRESS"), mk(3, "DONE"), mk(4, "NEW")]
    hist = {1: [{"transcribed_lines": 30, "toolName": "TrHtr"}],                       # HTR only
            2: [{"transcribed_lines": 31}, {"transcribed_lines": 30, "toolName": "TrHtr"}],  # edited
            4: [{"transcribed_lines": 0}]}                                            # empty
    monkeypatch.setattr(tp, "transcript_history", lambda ts, c, d: hist)
    kept = pipeline._machine_pages(None, 1, pages)
    assert [p.page_nr for p in kept] == [1, 4]
    assert "1 corrected (DONE/FINAL) and 1 edited after their HTR: 2" in capsys.readouterr().out


# ── a gateway restart is waited out, not booked as a failed page (26.10.) ───

class _Gw:
    def __init__(self, fails):
        self.fails, self.calls = list(fails), 0

    def recognize(self, send, model, lines=None):
        self.calls += 1
        if self.fails:
            raise self.fails.pop(0)
        return "ok"


def _wrapped(cause):
    err = RuntimeError("ATR gateway unreachable")
    err.__cause__ = cause
    return err


def test_a_restarting_gateway_is_asked_again(monkeypatch):
    import httpx

    from ruemlang import pipeline
    slept = []
    monkeypatch.setattr(pipeline.time, "sleep", slept.append)
    refused = lambda: _wrapped(httpx.ConnectError("[Errno 111] Connection refused"))
    gw = _Gw([refused(), refused()])
    assert pipeline._recognize(gw, "img", "m", None, "k") == "ok"
    assert gw.calls == 3 and slept == [10, 20]
    gw = _Gw([refused() for _ in range(4)])
    with pytest.raises(RuntimeError):
        pipeline._recognize(gw, "img", "m", None, "k")
    assert gw.calls == 4, "three waits, then the page is booked as failed"


def test_only_a_refused_connection_is_retried(monkeypatch):
    import httpx

    from ruemlang import pipeline
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    for err in (_wrapped(httpx.ReadTimeout("slow")), RuntimeError("503 does not fit")):
        gw = _Gw([err])
        with pytest.raises(RuntimeError):
            pipeline._recognize(gw, "img", "m", None, "k")
        assert gw.calls == 1
