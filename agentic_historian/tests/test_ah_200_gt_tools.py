"""training-atr-models#200 — foreign ground truth from the MCP, through the gateway.

Survey anything on GitHub or Zenodo; import only what the register holds; never
public. The gateway is a MockTransport that records what reached it.
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                    # noqa: E402
from mcp_atr import gt                           # noqa: E402

JOB = "20261113T101500Z-weisthuemer"


class Gateway:
    def __init__(self, status: int = 200, answer=None):
        self.status, self.answer, self.seen = status, answer or {"done": True}, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return httpx.Response(self.status, json=self.answer)

    @property
    def transport(self):
        return httpx.MockTransport(self)


@pytest.fixture(autouse=True)
def gateway_config(monkeypatch):
    monkeypatch.setattr(config, "ATR_GATEWAY_URL", "http://130.92.59.240:8200")
    monkeypatch.setattr(config, "ATR_API_KEY", "k")


def test_sources_asks_the_gateway_with_the_key():
    gw = Gateway(answer={"sources": [{"id": "Weisthuemer"}]})
    out = gt.sources(False, transport=gw.transport)
    assert out["ok"] and out["sources"][0]["id"] == "Weisthuemer"
    (req,) = gw.seen
    assert req.url.path == "/train/gt/sources" and req.url.params["check_hf"] == "false"
    assert req.headers["X-API-Key"] == "k"


@pytest.mark.parametrize("url", [
    "https://github.com/UB-Mannheim/Fibeln", "https://zenodo.org/records/11046062",
    "https://doi.org/10.5281/zenodo.11046062", "10.5281/zenodo.11046062"])
def test_survey_forwards_github_and_zenodo(url):
    gw = Gateway(answer={"kind": "github", "licence": {"state": "agree"}})
    out = gt.survey(url, transport=gw.transport)
    assert out["ok"] and json.loads(gw.seen[0].content) == {"url": url}


@pytest.mark.parametrize("url", [
    "http://github.com/a/b", "https://intranet.unibe.ch/x", "file:///etc/passwd",
    "github.com/a/b", "", "https://github.com/a b", "x" * 600])
def test_survey_refuses_anything_else_before_sending(url):
    gw = Gateway()
    out = gt.survey(url, transport=gw.transport)
    assert out["ok"] is False and gw.seen == []


def test_import_sends_a_dry_run_and_never_public():
    gw = Gateway(answer={"id": JOB, "status": "running", "dry_run": True})
    out = gt.start_import("Weisthuemer", transport=gw.transport)
    assert out["ok"] and out["id"] == JOB
    body = json.loads(gw.seen[0].content)
    assert body == {"id": "Weisthuemer", "dry_run": True, "parts": [], "all_parts": False}
    assert "public" not in body


def test_import_passes_zenodo_parts():
    gw = Gateway()
    gt.start_import("reichenau-inkunabeln", False, ["lateinisch"], transport=gw.transport)
    assert json.loads(gw.seen[0].content)["parts"] == ["lateinisch"]


@pytest.mark.parametrize("source_id, parts", [
    ("https://github.com/x/y", None), ("../x", None), ("", None), ("a b", None),
    ("Weisthuemer", ["../etc"]), ("Weisthuemer", ["a b"]), ("Weisthuemer", ["p"] * 11)])
def test_bad_ids_and_parts_never_reach_the_gateway(source_id, parts):
    gw = Gateway()
    out = gt.start_import(source_id, True, parts, transport=gw.transport)
    assert out["ok"] is False and gw.seen == []


def test_the_trainers_refusal_comes_back_as_said():
    gw = Gateway(status=404, answer={"detail": "unknown source 'x'; the register knows: …"})
    out = gt.start_import("x", transport=gw.transport)
    assert out == {"ok": False, "status": 404,
                   "error": "unknown source 'x'; the register knows: …"}


def test_status_checks_the_job_id():
    gw = Gateway(answer={"id": JOB, "status": "completed"})
    assert gt.import_status(JOB, transport=gw.transport)["status"] == "completed"
    assert gw.seen[0].url.path == f"/train/gt/import/{JOB}"
    for bad in ("x", "../" + JOB, JOB + "/..", ""):
        assert gt.import_status(bad, transport=gw.transport)["ok"] is False
    assert len(gw.seen) == 1


def test_an_unreachable_gateway_is_an_answer_not_an_exception():
    def down(request):
        raise httpx.ConnectError("refused", request=request)
    out = gt.sources(transport=httpx.MockTransport(down))
    assert out["ok"] is False and "unreachable" in out["error"]


def test_the_server_offers_the_four_tools_and_no_public_parameter():
    source = (PKG / "mcp_atr" / "server.py").read_text()
    for tool in ("gt_sources", "gt_survey", "gt_import", "gt_import_status"):
        assert f"def {tool}(" in source
    block = source[source.index("def gt_import("):source.index("def gt_import_status(")]
    assert "public:" not in block and "public=" not in block
    assert "never public" in source
