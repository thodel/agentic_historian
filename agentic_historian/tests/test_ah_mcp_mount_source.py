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


# ── dav: sources through MCP ─────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("dav:digitalisate", "dav:digitalisate"),
    ("dav:/digitalisate/", "dav:digitalisate"),
    ("dav:wlb stuttgart", "dav:wlb stuttgart"),   # this share has spaces in it
    ("dav:", "dav:"),
])
def test_a_dav_source_passes_through_as_a_share_folder(hosts, value, expected):
    """It names a folder in the share, not a path here — nothing to resolve."""
    assert jobs.resolve_source(value) == expected


@pytest.mark.parametrize("value", [
    "dav:../../etc",
    "dav:digitalisate/../..",
    "dav:a/../b",
])
def test_climbing_out_of_the_named_folder_is_refused(hosts, value):
    """`..` is not a containment problem here — it is a request to read a folder
    other than the one the caller named, which is the same thing a caller should
    not be able to do by writing it into an argument."""
    with pytest.raises(jobs.JobError, match="invalid share folder"):
        jobs.resolve_source(value)


def test_a_dav_source_always_gets_a_cache(hosts):
    """The runner refuses a dav: run without one, and it is right to: that cache
    is the only copy of a page that ever lands on this disk."""
    assert jobs.cache_dir_for("dav:digitalisate") == hosts["data"] / "page_cache"


def test_the_dav_source_reaches_the_command_line(hosts):
    argv = jobs.batch_argv("dav:digitalisate", ["qwen3.5-4b-german-xix-v2"], "r",
                           cache_dir=hosts["data"] / "page_cache")

    assert argv[argv.index("--source") + 1] == "dav:digitalisate"
    assert "--cache-dir" in argv
