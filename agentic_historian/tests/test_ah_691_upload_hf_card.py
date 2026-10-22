"""#691 — the dataset card goes to the hub from a session, with the hub's own
front matter spliced in first.

Twice the end of a dataset upload was a terminal command (`hf upload … README.md`)
and a splice done by eye: `pagexml-hf` writes a README whose YAML block carries
the measured sizes, the card in `docs/` carries the text, and the card on the hub
has to be both. This is that splice and that upload as a tool, token never in
argv, and `upload_hf` saying afterwards what the hub holds.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import hf_export as hf                     # noqa: E402
from mcp_atr import jobs                   # noqa: E402

HUB = """---
dataset_info:
  config_name: default
  splits:
  - name: train
    num_examples: 281
    num_bytes: 2198208407
  dataset_size: 2198208407
license: mit
---

# Dataset Card for image-text_lassberg-correspondence_xix_uncorrected-atr

This dataset was created using pagexml-hf converter from Transkribus PageXML data.
"""

CARD_PLACEHOLDER = """<!-- FRONTMATTER: pagexml-hf schreibt beim Upload selbst ein README.
     Dieser Block wird nach dem Upload wörtlich übernommen. -->

# Laßberg correspondence — uncorrected machine readings

**Nobody has read this text.**
"""

CARD_OLD_FM = """---
dataset_info:
  splits:
  - name: train
    num_examples: 99
---

# Laßberg correspondence — uncorrected machine readings

