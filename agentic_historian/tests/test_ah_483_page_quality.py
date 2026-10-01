"""What kind of reading a page came back with (#483).

The report counted empty pages and said nothing about the other way a reading
fails. Measured on the Laßberg corpus, 2026-10-01:

    Briefe UB Freiburg__lassberg-letter-0083__002
      trocr-kurrent:  656 characters
      qwen3.5-4b:   8 824 characters, some 8 000 of them "000000000000…"

`truncated` false, no error, a 200 and two files on disk. By every signal the
runner had, that page was read — and it carries seven thousand characters of
padding, so it moves every average that includes it. The reading comparison put
qwen3.5-4b at 1183 characters a page against trocr's 973, and about twenty-one
such pages out of 778 account for the whole gap.

The fixtures below are the real readings, shortened only where the padding is.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import page_quality as pq                       # noqa: E402

# The French page the VLM padded, with the run of zeros kept (shortened to 3000).
LOOPED = ("Eoubles de la Geurve ne metaitent toute correspondence la Murie de "
          "Sens, qui ne respectent pas trop le Serêt des Lettres. D'après toute "
          "Aparnu été Suomoditéc affinir sous peu de jours.\n10 bis 12"
          + "0" * 3000)

# A full letter page, read coherently. 1043 characters on the real page.
LETTER = ("Stuttgard, den 3. Apr\nErhalten u. geantwortet den 12t April.\n1870.\n"
          "Hochwohlgeboren Breichner,\nHochzuverehrender Herr!\n"
          "In dem Stande das mir kürzlich zu Gehalt gekommenen Lindehalb haben "
          "sei. Hochwohlgeboren eine Galerie den Münzungen des Schluges und der "
          "benachbarten Gegend aufgestellt, welche für mich um so anziehendes "
          "war, als ich selbst mit mehrerer die her allen Dichten mich näher "
          "beschäftigt haben.")

# A letter page that happens to use "der" eight times — the obvious false
# positive for any naive repetition check.
MANY_DER = ("der Vater und der Sohn und der Bruder\nwaren in der Stadt, und der "
            "Rat\nhat der Sache den Lauf gelassen, wie der\nBrauch es will, und "
            "der Schreiber\nvermerkte der Ordnung nach, was der\nHerr der "
            "Versammlung vorgetragen hat und was der Rat beschloß.")


# ── the four verdicts ────────────────────────────────────────────────────────

def test_the_padded_page_is_repetitive():
    """The whole incident in one assertion."""
    assert pq.classify(LOOPED).verdict == "repetitive"


def test_a_full_letter_page_is_ok():
    assert pq.classify(LETTER).verdict == "ok"


def test_a_page_with_eight_ders_is_not_a_loop():
    """"Text contains many 'der'" would have caught this one. A measure over
    repeated *lines* and character runs does not."""
    q = pq.classify(MANY_DER)
    assert q.verdict == "ok"
    assert q.repeat_ratio == 0.0


def test_whitespace_only_is_empty():
    assert pq.classify("   \n  \t ").verdict == "empty"


def test_a_page_number_is_short_not_empty():
    """`53` on an otherwise blank page: it read something, just not a page."""
    assert pq.classify("53").verdict == "short"


def test_shortness_wins_over_repetition():
    """`repetitive` is a claim about a model that lost control on a page it was
    reading, and three words cannot support it."""
    q = pq.classify("der der der der der der der der")
    assert q.verdict == "short"
    assert q.repeat_ratio == 0.0, "one line, so there is nothing to repeat"


# ── the measures, kept so a threshold change is arithmetic ───────────────────

def test_the_measures_are_kept_beside_the_verdict():
    q = pq.classify(LOOPED)
    assert q.longest_char_run == 3000
    assert q.chars > 3000 and q.lines == 2
    assert set(q.as_dict()) == {"verdict", "chars", "lines",
                                "longest_char_run", "repeat_ratio"}


def test_a_lowered_threshold_reclassifies_without_rereading():
    """The point of storing the measures: changing the rule is arithmetic, not a
    reason to read a corpus again."""
    assert pq.classify(LETTER, max_char_run=2).verdict == "repetitive"


# ── the two signals, separately ──────────────────────────────────────────────

def test_a_long_run_of_one_character_is_caught():
    body = "Euer Hochwohlgeboren haben vor zwei Jahren den Auftrag ertheilt, " \
           "mich für ein vollständiges Exemplar des Blattes umzusehen."
    assert pq.classify(body + "0" * 20).verdict == "repetitive"


def test_a_round_number_is_not_a_run():
    """"1000" is a run of three zeros, and every nineteenth-century letter has
    dates and sums in it."""
    body = ("Im Jahre 1800 wurden 10000 Gulden gezahlt, und 1000 weitere im "
            "Jahre 1808, wie der Rat es beschlossen hatte und wie es in den "
            "Büchern der Stadt eingetragen steht.")
    assert pq.classify(body).verdict == "ok"


def test_half_the_lines_repeating_is_caught():
    line = "und so weiter in derselben Weise fortgesetzt\n"
    assert pq.classify(line * 12).verdict == "repetitive"


def test_three_lines_two_the_same_is_not_a_loop():
    """Below six lines the ratio is noise: a two-line address panel where both
    lines happen to match is a short page, not a model out of control."""
    text = ("Herrn Joseph von Laßberg auf Eppishausen bei Bischofszell im "
            "Kanton Thurgau\nHerrn Joseph von Laßberg auf Eppishausen bei "
            "Bischofszell im Kanton Thurgau\nfranco Konstanz")
    assert pq.classify(text).verdict == "ok"


def test_the_longest_run_of_an_empty_text_is_zero():
    assert pq.longest_char_run("") == 0
    assert pq.repeat_ratio([]) == 0.0


# ── the verdict reaches the page's JSON and the run's report ──────────────────

def test_the_page_json_carries_the_verdict(tmp_path):
    """A corpus whose pages do not carry it can only be judged by reading all of
    it again."""
    import atr_batch as b

    class _Result:
        text = LOOPED
        lines: list = []
        confidence = 0.9
        timing_ms = 12
        truncated = False
        engine = "vllm"
        service_version = "0.1.0"
        second_opinion = None
        segmented_by = None

    image = tmp_path / "k.jpg"
    image.write_bytes(b"\xff\xd8\xff")
    payload = b._result_payload(
        b.PageRef(path=image, doc_id="d", key="k"),
        "qwen3.5-4b-german-xix-v2", "run", _Result(),
        source={"name": "k.tif", "sha256": "ab", "bytes": 3},
        read_path=image)
    assert payload["quality"]["verdict"] == "repetitive"
    assert payload["quality"]["longest_char_run"] == 3000


def test_the_report_counts_and_names_the_padded_pages():
    import atr_batch as b

    report = b.BatchReport(run="r", out_root=Path("/tmp/x"), pages=3)
    report.models.append(b.ModelOutcome(
        model="qwen3.5-4b-german-xix-v2", done=3, chars=9000, repetitive=1,
        repetitive_keys=["Briefe UB Freiburg__lassberg-letter-0083__002"]))
    text = b.format_report(report)
    assert "| looped |" in text
    assert "## Pages the model padded" in text
    assert "lassberg-letter-0083__002" in text
    assert "every average over them is wrong" in text


def test_a_run_without_padded_pages_gets_no_section():
    """An empty section reads as a finding that was looked for and not found,
    which is not the same as a column that is zero."""
    import atr_batch as b

    report = b.BatchReport(run="r", out_root=Path("/tmp/x"), pages=1)
    report.models.append(b.ModelOutcome(model="m", done=1, chars=1100))
    assert "Pages the model padded" not in b.format_report(report)
