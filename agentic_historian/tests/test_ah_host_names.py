"""130.92.59.240 is idhefix, 130.92.59.242 is asteraix (#436, serving#136).

For months both repositories called the serving box asterAIx. It is idhefix;
asteraix is a different machine and since 16.09.2026 it is the training box. The
count in serving-atr-inference#136 found 176 places and one fact that made the
fix mechanical: **not one of them meant the other machine**.

The serving repo's share was swept there; this file keeps ours swept. A name
that is wrong everywhere it appears cannot be fixed once — the next comment
written from memory brings it back, and here it reached a Discord command
description that every user of the bot could read (#478).

What the check cannot do is tell whether a *sentence* is still true. Two of them
were not, and were rewritten rather than renamed: `TrainingClient`'s "both
services live on the same box", and the topology in
`docs/TRAINING_INTEGRATION_PLAN.md`.

## Why the first version of this check was not enough

It looked for one exact capitalisation, and two places survived it with
``asteraiX`` — the example gateway URL in `test_ah_361_training_client.py`,
which pointed the reader at the training box for a setting that names the
gateway. #457, the first attempt at #436, had already shown the same hole from
the other side: it introduced ``astraix``, which is not a machine at all.

So the rule is on the spelling, not on a list of misspellings: the name is
written ``asteraix`` or, as a Python constant, ``ASTERAIX``. Anything in
between — ``asterAIx``, ``asteraiX`` — is somebody typing it from memory, and
``astraix`` in any case is nothing. The same holds for ``idhefix``.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BOT = REPO / "agentic_historian" / "bot.py"

#: Built from pieces, so this file is not its own counter-example — and it
#: excludes itself from the scan besides, because the prose above has to be
#: able to name the mistakes it is about.
NAMES = ("ast" + "eraix", "idh" + "efix")
#: ``astraix`` included deliberately: it is not a machine, and #457 wrote it.
SPELLING = re.compile("|".join(["ast" + "e?raix", "idh" + "efix"]), re.IGNORECASE)

SKIP_DIRS = {".git", ".venv", ".venvs", "__pycache__", ".pytest_cache", ".ruff_cache",
             ".claude", "node_modules", "data"}
SUFFIXES = {".md", ".py", ".sh", ".txt", ".yaml", ".yml", ".toml", ".conf", ".service",
            ".json", ".example"}

#: Which machine each address is. The pairing is the part that cannot be
#: checked by spelling: `docs/CLAUDE_CODE_CONNECTIVITY.md` once read
#: "asterAIx (130.92.59.240)", where both halves were spelled fine.
HOSTS = {"130.92.59.240": NAMES[1], "130.92.59.242": NAMES[0]}
NEAR = 60


def _text_files() -> list[Path]:
    return [p for p in sorted(REPO.rglob("*"))
            if p.is_file() and not (set(p.relative_to(REPO).parts) & SKIP_DIRS)
            and p.suffix in SUFFIXES]


def _scanned() -> list[Path]:
    here = Path(__file__).resolve()
    return [p for p in _text_files() if p.resolve() != here]


def misspellings(text: str) -> list[str]:
    """Every way the text writes a machine name that is not a way to write it.

    >>> misspellings("asteraix and idhefix and ASTERAIX")
    []
    >>> misspellings("asterAIx, asteraiX and astraix")
    ['asterAIx', 'asteraiX', 'astraix']
    """
    found = {m.group(0) for m in SPELLING.finditer(text)}
    return sorted(s for s in found if s not in NAMES and s != s.upper())


# ── the scan is looking at something ────────────────────────────────────────
def test_the_scan_is_not_vacuous():
    files = _scanned()
    assert any(p.name == "bot.py" for p in files), "the scan found no source"
    assert len(files) > 50


def test_the_check_catches_the_spellings_it_is_for():
    """#457's mistake and the two this file was written for, as a unit test of
    the rule itself: a guard nobody has seen fail is a guard nobody has seen."""
    assert misspellings("ast" + "raix") == ["ast" + "raix"]
    assert misspellings("ast" + "erAIx") == ["ast" + "erAIx"]
    assert misspellings("ast" + "eraiX") == ["ast" + "eraiX"]
    assert misspellings("Idh" + "efix") == ["Idh" + "efix"]


def test_a_correct_spelling_is_not_an_offence():
    """``ASTERAIX`` is a constant in test_ah_439_both_machines.py, not somebody
    getting the name wrong."""
    assert misspellings("asteraix trains, idhefix serves; ASTERAIX = {...}") == []


# ── the repository ──────────────────────────────────────────────────────────
def test_no_file_spells_a_machine_name_from_memory():
    offenders = {str(p.relative_to(REPO)): found for p in _scanned()
                 if (found := misspellings(p.read_text(encoding="utf-8",
                                                       errors="replace")))}

    assert not offenders, (
        "130.92.59.240 is idhefix and 130.92.59.242 is asteraix (#436); these "
        "spell one of them some other way:\n  "
        + "\n  ".join(f"{path}: {', '.join(found)}" for path, found in offenders.items()))


def test_every_address_is_paired_with_its_own_machine():
    """The mistake spelling cannot catch. Both halves of
    "asterAIx (130.92.59.240)" were spelled fine; the sentence was still wrong,
    and it sent a reader to the other box."""
    wrong = []
    for path in _scanned():
        text = path.read_text(encoding="utf-8", errors="replace")
        for address, belongs_to in HOSTS.items():
            other = next(n for n in NAMES if n != belongs_to)
            for match in re.finditer(re.escape(address), text):
                window = text[max(0, match.start() - NEAR):match.end() + NEAR]
                if other in window.lower() and belongs_to not in window.lower():
                    wrong.append(f"{path.relative_to(REPO)}: {address} next to {other}")

    assert not wrong, "an address beside the wrong machine:\n  " + "\n  ".join(wrong)


# ── what a user of the bot reads ────────────────────────────────────────────
def test_no_slash_command_description_names_a_machine_wrongly():
    """These are the strings Discord shows in the command picker, so a name
    typed from memory here is read by everyone who types `/atr_`. That is what
    #478 was, and why #436 is its own issue rather than a tidy-up."""
    descriptions = re.findall(r'description="([^"]*)"', BOT.read_text(encoding="utf-8"))
    assert len(descriptions) > 10, "no slash-command descriptions found in bot.py"

    offenders = {d: misspellings(d) for d in descriptions if misspellings(d)}

    assert not offenders, offenders


def test_the_jobs_command_names_the_training_box():
    """Not only spelled right: `/atr_jobs` reads the trainer, and since the
    split the trainer is asteraix. The gateway it goes through is idhefix, and
    saying so here would name the proxy rather than the machine with the jobs."""
    source = BOT.read_text(encoding="utf-8")
    line = next(ln for ln in source.splitlines() if 'name="atr_jobs"' in ln)

    assert NAMES[0] in line, line


# ── the rename changed no behaviour ─────────────────────────────────────────
def test_the_gateway_url_still_points_at_the_serving_box():
    """#436's second test, and the seam it checks (serving#137): the bot reaches
    the trainer through `/train/*` on the gateway, because only :8200 is open to
    this host. A bot that needed reconfiguring after the cutover would be the
    proof that the seam did not hold — so this is a default, not a new setting.
    """
    import config

    assert "8200" in config.ATR_GATEWAY_URL or config.ATR_GATEWAY_URL.startswith("http")
    assert misspellings(config.ATR_GATEWAY_URL) == []
    assert not hasattr(config, "ATR_TRAIN_URL"), (
        "the bot must not learn the trainer's address: it goes through the "
        "gateway, which is the only port open from this host")
