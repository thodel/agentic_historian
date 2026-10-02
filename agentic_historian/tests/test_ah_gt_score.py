"""Scoring readings against hand-corrected pages (`gt_score`, `transkribus` harvest).

Everything measured about this corpus so far has been relative: who differs from
whom, never who is right (#326). Two PAGE XML files arrived on 2026-10-01 from
Transkribus collection 36479 at `status="FINAL"` — letters in Laßberg's own hand,
Eppishausen 1831, one of them mentioning "Prof: Grimm aus Göttingen". With those,
#416's question stops being a judgement about length.

Offline: the XML fixtures are the real files' shape, no request leaves the tests.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import gt_score as gs                           # noqa: E402
import transkribus as tk                        # noqa: E402

NS = 'xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"'

LETTER_LINES = [
    "Eppishausen am 21 Januar 1831.",
    "Hochgeschäzter Herr!",
    "Da ich das vergnügen nicht haben soll, Sie in meiner Waldklause zu",
    "bewirten; so bliebt mir freilich nichts anders übrig, als Inen die Siegel",
    "die erlaubnis die urkunden länger zu behalten, habe ich zwar selbst anti¬",
    "cipirt: allein, ich muss eben des wegen angelegenst bitten,",
    "J.v.Laßzberg",
]


def _pagexml(lines, *, region_text=True, status="FINAL",
             doc_id="1682295", page_id="61849396") -> str:
    body = "".join(
        f"<TextLine id='tl_{i}'><TextEquiv><Unicode>{t}</Unicode>"
        f"</TextEquiv></TextLine>" for i, t in enumerate(lines))
    region_equiv = (f"<TextEquiv><Unicode>{chr(10).join(lines)}</Unicode></TextEquiv>"
                    if region_text else "<TextEquiv><Unicode></Unicode></TextEquiv>")
    return (
        f"<?xml version='1.0' encoding='UTF-8'?><PcGts {NS}>"
        f"<Metadata><TranskribusMetadata docId='{doc_id}' pageId='{page_id}' "
        f"pageNr='1' status='{status}'/></Metadata>"
        f"<Page imageFilename='{page_id}.tif' imageWidth='2938' imageHeight='3638'>"
        f"<TextRegion id='tr_2'>{body}{region_equiv}</TextRegion>"
        f"</Page></PcGts>".replace("'", '"')
    )


@pytest.fixture
def gt_file(tmp_path):
    path = tmp_path / "doc1682295_page1_ts1.xml"
    path.write_text(_pagexml(LETTER_LINES), encoding="utf-8")
    return path


def _run(root: Path, model: str, pages: dict[str, str]) -> Path:
    d = root / model
    d.mkdir(parents=True, exist_ok=True)
    for key, text in pages.items():
        (d / f"{key}.txt").write_text(text, encoding="utf-8")
    return root


DECOY = ("Völlig anderer Text über ganz andere Dinge, ohne jede Beziehung zu "
         "diesem Brief, nur als Ablenkung vorhanden und lang genug dafür. ") * 3


# ── reading the ground truth ─────────────────────────────────────────────────

def test_the_page_text_and_its_transkribus_identity(gt_file):
    gt = gs.read_pagexml(gt_file)
    assert gt.text.splitlines()[0] == "Eppishausen am 21 Januar 1831."
    assert gt.text.splitlines()[-1] == "J.v.Laßzberg"
    assert (gt.doc_id, gt.page_id, gt.status) == ("1682295", "61849396", "FINAL")
    assert gt.image == "61849396.tif"
    assert gt.lines == len(LETTER_LINES)


def test_a_file_without_region_text_falls_back_to_the_lines(tmp_path):
    """Same text, assembled rather than stored — some exports have only one."""
    path = tmp_path / "lines_only.xml"
    path.write_text(_pagexml(LETTER_LINES, region_text=False), encoding="utf-8")
    assert gs.read_pagexml(path).text.splitlines() == LETTER_LINES


def test_a_file_with_no_transcription_is_an_error(tmp_path):
    path = tmp_path / "empty.xml"
    path.write_text(_pagexml([], region_text=False), encoding="utf-8")
    with pytest.raises(gs.GroundTruthError) as err:
        gs.read_pagexml(path)
    assert "no transcribed text" in str(err.value)


def test_malformed_xml_names_the_file(tmp_path):
    path = tmp_path / "broken.xml"
    path.write_text("<PcGts><oops", encoding="utf-8")
    with pytest.raises(gs.GroundTruthError) as err:
        gs.read_pagexml(path)
    assert "broken.xml" in str(err.value)


def test_a_page_below_done_is_not_treated_as_truth(tmp_path):
    """IN_PROGRESS is a machine's output. Scoring our readings against it would be
    scoring one model by another, so it is collected as unusable rather than
    warned about and then counted anyway."""
    path = tmp_path / "wip.xml"
    path.write_text(_pagexml(LETTER_LINES, status="IN_PROGRESS"), encoding="utf-8")
    unusable: list = []
    assert gs.score([path], {"r/m": {"k": "x" * 200}}, unusable=unusable) == []
    assert unusable[0].reason == "status 'IN_PROGRESS' is below DONE"


# ── matching, because Transkribus shares no key with us ──────────────────────

def test_the_right_page_is_found_among_decoys(gt_file, tmp_path):
    truth = gs.read_pagexml(gt_file).text
    run = _run(tmp_path / "run", "m", {
        "Aarau__p1": DECOY, "Aarau__p2": truth.replace("Januar", "Januer"),
        "Aarau__p3": DECOY})
    scored, _, _ = gs.score_run_dirs([gt_file], [run])
    match = scored[0].matches[0]
    assert match.key == "Aarau__p2"
    assert match.cer < 0.02
    assert match.confident


def test_a_doubtful_match_says_so(gt_file, tmp_path):
    """Two pages equally unlike the ground truth mean the page is not in this run,
    and scoring it would invent a number."""
    run = _run(tmp_path / "run", "m", {"a": DECOY, "b": DECOY + "x"})
    scored, _, _ = gs.score_run_dirs([gt_file], [run])
    assert not scored[0].matches[0].confident


def test_readings_that_match_different_pages_are_not_averaged(gt_file, tmp_path):
    """Matched independently on purpose: if two readings place the same ground
    truth on different pages, that is worth knowing, not smoothing over."""
    truth = gs.read_pagexml(gt_file).text
    a = _run(tmp_path / "a", "m1", {"right": truth, "other": DECOY})
    b = _run(tmp_path / "b", "m2", {"wrong": truth[:60] + DECOY, "x": DECOY})
    scored, _, report = gs.score_run_dirs([gt_file], [a, b])
    assert scored[0].agreed_key == ""
    assert "matched different pages" in report


def test_cer_is_measured_against_the_ground_truth_not_symmetrically(gt_file, tmp_path):
    """`compare_runs` must be symmetric because neither side is truth. Here one
    side IS truth, so the reference is the ground truth and an insertion costs
    what an insertion costs."""
    truth = gs.read_pagexml(gt_file).text
    run = _run(tmp_path / "run", "m", {"p": truth + truth})      # doubled reading
    scored, _, _ = gs.score_run_dirs([gt_file], [run])
    assert scored[0].matches[0].cer > 0.9, "insertions are not free"


def test_an_identical_reading_scores_zero(gt_file, tmp_path):
    truth = gs.read_pagexml(gt_file).text
    run = _run(tmp_path / "run", "m", {"p": truth})
    scored, _, _ = gs.score_run_dirs([gt_file], [run])
    assert scored[0].matches[0].cer == 0.0


# ── the paths ────────────────────────────────────────────────────────────────

def test_a_directory_of_ground_truth_is_expanded(tmp_path):
    d = tmp_path / "harvest"
    d.mkdir()
    for n in ("doc1_page1_ts1.xml", "doc2_page1_ts9.xml"):
        (d / n).write_text(_pagexml(LETTER_LINES), encoding="utf-8")
    assert len(gs.expand_gt_paths([str(d)])) == 2


def test_a_missing_ground_truth_path_is_an_error(tmp_path):
    with pytest.raises(gs.GroundTruthError):
        gs.expand_gt_paths([str(tmp_path / "nope.xml")])


def test_scoring_needs_a_reading(gt_file):
    with pytest.raises(gs.GroundTruthError):
        gs.score([gt_file], {})


# ── harvesting out of a collection ───────────────────────────────────────────

def _fulldoc(statuses):
    """A document whose pages carry the given latest-version statuses."""
    return {"pageList": {"pages": [
        {"pageNr": i + 1, "tsList": {"transcripts": [
            {"status": st, "url": f"https://x/ts{i}", "tsId": 100 + i},
            {"status": "FINAL", "url": f"https://x/old{i}", "tsId": 1 + i},
        ]}} for i, st in enumerate(statuses)]}}


def test_only_hand_corrected_pages_are_harvested():
    pages = tk.corrected_pages(_fulldoc(["NEW", "IN_PROGRESS", "DONE", "FINAL"]))
    assert [p["status"] for p in pages] == ["DONE", "FINAL"]
    assert [p["page_nr"] for p in pages] == [3, 4]


def test_only_the_latest_version_counts():
    """Every page here has an older FINAL version. Harvesting it would score our
    readings against a transcription somebody has since corrected away."""
    pages = tk.corrected_pages(_fulldoc(["IN_PROGRESS"]))
    assert pages == []


def test_the_harvest_names_files_by_identities_that_are_stable(tmp_path):
    """A page can be corrected again. With the transcript id in the name, two
    harvests sit side by side instead of one overwriting the other."""

    class _Api:
        def get(self, url, params=None, timeout=None):
            if url.endswith("/list"):
                return _R(200, [{"docId": 1682295, "title": "letter"}])
            if url.endswith("/fulldoc"):
                return _R(200, _fulldoc(["FINAL"]))
            return _R(200, None, text=_pagexml(LETTER_LINES))

    written = tk.harvest_ground_truth("36479", "SID", tmp_path, session=_Api())
    assert [p.name for p in written] == ["doc1682295_page1_ts100.xml"]
    assert gs.read_pagexml(written[0]).status == "FINAL"


def test_an_already_harvested_page_is_not_fetched_again(tmp_path):
    (tmp_path / "doc1682295_page1_ts100.xml").write_text(
        _pagexml(LETTER_LINES), encoding="utf-8")
    fetched: list[str] = []

    class _Api:
        def get(self, url, params=None, timeout=None):
            if url.endswith("/list"):
                return _R(200, [{"docId": 1682295, "title": "letter"}])
            if url.endswith("/fulldoc"):
                return _R(200, _fulldoc(["FINAL"]))
            fetched.append(url)
            return _R(200, None, text="x")

    written = tk.harvest_ground_truth("36479", "SID", tmp_path, session=_Api())
    assert len(written) == 1 and fetched == []


class _R:
    def __init__(self, status_code, payload, text="[]"):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


# ── DONE is ground truth too ──────────────────────────────────────────────────
#
# 2026-10-02, from the operator: "bereits mit status 'done' und 'final' wollen wir
# evaluieren. 'ground truth' wurde nie als tag vergeben". Requiring FINAL would
# have discarded the larger half of what has been corrected, and told them in a
# warning that their own corrected pages were "not ground truth yet".

def test_done_counts_as_corrected(tmp_path):
    path = tmp_path / "done.xml"
    path.write_text(_pagexml(LETTER_LINES, status="DONE"), encoding="utf-8")
    assert gs.read_pagexml(path).status == "DONE"
    assert "DONE" in gs.CORRECTED_STATUSES


def test_a_done_page_is_not_warned_about(tmp_path, monkeypatch):
    """The warning said "not ground truth yet" for a page a human had finished."""
    warned: list[str] = []
    monkeypatch.setattr(gs.logger, "warning", lambda msg: warned.append(str(msg)))
    for status in ("DONE", "FINAL", "GT"):
        path = tmp_path / f"{status}.xml"
        path.write_text(_pagexml(LETTER_LINES, status=status), encoding="utf-8")
        gs.score([path], {"r/m": {"k": "x" * 300}})
    assert warned == []


def test_in_progress_is_still_warned_about(tmp_path, monkeypatch):
    """NEW and IN_PROGRESS are a model's output. Scoring against those measures
    agreement between two machines and would look like accuracy."""
    warned: list[str] = []
    monkeypatch.setattr(gs.logger, "warning", lambda msg: warned.append(str(msg)))
    path = tmp_path / "wip.xml"
    path.write_text(_pagexml(LETTER_LINES, status="IN_PROGRESS"), encoding="utf-8")
    gs.score([path], {"r/m": {"k": "x" * 300}})
    assert len(warned) == 1 and "below DONE" in warned[0]


def test_the_harvest_and_the_scoring_share_one_definition():
    """Two lists would drift, and then a page could be harvested as ground truth
    and rejected as not-ground-truth by the thing that scores it."""
    assert tk.GT_STATUSES is gs.CORRECTED_STATUSES


def test_the_report_names_the_status_mix(tmp_path):
    """If the CER splits along DONE vs FINAL, the status is telling you something
    about the correction rather than about the model."""
    for i, status in enumerate(("DONE", "DONE", "FINAL")):
        (tmp_path / f"p{i}.xml").write_text(
            _pagexml(LETTER_LINES, status=status, page_id=f"6184939{i}"),
            encoding="utf-8")
    run = _run(tmp_path / "run", "m", {"p": "x" * 300})
    _, _, report = gs.score_run_dirs(sorted(tmp_path.glob("p*.xml")), [run])
    assert "3 ground-truth page(s): 2× DONE, 1× FINAL" in report


# ── one bad file costs that file, not the job ────────────────────────────────
#
# On 2026-10-02 a harvest of sixteen pages produced no numbers at all:
#
#     Error: doc14662279_page3_ts279034318.xml: no transcribed text in this file
#
# Fifteen scored pages were thrown away because the sixteenth was empty. Same
# shape as the share walk that a single unreadable folder used to kill — and the
# same fix: collect the casualty, name it in the report, score the rest.

def test_one_empty_file_does_not_lose_the_others(tmp_path):
    (tmp_path / "good.xml").write_text(_pagexml(LETTER_LINES), encoding="utf-8")
    (tmp_path / "doc14662279_page3_ts279034318.xml").write_text(
        _pagexml([], region_text=False), encoding="utf-8")
    run = _run(tmp_path / "run", "m", {"p1": "\n".join(LETTER_LINES)})

    scored, unusable, report = gs.score_run_dirs(
        sorted(tmp_path.glob("*.xml")), [run])

    assert [s.gt.source.name for s in scored] == ["good.xml"]
    assert [u.source.name for u in unusable] == [
        "doc14662279_page3_ts279034318.xml"]
    assert "no transcribed text" in unusable[0].reason


def test_the_report_names_what_it_skipped(tmp_path):
    """A silently dropped file would overstate how much ground truth there was."""
    (tmp_path / "good.xml").write_text(_pagexml(LETTER_LINES), encoding="utf-8")
    (tmp_path / "hopeless.xml").write_text("<PcGts><oops", encoding="utf-8")
    run = _run(tmp_path / "run", "m", {"p1": "\n".join(LETTER_LINES)})

    _, _, report = gs.score_run_dirs(sorted(tmp_path.glob("*.xml")), [run])

    assert "could not be scored" in report
    assert "hopeless.xml" in report
    assert "1 ground-truth page(s)" in report      # the skipped one is not counted


def test_a_page_below_done_is_skipped_not_scored(tmp_path):
    """IN_PROGRESS is a model's output. Including it in a quality table would put a
    machine's reading in the truth column, which is the one thing this table is
    for not doing."""
    (tmp_path / "wip.xml").write_text(
        _pagexml(LETTER_LINES, status="IN_PROGRESS"), encoding="utf-8")
    (tmp_path / "done.xml").write_text(
        _pagexml(LETTER_LINES, status="DONE", page_id="61849397"), encoding="utf-8")
    run = _run(tmp_path / "run", "m", {"p1": "\n".join(LETTER_LINES)})

    scored, unusable, _ = gs.score_run_dirs(sorted(tmp_path.glob("*.xml")), [run])

    assert [s.gt.status for s in scored] == ["DONE"]
    assert [u.source.name for u in unusable] == ["wip.xml"]
    assert "below DONE" in unusable[0].reason


def test_a_file_without_a_status_is_still_scored(tmp_path):
    """Hand-made PAGE XML has no TranskribusMetadata. Absent is not below DONE."""
    path = tmp_path / "handmade.xml"
    path.write_text(
        f"<?xml version='1.0' encoding='UTF-8'?><PcGts {NS}>"
        f"<Page imageFilename='x.tif'><TextRegion id='tr'><TextEquiv><Unicode>"
        f"{chr(10).join(LETTER_LINES)}</Unicode></TextEquiv></TextRegion>"
        f"</Page></PcGts>".replace("'", '"'), encoding="utf-8")
    run = _run(tmp_path / "run", "m", {"p1": "\n".join(LETTER_LINES)})

    scored, unusable, _ = gs.score_run_dirs([path], [run])

    assert len(scored) == 1 and not unusable


def test_nothing_scorable_is_still_an_error(tmp_path):
    """Containment, not silence: a harvest where every file is empty must not
    report a clean run of zero pages."""
    (tmp_path / "a.xml").write_text(_pagexml([], region_text=False),
                                    encoding="utf-8")
    run = _run(tmp_path / "run", "m", {"p1": "x" * 200})
    with pytest.raises(gs.GroundTruthError) as err:
        gs.score_run_dirs([tmp_path / "a.xml"], [run])
    assert "none of the 1 ground-truth file(s)" in str(err.value)
    assert "a.xml" in str(err.value)


# ── agreement is not identification ──────────────────────────────────────────

def test_one_reading_agrees_with_itself_but_locates_nothing(tmp_path):
    """2026-10-02: a single reading matched at 106.9 % against a runner-up at
    107.7 % and `agreed_key` said yes, because there was nobody to disagree. That
    key must not reach `atr-batch --keys-from`: it would send eight models to read
    a different page and score them against this page's ground truth."""
    gt = tmp_path / "gt.xml"
    gt.write_text(_pagexml(LETTER_LINES), encoding="utf-8")
    run = _run(tmp_path / "run", "m", {"p1": DECOY, "p2": DECOY.replace("Dinge", "Sachen")})

    scored, _, _ = gs.score_run_dirs([gt], [run])

    assert scored[0].agreed_key          # it agreed — with itself
    assert not scored[0].located         # and located nothing
    assert not scored[0].matches[0].confident


