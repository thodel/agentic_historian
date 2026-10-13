"""
test_ah_638_training_report.py — T5: der Bericht, bei dem jede Zahl beschriftet ist.

`TRAINING_INTEGRATION_PLAN.md` §5 legt die Form fest (der Bot holt den Record
und committet ihn; Kurven zeichnen ist das Outputs-Epic), also prüft dieser Test
nicht die Form, sondern die **Ehrlichkeit der Zahlen** — den Teil, den der Plan
offen lässt und ausdrücklich verlangt:

> Ein Trainingsbericht muss jede Zahl beschriften: welche Referenz, welche
> Normalisierung, welcher Umfang — und muss `ketos test`-CER sichtbar getrennt
> von Eval-Harness-CER halten.

Nach dem Lesen der Contracts sind es vier Zahlen, die alle wie „Genauigkeit"
aussehen, und drei Fallen:

  1. `val_metric` ist eine **Accuracy** — höher ist besser, anders als alle
     anderen hier.
  2. Die Kurve ist **unvollständig per Konstruktion** (kraken behält die zehn
     besten Checkpoints), und `still_improving` ist die eigentliche Aussage.
  3. Die Eval-Harness-Zahl ist nicht im Record und muss als fehlend dastehen,
     sonst steht die `ketos test`-Zahl für sie ein.

Offline: kein Netz, kein Commit — `_commit_files` und das Gateway sind gestubbt.

Run: pytest agentic_historian/tests/test_ah_638_training_report.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import training_report as tr                         # noqa: E402

RUN_ID = "20261012T090000Z-medieval-smoke"


def _job(**over) -> dict:
    job = {
        "id": RUN_ID,
        "status": "completed",
        "request": {
            "engine": "kraken", "model_id": "dh-unibe/medieval-v3",
            "datasets": [{"hf_repo": "dh-unibe/image-text_medieval-scripts_xiv-xv-xvi",
                          "projects": ["SAA_0428"], "max_pages": 40}],
            "params": {"epochs": 10, "spec": "[1,120,0,1 …]"},
        },
        "started_at": "2026-10-12T09:01:00Z",
        "finished_at": "2026-10-12T15:31:00Z",
        "metrics": {"cer": 0.0712, "char_accuracy": 92.88, "samples": 240,
                    "benchmark_cer": 0.0931, "measured_on": "TEST_federal_minutes",
                    "benchmark_samples": 120},
        "model_path": "/srv/models/medieval-v3",
        "code": {"commit": "abc123def4567890", "dirty": False},
        "registration": "in die gemeinsame Registry geschrieben",
        "promoted": True,
        "published": "skip: auto-publish is off "
                     "(ATR_TRAIN_AUTO_PUBLISH_MIN_ACCURACY=0)",
    }
    job.update(over)
    return job


def _curve(**over) -> dict:
    curve = {
        "job_id": RUN_ID, "source": "checkpoints", "complete": False,
        "note": "kraken behält nur die zehn besten Checkpoints",
        "still_improving": True, "last_epoch": 50,
        "best": {"epoch": 48, "val_metric": 0.9288, "val_error": 0.0712},
        "points": [{"epoch": e, "val_metric": 0.9, "val_error": 0.1}
                   for e in range(41, 51)],
    }
    curve.update(over)
    return curve


# ── the four numbers, each with its scope ──────────────────────────────────

def test_all_four_measurements_appear_with_their_scope():
    page = tr.index_md(_job(), _curve())
    assert "Benchmark-CER" in page
    assert "`ketos test`-CER" in page
    assert "val_metric" in page
    assert "Eval-Harness-CER" in page
    # And each carries what it was measured on.
    assert "TEST_federal_minutes" in page
    assert "Zeilenbilder aus der GT-Segmentierung" in page
    assert "krakens eigene Validierung" in page
    assert "unsere** Segmentierung" in page


def test_the_ketos_number_and_the_eval_harness_number_are_kept_apart():
    """The one separation §5 names explicitly: they measure different things and
    will disagree."""
    page = tr.index_md(_job(), _curve())
    ketos = page.index("`ketos test`-CER")
    harness = page.index("Eval-Harness-CER")
    assert ketos != harness
    assert "wird von ihr abweichen" in page


def test_the_missing_eval_harness_number_is_visibly_missing():
    """Leaving it out would let the `ketos test` number stand in for it."""
    [harness] = [n for n in tr.numbers(_job(), _curve())
                 if n.label == "Eval-Harness-CER"]
    assert harness.measured is False
    assert harness.shown() == "nicht gemessen"
    assert "Eval-Harness-CER | nicht gemessen" in tr.index_md(_job(), _curve())


def test_the_accuracy_numbers_say_that_higher_is_better():
    """`val_metric` points the other way from every other number here, and
    `curves.py` gives both values so nobody has to remember which."""
    page = tr.index_md(_job(), _curve())
    rows = {line.split("|")[1].strip(): line for line in page.splitlines()
            if line.startswith("| ") and "ist besser" in line or "| —" in line}
    for label in ("krakens `val_metric` (bester Checkpoint)", "`char_accuracy`"):
        assert "höher ist besser" in rows[label], label
    for label in ("Benchmark-CER", "`ketos test`-CER",
                  "krakens `val_error` (bester Checkpoint)"):
        assert "niedriger ist besser" in rows[label], label


def test_both_directions_of_krakens_metric_are_given():
    labels = {n.label for n in tr.numbers(_job(), _curve())}
    assert "krakens `val_metric` (bester Checkpoint)" in labels
    assert "krakens `val_error` (bester Checkpoint)" in labels


def test_a_fraction_is_shown_as_both_a_fraction_and_a_percentage():
    [cer] = [n for n in tr.numbers(_job(), _curve())
             if n.label == "`ketos test`-CER"]
    assert cer.shown() == "0.0712 (7.12 %)"


def test_a_percentage_keeps_its_unit_and_says_what_it_is_for():
    [acc] = [n for n in tr.numbers(_job(), _curve())
             if n.label == "`char_accuracy`"]
    assert acc.shown() == "92.88 %"
    assert "Hub-Schwelle" in acc.note       # the third unit for the same idea


def test_a_number_that_was_not_measured_has_no_direction():
    """Printing one anyway makes an absence look like a result with a property."""
    [harness] = [n for n in tr.numbers(_job(), _curve())
                 if n.label == "Eval-Harness-CER"]
    assert harness.arrow() == "—"


def test_a_cer_of_zero_is_a_measurement_and_not_a_gap():
    """The project's recurring mistake, in the field where 0 is the best result."""
    page = tr.index_md(_job(metrics={"cer": 0.0, "char_accuracy": 100.0}), _curve())
    assert "0.0000 (0.00 %)" in page
    assert "`ketos test`-CER | nicht gemessen" not in page


