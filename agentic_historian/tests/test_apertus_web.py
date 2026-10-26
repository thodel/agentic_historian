"""The Apertus test page (apertus_web/): what it forwards, and what it refuses.

The page is a public URL in front of a GPU on idhefix. These tests pin the
restraint that makes that acceptable: no password, no start; no credentials, no
page; one model and one request shape to the gateway, whatever the browser sends;
a cross-site POST is refused. The gateway is a mock transport — nothing leaves
the process.
"""

import importlib
import io
import json
import sys

import httpx
import pytest
from starlette.testclient import TestClient

PASSWORD = "a-long-enough-test-password"
AUTH = ("tester", PASSWORD)


def _real_pillow():
    """Pillow, never the MagicMock a sibling module installs while pytest collects.

    ``test_ah_108_hf_ocr_trocr_seq2seq`` puts a mock PIL into ``sys.modules`` at
    import time and hands the real one back only when its own tests are done —
    after this file was collected. Same remedy as ``test_ah_235``: drop the mock
    and resolve Pillow here.
    """
    for name in [n for n in list(sys.modules) if n == "PIL" or n.startswith("PIL.")]:
        if type(sys.modules[name]).__module__.startswith("unittest.mock"):
            del sys.modules[name]
    from PIL import Image
    return Image


@pytest.fixture
def web():
    _real_pillow()
    import apertus_web.app as module
    if type(module.Image).__module__.startswith("unittest.mock"):
        module = importlib.reload(module)
    return module


def _png(w=3000, h=2000) -> bytes:
    buf = io.BytesIO()
    _real_pillow().new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


class Gateway:
    """Records what reached the gateway and answers like vLLM would."""

    def __init__(self, status=200, resident=()):
        self.calls: list[httpx.Request] = []
        self.status = status
        self.resident = list(resident)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "resident_models": self.resident})
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "model failed to start"})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "Ein leeres weisses Blatt."},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7000, "completion_tokens": 6},
        })


@pytest.fixture
def gateway():
    return Gateway()


@pytest.fixture
def client(monkeypatch, gateway, web):
    monkeypatch.setenv("APERTUS_WEB_PASSWORD", PASSWORD)
    app = web.build_app(gateway_url="http://gw.test:8200", api_key="secret-key",
                        transport=httpx.MockTransport(gateway))
    return TestClient(app, base_url="http://tei.test")


def _ask(client, **extra):
    data = {"prompt": "Beschreibe das Bild.", **extra.pop("data", {})}
    files = {"image": ("bild.png", _png(), "image/png")}
    return client.post("/api/ask", data=data, files=files, auth=AUTH, **extra)


@pytest.mark.parametrize("pw", ["", "short"])
def test_refuses_to_start_without_a_real_password(monkeypatch, web, pw):
    monkeypatch.setenv("APERTUS_WEB_PASSWORD", pw)
    with pytest.raises(web.ConfigError):
        web.build_app(gateway_url="http://gw.test", api_key="k")


@pytest.mark.parametrize("path,method", [("/", "get"), ("/api/status", "get"), ("/api/ask", "post")])
def test_every_route_needs_the_password(client, gateway, path, method):
    r = getattr(client, method)(path)
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")
    r = getattr(client, method)(path, auth=("tester", "wrong-password-xxxx"))
    assert r.status_code == 401
    assert gateway.calls == []


def test_page_names_the_model(client):
    r = client.get("/", auth=AUTH)
    assert r.status_code == 200
    assert "apertus-1.5-8b" in r.text and "{{MODEL}}" not in r.text


def test_forwards_one_image_one_prompt_to_one_model(client, gateway, web):
    r = _ask(client, data={"thinking": "1", "max_tokens": "999999"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["text"] == "Ein leeres weisses Blatt."
    assert out["original_size"] == [3000, 2000]
    w, h = out["sent_size"]
    assert w * h <= web.MAX_PIXELS

    (call,) = gateway.calls
    assert call.url.path == "/v1/chat/completions"
    assert call.headers["x-api-key"] == "secret-key"
    body = json.loads(call.content)
    assert body["model"] == "apertus-1.5-8b"
    assert body["max_tokens"] == web.MAX_TOKENS_CAP          # clamped
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    content = body["messages"][0]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert content[1] == {"type": "text", "text": "Beschreibe das Bild."}


def test_the_browser_cannot_choose_the_model(client, gateway):
    _ask(client, data={"model": "some-other-model"})
    assert json.loads(gateway.calls[0].content)["model"] == "apertus-1.5-8b"


def test_cross_site_post_is_refused(client, gateway):
    r = _ask(client, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert gateway.calls == []


def test_same_origin_post_passes(client, gateway):
    r = _ask(client, headers={"origin": "http://tei.test"})
    assert r.status_code == 200


def test_not_an_image_is_a_400_not_a_gateway_call(client, gateway):
    r = client.post("/api/ask", data={"prompt": "x"},
                    files={"image": ("a.png", b"not an image", "image/png")}, auth=AUTH)
    assert r.status_code == 400
    assert gateway.calls == []


def test_empty_prompt_is_refused(client, gateway):
    r = client.post("/api/ask", data={"prompt": "  "},
                    files={"image": ("a.png", _png(10, 10), "image/png")}, auth=AUTH)
    assert r.status_code == 400
    assert gateway.calls == []


def test_gateway_error_is_reported_with_its_detail(monkeypatch, web):
    monkeypatch.setenv("APERTUS_WEB_PASSWORD", PASSWORD)
    app = web.build_app(gateway_url="http://gw.test", api_key="k",
                        transport=httpx.MockTransport(Gateway(status=503)))
    r = _ask(TestClient(app))
    assert r.status_code == 502
    assert "503" in r.json()["error"] and "model failed to start" in r.json()["error"]


@pytest.mark.parametrize("resident,expected", [(["apertus-1.5-8b"], True), ([], False)])
def test_status_says_warm_or_cold(monkeypatch, web, resident, expected):
    monkeypatch.setenv("APERTUS_WEB_PASSWORD", PASSWORD)
    app = web.build_app(gateway_url="http://gw.test", api_key="k",
                        transport=httpx.MockTransport(Gateway(resident=resident)))
    r = TestClient(app).get("/api/status", auth=AUTH)
    assert r.json()["resident"] is expected
