"""
register.py — who wrote which letter, from the edition's own correspondence data.

**Why this ends an argument rather than joining it.** The hand a page is in has
been inferred all week from its dateline, because the hand "is not recorded
anywhere in the data". It is — in `michaelscho/lassberg`, the edition repository
this project already publishes its readings to (`GITHUB_TEXT_REPO`). Each letter
there is a TEI file whose `correspDesc` names the sender with a GND identifier:

    <correspAction type="sent">
      <persName key="…#lassberg-correspondent-0373"
                ref="https://d-nb.info/gnd/118778862">Joseph von Laßberg</persName>
      <placeName key="…#lassberg-place-0147">Eppishausen</placeName>
      <date when="1833-08-13">1833-08-13</date>
    </correspAction>

That is a record, not a guess, and where it reaches it replaces the inference.
Measured on 2026-10-10: 279 letters carry a sender, 128 of them Laßberg himself,
across four correspondences (Pupikofer 84, Uhland 43, Wackernagel 24).

**It settled every open case immediately.** The three letters whose dated pages
disagreed — `lassberg-letter-1737`, `-1787`, `-2358` — are all his, and two of
them even name Eppishausen. `-1280`, the letter whose third line reads "nach
Eppishausen befördert" and which was the argument against widening the dateline
window, is Pupikofer's: the argument was right. `-1209`, which the rule could not
read at all, is Wackernagel writing from Berlin.

**What it does not reach.** 279 letter ids out of the roughly 3226 the corpus
uses, and four correspondents out of the whole correspondence. So this is the
primary source and the dateline rule is the fallback, not the reverse — and where
the two disagree the disagreement is reported rather than silently resolved,
because then one of them is wrong about a specific letter and that is worth
seeing.

**Identity by GND, never by spelling.** "Laßberg" is also written "Lassberg" and
"Laspberg", and a name string is a label while `118778862` is an identifier. A
register entry with no GND falls back to the name, and says so.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

TEI = {"t": "http://www.tei-c.org/ns/1.0"}
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

#: Joseph von Laßberg in the GND. The one identifier that decides whose hand a
#: letter is in; see the module docstring on why not the name.
LASSBERG_GND = "118778862"

#: Names accepted when an entry carries no GND at all. Spelling variants of one
#: man, and the reason this list is a fallback rather than the rule.
LASSBERG_NAMES = ("laßberg", "lassberg", "laspberg")


@dataclass(frozen=True)
class Letter:
    """One letter's correspondence record: who sent it, from where, when."""

    id: str
    sender: str = ""
    sender_key: str = ""
    sender_gnd: str = ""
    place: str = ""
    date: str = ""

    @property
    def by_lassberg(self) -> bool:
        if self.sender_gnd:
            return self.sender_gnd == LASSBERG_GND
        low = self.sender.lower()
        return any(n in low for n in LASSBERG_NAMES)

    @property
    def project(self) -> str:
        """The dataset project this letter's pages belong in."""
        return "lassberg" if self.by_lassberg else "korrespondenten"

    @property
    def evidence(self) -> str:
        who = self.sender or "(unnamed sender)"
        where = f", {self.place}" if self.place else ""
        when = f", {self.date}" if self.date else ""
        how = f" [GND {self.sender_gnd}]" if self.sender_gnd else " [by name]"
        return f"register: {who}{where}{when}{how}"


@dataclass
class Register:
    """Every letter the edition records a sender for, and where it came from."""

    letters: dict
    source: Path
    revision: str = ""          # the checkout's commit, when it could be read
    without_sender: tuple = ()  # letter files with no correspAction type="sent"

    def __contains__(self, letter_id: str) -> bool:
        return letter_id in self.letters

    def get(self, letter_id: str) -> Optional[Letter]:
        return self.letters.get(letter_id)

    @property
    def by_sender(self) -> dict:
        out: dict = {}
        for letter in self.letters.values():
            out[letter.sender] = out.get(letter.sender, 0) + 1
        return out

    @property
    def lassberg_letters(self) -> list:
        return sorted(l.id for l in self.letters.values() if l.by_lassberg)


