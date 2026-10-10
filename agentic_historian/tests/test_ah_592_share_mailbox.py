"""#592: the hot folder on a share token instead of somebody's edu-ID account.

The ingest authenticated as a **user** — `SWITCHDRIVE_USER` plus an app passcode
against that account's own root — so the corpus entrance hung on a personal
account. On 09./10.10.2026 that cost two days: a valid App Passcode stopped
being accepted and **why is still unknown** (`LESSONS_2026-10.md` §9, three
mechanisms proposed and two refuted). The shape is worse than the incident:
SWITCH documents one passcode per device and deleting one closes that connection
at once, so a passcode shared between tei, a laptop and a backup dies the moment
any of them is tidied up.

A public share has no user, no account and no app passcode. Both ways stay — a
switch that keeps the old one is a switch with a return ticket — so the thing
that has to be true is that exactly one of them is in use, that the answer
**says which**, and that the diagnosis never names the credential the other way
would have used.

Offline — HTTP is a stub. Two things here are measurements the code performs
rather than assumptions it carries (#592 asks for both): which public endpoint a
server serves, and whether a file-drop share can be listed at all.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                    # noqa: E402
import ingest_mailbox as mb      # noqa: E402
import webdav_probe as wp        # noqa: E402
from utils import nextcloud as nc    # noqa: E402

TOKEN = "AbCdEf123456"
SHARE_URL = f"https://drive.switch.ch/index.php/s/{TOKEN}"
BASE = "https://drive.switch.ch"
DAV = f"{BASE}/public.php/dav/files/{TOKEN}"
LEGACY = f"{BASE}/public.php/webdav"
SHARE_PASS = "freigabe-passwort-19"

MULTISTATUS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/public.php/webdav/missiven/</d:href></d:response>
  <d:response><d:href>/public.php/webdav/missiven/001r.jpg</d:href></d:response>
  <d:response><d:href>/public.php/webdav/missiven/002v.JPG</d:href></d:response>
  <d:response><d:href>/public.php/webdav/missiven/notizen.txt</d:href></d:response>
</d:multistatus>"""


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", SHARE_URL)
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_PASS", SHARE_PASS)
    monkeypatch.setattr(config, "SWITCHDRIVE_USER", "")
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "")
    monkeypatch.setattr(config, "NEXTCLOUD_REMOTE_DIR", "")
    monkeypatch.setattr(config, "ENV_SOURCE", {})
    return tmp_path


class _Http:
    """Answers per (method, endpoint prefix). Records every call.

    ``serves`` names the endpoints this fake server actually has; anything else
    answers 404, which is how a server that moved (or never moved) its public
    share endpoint behaves.
    """

    def __init__(self, *, serves=(DAV,), root=207, target=207,
                 body=MULTISTATUS, options=200):
        self.calls: list = []
        self._serves = tuple(serves)
        self._root, self._target, self._body = root, target, body
        self._options = options

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw.get("auth")))
        served = any(url.rstrip("/").startswith(e.rstrip("/"))
                     for e in self._serves)
        if method == "OPTIONS":
            return _Answer(self._options)
        if not served:
            return _Answer(404)
        base = next(e for e in self._serves
                    if url.rstrip("/").startswith(e.rstrip("/")))
        at_root = url.rstrip("/") == base.rstrip("/")
        if at_root:
            return _Answer(self._root)
        return _Answer(self._target, self._body)


class _Answer:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


# ── parsing and masking ─────────────────────────────────────────────────────

def test_the_hotfolder_share_is_parsed_from_the_browser_url():
    share = nc.hotfolder_share()
    assert share.token == TOKEN
    assert share.base_url == BASE
    assert share.password == SHARE_PASS


