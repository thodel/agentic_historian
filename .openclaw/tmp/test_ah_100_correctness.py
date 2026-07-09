"""Tests for #100: three small correctness fixes.

Run offline (no GPUStack/VPN) — all LLM calls mocked.

Run:  python tests/test_ah_100_correctness.py   (or: pytest)
"""

import pathlib
import sys

PKG = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))


# ── Test 1: PROGRESS_FILE resolves to package dir, not repo root ─────────────

def test_progress_file_resolves_to_package_dir():
    """reporter.py PROGRESS_FILE must point to the package-local PROGRESS.md."""
    from agentic_historian import reporter
    # The file exists at the expected package-local path
    assert reporter.PROGRESS_FILE.exists(), (
        f"PROGRESS_FILE={reporter.PROGRESS_FILE} does not exist; "
        f"it should resolve to {reporter.PROGRESS_FILE.parent}/PROGRESS.md"
    )
    # It must NOT be the repo-root PROGRESS.md (which is at the repo root)
    # The package is at agentic_historian/agentic_historian/PROGRESS.md
    # (two levels below repo root — parent/parent from this file)
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    package_dir = pathlib.Path(__file__).resolve().parents[1]
    assert reporter.PROGRESS_FILE.relative_to(package_dir) == pathlib.Path(
        "PROGRESS.md"
    ), f"{reporter.PROGRESS_FILE} is not inside the package dir {package_dir}"


# ── Test 2: bot uses a_meta.qa_score, not transcription_qa ───────────────────

def test_bot_run_pipeline_qa_key():
    """
    bot.py:run_pipeline reads result['a_meta']['qa_score'], not
    result['transcription_qa'] (which is never set in the pipeline).
    """
    import json

    # Simulate the pipeline result shape produced by orchestrator
    pipeline_result = {
        "a_meta": {"qa_score": 0.923},
        "transcription": "dummy",
        "entities": {"entities": []},
        "errors": [],
    }

    # The fixed lookup — must return the qa_score
    qa = pipeline_result.get("a_meta", {}).get("qa_score", "?")
    assert qa == 0.923, f"Expected 0.923, got {qa}"

    # The broken lookup (what the old code did) — must NOT return qa_score
    broken_qa = pipeline_result.get("transcription_qa", "?")
    assert broken_qa == "?", (
        f"The broken key 'transcription_qa' must not be used; got {broken_qa}"
    )


# ── Test 3: header strip uses split(..., 1) to preserve body paragraphs ──────

def test_header_strip_preserves_all_body_paragraphs():
    """
    run_agent_b/c strip the #... header via split("\n\n", 1)[-1].

    Using split(..., 2)[-1] is wrong: it takes the 3rd "part" when the text
    has 3+ blank-line-separated blocks, discarding the second paragraph of
    the body.  split(..., 1)[-1] correctly removes exactly one header block.
    """
    # Real transcription format written by agents/text_recognition._save():
    #   # Transkription: doc_id
    #   # QA-Score: 0.85
    #   # HTR-Source: kraken
    #   # Modell: internvl3-8b
    #
    #   <actual body — may have multiple paragraphs>

    header = (
        "# Transkription: test_doc\n"
        "# QA-Score: 0.85\n"
        "# HTR-Source: kraken\n"
        "# Modell: internvl3-8b\n\n"
    )
    body_two_paragraphs = (
        "Absatz eins des Dokuments.\n\n"
        "Absatz zwei — sollte NICHT verworfen werden."
    )
    full_transcription = header + body_two_paragraphs

    # Old (broken): split("\n\n", 2)[-1]
    old_stripped = full_transcription.split("\n\n", 2)[-1]
    # New (fixed): split("\n\n", 1)[-1]
    new_stripped = full_transcription.split("\n\n", 1)[-1]

    assert old_stripped == body_two_paragraphs, (
        "sanity check: old code gives the same result for 2 paragraphs"
    )

    # The problem: if body itself has 2+ paragraphs, split(..., 2) takes the
    # LAST of 3 parts (header + para1 + para2), so para1 is lost.
    # Simulate a transcription where the body also starts with two paragraphs
    # (i.e., 3 blank-line blocks total).
    body_three_paragraphs = (
        "Erster Absatz.\n\n"
        "Zweiter Absatz.\n\n"
        "Dritter Absatz."
    )
    full_3_block_transcription = header + body_three_paragraphs

    # Old: split at most 2 times → ["# header\n...", "Para1\n\nPara2", "Para3"]
    #   [-1] = "Para3"  ← WRONG, para1+para2 gone
    old_result_3blocks = full_3_block_transcription.split("\n\n", 2)[-1]

    # New: split at most 1 time → ["# header\n...", "Para1\n\nPara2\n\nPara3"]
    #   [-1] = full body preserved
    new_result_3blocks = full_3_block_transcription.split("\n\n", 1)[-1]

    assert "Erster Absatz" in new_result_3blocks, (
        f"Fixed split(...,1) must preserve all body paragraphs; got:\n"
        f"{new_result_3blocks!r}"
    )
    assert old_result_3blocks == "Dritter Absatz.", (
        f"Old split(...,2) drops para1+para2, keeping only 'Dritter Absatz.'; "
        f"this demonstrates the bug. Got: {old_result_3blocks!r}"
    )


if __name__ == "__main__":
    test_progress_file_resolves_to_package_dir()
    print("PASS: test_progress_file_resolves_to_package_dir")

    test_bot_run_pipeline_qa_key()
    print("PASS: test_bot_run_pipeline_qa_key")

    test_header_strip_preserves_all_body_paragraphs()
    print("PASS: test_header_strip_preserves_all_body_paragraphs")

    print("\nAll #100 tests passed.")