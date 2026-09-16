"""Working copies of archival scans, and the disk that ran out (2026-09-16).

The Lassberg digitisations are uncompressed TIFF: 2636 x 3212 x 3 bytes is 25 MB
a page, ~160 GB for the share, against the 92 GB tei has in total. The first full
pull filled the root filesystem at page 899, logged one error per remaining file
for fifteen minutes, and then died on a `mkdir` with a traceback.
"""

from __future__ import annotations

import errno
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config                                    # noqa: E402
from utils import images                         # noqa: E402
from utils import nextcloud                      # noqa: E402


@pytest.fixture(autouse=True)
def Image():
    """The real Pillow, whatever ran before this module.

    ``test_ah_108_hf_ocr_trocr_seq2seq`` puts a ``MagicMock`` PIL into
    ``sys.modules`` at import time so it can test the HF path without the
    library. It now cleans up after itself, but these tests exercise real image
    encoding and must not depend on another module's teardown having run — a
    mocked ``Image.save`` writes nothing and the failure surfaces three frames
    away, as a ``FileNotFoundError`` on the rename.
    """
    for name in [n for n in list(sys.modules) if n == "PIL" or n.startswith("PIL.")]:
        if type(sys.modules[name]).__module__.startswith("unittest.mock"):
            del sys.modules[name]
    from PIL import Image as real

    return real


def tiff(Image, path: Path, size=(400, 300)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path, format="TIFF", compression="none")
    return path


# ── which files are worth converting ─────────────────────────────────────────

@pytest.mark.parametrize("name", ["a.tif", "a.TIFF", "b.bmp", "c.png"])
def test_bulky_formats_are_converted(name):
    assert images.needs_conversion(Path(name))


@pytest.mark.parametrize("name", ["a.jpg", "a.JPEG"])
def test_a_jpeg_is_left_alone(name):
    """Re-encoding an already-compressed image loses quality for nothing."""
    assert not images.needs_conversion(Path(name))


def test_the_working_copy_keeps_the_name_and_changes_the_suffix(tmp_path):
    assert images.working_path(tmp_path / "x.tif").name == "x.jpg"


# ── the conversion itself ────────────────────────────────────────────────────

def test_conversion_keeps_full_resolution(tmp_path, Image):
    """Only the encoding changes. Downscaling here would decide once, and
    irreversibly, what every future model gets to see."""
    src = tiff(Image, tmp_path / "page.tif", size=(1200, 900))
    dest = images.convert_file(src)

    with Image.open(dest) as img:
        assert img.size == (1200, 900)


def test_conversion_is_much_smaller_and_drops_the_original(tmp_path, Image):
    src = tiff(Image, tmp_path / "page.tif", size=(1200, 900))
    before = src.stat().st_size
    dest = images.convert_file(src)

    assert dest.stat().st_size < before / 4
    assert not src.exists(), "the archive keeps the original; the mirror does not"


def test_a_failed_conversion_costs_nothing(tmp_path):
    """An unreadable scan must not take the corpus down, and must not lose the
    file it could not read."""
    src = tmp_path / "broken.tif"
    src.write_bytes(b"not an image")

    assert images.convert_file(src) is None
    assert src.exists()


def test_conversion_from_bytes_writes_a_jpeg(tmp_path, Image):
    buffer = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(buffer, format="TIFF")
    dest = images.convert_bytes(buffer.getvalue(), tmp_path / "out.jpg")

    with Image.open(dest) as img:
        assert img.format == "JPEG" and img.size == (100, 80)


def test_nothing_is_left_behind_half_written(tmp_path, Image):
    src = tiff(Image, tmp_path / "page.tif")
    images.convert_file(src)
    assert not list(tmp_path.glob("*.part"))


# ── the resume rule ──────────────────────────────────────────────────────────

