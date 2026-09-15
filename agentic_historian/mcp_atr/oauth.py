"""
mcp_atr/oauth.py — the authorization server claude.ai insists on.

The web connector cannot send a static bearer token. Its dialog takes a URL and,
under Advanced settings, an OAuth client id and secret — nothing else. Given a
401 it does what the MCP spec says: discover the protected-resource metadata,
discover the authorization server, register itself, and run authorization-code
with PKCE. On 2026-09-15 that produced, verbatim:

    Registrierung beim Anmeldedienst von ATR-TEI-MCP fehlgeschlagen.

— dynamic client registration against a server that has no OAuth at all.

So this module is the authorization server. The MCP SDK already implements every
endpoint (`/authorize`, `/token`, `/register`, the two metadata documents); what
it does not and cannot supply is the part that is specific to this deployment:
**who is allowed in, and how they prove it.** That is the whole of what is
written here.

**The login is the point, not a formality.** An authorization server that hands a
token to whoever asks is an open door with extra steps — worse than the bearer
token it replaces, because it looks authenticated. So `/authorize` redirects to a
password form, and no code is minted until that password checks out. One shared
password from the environment, compared in constant time: this endpoint has one
operator, and a user database would be more moving parts than the thing it
guards.

**What is persisted, and what is not.** Registered clients, access tokens and
refresh tokens live in a JSON file, because a restart of the service must not log
the operator out and must not invalidate a connector that took a browser round
trip to set up. Authorization codes stay in memory: they live five minutes, they
are single-use, and losing them to a restart costs one retry.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

__all__ = ["AtrAuthProvider", "PendingLogin", "password_from_env", "ConfigError"]

#: An authorization code's whole life. Long enough for a browser redirect, short
#: enough that one leaking from a log or a referer is worthless by the time
#: anybody reads it. RFC 6749 §4.1.2 recommends ten minutes or less.
CODE_TTL_S = 300

#: How long an access token is good for. A connector refreshes silently, so this
#: is not a usability knob — it bounds how long a stolen token works.
ACCESS_TTL_S = 3600

#: Refresh tokens expire too. A connector nobody has used for a month should have
#: to be re-authorised rather than quietly retaining access to a GPU host.
REFRESH_TTL_S = 30 * 24 * 3600


class ConfigError(RuntimeError):
    """The server is not safe to start."""


def password_from_env() -> str:
    """The one credential. Absent or short is a refusal, not a default.

    Same reasoning as the bearer token it replaces: a deployment accident must
    fail loudly at boot rather than quietly serve an endpoint that starts jobs on
    a machine with two A40s.
    """
    password = os.environ.get("ATR_MCP_PASSWORD", "")
    if len(password) < 16:
        raise ConfigError(
            "ATR_MCP_PASSWORD is unset or shorter than 16 characters. It is the "
            "only thing between the public internet and this host's GPUs. "
            "Generate one with `python3 -c 'import secrets; "
            "print(secrets.token_urlsafe(24))'`."
        )
    return password


class PendingLogin:
    """An authorization request parked while its human types a password."""

    __slots__ = ("client_id", "params", "created_at")

    def __init__(self, client_id: str, params: AuthorizationParams) -> None:
        self.client_id = client_id
        self.params = params
        self.created_at = time.time()

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.created_at > CODE_TTL_S


class AtrAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """Authorization server for one operator and one resource.

    Deliberately not general. There is one password, one resource, and no scopes
    worth distinguishing — every tool this protects is reachable by anyone who
    can reach any of them, so pretending otherwise would be decoration.
    """

    def __init__(self, public_base: str, store_path: Path, password: str) -> None:
        #: Public URL of this server, e.g. https://tei.dh.unibe.ch/mcp/atr —
        #: everything the browser is redirected to has to be absolute and public,
        #: because the browser is on the far side of nginx.
        self.public_base = public_base.rstrip("/")
        self.store_path = Path(store_path)
        self._password = password

        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._access: dict[str, AccessToken] = {}
        self._refresh: dict[str, RefreshToken] = {}
        #: In memory only — see the module docstring.
        self._codes: dict[str, AuthorizationCode] = {}
        self._pending: dict[str, PendingLogin] = {}
        self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.store_path.is_file():
            return
        try:
            raw: dict[str, Any] = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt store costs one re-authorisation. Refusing to start over
            # it would cost the whole endpoint.
            return
        for data in raw.get("clients", []):
            client = OAuthClientInformationFull.model_validate(data)
            self._clients[client.client_id] = client
        now = time.time()
        for data in raw.get("access", []):
            token = AccessToken.model_validate(data)
            if not token.expires_at or token.expires_at > now:
                self._access[token.token] = token
        for data in raw.get("refresh", []):
            token = RefreshToken.model_validate(data)
            if not token.expires_at or token.expires_at > now:
                self._refresh[token.token] = token

    def _save(self) -> None:
        payload = {
            "clients": [c.model_dump(mode="json") for c in self._clients.values()],
            # The seeded static token is configuration, re-read from the
            # environment at every start. Writing it here would make a second
            # place to leak it from and would keep it alive after it was removed
            # from the environment file, which is how it is meant to be revoked.
            "access": [t.model_dump(mode="json") for t in self._access.values()
                       if t.client_id != "static"],
            "refresh": [t.model_dump(mode="json") for t in self._refresh.values()],
        }
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store_path.with_suffix(".part")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.chmod(0o600)          # it holds live credentials
        os.replace(tmp, self.store_path)

    # ── clients ──────────────────────────────────────────────────────────────

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info
        self._save()

    # ── authorization ────────────────────────────────────────────────────────

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Park the request and send the browser to the password form.

        Returning a URL rather than a code is the whole shape of this endpoint:
        nothing is granted here. The code is minted in `complete_login`, after a
        password, and only then does the browser go back to the client.
        """
        self._sweep()
        rid = secrets.token_urlsafe(24)
        self._pending[rid] = PendingLogin(client.client_id, params)
        return f"{self.public_base}/login?{urlencode({'rid': rid})}"

    def password_ok(self, attempt: str) -> bool:
        """Constant time, because a comparison that returns early leaks the
        length and then, attempt by attempt, the password."""
        return secrets.compare_digest(attempt or "", self._password)

    def pending(self, rid: str) -> PendingLogin | None:
        login = self._pending.get(rid or "")
        if login is None or login.expired():
            self._pending.pop(rid, None)
            return None
        return login

    def complete_login(self, rid: str) -> str | None:
        """Mint a code for a pending request and return the client's redirect URL.

        One-shot: the pending request is consumed, so a replayed form post gets
        nothing. Returns None when the request is unknown or has expired.
        """
        login = self._pending.pop(rid or "", None)
        if login is None or login.expired():
            return None

        code = secrets.token_urlsafe(32)          # 256 bits; RFC 6749 asks 128
        params = login.params
        self._codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + CODE_TTL_S,
            client_id=login.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject="operator",
        )
        query = {"code": code}
        if params.state is not None:
            query["state"] = params.state
        separator = "&" if "?" in str(params.redirect_uri) else "?"
        return f"{params.redirect_uri}{separator}{urlencode(query)}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._codes.get(authorization_code)
        if code is None:
            return None
        if code.client_id != client.client_id or code.expires_at < time.time():
            self._codes.pop(authorization_code, None)
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Single use. A code that survived its exchange is a replay waiting to
        # happen, and PKCE alone does not stop one that leaked with its verifier.
        if self._codes.pop(authorization_code.code, None) is None:
            raise TokenError("invalid_grant", "authorization code already used")
        return self._issue(client.client_id, authorization_code.scopes,
                           authorization_code.resource, authorization_code.subject)

    # ── refresh ──────────────────────────────────────────────────────────────

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        token = self._refresh.get(refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        if token.expires_at and token.expires_at < time.time():
            self._refresh.pop(refresh_token, None)
            return None
        return token

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken,
        scopes: list[str] | None = None,
    ) -> OAuthToken:
        # Rotate: the old refresh token dies with its use, so a stolen one is
        # worth a single window and its use is visible as the operator's own
        # session breaking.
        self._refresh.pop(refresh_token.token, None)
        return self._issue(client.client_id, scopes or refresh_token.scopes,
                           refresh_token.resource, refresh_token.subject)

    # ── tokens ───────────────────────────────────────────────────────────────

    def _issue(self, client_id: str, scopes: list[str], resource: str | None,
               subject: str | None) -> OAuthToken:
        now = int(time.time())
        access = AccessToken(token=secrets.token_urlsafe(32), client_id=client_id,
                             scopes=scopes, expires_at=now + ACCESS_TTL_S,
                             resource=resource, subject=subject)
        refresh = RefreshToken(token=secrets.token_urlsafe(32), client_id=client_id,
                               scopes=scopes, expires_at=now + REFRESH_TTL_S,
                               resource=resource, subject=subject)
        self._access[access.token] = access
        self._refresh[refresh.token] = refresh
        self._sweep()
        self._save()
        return OAuthToken(access_token=access.token, token_type="Bearer",
                          expires_in=ACCESS_TTL_S, scope=" ".join(scopes) or None,
                          refresh_token=refresh.token)

    def seed_static_token(self, token: str, resource: str) -> None:
        """Accept one pre-shared token as if it had been issued here.

        For clients that *can* send a header and have no browser — the Claude
        Code CLI takes one with ``--header``. Making it a real entry in the token
        store rather than a second check in front of the door means there is one
        place that decides whether a request is authorised, and the SDK's own
        middleware keeps doing the deciding.

        No expiry: it is configuration, not a session, and it dies when it is
        removed from the environment file. Not persisted, for the same reason —
        it is re-seeded from the environment at every start, and writing it to a
        second file would only be a second place to leak it from.
        """
        self._access[token] = AccessToken(
            token=token, client_id="static", scopes=[], expires_at=None,
            resource=resource, subject="operator",
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        found = self._access.get(token)
        if found is None:
            return None
        if found.expires_at and found.expires_at < time.time():
            self._access.pop(token, None)
            return None
        return found

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._access.pop(token.token, None)
        self._refresh.pop(token.token, None)
        self._save()

    # ── housekeeping ─────────────────────────────────────────────────────────

    def _sweep(self) -> None:
        """Drop what has expired. Called on the paths that already write, so an
        endpoint nobody uses does not accumulate a store nobody prunes."""
        now = time.time()
        for code, entry in list(self._codes.items()):
            if entry.expires_at < now:
                del self._codes[code]
        for rid, pending in list(self._pending.items()):
            if pending.expired(now):
                del self._pending[rid]
        for token, access in list(self._access.items()):
            if access.expires_at and access.expires_at < now:
                del self._access[token]
        for token, refresh in list(self._refresh.items()):
            if refresh.expires_at and refresh.expires_at < now:
                del self._refresh[token]
