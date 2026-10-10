"""#563: tell the four SwitchDrive failures apart instead of guessing.

`/pull_folder missiven` answered `received 401 (Unauthorized)` on 2026-10-09 and
finding the cause took four steps — network, endpoint, path, credentials —
because the message named none of them. `is_configured()` answers "are the
values present", which is not the question anybody has.

Three contracts, each with its own section:

**The auth layer asks about the ROOT, never the target.** A PROPFIND on
`…/missiven/` answers 401 *before* it looks at the path, so a dead app password
and a missing folder arrive as one sentence. Asking the root first splits them.
This is the reordering that would have saved the afternoon, and it is asserted
on the actual URLs the probe requests.

**A layer that was not reached is `unknown`, not `failed`.** After a 401 nothing
is known about the folder; "path: failed" there asserts something nobody
measured, and it is what sent us looking at the folder first.

**The diagnosis names the action, not the status code.** And never the secret —
only its length, and which file it came from, because a value already in
`os.environ` cannot be replaced by editing any `.env`.

Offline — HTTP is a stub. Run from the repo root:
    pytest agentic_historian/tests/test_ah_563_pull_preflight.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config          # noqa: E402
import webdav_probe as wp   # noqa: E402
from utils import switchdrive   # noqa: E402

ROOT = "https://drive.switch.ch/remote.php/webdav"
TARGET = "agentic_historian_hotfolder/missiven"
SECRET = "s3cret-app-password-23"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "SWITCHDRIVE_URL", ROOT)
    monkeypatch.setattr(config, "SWITCHDRIVE_USER", "tobias.hodel@example.ch")
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", SECRET)
    monkeypatch.setattr(config, "SWITCHDRIVE_REMOTE_DIR",
                        "agentic_historian_hotfolder")
    monkeypatch.setattr(config, "ENV_SOURCE", {})
    return tmp_path


# ── a stub that records what was asked, in order ─────────────────────────────

MULTISTATUS = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/remote.php/webdav/x/missiven/</d:href></d:response>
  <d:response><d:href>/remote.php/webdav/x/missiven/001r.jpg</d:href></d:response>
  <d:response><d:href>/remote.php/webdav/x/missiven/002v.JPG</d:href></d:response>
  <d:response><d:href>/remote.php/webdav/x/missiven/Beschr%C3%A4nkung.pdf</d:href></d:response>
  <d:response><d:href>/remote.php/webdav/x/missiven/notizen.txt</d:href></d:response>
</d:multistatus>"""

UNAUTHORISED = """<?xml version="1.0"?>
<d:error xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns">
  <s:exception>Sabre\\DAV\\Exception\\NotAuthenticated</s:exception>
  <s:message>Username or password was incorrect, No 'Authorization: Bearer'
  header found.</s:message>
</d:error>"""


class _Http:
    """Answers by (method, whether the url ends at the root). Records calls."""

    def __init__(self, *, options=200, root=207, target=207,
                 root_body="", target_body=MULTISTATUS, raises=None):
        self.calls: list = []
        self._options, self._root, self._target = options, root, target
        self._root_body, self._target_body = root_body, target_body
        self._raises = raises or {}

    def __call__(self, method, url, **kw):
        at_root = url.rstrip("/") == ROOT.rstrip("/")
        self.calls.append((method, url, tuple(kw.get("auth") or ())))
        key = "options" if method == "OPTIONS" else ("root" if at_root else "target")
        if key in self._raises:
            raise self._raises[key]
        status = {"options": self._options, "root": self._root,
                  "target": self._target}[key]
        body = self._root_body if key == "root" else self._target_body
        return SimpleNamespace(status_code=status, text=body)


def _pf(http=None, folder=TARGET, **kw):
    return switchdrive.preflight(folder, request=http or _Http(), **kw)


def _text(found):
    return "\n".join(wp.format_probe(found))


# ── the auth layer asks about the ROOT ───────────────────────────────────────

def test_auth_is_checked_against_the_root_before_the_target():
    """The reordering that would have saved the afternoon: a PROPFIND on the
    target answers 401 before it looks at the path."""
    http = _Http()
    _pf(http)

    propfinds = [url for method, url, _a in http.calls if method == "PROPFIND"]
    assert propfinds[0].rstrip("/") == ROOT.rstrip("/"), \
        "the first authenticated request was not the root"
    assert TARGET in propfinds[1]