def test_a_run_with_no_metrics_says_so_instead_of_printing_zeros():
    page = tr.index_md(_job(metrics={}), {})
    assert "keine** Zahl gemessen" in page
    assert "0.0000" not in page


def test_a_missing_benchmark_says_none_was_named():
    [bench] = [n for n in tr.numbers(_job(metrics={"cer": 0.07}), _curve())
               if n.label == "Benchmark-CER"]
    assert bench.measured is False
    assert "kein Benchmark" in bench.note


def test_the_benchmark_comes_first_because_the_contract_calls_it_primary():
    """The trainer's `Metrics` docstring: the benchmark is the primary quality
    claim, `cer` a secondary check on a split that overlaps training."""
    assert tr.numbers(_job(), _curve())[0].label == "Benchmark-CER"
    page = tr.index_md(_job(), _curve())
    assert page.index("Benchmark-CER") < page.index("`ketos test`-CER")


@pytest.mark.parametrize("metrics", [
    {"cer": 0.07, "samples": 240},        # with the sample count
    {"cer": 0.07},                        # and without it — a separate branch
])
def test_the_split_is_marked_as_overlapping_the_training_projects(metrics):
    """Said in both branches: the overlap is a property of the split, not of
    whether the run happened to report how many samples it scored."""
    [cer] = [n for n in tr.numbers(_job(metrics=metrics), _curve())
             if n.label == "`ketos test`-CER"]
    assert "überlappt die Trainingsprojekte" in cer.note


