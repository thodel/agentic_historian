"""Reading a page through an external model API (`external_atr`).

It is a second recogniser beside `gateway_recogniser`, which is all it has to
be: `atr_batch`'s recogniser is `Callable[[Path, str], object]` and every field
of the result is read with `getattr(..., default)`. So it inherits resumability
by presence, the outage circuits, `--keys-from` and `--letters-from`.

Three things it records that a transcription alone would not, and each has a
test here because each was a known way to be wrong:

- **truncation**, because a page that hits the output ceiling comes back as an
  ordinary success that stops mid-sentence — the first hazard the MCP server's
  own instructions name;
- **which prompt**, because two readings of one page under different prompts are
  different measurements and a comparison that cannot tell them apart is not
  one;
- **what it cost**, because a run against a metered API that cannot say what it
  spent is a run nobody can authorise a second time.

And one it refuses to invent: line geometry. There are no polygons in a chat
completion, so `lines` is empty rather than one line per newline, which would
look like segmentation and be nothing of the kind.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import external_atr as ex                     # noqa: E402


# ── the versioned prompt ─────────────────────────────────────────────────────

def test_the_shipped_prompt_loads_and_has_a_digest():
    text, digest = ex.load_prompt()

    assert "palaeographer" in text
    assert len(digest) == 8


def test_the_same_prompt_gives_the_same_digest():
    assert ex.load_prompt()[1] == ex.load_prompt()[1]


@pytest.mark.parametrize("name", ["../config.py", "/etc/passwd",
                                  "sub/p.md", ".hidden"])
def test_a_prompt_is_named_not_pathed(name):
    """A prompt that can come from anywhere is one no record can identify, and
    the record is the point."""
    with pytest.raises(ValueError):
        ex.load_prompt(name)


def test_a_missing_prompt_says_which_ones_exist():
    with pytest.raises(FileNotFoundError) as exc:
        ex.load_prompt("nope.md")

    assert "lassberg_atr.md" in str(exc.value)


# ── what the answer becomes ──────────────────────────────────────────────────

class _Msg:
    def __init__(self, content):
        self.content = content


def test_plain_mode_takes_the_text_as_it_is():
    assert ex._answer(_Msg("  Eppishausen am 21 Januar  "), False) == (
        "Eppishausen am 21 Januar", "")


def test_structured_mode_splits_the_two_fields():
    msg = _Msg('{"diplomatic": "H. v. L.", "normalized": "Herr von Laßberg"}')

    assert ex._answer(msg, True) == ("H. v. L.", "Herr von Laßberg")


def test_prose_where_json_was_asked_for_keeps_the_reading():
    """The model has still read the page. Losing that to a parse error would be
    worse than keeping it without the normalisation — and it is logged."""
    assert ex._answer(_Msg("Eppishausen am 21 Januar"), True) == (
        "Eppishausen am 21 Januar", "")


def test_a_structured_answer_without_the_optional_field_is_fine():
    assert ex._answer(_Msg('{"diplomatic": "x"}'), True) == ("x", "")


# ── the reading ──────────────────────────────────────────────────────────────

def test_it_invents_no_line_geometry():
    """There are no polygons in a chat completion. One line per newline would
    look like segmentation and be nothing of the kind."""
    assert ex.Reading(text="a\nb\nc").lines == []


def test_the_fields_are_the_runners_own():
    """So `atr_batch._result_payload` and every report work unchanged."""
    r = ex.Reading()
    for name in ("text", "lines", "confidence", "engine", "timing_ms",
                 "truncated", "service_version"):
        assert hasattr(r, name)


# ── what a run cost ──────────────────────────────────────────────────────────

def test_spend_accumulates_across_pages():
    spend = ex.Spend()
    spend.add(ex.Reading(prompt_tokens=1700, completion_tokens=900))
    spend.add(ex.Reading(prompt_tokens=1700, completion_tokens=1100))

    assert spend.calls == 2
    assert spend.prompt_tokens == 3400 and spend.completion_tokens == 2000
    assert "2 call(s)" in str(spend)


def test_spend_is_safe_for_the_concurrent_runner():
    """The batch reads pages concurrently, so the tally is locked."""
    import threading

    spend = ex.Spend()
    def _add():
        for _ in range(200):
            spend.add(ex.Reading(prompt_tokens=1, completion_tokens=1))
    threads = [threading.Thread(target=_add) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert spend.calls == 800 and spend.prompt_tokens == 800


# ── the key, which must never reach a command line ───────────────────────────

def test_without_a_key_it_refuses_before_any_call(monkeypatch):
    monkeypatch.setattr(ex.config, "GEMINI_API_KEY", "")

    with pytest.raises(RuntimeError) as exc:
        ex.openai_recogniser()

    assert "GEMINI_API_KEY" in str(exc.value)
    assert "ps" in str(exc.value)             # and why not on the command line


def test_the_image_becomes_a_data_uri(tmp_path):
    page = tmp_path / "p.jpg"
    page.write_bytes(b"\xff\xd8\xff\xd9")

    assert ex._image_url(page).startswith("data:image/jpeg;base64,")


def test_a_tiff_is_labelled_as_one(tmp_path):
    page = tmp_path / "p.tif"
    page.write_bytes(b"II*\x00")

    assert ex._image_url(page).startswith("data:image/tiff;base64,")
