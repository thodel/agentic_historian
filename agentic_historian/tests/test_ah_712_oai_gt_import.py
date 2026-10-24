"""#712 — a library's OAI-PMH set of images + ALTO as a ground-truth tree.

On 23/24 October the BLB's Laßberg set became `lassberg-blb-gt` through three
scripts, `nohup` and a log in /tmp. This is the same as a module, a CLI command
and a job tool — with the sources as names in code, downloads only from the
source's host, and two faults of the scripts fixed: a rebuild after a split
wrote held-out pages back into training, and it overwrote the manifest.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import hf_export as hf                     # noqa: E402
import oai_gt                              # noqa: E402
import tree_split as ts                    # noqa: E402
from mcp_atr import jobs                   # noqa: E402

BASE = "https://digital.blb-karlsruhe.de"
SRC = oai_gt.SOURCES["blb-lassberg"]


def _record(vl, shelf, title, pages, *, with_files=True, deleted=False):
    if deleted:
        return (f'<record><header status="deleted"><identifier>oai:x:{vl}</identifier>'
                f'</header></record>')
    files_max = "".join(f'<mets:file ID="IMG{vl}_{i}"><mets:FLocat xlink:href="{BASE}/download/webcache/0/{vl}{i}"/></mets:file>'
                        for i in range(1, pages + 1))
    files_ft = "".join(f'<mets:file ID="FT{vl}_{i}"><mets:FLocat xlink:href="{BASE}/download/fulltext/alto3/{vl}{i}"/></mets:file>'
                       for i in range(1, pages + 1))
    divs = "".join(f'<mets:div TYPE="page" ORDER="{i}"><mets:fptr FILEID="IMG{vl}_{i}"/>'
                   f'<mets:fptr FILEID="FT{vl}_{i}"/></mets:div>' for i in range(1, pages + 1))
    filesec = (f'<mets:fileSec><mets:fileGrp USE="MAX">{files_max}</mets:fileGrp>'
               f'<mets:fileGrp USE="FULLTEXT">{files_ft}</mets:fileGrp></mets:fileSec>'
               f'<mets:structMap TYPE="PHYSICAL"><mets:div TYPE="physSequence">{divs}</mets:div></mets:structMap>'
               if with_files else "")
    return f"""<record><header><identifier>oai:digital.blb-karlsruhe.de:{vl}</identifier></header>
<metadata><mets:mets xmlns:mets="http://www.loc.gov/METS/" xmlns:mods="http://www.loc.gov/mods/v3"
 xmlns:xlink="http://www.w3.org/1999/xlink"><mets:dmdSec><mets:mdWrap><mets:xmlData><mods:mods>
