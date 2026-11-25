"""#903 — a page set from a TEI edition: the Missiven 1450–1550.

Pinned offline on a three-letter edition built here: what counts as text on the
page, which date a letter has, which letters a selection takes, what is written
and that third_reading reads the result at page level only.

Run from the repo root.
"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from eval import tei_pages as tp  # noqa: E402
from eval import third_reading as tr  # noqa: E402

pytest.importorskip("PIL")

TEI_NS = "http://www.tei-c.org/ns/1.0"


def _image():
    """The real Pillow at run time (test_ah_108 mocks it during collection)."""
    for name in [n for n in list(sys.modules) if n == "PIL" or n.startswith("PIL.")]:
        if type(sys.modules[name]).__module__.startswith("unittest.mock"):
            del sys.modules[name]
    from PIL import Image
    return Image


def _letter(date_xml: str, body: str) -> str:
    return (f'<TEI xmlns="{TEI_NS}"><teiHeader><profileDesc><correspDesc>'
            f'<correspAction type="sent">{date_xml}</correspAction>'
            f'<correspAction type="received"><date when="2024-09-16">x</date></correspAction>'
            f'</correspDesc></profileDesc></teiHeader><text><body>{body}</body></text></TEI>')


BODY = ('<div><pb facs="A_1.JPG" n="1"/><lb/>Min undertaenig <persName>Hans</persName> dienst'
        '<lb/>Als ir <note>Es ist unklar, wer gemeint ist</note>wol wisst'
        '<pb facs="A_2.JPG" n="2"/><lb/>Datum <origDate when="1490-03-02">1490</origDate></div>')


def _edition(tmp: Path, letters: dict[str, str]) -> Path:
    d = tmp / "edition" / "data"
    d.mkdir(parents=True)
    for name, xml in letters.items():
        (d / f"{name}.xml").write_text(xml, encoding="utf-8")
    return tmp / "edition"


def _jpeg(*_a) -> bytes:
    buf = io.BytesIO()
    _image().new("RGB", (60, 40), "white").save(buf, format="JPEG")
    return buf.getvalue()


# ── reading the edition ──────────────────────────────────────────────────────

def test_pages_split_at_pb_lines_at_lb_and_notes_are_not_on_the_page():
    pages = tp.page_texts(_letter('<date when="1490-03-02"/>', BODY))
    assert [p.image for p in pages] == ["A_1.JPG", "A_2.JPG"]
    assert pages[0].lines == ("Min undertaenig Hans dienst", "Als ir wol wisst")
    assert pages[1].lines == ("Datum 1490",)


def test_the_header_is_not_text():
    pages = tp.page_texts(_letter('<date when="1490-03-02">2. März</date>', BODY))
    assert all("März" not in line for p in pages for line in p.lines)


def test_a_document_without_a_body_is_refused():
    with pytest.raises(tp.TeiError, match="body"):
        tp.page_texts(f'<TEI xmlns="{TEI_NS}"><teiHeader/></TEI>')


@pytest.mark.parametrize("date_xml, want", [
    ('<date when="1490-03-02"/>', "1490-03-02"),
    ('<date notBefore="1501" notAfter="1503"/>', "1501"),
    ("<date>1550</date>", "1550"),
    ("<date>ohne Jahr</date>", None),
    ("", None),
])
def test_the_sent_date(date_xml, want):
    assert tp.sent_date(_letter(date_xml, BODY)) == want


def test_the_received_date_is_not_the_letters_date():
    xml = _letter("", BODY)
    assert tp.sent_date(xml) is None, "the received action's 2024 is the editor's, not the letter's"


# ── the selection ────────────────────────────────────────────────────────────

def _letters(years):
    return [tp.Letter(f"l{i:03d}", Path("x"), f"{y}-01-01", y) for i, y in enumerate(years)]


def test_the_closed_interval_and_date_order():
    got = tp.select(_letters([1551, 1450, 1449, 1550, 1500]), (1450, 1550), None)
    assert [lt.year for lt in got] == [1450, 1500, 1550]


def test_a_sample_is_spread_over_the_period_not_its_first_decade():
    got = tp.select(_letters(range(1450, 1551)), (1450, 1550), 5)
    assert [lt.year for lt in got] == [1450, 1475, 1500, 1525, 1550]
    assert [lt.year for lt in tp.select(_letters(range(1450, 1551)), (1450, 1550), 1)] == [1500]


# ── the set ──────────────────────────────────────────────────────────────────

def _build(tmp, fetch=_jpeg, letters=None):
    ed = _edition(tmp, {
        "m_001": _letter('<date when="1449-12-31"/>', BODY.replace("A_", "B_")),
        "m_002": _letter('<date when="1490-03-02"/>', BODY),
        "m_003": _letter("<date>1550</date>", BODY.replace("A_", "C_")),
    })
    logs = []
    res = tp.build(ed, tmp / "set", (1450, 1550), letters, fetch=fetch, log=logs.append)
    return res, logs


def test_the_set_is_written_with_its_selection(tmp_path):
    urls = []
    res, logs = _build(tmp_path, fetch=lambda url: (urls.append(url), _jpeg())[1])
    assert res["letters"] == 2 and res["written"] == 4 and res["failed"] == []
    assert urls[0] == ("https://media.sources-online.org/cantaloupe/iiif/2/"
                       "sg-missiven!A_1.JPG/full/1500,/0/default.jpg")
    rows = list(csv.DictReader(open(tmp_path / "set" / "docs.csv", encoding="utf-8")))
    assert rows[0] == {"page": "m_002/A_1.xml", "doc": "m_002", "year": "1490"}
    assert {r["doc"] for r in rows} == {"m_002", "m_003"}
    sel = json.loads((tmp_path / "set" / "selection.json").read_text())
    assert sel["years"] == [1450, 1550] and sel["letters_in_range"] == 2
    assert [lt["id"] for lt in sel["letters"]] == ["m_002", "m_003"]
    assert "CC-BY-SA" in sel["licence"]


def test_an_image_on_disk_is_not_fetched_again(tmp_path):
    _build(tmp_path)
    urls = []
    res = tp.build(tmp_path / "edition", tmp_path / "set", (1450, 1550), None,
                   fetch=lambda url: (urls.append(url), _jpeg())[1], log=lambda m: None)
    assert urls == [] and res["written"] == 4


def test_a_failed_image_is_named_and_left_out_of_the_set(tmp_path):
    def fetch(url):
        if "A_2" in url:
            raise RuntimeError("503")
        return _jpeg()
    res, logs = _build(tmp_path, fetch=fetch)
    assert res["failed"] == [{"letter": "m_002", "image": "A_2.JPG", "error": "503"}]
    rows = list(csv.DictReader(open(tmp_path / "set" / "docs.csv", encoding="utf-8")))
    assert "m_002/A_2.xml" not in {r["page"] for r in rows}
    assert not (tmp_path / "set" / "pages" / "m_002" / "A_2.JPG.part").exists()


def test_a_dry_run_writes_nothing(tmp_path):
    ed = _edition(tmp_path, {"m_002": _letter('<date when="1490-03-02"/>', BODY)})
    res = tp.build(ed, tmp_path / "set", (1450, 1550), None, dry_run=True,
                   fetch=lambda u: pytest.fail("fetched"), log=lambda m: None)
    assert res == {"letters": 1, "pages": 2, "written": 0, "failed": []}
    assert not (tmp_path / "set").exists()


def test_main_refuses_a_directory_that_is_not_an_edition(tmp_path, capsys):
    assert tp.main([str(tmp_path), "--out", str(tmp_path / "o"), "--years", "1450", "1550"]) == 2
    assert "not a directory" in capsys.readouterr().err
    assert tp.main([str(tmp_path), "--out", str(tmp_path / "o"), "--years", "1550", "1450"]) == 2


# ── read by third_reading ────────────────────────────────────────────────────

def test_third_reading_reads_the_whole_page_text_and_no_line_level(tmp_path):
    _build(tmp_path)
    pages = tr.load_set({"name": "m", "pages": "pages", "docs": "docs.csv",
                         "years": [1450, 1550]}, tmp_path / "set")
    assert len(pages) == 4 and all(p.lines == [] for p in pages)
    first = next(p for p in pages if p.key == "m_002/A_1.xml")
    assert first.reference == "Min undertaenig Hans dienst\nAls ir wol wisst"
    assert first.doc == "m_002" and first.year == 1490

    plan = tmp_path / "set" / "plan.json"
    plan.write_text(json.dumps({"sets": [{"name": "m", "pages": "pages", "docs": "docs.csv",
                                          "kraken": "k", "trocr": "t", "qwen": "q"}]}))
    logs = []
    args = tr.build_parser().parse_args([str(plan), "--out", str(tmp_path / "run"), "--dry-run"])
    assert tr.run(args, gateway_factory=lambda: pytest.fail("network"), log=logs.append) == 0
    assert any("page level only" in m for m in logs)


def test_a_set_with_geometry_keeps_its_line_level(tmp_path):
    """The page-level-only rule is about the set, not about one page: a PAGE
    set with coordinates is unchanged."""
    from test_ah_903_third_reading import BOXES, REFS, FakeGateway, _write_page
    _write_page(tmp_path / "pages" / "d1", "p0", list(zip(REFS, BOXES)))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"sets": [{"name": "s", "pages": "pages", "kraken": "kr",
                                          "trocr": "tr", "qwen": "qline"}]}))
    logs = []
    args = tr.build_parser().parse_args([str(plan), "--out", str(tmp_path / "run")])
    tr.run(args, gateway_factory=FakeGateway, log=logs.append)
    rep = json.loads((tmp_path / "run" / "report.json").read_text())
    assert sorted(g["level"] for g in rep["groups"]) == ["line", "page"]
    assert not any("page level only" in m for m in logs)


def test_a_set_without_geometry_is_scored_at_page_level(tmp_path):
    from test_ah_903_third_reading import FakeGateway
    _build(tmp_path)
    plan = tmp_path / "set" / "plan.json"
    plan.write_text(json.dumps({"sets": [{"name": "m", "pages": "pages", "docs": "docs.csv",
                                          "kraken": "kr", "trocr": "tr", "qwen": "qline"}]}))
    gw = FakeGateway()
    args = tr.build_parser().parse_args([str(plan), "--out", str(tmp_path / "run")])
    tr.run(args, gateway_factory=lambda: gw, log=lambda m: None)
    rep = json.loads((tmp_path / "run" / "report.json").read_text())
    assert [g["level"] for g in rep["groups"]] == ["page"]
    a1 = next(a for a in rep["groups"][0]["arms"] if a["arm"] == "A1")
    assert a1["items"] == 4 and a1["chars"] > 0
    assert not any(c[0] == "recognize" and c[2] == [] for c in gw.calls)


def test_a_page_without_text_is_not_a_page_of_the_set():
    """The blank back of a letter has an image and no reference: scoring a
    reading against nothing would count every character a model saw as an
    error, so it is left out."""
    body = '<div><pb facs="A_1.JPG"/><lb/>Min dienst<pb facs="A_2.JPG"/><pb facs="A_3.JPG"/><lb/>Datum</div>'
    pages = tp.page_texts(_letter('<date when="1490-03-02"/>', body))
    assert [p.image for p in pages] == ["A_1.JPG", "A_3.JPG"]