def test_the_hotfolder_share_is_not_the_corpus_share(monkeypatch):
    """NEXTCLOUD_SHARE_* names the Laßberg corpus on the GWDG instance, which is
    mounted and read in place (#487). Reusing it for the mailbox would point the
    hot folder at a 160 GB holding — the two jobs #592 says not to mix."""
    monkeypatch.setattr(config, "NEXTCLOUD_SHARE_URL",
                        "https://sync.gwdg.de/index.php/s/ZZZZZZZZZZZZ")
    monkeypatch.setattr(config, "NEXTCLOUD_SHARE_PASS", "other")
    assert nc.hotfolder_share().token == TOKEN
    assert nc.share_from_config().token == "ZZZZZZZZZZZZ"


def test_an_unconfigured_share_raises_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", "")
    with pytest.raises(nc.NextcloudError):
        nc.hotfolder_share()


def test_a_token_is_identified_without_being_handed_over():
    masked = nc.masked_token(TOKEN)
    assert TOKEN not in masked
    assert masked.startswith("AbCd")
    assert "12" in masked          # its length, which tells two shares apart
    assert nc.masked_token("") == ""
    assert nc.masked_token("ab") == "…"


# ── which way is in use, and saying so ──────────────────────────────────────

def test_the_share_is_used_when_configured():
    box = mb.which()
    assert box.way == mb.SHARE
    assert box.configured
    assert "Freigabe" in box.line()


def test_the_account_is_used_when_no_share_is_configured(monkeypatch):
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", "")
    monkeypatch.setattr(config, "SWITCHDRIVE_USER", "someone@example.ch")
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "app-pass")
    box = mb.which()
    assert box.way == mb.ACCOUNT
    assert "someone@example.ch" in box.line()


def test_the_share_wins_when_both_are_configured(monkeypatch):
    """Preferring the account would mean the migration never takes effect while
    looking as though it had."""
    monkeypatch.setattr(config, "SWITCHDRIVE_USER", "someone@example.ch")
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "app-pass")
    assert mb.which().way == mb.SHARE


def test_an_unparsable_share_url_does_not_fall_back_to_the_account(monkeypatch):
    """A misspelled share URL must not read as "the share works".

    Falling through would make the next failure a message about an app passcode
    while the operator believes a share is in use — the exact puzzle #563 was
    filed for, reintroduced by a convenience.
    """
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", "not-a-url")
    monkeypatch.setattr(config, "SWITCHDRIVE_USER", "someone@example.ch")
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "app-pass")
    box = mb.which()
    assert box.way == mb.NONE
    assert "nicht lesbar" in box.detail


def test_nothing_configured_is_reported_as_nothing_configured(monkeypatch):
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", "")
    box = mb.which()
    assert box.way == mb.NONE
    assert not box.configured
    assert "SWITCHDRIVE_SHARE_URL" in box.line()


def test_the_way_line_never_names_a_credential_mechanism(monkeypatch):
    """It goes onto every answer, including the error from a full disk.

    #563's test caught the first version of this saying "(App-Passwort)" there:
    naming a remedy on an unrelated failure is the invented advice that test
    exists to prevent. A remedy needs a measured status code, so it belongs in
    ``_advise`` and ``explain``.
    """
    # "mit/ohne Passwort" is a fact about the share, not a remedy, and it is
    # worth having. "App-Passwort" on an unrelated error is the thing forbidden.
    assert "App-Passwort" not in mb.which().line()
    assert SHARE_PASS not in mb.which().line()
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", "")
    monkeypatch.setattr(config, "SWITCHDRIVE_USER", "someone@example.ch")
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "app-pass")
    line = mb.which().line()
    assert "App-Passwort" not in line
    assert "app-pass" not in line


def test_the_way_line_never_carries_the_token():
    assert TOKEN not in mb.which().line()
    assert SHARE_PASS not in mb.which().line()


# ── the measurement: which endpoint this server serves ──────────────────────

def test_the_newer_endpoint_is_tried_first_and_wins_when_served():
    http = _Http(serves=(DAV,))
    found = mb.preflight("missiven", request=http)
    assert found.ok
    assert found.endpoint == DAV
    propfinds = [u for m, u, _ in http.calls if m == "PROPFIND"]
    assert propfinds[0].startswith(DAV)


