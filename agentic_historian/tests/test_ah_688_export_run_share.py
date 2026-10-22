"""#688 — a run's readings as text on the research share, with what explains them.

On 2026-10-22, on request, the eval run went to `Projekt_Lassberg/HTR/VLMs` as
five `cp` lines and a README typed by hand — which had to say what every record
beside a reading already knew: the model, the prompt digest, the settings, which
pages came back empty, that nobody has read the text. This is that export as a
tool, its README written from the records, under the same containment as the
import.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
from mcp_atr import jobs                   # noqa: E402

SETTINGS = "prompt-9e0503e8+think-low|max8192"


def _record(key, text, verdict="ok", truncated=False, model="gemini-3.8-flash",
            when="2026-10-22T16:08:00+00:00"):
    return {"model": model, "run": "gemini-eval-random", "engine": "external",
            "text": text, "settings": SETTINGS, "truncated": truncated,
            "quality": {"verdict": verdict}, "recognised_at": when,
            "source": {"sha256": "abc"}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    runs = tmp_path / "vlm_test"
    run = runs / "gemini-eval-random" / "gemini-3.8-flash"
    run.mkdir(parents=True)
    pages = {
        "Aarau__upload__lassberg-letter-0892__00012": ("Hochwohlgeborner Herr " * 10).strip(),
        "Basel__lassberg-letter-1209__PA 82a B 9_Seite_005": ("Die Handschrift " * 12).strip(),
        "blb lassberg__K 2911,110__page_0002": "",
    }
    for key, text in pages.items():
        verdict = "empty" if not text else "ok"
        (run / f"{key}.json").write_text(json.dumps(_record(key, text, verdict)),
                                         encoding="utf-8")
        (run / f"{key}.txt").write_text(text, encoding="utf-8")
    (runs / "gemini-eval-random" / "report.md").write_text("# ATR batch\n", encoding="utf-8")
    (runs / "score-gt-20261022T164235.md").write_text("# Readings\n", encoding="utf-8")
    (runs / "score-gt-20261022T164235.json").write_text(json.dumps({
        "by_reading": [{"reading": "gemini-eval-random/gemini-3.8-flash", "pages": 34,
                        "median": 0.048, "p90": 0.128, "best": 0.007, "worst": 0.171}],
        "pages": []}), encoding="utf-8")
    gt = tmp_path / "gt"
    gt.mkdir()
    (gt / "eval-random.txt").write_text("# keys\nK1\nK2\n", encoding="utf-8")
    research = tmp_path / "mnt" / "Projekt_Lassberg"
    (research / "HTR").mkdir(parents=True)
    (tmp_path / "mnt" / "other-project").mkdir()
    monkeypatch.setattr(config, "VLM_TEST_ROOT", runs)
    monkeypatch.setattr(config, "GT_ROOT", gt)
    monkeypatch.setattr(config, "ATR_RESEARCH_DIR", research)
    monkeypatch.setattr(config, "ATR_RESEARCH_EXPORT_SUBDIR", "HTR/VLMs")
    return tmp_path


DEST = Path("mnt/Projekt_Lassberg/HTR/VLMs/gemini-eval-random")


# ── the export, as it was done by hand ───────────────────────────────────────

def test_the_export_lays_out_txt_records_report_keys_score_and_readme(world):
    result = jobs.export_run_share("gemini-eval-random", keys="eval-random.txt",
                                   score_report="score-gt-20261022T164235")

    dest = world / DEST
    assert result["ok"] and result["dest"] == str(dest)
    model = dest / "gemini-3.8-flash"
    assert sorted(p.name for p in (model / "txt").iterdir()) == [
        "Aarau__upload__lassberg-letter-0892__00012.txt",
        "Basel__lassberg-letter-1209__PA 82a B 9_Seite_005.txt",
        "blb lassberg__K 2911,110__page_0002.txt",
    ]
    assert len(list((model / "records").glob("*.json"))) == 3
    assert (model / "txt" / "blb lassberg__K 2911,110__page_0002.txt").read_text() == ""
    assert (dest / "report.md").is_file()
    assert (dest / "keys.txt").read_text(encoding="utf-8").endswith("K2\n")
    assert (dest / "score-gt.md").is_file() and (dest / "score-gt.json").is_file()
    assert (dest / "README.md").is_file()
    assert result["pages"] == 3 and result["per_model"] == {"gemini-3.8-flash": 3}
    assert result["files_copied"] == 6
    assert result["extras"] == ["report.md", "keys.txt", "score-gt.md",
                                "score-gt.json", "README.md"]


def test_the_readme_is_written_from_the_records(world):
    jobs.export_run_share("gemini-eval-random", keys="eval-random.txt",
                          score_report="score-gt-20261022T164235")
    readme = (world / DEST / "README.md").read_text(encoding="utf-8")

    assert "nicht Ground Truth" in readme and "Niemand hat diesen Text gelesen" in readme
    assert "`gemini-3.8-flash`" in readme
    assert f"`{SETTINGS}`" in readme and "`9e0503e8`" in readme
    assert "gelesen 2026-10-22" in readme
    assert "- `ok`: 2" in readme and "- `empty`: 1" in readme
    assert "- `blb lassberg__K 2911,110__page_0002`" in readme, "the empty page, by name"
    assert "eval-random.txt" in readme
    assert "| `gemini-eval-random/gemini-3.8-flash` | 34 | 4.8% | 12.8% | 0.7% | 17.1% |" in readme


def test_without_a_report_the_readme_says_none_was_given(world):
    jobs.export_run_share("gemini-eval-random")
    readme = (world / DEST / "README.md").read_text(encoding="utf-8")
    assert "nicht beigelegt" in readme and "## Gegen die Referenz" not in readme
    assert not (world / DEST / "score-gt.md").exists()
    assert not (world / DEST / "keys.txt").exists()


def test_a_dry_run_counts_names_and_writes_nothing(world):
    result = jobs.export_run_share("gemini-eval-random", dry_run=True)
    assert result["dry_run"] and result["files_copied"] == 6
    assert result["dest"] == str(world / DEST)
    assert "Niemand hat diesen Text gelesen" in result["readme"]
    assert not (world / DEST).exists()


def test_a_second_export_skips_what_is_there_and_deletes_nothing(world):
    jobs.export_run_share("gemini-eval-random")
    stray = world / DEST / "gemini-3.8-flash" / "txt" / "stray.txt"
    stray.write_text("keep me", encoding="utf-8")
    again = jobs.export_run_share("gemini-eval-random")
    assert again["files_copied"] == 0 and again["files_present"] == 6
    assert stray.read_text(encoding="utf-8") == "keep me"


def test_the_model_narrows_and_a_dest_renames(world):
    (world / "vlm_test" / "gemini-eval-random" / "other-model").mkdir()
    (world / "vlm_test" / "gemini-eval-random" / "other-model" / "k.json").write_text(
        json.dumps(_record("k", "x", model="other-model")), encoding="utf-8")
    result = jobs.export_run_share("gemini-eval-random", model="gemini-3.8-flash",
                                   dest="eval-random-gemini")
    assert result["per_model"] == {"gemini-3.8-flash": 3}
    assert result["dest"].endswith("HTR/VLMs/eval-random-gemini")
    assert not (world / "mnt/Projekt_Lassberg/HTR/VLMs/eval-random-gemini/other-model").exists()


# ── containment and refusals ─────────────────────────────────────────────────

def test_dest_is_a_name_under_the_export_folder_never_a_path(world):
    with pytest.raises(jobs.JobError):
        jobs.export_run_share("gemini-eval-random", dest="../../other-project")
    with pytest.raises(jobs.JobError):
        jobs.export_run_share("gemini-eval-random", dest="/tmp/x")
    assert not any((world / "mnt" / "other-project").iterdir())


def test_keys_and_report_are_names_and_must_exist(world):
    with pytest.raises(jobs.JobError):
        jobs.export_run_share("gemini-eval-random", keys="no-such.txt")
    with pytest.raises(jobs.JobError):
        jobs.export_run_share("gemini-eval-random", keys="../../etc/passwd")
    with pytest.raises(jobs.JobError):
        jobs.export_run_share("gemini-eval-random", score_report="score-gt-19990101T000000")
    assert jobs.export_run_share("gemini-eval-random",
                                 score_report="score-gt-20261022T164235.md")["ok"]


def test_a_missing_run_or_model_or_root_is_an_error(world, monkeypatch):
    with pytest.raises(jobs.JobError):
        jobs.export_run_share("no-such-run")
    with pytest.raises(jobs.JobError):
        jobs.export_run_share("gemini-eval-random", model="nobody")
    monkeypatch.setattr(config, "ATR_RESEARCH_DIR", None)
    with pytest.raises(jobs.JobError) as err:
        jobs.export_run_share("gemini-eval-random")
    assert "ATR_RESEARCH_DIR" in str(err.value)


def test_a_corpus_run_is_refused(world, monkeypatch):
    import run_export as rx
    many = [rx.RunPage(key=f"k{i}", model="m", text="x", meta={}) for i in range(2001)]
    monkeypatch.setattr(rx, "read_run", lambda *a, **k: many)
    with pytest.raises(jobs.JobError) as err:
        jobs.export_run_share("gemini-eval-random")
    assert "#480" in str(err.value)


def test_the_tool_is_on_the_server():
    src = (PKG / "mcp_atr" / "server.py").read_text(encoding="utf-8")
    assert "def export_run_share(" in src
