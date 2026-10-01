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
                                "longest_char_run", "repeat_ratio",
                                "mean_line_chars"}


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


# ── the short-line loop: #483's own example, which read `short` ─────────────

#: The second loop, verbatim from the issue text. Not padding but filler,
#: produced when the segmenter finds lines where there are none and the model
#: obliges. It read `short` until `looks_repetitive` gained the short-line path:
#: a loop of three-character lines cannot reach `SHORT_CHARS` until some thirty
#: of them, so the whole failure mode fell through the character threshold.
FILLER = "die\nder\n1869\nder\nder\nder\n1865\nde\n1850"

#: What a page of it looks like when the model fills the whole sheet.
FILLER_PAGE = "\n".join(["der", "die", "der", "1869", "der", "der", "de", "der",
                         "1850", "der", "die", "der", "der", "1865", "der",
                         "de", "der", "der", "die", "der"])

#: A second full letter page at the other observed length (1450 characters).
#:
#: **This one is a reconstruction, not a reading.** The real page's text is not
#: in this repository; what is pinned here is its *shape* — a coherent letter of
#: that length with a high `der` density (35 of them), which is the property
#: that must not trip the measure. The 1043-character `LETTER` above is the real
#: reading.
LETTER_LONG = (
    "Eppishausen, den 14. Juni 1870.\n"
    "Hochwohlgeborener Herr, hochzuverehrender Freund!\n"
    "Der Brief, den Euer Hochwohlgeboren mir unter dem neunten dieses "
    "Monats zu senden die Gewogenheit hatten, ist mir erst in der "
    "zweiten Haelfte der vergangenen Woche zu Handen gekommen, weil der "
    "Bote, der die Sendung von der Post abzuholen hatte, durch die "
    "anhaltenden Regenguesse auf der Strasse nach Bischofszell "
    "aufgehalten worden war und der Wagen der Gesellschaft erst am "
    "Donnerstag der folgenden Woche hier eintraf.\n"
    "Was nun die Handschriften betrifft, nach denen Euer Hochwohlgeboren "
    "sich zu erkundigen geruhten, so befinden sie sich samt und sonders "
    "in der Bibliothek, und zwar in dem Schranke, der an der Nordseite "
    "des Saales aufgestellt ist. Der Band, der die Lieder der "
    "Minnesaenger enthaelt, ist derjenige, auf den der verstorbene Herr "
    "von Lassberg den groessten Wert gelegt hat; der Einband ist der "
    "urspruengliche, und der Zustand der Blaetter ist so gut, wie es "
    "nach der Laenge der Zeit nur zu erwarten steht.\n"
    "Was die Uebersendung angeht, so stehe ich der Sache nicht "
    "entgegen, sofern der Transport in der Weise geschieht, die ich mir "
    "vorbehalten habe, und sofern der Empfaenger der Kiste mir den "
    "Empfang derselben unverzueglich anzeigt. Der Schreiber bittet, "
    "dies der Billigkeit anheimzustellen.\n"
    "Mit der Versicherung meiner aufrichtigen Ergebenheit und in der "
    "Hoffnung auf eine baldige Nachricht zeichne ich als Euer "
    "Hochwohlgeboren ganz gehorsamster\n"
)


def test_the_filler_page_is_repetitive():
    """#483's named test. This is the case the module was missing: it caught the
    page padded with 8 000 zeros and read this one as `short`."""
    assert pq.classify(FILLER).verdict == "repetitive"


def test_a_whole_page_of_filler_is_repetitive():
    assert pq.classify(FILLER_PAGE).verdict == "repetitive"


def test_both_full_letter_pages_are_ok_and_not_repetitive():
    """The pair #483 names. `LETTER` is the real 1043-character reading;
    `LETTER_LONG` is a reconstruction at the other observed length — see its
    comment."""
    for page in (LETTER, LETTER_LONG):
        assert pq.classify(page).verdict == "ok"
        assert not pq.looks_repetitive(pq.classify(page))
    # `LETTER` is shortened in this file (see its own comment: 1043 characters
    # on the real page), so only the reconstruction's length is pinned.
    assert 1400 <= len(LETTER_LONG.strip()) <= 1500


def test_a_der_heavy_page_at_letter_length_is_still_ok():
    """`MANY_DER` above makes this point on a short page. At full page length it
    is the case the issue actually names: 35 `der` in 1450 characters, which is
    what any naive repetition check would die on."""
    assert LETTER_LONG.lower().count("der") >= 30
    assert pq.classify(LETTER_LONG).verdict == "ok"


# ── the loop test as its own function, with its negatives ───────────────────

def test_the_loop_test_is_separately_callable():
    """#483 asks for it: "Die Schleifenerkennung als eigene, testbare
    Funktion"."""
    assert pq.looks_repetitive(pq.classify(FILLER))
    assert not pq.looks_repetitive(pq.classify(LETTER))


