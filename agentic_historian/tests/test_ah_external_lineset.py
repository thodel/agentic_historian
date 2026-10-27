"""An external model on a published line test set (`external_lineset`, #747).

What has to hold for the number to sit in the trainer's table: the trainer's
CER definition and edit direction, an unanswered line counted as empty, a
sample spread over the set rather than its first shard, and no line paid for
twice under the same settings.
"""

import json
import sys
import types
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import external_lineset as el                 # noqa: E402
from mcp_atr import lineset                   # noqa: E402
from mcp_atr.jobs import JobError             # noqa: E402


def test_a_sample_is_spread_over_the_set():
    picked = el.chosen(2751, 50)
    assert len(picked) == 50 and min(picked) == 0 and max(picked) > 2600
    assert el.chosen(10, None) == set(range(10)) == el.chosen(10, 99)


def test_score_uses_the_trainers_definitions():
    s = el.score([("Bundesrath", "Bundesrat"), ("", "Bern")])
    assert s["chars"] == 13 and s["errors"] == 5 and s["cer"] == round(5 / 13, 4)
    assert s["deletions"] == 1, "a character the hypothesis added is a deletion (kraken's direction)"
    assert s["insertions"] == 4, "a reference character never written is an insertion"
    assert s["truncated_cer"] == round(4 / 13, 4), "clipped to the reference, the extra h is gone"


def test_an_unanswered_line_counts_as_empty():
    refs = {"a": "Bern", "b": "Zürich"}
    rep = el.report({"a": {"text": "Bern", "finish_reason": "stop"}}, refs)
    assert rep["all"]["cer"] == round(6 / 10, 4) and rep["answered_only"]["cer"] == 0.0
    assert rep["answered"] == 1 and rep["finish_reasons"] == {"stop": 1}


def _parquet(path: Path, n: int):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq
    table = pa.table({"image": [{"bytes": b"\xff\xd8", "path": None}] * n,
                      "text": [f"Zeile {i}" for i in range(n)],
                      "filename": [f"{i}_page_1_line_{i}_docId_9.jpg" for i in range(n)]})
    pq.write_table(table, path)


def test_a_run_reads_scores_and_resumes(tmp_path, monkeypatch):
    shard = tmp_path / "s.parquet"
    _parquet(shard, 6)
    monkeypatch.setattr(el, "fetch", lambda dataset, dest: [shard])
    calls = []

    def read(img, model):
        calls.append(img.name)
        n = int(img.name.split("_")[0])
        if n == 3:
            raise RuntimeError("503")
        return types.SimpleNamespace(text=f"Zeile {n}", truncated=False, prompt_tokens=7,
                                     completion_tokens=2, settings="prompt-x|max8192",
                                     service_version="m@prompt-x", finish_reason="stop")
    read.settings, read.close = "prompt-x|max8192", lambda: None
    fake = types.SimpleNamespace(
        openai_recogniser=lambda **kw: read,
        preflight=lambda m: types.SimpleNamespace(refused=False, line=f"  ok {m}"))
    monkeypatch.setitem(sys.modules, "external_atr", fake)
    monkeypatch.setattr(el.config, "ATR_DATASETS_DIR", None)
    out = tmp_path / "run"
    assert el.main(["--model", "gemini-4-argon", "--out", str(out), "--full", "--workers", "2"]) == 0
    rep = json.loads((out / "report_gemini-4-argon.json").read_text())
    assert rep["lines"] == 6 and rep["answered"] == 5
    assert rep["answered_only"]["cer"] == 0.0 and rep["all"]["cer"] > 0
    assert (out / "errors_gemini-4-argon.jsonl").exists()
    calls.clear()
    el.main(["--model", "gemini-4-argon", "--out", str(out), "--full"])
    assert calls == ["3_page_1_line_3_docId_9.jpg"], "only the failed line is read again"


def test_nothing_runs_without_a_cut(tmp_path):
    with pytest.raises(SystemExit):
        el.main(["--model", "m", "--out", str(tmp_path)])


# ── the MCP side ─────────────────────────────────────────────────────────────

def test_lineset_argv(monkeypatch, tmp_path):
    monkeypatch.setattr(lineset.config, "VLM_TEST_ROOT", tmp_path)
    argv = lineset.lineset_argv("fedmin-argon", "gemini-4-argon", sample=50, reasoning="low")
    assert argv[1] == str(lineset.SCRIPT) and argv[argv.index("--sample") + 1] == "50"
    assert argv[argv.index("--out") + 1] == str(tmp_path / "fedmin-argon")
    assert "--full" in lineset.lineset_argv("r", "m", full=True)
    for kw in ({}, {"sample": 5, "full": True}, {"sample": 5, "dataset": "../x"},
               {"sample": 5, "prompt": "/etc/passwd"}, {"sample": 5, "reasoning": "max"},
               {"sample": 5, "workers": 99}):
        with pytest.raises(JobError):
            lineset.lineset_argv("r", "m", **kw)
    with pytest.raises(JobError):
        lineset.lineset_argv("r", "x;rm", sample=5)


def test_the_line_prompt_ships():
    import external_atr
    text, _ = external_atr.load_prompt("federal_minutes_line.md")
    assert "exactly as written" in text and "one line" in text