def test_the_network_layer_asks_without_credentials():
    """A 401 there is a *success* for that layer — the host answered. Sending
    credentials would make an auth failure look like a network failure."""
    http = _Http(options=401)
    found = _pf(http)

    options = [(url, auth) for method, url, auth in http.calls
               if method == "OPTIONS"]
    assert options and options[0][1] == ()
    assert found.check("network").ok


def test_a_401_at_the_root_blames_the_credentials_and_clears_the_path():
    http = _Http(root=401, root_body=UNAUTHORISED)
    found = _pf(http)

    assert found.check("auth").failed
    assert found.check("path").unknown, "the path was never asked about"
    assert found.blocked_at.layer == "auth"
    assert "Zugangsdaten wurden abgelehnt" in found.action
    assert "nicht der Ordner" in found.action


def test_a_401_does_not_cost_a_request_to_the_target():
    http = _Http(root=401)
    _pf(http)

    assert not any(TARGET in url for _m, url, _a in http.calls)


def test_a_404_below_a_good_root_blames_the_path():
    """207 at the root and 404 below it is the path, and the credentials are
    fine — the other half of the split."""
    found = _pf(_Http(root=207, target=404))

    assert found.check("auth").ok
    assert found.check("path").failed
    assert "existiert nicht" in found.action
    assert "geteilter" in found.action, "the share case is not named"


def test_a_403_on_the_target_is_a_permission_not_a_password():
    found = _pf(_Http(target=403))

    assert found.check("path").failed
    assert "keine Leseberechtigung" in found.action
    assert "App-Passwort" not in found.action


# ── a layer that was not reached is unknown ──────────────────────────────────

def test_everything_after_a_failure_is_unknown_not_failed():
    found = _pf(_Http(raises={"options": OSError("no route to host")}))

    assert found.check("network").failed
    for later in ("auth", "path"):
        assert found.check(later).unknown
        assert "nicht geprüft" in found.check(later).detail


def test_an_unknown_layer_is_rendered_as_unchecked():
    rendered = _text(_pf(_Http(root=401)))

    assert "▫️ **path**" in rendered
    assert "❌ **auth**" in rendered
    assert "✅ **config**" in rendered


def test_every_layer_appears_exactly_once_in_the_report():
    """A report that silently omits a layer is the defect this issue is about."""
    for http in (_Http(), _Http(root=401), _Http(target=404),
                 _Http(raises={"options": OSError("down")})):
        rendered = _text(switchdrive.preflight(TARGET, request=http))
        for layer in wp.LAYERS:
            assert rendered.count(f"**{layer}**") == 1, layer


def test_a_missing_configuration_stops_before_touching_the_network(monkeypatch):
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "")
    http = _Http()
    found = switchdrive.preflight(TARGET, request=http)

    assert found.check("config").failed and "PASS" in found.check("config").detail
    assert http.calls == [], "the network was touched although nothing was configured"
    assert ".env.gpustack" in found.action


def test_a_template_secret_is_caught_by_the_layer_that_claims_to_check_config(
        monkeypatch):
    """`<Passwort>` is set, is truthy, passes every "is it configured" test in
    this repo, and fails at the far end of a network call as a 401 about the
    server (2026-09-18 on tei)."""
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "<Passwort>")
    monkeypatch.setattr(config, "unfilled_secrets",
                        lambda: {"SWITCHDRIVE_PASS": "still a template "
                                                     "(<Passwort>) in .env.gpustack"})
    http = _Http()
    found = switchdrive.preflight(TARGET, request=http)

    assert found.check("config").failed
    assert "template" in found.check("config").detail
    assert http.calls == []


# ── the diagnosis names the action, never the secret ─────────────────────────

def test_the_secret_never_appears_in_any_output():
    for http in (_Http(), _Http(root=401), _Http(target=404)):
        found = switchdrive.preflight(TARGET, request=http)
        rendered = _text(found)
        assert SECRET not in rendered
        assert SECRET not in repr(found)
        assert found.secret_chars == len(SECRET)


def test_the_length_is_reported_because_it_distinguishes_a_token():
    rendered = _text(_pf())

    assert f"{len(SECRET)} Zeichen" in rendered


def test_a_secret_from_a_file_names_the_file(monkeypatch):
    monkeypatch.setattr(config, "ENV_SOURCE",
                        {"SWITCHDRIVE_PASS": config.REPO_ROOT / ".env.gpustack"})
    found = _pf(_Http(root=401))

    assert ".env.gpustack" in found.secret_source
    assert ".env.gpustack" in found.action


