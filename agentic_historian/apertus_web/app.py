"""Apertus 1.5 test page: upload an image, ask a question, read the answer.

Published at https://tei.dh.unibe.ch/apertus/ behind nginx (deploy/apertus-web/).
The browser never sees the gateway or its key: this app holds ``ATR_API_KEY`` and
forwards one request shape only — one image, one prompt, to one model — to the
gateway's ``/v1/chat/completions`` on idhefix. That restraint is the point. The
gateway starts GPU work on a shared card; a page that forwarded arbitrary bodies
would be a public door to every model on it.

Access is a shared password (HTTP Basic, ``APERTUS_WEB_PASSWORD``, ≥ 16 chars).
The app refuses to start without one, as the MCP server next door does: a
deployment accident must fail at boot, not serve the GPU to the internet.

One request reaches the model at a time; a second waits, a fourth is turned away
with 429. The first request after the model was evicted is a cold start on
idhefix (one to three minutes); the page says so rather than looking hung.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import io
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, UnidentifiedImageError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

import config

MODEL = os.environ.get("APERTUS_WEB_MODEL", "apertus-1.5-8b")
MAX_PIXELS = 1_960_000          # the model's own processor ceiling
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_PROMPT_CHARS = 4000
MAX_TOKENS_CAP = 4096
MAX_WAITING = 3                 # requests allowed to queue behind the running one
#: A cold start on idhefix plus a long answer. nginx's proxy_read_timeout for
#: /apertus/ is set above this (deploy/apertus-web/nginx-apertus.conf).
GATEWAY_TIMEOUT_S = 840.0

INDEX_HTML = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")


class ConfigError(RuntimeError):
    pass


def _password() -> str:
    pw = os.environ.get("APERTUS_WEB_PASSWORD", "")
    if len(pw) < 16:
        raise ConfigError(
            "APERTUS_WEB_PASSWORD is unset or shorter than 16 characters. This page "
            "starts GPU work on idhefix and is reachable from the public internet; "
            "it will not run without one. Generate one with "
            "`python3 -c 'import secrets; print(secrets.token_urlsafe(18))'`."
        )
    return pw


def _authorised(request: Request, password: str) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    _, _, given = decoded.partition(":")      # the user name is not checked
    return hmac.compare_digest(given.encode(), password.encode())


def _same_origin(request: Request) -> bool:
    """A POST must come from this page. Basic credentials are sent by the browser
    on cross-site requests too, so without this any page the user has open could
    spend GPU time in their name."""
    origin = request.headers.get("origin")
    if origin is None:
        return True                           # curl and other non-browser clients
    return urlsplit(origin).netloc == request.headers.get("host", "")


def prepare_image(data: bytes) -> tuple[str, tuple[int, int], tuple[int, int]]:
    """A JPEG data URL within the model's pixel ceiling, plus original and sent size."""
    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGB")
        original = img.size
        area = img.width * img.height
        if area > MAX_PIXELS:
            scale = (MAX_PIXELS / area) ** 0.5
            img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                             Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        sent = img.size
    url = f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode()}"
    return url, original, sent