def test_repetition_alone_is_not_enough_and_neither_is_shortness():
    """The two halves of the short-line path, each without the other.

    A word list is as short-lined as a loop and repeats nothing; a letter page
    whose segmenter duplicated two lines repeats and is not short-lined. Either
    signal alone would catch one of these, and both are pages somebody wrote."""
    word_list = "\n".join(["Acker", "Wiese", "Wald", "Garten", "Haus", "Hof",
                           "Scheune", "Stall"])
    duplicated = LETTER + "\nder\nder"

    short_lined = pq.classify(word_list)
    repeating = pq.classify(duplicated)

    assert short_lined.mean_line_chars <= pq.SHORT_LINE_CHARS
    assert short_lined.repeat_ratio == 0.0
    assert not pq.looks_repetitive(short_lined)

    assert repeating.repeat_ratio > 0.0
    assert repeating.mean_line_chars > pq.SHORT_LINE_CHARS
    assert not pq.looks_repetitive(repeating)


def test_the_margins_are_wide_on_the_observed_cases():
    """Calibration, pinned. A threshold chosen to sit between two numbers is
    worth only as much as the gap, so the gap is the assertion."""
    loop = pq.classify(FILLER_PAGE)
    letter = pq.classify(LETTER)

    assert loop.mean_line_chars < 5 < 40 < letter.mean_line_chars
    assert letter.repeat_ratio == 0.0
    assert loop.repeat_ratio >= 2 * pq.REPEAT_RATIO_SHORT_LINES


def test_under_six_lines_nothing_is_a_loop():
    """The argument that used to be carried by the character count, now carried
    by the line count: three words cannot support a claim about a model that
    ran out of control."""
    assert pq.classify("der\nder\nder").verdict == "short"
    assert not pq.looks_repetitive(pq.classify("der\nder\nder"))


def test_a_fragment_is_still_short_not_repetitive():
    """`53` and `der\\ns¬`, the two observed fragments. One line and two lines:
    neither reaches the line floor, so both stay what they are."""
    assert pq.classify("53").verdict == "short"
    assert pq.classify("der\ns¬").verdict == "short"


# ── the aggregate and the pages say the same thing ──────────────────────────

def _corpus(tmp_path, readings):
    """A run over one page per reading, so the report can be summed."""
    from types import SimpleNamespace
    from PIL import Image
    import atr_batch as batch

    root = tmp_path / "share"
    root.mkdir()
    for i, _ in enumerate(readings):
        Image.new("RGB", (40, 30), (i, i, i)).save(root / f"p{i:02d}.png")
    pages = batch.discover_pages(root)
    texts = dict(zip((p.key for p in pages), readings))

    def recognise(path, model):
        key = next(p.key for p in pages if str(path).endswith(p.path.name))
        return SimpleNamespace(text=texts[key], lines=[], confidence=0.5,
                               engine="kraken", timing_ms=5, truncated=False,
                               service_version="1", segmented_by=None,
                               second_opinion=None)

    outcome = batch.run_model(pages, "m", "r", tmp_path / "out", recognise,
                              retries=0, concurrency=1)
    return outcome, tmp_path / "out" / "m", pages


def test_the_aggregate_matches_the_sum_of_the_page_verdicts(tmp_path):
    """#483's last test. The report's numbers are the page verdicts counted, so
    the two cannot say different things about the same corpus."""
    import json

    readings = [LETTER, LETTER_LONG,          # ok, ok
                "   \n\n ", "",               # empty, empty
                "53", "der\ns¬",              # short, short
                FILLER, FILLER_PAGE]          # repetitive, repetitive
    outcome, out_dir, pages = _corpus(tmp_path, readings)

    on_disk: dict[str, int] = {}
    for written in sorted(out_dir.glob("*.json")):
        verdict = json.loads(written.read_text(encoding="utf-8"))["quality"]["verdict"]
        on_disk[verdict] = on_disk.get(verdict, 0) + 1

    assert outcome.verdicts == on_disk
    assert on_disk == {"ok": 2, "empty": 2, "short": 2, "repetitive": 2}
    assert sum(outcome.verdicts.values()) == outcome.done == len(pages)
    # The two named columns are the same counts, not a second opinion.
    assert (outcome.empty, outcome.repetitive) == (on_disk["empty"],
                                                   on_disk["repetitive"])


def test_a_whitespace_page_is_empty_in_the_report_too(tmp_path):
    """The divergence this fixes. `empty` in the report was `chars == 0`, and
    `chars` is the length of the *raw* text while the verdict strips first — so
    a page of nothing but whitespace read `empty` on disk and was not counted
    empty in the report. The two numbers the issue asks to agree were already
    disagreeing, on exactly the case its fourth test names."""
    import json

    outcome, out_dir, _ = _corpus(tmp_path, ["   \n\n \t "])

    page = json.loads(next(out_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert page["quality"]["verdict"] == "empty"
    assert outcome.empty == 1
    assert outcome.verdicts == {"empty": 1}


def test_a_rebuilt_report_counts_the_same_four(tmp_path):
    """A report reconstructed from disk describes what is on disk, so it
    reclassifies rather than reading a stored verdict — and must land on the
    same four numbers for pages that have one."""
    import atr_batch as batch

    readings = [LETTER, "   ", "53", FILLER_PAGE]
    outcome, _, _ = _corpus(tmp_path, readings)

    rebuilt = batch.report_from_outputs(tmp_path / "out")

    assert rebuilt.models[0].verdicts == outcome.verdicts
