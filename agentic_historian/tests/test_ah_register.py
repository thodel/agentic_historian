"""Who wrote which letter, from the edition's own data (`register`).

The hand a page is in was inferred all week from its dateline, because it "is
not recorded anywhere in the data". It is — in `michaelscho/lassberg`, the
edition repository this project already publishes its readings to. Each letter
is a TEI file whose `correspDesc` names the sender with a GND.

Measured against the real register on 2026-10-10: 279 letters with a sender, 128
of them Laßberg's, four correspondences. It settled every case the dateline rule
had left open — `lassberg-letter-1737`, `-1787` and `-2358`, whose dated pages
disagreed, are all his; `-1280`, the letter whose third line reads "nach
Eppishausen befördert" and which was the argument against widening the dateline
window, is Pupikofer's.

It covers 279 of some 3226 letter ids, so it is the primary source and the
dateline rule is the fallback. Where the two disagree, the disagreement is
reported rather than resolved in silence.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import register as reg                        # noqa: E402

NS = 'xmlns="http://www.tei-c.org/ns/1.0"'


def _letter(folder: Path, lid: str, *, sender="Joseph von Laßberg",
            gnd=reg.LASSBERG_GND, place="Eppishausen", date="1833-08-13",
            sent=True) -> Path:
    ref = f' ref="https://d-nb.info/gnd/{gnd}"' if gnd else ""
    action = (f'<correspAction type="sent">'
              f'<persName key="../register/p.xml#c-0373"{ref}>{sender}</persName>'
              f'<placeName key="../register/l.xml#p-1">{place}</placeName>'
              f'<date when="{date}">{date}</date>'
              f'</correspAction>') if sent else ""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{lid}.xml"
    path.write_text(
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<TEI {NS} xml:id="{lid}"><teiHeader><profileDesc>'
        f'<correspDesc xml:id="cd-{lid}">{action}'
        f'<correspAction type="received"><persName>X</persName></correspAction>'
        f'</correspDesc></profileDesc></teiHeader>'
        f'<text><body><p>Text</p></body></text></TEI>', encoding="utf-8")
    return path


# ── reading a letter ─────────────────────────────────────────────────────────

def test_the_sender_place_and_date_are_read(tmp_path):
    letter = reg.read_letter(_letter(tmp_path, "lassberg-letter-1737"))

    assert letter.id == "lassberg-letter-1737"
    assert letter.sender == "Joseph von Laßberg"
    assert letter.sender_gnd == reg.LASSBERG_GND
    assert letter.place == "Eppishausen" and letter.date == "1833-08-13"
    assert letter.project == "lassberg"


def test_a_correspondent_is_not_lassberg(tmp_path):
    letter = reg.read_letter(_letter(tmp_path, "lassberg-letter-1280",
                                     sender="Johann Adam Pupikofer",
                                     gnd="11565108X", place=""))

    assert letter.project == "korrespondenten"
    assert not letter.by_lassberg


def test_identity_is_the_gnd_and_not_the_name(tmp_path):
    """A name is a label. Somebody else called Laßberg, or an edition that
    changed its spelling, must not move a letter between projects."""
    letter = reg.read_letter(_letter(tmp_path, "lassberg-letter-0001",
                                     sender="Joseph von Laßberg",
                                     gnd="11565108X"))

    assert letter.project == "korrespondenten"
    assert "GND 11565108X" in letter.evidence


@pytest.mark.parametrize("spelling", ["Joseph von Laßberg", "J. von Lassberg",
                                      "Laspberg, Joseph von"])
def test_without_a_gnd_the_name_is_the_fallback_and_says_so(tmp_path, spelling):
    letter = reg.read_letter(_letter(tmp_path, "lassberg-letter-0002",
                                     sender=spelling, gnd=""))

    assert letter.by_lassberg
    assert "[by name]" in letter.evidence


def test_a_letter_with_no_sent_action_decides_nothing(tmp_path):
    letter = reg.read_letter(_letter(tmp_path, "lassberg-letter-0003", sent=False))

    assert letter.sender == "" and letter.sender_gnd == ""


def test_an_unparsable_file_costs_that_file_not_the_register(tmp_path):
    """The rule the ground truth follows since one empty page ended a whole
    scoring run (#542)."""
    _letter(tmp_path, "lassberg-letter-0004")
    (tmp_path / "lassberg-letter-bad.xml").write_text("<TEI>unclosed",
                                                      encoding="utf-8")

    book = reg.read_register(tmp_path)

    assert list(book.letters) == ["lassberg-letter-0004"]


# ── the register ─────────────────────────────────────────────────────────────

def test_only_letters_with_a_sender_decide_anything(tmp_path):
    _letter(tmp_path, "lassberg-letter-0005")
    _letter(tmp_path, "lassberg-letter-0006", sent=False)

    book = reg.read_register(tmp_path)

    assert list(book.letters) == ["lassberg-letter-0005"]
    assert book.without_sender == ("lassberg-letter-0006",)


def test_the_stylesheet_beside_the_letters_is_ignored(tmp_path):
    """Somebody else's directory holds a stylesheet, a template and work in
    progress; only `lassberg-letter-*.xml` is read."""
    _letter(tmp_path, "lassberg-letter-0007")
    (tmp_path / "Unbenannt1.xsl").write_text("<xsl:stylesheet/>", encoding="utf-8")
    (tmp_path / "letter_template.xml").write_text("<TEI/>", encoding="utf-8")

    assert list(reg.read_register(tmp_path).letters) == ["lassberg-letter-0007"]


def test_the_senders_are_counted_and_lassbergs_letters_listed(tmp_path):
    _letter(tmp_path, "lassberg-letter-0010")
    _letter(tmp_path, "lassberg-letter-0011")
    _letter(tmp_path, "lassberg-letter-0012", sender="Ludwig Uhland",
            gnd="118625063")

    book = reg.read_register(tmp_path)

    assert book.by_sender == {"Joseph von Laßberg": 2, "Ludwig Uhland": 1}
    assert book.lassberg_letters == ["lassberg-letter-0010",
                                     "lassberg-letter-0011"]
    assert "lassberg-letter-0010" in book


def test_a_directory_that_is_not_there_names_the_clone_command(tmp_path):
    with pytest.raises(NotADirectoryError) as exc:
        reg.read_register(tmp_path / "nope")

    assert "github.com/michaelscho/lassberg" in str(exc.value)


def test_a_directory_with_no_senders_is_an_error_not_an_empty_register(tmp_path):
    _letter(tmp_path, "lassberg-letter-0020", sent=False)

    with pytest.raises(ValueError):
        reg.read_register(tmp_path)


def test_the_revision_is_read_so_a_label_can_be_traced(tmp_path):
    """A dataset card should be able to name which version of somebody else's
    register assigned its labels."""
    _letter(tmp_path / "data" / "letters", "lassberg-letter-0030")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "refs" / "heads" / "main").write_text("0e88d55736f3abc\n",
                                                 encoding="utf-8")

    assert reg.read_register(tmp_path / "data" / "letters").revision == "0e88d55736f3"


def test_no_checkout_means_no_revision_rather_than_a_wrong_one(tmp_path):
    _letter(tmp_path, "lassberg-letter-0031")

    assert reg.read_register(tmp_path).revision == ""


def test_the_coverage_is_printed_before_anything_is_decided_with_it(tmp_path):
    _letter(tmp_path, "lassberg-letter-0040")
    _letter(tmp_path, "lassberg-letter-0041", sender="Ludwig Uhland",
            gnd="118625063")

    text = reg.format_register(reg.read_register(tmp_path))

    assert "letters  : 2" in text and "Laßberg  : 1" in text
    assert "Ludwig Uhland" in text
    assert "the fallback" in text