def build_app(gateway_url: str | None = None, api_key: str | None = None,
              transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    password = _password()
    gateway = (gateway_url or config.ATR_GATEWAY_URL).rstrip("/")
    key = config.ATR_API_KEY if api_key is None else api_key
    headers = {"X-API-Key": key} if key else {}
    slot = asyncio.Semaphore(1)
    waiting = 0

    def guarded(handler):
        async def wrapper(request: Request) -> Response:
            if not _authorised(request, password):
                return Response("Passwort erforderlich.", status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Apertus-Test", charset="UTF-8"'})
            return await handler(request)
        return wrapper

    async def index(request: Request) -> Response:
        return HTMLResponse(INDEX_HTML.replace("{{MODEL}}", MODEL))

    async def status(request: Request) -> Response:
        """Whether the model is resident (warm) or the next call is a cold start."""
        try:
            async with httpx.AsyncClient(timeout=10, headers=headers, transport=transport) as c:
                r = await c.get(f"{gateway}/health")
            r.raise_for_status()
            resident = MODEL in (r.json().get("resident_models") or [])
            return JSONResponse({"gateway": "ok", "model": MODEL, "resident": resident,
                                 "busy": slot.locked(), "waiting": waiting})
        except (httpx.HTTPError, ValueError) as exc:
            return JSONResponse({"gateway": "unreachable", "model": MODEL,
                                 "error": type(exc).__name__}, status_code=502)

    async def ask(request: Request) -> Response:
        nonlocal waiting
        if not _same_origin(request):
            return JSONResponse({"error": "Anfrage von fremder Seite abgelehnt."}, status_code=403)
        length = int(request.headers.get("content-length") or 0)
        if length > MAX_UPLOAD_BYTES + 64 * 1024:
            return JSONResponse({"error": "Bild zu gross (max. 25 MB)."}, status_code=413)
        form = await request.form(max_files=1, max_fields=5)
        upload = form.get("image")
        prompt = str(form.get("prompt") or "").strip()
        thinking = str(form.get("thinking") or "") in ("1", "true", "on")
        try:
            max_tokens = min(max(int(form.get("max_tokens") or 2048), 64), MAX_TOKENS_CAP)
        except ValueError:
            max_tokens = 2048
        if upload is None or not hasattr(upload, "read"):
            return JSONResponse({"error": "Kein Bild hochgeladen."}, status_code=400)
        if not prompt:
            return JSONResponse({"error": "Keine Frage angegeben."}, status_code=400)
        if len(prompt) > MAX_PROMPT_CHARS:
            return JSONResponse({"error": f"Frage zu lang (max. {MAX_PROMPT_CHARS} Zeichen)."},
                                status_code=400)
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": "Bild zu gross (max. 25 MB)."}, status_code=413)
        try:
            url, original, sent = prepare_image(data)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            return JSONResponse({"error": "Datei ist kein lesbares Bild."}, status_code=400)

        body: dict = {
            "model": MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": url}},
                {"type": "text", "text": prompt},
            ]}],
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        if thinking:
            body["chat_template_kwargs"] = {"enable_thinking": True}

        if waiting >= MAX_WAITING:
            return JSONResponse({"error": "Zu viele Anfragen in der Warteschlange. "
                                          "Bitte gleich nochmals versuchen."}, status_code=429)
        # `waiting` counts the requests queued for the slot, not the one holding it.
        waiting += 1
        try:
            await slot.acquire()
        finally:
            waiting -= 1
        try:
            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT_S, headers=headers,
                                         transport=transport) as c:
                r = await c.post(f"{gateway}/v1/chat/completions", json=body)
            elapsed = time.monotonic() - t0
        except httpx.HTTPError as exc:
            return JSONResponse({"error": f"Gateway nicht erreichbar ({type(exc).__name__})."},
                                status_code=502)
        finally:
            slot.release()
        if r.status_code >= 400:
            # The gateway's detail names the cause (model not servable, cold start
            # failed, out of memory); it carries no secret, and hiding it would
            # leave the tester guessing.
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            return JSONResponse({"error": f"Gateway-Fehler {r.status_code}: {str(detail)[:500]}"},
                                status_code=502)
        out = r.json()
        choice = out["choices"][0]
        usage = out.get("usage") or {}
        return JSONResponse({
            "text": choice["message"]["content"],
            "finish_reason": choice.get("finish_reason"),
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "seconds": round(elapsed, 1),
            "original_size": list(original),
            "sent_size": list(sent),
            "model": MODEL,
        })

    return Starlette(routes=[
        Route("/", guarded(index), methods=["GET"]),
        Route("/api/status", guarded(status), methods=["GET"]),
        Route("/api/ask", guarded(ask), methods=["POST"]),
    ])


app = build_app() if os.environ.get("APERTUS_WEB_PASSWORD") else None
