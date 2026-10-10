"""Which way the hot folder is read — a share token, or somebody's account.

(Named ``ingest_mailbox`` and not ``mailbox``: this package has no
``__init__.py`` and its directory goes on ``sys.path``, so a module called
``mailbox`` would shadow the standard library's for every import in the process,
this project's dependencies included.)

The ingest authenticated as a **user**: ``SWITCHDRIVE_USER`` plus an app passcode
against that account's own root. So the corpus entrance hung on a personal
edu-ID account, and on 09./10.10.2026 that cost two days — a valid App Passcode
stopped being accepted and **why is still unknown** (``LESSONS_2026-10.md`` §9:
three mechanisms proposed, two refuted).

Worse than the incident is its shape. SWITCH documents one passcode *per device*,
and deleting a passcode closes that connection immediately — so a passcode shared
between tei, a laptop and a backup dies the moment any one of them is tidied up,
and the pipeline dies with it. Nothing in that chain is about the corpus.

A public share has none of those properties (#592):

====================  ===================================  ======================
                      account (as it was)                  share token
====================  ===================================  ======================
hangs on an edu-ID    yes                                  no
dies with a passcode  yes                                  no
revoking              delete the passcode (all devices)    delete the share
rotating              new passcode, everywhere             new share, one value
giver needs account   yes                                  no — they get a link
====================  ===================================  ======================

**Both ways stay.** A switch that keeps the old way is a switch with a return
ticket, and the account path works for as long as it works. This module is the
one place that decides which is in use, so the decision cannot be made
differently in `/pull`, `/pull_folder`, the watcher and the preflight — and so
the answer can **say which way it took**. Without that the next failure is the
puzzle #563 was filed for: a message about a credential nobody can tell is the
credential in use.

The two clients themselves stay separate (``utils/switchdrive.py``,
``utils/nextcloud.py``): they overlap in the protocol and in nothing else, and
folding them together would mean a client that takes credentials it does not use
half the time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

import config

__all__ = ["SHARE", "ACCOUNT", "NONE", "Mailbox", "which", "answer", "describe",
           "pull_folder", "list_subdirs", "preflight", "explain",
           "load_processed", "mark_processed"]

SHARE = "share"      #: a public share, authenticated with its token
ACCOUNT = "account"  #: an edu-ID account, authenticated with an app passcode
NONE = "none"        #: neither is configured


@dataclass(frozen=True)
class Mailbox:
    """Which way is in use, and enough about it to put in an answer.

    No credential of any kind on this object — not even the share token, which
    *is* the share's credential. ``detail`` is for a Discord message, so it must
    be safe to read out loud.
    """

    way: str
    #: Short name for a message: "Freigabe" / "Konto".
    label: str
    #: Which mailbox, in one clause — and **only** that. Never the credential
    #: mechanism: this line goes onto every answer, including the error from a
    #: full disk, and #563's test caught the first version of it saying
    #: "(App-Passwort)" there. Naming a remedy on an unrelated failure is the
    #: invented advice that test exists to prevent; remedies belong in
    #: ``_advise`` and ``explain``, where a status code has been measured.
    detail: str = ""

    @property
    def configured(self) -> bool:
        return self.way != NONE

    def line(self) -> str:
        """The one line an answer carries so the way taken is never a guess."""
        if self.way == NONE:
            return ("❌ Kein Briefkasten konfiguriert — entweder "
                    "`SWITCHDRIVE_SHARE_URL` (Freigabe, empfohlen) oder "
                    "`SWITCHDRIVE_USER`/`SWITCHDRIVE_PASS` (Konto) in "
                    "`.env.gpustack` setzen.")
        return f"_Weg: **{self.label}** — {self.detail}._"


def which() -> Mailbox:
    """The way in use. The share wins when configured.

    The share first because it is the one without the failure mode: if both are
    set, preferring the account would mean the migration never actually takes
    effect while looking as though it had.
    """
    from utils import nextcloud

    if (config.SWITCHDRIVE_SHARE_URL or "").strip():
        try:
            share = nextcloud.hotfolder_share()
        except nextcloud.NextcloudError as exc:
            # Set but unparsable. Not silently falling back to the account: a
            # misspelled share URL would then read as "the share works", and the
            # next failure would be about a credential nobody meant to use.
            return Mailbox(way=NONE, label="Freigabe",
                           detail=f"`SWITCHDRIVE_SHARE_URL` ist gesetzt, aber "
                                  f"nicht lesbar: {exc}")
        return Mailbox(
            way=SHARE, label="Freigabe",
            detail=(f"Token {nextcloud.masked_token(share.token)} auf "
                    f"`{share.base_url}`"
                    + (", mit Passwort" if share.password
                       else ", ohne Passwort")))

    from utils import switchdrive
    if switchdrive.is_configured():
        return Mailbox(way=ACCOUNT, label="Konto",
                       detail=f"`{config.SWITCHDRIVE_USER}` auf "
                              f"`{config.SWITCHDRIVE_URL}`")
    return Mailbox(way=NONE, label="—")


def answer(box: Mailbox, text: str) -> str:
    """One answer from a pull command, with the way it took.

    Every message these commands send goes through here — including the
    intermediate "processing…" one, where the footer is redundant. That is the
    price of a rule a guard can hold: a test can assert that no answer bypasses
    this, and it cannot assert "the last message of every path", which is what a
    per-branch footer would need. A mutation that stripped the footer from the
    one line people actually read passed a guard that only looked for it
    somewhere in the function.
    """
    return f"{text}\n{box.line()}"


def describe() -> str:
    """:meth:`Mailbox.line` for the way in use. For an answer's footer."""
    return which().line()