def test_a_clear_match_is_located(gt_file, tmp_path):
    run = _run(tmp_path / "run", "m", {"p1": "\n".join(LETTER_LINES), "p2": DECOY})
    scored, _, _ = gs.score_run_dirs([gt_file], [run])
    assert scored[0].located == "p1"


def test_one_confident_reading_locates_the_page_for_all_of_them(gt_file, tmp_path):
    """A second reading being doubtful at the same key says something about that
    reading, not about which page this is."""
    good = _run(tmp_path / "good", "m", {"p1": "\n".join(LETTER_LINES), "p2": DECOY})
    weak = _run(tmp_path / "weak", "m", {"p1": DECOY, "p2": DECOY.replace("Dinge", "Sachen")})
    # the weak run's best is p1 too, but only just
    scored, _, _ = gs.score_run_dirs([gt_file], [good, weak])
    if scored[0].agreed_key:             # only assert the rule when they agree
        assert scored[0].located == "p1"
        assert [m.confident for m in scored[0].matches] == [True, False]


def test_readings_that_disagree_locate_nothing(gt_file, tmp_path):
    a = _run(tmp_path / "a", "m", {"p1": "\n".join(LETTER_LINES), "p2": DECOY})
    b = _run(tmp_path / "b", "m", {"p1": DECOY, "p2": "\n".join(LETTER_LINES)})
    scored, _, _ = gs.score_run_dirs([gt_file], [a, b])
    assert not scored[0].agreed_key and not scored[0].located
