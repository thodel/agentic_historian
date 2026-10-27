"""The page's diagnose and bring-up (apertus_web/ops.py, /api/diagnose, /api/warmup).

Each failure seen while bringing Apertus up on idhefix (2026-10-26/27) has a
case here: an unreachable gateway, a refused key, a model the gateway does not
list, a card held by an unregistered vLLM, and a cold start that died. The page
must say which one it is, in words, and keep the gateway's own message beside it.
"""

import asyncio
import importlib
import json
import sys
import time

import httpx
import pytest
from starlette.testclient import TestClient

PASSWORD = "a-long-enough-test-password"
AUTH = ("tester", PASSWORD)
MODEL = "apertus-1.5-8b"
SPEC = {"id": MODEL, "hf_repo": "swiss-ai/Apertus-v1.5-8B", "gpu_affinity": 1,
        "vram_mb": 18000, "resident": False}


def _real_pillow():
    # test_ah_108 leaves a MagicMock PIL in sys.modules while pytest collects.
    for name in [n for n in list(sys.modules) if n == "PIL" or n.startswith("PIL.")]:
        if type(sys.modules[name]).__module__.startswith("unittest.mock"):
            del sys.modules[name]
    from PIL import Image
    return Image


@pytest.fixture
def ops():
    _real_pillow()
    import apertus_web.ops as module
    return module


@pytest.fixture
def web():
    _real_pillow()
    import apertus_web.app as module
    if type(module.Image).__module__.startswith("unittest.mock"):
        module = importlib.reload(module)
    return module


class Gateway:
    def __init__(self, *, resident=False, models=(SPEC,), key_ok=True, free_mib=34723,
                 residents=(), holders=(), chat_status=200, chat_detail="", down=False):
        self.resident, self.models, self.key_ok = resident, list(models), key_ok
        self.free_mib, self.residents, self.holders = free_mib, list(residents), list(holders)
        self.chat_status, self.chat_detail, self.down = chat_status, chat_detail, down
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "model_count": 58,
                                             "resident_models": [MODEL] if self.resident else []})
        if not self.key_ok:
            return httpx.Response(401, json={"detail": "missing or invalid X-API-Key"})
        if path == "/models":
            return httpx.Response(200, json={"models": self.models})
        if path == "/gpu":
            return httpx.Response(200, json={
                "cards": [{"index": 1, "memory_free_mib": self.free_mib,
                           "processes": self.holders}],
                "vllm": {"gpu": 1, "residents": [{"id": r} for r in self.residents]}})
        if path == "/v1/chat/completions":
            body = json.loads(request.content)
            assert body["model"] == MODEL
            assert body["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/png")
            if self.chat_status != 200:
                return httpx.Response(self.chat_status, json={"detail": self.chat_detail})
            self.resident = True
            return httpx.Response(200, json={"choices": [{"message": {"content": " Grau. "}}]})
        return httpx.Response(404)


def _diagnose(ops, gw: Gateway, last=None) -> dict:
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(gw)) as c:
            return await ops.diagnose(c, "http://gw", MODEL, last)
    return {c["id"]: c for c in asyncio.run(go())}


def test_all_green_when_loaded(ops):
    checks = _diagnose(ops, Gateway(resident=True))
    assert [c["state"] for c in checks.values()] == ["ok"] * 4
    assert "gpu" not in checks                          # no room needed for a resident model


def test_cold_but_room_is_a_warning_not_an_error(ops):
    checks = _diagnose(ops, Gateway())
    assert checks["loaded"]["state"] == "warn" and "6 min" in checks["loaded"]["detail"]
    assert checks["gpu"]["state"] == "ok" and "22748 MiB nötig" in checks["gpu"]["detail"]


def test_unreachable_gateway_stops_the_checks(ops):
    checks = _diagnose(ops, Gateway(down=True))
    assert list(checks) == ["gateway"]
    assert checks["gateway"]["state"] == "error" and "ConnectError" in checks["gateway"]["detail"]


def test_refused_key_names_the_file_to_fix(ops):
    checks = _diagnose(ops, Gateway(key_ok=False))
    assert checks["key"]["state"] == "error" and "/etc/apertus-web.env" in checks["key"]["detail"]


def test_model_missing_from_the_gateway_points_at_the_serving_pr(ops):
    checks = _diagnose(ops, Gateway(models=[]))
    assert checks["registered"]["state"] == "error"
    assert "serving-atr-inference#234" in checks["registered"]["detail"]


def test_full_card_with_a_resident_to_evict_is_only_a_warning(ops):
    checks = _diagnose(ops, Gateway(free_mib=4586, residents=["qwen3vl-medieval-german-v3"]))
    assert checks["gpu"]["state"] == "warn" and "qwen3vl-medieval-german-v3" in checks["gpu"]["detail"]