def test_a_server_serving_only_the_legacy_endpoint_is_found():
    """The measurement #592 asks for. SwitchDrive is an ownCloud descendant and
    the GWDG reading (Nextcloud 30) does not transfer — so the code asks."""
    http = _Http(serves=(LEGACY,))
    found = mb.preflight("missiven", request=http)
    assert found.ok
    assert found.endpoint == LEGACY


def test_the_report_names_both_attempts_when_the_endpoint_was_a_question():
    """Both are identifiable — and the newer one's URL carries the token, so it
    appears redacted rather than in full. Distinguishing the two endpoints does
    not require printing the credential, and that is the test that nearly
    contradicted the redaction one."""
    http = _Http(serves=(LEGACY,))
    found = mb.preflight("missiven", request=http)
    assert len(found.endpoints_tried) == 2
    text = "\n".join(wp.format_probe(found))
    assert "public.php/dav/files" in text        # the newer endpoint, named
    assert TOKEN not in text                     # but not its token
    assert LEGACY in text                        # the legacy one has none


def test_a_mailbox_with_one_endpoint_reports_no_attempt_list(monkeypatch):
    """The account path must not grow a list of one."""
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", "")
    monkeypatch.setattr(config, "SWITCHDRIVE_USER", "someone@example.ch")
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "app-pass")
    monkeypatch.setattr(config, "SWITCHDRIVE_URL", "https://drive.switch.ch/remote.php/webdav")
    found = mb.preflight("missiven", request=_Http(
        serves=("https://drive.switch.ch/remote.php/webdav",)))
    assert found.endpoints_tried == []


def test_the_furthest_probe_is_reported_not_the_first():
    """A server with only the legacy endpoint 404s the newer one at *auth*.

    Returning the first attempt would report that dead endpoint's advice about a
    mailbox that works perfectly well.
    """
    http = _Http(serves=(LEGACY,), target=404)
    found = mb.preflight("missiven", request=http)
    assert found.endpoint == LEGACY
    assert found.blocked_at.layer == "path"      # not auth, which DAV failed at


def test_a_config_failure_is_not_asked_twice(monkeypatch):
    """Endpoint-independent: the same values are missing for every candidate."""
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", "")
    found = nc.preflight("missiven", request=_Http())
    assert found.blocked_at.layer == "config"
    assert len(found.endpoints_tried) <= 1


# ── the probe's layers, for a share ─────────────────────────────────────────

def test_a_share_without_a_password_is_a_valid_configuration(monkeypatch):
    """A public share may have none. Without ``secret_optional`` the config
    layer reports "nicht gesetzt: PASS" for a share that works."""
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_PASS", "")
    found = mb.preflight("missiven", request=_Http())
    assert found.check("config").ok
    assert found.ok


def test_the_auth_layer_asks_the_share_root_not_the_target():
    http = _Http(serves=(DAV,))
    mb.preflight("missiven", request=http)
    propfinds = [u for m, u, _ in http.calls if m == "PROPFIND"]
    assert propfinds[0].rstrip("/") == DAV.rstrip("/")
    assert propfinds[1].rstrip("/").endswith("/missiven")


def test_the_token_is_what_is_sent_as_the_username():
    http = _Http(serves=(DAV,))
    mb.preflight("missiven", request=http)
    auths = [a for m, _u, a in http.calls if m == "PROPFIND" and a]
    assert auths[0] == (TOKEN, SHARE_PASS)


def test_the_token_never_appears_in_the_report():
    """It **is** the credential for a share, unlike a username on the account
    path — so printing it into a channel or a log hands over the access.

    Masking the username was not enough, and this test is why: the token is in
    the endpoint URL itself (``public.php/dav/files/<token>``), which the report
    prints twice — once as the endpoint and once per attempt. The credential was
    in the one place nobody thought to look.
    """
    http = _Http(serves=(DAV,), root=401)
    found = mb.preflight("missiven", request=http)
    text = "\n".join(wp.format_probe(found))
    assert TOKEN not in text
    assert SHARE_PASS not in text
    assert "AbCd" in text          # identified, not handed over


