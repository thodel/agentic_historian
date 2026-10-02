"""Tests for #103: Silent truncation in Agents B & C.

Run offline (no GPUStack/VPN) — file-level checks + functional tests.
"""

import re
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))


SD_PATH = "agentic_historian/agents/source_description.py"
EA_PATH = "agentic_historian/agents/entity_agent.py"


def read(path):
    with open(path) as f:
        return f.read()


# ── Agent B: describe() no longer uses [:4000] slice ─────────────────────────

def test_agent_b_no_4000_truncation():
    src = read(SD_PATH)
    idx = src.find("def describe(")
    # 6000, not 3000: #301 added the image-only description branch at the top of
    # describe(), pushing the chunking code past a 3000-char window.
    func = src[idx:idx + 6000]
    assert "transcription[:4000]" not in func
    assert "transcription_snippet" in func


# ── Agent B: _care_flag samples beginning AND end ─────────────────────────────

def test_care_flag_uses_first_and_last_chunk():
    src = read(SD_PATH)
    idx = src.find("def _care_flag(")
    func = src[idx:idx + 800]
    assert "transcription[-" in func, "_care_flag must sample from END of doc"


# ── Agent C: all chunks processed, not [:30_000] prefix ───────────────────────

def test_agent_c_processes_all_chunks():
    src = read(EA_PATH)
    assert "def _chunk_text" in src, "entity_agent.py must define _chunk_text()"
    idx = src.find("def _extract_llm(")
    func = src[idx:idx + 2000]
    assert "transcription[:30_000]" not in func, "_extract_llm still truncates [:30000]"
    assert "_chunk_text" in func, "_extract_llm must call _chunk_text"


# ── Agent C: max_tokens raised ───────────────────────────────────────────────

def test_agent_c_maxtokens_raised():
    src = read(EA_PATH)
    idx = src.find("def _extract_llm(")
    func = src[idx:idx + 2000]
    mt = re.search(r"max_tokens\s*=\s*(\d+)", func)
    assert mt, "max_tokens not found"
    val = int(mt.group(1))
    assert val >= 8000, f"max_tokens={val} is too small (should be >= 8000)"


# ── Functional: _chunk_text returns correct chunks ───────────────────────────

def test_chunk_text_splits_large_text_into_correct_chunks():
    """_chunk_text(chunk_size=25000, overlap=2000) on 50k chars → 3 chunks.

    Chunks:  [0:25000], [23000:48000], [46000:71000]
    Step = chunk_size - overlap = 23000; 3 steps to cover 50000.
    Each chunk ≤ 25000 chars.  Last chunk extends to end of text (with padding).

    Against the real `_chunk_text` (#466). This test used to define its own
    copy of the function and check that — so it proved a copy correct while the
    shipped chunker was never exercised here, and would have stayed green if
    that one broke. A second copy of the chunking rule is also exactly the
    coupling #466 is about: change the step and every offset in the passage
    database is silently wrong.
    """
    from agents.entity_agent import _chunk_text

    long_text = "X" * 50_000
    chunks = [c.text for c in _chunk_text(long_text)]

    # With 25k/2k, 50k → 3 chunks
    assert len(chunks) == 3, f"Expected 3 chunks for 50k, got {len(chunks)}"

    # Each chunk ≤ chunk_size
    for i, c in enumerate(chunks):
        assert len(c) <= 25_000, f"Chunk {i} too large: {len(c)}"

    # Chunk 1: positions 0-24999
    assert chunks[0] == "X" * 25_000
    # Chunk 2: positions 23000-47999 (23k overlap)
    assert chunks[1] == "X" * 25_000
    assert chunks[1].startswith("X" * 23_000)  # 23000-char overlap confirmed
    # Chunk 3: positions 46000-end (remaining 4000 chars + padding)
    assert chunks[2] == "X" * 4_000, f"Chunk 3 should be 4000 chars (46000-50000), got {len(chunks[2])}"
    assert chunks[2].startswith("X" * 2_000)  # 2000-char overlap confirmed

    # Small text returns single chunk
    short = "A" * 1000
    assert [c.text for c in _chunk_text(short)] == ["A" * 1000]

    # Exact chunk_size returns single chunk
    exact = "B" * 25_000
    assert [c.text for c in _chunk_text(exact)] == ["B" * 25_000]

    # And each chunk now says where it begins, which is what makes an offset
    # expressible at all (#466). Checked against the slice it claims to be.
    for piece in _chunk_text(long_text):
        assert long_text[piece.start:piece.end] == piece.text


# ── Acceptance: all chunks processed for >30k input ─────────────────────────

def test_all_chunks_processed_for_large_input(monkeypatch):
    """Every chunk reaches the model, measured rather than grepped.

    This asserted that the source of `_extract_llm` contained the literal text
    `for i, chunk in enumerate(chunks)`, so renaming a loop variable broke it
    while the behaviour it cares about was untouched (#466 renamed it to
    `piece`, which carries a chunk and its offset). Counting the calls is what
    the test was always trying to say, and it still fails on the defect #103
    opened for: a `transcription[:30_000]` prefix would make one call where
    three are due.
    """
    from agents import entity_agent as ec

    seen = []

    def fake(prompt, **kw):
        seen.append(prompt)
        return '{"entities": []}'

    monkeypatch.setattr(ec.gs, "chat_text", fake)
    # Distinct markers, so a prompt says which chunk it carries. All-"X" text
    # cannot: every chunk is a substring of every other.
    long_text = "".join(f"<<{i:05d}>>" + "X" * 90 for i in range(500))
    chunks = ec._chunk_text(long_text)
    ec._extract_llm(long_text)

    assert len(chunks) == 3
    # Coverage, not call count: a chunk with no entities in it answers
    # `{"entities": []}`, which the retry path treats as an unparsable reply and
    # asks again, so the number of calls is not the number of chunks. What #103
    # is about is that no chunk is skipped.
    for piece in chunks:
        assert any(piece.text in prompt for prompt in seen), \
            f"chunk at {piece.start} never reached the model"


if __name__ == "__main__":
    test_agent_b_no_4000_truncation()
    print("PASS: test_agent_b_no_4000_truncation")
    test_care_flag_uses_first_and_last_chunk()
    print("PASS: test_care_flag_uses_first_and_last_chunk")
    test_agent_c_processes_all_chunks()
    print("PASS: test_agent_c_processes_all_chunks")
    test_agent_c_maxtokens_raised()
    print("PASS: test_agent_c_maxtokens_raised")
    test_chunk_text_splits_large_text_into_correct_chunks()
    print("PASS: test_chunk_text_splits_large_text_into_correct_chunks")
    # test_all_chunks_processed_for_large_input takes pytest's monkeypatch
    # fixture (it counts real calls now instead of grepping the source), so it
    # runs under pytest rather than from here.
    print("\nAll #103 tests passed (run pytest for the full set).")
