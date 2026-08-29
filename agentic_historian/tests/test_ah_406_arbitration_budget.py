"""#406: fusion's arbitration must not pay for an attempt that cannot finish.

Measured on saa-0428/001r: `fuse` was 51.8s of a 104s page, and the run log showed

    gpt-oss-120b returned empty content (finish=length);
    retrying with doubled budget (4096 → 8192)

The TEXT model is a REASONING model: it spends its budget thinking before writing
content, so 4096 returned nothing and the client retried at double. The page paid
for both. The retry is a correct safety net; it was serving as the normal path.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_406_arbitration_budget.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config     # noqa: E402
import fusion     # noqa: E402


def test_the_default_budget_is_the_one_that_empirically_works():
    """4096 failed and 8192 succeeded on the measured page."""
    assert config.FUSION_ARBITRATE_MAX_TOKENS >= 8192


def test_the_default_llm_asks_for_the_configured_budget(monkeypatch):
    seen = {}

    class _Stub:
        @staticmethod
        def chat_text(prompt, system=None, **kw):
            seen.update(kw)
            return "{}"

    monkeypatch.setitem(sys.modules, "utils.gpustack_client", _Stub)
    fusion._default_llm("frage")
    assert seen.get("max_tokens") == config.FUSION_ARBITRATE_MAX_TOKENS


def test_the_budget_is_configurable(monkeypatch):
    """An operator on a smaller model must be able to lower it."""
    seen = {}

    class _Stub:
        @staticmethod
        def chat_text(prompt, system=None, **kw):
            seen.update(kw)
            return "{}"

    monkeypatch.setitem(sys.modules, "utils.gpustack_client", _Stub)
    monkeypatch.setattr(config, "FUSION_ARBITRATE_MAX_TOKENS", 2048)
    fusion._default_llm("frage")
    assert seen.get("max_tokens") == 2048


def test_the_call_is_attributed_to_fusion(monkeypatch):
    """The run log named this call `[unknown]`, which is why it took a page
    measurement to find where 50s were going."""
    seen = {}

    class _Stub:
        @staticmethod
        def chat_text(prompt, system=None, **kw):
            seen.update(kw)
            return "{}"

    monkeypatch.setitem(sys.modules, "utils.gpustack_client", _Stub)
    fusion._default_llm("frage")
    assert seen.get("agent_name") == "fusion"


# ── the fallback must stay intact ────────────────────────────────────────────

def test_arbitration_failure_still_falls_back_deterministically():
    """The budget change must not make a failed call fatal — #300's vote path is
    the floor, and a page must never be lost to an LLM outage."""
    def boom(prompt):
        raise RuntimeError("gateway down")
    assert fusion._arbitrate([{"idx": 1, "options": {"a": "x"}, "context": "c"}],
                             boom) == {}


def test_empty_slots_never_call_the_llm():
    """A page with no disagreement columns should not pay for arbitration at all."""
    called = []
    fusion._arbitrate([], lambda p: called.append(p) or "{}")
    assert called == []