def test_full_card_held_by_an_orphan_names_it(ops):
    """2026-10-26: an unregistered VLLM::EngineCore held 30 GB of card 1."""
    orphan = {"pid": 3861326, "used_mib": 30132, "service": "atr-gateway.service",
              "command": "VLLM::EngineCore"}
    checks = _diagnose(ops, Gateway(free_mib=4586, holders=[orphan]))
    assert checks["gpu"]["state"] == "error"
    assert "3861326" in checks["gpu"]["detail"] and "kill" in checks["gpu"]["detail"]


def test_a_foreign_holder_gets_no_kill_advice(ops):
    rag = {"pid": 3711448, "used_mib": 30000, "service": "cron.service",
           "command": "python3 manage.py ingest"}
    checks = _diagnose(ops, Gateway(free_mib=4586, holders=[rag]))
    assert checks["gpu"]["state"] == "error" and "cron.service" in checks["gpu"]["detail"]
    assert "kill" not in checks["gpu"]["detail"]


def test_a_failed_bring_up_shows_in_the_diagnosis(ops):
    last = {"state": "error", "hint": "vLLM ist beim Start abgestürzt.", "error": "Gateway 502: x"}
    checks = _diagnose(ops, Gateway(), last)
    assert checks["warmup"]["state"] == "error" and "Gateway 502" in checks["warmup"]["detail"]


def _warm(ops, gw: Gateway, timeout=60.0):
    w = ops.Warmup()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(gw)) as c:
            await w.run(c, "http://gw", MODEL, timeout)
    asyncio.run(go())
    return w


def test_warmup_reports_the_answer(ops):
    w = _warm(ops, Gateway())
    v = w.view()
    assert v["state"] == "ok" and v["answer"] == "Grau." and v["seconds"] is not None
    assert any("Kaltstart" in line for line in v["log"])


@pytest.mark.parametrize("status,detail,hint", [
    (502, "vLLM process exited (code 1) during startup; see the gateway journal",
     "abgestürzt"),
    (503, "GPU 1 has 4586 MB free, apertus-1.5-8b needs 22748 MB", "kein Platz"),
    (502, "vLLM instance did not become healthy in 900 s", "Startzeit"),
])
def test_warmup_failure_keeps_the_gateways_words_and_adds_a_hint(ops, status, detail, hint):
    v = _warm(ops, Gateway(chat_status=status, chat_detail=detail)).view()
    assert v["state"] == "error"
    assert detail in v["error"] and str(status) in v["error"]
    assert hint in v["hint"]
    assert "journalctl" in v["journal"]


def test_warmup_on_an_unreachable_gateway(ops):
    v = _warm(ops, Gateway(down=True)).view()
    assert v["state"] == "error" and "nicht erreichbar" in v["error"]


# ── through the app ─────────────────────────────────────────────────────────

def _client(web, monkeypatch, gw):
    monkeypatch.setenv("APERTUS_WEB_PASSWORD", PASSWORD)
    app = web.build_app(gateway_url="http://gw", api_key="k", transport=httpx.MockTransport(gw))
    return TestClient(app, base_url="http://tei.test")


@pytest.mark.parametrize("method,path", [("get", "/api/diagnose"), ("get", "/api/warmup"),
                                         ("post", "/api/warmup")])
def test_new_routes_need_the_password(web, monkeypatch, method, path):
    gw = Gateway()
    r = getattr(_client(web, monkeypatch, gw), method)(path)
    assert r.status_code == 401 and gw.calls == []


def test_diagnose_route(web, monkeypatch):
    r = _client(web, monkeypatch, Gateway()).get("/api/diagnose", auth=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert d["model"] == MODEL and {c["id"] for c in d["checks"]} >= {"gateway", "loaded", "gpu"}


def test_warmup_cross_site_is_refused(web, monkeypatch):
    gw = Gateway()
    r = _client(web, monkeypatch, gw).post("/api/warmup", auth=AUTH,
                                           headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and gw.calls == []


def test_warmup_route_runs_to_ready(web, monkeypatch):
    gw = Gateway()
    with _client(web, monkeypatch, gw) as c:          # the context keeps the loop alive
        r = c.post("/api/warmup", auth=AUTH, headers={"origin": "http://tei.test"})
        assert r.status_code == 202 and r.json()["state"] == "running"
        assert c.post("/api/warmup", auth=AUTH).status_code in (202, 409)
        deadline = time.monotonic() + 10
        while (v := c.get("/api/warmup", auth=AUTH).json())["state"] == "running":
            assert time.monotonic() < deadline
            time.sleep(0.05)
    assert v["state"] == "ok" and v["answer"] == "Grau."
    assert "POST /v1/chat/completions" in gw.calls
