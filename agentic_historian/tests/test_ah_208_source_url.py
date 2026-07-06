"""#208: the source URL is derived, carried in the result, and passed to publish.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_208_source_url.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config  # noqa: E402
import orchestrator as orch  # noqa: E402


def test_derive_prefers_explicit(monkeypatch):
    monkeypatch.setattr(config, "SOURCE_URL_BASE", "https://base")
    assert orch._derive_source_url(Path("x/BAT_1.jpg"), "https://explicit/x") == "https://explicit/x"


def test_derive_from_base(monkeypatch):
    monkeypatch.setattr(config, "SOURCE_URL_BASE", "https://drive/share")
    assert orch._derive_source_url(Path("hot/BAT_1.jpg")) == "https://drive/share/BAT_1.jpg"


def test_derive_none_when_unset(monkeypatch):
    monkeypatch.setattr(config, "SOURCE_URL_BASE", "")
    assert orch._derive_source_url(Path("hot/BAT_1.jpg")) is None


def test_to_json_includes_source_url_only_when_set():
    ctx = orch.PipelineContext("doc1")
    assert "source_url" not in ctx.to_json()
    ctx.source_url = "https://src/doc1.jpg"
    assert ctx.to_json()["source_url"] == "https://src/doc1.jpg"


def test_publish_outputs_passes_source_url(monkeypatch):
    from utils import publish_github
    captured = {}
    monkeypatch.setattr(publish_github, "is_enabled", lambda: True)
    monkeypatch.setattr(publish_github, "publish_doc",
                        lambda doc_id, source_url=None: captured.update(doc=doc_id, url=source_url))
    orch._publish_outputs("doc1", "https://src/doc1.jpg")
    assert captured == {"doc": "doc1", "url": "https://src/doc1.jpg"}


def test_publish_outputs_noop_when_disabled(monkeypatch):
    from utils import publish_github
    called = {"n": 0}
    monkeypatch.setattr(publish_github, "is_enabled", lambda: False)
    monkeypatch.setattr(publish_github, "publish_doc",
                        lambda *a, **k: called.update(n=called["n"] + 1))
    orch._publish_outputs("doc1", "https://src/doc1.jpg")
    assert called["n"] == 0