def _module(box: Optional[Mailbox] = None):
    """The client for the way in use, or raise with what to configure."""
    box = box or which()
    if box.way == SHARE:
        from utils import nextcloud
        return nextcloud
    if box.way == ACCOUNT:
        from utils import switchdrive
        return switchdrive
    raise RuntimeError(box.line())


def pull_folder(remote_dir: Optional[str] = None,
                local_dir: Optional[Path] = None,
                recursive: bool = False) -> list[Path]:
    """Download one folder, by whichever way is configured.

    ``recursive`` defaults to False as ``switchdrive.pull_folder`` does, not to
    the share client's True: the callers are the same callers, and a default
    that changed with the mailbox would make `/pull` descend or not depending on
    a setting nobody connected to it.
    """
    box = which()
    logger.info(f"[mailbox] pull via {box.way}: {remote_dir}")
    return _module(box).pull_folder(remote_dir, local_dir, recursive)


def list_subdirs(remote_dir: Optional[str] = None) -> list[str]:
    """The orders under ``remote_dir``. Both ways return ``[remote_dir]`` when
    it has no subfolders, so a flat folder is one order either way."""
    return _module().list_subdirs(remote_dir)


def preflight(remote_dir: Optional[str] = None, **kw):
    """The four layers, for the way in use (#563 for the account, #592 here).

    An unconfigured mailbox still returns a probe rather than raising, and it
    fails at ``config`` — which is true, and which keeps the command that calls
    this one command rather than one command and one special case.
    """
    box = which()
    if box.way == SHARE:
        from utils import nextcloud
        return nextcloud.preflight(remote_dir, **kw)
    if box.way == ACCOUNT:
        from utils import switchdrive
        return switchdrive.preflight(remote_dir, **kw)

    import webdav_probe
    return webdav_probe.probe(
        label="Briefkasten", endpoint="", user="", secret="",
        target=str(remote_dir or ""), advise=lambda *a: box.line(), **kw)


def explain(exc: Exception, remote_dir: str = "") -> str:
    """One sentence naming the thing to change, for the way in use.

    Dispatched rather than merged, because the sentences contradict each other:
    a 401 on the account path says "rotate the app passcode", and on the share
    path saying that would send somebody to rotate a credential this way does
    not use. That is the failure mode #592 exists to remove, so it must not be
    reintroduced by the error text.
    """
    box = which()
    if box.way == SHARE:
        from utils import nextcloud
        return nextcloud.explain(exc, remote_dir)
    if box.way == ACCOUNT:
        from utils import switchdrive
        return switchdrive.explain(exc, remote_dir)
    return box.line()


def load_processed() -> set:
    """Orders already done. Shared by both ways on purpose: the record is about
    the material, not about how it was fetched, and a switch of mailbox must not
    make the pipeline reprocess the whole corpus."""
    from utils import switchdrive
    return switchdrive.load_processed()


def mark_processed(order_id: str) -> None:
    from utils import switchdrive
    switchdrive.mark_processed(order_id)
