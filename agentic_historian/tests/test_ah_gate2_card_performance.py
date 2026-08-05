"""The Gate-2 card must not recompute its CER matrix on every click.

Measured on tei with a real 9-candidate page: `compare_paths` costs **2.9s** (36
pairs of full-page Levenshtein), and a single toggle triggered it three times —
render_vote_card, build_view, then the re-render — ~8.7s against Discord's **3s**
interaction budget. Every click died with "Diese Interaktion ist fehlgeschlagen".

The cost is O(n²) in candidates, so 2-3 candidates (every earlier fixture) stayed
under 0.2s and hid the bug completely. It only appears on the multi-engine pages
the card exists for.

Asserted by COUNTING cer() calls rather than by timing — deterministic, and it
tests the actual property: identical work is not repeated.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_gate2_card_performance.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config          # noqa: E402
import path_compare    # noqa: E402
from runstate import RunState  # noqa: E402

# 9 candidates = 36 pairs — the size that broke it live.
PATHS = {
    f"p1.jpg:eng{i}/model-{i}": f"lesart {i} " + ("unser fruntlich gruos vor liebe " * 8)
    for i in range(9)
}


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "PREFERENCES_LOG_PATH", tmp_path / "p.jsonl")
    monkeypatch.setattr(config, "PSEUDONYM_SALT_PATH", tmp_path / ".salt")
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", False)
    path_compare.clear_compare_cache()
    return tmp_path


@pytest.fixture
def counted(monkeypatch):
    """Count real CER computations."""
    calls = {"n": 0}
    real = path_compare.cer

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(path_compare, "cer", counting)
    return calls


class _Interaction:
    def __init__(self):
        self.user = SimpleNamespace(id=7, display_name="Anna", name="Anna")
        self.edited, self.views = [], []
        # Model the real interaction lifecycle: a callback defers (marking the
        # response done) and then edits via edit_original_response. A mock that
        # only offers edit_message cannot catch a callback responding too late,
        # which is exactly the 10062 "Unknown interaction" seen live on tei.
        self._done = False
        self.response = SimpleNamespace(
            edit_message=self._edit,
            defer=self._defer,
            is_done=lambda: self._done,
        )

    async def _defer(self):
        self._done = True

    async def edit_original_response(self, *, content=None, view=None):
        await self._edit(content=content, view=view)

    async def _edit(self, *, content=None, view=None):
        self.edited.append(content)
        self.views.append(view)


def test_the_comparison_is_computed_once_for_identical_paths(counted):
    path_compare.compare_paths(PATHS)
    first = counted["n"]
    assert first == 36                      # 9 candidates → 36 pairs

    path_compare.compare_paths(PATHS)
    path_compare.compare_paths(dict(PATHS))  # equal content, different dict object
    assert counted["n"] == first            # cache hit, no recomputation


def test_rendering_and_building_the_view_share_one_computation(counted):
    st = RunState(doc_id="perf")
    path_compare.render_vote_card(st, PATHS)

    async def build():
        return path_compare.build_view(st, PATHS)
    asyncio.run(build())

    assert counted["n"] == 36               # was 72 — one matrix per consumer


def test_a_toggle_click_recomputes_nothing(counted):
    """The regression: a click changes only the SELECTION, never the texts."""
    st = RunState(doc_id="perf")

    async def go():
        view = path_compare.build_view(st, PATHS)
        before = counted["n"]
        btn = next(b for b in view.children
                   if getattr(b, "path", None) == "p1.jpg:eng0/model-0")
        await btn.callback(_Interaction())
        return before

    before = asyncio.run(go())
    assert before == 36                     # the initial build
    assert counted["n"] == before, "a toggle must not recompute the CER matrix"


def test_the_cache_is_bounded():
    """A long-lived bot must not accumulate a matrix per document forever."""
    for i in range(path_compare._COMPARE_CACHE_MAX + 10):
        path_compare.compare_paths({f"a{i}": f"text {i}", f"b{i}": f"other {i}"})
    assert len(path_compare._COMPARE_CACHE) <= path_compare._COMPARE_CACHE_MAX


def test_changed_texts_are_not_served_from_cache(counted):
    """Correctness beats speed: different candidates must be recompared."""
    a = {"x": "eine lesart", "y": "andere lesart"}
    b = {"x": "eine lesart", "y": "voellig andere worte hier"}
    first = path_compare.compare_paths(a)["max_cer"]
    second = path_compare.compare_paths(b)["max_cer"]
    assert first != second
