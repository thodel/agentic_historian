"""S2 item 2 (#567): the writable paths have to be derived, not guessed.

`hardening.conf` sets `ProtectSystem=strict`, so everything not listed in
`ReadWritePaths=` is read-only — and a missing path does not degrade, it fails
the first write. The drop-in shipped a `/srv` example as a CHANGEME. On tei the
checkout is under `/home/dh`, so installing it unedited makes `data/`
unwritable; a placeholder that must be corrected before it is safe is a step
nobody takes, and that is why the item sat unimplemented while the repo counted
it done.

`deploy/systemd/writable_paths.py` reads the paths out of the live config
instead. These tests pin the two properties that make it safe to paste into a
unit file:

* it must not **miss** a directory the bot writes (then writes fail), and
* it must not **invent** one (then the sandbox is wider than it looks) — above
  all `sys.prefix` when that is the system Python, where the emitted line would
  be `ReadWritePaths=/usr`: worse than installing no hardening at all.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy/systemd/writable_paths.py"
HARDENING = REPO / "deploy/systemd/agentic-historian.service.d/hardening.conf"


def _load():
    spec = importlib.util.spec_from_file_location("writable_paths", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wp = _load()


def _cfg(tmp_path, **over):
    base = tmp_path / "checkout" / "agentic_historian"
    values = dict(
        BASE_DIR=base,
        DATA_DIR=base / "data",
        KH_DIR=base / "knowledge_hub" / "data",
        HOT_FOLDER=base / "data" / "hot_folder",
        PROCESSED_FOLDER=base / "data" / "hot_folder" / "processed",
        NEXTCLOUD_STAGING_DIR=base / "data" / "nextcloud",
        GT_ROOT=base / "data" / "gt",
        VLM_TEST_ROOT=base / "data" / "vlm_test",
        ATR_PAGE_CACHE=None,
        ATR_PAGE_CACHE_ARCHIVE=None,
    )
    values.update(over)
    return SimpleNamespace(**values)


# ── it must not miss a directory ─────────────────────────────────────────────

def test_the_data_dir_is_listed(tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg.DATA_DIR in wp.data_paths(cfg)


def test_the_knowledge_hub_dir_is_listed_even_though_it_is_not_under_data(tmp_path):
    """hub.json lives under knowledge_hub/data, NOT under data/. A unit that
    lists only DATA_DIR leaves every knowledge-hub write failing — and after
    #393/#643 those writes go through a lock file in the same directory, so the
    failure would be a refused lock rather than an obvious missing file."""
    cfg = _cfg(tmp_path)
    paths = wp.data_paths(cfg)
    assert cfg.KH_DIR in paths
    assert not any(wp._is_within(cfg.KH_DIR, p) for p in paths if p != cfg.KH_DIR), (
        "KH_DIR must be listed in its own right, not assumed to be under DATA_DIR")


def test_an_archive_outside_the_checkout_is_listed(tmp_path):
    """ATR_PAGE_CACHE_ARCHIVE is settable to anywhere — and it is set on tei."""
    archive = tmp_path / "elsewhere" / "page-archive"
    cfg = _cfg(tmp_path, ATR_PAGE_CACHE_ARCHIVE=str(archive))
    assert archive in wp.data_paths(cfg)


def test_a_relocated_gt_root_is_listed(tmp_path):
    outside = tmp_path / "gt-elsewhere"
    cfg = _cfg(tmp_path, GT_ROOT=outside)
    assert outside in wp.data_paths(cfg)


def test_a_path_that_is_unset_is_simply_absent(tmp_path):
    cfg = _cfg(tmp_path, ATR_PAGE_CACHE=None, ATR_PAGE_CACHE_ARCHIVE=None)
    rendered = wp.render(wp.data_paths(cfg))
    assert "None" not in rendered, "an unset path must not reach the unit file"


# ── it must not invent one ───────────────────────────────────────────────────

def test_the_system_prefix_is_never_emitted(monkeypatch, tmp_path):
    """The one that would do real damage.

    Run with the system interpreter, ``sys.prefix`` is ``/usr``. Emitting
    ``ReadWritePaths=/usr`` hands the process write access to the whole system —
    the exact opposite of what the file is for, and it would look like
    hardening while removing it.
    """
    monkeypatch.setattr(wp.sys, "prefix", "/usr", raising=False)
    monkeypatch.setattr(wp.sys, "base_prefix", "/usr", raising=False)
    assert not wp.in_virtualenv(), "precondition: not a venv"

    paths = wp.update_paths(_cfg(tmp_path))
    rendered = wp.render(paths)
    assert "/usr" not in rendered.split("ReadWritePaths=")[-1] or Path("/usr") not in paths
    assert Path("/usr") not in paths, "the system prefix reached the unit file"


def test_a_virtualenv_outside_the_checkout_is_emitted(monkeypatch, tmp_path):
    """The counter-test: leaving it out breaks `pip install` during /update."""
    venv = tmp_path / "venvs" / "bot"
    monkeypatch.setattr(wp.sys, "prefix", str(venv), raising=False)
    monkeypatch.setattr(wp.sys, "base_prefix", "/usr", raising=False)
    assert wp.in_virtualenv(), "precondition: a venv"

    assert venv in wp.update_paths(_cfg(tmp_path))


def test_a_virtualenv_inside_the_checkout_needs_no_line_of_its_own(
        monkeypatch, tmp_path):
    """Which is the case on tei (agentic_historian/.venv). The checkout entry
    already covers it, and a redundant line is noise in a file whose whole job
    is to be read carefully."""
    cfg = _cfg(tmp_path)
    venv = Path(cfg.BASE_DIR) / ".venv"
    monkeypatch.setattr(wp.sys, "prefix", str(venv), raising=False)
    monkeypatch.setattr(wp.sys, "base_prefix", "/usr", raising=False)

    paths = wp.update_paths(cfg)
    assert venv not in paths
    assert Path(cfg.BASE_DIR).parent in paths
    assert any(wp._is_within(venv, p) for p in paths), (
        "the venv has to be covered by something, just not by its own line")


def test_the_checkout_is_only_in_the_update_tier(tmp_path):
    """Normal operation must not make the code directory writable."""
    cfg = _cfg(tmp_path)
    checkout = Path(cfg.BASE_DIR).parent
    assert checkout not in wp.data_paths(cfg)
    assert checkout in wp.update_paths(cfg)


# ── the rendering ────────────────────────────────────────────────────────────

def test_a_child_next_to_its_parent_is_dropped(tmp_path):
    """ReadWritePaths covers a directory and everything under it, so listing
    both is noise — and a security unit nobody re-reads is the point of
    failure."""
    cfg = _cfg(tmp_path)
    paths = wp.data_paths(cfg)
    assert cfg.HOT_FOLDER not in paths, "hot folder is inside DATA_DIR"
    assert cfg.GT_ROOT not in paths, "gt root is inside DATA_DIR"
    assert cfg.DATA_DIR in paths


def test_every_line_is_a_readwritepaths_assignment(tmp_path):
    for line in wp.render(wp.data_paths(_cfg(tmp_path))).splitlines():
        assert line.startswith("ReadWritePaths=/"), f"not an absolute setting: {line!r}"


def test_render_of_nothing_is_empty_not_a_dangling_assignment():
    assert wp.render([]) == "", (
        "an empty ReadWritePaths= would CLEAR the list, not leave it unset")


# ── the drop-in itself ───────────────────────────────────────────────────────

def _active_lines() -> list[str]:
    """The settings systemd actually reads — comments are prose, not config.

    The distinction matters here: the file *explains* the old `/srv` placeholder
    in a comment, and a test that greps the whole file cannot tell that from
    shipping it.
    """
    stripped = (line.strip()
                for line in HARDENING.read_text(encoding="utf-8").splitlines())
    return [line for line in stripped
            if line and not line.startswith("#") and not line.startswith("[")]


def test_the_dropin_no_longer_ships_the_srv_placeholder():
    assert not any(line.startswith("ReadWritePaths=/srv/")
                   for line in _active_lines()), (
        "the CHANGEME example is active again — installed unedited it makes "
        "data/ read-only on a /home checkout")


def test_the_dropin_lists_both_data_directories():
    joined = "\n".join(line for line in _active_lines()
                       if line.startswith("ReadWritePaths="))
    assert "/agentic_historian/data" in joined
    assert "/knowledge_hub/data" in joined, (
        "hub.json's directory is missing — knowledge-hub writes would fail")


def test_protect_home_stays_commented():
    """On a /home checkout ProtectHome=read-only makes the data dirs unwritable
    whatever ReadWritePaths says."""
    for line in HARDENING.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("ProtectHome="):
            pytest.fail(f"ProtectHome is active: {stripped!r}")


def test_protect_system_strict_is_what_makes_the_list_load_bearing():
    """Checked against the ACTIVE settings, not the file.

    `ProtectSystem=strict` appears three times here — twice explaining itself in
    comments and once as the setting. Grepping the whole file therefore survives
    weakening the real line to `full`, which is what a mutation proved: the
    assertion read as a check and was prose-matching. `full` leaves /home
    writable, and then the ReadWritePaths list below means nothing.
    """
    settings = dict(line.split("=", 1) for line in _active_lines() if "=" in line)
    assert settings.get("ProtectSystem") == "strict", (
        f"ProtectSystem is {settings.get('ProtectSystem')!r}; with anything but "
        f"'strict' the ReadWritePaths list is not load-bearing")


def test_the_dropin_points_at_the_deriving_script():
    """A reader who changes a *_DIR setting has to be told where the list comes
    from, or the file goes stale the way the /srv example did."""
    assert "writable_paths.py" in HARDENING.read_text(encoding="utf-8")
