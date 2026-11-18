"""#481 — material the project may hold but must not read or pass on.

``PA 82a G 1`` came from the Staatsarchiv Basel-Stadt as a reproduction for
private use only (order letter 25.03.2025). Since #476 the batch runner reads
PDFs, so the next run over the Basel folder would have transcribed it. Pinned
here: the register matches the shelfmark in every spelling the project uses and
nothing near it; the batch listing, the publisher, the Transkribus upload and the
HF export each leave it out and say why.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import restricted   # noqa: E402

PDF = "Basel/lassberg-letter-1173/PA 82a G 1_003.pdf"


@pytest.fixture(autouse=True)
def _no_local_register(monkeypatch):
    monkeypatch.delenv("AH_RESTRICTED_FILE", raising=False)
    restricted.reload()
    yield
    restricted.reload()


# ── the register ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    PDF,
    "/mnt/gwdg/Digitalisate/Basel/lassberg-letter-1173/PA 82a G 1_003.pdf",
    "Basel__lassberg-letter-1173__PA 82a G 1_003__p0001",      # a PDF page's key
    "PA_82a_G_1_003.jpg",
    "Basel/lassberg-letter-1173/anything-else.jpg",            # the folder itself
    "Basel__lassberg-letter-1173__page_0001",                   # its page key, no shelfmark
    "basel/LASSBERG-LETTER-1173/x.tif",                         # case
])
def test_the_shelfmark_and_its_folder_are_restricted_in_every_spelling(path):
    entry = restricted.restriction(path)
    assert entry is not None
    assert "private use only" in entry.reason and "25.03.2025" in entry.source


@pytest.mark.parametrize("path", [
    "Basel/lassberg-letter-1743/PA 82a B 9_Seite_030.jpg",     # the publishable Basel delivery
    "Basel/PA 82a G 10.pdf", "Basel/PA 82a G 12_001.jpg",       # the digit guard
    "Aarau/lassberg-letter-1173/page-001.jpg",                  # another holding's 1173
    "Basel/lassberg-letter-11730/x.jpg",
])
def test_nothing_near_it_is_restricted(path):
    assert restricted.restriction(path) is None


def test_a_host_local_file_adds_entries_and_a_broken_one_fails_loudly(tmp_path, monkeypatch):
    f = tmp_path / "restricted.txt"
    f.write_text("# a comment line\nZürich/.*Ms_Z_II  # embargo until 2027\n\n", encoding="utf-8")
    monkeypatch.setenv("AH_RESTRICTED_FILE", str(f))
    restricted.reload()
    entry = restricted.restriction("Zürich/bd1/Ms_Z_II_004.jpg")
    assert entry is not None and entry.reason == "embargo until 2027" and entry.source.endswith(":2")
    assert restricted.restriction(PDF) is not None              # the register still applies
    f.write_text("([unclosed\n", encoding="utf-8")
    restricted.reload()
    with pytest.raises(ValueError, match=r"restricted\.txt:1: not a regular expression"):
        restricted.restriction("x")


# ── the four ways in and out ─────────────────────────────────────────────────

def test_the_batch_listing_never_opens_it_and_says_why():
    import atr_batch
    opened = []

    def count(path):
        opened.append(str(path))
        return 3
    listed = atr_batch.pages_from_paths(
        [PDF, "Basel/PA 82a B 9.pdf", "Basel/lassberg-letter-1743/p1.jpg"], root="/share",
        count_pages=count)
    keys = [p.key for p in listed]
    assert not any("82a G 1" in k for k in keys)
    assert any("82a B 9" in k for k in keys) and any("p1" in k for k in keys)
    assert not any("82a G 1" in o for o in opened)              # not even counted
    skipped = {s.rel: s.reason for s in listed.skipped}
    assert skipped[PDF].startswith("restricted: private use only")


def test_the_publisher_withholds_it_from_any_run_directory(tmp_path, monkeypatch):
    import config
    from utils import publish_github as pg
    monkeypatch.setattr(config, "GITHUB_TOKEN", "x")
    run = tmp_path / "run"
    (run / "qwen").mkdir(parents=True)
    (run / "qwen" / "Basel__lassberg-letter-1173__PA 82a G 1_003__p0001.txt").write_text("Lesung", encoding="utf-8")
    (run / "qwen" / "Basel__lassberg-letter-1743__PA 82a B 9_Seite_030.txt").write_text("Lesung", encoding="utf-8")
    files = pg.collect_tree(run, "data/textrecognition")
    assert list(files) == ["data/textrecognition/qwen/Basel__lassberg-letter-1743__PA 82a B 9_Seite_030.txt"]


def test_the_transkribus_upload_leaves_it_out(tmp_path):
    import transkribus
    for rel in ("Basel/lassberg-letter-1173/p1.jpg", "Basel/lassberg-letter-1743/p1.jpg"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xd8")
    letters = transkribus.letters_from_cache(tmp_path)
    assert [letter.title for letter in letters] == ["Basel__lassberg-letter-1743"]
    un = transkribus.Unassigned(restricted=2)
    assert "restricted (restricted.py, #481): 2 page(s)" in transkribus.format_unassigned(un)


def test_the_hf_export_rejects_it_with_the_reason():
    import hf_export
    item = SimpleNamespace(located="Basel__lassberg-letter-1173__PA 82a G 1_003__p0001",
                           gt=SimpleNamespace(source=Path("page-1.xml")))
    plan = hf_export.plan([item], {})
    assert plan.entries == []
    assert plan.rejected[0].reason.startswith("restricted: private use only")
