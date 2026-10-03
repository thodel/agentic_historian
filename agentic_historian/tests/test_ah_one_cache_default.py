"""One answer to "where is the page cache" (`config.page_cache_dir()`).

Three rounds went into having two. `ATR_PAGE_CACHE` is unset on tei, and each
entry point filled the gap differently:

* `mcp_atr.jobs.cache_dir_for` fell back to `DATA_DIR/page_cache`, so a `dav:`
  run started over MCP worked.
* `atr-batch` on the CLI had no fallback and refused the same run with
  "dav: sources need --cache-dir — it is where the pages land".
* `export-hf` had no fallback either, so the runbook said `--source
  "$ATR_PAGE_CACHE"` — and that shell variable was set but **not exported**,
  naming a different directory that happened to hold images. 357 "no image"
  lines, and two wrong diagnoses (a PDF renderer, a cache eviction) before the
  right one.

A cache nobody configured is still a cache. What must not differ is *which*
directory each entry point thinks it is, so they now read one value and these
tests hold them to it.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402


def test_the_default_is_the_configured_cache_when_there_is_one(monkeypatch):
    import importlib

    monkeypatch.setenv("ATR_PAGE_CACHE", "/somewhere/else")
    fresh = importlib.reload(config)
    try:
        assert fresh.page_cache_dir() == Path("/somewhere/else")
    finally:
        monkeypatch.delenv("ATR_PAGE_CACHE", raising=False)
        importlib.reload(config)


def test_unconfigured_falls_back_under_the_data_dir():
    """tei's case. The corpus run's own argv named exactly this path."""
    if config.ATR_PAGE_CACHE:
        assert config.page_cache_dir() == config.ATR_PAGE_CACHE
    else:
        assert config.page_cache_dir() == config.DATA_DIR / "page_cache"


def test_every_entry_point_reads_the_same_value(monkeypatch, tmp_path):
    """The assertion the three rounds were about."""
    import hf_export as hf
    from mcp_atr import jobs

    monkeypatch.setattr(config, "ATR_PAGE_CACHE", None)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    assert hf.default_source() == tmp_path / "page_cache"
    assert Path(jobs.cache_dir_for("dav:digitalisate")) == tmp_path / "page_cache"


def test_a_mounted_source_gets_it_too(monkeypatch, tmp_path):
    from mcp_atr import jobs

    mount = tmp_path / "gwdg"
    (mount / "digitalisate").mkdir(parents=True)
    monkeypatch.setattr(config, "ATR_PAGE_CACHE", None)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "ATR_MOUNT_DIR", mount)

    assert Path(jobs.cache_dir_for(mount / "digitalisate")) == tmp_path / "page_cache"


def test_the_cli_offers_a_cache_dir_without_the_variable():
    """The failure as it happened: `--source dav:…` was refused with "dav:
    sources need --cache-dir" because that argument's default was None whenever
    ATR_PAGE_CACHE is unset — while the MCP path, which had a fallback, ran the
    same source fine."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ah_cli_cache", PKG / "__main__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    args = module.build_parser().parse_args(
        ["atr-batch", "--source", "dav:digitalisate", "--models", "m", "--run", "r"])

    assert args.cache_dir == str(config.page_cache_dir())
    assert args.cache_dir            # the check it used to trip is `not cache_dir`
