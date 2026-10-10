"""The letter register and the selection it makes possible, over MCP.

"Every page Laßberg wrote that nobody has transcribed yet" cannot be answered
from the pages: the hand is read off a transcription and these have none. The
edition's register records a sender per *letter*, and the letter id is in the
page key, so the answer is a join — `letter_register` writes the letter ids and
`start_batch`'s `letters_from` reads them back.

Both ends keep this path's one rule: **a caller names a file under the
ground-truth root, never a path.** The server sits behind a bearer token on the
public internet, and that validation is what stands between the token and a box
with two A40s. A list that could be a path would turn a tool that chooses pages
into one that chooses files.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from mcp_atr import jobs                   # noqa: E402

GND_LASSBERG = "118778862"
GND_UHLAND = "118625063"
NS = 'xmlns="http://www.tei-c.org/ns/1.0"'


def _letter(folder: Path, lid: str, sender: str, gnd: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{lid}.xml").write_text(
        f'<TEI {NS} xml:id="{lid}"><teiHeader><profileDesc><correspDesc>'
        f'<correspAction type="sent">'
        f'<persName ref="https://d-nb.info/gnd/{gnd}">{sender}</persName>'
        f'</correspAction></correspDesc></profileDesc></teiHeader></TEI>',
        encoding="utf-8")


@pytest.fixture
def register(tmp_path, monkeypatch):
    folder = tmp_path / "letters"
    _letter(folder, "lassberg-letter-1737", "Joseph von Laßberg", GND_LASSBERG)
    _letter(folder, "lassberg-letter-2358", "Joseph von Laßberg", GND_LASSBERG)
    _letter(folder, "lassberg-letter-0626", "Ludwig Uhland", GND_UHLAND)
    monkeypatch.setattr(jobs.config, "LASSBERG_REGISTER", str(folder))
    monkeypatch.setattr(jobs.config, "GT_ROOT", tmp_path / "gt")
    (tmp_path / "gt").mkdir()
    return folder


# ── the register over MCP ────────────────────────────────────────────────────

def test_it_answers_without_a_job_id(register):
    """280 small local files is a second, not the quarter-hour a scoring run
    spends matching. A job id would be ceremony around the answer."""
    out = jobs.letter_register_job()

    assert out["done"] is True and "job_id" not in out
    assert out["letters"] == 3 and out["by_lassberg"] == 2


def test_lassbergs_letters_are_the_default_selection(register):
    out = jobs.letter_register_job()

    assert out["selected"] == 2
    assert GND_LASSBERG in out["selection"]


def test_a_sender_can_be_chosen_by_gnd(register):
    out = jobs.letter_register_job(sender_gnd=GND_UHLAND)

    assert out["selected"] == 1 and out["selection"] == f"GND {GND_UHLAND}"


def test_every_sender_when_neither_is_asked_for(register):
    assert jobs.letter_register_job(lassberg=False)["selected"] == 3


def test_the_senders_come_back_as_data(register):
    assert jobs.letter_register_job()["senders"] == {
        "Joseph von Laßberg": 2, "Ludwig Uhland": 1}


def test_an_unset_register_names_the_clone_command(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs.config, "LASSBERG_REGISTER", "")

    with pytest.raises(jobs.JobError) as exc:
        jobs.letter_register_job()

    assert "github.com/michaelscho/lassberg" in str(exc.value)


# ── the handover ─────────────────────────────────────────────────────────────

def test_the_file_it_writes_is_the_file_start_batch_reads(register):
    """The two ends under one root, which is the whole point of naming rather
    than pathing."""
    out = jobs.letter_register_job(out="lassberg-briefe.txt")

    assert out["letters_file"] == "lassberg-briefe.txt"
    path = jobs.resolve_letters_file("lassberg-briefe.txt")
    ids = [l for l in path.read_text(encoding="utf-8").splitlines()
           if l and not l.startswith("#")]
    assert ids == ["lassberg-letter-1737", "lassberg-letter-2358"]


def test_the_written_file_names_its_source_and_revision(register):
    """A dataset card has to be able to say which version of somebody else's
    data assigned its labels."""
    jobs.letter_register_job(out="briefe.txt")

    head = jobs.resolve_letters_file("briefe.txt").read_text(encoding="utf-8")
    assert "letters" in head.splitlines()[1]        # the register's directory
    assert "UTC" in head.splitlines()[1]


@pytest.mark.parametrize("name", ["../../etc/passwd", "/etc/passwd",
                                  "sub/dir/keys.txt"])
def test_a_letter_list_cannot_be_a_path(register, name):
    with pytest.raises(jobs.JobError):
        jobs.resolve_letters_file(name)


def test_a_missing_letter_list_names_the_tool_that_writes_one(register):
    with pytest.raises(jobs.JobError) as exc:
        jobs.resolve_letters_file("nope.txt")

    assert "letter_register" in str(exc.value)


def test_a_missing_key_list_still_names_its_own_tool(register):
    """The resolver was generalised, so the two errors must not have merged."""
    with pytest.raises(jobs.JobError) as exc:
        jobs.resolve_keys_file("nope.txt")

    assert "score_ground_truth" in str(exc.value)


# ── the argv ─────────────────────────────────────────────────────────────────

def test_the_selection_reaches_the_documented_cli():
    argv = jobs.batch_argv(Path("/corpus"), ["m"], "run",
                           letters_from=Path("/gt/briefe.txt"),
                           exclude_from=[Path("/gt/a.txt"), Path("/gt/b.txt")],
                           keys_out=Path("/gt/out.txt"), limit=300,
                           dry_run=True)

    assert argv[argv.index("--letters-from") + 1] == "/gt/briefe.txt"
    assert argv.count("--exclude-from") == 2
    assert argv[argv.index("--keys-out") + 1] == "/gt/out.txt"
    assert "--limit" in argv and "--dry-run" in argv


def test_nothing_is_passed_when_nothing_is_asked_for():
    argv = jobs.batch_argv(Path("/corpus"), ["m"], "run")

    for flag in ("--letters-from", "--exclude-from", "--keys-out"):
        assert flag not in argv


# ── a metered reader may not be pointed at everything ────────────────────────
#
# The gateway's cost is a card that is already paid for and idle; an external
# API's is per page and lands on an invoice. A caller who forgets a selection
# would get 6466 untranscribed pages, and the only thing between a typo and
# that bill should not be the caller's memory.

def test_the_external_reader_reaches_the_cli():
    argv = jobs.batch_argv(Path("/corpus"), ["gemini-3.8-flash"], "run",
                           via="external", prompt="lassberg_atr.md",
                           structured=True)

    assert argv[argv.index("--via") + 1] == "external"
    assert argv[argv.index("--prompt") + 1] == "lassberg_atr.md"
    assert "--structured" in argv


def test_the_gateway_is_not_named_on_the_command_line():
    """It is the default, and a flag that says what would happen anyway is a
    flag that drifts from the default without anybody noticing."""
    argv = jobs.batch_argv(Path("/corpus"), ["m"], "run")

    assert "--via" not in argv and "--prompt" not in argv


def test_the_guard_names_what_would_be_read():
    assert "6466" in jobs.EXTERNAL_NEEDS_A_SELECTION
    assert "dry_run" in jobs.EXTERNAL_NEEDS_A_SELECTION
