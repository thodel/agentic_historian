"""Rebuilding a run's report from the results on disk (`report-run`).

The report a run writes records what the runner *observed*, and that is not the
same as what is on disk. A resumed run sees most of its corpus as ``skipped`` and
counts nothing about it — including whether a page came back empty, which is the
one failure mode no other column reveals. The corpus run of 2026-09-17 hit the
other case: it started before the empty column existed and finished after, so its
report has no such column while 77 of its 899 pages were blank.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_batch as batch                       # noqa: E402


def write_result(run_dir: Path, model: str, key: str, text: str, *,
                 at="2026-09-17T06:30:41+00:00", ms=38000, lines=3,
                 truncated=False, schema=None) -> Path:
    d = run_dir / model
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.txt").write_text(text, encoding="utf-8")
    payload = {
        "schema": schema or batch.SCHEMA,
        "run": run_dir.name,
        "model": model,
        "text": text,
        "lines": [{"text": text}] * lines,
        "timing_ms": ms,
        "truncated": truncated,
        "recognised_at": at,
        "source": {"name": f"{key}.tif", "key": key, "sha256": "x", "bytes": 1},
    }
    path = d / f"{key}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    run = tmp_path / "atr_trocr_corpus"
    write_result(run, "trocr-kurrent", "p001", "Euer Hochwohlgeboren",
                 at="2026-09-17T06:30:41+00:00")
    write_result(run, "trocr-kurrent", "p002", "", at="2026-09-17T06:40:41+00:00")
    write_result(run, "trocr-kurrent", "p003", "Mein lieber Freund",
                 at="2026-09-17T07:30:41+00:00")
    return run


# ── what a rebuild recovers ──────────────────────────────────────────────────

def test_empty_pages_are_counted_however_the_run_went(run_dir):
    """The whole point: a resumed run's report says nothing about these."""
    report = batch.report_from_outputs(run_dir)

    (model,) = report.models
    assert (model.done, model.empty) == (3, 1)
    assert model.empty_keys == ["p002"]


def test_the_empty_section_names_them(run_dir):
    md = batch.format_report(batch.report_from_outputs(run_dir))

    assert "## Pages that came back empty" in md
    assert "p002" in md
    assert "| `trocr-kurrent` | 3 | 0 | 0 | 1 |" in md      # read/skipped/failed/empty


def test_characters_timings_and_wall_come_from_the_results(run_dir):
    report = batch.report_from_outputs(run_dir)

    (model,) = report.models
    assert model.chars == len("Euer Hochwohlgeboren") + len("Mein lieber Freund")
    assert model.lines == 9
    assert model.recognition_ms == 3 * 38000
    assert model.elapsed_s == 3600.0                        # 06:30:41 → 07:30:41
    assert report.started_at.startswith("2026-09-17T06:30:41")


def test_the_run_name_comes_from_the_results_not_the_folder(tmp_path):
    run = tmp_path / "renamed-since"
    write_result(run, "trocr-kurrent", "p001", "x")

    assert batch.report_from_outputs(run).run == "renamed-since"


def test_several_models_are_reported_side_by_side(run_dir):
    write_result(run_dir, "kraken-dh", "p001", "fuer hochbohlgeboren")

    report = batch.report_from_outputs(run_dir)

    assert [m.model for m in report.models] == ["kraken-dh", "trocr-kurrent"]
    assert report.pages == 3                                # the longest model


def test_truncation_is_carried_through(run_dir):
    write_result(run_dir, "trocr-kurrent", "p004", "cut off here", truncated=True)

    report = batch.report_from_outputs(run_dir)

    assert report.models[0].truncated == 1
    assert "cut off at the token ceiling" in batch.format_report(report).lower() \
        or "cut off" in batch.format_report(report)


# ── what a rebuild admits it cannot know ─────────────────────────────────────

def test_a_rebuilt_report_says_it_is_rebuilt(run_dir):
    report = batch.report_from_outputs(run_dir)
    md = batch.format_report(report)

    assert report.rebuilt is True
    assert report.to_dict()["rebuilt"] is True
    assert "Rebuilt from the files in this directory" in md
    assert "failed column is not trustworthy" in md


def test_a_result_of_another_shape_counts_as_failed_not_as_read(run_dir):
    """An older schema is a page that has to be read again, not a page that was."""
    write_result(run_dir, "trocr-kurrent", "p009", "from an older runner",
                 schema="atr-batch/0")

    (model,) = batch.report_from_outputs(run_dir).models
    assert (model.done, model.failed) == (3, 1)
    assert "p009" in model.errors[0]


def test_an_unparsable_result_counts_as_failed(run_dir):
    (run_dir / "trocr-kurrent" / "p010.json").write_text("{half", encoding="utf-8")

    (model,) = batch.report_from_outputs(run_dir).models
    assert model.failed == 1


def test_a_missing_run_directory_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        batch.report_from_outputs(tmp_path / "never-ran")


def test_a_run_directory_with_no_models_reports_nothing(tmp_path):
    empty = tmp_path / "run"
    empty.mkdir()

    report = batch.report_from_outputs(empty)

    assert report.models == [] and report.pages == 0


# ── the CLI ──────────────────────────────────────────────────────────────────

def _cli():
    import importlib.util

    spec = importlib.util.spec_from_file_location("ah_cli", PKG / "__main__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_run_writes_both_files(run_dir, capsys):
    mod = _cli()
    args = mod.build_parser().parse_args(["report-run", "--run-dir", str(run_dir)])

    assert mod.report_run(args) == 0
    assert (run_dir / "report.md").exists()
    data = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert data["rebuilt"] is True and data["models"][0]["empty"] == 1


def test_report_run_dry_run_leaves_the_files_alone(run_dir, capsys):
    mod = _cli()
    (run_dir / "report.md").write_text("the old one", encoding="utf-8")
    args = mod.build_parser().parse_args(
        ["report-run", "--run-dir", str(run_dir), "--dry-run"])

    assert mod.report_run(args) == 0
    assert (run_dir / "report.md").read_text(encoding="utf-8") == "the old one"
    assert "not written" in capsys.readouterr().out