def test_the_page_warns_against_quoting_one_number_alone():
    page = tr.index_md(_job(), _curve())
    assert "verschiedene** Fragen" in page


# ── the curve, and what it cannot say ─────────────────────────────────────

def test_the_curve_is_reported_as_incomplete_with_the_reason():
    """kraken keeps the top ten checkpoints. Reading the curve as complete is
    the mistake `curves.py` was written to prevent.

    The reason is asserted with the fixture's **own** wording: "zehn besten
    Checkpoints" also appears in the numbers table above, so the obvious
    assertion passes even when the curve section says nothing at all.
    """
    curve = _curve(note="nur die Top-10-Checkpoints liegen auf der Platte")
    page = tr.index_md(_job(), curve)
    assert "**Vollständig:** nein — nur die Top-10-Checkpoints liegen auf der "
    assert "nur die Top-10-Checkpoints liegen auf der Platte" in page


def test_the_default_reason_is_given_when_the_curve_states_none():
    page = tr.index_md(_job(), _curve(note=""))
    assert "**Vollständig:** nein — kraken behält nur die zehn besten" in page


def test_still_improving_is_turned_into_the_finding_it_implies():
    """Checkpoints 41–50 mean "was still getting better"; 3–12 mean "peaked
    early and got worse for forty epochs". Those call for opposite responses."""
    late = tr.index_md(_job(), _curve(still_improving=True))
    assert "noch besser:** ja" in late
    assert "Länger trainieren" in late

    early = tr.index_md(_job(), _curve(
        still_improving=False,
        points=[{"epoch": e, "val_metric": 0.5} for e in range(3, 13)]))
    assert "noch besser:** nein" in early
    assert "Daten oder die Konfiguration" in early


def test_an_unknown_still_improving_is_unknown_and_not_no():
    page = tr.index_md(_job(), _curve(still_improving=None))
    assert "noch besser:** unbekannt" in page


def test_the_recorded_epoch_range_is_named():
    page = tr.index_md(_job(), _curve())
    assert "Epoche 41–50" in page


def test_a_live_reading_is_marked_as_not_final():
    page = tr.index_md(_job(status="training"), _curve(live=True))
    assert "Live gelesen" in page
    assert "kein Endstand" in page


def test_a_curve_with_no_points_says_why():
    page = tr.index_md(_job(), {"points": [], "note": "job is queued"})
    assert "Keine Epochen" in page
    assert "job is queued" in page


def test_no_curve_at_all_is_not_an_empty_curve():
    page = tr.index_md(_job(), None)
    assert "Keine Kurve abgeholt" in page


def test_a_complete_curve_says_so():
    page = tr.index_md(_job(), _curve(complete=True))
    assert "**Vollständig:** ja" in page


# ── the rest of the record ────────────────────────────────────────────────

def test_the_datasets_are_named_with_their_caps():
    page = tr.index_md(_job(), _curve())
    assert "dh-unibe/image-text_medieval-scripts_xiv-xv-xvi" in page
    assert "SAA_0428" in page
    assert "max. 40 Seiten" in page


def test_an_all_projects_dataset_says_all_projects():
    job = _job()
    job["request"]["datasets"] = [{"hf_repo": "r", "all_projects": True,
                                   "max_pages": 500}]
    assert "alle Projekte" in tr.index_md(job, _curve())


def test_the_code_commit_is_named_so_a_number_has_an_evaluator():
    page = tr.index_md(_job(), _curve())
    assert "abc123def456" in page


def test_dirty_code_is_marked_as_dirty():
    page = tr.index_md(_job(code={"commit": "abc1234567890", "dirty": True}),
                       _curve())
    assert "(dirty)" in page


def test_the_wall_time_comes_from_the_records_own_timestamps():
    assert "6.5 h" in tr.index_md(_job(), _curve())


def test_a_run_without_timestamps_has_an_unknown_wall_time():
    page = tr.index_md(_job(started_at=None, finished_at=None), _curve())
    assert "Laufzeit | unbekannt" in page


