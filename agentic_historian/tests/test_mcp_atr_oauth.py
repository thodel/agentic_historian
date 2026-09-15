"""The authorization server: what it grants, and above all what it does not.

The claude.ai web connector cannot send a static bearer token — its dialog takes
a URL and, under Advanced settings, an OAuth client id and secret. Given a 401 it
runs the MCP discovery flow, and against a server without OAuth it fails at the
first step. On 2026-09-15 it said so verbatim: *Registrierung beim Anmeldedienst
von ATR-TEI-MCP fehlgeschlagen* — dynamic client registration with nothing to
register against.

The MCP SDK implements every endpoint. What it cannot supply is **who is allowed
in**, and that is the whole of `mcp_atr/oauth.py`. So that is what this file is
about: an authorization server without a login hands tokens to whoever asks,
which is an open door with extra steps — worse than the bearer token it replaces,
because it looks authenticated.

Offline and pure: no HTTP, no browser. The wire-level flow (metadata, DCR,
PKCE, the code exchange, a real MCP call) was driven against a running server
before this was committed; these are the properties that must not regress.
"""

import asyncio
import json
import time
from pathlib import Path

import pytest

import sys
PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from mcp.server.auth.provider import (             # noqa: E402
    AuthorizationParams,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull   # noqa: E402

from mcp_atr.oauth import (                        # noqa: E402
    ACCESS_TTL_S,
    CODE_TTL_S,
    AtrAuthProvider,
    ConfigError,
    password_from_env,
)

PASSWORD = "a-password-of-sufficient-length"
BASE = "https://tei.dh.unibe.ch/mcp/atr"
RESOURCE = f"{BASE}/mcp"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def provider(tmp_path):
    return AtrAuthProvider(public_base=BASE, store_path=tmp_path / "oauth.json",
                           password=PASSWORD)


@pytest.fixture
def client():
    return OAuthClientInformationFull(
        client_id="c-1",
        redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )


def _params(state="st-1", resource=RESOURCE):
    return AuthorizationParams(
        state=state, scopes=None, code_challenge="chal",
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        redirect_uri_provided_explicitly=True, resource=resource,
    )


# ── the password ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["", "short", "x" * 15])
def test_a_missing_or_short_password_refuses_to_start(monkeypatch, value):
    """It is the only thing between the public internet and this host's GPUs. A
    deployment accident has to fail at boot, not serve an open door."""
    monkeypatch.setenv("ATR_MCP_PASSWORD", value)
    with pytest.raises(ConfigError):
        password_from_env()


def test_a_long_enough_password_is_taken_as_given(monkeypatch):
    monkeypatch.setenv("ATR_MCP_PASSWORD", PASSWORD)
    assert password_from_env() == PASSWORD


@pytest.mark.parametrize("attempt", ["", "wrong", PASSWORD[:-1], PASSWORD + "x", None])
def test_only_the_password_is_the_password(provider, attempt):
    assert provider.password_ok(attempt) is False


def test_the_password_is_the_password(provider):
    assert provider.password_ok(PASSWORD) is True


# ── /authorize grants nothing ────────────────────────────────────────────────

def test_authorize_returns_a_login_page_not_a_code(provider, client):
    """The shape of the whole endpoint: nothing is granted here. A server that
    mints a code at /authorize is an authorization server with no authorization."""
    url = run(provider.authorize(client, _params()))
    assert url.startswith(f"{BASE}/login?rid=")


def test_the_login_url_is_public(provider, client):
    """The browser is on the far side of nginx. A relative or loopback URL here
    produces a flow that looks like it works and lands the user nowhere."""
    url = run(provider.authorize(client, _params()))
    assert url.startswith("https://")
    assert "127.0.0.1" not in url and "localhost" not in url


def test_a_pending_request_survives_until_it_is_used(provider, client):
    rid = run(provider.authorize(client, _params())).split("rid=")[1]
    assert provider.pending(rid) is not None


def test_an_unknown_request_id_is_not_a_login(provider):
    assert provider.pending("nope") is None
    assert provider.complete_login("nope") is None


def test_an_expired_request_cannot_be_completed(provider, client, monkeypatch):
    rid = run(provider.authorize(client, _params())).split("rid=")[1]
    later = time.time() + CODE_TTL_S + 1
    monkeypatch.setattr("mcp_atr.oauth.time.time", lambda: later)
    assert provider.pending(rid) is None
    assert provider.complete_login(rid) is None


def test_completing_a_login_redirects_back_with_code_and_state(provider, client):
    rid = run(provider.authorize(client, _params(state="st-xyz"))).split("rid=")[1]
    target = provider.complete_login(rid)
    assert target.startswith("https://claude.ai/api/mcp/auth_callback?")
    assert "code=" in target and "state=st-xyz" in target


def test_a_request_without_state_does_not_invent_one(provider, client):
    rid = run(provider.authorize(client, _params(state=None))).split("rid=")[1]
    assert "state=" not in provider.complete_login(rid)


def test_a_login_is_one_shot(provider, client):
    """A replayed form post must get nothing. Otherwise one captured request id
    mints codes for as long as it is remembered."""
    rid = run(provider.authorize(client, _params())).split("rid=")[1]
    assert provider.complete_login(rid) is not None
    assert provider.complete_login(rid) is None


# ── the code ─────────────────────────────────────────────────────────────────

def _code_for(provider, client, resource=RESOURCE):
    rid = run(provider.authorize(client, _params(resource=resource))).split("rid=")[1]
    target = provider.complete_login(rid)
    return target.split("code=")[1].split("&")[0]


def test_a_code_carries_the_challenge_and_the_resource(provider, client):
    code = run(provider.load_authorization_code(client, _code_for(provider, client)))
    assert code.code_challenge == "chal"
    assert code.resource == RESOURCE


def test_a_code_belongs_to_the_client_that_asked_for_it(provider, client):
    code = _code_for(provider, client)
    other = client.model_copy(update={"client_id": "c-2"})
    assert run(provider.load_authorization_code(other, code)) is None


def test_a_code_is_single_use(provider, client):
    """PKCE does not stop a replay of a code that leaked with its verifier; only
    spending the code does."""
    code = run(provider.load_authorization_code(client, _code_for(provider, client)))
    run(provider.exchange_authorization_code(client, code))
    with pytest.raises(TokenError):
        run(provider.exchange_authorization_code(client, code))


def test_an_expired_code_is_not_loadable(provider, client, monkeypatch):
    code = _code_for(provider, client)
    # The value has to be computed *before* patching: `mcp_atr.oauth.time` is the
    # stdlib module itself, so a lambda calling time.time() would call itself.
    later = time.time() + CODE_TTL_S + 1
    monkeypatch.setattr("mcp_atr.oauth.time.time", lambda: later)
    assert run(provider.load_authorization_code(client, code)) is None


# ── tokens ───────────────────────────────────────────────────────────────────

def test_an_exchange_yields_an_access_and_a_refresh_token(provider, client):
    code = run(provider.load_authorization_code(client, _code_for(provider, client)))
    token = run(provider.exchange_authorization_code(client, code))
    assert token.token_type == "Bearer"
    assert token.expires_in == ACCESS_TTL_S
    assert token.refresh_token
    assert run(provider.load_access_token(token.access_token)) is not None


def test_the_token_remembers_which_resource_it_is_for(provider, client):
    """`validate_token_resource` refuses a token minted for another endpoint —
    which it can only do if the resource travels from the request to the token."""
    code = run(provider.load_authorization_code(client, _code_for(provider, client)))
    issued = run(provider.exchange_authorization_code(client, code))
    assert run(provider.load_access_token(issued.access_token)).resource == RESOURCE


def test_an_expired_access_token_stops_working(provider, client, monkeypatch):
    code = run(provider.load_authorization_code(client, _code_for(provider, client)))
    issued = run(provider.exchange_authorization_code(client, code))
    later = time.time() + ACCESS_TTL_S + 1
    monkeypatch.setattr("mcp_atr.oauth.time.time", lambda: later)
    assert run(provider.load_access_token(issued.access_token)) is None


def test_an_unknown_token_is_not_a_token(provider):
    assert run(provider.load_access_token("made-up")) is None


def test_refreshing_rotates_the_refresh_token(provider, client):
    """A stolen refresh token is then worth one window, and its use is visible as
    the operator's own session breaking."""
    code = run(provider.load_authorization_code(client, _code_for(provider, client)))
    first = run(provider.exchange_authorization_code(client, code))

    old = run(provider.load_refresh_token(client, first.refresh_token))
    second = run(provider.exchange_refresh_token(client, old))

    assert second.access_token != first.access_token
    assert second.refresh_token != first.refresh_token
    assert run(provider.load_refresh_token(client, first.refresh_token)) is None


def test_a_refresh_token_belongs_to_its_client(provider, client):
    code = run(provider.load_authorization_code(client, _code_for(provider, client)))
    issued = run(provider.exchange_authorization_code(client, code))
    other = client.model_copy(update={"client_id": "c-2"})
    assert run(provider.load_refresh_token(other, issued.refresh_token)) is None


def test_revoking_a_token_takes_it_out(provider, client):
    code = run(provider.load_authorization_code(client, _code_for(provider, client)))
    issued = run(provider.exchange_authorization_code(client, code))
    access = run(provider.load_access_token(issued.access_token))
    run(provider.revoke_token(access))
    assert run(provider.load_access_token(issued.access_token)) is None


# ── the static token, for clients that can send a header ─────────────────────

def test_a_seeded_token_is_a_real_token(provider):
    """The Claude Code CLI takes one with --header. Making it an entry in the
    store rather than a second check in front of the door keeps one place
    deciding whether a request is authorised."""
    provider.seed_static_token("s" * 40, RESOURCE)
    token = run(provider.load_access_token("s" * 40))
    assert token is not None and token.resource == RESOURCE and token.expires_at is None


def test_a_seeded_token_is_not_written_to_disk(provider, tmp_path):
    """It is configuration. Persisting it would only be a second place to leak it
    from, and it is re-seeded from the environment at every start."""
    provider.seed_static_token("s" * 40, RESOURCE)
    provider._save()
    stored = json.loads((tmp_path / "oauth.json").read_text())
    assert all(t["token"] != "s" * 40 for t in stored["access"])


# ── persistence ──────────────────────────────────────────────────────────────

def test_clients_and_tokens_survive_a_restart(tmp_path, client):
    """A restart of the service must not log the operator out, and must not
    invalidate a connector that took a browser round trip to set up."""
    store = tmp_path / "oauth.json"
    first = AtrAuthProvider(BASE, store, PASSWORD)
    run(first.register_client(client))
    code = run(first.load_authorization_code(client, _code_for(first, client)))
    issued = run(first.exchange_authorization_code(client, code))

    second = AtrAuthProvider(BASE, store, PASSWORD)
    assert run(second.get_client(client.client_id)) is not None
    assert run(second.load_access_token(issued.access_token)) is not None


def test_codes_do_not_survive_a_restart(tmp_path, client):
    """Deliberate: they live five minutes and losing one costs a retry. Writing
    them to disk would be a credential at rest for no benefit."""
    store = tmp_path / "oauth.json"
    first = AtrAuthProvider(BASE, store, PASSWORD)
    run(first.register_client(client))
    code = _code_for(first, client)
    first._save()

    second = AtrAuthProvider(BASE, store, PASSWORD)
    assert run(second.load_authorization_code(client, code)) is None


def test_the_store_is_not_world_readable(tmp_path, client):
    store = tmp_path / "oauth.json"
    provider = AtrAuthProvider(BASE, store, PASSWORD)
    run(provider.register_client(client))
    assert store.stat().st_mode & 0o077 == 0, "it holds live credentials"


def test_a_corrupt_store_costs_a_reauthorisation_not_the_endpoint(tmp_path):
    """Refusing to start over an unreadable file would turn a recoverable fault
    into an outage."""
    store = tmp_path / "oauth.json"
    store.write_text("{not json")
    provider = AtrAuthProvider(BASE, store, PASSWORD)
    assert run(provider.get_client("anything")) is None


def test_expired_entries_are_not_reloaded(tmp_path, client):
    store = tmp_path / "oauth.json"
    provider = AtrAuthProvider(BASE, store, PASSWORD)
    run(provider.register_client(client))
    code = run(provider.load_authorization_code(client, _code_for(provider, client)))
    issued = run(provider.exchange_authorization_code(client, code))

    raw = json.loads(store.read_text())
    for entry in raw["access"] + raw["refresh"]:
        entry["expires_at"] = int(time.time()) - 10
    store.write_text(json.dumps(raw))

    revived = AtrAuthProvider(BASE, store, PASSWORD)
    assert run(revived.load_access_token(issued.access_token)) is None
