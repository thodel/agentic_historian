"""#738 — `upload_hf_card` does not write into the production checkout.

On 26.10. the job spliced the hub's front matter into
`docs/hf-dataset-card-blb-gt.md` and wrote it back, as #691 had it do. On tei
that is the production checkout: `main` had a local change, the next
`update.sh` stopped at its pre-flight ("tracked changes on prod"), the change
had to be thrown away by hand and the repository caught up by a PR (#730).
These pin:

* the spliced card goes to the job's directory and is uploaded from there;
* the reply says that `docs/` differs from the hub, with the diff;
* `--write-back` writes `docs/` in a working tree, and is refused in an MCP
  job and on the production checkout — before anything is fetched or uploaded.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import hf_export as hf                     # noqa: E402

# The cards, the hub README and the CLI loader of #691, by path: tests/ is no package.
_spec = importlib.util.spec_from_file_location(
    "ah_test_691_for_738", Path(__file__).with_name("test_ah_691_upload_hf_card.py"))
_t691 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_t691)
CARD_OLD_FM, CARD_PLACEHOLDER, HUB = _t691.CARD_OLD_FM, _t691.CARD_PLACEHOLDER, _t691.HUB
_card, _cli = _t691._card, _t691._cli


def _args(**kw):
    return SimpleNamespace(**{"card": "hf-dataset-card-z.md", "repo_id": "dh-unibe/z",
                              "dry_run": False, "write_back": False, **kw})


def _hub(monkeypatch):
    uploads, fetched = [], []
    monkeypatch.setattr(hf, "hub_readme",
                        lambda repo_id, token=None: fetched.append(repo_id) or HUB)
    monkeypatch.setattr(hf, "upload_card",
                        lambda repo_id, path, token=None: uploads.append(path) or "hf")
    monkeypatch.setattr(hf, "hub_state", lambda repo_id, token=None: {"repo_id": repo_id})
    return uploads, fetched


def _work_tree(tmp_path, monkeypatch, cli):
    monkeypatch.delenv("AH_JOB_DIR", raising=False)
    monkeypatch.setattr(cli, "PRODUCTION_CHECKOUT", tmp_path / "elsewhere")


def test_without_a_job_dir_the_card_goes_to_a_temporary_one(tmp_path, monkeypatch, capsys):
    cli = _cli()
    card = _card(tmp_path, monkeypatch, CARD_OLD_FM)
    _work_tree(tmp_path, monkeypatch, cli)
    uploads, _ = _hub(monkeypatch)

    assert cli.upload_hf_card(_args()) == 0
    (path,) = uploads
    assert path != card and path.name == card.name and "hf-card-" in str(path.parent)
    assert "num_examples: 281" in path.read_text(encoding="utf-8")
    assert card.read_text(encoding="utf-8") == CARD_OLD_FM
    assert "docs/ untouched" in capsys.readouterr().out


def test_the_reply_names_the_difference_with_the_diff(tmp_path, monkeypatch, capsys):
    cli = _cli()
    _card(tmp_path, monkeypatch, CARD_OLD_FM)
    _work_tree(tmp_path, monkeypatch, cli)
    _hub(monkeypatch)

    assert cli.upload_hf_card(_args()) == 0
    out = capsys.readouterr().out
    assert ("docs/hf-dataset-card-z.md differs from the hub in the front matter "
            "only — commit it from a dev checkout") in out
    assert "-    num_examples: 99" in out and "+    num_examples: 281" in out
    assert "+  dataset_size: 2198208407" in out


def test_a_card_that_already_matches_says_so(tmp_path, monkeypatch, capsys):
    cli = _cli()
    _card(tmp_path, monkeypatch, hf.splice_frontmatter(CARD_PLACEHOLDER, HUB))
    _work_tree(tmp_path, monkeypatch, cli)
    _hub(monkeypatch)
    assert cli.upload_hf_card(_args()) == 0
    out = capsys.readouterr().out
    assert "already carries the hub's front matter" in out and "differs" not in out


def test_write_back_writes_docs_in_a_working_tree(tmp_path, monkeypatch, capsys):
    cli = _cli()
    card = _card(tmp_path, monkeypatch)
    _work_tree(tmp_path, monkeypatch, cli)
    uploads, _ = _hub(monkeypatch)

    assert cli.upload_hf_card(_args(write_back=True)) == 0
    assert uploads == [card]
    assert "num_examples: 281" in card.read_text(encoding="utf-8")
    assert "— commit it" in capsys.readouterr().out


def test_write_back_is_refused_in_an_mcp_job(tmp_path, monkeypatch, capsys):
    cli = _cli()
    card = _card(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "PRODUCTION_CHECKOUT", tmp_path / "elsewhere")
    monkeypatch.setenv("AH_JOB_DIR", str(tmp_path / "job"))
    uploads, fetched = _hub(monkeypatch)

    assert cli.upload_hf_card(_args(write_back=True)) == 2
    err = capsys.readouterr().err
    assert "runs as an MCP job" in err and "stops update.sh" in err
    assert uploads == [] and fetched == [], "refused before the network"
    assert card.read_text(encoding="utf-8") == CARD_PLACEHOLDER


def test_write_back_is_refused_on_the_production_checkout(tmp_path, monkeypatch, capsys):
    cli = _cli()
    card = _card(tmp_path, monkeypatch)
    monkeypatch.delenv("AH_JOB_DIR", raising=False)
    monkeypatch.setattr(cli, "PRODUCTION_CHECKOUT", tmp_path)       # REPO_ROOT is tmp_path
    uploads, _ = _hub(monkeypatch)

    assert cli.upload_hf_card(_args(write_back=True)) == 2
    assert "is the production checkout" in capsys.readouterr().err
    assert uploads == [] and card.read_text(encoding="utf-8") == CARD_PLACEHOLDER


def test_the_production_checkout_is_update_shs(monkeypatch):
    monkeypatch.delenv("AH_REPO", raising=False)
    cli = _cli()
    assert cli.PRODUCTION_CHECKOUT == Path("/home/dh/agentic_historian")
    update = (PKG.parent / "update.sh").read_text(encoding="utf-8")
    assert 'REPO="${AH_REPO:-/home/dh/agentic_historian}"' in update


def test_the_job_never_passes_write_back(tmp_path, monkeypatch):
    from mcp_atr import jobs
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    for dry in (True, False):
        assert "--write-back" not in jobs.upload_card_argv("hf-dataset-card-z.md", dry_run=dry)


def test_the_runner_gives_the_job_its_directory():
    src = (PKG / "mcp_atr" / "_run.py").read_text(encoding="utf-8")
    assert 'env["AH_JOB_DIR"] = str(directory)' in src
