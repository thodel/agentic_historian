"""#687 — a manifest on the research share becomes a ground-truth set under GT_ROOT.

On 2026-10-22 the HTR project's `eval_manifest_random.txt` (39 PAGE XML files,
12 documents) reached `GT_ROOT/eval-random` through a `while read … cp` loop, a
path remap (`/storage/research/…` on the machine that wrote it, `/mnt/…` on tei)
and two one-liners counting documents and statuses. Everything after that was a
tool call. This is the step before, with its finding, and with the same
containment rule the other roots have.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
from mcp_atr import jobs                   # noqa: E402

FOREIGN = "/storage/research/wbkolleg_dh_1/Projekt_Lassberg"

PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15">
  <Metadata><TranskribusMetadata docId="{doc}" pageId="{pg}" status="{status}"/></Metadata>
  <Page imageFilename="{img}" imageWidth="100" imageHeight="100">
    <TextRegion id="r1"><Coords points="0,0 1,0 1,1 0,1"/>
      <TextEquiv><Unicode>{text}</Unicode></TextEquiv>
    </TextRegion>
  </Page>
</PcGts>
"""


def _page(path: Path, doc="1", pg="1", status="FINAL", text="Hochwohlgeborner Herr"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PAGE.format(doc=doc, pg=pg, status=status,
                                img=f"{pg}.tif", text=text), encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A research root `Projekt_Lassberg` with two documents and a manifest
    written on the other machine, plus a GT_ROOT."""
    root = tmp_path / "mnt" / "wbkolleg_dh_1" / "Projekt_Lassberg"
    docs = root / "HTR" / "data" / "ground_truth" / "documents"
    _page(docs / "done_K 2911,17_9985431" / "001.xml", doc="9985431", pg="1")
    _page(docs / "done_K 2911,17_9985431" / "002.xml", doc="9985431", pg="2",
          status="DONE")
    _page(docs / "done_lassberg-letter-0892_7609939" / "001.xml", doc="7609939",
          pg="1")
    _page(docs / "done_lassberg-letter-0892_7609939" / "002.xml", doc="7609939",
          pg="2", text="")                       # a page without text
    manifests = root / "HTR" / "data" / "ground_truth" / "manifests"
    manifests.mkdir(parents=True)
    (manifests / "eval_manifest_random.txt").write_text(
        f"{FOREIGN}/HTR/data/ground_truth/documents/done_K 2911,17_9985431/001.xml\n"
        f"{FOREIGN}/HTR/data/ground_truth/documents/done_K 2911,17_9985431/002.xml\n"
        f"{FOREIGN}/HTR/data/ground_truth/documents/done_lassberg-letter-0892_7609939/001.xml\n"
        f"{FOREIGN}/HTR/data/ground_truth/documents/done_lassberg-letter-0892_7609939/002.xml\n",
        encoding="utf-8")
    (tmp_path / "neighbour").mkdir()
    (tmp_path / "neighbour" / "secret.xml").write_text("x", encoding="utf-8")
    monkeypatch.setattr(config, "ATR_RESEARCH_DIR", root)
    monkeypatch.setattr(config, "GT_ROOT", tmp_path / "gt")
    return root


MANIFEST = "HTR/data/ground_truth/manifests/eval_manifest_random.txt"


# ── the import, as it was done by hand ───────────────────────────────────────

def test_the_import_remaps_copies_and_names_by_document(project, tmp_path):
    result = jobs.import_ground_truth(MANIFEST, "eval-random")

    dest = tmp_path / "gt" / "eval-random"
    assert result["ok"] and result["ground_truth_dir"] == str(dest)
    assert sorted(p.name for p in dest.iterdir()) == [
        "done_K 2911,17_9985431__001.xml",
        "done_K 2911,17_9985431__002.xml",
        "done_lassberg-letter-0892_7609939__001.xml",
        "done_lassberg-letter-0892_7609939__002.xml",
    ]
    assert result["lines"] == 4 and result["files"] == 4 and result["copied"] == 4
    assert result["missing"] == [] and result["unmappable"] == []


def test_the_finding_is_the_one_the_shell_produced(project):
    """Documents with page counts, the status mix, the files without text."""
    result = jobs.import_ground_truth(MANIFEST, "eval-random")

    assert result["documents"] == {"done_K 2911,17_9985431": 2,
                                   "done_lassberg-letter-0892_7609939": 2}
    assert result["statuses"] == {"FINAL": 2, "DONE": 1, "unreadable": 1}
    assert [u["file"] for u in result["unusable"]] == [
        "done_lassberg-letter-0892_7609939__002.xml"]
    assert "no transcribed text" in result["unusable"][0]["reason"]
    assert "score_ground_truth" in result["next"] and "'eval-random'" in result["next"]


def test_a_dry_run_reports_and_copies_nothing(project, tmp_path):
    result = jobs.import_ground_truth(MANIFEST, "eval-random", dry_run=True)
    assert result["dry_run"] and result["copied"] == 4
    assert not (tmp_path / "gt" / "eval-random").exists()


def test_a_second_import_skips_what_is_already_there(project):
    jobs.import_ground_truth(MANIFEST, "eval-random")
    again = jobs.import_ground_truth(MANIFEST, "eval-random")
    assert again["copied"] == 0 and again["already_present"] == 4


def test_a_missing_file_and_an_unmappable_line_are_named_not_dropped(project):
    manifest = project / "HTR/data/ground_truth/manifests/odd.txt"
    manifest.write_text(
        f"{FOREIGN}/HTR/data/ground_truth/documents/done_K 2911,17_9985431/001.xml\n"
        f"{FOREIGN}/HTR/data/ground_truth/documents/done_gone_1/001.xml\n"
        "/somewhere/else/entirely/001.xml\n"
        "# a comment, and a blank line\n\n",
        encoding="utf-8")

    result = jobs.import_ground_truth("HTR/data/ground_truth/manifests/odd.txt",
                                      "odd")

    assert result["files"] == 1
    assert result["missing"] == ["HTR/data/ground_truth/documents/done_gone_1/001.xml"]
    assert result["unmappable"] == ["/somewhere/else/entirely/001.xml"]


def test_a_relative_line_is_taken_as_it_is(project):
    manifest = project / "HTR/data/ground_truth/manifests/rel.txt"
    manifest.write_text(
        "HTR/data/ground_truth/documents/done_K 2911,17_9985431/001.xml\n",
        encoding="utf-8")
    result = jobs.import_ground_truth("HTR/data/ground_truth/manifests/rel.txt",
                                      "rel")
    assert result["files"] == 1 and result["unmappable"] == []


def test_two_lines_landing_on_one_copy_name_stop_the_import(project, tmp_path):
    """The document folder and file name are the identity; when they are not,
    nothing is written, because a silent overwrite is a page that vanished."""
    docs = project / "HTR/data/ground_truth"
    _page(docs / "other" / "done_K 2911,17_9985431" / "001.xml", doc="x")
    manifest = project / "HTR/data/ground_truth/manifests/dup.txt"
    manifest.write_text(
        f"{FOREIGN}/HTR/data/ground_truth/documents/done_K 2911,17_9985431/001.xml\n"
        f"{FOREIGN}/HTR/data/ground_truth/other/done_K 2911,17_9985431/001.xml\n",
        encoding="utf-8")
    with pytest.raises(jobs.JobError) as err:
        jobs.import_ground_truth("HTR/data/ground_truth/manifests/dup.txt", "dup")
    assert "same copy name" in str(err.value)
    assert not (tmp_path / "gt" / "dup").exists()


def test_a_manifest_that_names_nothing_usable_is_an_error(project):
    manifest = project / "HTR/data/ground_truth/manifests/none.txt"
    manifest.write_text("/elsewhere/a.xml\n", encoding="utf-8")
    with pytest.raises(jobs.JobError) as err:
        jobs.import_ground_truth("HTR/data/ground_truth/manifests/none.txt", "none")
    assert "1 unmappable" in str(err.value)


# ── containment: a second root, the same rule ────────────────────────────────

def test_the_manifest_path_cannot_leave_the_research_root(project):
    with pytest.raises(jobs.JobError):
        jobs.import_ground_truth("../../../neighbour/secret.xml", "x")
    with pytest.raises(jobs.JobError):
        jobs.import_ground_truth("/etc/passwd", "x")


def test_a_manifest_line_cannot_reach_beside_the_root(project, tmp_path):
    """The lines are foreign data: one that climbs out is unmappable, not read."""
    manifest = project / "HTR/data/ground_truth/manifests/climb.txt"
    manifest.write_text(
        f"{FOREIGN}/../neighbour/secret.xml\n"
        f"{FOREIGN}/HTR/data/ground_truth/documents/done_K 2911,17_9985431/001.xml\n",
        encoding="utf-8")
    result = jobs.import_ground_truth("HTR/data/ground_truth/manifests/climb.txt",
                                      "climb")
    assert result["files"] == 1
    assert result["unmappable"] == [f"{FOREIGN}/../neighbour/secret.xml"]
    assert not any("secret" in p.name
                   for p in (tmp_path / "gt" / "climb").iterdir())


def test_the_set_name_is_a_name_not_a_path(project):
    with pytest.raises(jobs.JobError):
        jobs.import_ground_truth(MANIFEST, "../../etc")


def test_an_unset_root_says_how_to_set_it(project, monkeypatch):
    monkeypatch.setattr(config, "ATR_RESEARCH_DIR", None)
    with pytest.raises(jobs.JobError) as err:
        jobs.import_ground_truth(MANIFEST, "x")
    assert "ATR_RESEARCH_DIR" in str(err.value)
    with pytest.raises(jobs.JobError):
        jobs.research_list()


# ── the listing ──────────────────────────────────────────────────────────────

def test_the_listing_is_one_level_with_sizes(project):
    result = jobs.research_list("HTR/data/ground_truth/manifests")
    assert [e["name"] for e in result["entries"]] == ["eval_manifest_random.txt"]
    assert result["entries"][0]["type"] == "file"
    assert result["entries"][0]["bytes"] > 0
    top = jobs.research_list()
    assert [e["name"] for e in top["entries"]] == ["HTR"]
    assert top["entries"][0]["type"] == "dir" and top["entries"][0]["bytes"] is None


def test_the_listing_caps_and_says_so(project):
    docs = project / "HTR/data/ground_truth/documents"
    result = jobs.research_list("HTR/data/ground_truth/documents", limit=1)
    assert result["count"] == 2 and len(result["entries"]) == 1
    assert result["truncated"]
    assert docs.is_dir()


def test_the_listing_cannot_leave_the_root(project):
    with pytest.raises(jobs.JobError):
        jobs.research_list("../neighbour")
    with pytest.raises(jobs.JobError):
        jobs.research_list("HTR/no-such-folder")


def test_the_tools_are_on_the_server():
    src = (PKG / "mcp_atr" / "server.py").read_text(encoding="utf-8")
    assert "def research_list(" in src and "def import_ground_truth(" in src