def test_the_trainers_own_sentences_are_copied_verbatim():
    """Each of these is already a sentence the trainer wrote about what it did —
    including when it did nothing (#88)."""
    page = tr.index_md(_job(), _curve())
    assert "ATR_TRAIN_AUTO_PUBLISH_MIN_ACCURACY=0" in page
    assert "in die gemeinsame Registry geschrieben" in page
    assert "**Promoviert:** ja" in page


def test_a_run_that_was_not_promoted_says_no_rather_than_nothing():
    page = tr.index_md(_job(promoted=False), _curve())
    assert "**Promoviert:** nein" in page


def test_a_guard_that_was_overridden_is_on_the_page():
    """A CER from a run known not to converge must never read as an ordinary
    one — the trainer records that, so the page carries it."""
    page = tr.index_md(_job(convergence_override="zu wenige Optimizer-Schritte"),
                       _curve())
    assert "Konvergenz-Guard übergangen" in page


def test_the_request_is_reproducible_from_the_page():
    page = tr.index_md(_job(), _curve())
    assert "## Reproduzierbarkeit" in page
    assert '"epochs": 10' in page


def test_a_run_that_did_not_complete_is_marked_at_the_top():
    page = tr.index_md(_job(status="failed"), _curve())
    assert page.index("endete als `failed`") < page.index("## Zahlen")
    assert "kein fertiges Modell" in page


# ── the files and the path ───────────────────────────────────────────────

def test_the_files_land_under_the_runs_own_directory():
    out = tr.files(_job(), _curve())
    assert set(out) == {
        f"docs/training/{RUN_ID}/index.md",
        f"docs/training/{RUN_ID}/job.json",
        f"docs/training/{RUN_ID}/training.json",
    }


def test_the_raw_record_and_curve_go_beside_the_page_unchanged():
    """The page is a reading of them; a reader who distrusts the reading needs
    the source."""
    out = tr.files(_job(), _curve())
    stored = json.loads(out[f"docs/training/{RUN_ID}/job.json"].decode())
    assert stored["metrics"]["cer"] == 0.0712
    curve = json.loads(out[f"docs/training/{RUN_ID}/training.json"].decode())
    assert curve["best"]["epoch"] == 48


def test_a_run_without_a_curve_still_publishes_a_report():
    """It says what was asked for and what it cost. A missing curve is not a
    reason to publish nothing."""
    out = tr.files(_job(), None)
    assert f"docs/training/{RUN_ID}/index.md" in out
    assert f"docs/training/{RUN_ID}/training.json" not in out


@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "a/b", ".", "..", "", "   ", "x" * 65,
    "run?q=1", "run#frag", "run id",
])
def test_a_run_id_that_may_not_become_a_path_is_refused(bad):
    """Refused here rather than published and retired later — the project has
    already had to retire addresses built from unchecked ids."""
    with pytest.raises(tr.RunIdRefused):
        tr.run_path(bad)
    with pytest.raises(tr.RunIdRefused):
        tr.files({"id": bad}, None)


@pytest.mark.parametrize("good", [
    RUN_ID, "job-42", "20261012T090000Z", "a.b_c-d", "x" * 64,
])
def test_a_plausible_run_id_is_accepted(good):
    assert tr.run_path(good) == f"docs/training/{good}"


def test_the_page_is_valid_jekyll_front_matter():
    page = tr.index_md(_job(), _curve())
    assert page.startswith("---\nlayout: default\n")
    assert page.split("---")[2].lstrip().startswith("# Trainingslauf")


# ── the publish seam ────────────────────────────────────────────────────

def test_publishing_commits_the_files_in_one_commit(monkeypatch):
    from utils import publish_github

    seen = {}

    def _commit(files, message, session=None, **kw):
        seen["files"], seen["message"] = files, message
        return "https://github.com/x/y/commit/abc"

    monkeypatch.setattr(publish_github, "is_enabled", lambda: True)
    monkeypatch.setattr(publish_github, "_commit_files", _commit)

    url = publish_github.publish_training(RUN_ID, tr.files(_job(), _curve()))
    assert url.endswith("/abc")
    assert len(seen["files"]) == 3
    assert RUN_ID in seen["message"]