def test_a_secret_from_the_process_environment_says_so(monkeypatch):
    """The one that bit the share path on 2026-10-03: a value already in
    os.environ wins over every .env file, so a service started with a stale
    password keeps using it and the corrected file looks innocent."""
    monkeypatch.setattr(config, "ENV_SOURCE", {})
    found = _pf(_Http(root=401))

    assert found.secret_source == ""
    assert "Prozess-Umgebung" in found.action
    assert "override=False" in found.action


def test_the_401_advice_names_the_app_password_and_the_restart():
    action = _pf(_Http(root=401)).action

    assert "App-Passwort" in action
    assert "neu starten" in action


def test_the_401_advice_puts_the_account_question_to_a_human():
    """Which address is the right edu-ID account is not something code can
    decide, so the probe reports the username and asks."""
    action = _pf(_Http(root=401)).action

    assert "tobias.hodel@example.ch" in action
    assert "nur ein Mensch" in action


def test_a_browser_check_is_explicitly_not_evidence():
    """"Der Zugriff funktioniert noch" was reported against this very 401: a
    browser has a session, this call uses Basic auth, and the two are not the
    same claim."""
    action = _pf(_Http(root=500)).action

    assert "Browser" in action and "Basic-Auth" in action


# ── success ──────────────────────────────────────────────────────────────────

def test_a_healthy_mailbox_passes_every_layer_and_counts_the_files():
    found = _pf()

    assert found.ok and found.blocked_at is None
    assert found.files == 3, "jpg + JPG + pdf, not the txt and not the collection"
    assert "3 verwertbare" in _text(found)


def test_an_empty_folder_is_not_a_failure():
    """"Nothing to pull" and "cannot reach it" are different answers."""
    empty = """<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
      <d:response><d:href>/remote.php/webdav/x/missiven/</d:href></d:response>
    </d:multistatus>"""
    found = _pf(_Http(target_body=empty))

    assert found.ok and found.files == 0


def test_without_a_folder_the_path_layer_stays_unknown():
    """"Not asked" is not "not there" — the probe is useful for the credentials
    alone, which is the common case."""
    http = _Http()
    found = switchdrive.preflight(None, request=http)

    assert found.check("auth").ok
    assert found.check("path").unknown and found.files is None
    assert not any(TARGET in url for _m, url, _a in http.calls)
    assert "Kein Zielordner" in _text(found)


def test_a_short_folder_name_is_resolved_against_the_remote_dir():
    http = _Http()
    switchdrive.preflight("missiven", request=http)

    assert any("agentic_historian_hotfolder/missiven" in url
               for _m, url, _a in http.calls)


def test_the_probe_never_raises():
    found = _pf(_Http(raises={"root": RuntimeError("boom")}))

    assert found.check("auth").failed and "boom" in found.check("auth").detail


# ── counting ─────────────────────────────────────────────────────────────────

def test_collections_and_foreign_extensions_are_not_counted():
    assert wp.count_entries(MULTISTATUS,
                            exts=switchdrive.INGEST_EXTS) == 3


def test_a_collection_is_skipped_even_with_no_extension_filter():
    """Without a filter the extension can no longer hide the collection, which
    is what made the first version of this pair vacuous: `missiven/` has no
    suffix, so the extension check dropped it and the href-ends-in-slash rule
    was never exercised."""
    assert wp.count_entries(MULTISTATUS) == 4      # the 3 + notizen.txt, not the dir


def test_a_percent_encoded_suffix_is_decoded_before_it_is_read():
    """`%2E` is a dot. Reading the suffix off the raw href misses the extension
    entirely and the file is silently not counted — `Beschr%C3%A4nkung.pdf`
    cannot show this, because its `.pdf` survives either way, which is why the
    first version of this test passed against the un-decoded code."""
    body = """<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">
      <d:response><d:href>/x/scan%2Ejpg</d:href></d:response>
      <d:response><d:href>/x/Beschr%C3%A4nkung%2Epdf</d:href></d:response>
    </d:multistatus>"""

    assert wp.count_entries(body, exts={".jpg", ".pdf"}) == 2


def test_an_unparsable_body_counts_nothing_rather_than_raising():
    assert wp.count_entries("<not xml", exts={".jpg"}) == 0