**Nobody has read this text.**
"""


# ── the splice ───────────────────────────────────────────────────────────────

def test_the_placeholder_is_replaced_by_the_hubs_block_verbatim():
    out = hf.splice_frontmatter(CARD_PLACEHOLDER, HUB)
    fm, body = hf.split_frontmatter(out)
    assert "num_examples: 281" in fm and "dataset_size: 2198208407" in fm
    assert body.startswith("# Laßberg correspondence")
    assert "FRONTMATTER" not in out
    assert "pagexml-hf converter" not in out, "the hub's body is not the card"


def test_an_old_front_matter_is_replaced_not_kept():
    out = hf.splice_frontmatter(CARD_OLD_FM, HUB)
    assert "num_examples: 99" not in out and "num_examples: 281" in out
    assert out.startswith("---\n") and out.count("---\n") == 2, "one block, two fences"


def test_a_card_without_either_keeps_its_whole_text_as_body():
    out = hf.splice_frontmatter("# Title\n\nbody\n", HUB)
    assert out.endswith("# Title\n\nbody\n")


def test_a_hub_readme_without_front_matter_is_refused():
    with pytest.raises(ValueError) as err:
        hf.splice_frontmatter(CARD_PLACEHOLDER, "# no yaml here\n")
    assert "upload the dataset first" in str(err.value)


def test_splicing_twice_is_the_same_card():
    once = hf.splice_frontmatter(CARD_PLACEHOLDER, HUB)
    assert hf.splice_frontmatter(once, HUB) == once


# ── a card is named ──────────────────────────────────────────────────────────

def test_a_card_is_named_under_docs_not_pathed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "hf-dataset-card-x.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "docs" / "other.md").write_text("# o\n", encoding="utf-8")

    assert hf.resolve_card("hf-dataset-card-x.md") == tmp_path / "docs" / "hf-dataset-card-x.md"
    for bad in ("other.md", "../hf-dataset-card-x.md", "docs/hf-dataset-card-x.md",
                "hf-dataset-card-x.txt", ""):
        with pytest.raises(ValueError):
            hf.resolve_card(bad)
    with pytest.raises(FileNotFoundError) as err:
        hf.resolve_card("hf-dataset-card-missing.md")
    assert "hf-dataset-card-x.md" in str(err.value), "the error names what exists"


# ── the hub, read back ───────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status, text="", data=None):
        self.status_code, self.text, self._data = status, text, data

    def json(self):
        return self._data


def test_hub_state_reads_the_measured_block(monkeypatch):
    import requests
    payload = {"private": True, "lastModified": "2026-10-22T16:00:00.000Z",
               "cardData": {"dataset_info": {
                   "features": [{"name": "image"}, {"name": "text"},
                                {"name": "filename"}, {"name": "project_name"}],
                   "splits": [{"name": "train", "num_examples": 281,
                               "num_bytes": 2198208407}],
                   "dataset_size": 2198208407}}}
    seen = {}

    def _get(url, headers=None, timeout=None):
        seen["url"], seen["headers"] = url, headers
        return _Resp(200, data=payload)

    monkeypatch.setattr(requests, "get", _get)
    monkeypatch.setattr(config, "HF_TOKEN", "hf_secret")
    state = hf.hub_state("dh-unibe/x")

    assert state["rows"] == 281 and state["bytes"] == 2198208407
    assert state["columns"] == ["image", "text", "filename", "project_name"]
    assert state["private"] is True
    assert seen["url"].endswith("/api/datasets/dh-unibe/x")
    assert seen["headers"]["Authorization"] == "Bearer hf_secret"
    line = hf.format_hub_state(state)
    assert "281 row(s)" in line and "2.20 GB" in line and "private" in line
    assert "hf_secret" not in line


def test_hub_state_never_raises(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(404))
    assert "error" in hf.hub_state("dh-unibe/x")
    assert "not confirmed" in hf.format_hub_state(hf.hub_state("dh-unibe/x"))

    def _boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "get", _boom)
    assert "ConnectionError" in hf.hub_state("dh-unibe/x")["error"]
    assert hf.hub_readme("dh-unibe/x") is None


# ── the upload path keeps the token out of argv ──────────────────────────────

def test_the_hf_command_line_carries_no_token():
    argv = hf.upload_card_argv("dh-unibe/x", Path("/docs/hf-dataset-card.md"),
                               "/opt/pagexml-hf/bin/hf")
    assert argv == ["/opt/pagexml-hf/bin/hf", "upload", "dh-unibe/x",
                    "/docs/hf-dataset-card.md", "README.md", "--repo-type", "dataset"]
    assert not any("token" in a.lower() for a in argv)


def test_the_job_argv_is_the_documented_cli_and_names_the_card(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "hf-dataset-card-y.md").write_text("# y\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(jobs, "start_and_peek",
                        lambda kind, argv, **kw: seen.update(kind=kind, argv=list(argv)) or {})

    result = jobs.upload_card_job("hf-dataset-card-y.md", repo_id="dh-unibe/y")

    assert seen["kind"] == "upload-hf-card"
    assert seen["argv"][1:4] == ["-m", "agentic_historian", "upload-hf-card"]
    assert seen["argv"][seen["argv"].index("--card") + 1] == "hf-dataset-card-y.md"
    assert "--dry-run" in seen["argv"], "a dry run is the default"
    assert result["repo_id"] == "dh-unibe/y"
    with pytest.raises(jobs.JobError):
        jobs.upload_card_job("../etc/passwd")
    with pytest.raises(jobs.JobError):
        jobs.upload_card_job("hf-dataset-card-y.md", repo_id="not a repo id")


# ── the CLI ──────────────────────────────────────────────────────────────────

def _cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ah_main_691", PKG / "__main__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _card(tmp_path, monkeypatch, text=CARD_PLACEHOLDER):
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    (tmp_path / "docs").mkdir(exist_ok=True)
    card = tmp_path / "docs" / "hf-dataset-card-z.md"
    card.write_text(text, encoding="utf-8")
    monkeypatch.setattr(config, "HF_TOKEN", "hf_secret")
    return card


def test_a_dry_run_shows_the_splice_and_writes_nothing(tmp_path, monkeypatch, capsys):
    cli = _cli()
    card = _card(tmp_path, monkeypatch)
    monkeypatch.setattr(hf, "hub_readme", lambda repo_id, token=None: HUB)
    uploads = []
    monkeypatch.setattr(hf, "upload_card", lambda *a, **k: uploads.append(a) or "x")

    rc = cli.upload_hf_card(SimpleNamespace(card="hf-dataset-card-z.md",
                                            repo_id="dh-unibe/z", dry_run=True))

    out = capsys.readouterr().out
    assert rc == 0 and "num_examples: 281" in out and "dry run" in out
    assert card.read_text(encoding="utf-8") == CARD_PLACEHOLDER
    assert uploads == []
    assert "hf_secret" not in out


def test_a_real_run_writes_the_card_back_and_uploads_it(tmp_path, monkeypatch, capsys):
    cli = _cli()
    card = _card(tmp_path, monkeypatch)
    monkeypatch.setattr(hf, "hub_readme", lambda repo_id, token=None: HUB)
    uploads = []
    monkeypatch.setattr(hf, "upload_card",
                        lambda repo_id, path, token=None: uploads.append((repo_id, path)) or "hf")
    monkeypatch.setattr(hf, "hub_state", lambda repo_id, token=None: {
        "repo_id": repo_id, "rows": 281, "bytes": 2198208407,
        "columns": ["image", "text"], "private": True})

    rc = cli.upload_hf_card(SimpleNamespace(card="hf-dataset-card-z.md",
                                            repo_id="dh-unibe/z", dry_run=False))

    assert rc == 0
    assert uploads == [("dh-unibe/z", card)]
    text = card.read_text(encoding="utf-8")
    assert text.startswith("---\ndataset_info:") and "num_examples: 281" in text
    assert "**Nobody has read this text.**" in text
    out = capsys.readouterr().out
    assert "uploaded hf-dataset-card-z.md as README.md" in out
    assert "281 row(s)" in out


def test_a_dataset_without_a_readme_is_refused(tmp_path, monkeypatch, capsys):
    cli = _cli()
    _card(tmp_path, monkeypatch)
    monkeypatch.setattr(hf, "hub_readme", lambda repo_id, token=None: None)
    rc = cli.upload_hf_card(SimpleNamespace(card="hf-dataset-card-z.md",
                                            repo_id="dh-unibe/z", dry_run=False))
    assert rc == 1 and "upload the dataset first" in capsys.readouterr().err


def test_no_token_is_an_error_before_any_network(tmp_path, monkeypatch, capsys):
    cli = _cli()
    _card(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "HF_TOKEN", "")
    monkeypatch.delenv("HUGGINGFACE_HUB_TOKEN", raising=False)
    called = []
    monkeypatch.setattr(hf, "hub_readme", lambda *a, **k: called.append(1) or HUB)
    rc = cli.upload_hf_card(SimpleNamespace(card="hf-dataset-card-z.md",
                                            repo_id="dh-unibe/z", dry_run=True))
    assert rc == 2 and "HF_TOKEN" in capsys.readouterr().err and called == []


def test_the_tool_is_on_the_server_next_to_upload_hf():
    src = (PKG / "mcp_atr" / "server.py").read_text(encoding="utf-8")
    assert src.index("def upload_hf(") < src.index("def upload_hf_card(")
