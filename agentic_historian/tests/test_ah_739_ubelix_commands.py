"""#739 — the UBELIX commands `train_spec` prints, as they have to be typed.

26.10.: every line of the old output went wrong once. `scp` from tei to an
`ubelix` alias that does not exist; "in training-atr-models" as a comment, so
an old copy of the scripts in ~/ubelix answered; `JOB_ID=<prepare job id>`,
which is the TRAINER id and which the shell read as a redirection; and two
specs that build the same corpus got two eleven-hour builds.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import train_spec as tsp                   # noqa: E402
from mcp_atr import jobs                   # noqa: E402

SHA = "c25587dd22995eaea1058e78554e20e6194ab3ff"
BLB = "dh-unibe/image-text_lassberg-blb-gt"


def _prepared(name):
    return tsp.prepare(tsp.load(name), resolve=lambda repo: SHA if repo == BLB else None)


def _commands(names, request_dir=tsp.REQUEST_DIR):
    preps = [(n, _prepared(n)) for n in names]
    states = {n: tsp.request_state(n, p, request_dir) for n, p in preps}
    return "\n".join(tsp.ubelix_commands([(n, p["body"]) for n, p in preps], states))


def test_the_request_file_is_named_by_the_revision_it_pinned():
    assert tsp.request_name("blb-qwen35-page-v1", _prepared("blb-qwen35-page-v1")) == \
        "blb-qwen35-page-v1@c25587d.json"


def test_the_committed_requests_are_the_bodies_train_spec_builds():
    """training/requests (#732) and train_spec agree, or UBELIX trains on a body nobody built."""
    for n in ("blb-qwen35-page-v1", "blb-olmocr2-page-v1"):
        st = tsp.request_state(n, _prepared(n))
        assert st == {"file": f"{n}@c25587d.json", "present": True, "same": True}


def test_the_body_is_fetched_on_ubelix_never_scp_from_tei():
    out = _commands(["blb-qwen35-page-v1"])
    assert "scp" not in out and "ubelix:" not in out
    assert ("curl -fsSL -o ~/ubelix/specs/blb-qwen35-page-v1.json "
            "https://raw.githubusercontent.com/thodel/agentic_historian/main/training/requests/"
            "blb-qwen35-page-v1@c25587d.json") in out


def test_everything_runs_in_the_trainers_checkout_after_a_pull():
    first = [l for l in _commands(["blb-qwen35-page-v1"]).splitlines() if not l.startswith("#")][0]
    assert first == "cd ~/training-atr-models && git pull --ff-only"


def test_no_angle_bracket_placeholder_and_the_job_id_is_the_trainers():
    out = _commands(["blb-qwen35-page-v1"])
    assert "<" not in out.replace("2>&1", ""), "a <placeholder> is a redirection to the shell"
    assert "runs/prep-${PREP}.jobid" in out and "--dependency=afterok:$PREP" in out
    assert "sed -n 's/^Submitted batch job" in out, "the Slurm number is read, not typed"


def test_a_missing_id_file_submits_nothing():
    line = next(l for l in _commands(["blb-qwen35-page-v1"]).splitlines() if l.startswith("PREP=$PREP_1"))
    script = line.replace("ubelix/submit.sh", "echo SUBMITTED")
    res = subprocess.run(["bash", "-c", "PREP_1=99999999; USER=nobody-here; " + script],
                         capture_output=True, text=True)
    assert "SUBMITTED" not in res.stdout and "not there yet" in res.stdout


def test_the_same_corpus_is_built_once_and_the_second_queues_behind_it():
    out = _commands(["blb-qwen35-page-v1", "blb-olmocr2-page-v1"])
    assert out.count("ubelix/prepare.sbatch") == 2
    olm = next(l for l in out.splitlines() if l.startswith("PREP_2="))
    assert "--dependency=afterok:$PREP_1" in olm
    assert "same corpus" in out


def test_a_different_corpus_is_built_on_its_own():
    out = _commands(["blb-qwen35-page-v1", "blb-kraken-v1"])
    kr = next(l for l in out.splitlines() if l.startswith("PREP_2="))
    assert "--dependency" not in kr and "same corpus" not in out


def test_a_request_not_on_main_says_how_to_write_and_commit_it(tmp_path):
    out = _commands(["blb-qwen35-page-v1"], request_dir=tmp_path)
    assert out.startswith("# FIRST") and "--write-request" in out


def test_write_request_writes_the_pinned_body(tmp_path):
    path = tsp.write_request("blb-qwen35-page-v1", _prepared("blb-qwen35-page-v1"), tmp_path)
    assert path.name == "blb-qwen35-page-v1@c25587d.json"
    body = json.loads(path.read_text())
    assert any(d.get("revision") == SHA for d in body["datasets"])
    assert "_note" not in path.read_text()


def test_the_tool_takes_several_specs_only_for_ubelix():
    argv = jobs.train_spec_argv("blb-qwen35-page-v1, blb-olmocr2-page-v1", "ubelix")
    assert argv[argv.index("--spec") + 1] == "blb-qwen35-page-v1,blb-olmocr2-page-v1"
    with pytest.raises(jobs.JobError):
        jobs.train_spec_argv("blb-qwen35-page-v1,blb-olmocr2-page-v1", "submit")
    with pytest.raises(jobs.JobError):
        jobs.train_spec_argv("blb-qwen35-page-v1,no-such", "ubelix")
