"""Tests for SEC-8 (#579): the MCP login page — reflected XSS and no rate limit.

The public ``/login`` page put ``rid`` into ``value="{rid}"`` unescaped and
rendered the form even for a POST with a wrong password and an arbitrary/expired
``rid`` — so a foreign form could get this origin to render attacker markup around
a password field. There was also no throttle on the password form guarding a
two-A40 host. The fix: render the form only for a real pending ``rid``, escape it,
and rate-limit the POST.

Two layers: pure provider throttle tests (deterministic with an injected clock),
and route tests driven through the real ASGI app with Starlette's TestClient
(offline — no network, loopback only). The module-level ``app`` is not built
because ``ATR_MCP_PASSWORD`` is unset during tests; the app is assembled here.
"""

import asyncio
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from mcp.server.auth.provider import AuthorizationParams            # noqa: E402
from mcp.server.auth.settings import (                             # noqa: E402
    AuthSettings, ClientRegistrationOptions, RevocationOptions)
from mcp.shared.auth import OAuthClientInformationFull             # noqa: E402

from mcp_atr import server as srv                                  # noqa: E402
from mcp_atr.oauth import (                                        # noqa: E402
    LOGIN_LOCKOUT_S, LOGIN_MAX_FAILS, LOGIN_WINDOW_S, AtrAuthProvider)

BASE = "https://tei.dh.unibe.ch/mcp/atr"
PASSWORD = "a-password-of-sufficient-length"


# ── the throttle, as pure provider state (SEC-8 point 2) ─────────────────────

def _provider(tmp_path):
    return AtrAuthProvider(public_base=BASE, store_path=tmp_path / "o.json", password=PASSWORD)


def test_a_fresh_provider_is_not_locked(tmp_path):
    assert _provider(tmp_path).login_locked_for(now=1000.0) == 0.0


def test_lock_trips_after_max_failures_in_the_window(tmp_path):
    p = _provider(tmp_path)
    for i in range(LOGIN_MAX_FAILS - 1):
        p.note_login_failure(now=1000.0 + i)
    assert p.login_locked_for(now=1000.0) == 0.0           # still allowed
    p.note_login_failure(now=1000.0 + LOGIN_MAX_FAILS)      # the one that trips it
    assert p.login_locked_for(now=1000.0 + LOGIN_MAX_FAILS) == pytest.approx(LOGIN_LOCKOUT_S)


def test_old_failures_fall_out_of_the_window(tmp_path):
    p = _provider(tmp_path)
    for i in range(LOGIN_MAX_FAILS - 1):
        p.note_login_failure(now=1000.0 + i)
    # A failure well after the window: the earlier ones no longer count, so this
    # does not trip the lock.
    p.note_login_failure(now=1000.0 + LOGIN_WINDOW_S + 100)
    assert p.login_locked_for(now=1000.0 + LOGIN_WINDOW_S + 100) == 0.0


def test_success_clears_the_throttle(tmp_path):
    p = _provider(tmp_path)
    for i in range(LOGIN_MAX_FAILS):
        p.note_login_failure(now=1000.0 + i)
    assert p.login_locked_for(now=1000.0 + LOGIN_MAX_FAILS) > 0
    p.note_login_success()
    assert p.login_locked_for(now=1_000_000.0) == 0.0


# ── the route, through the real ASGI app ─────────────────────────────────────

def _app_and_provider(tmp_path):
    provider = _provider(tmp_path)
    settings = AuthSettings(
        issuer_url=BASE, resource_server_url=f"{BASE}/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True),
        validate_token_resource=True)
    app = srv.build_server(provider, settings).streamable_http_app()
    return app, provider


def _client(app):
    from starlette.testclient import TestClient
    return TestClient(app)


def _pending_rid(provider):
    client = OAuthClientInformationFull(
        client_id="c1", redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
        grant_types=["authorization_code"], response_types=["code"],
        token_endpoint_auth_method="none")
    params = AuthorizationParams(
        state="s", scopes=None, code_challenge="chal",
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        redirect_uri_provided_explicitly=True, resource=f"{BASE}/mcp")
    return asyncio.run(provider.authorize(client, params)).split("rid=")[1]


def test_unknown_rid_does_not_render_the_form(tmp_path):
    app, _ = _app_and_provider(tmp_path)
    r = _client(app).get("/login?rid=nope")
    assert r.status_code == 400
    assert 'name="rid"' not in r.text


def test_a_pending_rid_renders_the_form(tmp_path):
    app, provider = _app_and_provider(tmp_path)
    r = _client(app).get("/login?rid=" + _pending_rid(provider))
    assert r.status_code == 200 and 'name="rid"' in r.text


def test_an_xss_payload_rid_is_never_reflected(tmp_path):
    """The core reflected-XSS fix: an arbitrary rid is not a pending request, so
    the form never renders and the payload never reaches the page."""
    app, _ = _app_and_provider(tmp_path)
    r = _client(app).get("/login?rid=%22%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E")
    assert r.status_code == 400
    assert "<script>" not in r.text


def test_the_rid_is_escaped_when_the_form_renders(tmp_path, monkeypatch):
    """Defense in depth: even if a value reaches value="…", it is html-escaped, so
    it cannot break out of the attribute."""
    app, provider = _app_and_provider(tmp_path)
    # Force the form to render for any rid, then feed it a breakout payload.
    monkeypatch.setattr(provider, "pending", lambda rid: object())
    r = _client(app).get('/login?rid=%22%3E%3Cscript%3E')
    assert r.status_code == 200
    assert "<script>" not in r.text          # not raw
    assert "&lt;script&gt;" in r.text or "&quot;" in r.text   # escaped instead


def test_wrong_password_returns_401_and_the_form(tmp_path):
    app, provider = _app_and_provider(tmp_path)
    rid = _pending_rid(provider)
    r = _client(app).post("/login", data={"rid": rid, "password": "wrong"})
    assert r.status_code == 401 and 'name="rid"' in r.text


def test_the_form_locks_out_after_too_many_wrong_passwords(tmp_path):
    app, provider = _app_and_provider(tmp_path)
    rid = _pending_rid(provider)          # survives failures (only success consumes it)
    client = _client(app)
    for _ in range(LOGIN_MAX_FAILS):
        assert client.post("/login", data={"rid": rid, "password": "x"}).status_code == 401
    locked = client.post("/login", data={"rid": rid, "password": "x"})
    assert locked.status_code == 429


def test_the_right_password_redirects_back_to_the_client(tmp_path):
    app, provider = _app_and_provider(tmp_path)
    rid = _pending_rid(provider)
    r = _client(app).post("/login", data={"rid": rid, "password": PASSWORD},
                          follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://claude.ai/api/mcp/auth_callback?")
