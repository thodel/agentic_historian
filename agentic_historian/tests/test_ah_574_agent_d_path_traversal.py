"""SEC-3 (#574): /agent_d corpus_name must not escape the outputs directory.

``corpus_analysis._save`` built ``OUTPUTS_DIR / f"corpus_{name}"`` and called
``mkdir(parents=True)``. The ``corpus_`` prefix blocks a leading ``..`` but not
``x/../../../ziel``, which climbs out of OUTPUTS_DIR. ``/agent_d`` is the
Discord-reachable command that supplies the name.

Offline: no Discord, no network.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config  # noqa: E402
from agents import corpus_analysis  # noqa: E402


@pytest.fixture(autouse=True)
def _outputs_dir(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "OUTPUTS_DIR", out)
    return out


# Real escapes need an internal "/" so a later ".." can climb past the
# corpus_<first-segment> directory. A *leading* ".." is glued to the prefix
# ("corpus_.." — a literal dir name) and stays contained; see the test below.
TRAVERSALS = ["x/../../../ziel", "a/../../b", "x/../../../../etc/passwd"]


def test_out_dir_contained_for_normal_names():
    outputs = config.OUTPUTS_DIR.resolve()
    assert corpus_analysis.corpus_out_dir("default").resolve().is_relative_to(outputs)
    # spaces stay allowed (become underscores), still contained
    d = corpus_analysis.corpus_out_dir("my corpus")
    assert d.resolve().is_relative_to(outputs)
    assert d.name == "corpus_my_corpus"


def test_leading_dotdot_is_neutralised_by_the_prefix():
    """A leading ".." does not escape: "corpus_" + "../ziel" = "corpus_../ziel",
    whose first component is the literal dir "corpus_..", still inside OUTPUTS_DIR."""
    d = corpus_analysis.corpus_out_dir("../ziel")
    assert d.resolve().is_relative_to(config.OUTPUTS_DIR.resolve())


@pytest.mark.parametrize("name", TRAVERSALS)
def test_out_dir_rejects_traversal(name):
    with pytest.raises(ValueError):
        corpus_analysis.corpus_out_dir(name)


def test_save_cannot_escape_outputs():
    """_save refuses an escaping name before writing anything."""
    with pytest.raises(ValueError):
        corpus_analysis._save("x/../../../ziel", {"stats": {}})
    # nothing written above the outputs dir
    assert list(config.OUTPUTS_DIR.parent.glob("ziel*")) == []
    assert list(config.OUTPUTS_DIR.glob("corpus_*")) == []


# ── /agent_d command rejects the name before running anything ────────────────

def _agent_d_ctx():
    sent = []

    async def _defer():
        pass

    async def _send(content=None, view=None, embed=None):
        sent.append(content)
        return SimpleNamespace(id=1, content=content)

    async def _respond(content=None, ephemeral=False):
        sent.append(content)

    return SimpleNamespace(
        guild=SimpleNamespace(id=1, owner_id=77),   # authorised (SEC-1 gate)
        author=SimpleNamespace(id=77, roles=[]),
        channel=SimpleNamespace(id=9),
        defer=_defer,
        followup=SimpleNamespace(send=_send),
        respond=_respond,
        sent=sent,
    )


def _agent_d_cmd():
    import bot
    return next(c for c in bot.bot.pending_application_commands
                if c.name == "agent_d").callback


def test_agent_d_rejects_traversal_and_runs_nothing():
    ctx = _agent_d_ctx()
    asyncio.run(_agent_d_cmd()(ctx, "x/../../../ziel"))
    body = "\n".join(c for c in ctx.sent if c)
    assert "Ungültiger Korpusname" in body
    assert list(config.OUTPUTS_DIR.glob("corpus_*")) == []


def test_agent_d_validates_in_source():
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    region = src[src.find("async def agent_d_cmd"):src.find("async def agent_e_cmd")]
    assert "corpus_out_dir" in region, "/agent_d must validate the corpus name up front"