def test_a_transport_error_does_not_leak_the_token_through_its_message():
    """The gap a mutation found: scrubbing only the report's own lines looked
    sufficient because a 401's detail is "HTTP 401 auf der Wurzel".

    But a transport failure's detail is the exception's text, and requests puts
    the whole URL in it — ``Max retries exceeded with url:
    /public.php/dav/files/<token>/``. So the scrub has to happen where the Check
    is built, not only where it is printed.
    """
    class _Boom:
        def __call__(self, method, url, **kw):
            raise OSError(
                f"HTTPSConnectionPool(host='drive.switch.ch', port=443): "
                f"Max retries exceeded with url: /public.php/dav/files/{TOKEN}/")

    found = mb.preflight("missiven", request=_Boom())
    blocked = found.blocked_at
    assert blocked.layer == "network"
    assert TOKEN not in blocked.detail
    assert TOKEN not in "\n".join(wp.format_probe(found))


def test_a_placeholder_share_password_fails_at_config_and_is_asked_once():
    """``<Passwort>`` is set, is truthy, passes every "is it configured" test in
    this repo, and fails at the far end of a network call (#563's config layer).

    On the share path it is also the only way the config layer can fail, since
    URL and token both come from a parsed share — which makes it the case that
    proves the "do not ask a config failure once per endpoint" rule: the values
    are the same for every candidate, so asking twice prints the same complaint
    twice.
    """
    import config as _config
    original = _config.unfilled_secrets
    try:
        _config.unfilled_secrets = lambda: {
            "SWITCHDRIVE_SHARE_PASS": "ist noch der Platzhalter `<Passwort>`"}
        http = _Http(serves=(DAV,))
        found = mb.preflight("missiven", request=http)
    finally:
        _config.unfilled_secrets = original
    assert found.blocked_at.layer == "config"
    assert len(found.endpoints_tried) == 1
    assert http.calls == []          # nothing was asked of the network at all


def test_the_token_does_not_reach_the_watcher_either():
    """``credential_watch`` renders ``blocked.detail`` and ``blocked.action``
    rather than the report, so a scrub that lived only in ``format_probe``
    would leak through the announcement."""
    import credential_watch

    found = mb.preflight("missiven", request=_Http(serves=(DAV,), root=401))
    state = credential_watch.WatchState(blocked="auth", since=0.0)
    notice, _ = credential_watch.decide(state, found, 10_000.0,
                                        label="Briefkasten (Freigabe)")
    assert notice is not None
    assert TOKEN not in notice.text
    assert SHARE_PASS not in notice.text


def test_a_401_on_the_share_names_the_share_and_not_an_app_password():
    """The whole point of the migration, in the error text: this way has no app
    passcode, and advice naming one sends somebody to a settings page where
    there is nothing to fix."""
    found = mb.preflight("missiven", request=_Http(serves=(DAV,), root=401))
    action = found.blocked_at.action
    # It names the app passcode only to rule it out — the one mention that helps
    # rather than misleads, because the reader's reflex after 09.10. is to rotate
    # one, and on this path there is none to rotate.
    assert "Kein App-Passwort" in action
    assert "edu-ID" in action
    assert "Freigabe" in action


def test_a_401_without_a_configured_share_password_says_so(monkeypatch):
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_PASS", "")
    found = mb.preflight("missiven", request=_Http(serves=(DAV,), root=401))
    action = found.blocked_at.action
    assert "SWITCHDRIVE_SHARE_PASS" in action
    assert "kein" in action.lower()


def test_a_write_only_share_lands_on_403_with_its_own_advice():
    """The second measurement #592 asks for: a file-drop share authenticates and
    may refuse to list. That is a 403, and it needs its own sentence rather than
    looking like a credential problem."""
    found = mb.preflight("missiven", request=_Http(serves=(DAV,), root=403))
    action = found.blocked_at.action
    assert "ablegen" in action
    assert "Lese" in action