def test_a_converted_page_counts_as_present(tmp_path, monkeypatch):
    """The one that matters for a 6000-page share: after conversion the mirror
    holds `x.jpg` and the share still offers `x.tif`. Checking only the original
    name would re-download the entire corpus on the next run."""
    local = tmp_path / "mirror"
    (local / "doc").mkdir(parents=True)
    (local / "doc" / "page.jpg").write_bytes(b"already converted")

    calls: list[str] = []

    class FakeClient:
        def download_file(self, remote, dest):        # noqa: ANN001, ANN202
            calls.append(remote)
            raise AssertionError("should not download a page already converted")

    monkeypatch.setattr(nextcloud, "_connect", lambda *a, **k: FakeClient())
    monkeypatch.setattr(nextcloud, "_walk",
                        lambda *a, **k: iter([("share/doc/page.tif", 26_000_000)]))

    out = nextcloud.pull_folder("share", local, share=object(), convert=True)

    assert calls == []
    assert out == [local / "doc" / "page.jpg"]


def test_without_conversion_the_original_name_still_rules(tmp_path, monkeypatch):
    local = tmp_path / "mirror"
    (local / "doc").mkdir(parents=True)
    (local / "doc" / "page.tif").write_bytes(b"x" * 11)

    monkeypatch.setattr(nextcloud, "_connect", lambda *a, **k: object())
    monkeypatch.setattr(nextcloud, "_walk",
                        lambda *a, **k: iter([("share/doc/page.tif", 11)]))

    out = nextcloud.pull_folder("share", local, share=object(), convert=False)
    assert out == [local / "doc" / "page.tif"]


# ── the full disk ────────────────────────────────────────────────────────────

def test_a_full_disk_stops_the_pull_at_the_first_failure(tmp_path, monkeypatch):
    """It logged one error per remaining file and spent fifteen minutes doing it.
    Every later file fails for the same reason the first one did."""
    attempts: list[str] = []

    class FullDisk:
        def download_file(self, remote, dest):        # noqa: ANN001, ANN202
            attempts.append(remote)
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(nextcloud, "_connect", lambda *a, **k: FullDisk())
    monkeypatch.setattr(nextcloud, "_walk", lambda *a, **k: iter(
        [(f"share/doc/p{i}.tif", 26_000_000) for i in range(50)]))

    with pytest.raises(nextcloud.NextcloudError, match="out of disk space"):
        nextcloud.pull_folder("share", tmp_path / "mirror", share=object())

    assert len(attempts) == 1, "one failure is enough to know the disk is full"


def test_the_disk_error_says_the_pull_resumes(tmp_path, monkeypatch):
    class FullDisk:
        def download_file(self, remote, dest):        # noqa: ANN001, ANN202
            raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(nextcloud, "_connect", lambda *a, **k: FullDisk())
    monkeypatch.setattr(nextcloud, "_walk",
                        lambda *a, **k: iter([("share/doc/p.tif", 26_000_000)]))

    with pytest.raises(nextcloud.NextcloudError) as err:
        nextcloud.pull_folder("share", tmp_path / "mirror", share=object())

    assert "resumes" in str(err.value), "the operator needs to know re-running is safe"


def test_an_ordinary_failure_still_only_costs_that_file(tmp_path, monkeypatch):
    """ENOSPC is the exception, not the new rule: one unreadable file must still
    leave the rest of the corpus alone."""
    seen: list[str] = []

    class Flaky:
        def download_file(self, remote, dest):        # noqa: ANN001, ANN202
            seen.append(remote)
            if remote.endswith("p0.tif"):
                raise OSError(errno.EIO, "I/O error")
            Path(dest).write_bytes(b"ok")

    monkeypatch.setattr(nextcloud, "_connect", lambda *a, **k: Flaky())
    monkeypatch.setattr(nextcloud, "_walk", lambda *a, **k: iter(
        [(f"share/doc/p{i}.tif", 2) for i in range(3)]))
    monkeypatch.setattr(images, "convert_file", lambda src, **k: None)

    out = nextcloud.pull_folder("share", tmp_path / "mirror", share=object(), convert=False)

    assert len(seen) == 3, "the other files were still attempted"
    assert len(out) == 2


def test_the_config_default_is_to_convert():
    assert config.NEXTCLOUD_CONVERT is True