<mods:titleInfo><mods:title>{title}</mods:title></mods:titleInfo>
<mods:location><mods:shelfLocator>{shelf}</mods:shelfLocator></mods:location>
<mods:originInfo><mods:dateCreated>1834</mods:dateCreated></mods:originInfo>
</mods:mods></mets:xmlData></mets:mdWrap></mets:dmdSec>{filesec}</mets:mets></metadata></record>"""


def _oai_page(records, token=""):
    tok = f"<resumptionToken>{token}</resumptionToken>" if token else "<resumptionToken/>"
    return (f'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>'
            f'{"".join(records)}{tok}</ListRecords></OAI-PMH>').encode()


def _alto(lines):
    tl = "".join(f'<TextLine HPOS="10" VPOS="{20 * i}" WIDTH="100" HEIGHT="15">'
                 + "".join(f'<String CONTENT="{w}"/><SP/>' for w in ln.split()) + "</TextLine>"
                 for i, ln in enumerate(lines, 1))
    return (f'<alto xmlns="http://www.loc.gov/standards/alto/ns-v3#"><Layout>'
            f'<Page WIDTH="200" HEIGHT="300"><PrintSpace><TextBlock HPOS="5" VPOS="5" WIDTH="150" HEIGHT="200">'
            f'{tl}</TextBlock></PrintSpace></Page></Layout></alto>').encode()


class _Web:
    """The library, as a dict of URL → bytes, with a request log."""

    def __init__(self, pages):
        self.oai_pages = pages
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        if "/oai?" in url:
            return self.oai_pages[1] if "resumptionToken" in url else self.oai_pages[0]
        if "/fulltext/" in url:
            return _alto([] if url.endswith("102") else ["Hochwohlgeborner Herr", "Ihr Schreiben"])
        return b"\xff\xd8 image " + url.encode()


@pytest.fixture
def web():
    first = _oai_page([
        _record(1, "K 2911,47", "Brief von Joseph von Laßberg an Wilhelm Wackernagel", 2),
        _record(9, "K 2911,47", "Brief von Joseph von Laßberg an Wilhelm Wackernagel", 0, with_files=False),
        _record(5, "K 2911,48", "Brief von Wilhelm Wackernagel an Joseph von Laßberg", 1),
    ], token="t1")
    second = _oai_page([
        _record(10, "K 2914,3", "Fürstenbergischer Landkalender 1805", 2),
        _record(11, "K 2912,1", "Abschrift des Nibelungenliedes", 1),
        _record(12, "K 2912,2", "x", 0, deleted=True),
    ])
    return _Web([first, second])


def _sizes(_data):
    return (400, 600)


# ── rules and sources ────────────────────────────────────────────────────────

def test_the_hand_comes_from_the_catalogue_title():
    h = oai_gt.lassberg_hand
    assert h("Brief von Joseph von Laßberg an Sulpiz Boisserée", "K 2911,8") == "lassberg"
    assert h("Brief von Wilhelm Wackernagel an Joseph von Laßberg", "K 2911,99") == "korrespondenten"
    assert h("Korrespondenz zwischen Laßberg und Uhland", "K 2911,1") == "unbestimmt"
    assert h("Fürstenbergischer Landkalender 1805", "K 2914,3") == "drucke"
    assert h("Abschrift des Nibelungenliedes", "K 2912,1") == "manuskripte"


def test_a_source_is_a_name_in_code_never_a_url():
    with pytest.raises(oai_gt.OaiGtError) as err:
        oai_gt.source("https://evil.example/oai")
    assert "blb-lassberg" in str(err.value) and "change to oai_gt.SOURCES" in str(err.value)


# ── inventory ────────────────────────────────────────────────────────────────

def test_the_inventory_follows_the_token_and_prefers_the_record_with_pages(tmp_path, web):
    out = oai_gt.inventory(SRC, tmp_path, get=web)
    assert out["oai_pages"] == 2
    assert out["objects"] == 4, "anchor without files and the deleted record are not objects"
    rows = json.loads((tmp_path / "_oai" / "blb-lassberg" / "inventory.json").read_text())
    k47 = next(r for r in rows if r["shelfmark"] == "K 2911,47")
    assert k47["vl_id"] == "1" and k47["n_pages"] == 2
    assert (tmp_path / "_oai" / "blb-lassberg" / "inventory.tsv").is_file()
    n = len(web.calls)
    oai_gt.inventory(SRC, tmp_path, get=web)
    assert len(web.calls) == n, "the second inventory reads the cache"


# ── build ────────────────────────────────────────────────────────────────────

def test_build_writes_image_and_page_xml_and_leaves_textless_pages_out(tmp_path, web):
    oai_gt.inventory(SRC, tmp_path, get=web)
    dry = oai_gt.build(SRC, tmp_path, dry_run=True, get=web, size_of=_sizes)
    assert dry["pages"] == 6 and not (tmp_path / "lassberg-blb-gt").exists()
    out = oai_gt.build(SRC, tmp_path, dry_run=False, get=web, size_of=_sizes)
    tree = tmp_path / "lassberg-blb-gt"
    assert out["counts"] == {"written": 5, "no-text": 1}
    assert (tree / "lassberg" / "K2911-47_0001.jpg").is_file()
    xml = (tree / "lassberg" / "page" / "K2911-47_0001.xml").read_text(encoding="utf-8")
    assert "GROUND TRUTH, converted from Badische Landesbibliothek" in xml
    assert "Hochwohlgeborner Herr" in xml and 'imageWidth="400"' in xml
    assert not (tree / "drucke" / "K2914-3_0002.jpg").exists(), "the ALTO without a line"
    m = hf.read_manifest(tree)
    assert m["kind"] == "ground-truth" and m["mode"] == "raw_xml"
    assert hf.inspect_upload(tree, "dh-unibe/x").projects["lassberg"] == 2


def test_build_needs_the_inventory_first(tmp_path):
    with pytest.raises(oai_gt.OaiGtError) as err:
        oai_gt.build(SRC, tmp_path, dry_run=True)
    assert "inventory" in str(err.value)


def test_a_file_on_another_host_is_not_fetched(tmp_path):
    obj = {"shelfmark": "K 1,1", "project": "lassberg", "title": "t", "date": "", "vl_id": "1",
           "image_group": "MAX"}
    pg = {"n": 1, "order": "1", "image": "https://elsewhere.example/a.jpg",
          "alto": f"{BASE}/download/fulltext/alto3/1"}
    calls = []
    r = oai_gt.build_page(SRC, obj, pg, tmp_path, held_out=set(), get=calls.append, size_of=_sizes)
    assert r["status"] == "failed" and "refused" in r["error"] and calls == []


def test_a_rebuild_after_the_split_keeps_held_out_pages_held_out(tmp_path, web):
    oai_gt.inventory(SRC, tmp_path, get=web)
    oai_gt.build(SRC, tmp_path, dry_run=False, get=web, size_of=_sizes)
    tree = tmp_path / "lassberg-blb-gt"
    ts.split(tree, force_eval=["K2911-47"], dry_run=False)
    hf.record_upload(tree, "dh-unibe/image-text_lassberg-blb-gt", private=True)
    # one page of the held-out object is lost, and the build runs again
    (tree / "lassberg_eval" / "K2911-47_0002.jpg").unlink()
    (tree / "lassberg_eval" / "page" / "K2911-47_0002.xml").unlink()
    out = oai_gt.build(SRC, tmp_path, dry_run=False, get=web, size_of=_sizes)
    assert out["counts"]["written"] == 1
    assert (tree / "lassberg_eval" / "K2911-47_0002.jpg").is_file(), "back on the eval side"
    assert not any((tree / "lassberg").glob("K2911-47_*")), "never in training"
    m = hf.read_manifest(tree)
    assert "split" in m and m["uploads"], "the manifest is merged, not replaced"
    assert m["changed_after_upload"] and out["changed_after_upload"]


# ── the job ──────────────────────────────────────────────────────────────────

def test_the_job_argv_takes_names_and_caps_the_workers(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", tmp_path)
    argv = jobs.oai_gt_import_argv("blb-lassberg", "build")
    assert argv[3:] == ["oai-gt-import", "--source", "blb-lassberg", "--step", "build",
                        "--workers", "4", "--dry-run"]
    assert "--root" not in argv, "the root is the server's, not the caller's"
    assert "--dry-run" not in jobs.oai_gt_import_argv("blb-lassberg", "build", dry_run=False)
    assert "--dry-run" not in jobs.oai_gt_import_argv("blb-lassberg", "inventory")
    for bad in (dict(source="https://x.example/oai", step="build"),
                dict(source="blb-lassberg", step="download"),
                dict(source="blb-lassberg", step="build", workers=64),
                dict(source="blb-lassberg", step="build", limit=0)):
        with pytest.raises(jobs.JobError):
            jobs.oai_gt_import_argv(bad.pop("source"), bad.pop("step"), **bad)
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", None)
    with pytest.raises(jobs.JobError):
        jobs.oai_gt_import_argv("blb-lassberg", "inventory")


def test_the_job_names_the_next_steps(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", tmp_path)
    monkeypatch.setattr(jobs, "start_and_peek", lambda kind, argv, **kw: {"done": False, "job_id": "j"})
    out = jobs.oai_gt_import_job("blb-lassberg", "build", dry_run=False)
    assert out["tree"] == "datasets:lassberg-blb-gt" and "split_tree" in out["next"]