def test_a_404_below_a_working_share_is_a_path_problem():
    found = mb.preflight("missiven", request=_Http(serves=(DAV,), target=404))
    blocked = found.blocked_at
    assert blocked.layer == "path"
    assert "Freigabe-Wurzel" in blocked.action
    assert found.check("auth").ok


def test_files_are_counted_in_the_target():
    found = mb.preflight("missiven", request=_Http(serves=(DAV,)))
    assert found.files == 2        # two images; notizen.txt is not ingestible


# ── the error sentence dispatches on the way in use ────────────────────────

class _Status(Exception):
    def __init__(self, code):
        super().__init__(f"received {code}")
        self.status_code = code


def test_a_401_explained_on_the_share_path_mentions_no_app_password():
    why = mb.explain(_Status(401), "missiven")
    assert "Freigabe" in why or "Token" in why
    assert "edu-ID" in why


def test_a_401_explained_on_the_account_path_still_names_the_app_password(monkeypatch):
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", "")
    monkeypatch.setattr(config, "SWITCHDRIVE_USER", "someone@example.ch")
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "app-pass")
    why = mb.explain(_Status(401), "missiven")
    assert "App-Passwort" in why


def test_an_unrelated_error_gets_no_invented_advice():
    """No status covers two things: a transport failure, and an error that was
    never an HTTP call (a full disk, a local permission problem). The first
    version of this said "network, DNS or an outage" to both — the invented
    advice #563's test forbids, reintroduced on the new path.
    """
    assert mb.explain(OSError("disk full"), "missiven") == ""


def test_a_403_on_the_share_path_names_the_file_drop_case():
    assert "ablegen" in mb.explain(_Status(403), "missiven")


# ── the delegation, and what must not change with the mailbox ─────────────

def test_pull_goes_to_the_share_client_when_the_share_is_configured(monkeypatch):
    seen: list = []
    monkeypatch.setattr(nc, "pull_folder",
                        lambda r, dest, rec: seen.append(("share", r, rec)) or [])
    mb.pull_folder("missiven", Path("/tmp/x"), False)
    assert seen == [("share", "missiven", False)]


def test_pull_goes_to_the_account_client_when_it_is_the_way(monkeypatch):
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", "")
    monkeypatch.setattr(config, "SWITCHDRIVE_USER", "someone@example.ch")
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "app-pass")
    from utils import switchdrive
    seen: list = []
    monkeypatch.setattr(switchdrive, "pull_folder",
                        lambda r, dest, rec: seen.append(("account", r, rec)) or [])
    mb.pull_folder("missiven", Path("/tmp/x"), False)
    assert seen == [("account", "missiven", False)]


def test_recursive_defaults_the_same_for_both_ways(monkeypatch):
    """The share client defaults ``recursive=True`` and the account client
    False. A default that changed with the mailbox would make `/pull` descend or
    not depending on a setting nobody connects to it."""
    seen: list = []
    monkeypatch.setattr(nc, "pull_folder",
                        lambda r, dest, rec: seen.append(rec) or [])
    mb.pull_folder("missiven")
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", "")
    monkeypatch.setattr(config, "SWITCHDRIVE_USER", "u")
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "p")
    from utils import switchdrive
    monkeypatch.setattr(switchdrive, "pull_folder",
                        lambda r, dest, rec: seen.append(rec) or [])
    mb.pull_folder("missiven")
    assert seen == [False, False]


def test_pulling_without_a_mailbox_says_what_to_configure(monkeypatch):
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", "")
    with pytest.raises(RuntimeError) as caught:
        mb.pull_folder("missiven")
    assert "SWITCHDRIVE_SHARE_URL" in str(caught.value)


