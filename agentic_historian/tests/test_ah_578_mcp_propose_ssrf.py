"""Tests for SEC-7 (#578): /mcp_propose must not be an SSRF into the private net.

`/mcp_propose` probed a user-supplied URL with the bot's credentials BEFORE any
scheme/host check, and the probe followed 307/308 redirects to arbitrary targets
— including internal ones (127.0.0.1:8300 is the MCP server; idhefix and GPUStack
sit on the VPN). The fix adds `mcp_probe.url_guard` (https + host-in-public-space),
applied before the probe connects and again before any redirect is followed, and
stops the SSE clients from following redirects.

Offline: IP-literal hosts resolve locally (no DNS); hostname cases inject a
resolver. The bot command is driven with a stub ctx; `probe` is replaced with a
recorder so a refused URL is proven to never be probed.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from utils import mcp_probe  # noqa: E402


# ── url_guard: scheme + public-host SSRF check ───────────────────────────────

@pytest.mark.parametrize("url", [
    "https://1.2.3.4/mcp",            # public IP literal
    "https://93.184.216.34/mcp/ssrq",
])
def test_url_guard_allows_public_https(url, monkeypatch):
    monkeypatch.setattr(mcp_probe, "_resolve_ips", lambda host: [host])
    assert mcp_probe.url_guard(url) is None


@pytest.mark.parametrize("url", [
    "http://example.com/mcp",          # not https
    "ftp://example.com/mcp",
    "https://127.0.0.1:8300/mcp",      # loopback — the MCP server
    "https://10.0.0.5/mcp",            # private
    "https://192.168.1.10/mcp",        # private
    "https://169.254.169.254/latest",  # link-local cloud metadata
    "https://[::1]:8300/mcp",          # loopback v6
    "https://0.0.0.0/mcp",             # unspecified
    "https:///nohost",                 # no host
])
def test_url_guard_refuses_unsafe(url):
    # IP-literal / schemeless cases need no DNS; each must be refused.
    assert mcp_probe.url_guard(url) is not None


def test_url_guard_refuses_hostname_resolving_internal(monkeypatch):
    monkeypatch.setattr(mcp_probe, "_resolve_ips", lambda host: ["10.0.0.9"])
    assert mcp_probe.url_guard("https://sneaky.example/mcp") is not None


def test_url_guard_refuses_if_any_resolved_ip_is_internal(monkeypatch):
    """A host that resolves to both a public and a private address is refused —
    the private one is enough to reach the network (DNS-rebinding shape)."""
    monkeypatch.setattr(mcp_probe, "_resolve_ips", lambda host: ["93.184.216.34", "127.0.0.1"])
    assert mcp_probe.url_guard("https://mixed.example/mcp") is not None


def test_url_guard_refuses_when_host_cannot_be_resolved(monkeypatch):
    def _boom(host):
        raise OSError("name or service not known")
    monkeypatch.setattr(mcp_probe, "_resolve_ips", _boom)
    assert mcp_probe.url_guard("https://nope.invalid/mcp") is not None


# ── _safe_redirect: a redirect may not bounce us internally ──────────────────

def test_safe_redirect_allows_trailing_slash_on_same_public_host():
    # Public IP literal, resolves locally — the legit MCP trailing-slash redirect.
    assert mcp_probe._safe_redirect("https://1.2.3.4/mcp", "/mcp/") == "https://1.2.3.4/mcp/"


def test_safe_redirect_blocks_bounce_to_loopback():
    assert mcp_probe._safe_redirect("https://1.2.3.4/mcp", "http://127.0.0.1:8300/") is None
    assert mcp_probe._safe_redirect("https://1.2.3.4/mcp", "https://10.0.0.1/x") is None


def test_safe_redirect_blocks_empty_location():
    assert mcp_probe._safe_redirect("https://1.2.3.4/mcp", "") is None


# ── the probe no longer follows redirects off /sse ───────────────────────────

def test_sse_clients_do_not_follow_redirects():
    src = Path(mcp_probe.__file__).read_text(encoding="utf-8")
    assert "follow_redirects=True" not in src, "an SSE/probe client still follows redirects"
    assert "_safe_redirect(target_url" in src, "the HTTP probe must route redirects through _safe_redirect"


# ── the bot checks the URL BEFORE probing ────────────────────────────────────

def _authorised_ctx(role_id):
    sent = []

    async def _send(content=None, **kw):
        sent.append(content)

    async def _defer(*a, **k):
        pass

    async def _respond(content=None, **kw):
        sent.append(content)

    author = SimpleNamespace(
        id=5, roles=[SimpleNamespace(id=role_id)],
        guild_permissions=SimpleNamespace(administrator=False, manage_guild=False))
    return SimpleNamespace(
        guild=SimpleNamespace(id=1, owner_id=999), author=author,
        defer=_defer, respond=_respond,
        followup=SimpleNamespace(send=_send), sent=sent)


def _mcp_propose_callback():
    import bot
    cmd = next(c for c in bot.bot.pending_application_commands
               if getattr(c, "name", None) == "mcp_propose")
    return cmd.callback


def test_internal_url_is_refused_before_the_probe(monkeypatch):
    import config
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", 7)
    probed = []

    async def _recording_probe(url):
        probed.append(url)
        return mcp_probe.ProbeReport(url=url)

    monkeypatch.setattr(mcp_probe, "probe", _recording_probe)

    ctx = _authorised_ctx(7)
    asyncio.run(_mcp_propose_callback()(ctx, name="evil", url="https://127.0.0.1:8300/mcp"))

    assert probed == [], "an internal URL must never reach the probe"
    assert ctx.sent and "SSRF" in ctx.sent[-1]


def test_public_url_proceeds_to_the_probe(monkeypatch):
    import config
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", 7)
    monkeypatch.setattr(mcp_probe, "_resolve_ips", lambda host: ["93.184.216.34"])
    probed = []

    async def _recording_probe(url):
        probed.append(url)
        return mcp_probe.ProbeReport(url=url)      # no tools → guardrail refusal

    monkeypatch.setattr(mcp_probe, "probe", _recording_probe)

    ctx = _authorised_ctx(7)
    asyncio.run(_mcp_propose_callback()(ctx, name="newsrc", url="https://good.example/mcp"))

    assert probed == ["https://good.example/mcp"], "a public URL must reach the probe"
