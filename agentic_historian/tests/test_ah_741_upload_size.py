"""#741 — what an upload becomes: the images as PNG, measured before they move.

26.10.: 7.8 GB of the BLB's JPEGs became 31.8 GB on the hub, one shard per
project, the largest 12.2 GB — because `pagexml-hf` re-encodes every page as
PNG. The dry run said none of it, and the space check sized the shards at
1.1 x the tree. Now a sample is encoded the same way, the estimate is printed,
the shards are checked with it, the staging peak is measured during the run,
and the shard sizes are read back from the hub.
"""

import io
import json
import sys
import time
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import hf_export as hf                     # noqa: E402

pytest.importorskip("PIL")


def _pil():
    """The real Pillow, resolved at run time. ``test_ah_108`` installs a
    ``MagicMock`` PIL into ``sys.modules`` while pytest collects; a module-level
    import would bind to it and write images that are not images (see
    ``test_ah_235_tocr_segmentation._make_temp_image``)."""
    for name in [n for n in list(sys.modules) if n == "PIL" or n.startswith("PIL.")]:
        if type(sys.modules[name]).__module__.startswith("unittest.mock"):
            del sys.modules[name]
    from PIL import Image
    return Image


def _jpeg(path: Path, seed: int, size=(240, 320)) -> None:
    import random
    Image = _pil()
    rnd = random.Random(seed)
    im = Image.new("RGB", size)
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                for _ in range(size[0] * size[1])])
    im.save(path, format="JPEG", quality=60)


def _tree(root: Path, pages: dict[str, int]) -> Path:
    tree = root / "t"
    n = 0
    for project, count in pages.items():
        (tree / project / "page").mkdir(parents=True)
        for i in range(1, count + 1):
            n += 1
            _jpeg(tree / project / f"K1-{n}_{i:04d}.jpg", n)
            (tree / project / "page" / f"K1-{n}_{i:04d}.xml").write_text("<PcGts/>")
    hf.write_manifest(tree, mode="raw_xml", kind="ground-truth")
    return tree


def test_the_factor_is_what_pagexml_hf_makes_of_the_images(tmp_path):
    tree = _tree(tmp_path, {"a": 3})
    images = sorted((tree / "a").glob("*.jpg"))
    factor, n = hf.png_factor(images)
    Image = _pil()
    expected = []
    for p in images:
        buf = io.BytesIO()
        Image.open(p).convert("RGB").save(buf, format="PNG")
        expected.append((len(buf.getvalue()), p.stat().st_size))
    assert n == 3
    assert factor == pytest.approx(sum(a for a, _ in expected) / sum(b for _, b in expected))
    assert factor > 1, "a JPEG re-encoded as PNG grows"


def test_the_sample_is_spread_and_bounded(tmp_path):
    tree = _tree(tmp_path, {"a": 30})
    _, n = hf.png_factor(sorted((tree / "a").glob("*.jpg")), sample=5)
    assert n == 5


def test_nothing_measurable_is_none_not_a_guess(tmp_path):
    bad = tmp_path / "x.jpg"
    bad.write_bytes(b"\xff\xd8 not an image")
    assert hf.png_factor([bad]) == (None, 0)
    assert hf.png_factor([]) == (None, 0)


def test_the_plan_estimates_the_hub_and_the_largest_shard(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", None)
    monkeypatch.setattr(config, "ATR_HF_SCRATCH", None)
    tree = _tree(tmp_path, {"big": 4, "small": 1})
    monkeypatch.setattr(hf, "png_factor", lambda images, sample=12: (4.0, 5))
    plan = hf.inspect_upload(tree, "dh-unibe/x")
    big = sum(f.stat().st_size for f in (tree / "big").glob("*.jpg"))
    assert plan.png_factor == 4.0 and plan.largest_shard == ("big", big * 4)
    assert plan.hub_estimate > 4 * big
    text = hf.format_upload(plan)
    assert "hub size  : ≈" in text and "×4.0 over 5 sampled" in text and "largest shard big" in text


def test_the_shards_are_checked_against_the_png_estimate_not_the_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", tmp_path)
    monkeypatch.setattr(config, "ATR_HF_SCRATCH", None)
    monkeypatch.setattr(config, "ATR_HF_ARROW_CACHE", tmp_path / "local")
    tree = _tree(tmp_path, {"a": 2})
    size = hf.tree_bytes(tree)
    monkeypatch.setattr(hf, "png_factor", lambda images, sample=12: (4.0, 2))
    # room for 1.1 x the tree on the mount — the old check passed, the new one must not
    room = {"local": 10**12, "_hf_scratch": int(size * 1.5)}
    monkeypatch.setattr(hf, "free_bytes", lambda p: next(v for k, v in room.items() if k in str(p)))
    plan = hf.inspect_upload(tree, "dh-unibe/x")
    assert any("parquet shards" in p and "No space left" in p for p in plan.problems)


def test_the_staging_peak_is_the_largest_size_seen(tmp_path):
    d = tmp_path / "stage"
    d.mkdir()
    with hf.StagingPeak([d], every_s=0.05) as peak:
        (d / "a").write_bytes(b"x" * 5000)
        time.sleep(0.2)
        (d / "a").unlink()
        time.sleep(0.2)
    assert peak.peak[str(d)] >= 5000


def test_the_upload_record_keeps_the_numbers(tmp_path):
    tree = _tree(tmp_path, {"a": 1})
    hf.record_upload(tree, "dh-unibe/x", private=True, tree_bytes=10, png_factor=4.1,
                     hub_estimate=41, staging_peak={"/s": 50}, ignored=None)
    up = hf.read_manifest(tree)["uploads"][-1]
    assert up["png_factor"] == 4.1 and up["staging_peak"] == {"/s": 50} and "ignored" not in up


def test_the_hub_shards_are_read_from_the_tree_api(monkeypatch):
    class _R:
        status_code = 200

        def json(self):
            return [
                {"type": "directory", "path": "data/train/manuskripte"},
                {"type": "file", "path": "data/train/manuskripte/x-0000.parquet",
                 "size": 1, "lfs": {"size": 12151833970}},
                {"type": "file", "path": "data/train/lassberg/x-0000.parquet",
                 "size": 1, "lfs": {"size": 2010220811}},
                {"type": "file", "path": "README.md", "size": 9}]

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    shards = hf.hub_shards("dh-unibe/x", token="t")
    assert shards == {"manuskripte": 12151833970, "lassberg": 2010220811}
    assert "largest manuskripte 12.15 GB" in hf.format_hub_shards(shards)


def test_the_cli_meters_the_staging_and_prints_the_shards():
    src = (PKG / "__main__.py").read_text(encoding="utf-8")
    body = src[src.index("def upload_hf(args"):src.index("def split_tree(args")]
    assert "hf.StagingPeak(work)" in body and "staging_peak=peak.peak" in body
    assert "hf.format_hub_shards(hf.hub_shards(plan.repo_id))" in body