def _text(element) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def read_letter(path: Path) -> Optional[Letter]:
    """One TEI letter file's correspondence record, or None when it has none.

    None rather than an exception: a directory of somebody else's data will
    contain a stylesheet, a template and work in progress, and one unparsable
    file must cost that file rather than the register — the same rule the ground
    truth follows since one empty page ended a whole scoring run (#542).
    """
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        logger.warning(f"[register] {path.name}: {exc}")
        return None
    letter_id = root.get(XML_ID) or path.stem
    sent = root.find(".//t:correspDesc/t:correspAction[@type='sent']", TEI)
    if sent is None:
        return Letter(id=letter_id)
    person = sent.find("t:persName", TEI)
    date = sent.find("t:date", TEI)
    gnd = ""
    key = ""
    if person is not None:
        gnd = (person.get("ref") or "").rstrip("/").rsplit("/", 1)[-1]
        key = (person.get("key") or "").split("#")[-1]
    when = ""
    if date is not None:
        when = date.get("when") or _text(date)
    return Letter(id=letter_id, sender=_text(person), sender_key=key,
                  sender_gnd=gnd, place=_text(sent.find("t:placeName", TEI)),
                  date=when)


def _revision(folder: Path) -> str:
    """The checkout's commit, read from .git without shelling out. "" when there
    is none — a dataset card should be able to name which version of somebody
    else's register assigned its labels, and a missing answer is better than a
    wrong one."""
    for parent in [folder, *folder.parents]:
        head = parent / ".git" / "HEAD"
        if not head.is_file():
            continue
        try:
            text = head.read_text(encoding="utf-8").strip()
            if text.startswith("ref:"):
                ref = (parent / ".git" / text.split(" ", 1)[1].strip())
                return ref.read_text(encoding="utf-8").strip()[:12] if ref.is_file() else ""
            return text[:12]
        except OSError:
            return ""
    return ""


def read_register(folder: Path) -> Register:
    """Every ``lassberg-letter-*.xml`` under ``folder``, by letter id.

    ``folder`` is the edition's ``data/letters`` — a checkout of
    `GITHUB_TEXT_REPO`, which this project already publishes readings to:

        git clone --depth 1 https://github.com/michaelscho/lassberg
    """
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise NotADirectoryError(
            f"no letter register at {folder} — it is the edition's data/letters, "
            f"from `git clone --depth 1 https://github.com/michaelscho/lassberg`")
    letters, mute = {}, []
    for path in sorted(folder.glob("lassberg-letter-*.xml")):
        letter = read_letter(path)
        if letter is None:
            continue
        if letter.sender or letter.sender_gnd:
            letters[letter.id] = letter
        else:
            mute.append(letter.id)
    if not letters:
        raise ValueError(f"{folder} holds no letter with a recorded sender")
    return Register(letters=letters, source=folder, revision=_revision(folder),
                    without_sender=tuple(mute))


def format_register(reg: Register) -> str:
    """What the register covers, before anything is decided with it."""
    lines = [f"register : {reg.source}"
             + (f"  @{reg.revision}" if reg.revision else ""),
             f"letters  : {len(reg.letters)}",
             f"Laßberg  : {len(reg.lassberg_letters)}  (GND {LASSBERG_GND})", "",
             "senders:"]
    for sender, n in sorted(reg.by_sender.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {n:>4}  {sender or '(unnamed)'}")
    if reg.without_sender:
        lines += ["", f"{len(reg.without_sender)} letter file(s) record no sender "
                      f"and decide nothing: "
                      + ", ".join(reg.without_sender[:5])
                      + (" …" if len(reg.without_sender) > 5 else "")]
    lines += ["", "This covers 279 of the roughly 3226 letter ids the corpus "
                  "uses, so it is the primary source and the dateline rule is "
                  "the fallback — not a replacement for it."]
    return "\n".join(lines)
