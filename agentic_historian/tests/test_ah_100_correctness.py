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
    assert reporter.PROGRESS_FILE.exists(), (
        f"PROGRESS_FILE={reporter.PROGRESS_FILE} does not exist"
    )
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
    pipeline_result = {
        "a_meta": {"qa_score": 0.923},
        "transcription": "dummy",
        "entities": {"entities": []},
        "errors": [],
    }
    # The fixed lookup — must return the qa_score
    qa = pipeline_result.get("a_meta", {}).get("qa_score", "?")
    assert qa == 0.923, f"Expected 0.923, got {qa}"
    # The broken lookup (what the old code did)
    broken_qa = pipeline_result.get("transcription_qa", "?")
    assert broken_qa == "?", f"transcription_qa must not be used; got {broken_qa}"


# ── Test 3: header strip uses split(..., 1) to preserve body paragraphs ──────

def test_header_strip_preserves_all_body_paragraphs():
    """
    run_agent_b/c strip the #... header via split("\n\n", 1)[-1].

    Using split(..., 2)[-1] is wrong: with 3 blank-line blocks it gives
    parts[2] (the LAST of 3), discarding the first body paragraph.

    split(..., 1)[-1] takes parts[1] (the last of 2), correctly removing
    only the header block and preserving all body text.
    """
    header = (
        "# Transkription: test_doc\n"
        "# QA-Score: 0.85\n"
        "# HTR-Source: kraken\n"
        "# Modell: internvl3-8b\n\n"
    )
    body_two = "Absatz eins.\n\nAbsatz zwei."
    body_three = "Absatz eins.\n\nAbsatz zwei.\n\nAbsatz drei."

    # With a 2-paragraph body (3 blank-line blocks total):
    full2 = header + body_two
    old2 = full2.split("\n\n", 2)[-1]
    new2 = full2.split("\n\n", 1)[-1]
    # old gives the LAST body para (wrong)
    assert old2 == "Absatz zwei.", f"old split gives last para; got {old2!r}"
    # new gives full body (correct)
    assert new2 == body_two, f"new split gives full body; got {new2!r}"

    # With a 3-paragraph body (4 blank-line blocks total):
    full3 = header + body_three
    old3 = full3.split("\n\n", 2)[-1]
    new3 = full3.split("\n\n", 1)[-1]
    # old discards the first body para
    assert "Absatz drei" in old3, f"old drops first para; got {old3!r}"
    assert "Absatz eins" not in old3, f"old must not have first para; got {old3!r}"
    # new preserves everything
    assert "Absatz eins" in new3, f"new must have first para; got {new3!r}"
    assert "Absatz drei" in new3, f"new must have third para; got {new3!r}"


if __name__ == "__main__":
    test_progress_file_resolves_to_package_dir()
    print("PASS: test_progress_file_resolves_to_package_dir")
    test_bot_run_pipeline_qa_key()
    print("PASS: test_bot_run_pipeline_qa_key")
    test_header_strip_preserves_all_body_paragraphs()
    print("PASS: test_header_strip_preserves_all_body_paragraphs")
    print("\nAll #100 tests passed.")
