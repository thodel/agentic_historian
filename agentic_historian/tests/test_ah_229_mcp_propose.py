"""#229 (P1-D2): /mcp_propose — probe report → reviewed PR (never hot-load).

Offline. The probe is a constructed ProbeReport; the GitHub session is a fake
that records calls. Run from the repo root:
    pytest agentic_historian/tests/test_ah_229_mcp_propose.py
"""

import base64
import importlib.util
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import mcp_propose                              # noqa: E402
from utils import mcp_probe                     # noqa: E402
from utils.mcp_probe import ProbeReport         # noqa: E402


def _report(tools=("search_persons",), transport="http", contract=True,
            contract_tool="search_persons"):
    return ProbeReport(
        url="https://tei.dh.unibe.ch/mcp/ssrq", transport=transport,
        server_info={"name": "ssrq-mcp", "version": "1"},
        tools=[{"name": t} for t in tools], contract=contract,
        contract_tool=contract_tool, sample=3,
    )


# ── fake GitHub session ───────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeGitHub:
    """Records POSTs; simulates a fresh feature branch off an existing main."""

    def __init__(self):
        self.posts: list[str] = []
        self.blobs: list[str] = []
        self.pull_body: dict | None = None

    def get(self, url, **kw):
        if "/ref/heads/mcp/add-" in url:
            return _Resp(404)                              # feature branch is new
        if "/ref/heads/main" in url:
            return _Resp(200, {"object": {"sha": "BASESHA"}})
        if "/commits/BASESHA" in url:
            return _Resp(200, {"tree": {"sha": "BASETREE"}})
        return _Resp(200, {})

    def post(self, url, **kw):
        self.posts.append(url)
        body = kw.get("json", {})
        if url.endswith("/blobs"):
            self.blobs.append(base64.b64decode(body["content"]).decode("utf-8"))
            return _Resp(201, {"sha": "BLOBSHA"})
        if url.endswith("/trees"):
            return _Resp(201, {"sha": "TREESHA"})
        if url.endswith("/commits"):
            return _Resp(201, {"sha": "NEWSHA",
                               "html_url": "https://github.com/x/commit/NEWSHA"})
        if url.endswith("/refs"):
            return _Resp(201, {})
        if url.endswith("/pulls"):
            self.pull_body = body
            return _Resp(201, {"html_url": "https://github.com/thodel/agentic_historian/pull/999"})
        return _Resp(201, {})

    def patch(self, url, **kw):                             # pragma: no cover
        return _Resp(200, {})


# ── guardrails ────────────────────────────────────────────────────────────────

def test_guardrail_http_refused():
    msg = mcp_propose.check_guardrails("ssrq", "http://tei/mcp/ssrq", _report())
    assert msg and "https" in msg.lower()


def test_guardrail_no_tools_refused():
    msg = mcp_propose.check_guardrails("ssrq", "https://tei/mcp/ssrq",
                                       _report(tools=(), transport=None))
    assert msg is not None


def test_guardrail_duplicate_name_refused():
    # 'hls' is a live registry source
    msg = mcp_propose.check_guardrails("hls", "https://tei/mcp/hls", _report())
    assert msg is not None and "hls" in msg


def test_guardrails_pass_for_new_valid_source():
    assert mcp_propose.check_guardrails("brandnew", "https://tei/mcp/brandnew", _report()) is None


# ── deterministic branch name ─────────────────────────────────────────────────

def test_branch_name_deterministic():
    assert mcp_propose.branch_name("ssrq") == "mcp/add-ssrq"


# ── registry patch stays importable + validates ───────────────────────────────

def test_patched_registry_imports_and_validates(tmp_path):
    from knowledge_hub import mcp_registry as reg
    snippet = mcp_probe.registry_snippet("ssrq", "https://tei.dh.unibe.ch/mcp/ssrq", _report())
    patched = mcp_propose.patch_registry(Path(reg.__file__).read_text(encoding="utf-8"), snippet)

    p = tmp_path / "patched_registry.py"
    p.write_text(patched, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("patched_registry", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["patched_registry"] = mod       # @dataclass resolves cls.__module__
    try:
        spec.loader.exec_module(mod)            # must not raise
        assert any(s.name == "ssrq" for s in mod.SOURCES)   # new source present
        mod.validate()                          # invariants hold
    finally:
        sys.modules.pop("patched_registry", None)


# ── the proposal: commit + PR ─────────────────────────────────────────────────

def test_propose_happy_path_opens_pr_with_snippet_patch():
    from knowledge_hub import mcp_registry as reg
    gh = _FakeGitHub()
    src = Path(reg.__file__).read_text(encoding="utf-8")
    result = mcp_propose.propose("ssrq", "https://tei.dh.unibe.ch/mcp/ssrq", _report(),
                                 registry_text=src, session=gh)

    assert result["ok"] is True
    assert result["branch"] == "mcp/add-ssrq"
    assert "pull/999" in result["pr_url"]
    # exactly one blob committed — the patched registry containing the new source
    assert len(gh.blobs) == 1
    assert 'name="ssrq"' in gh.blobs[0] and "full_url=" in gh.blobs[0]
    # PR opened head=mcp/add-ssrq → base=main, body carries the probe report
    assert gh.pull_body["head"] == "mcp/add-ssrq" and gh.pull_body["base"] == "main"
    assert "search_persons" in gh.pull_body["body"]


def test_propose_refused_before_any_github_call_on_guardrail():
    gh = _FakeGitHub()
    result = mcp_propose.propose("hls", "https://tei/mcp/hls", _report(),
                                 registry_text="x", session=gh)
    assert result["ok"] is False and gh.posts == []        # no commit, no PR


# ── command registration + role gating (pattern of test_ah_248) ───────────────

def test_mcp_propose_command_registered_with_options():
    import bot as bot_module
    cmd = next((c for c in bot_module.bot.pending_application_commands
                if getattr(c, "name", None) == "mcp_propose"), None)
    assert cmd is not None, "/mcp_propose must be registered"
    assert [o.name for o in cmd.options] == ["name", "url"]


def test_mcp_propose_is_role_gated():
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    idx = src.find('name="mcp_propose"')
    assert idx != -1
    # @require_role sits between the slash_command decorator and the function def
    after = src[idx:idx + 400]
    assert "@require_role" in after
    # and the registered callback is the require_role wrapper
    import bot as bot_module
    cmd = next(c for c in bot_module.bot.pending_application_commands
               if getattr(c, "name", None) == "mcp_propose")
    assert hasattr(cmd.callback, "__wrapped__")
