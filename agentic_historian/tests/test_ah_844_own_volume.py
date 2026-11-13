"""#844 counter-test: one of our own volumes into the e-codices bench.

Offline: Transkribus is replaced by page objects and byte fetchers. Pinned:
the reference refuses what the bench would misread, pages are spread over the
written ones and scaled to the bench's resolution, a page on disk is not
fetched again, and the result is a volume ``ecodices_bench`` runs and scores.
"""

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from eval import ecodices_bench as eb  # noqa: E402
from eval import own_volume as ov  # noqa: E402

Image = pytest.importorskip("PIL.Image")


def _jpeg(w=3000, h=4000) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (230, 220, 200)).save(out, "JPEG")
    return out.getvalue()


def test_the_reference_takes_what_the_historian_says_and_refuses_the_rest():
    assert ov.reference(1528, material="Papier", script="Kanzleikursive", hands=1) == {
        "date_from": 1528, "date_to": 1528, "material": "papier",
        "script": "Kanzleikursive", "hands": 1}
    for bad in (dict(date_from=15), dict(date_from=1528, date_to=1500),
                dict(date_from=1528, material="Leder"), dict(date_from=1528, hands=0)):
        with pytest.raises(ValueError):
            ov.reference(**bad)


def test_pages_are_spread_over_the_written_ones():
    pages = [SimpleNamespace(page_nr=i, nr_lines=(0 if i in (1, 2, 40) else 20), nr_words=0)
             for i in range(1, 41)]
    w = ov.written(pages)
    assert [p.page_nr for p in w][:2] == [3, 4] and w[-1].page_nr == 39
    chosen = ov.spread(w, 5)
    assert chosen[0].page_nr == 3 and chosen[-1].page_nr == 39 and len(chosen) == 5
    blank = [SimpleNamespace(page_nr=i, nr_lines=0, nr_words=0) for i in range(3)]
    assert ov.written(blank) == blank, "nothing transcribed yet: every page"


def test_the_volume_is_written_once_and_read_by_the_bench(tmp_path):
    calls = []

    def fetch(i):
        def go():
            calls.append(i)
            return _jpeg()
        return go

    ref = ov.reference(1528, material="papier", script="Kanzleikursive", hands=1)
    images = [(f"p{i:04d}", fetch(i)) for i in (3, 20, 39)]
    vdir = ov.add(tmp_path, "a-v-1443", ref, images=images, source={"transkribus": {"doc": 1}})
    assert sorted(p.name for p in (vdir / "pages").iterdir()) == ["p0003.jpg", "p0020.jpg", "p0039.jpg"]
    with Image.open(vdir / "pages" / "p0003.jpg") as im:
        assert max(im.size) == ov.MAX_SIDE, "scaled like the bench's e-codices pages"
    ov.add(tmp_path, "a-v-1443", ref, images=images, source={})
    assert calls == [3, 20, 39], "a page on disk is not fetched again"

    got = eb.reference_of(vdir)
    assert (got.date_from, got.date_to, got.hands) == (1528, 1528, 1)
    assert eb.volumes(tmp_path) == [vdir]
    assert [p.name for p in eb.local_pages(vdir)] == ["p0003.jpg", "p0020.jpg", "p0039.jpg"]


def test_an_e_codices_volume_is_not_overwritten(tmp_path):
    (tmp_path / "saa-0428").mkdir()
    (tmp_path / "saa-0428" / "tei.xml").write_text("<TEI/>")
    with pytest.raises(ValueError, match="e-codices"):
        ov.add(tmp_path, "saa-0428", ov.reference(1330), images=[], source={})


def test_a_folder_of_images_is_a_source_too(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(10):
        (src / f"img{i:02d}.jpg").write_bytes(_jpeg(800, 1000))
    (src / "notes.txt").write_text("x")
    images, source = ov._dir_images(src, 3)
    assert [n for n, _ in images] == ["img00", "img04", "img09"]
    assert source["pages"] == ["img00.jpg", "img04.jpg", "img09.jpg"]
    assert ov.main([str(tmp_path / "bench"), "rr-1857", "--from-dir", str(src), "--pages", "3",
                    "--date-from", "1857", "--material", "papier"]) == 0
    assert json.loads((tmp_path / "bench" / "rr-1857" / "reference.json").read_text())["date_to"] == 1857