def test_the_processed_record_is_shared_between_both_ways(monkeypatch):
    """It is about the material, not about how it was fetched. A switch of
    mailbox must not make the pipeline reprocess the whole corpus."""
    from utils import switchdrive
    monkeypatch.setattr(switchdrive, "load_processed", lambda: {"missiven"})
    assert mb.load_processed() == {"missiven"}
    monkeypatch.setattr(config, "SWITCHDRIVE_SHARE_URL", "")
    monkeypatch.setattr(config, "SWITCHDRIVE_USER", "u")
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "p")
    assert mb.load_processed() == {"missiven"}


def test_a_flat_share_folder_is_one_order(monkeypatch):
    """``ingest.run_switchdrive_orders`` relies on ``[remote_dir]`` for a folder
    with no subfolders. A share path returning ``[]`` there would quietly
    process nothing and report success."""
    monkeypatch.setattr(nc, "_connect", lambda share, probe_dir="": object())
    monkeypatch.setattr(nc, "_ls", lambda client, rdir: [
        {"name": "missiven/001r.jpg", "type": "file"}])
    assert nc.list_subdirs("missiven") == ["missiven"]


def test_share_subfolders_are_listed_as_orders(monkeypatch):
    monkeypatch.setattr(nc, "_connect", lambda share, probe_dir="": object())
    monkeypatch.setattr(nc, "_ls", lambda client, rdir: [
        {"name": "missiven/", "type": "directory"},
        {"name": "briefe/", "type": "directory"},
        {"name": "lose.jpg", "type": "file"}])
    assert nc.list_subdirs("") == ["briefe", "missiven"]


# ── the bot and the watcher use the way in use ─────────────────────────────

def test_the_pull_commands_go_through_the_mailbox():
    """A guard. Three commands and a watcher must not each decide this: the
    point of the module is that `/pull`, `/pull_folder`, `/pull_preflight` and
    the credential watcher cannot disagree about which way is in use.
    """
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    for name in ("pull_cmd", "pull_folder_cmd", "pull_preflight_cmd"):
        body = src[src.index(f"async def {name}("):]
        body = body[:body.index("\n@bot.slash_command")]
        assert "ingest_mailbox" in body, name
        assert "from utils import switchdrive" not in body, name


def test_every_answer_composes_the_way_rather_than_remembering_to():
    """**No** message from these commands may bypass ``ingest_mailbox.answer``.

    Two weaker versions of this guard let a mutation through. Asserting the
    footer "somewhere in the function" passed when it was stripped from the one
    line people read, because the error branch still had it; asserting it in the
    success block passed because a sibling branch did. The rule a guard can
    actually hold is that every send is composed, so the code was changed to make
    that true rather than the test weakened to accept less.
    """
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    for name in ("pull_cmd", "pull_folder_cmd"):
        body = src[src.index(f"async def {name}("):]
        body = body[:body.index("\n@bot.slash_command")]
        sends = re.findall(r"ctx\.followup\.send\(([^\n]*)", body)
        assert sends, name
        for sent in sends:
            # ``box.line()`` alone is the refusal, which *is* the line.
            assert ("ingest_mailbox.answer(" in sent
                    or sent.strip().startswith("box.line()")), f"{name}: {sent!r}"


def test_the_answer_composer_carries_the_way():
    box = mb.which()
    composed = mb.answer(box, "⬇️ 12 Datei(en) geholt")
    assert "12 Datei(en)" in composed
    assert "Freigabe" in composed
    assert TOKEN not in composed


def test_the_watcher_watches_the_way_in_use():
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    body = src[src.index("async def _credential_watch_loop"):]
    body = body[:body.index("\n# ── Commands")]
    assert "ingest_mailbox.preflight" in body
    assert "switchdrive.preflight" not in body
    # And the announcement says which mailbox, or a recovered message is about
    # a credential the reader cannot identify.
    assert "label=" in body


def test_ingest_reads_whichever_mailbox_is_configured():
    src = (PKG / "ingest.py").read_text(encoding="utf-8")
    body = src[src.index("def run_switchdrive_orders"):]
    assert "ingest_mailbox" in body
    assert "switchdrive.pull_folder" not in body