# ── status_of has one home ───────────────────────────────────────────────────

def test_status_of_distinguishes_no_status_from_a_status():
    """None means the host never answered — a different fact from a code, and a
    caller reading None as 0 or 500 loses it."""
    assert wp.status_of(SimpleNamespace(status_code=401)) == 401
    assert wp.status_of(OSError("timed out")) is None

    class ResourceNotFound(Exception):
        pass
    assert wp.status_of(ResourceNotFound()) == 404


def test_the_share_path_delegates_rather_than_keeping_a_second_copy():
    """Two implementations of "what status was that" would be two answers."""
    from utils import nextcloud

    assert nextcloud._status_of(SimpleNamespace(status_code=403)) == 403
    assert nextcloud._status_of(OSError("dns")) is None
    src = (PKG / "utils" / "nextcloud.py").read_text(encoding="utf-8")
    assert "from webdav_probe import status_of" in src


# ── /pull translates the error ───────────────────────────────────────────────

class _Ctx:
    def __init__(self):
        self.sent = []
        # Authorised caller for the fail-closed gate (#572): the guild owner.
        self.guild = SimpleNamespace(id=1, owner_id=7)
        self.author = SimpleNamespace(id=7, roles=[])

        async def _defer(ephemeral=False):
            pass

        async def _send(content=None, ephemeral=False, **kw):
            self.sent.append(content)
            return SimpleNamespace(id=1, content=content)

        self.defer = _defer
        self.followup = SimpleNamespace(send=_send)


def _cmd(name):
    import bot
    return next(c for c in bot.bot.pending_application_commands
                if c.name == name).callback


def test_pull_preflight_is_registered_and_role_gated():
    import bot
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    start = src.find('name="pull_preflight",')

    assert any(c.name == "pull_preflight"
               for c in bot.bot.pending_application_commands)
    assert start != -1
    assert "@require_role" in src[start:src.find("async def", start)]


def test_pull_preflight_reports_the_layers(monkeypatch):
    # The HTTP stub is the seam, not `preflight` itself: replacing `preflight`
    # with something that calls `_pf` — which calls `preflight` — is infinite
    # recursion, and it is the same self-referential patch that bit #397.
    real = switchdrive.preflight
    monkeypatch.setattr(switchdrive, "preflight",
                        lambda folder=None, **kw: real(folder,
                                                       request=_Http(root=401)))
    ctx = _Ctx()
    asyncio.run(_cmd("pull_preflight")(ctx, "missiven"))
    body = "\n".join(c for c in ctx.sent if c)

    assert "Zugangsdaten wurden abgelehnt" in body
    assert "▫️ **path**" in body
    assert SECRET not in body


def test_pull_translates_a_401_instead_of_passing_it_through(monkeypatch):
    """`/pull` is the path a historian actually takes; the raw status is for
    whoever is debugging."""
    class _Rejected(Exception):
        status_code = 401

        def __str__(self):
            return "received 401 (Unauthorized)"

    def _boom(*a, **k):
        raise _Rejected()

    monkeypatch.setattr(switchdrive, "pull_folder", _boom)
    monkeypatch.setattr(switchdrive, "load_processed", lambda: set())
    ctx = _Ctx()
    asyncio.run(_cmd("pull")(ctx, "missiven", False))
    body = "\n".join(c for c in ctx.sent if c)

    assert "received 401" in body, "the raw status is still shown for debugging"
    assert "Zugangsdaten wurden abgelehnt" in body
    assert "/pull_preflight" in body
    assert SECRET not in body


def test_pull_does_not_invent_advice_for_an_unrelated_error(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(switchdrive, "pull_folder", _boom)
    monkeypatch.setattr(switchdrive, "load_processed", lambda: set())
    ctx = _Ctx()
    asyncio.run(_cmd("pull")(ctx, "missiven", False))
    body = "\n".join(c for c in ctx.sent if c)

    assert "disk full" in body
    assert "App-Passwort" not in body
    assert "/pull_preflight" not in body


def test_is_configured_still_only_claims_presence():
    """The function is not the fix — `preflight` is. Its docstring now says so,
    and quietly turning it into a network call would make every caller pay for
    one."""
    assert switchdrive.is_configured() is True
    doc = switchdrive.is_configured.__doc__ or ""
    assert "present" in doc and "not whether they are accepted" in doc.lower()
