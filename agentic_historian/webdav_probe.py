"""
webdav_probe.py — tell the four WebDAV failures apart before guessing (#563).

``/pull_folder missiven`` failed on tei with

    ❌ Error: received 401 (Unauthorized)

and finding the cause took four steps — check the network, check the endpoint,
check the path, check the credentials — because the message named none of them.
``switchdrive.is_configured()`` answers "are the values present", which is not
the question anybody has; it reports "configured" and means "not empty".

So this probes the layers **separately**, and in the order in which one
invalidates the next:

``config`` → ``network`` → ``auth`` → ``path``

Two decisions carry the whole module.

**The auth layer asks about the ROOT, never the target.**
A ``PROPFIND`` on ``…/agentian_hotfolder/missiven/`` answers 401 before it ever
looks at the path, so a bad password and a missing folder arrive as the same
sentence. Asking the root first splits them: 401 at the root is the credentials
and the path is innocent; 207 at the root and 404 below it is the path and the
credentials are fine. That one reordering is what the four steps cost.

**A layer that was not reached is ``unknown``, not ``failed``.**
After a 401 nothing is known about the folder. Reporting "path: failed" there
would assert something nobody measured — and it is precisely the confusion that
sent us looking at the folder first. Same three-valued discipline as everywhere
else in this project: the absence of a measurement may not be dressed as a
measurement.

Mailbox-agnostic on purpose. A SwitchDrive pull authenticates as a *user*
(username + app password); a public share authenticates with the *share token as
the username* (``utils/nextcloud.py``). The two differ in what they put in the
auth tuple and in what the fix is, and in nothing this module does — so the
caller passes credentials and an ``advise`` hook, and this file never learns
what an app password is.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import unquote, urlsplit

from loguru import logger

__all__ = [
    "Check",
    "FAILED",
    "LAYERS",
    "OK",
    "Probe",
    "UNKNOWN",
    "count_entries",
    "format_probe",
    "probe",
    "status_of",
]

OK, FAILED, UNKNOWN = "ok", "failed", "unknown"

#: In order. Each one, failing, leaves the rest unknowable.
LAYERS = ("config", "network", "auth", "path")

#: Statuses a WebDAV collection listing may legitimately answer with.
_LISTED = (200, 207)


def status_of(exc: Exception) -> Optional[int]:
    """The HTTP status behind a webdav4 or requests exception, when there is one.

    ``None`` means *no status* — a timeout, a DNS failure, a refused connection.
    That is a different fact from a status, and a caller that reads None as 0 or
    as 500 loses the one distinction between "the host never answered" and "the
    host said no".

    Lifted out of ``nextcloud._status_of``, which now delegates here: both halves
    of this repository ask the same question of the same library, and two
    implementations of it would be two answers.
    """
    code = getattr(exc, "status_code", None)
    if code is None:
        response = getattr(exc, "response", None)          # requests
        code = getattr(response, "status_code", None)
    if code is None and type(exc).__name__ == "ResourceNotFound":
        return 404                                         # webdav4's own name
    try:
        return int(code) if code else None
    except (TypeError, ValueError):                        # pragma: no cover
        return None


# ── one layer's answer ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Check:
    layer: str
    status: str = UNKNOWN
    #: What was observed, in the terms of the layer ("207", "no route to host").
    detail: str = ""
    #: What to go and change. Empty when there is nothing to change.
    action: str = ""

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def failed(self) -> bool:
        return self.status == FAILED

    @property
    def unknown(self) -> bool:
        return self.status == UNKNOWN


@dataclass
class Probe:
    """One mailbox, four layers, and what to do about the first failure."""

    label: str
    endpoint: str = ""
    target: str = ""
    #: The identity that was offered. Shown because a wrong account is a common
    #: cause and only a human can judge it — see `switchdrive.preflight`.
    user: str = ""
    #: The secret's **length**, never the secret. Enough to tell a 23-character
    #: password from a generated token, and useless to anyone reading a log.
    secret_chars: int = 0
    #: Which file the secret came from, or "" when it came from the process
    #: environment — where no `.env` edit can replace it (`override=False`).
    secret_source: str = ""
    checks: list = field(default_factory=list)
    #: Ingestible files counted in ``target``. ``None`` = not counted, which is
    #: not the same as zero.
    files: Optional[int] = None

    def check(self, layer: str) -> Check:
        for found in self.checks:
            if found.layer == layer:
                return found
        return Check(layer=layer)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def blocked_at(self) -> Optional[Check]:
        """The first layer that failed, or None when every layer passed."""
        for found in self.checks:
            if found.failed:
                return found
        return None

    @property
    def action(self) -> str:
        blocked = self.blocked_at
        return blocked.action if blocked else ""


# ── the probe ────────────────────────────────────────────────────────────────

def _request(method: str, url: str, **kwargs):
    import requests
    return requests.request(method, url, **kwargs)


def count_entries(body: str, *, exts: Optional[Iterable[str]] = None) -> int:
    """Ingestible files named by a ``PROPFIND`` response body.

    Collections are skipped (their href ends in ``/``), and so is the collection
    the request was made on. Hrefs are percent-encoded, so they are unquoted
    before the suffix is read — ``%C3%A4`` is not an extension.
    """
    wanted = {e.lower() for e in (exts or ())}
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        logger.warning(f"[probe] unparsable PROPFIND body: {e}")
        return 0
    found = 0
    for href in root.iter("{DAV:}href"):
        text = unquote((href.text or "").strip())
        if not text or text.endswith("/"):
            continue
        if wanted and Path(text).suffix.lower() not in wanted:
            continue
        found += 1
    return found


def probe(*, label: str, endpoint: str, user: str, secret: str,
          target: str = "", secret_source: str = "", secret_key: str = "",
          exts: Optional[Iterable[str]] = None,
          advise: Optional[Callable[[str, Optional[int], "Probe"], str]] = None,
          timeout: float = 30.0, request: Optional[Callable] = None) -> Probe:
    """Run the four layers against one WebDAV endpoint. Never raises.

    ``advise(layer, status, probe) -> str`` is the mailbox-specific hook: this
    function knows that 401 is an auth failure and not what to do about one,
    because the answer differs between an account's app password and a share
    token. Returning "" is fine; the generic detail still stands.
    """
    call = request or _request
    found = Probe(label=label, endpoint=endpoint, target=target, user=user,
                  secret_chars=len(secret or ""), secret_source=secret_source)

    def _advise(layer: str, status: Optional[int]) -> str:
        if advise is None:
            return ""
        try:
            return advise(layer, status, found) or ""
        except Exception as e:                   # noqa: BLE001 — advice may not raise
            logger.warning(f"[probe] advice for {layer} failed: {e}")
            return ""

    def _stop(layer: str, detail: str, status: Optional[int] = None) -> Probe:
        """Record the failure and leave every later layer unknown."""
        found.checks.append(Check(layer=layer, status=FAILED, detail=detail,
                                  action=_advise(layer, status)))
        reached = LAYERS.index(layer)
        for later in LAYERS[reached + 1:]:
            found.checks.append(Check(
                layer=later, status=UNKNOWN,
                detail=f"nicht geprüft — {layer} hat vorher versagt"))
        return found

    # ── config ───────────────────────────────────────────────────────────────
    missing = [name for name, value in
               (("URL", endpoint), ("USER", user), ("PASS", secret))
               if not (value or "").strip()]
    if missing:
        return _stop("config", f"nicht gesetzt: {', '.join(missing)}")
    # A secret whose value is the literal "<Passwort>" is set, is truthy, passes
    # every "is it configured" test in this repo, and fails at the far end of a
    # network call with an error about the server — so it is caught here, by the
    # layer that claims to have checked the configuration. `config` already
    # knows how to spot one and which of three files to name (2026-09-18 on tei:
    # two files carried `<Passwort>` and the third the real one).
    if secret_key:
        try:
            import config as _config
            why = _config.unfilled_secrets().get(secret_key)
            if why:
                return _stop("config", f"{secret_key}: {why}")
        except Exception as e:                   # noqa: BLE001
            logger.warning(f"[probe] placeholder check skipped: {e}")
    found.checks.append(Check(
        layer="config", status=OK,
        detail=f"{user} · {len(secret)} Zeichen"
               + (f" aus {secret_source}" if secret_source
                  else " aus der Prozess-Umgebung")))

    root = endpoint.rstrip("/") + "/"

    # ── network ──────────────────────────────────────────────────────────────
    # Deliberately unauthenticated: a 401 here is a *success* for this layer —
    # the host answered. Only a transport failure is a network failure.
    try:
        answered = call("OPTIONS", root, timeout=timeout)
        found.checks.append(Check(
            layer="network", status=OK,
            detail=f"{urlsplit(root).netloc} antwortet "
                   f"(HTTP {getattr(answered, 'status_code', '?')})"))
    except Exception as e:                       # noqa: BLE001
        return _stop("network", f"{type(e).__name__}: {e}")

    # ── auth: the ROOT, never the target ─────────────────────────────────────
    try:
        answer = call("PROPFIND", root, auth=(user, secret),
                      headers={"Depth": "0"}, timeout=timeout)
    except Exception as e:                       # noqa: BLE001
        return _stop("auth", f"{type(e).__name__}: {e}", status_of(e))
    status = getattr(answer, "status_code", None)
    if status not in _LISTED:
        return _stop("auth", f"HTTP {status} auf der Wurzel", status)
    found.checks.append(Check(layer="auth", status=OK,
                              detail=f"HTTP {status} auf der Wurzel"))

    # ── path: only now, and only because auth said yes ───────────────────────
    if not target:
        found.checks.append(Check(
            layer="path", status=UNKNOWN,
            detail="kein Zielordner angegeben — nichts zu prüfen"))
        return found
    url = root + target.strip("/") + "/"
    try:
        answer = call("PROPFIND", url, auth=(user, secret),
                      headers={"Depth": "1"}, timeout=timeout)
    except Exception as e:                       # noqa: BLE001
        return _stop("path", f"{type(e).__name__}: {e}", status_of(e))
    status = getattr(answer, "status_code", None)
    if status not in _LISTED:
        return _stop("path", f"HTTP {status} auf `{target}`", status)
    found.files = count_entries(getattr(answer, "text", "") or "", exts=exts)
    found.checks.append(Check(
        layer="path", status=OK,
        detail=f"HTTP {status} · {found.files} verwertbare Datei(en)"))
    return found


# ── rendering ────────────────────────────────────────────────────────────────

_MARK = {OK: "✅", FAILED: "❌", UNKNOWN: "▫️"}


def format_probe(found: Probe) -> list[str]:
    """The report as Discord messages, one per call to send.

    A list rather than one string, following ``atr_status.format_gpu_views``:
    Discord caps a message, and a formatter that returns messages keeps the
    splitting testable without a client.
    """
    head = [f"🔍 **{found.label}** — Vorflug"
            + (f" für `{found.target}`" if found.target else ""),
            f"`{found.endpoint}`"]
    lines = list(head)
    for layer in LAYERS:
        check = found.check(layer)
        lines.append(f"{_MARK.get(check.status, '▫️')} **{layer}** — "
                     f"{check.detail or '—'}")
    messages = ["\n".join(lines)]

    # Three cases, not two. The third — nothing failed, but something was never
    # checked — is the whole point of this module, and the first version of this
    # function had no branch for it: a probe that cleared config, network and
    # auth with no target folder fell between `ok` and `blocked` and got no
    # closing line at all. Its own test caught that.
    blocked = found.blocked_at
    if blocked is not None:
        what = blocked.action or "Keine Handlungsempfehlung für diesen Fall."
        messages.append(f"**Blockiert bei `{blocked.layer}`.** {what}")
    elif found.ok:
        messages.append(
            f"Alles erreichbar — `{found.target}` enthält {found.files} "
            f"verwertbare Datei(en).")
    else:
        unchecked = [c.layer for c in found.checks if c.unknown]
        messages.append(
            f"Nichts ist fehlgeschlagen, aber **{', '.join(unchecked)}** "
            f"wurde nicht geprüft"
            + (" — Kein Zielordner angegeben, mit `folder:` nachholen."
               if "path" in unchecked else "."))
    return messages
