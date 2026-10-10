"""Tests for SEC-14 (#585): raw error text must not reach the channel.

bot.py posted ~20 exceptions verbatim as `❌ Error: {e}`, and atr_status passed
the gateway's URL and body through — leaking internal URLs, paths, gateway
responses and (with SEC-10) file contents. A shared helper now posts a generic
line and logs the detail; atr_status._get keeps the URL/body in the log only.

Offline: a stub ctx records what would be sent; atr_status._get is driven with a
fake httpx client (no network).
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_status   # noqa: E402
import bot          # noqa: E402


def _ctx():
    sent = []

    async def _send(content=None, **kw):
        sent.append(content)

    return SimpleNamespace(followup=SimpleNamespace(send=_send), sent=sent)


# ── the shared helper ────────────────────────────────────────────────────────

def test_report_error_hides_the_detail_and_points_to_the_log():
    ctx = _ctx()
    asyncio.run(bot._report_error(ctx, RuntimeError("SECRET_DETAIL_/srv/data/mcp_oauth.json")))
    assert ctx.sent and "SECRET_DETAIL" not in ctx.sent[-1]
    assert "Details siehe Log" in ctx.sent[-1]


def test_report_error_uses_a_note_when_given():
    ctx = _ctx()
    asyncio.run(bot._report_error(ctx, ValueError("x"), note="Pull fehlgeschlagen"))
    assert "Pull fehlgeschlagen" in ctx.sent[-1] and "x" not in ctx.sent[-1].replace("siehe", "")


# ── the sweep is complete in bot.py ──────────────────────────────────────────

def test_no_raw_exception_is_sent_to_the_channel():
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    assert 'followup.send(f"❌ Error: {e}")' not in src
    assert 'followup.send(f"❌ Fehler: {e}")' not in src
    assert '{type(exc).__name__}: {exc}"' not in src          # the old _atr branch
    assert "async def _report_error" in src


# ── atr_status._get no longer leaks the gateway URL or body ──────────────────

class _FakeResp:
    def __init__(self, status, text="", json_data=None):
        self.status_code = status
        self.text = text
        self._json = json_data or {}

    def json(self):
        return self._json


class _FakeClient:
    def __init__(self, resp=None, raise_exc=None):
        self._resp, self._raise = resp, raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None, params=None):
        if self._raise:
            raise self._raise
        return self._resp


def test_get_http_error_is_generic(monkeypatch):
    import httpx
    monkeypatch.setattr(atr_status.config, "ATR_GATEWAY_URL", "https://SECRET-GW.internal:9000")
    monkeypatch.setattr(atr_status.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(raise_exc=httpx.ConnectError("boom")))
    with pytest.raises(atr_status.AtrStatusError) as ei:
        asyncio.run(atr_status._get("/train/jobs"))
    assert "SECRET-GW" not in str(ei.value)


def test_get_4xx_hides_url_and_body(monkeypatch):
    monkeypatch.setattr(atr_status.config, "ATR_GATEWAY_URL", "https://SECRET-GW.internal:9000")
    monkeypatch.setattr(atr_status.httpx, "AsyncClient",
                        lambda **kw: _FakeClient(resp=_FakeResp(500, text="SECRET_BODY_contents")))
    with pytest.raises(atr_status.AtrStatusError) as ei:
        asyncio.run(atr_status._get("/train/jobs"))
    msg = str(ei.value)
    assert "SECRET-GW" not in msg and "SECRET_BODY" not in msg
    assert "500" in msg                 # the bare status is fine to show