def test_publishing_is_a_no_op_when_disabled(monkeypatch):
    from utils import publish_github

    monkeypatch.setattr(publish_github, "is_enabled", lambda: False)
    monkeypatch.setattr(publish_github, "_commit_files",
                        lambda *a, **k: pytest.fail("committed while disabled"))
    assert publish_github.publish_training(RUN_ID, tr.files(_job(), None)) is None


def test_a_failed_commit_does_not_raise(monkeypatch):
    """A training run that finished is not a failed run because a commit did
    not happen."""
    from utils import publish_github

    def _boom(*a, **k):
        raise RuntimeError("502 from GitHub")

    monkeypatch.setattr(publish_github, "is_enabled", lambda: True)
    monkeypatch.setattr(publish_github, "_commit_files", _boom)
    assert publish_github.publish_training(RUN_ID, tr.files(_job(), None)) is None


def test_an_unchanged_tree_is_not_a_failure(monkeypatch):
    """`_commit_files` returns None when the tree matches — a retry after a 5xx
    that actually landed."""
    from utils import publish_github

    monkeypatch.setattr(publish_github, "is_enabled", lambda: True)
    monkeypatch.setattr(publish_github, "_commit_files", lambda *a, **k: None)
    assert publish_github.publish_training(RUN_ID, tr.files(_job(), None)) is None


def test_nothing_to_publish_is_not_an_error(monkeypatch):
    from utils import publish_github

    monkeypatch.setattr(publish_github, "is_enabled",
                        lambda: pytest.fail("checked the flag before the files"))
    assert publish_github.publish_training(RUN_ID, {}) is None


# ── the bot side ───────────────────────────────────────────────────────

def _source() -> str:
    return (PKG / "bot.py").read_text(encoding="utf-8")


def test_only_a_completed_run_is_published_automatically():
    """A catalogue entry about a failed run is a decision, not a side effect."""
    source = _source()
    block = source[source.index("async def _train_watch_one("):]
    block = block[:block.index("\nasync def _train_say(")]
    assert 'fresh.last_status == "completed"' in block
    assert "_publish_training" in block


def test_the_report_is_published_before_the_run_leaves_the_registry():
    source = _source()
    block = source[source.index("async def _train_watch_one("):]
    block = block[:block.index("\nasync def _train_say(")]
    after = block[block.index("train_watch.decide("):]
    assert after.index("_publish_training") < after.index("train_watch.drop")


def test_the_manual_command_is_admin_gated():
    """It writes to a public repository."""
    source = _source()
    block = source[source.index('name="train_publish"'):]
    decorators = block[:block.index("async def")]
    assert "@admin_only" in decorators
    assert "@require_role" not in decorators


def test_the_command_refuses_a_bad_id_before_asking_the_gateway():
    source = _source()
    block = source[source.index("async def train_publish_cmd("):]
    block = block[:block.index("\n# ── /sparql")]
    assert block.index("run_path(job_id)") < block.index("_publish_training")


def test_a_missing_curve_does_not_stop_the_publish():
    source = _source()
    block = source[source.index("async def _publish_training("):]
    block = block[:block.index("\n@bot.slash_command")]
    # The curve is fetched in its own try, and its failure only logs.
    assert "no curve" in block
    assert block.count("atr_status.AtrStatusError") == 2


def test_the_command_registers_with_a_required_job_id():
    import bot as bot_module

    commands = {c.name: c for c in bot_module.bot.pending_application_commands}
    assert "train_publish" in commands
    [job_id] = commands["train_publish"].options
    assert job_id.name == "job_id" and job_id.required is True


def test_the_gateway_has_a_curve_call():
    import atr_status

    assert callable(atr_status.curve)
    source = (PKG / "atr_status.py").read_text(encoding="utf-8")
    block = source[source.index("async def curve("):]
    block = block[:block.index("\nasync def log(")]
    # Through the id guard, like every other path parameter (SEC-6, #577).
    assert "_safe_job_id(job_id)" in block
