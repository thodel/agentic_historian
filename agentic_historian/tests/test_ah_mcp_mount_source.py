"""Driving a run over the mounted share through MCP.

The share no longer fits on tei, so the corpus lives on a read-only mount. Two
things in the MCP path assumed the mirror and would have refused or degraded
every run over it: the containment allowlist, which rejects any source outside
the mirror and the comparison root, and the argv builder, which had no way to
pass `--cache-dir` — so every page would have crossed the network once per
model instead of once, at 25 MB a page.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
from mcp_atr import jobs                   # noqa: E402


@pytest.fixture
def hosts(tmp_path, monkeypatch):
    """A mirror, a comparison root and a mount, all under tmp_path."""
    mirror = tmp_path / "nextcloud"
    runs = tmp_path / "vlm_test"
    mount = tmp_path / "mnt" / "gwdg"
    for d in (mirror / "digitalisate", runs, mount / "digitalisate"):
        d.mkdir(parents=True)
    monkeypatch.setattr(config, "NEXTCLOUD_STAGING_DIR", mirror)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", runs)
    monkeypatch.setattr(config, "ATR_MOUNT_DIR", mount)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "ATR_PAGE_CACHE", None)
    return {"mirror": mirror, "runs": runs, "mount": mount, "data": tmp_path / "data"}


# ── the mount is a corpus root ───────────────────────────────────────────────

def test_a_source_on_the_mount_is_accepted(hosts):
    resolved = jobs.resolve_source(str(hosts["mount"] / "digitalisate"))

    assert resolved == (hosts["mount"] / "digitalisate").resolve()


def test_the_mirror_and_the_comparison_root_still_work(hosts):
    assert jobs.resolve_source(str(hosts["mirror"] / "digitalisate"))
    assert jobs.resolve_source(str(hosts["runs"]))


def test_everything_else_is_still_refused(hosts, tmp_path):
    stray = tmp_path / "elsewhere"
    stray.mkdir()

    with pytest.raises(jobs.JobError, match="outside the corpus roots"):
        jobs.resolve_source(str(stray))


def test_climbing_out_of_the_mount_is_refused(hosts):
    """The check is on the RESOLVED path, so `..` and symlinks fail alike."""
    with pytest.raises(jobs.JobError, match="outside the corpus roots"):
        jobs.resolve_source(str(hosts["mount"] / "digitalisate" / ".." / ".." / ".."))


def test_a_symlink_out_of_the_mount_is_refused(hosts, tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    link = hosts["mount"] / "escape"
    link.symlink_to(secret)

    with pytest.raises(jobs.JobError, match="outside the corpus roots"):
        jobs.resolve_source(str(link))


def test_a_host_without_a_mount_keeps_the_old_roots(hosts, monkeypatch):
    monkeypatch.setattr(config, "ATR_MOUNT_DIR", None)

    with pytest.raises(jobs.JobError, match="outside the corpus roots"):
        jobs.resolve_source(str(hosts["mount"] / "digitalisate"))


# ── pages off the mount are cached ───────────────────────────────────────────

def test_a_source_on_the_mount_gets_a_cache(hosts):
    where = jobs.cache_dir_for((hosts["mount"] / "digitalisate").resolve())

    assert where == hosts["data"] / "page_cache"


def test_a_source_on_local_disk_gets_none(hosts):
    """It would only duplicate files that are already cheap to read."""
    assert jobs.cache_dir_for((hosts["mirror"] / "digitalisate").resolve()) is None


def test_a_configured_cache_wins_everywhere(hosts, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ATR_PAGE_CACHE", tmp_path / "chosen")

    assert jobs.cache_dir_for((hosts["mirror"] / "digitalisate").resolve()) \
        == tmp_path / "chosen"
    assert jobs.cache_dir_for((hosts["mount"] / "digitalisate").resolve()) \
        == tmp_path / "chosen"


def test_the_cache_reaches_the_command_line(hosts):
    argv = jobs.batch_argv(hosts["mount"] / "digitalisate", ["trocr-kurrent"], "r",
                           cache_dir=hosts["data"] / "page_cache")

    assert "--cache-dir" in argv
    assert argv[argv.index("--cache-dir") + 1] == str(hosts["data"] / "page_cache")


def test_no_cache_means_no_flag(hosts):
    argv = jobs.batch_argv(hosts["mirror"] / "digitalisate", ["trocr-kurrent"], "r")

    assert "--cache-dir" not in argv
