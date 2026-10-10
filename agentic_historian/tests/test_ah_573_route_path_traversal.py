"""SEC-2 (#573): a doc_id must not escape the runs directory.

``RunState._path`` interpolates a doc_id — taken straight from a slash command,
an image stem or a folder name — into ``data/runs/<doc_id>.json``. Unchecked, an
id like ``../../x`` made :meth:`RunState.save` write outside ``data/runs/`` (and
let a read reach a neighbouring file). ``/route`` is the Discord-reachable,
unauthenticated path that triggered the write.

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
from runstate import RunState  # noqa: E402


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)
    return tmp_path


TRAVERSALS = ["../evil", "../../evil", "../../../etc/passwd", "/etc/passwd",
              "a/../../evil"]


# ── RunState._path containment ────────────────────────────────────────────────

def test_path_is_contained_for_a_normal_id():
    runs = (config.DATA_DIR / "runs").resolve()
    p = RunState._path("BAT_664")
    assert p.resolve().is_relative_to(runs)
    assert p.name == "BAT_664.json"


@pytest.mark.parametrize("doc_id", TRAVERSALS)
def test_path_rejects_traversal(doc_id):
    with pytest.raises(ValueError):
        RunState._path(doc_id)


@pytest.mark.parametrize("doc_id", TRAVERSALS)
def test_exists_returns_false_without_raising(doc_id):
    # /votes relies on exists() to tell "no such run" from "unsafe id".
    assert RunState.exists(doc_id) is False


def test_save_cannot_escape_runs():
    """The PoC: a traversal doc_id used to write a file above data/runs/."""
    with pytest.raises(ValueError):
        RunState(doc_id="../evil").save()
    assert not (config.DATA_DIR / "evil.json").exists()
    assert list(config.DATA_DIR.glob("*.json")) == []


# ── /route command rejects the id before anything is written ─────────────────

def _route_ctx():
    sent = []

    async def _defer():
        pass

    async def _send(content=None, view=None):
        sent.append(content)
        return SimpleNamespace(id=1, content=content)

    async def _respond(content=None, ephemeral=False):
        sent.append(content)

    return SimpleNamespace(
        # Authorised caller (guild owner) so the #572 gate lets the body run.
        guild=SimpleNamespace(id=1, owner_id=77),
        author=SimpleNamespace(id=77, roles=[]),
        channel=SimpleNamespace(id=9),
        defer=_defer,
        followup=SimpleNamespace(send=_send),
        respond=_respond,
        sent=sent,
    )


def _route_cmd():
    import bot
    return next(c for c in bot.bot.pending_application_commands
                if c.name == "route").callback


@pytest.mark.parametrize("doc_id", ["../../evil", "../x", "u-17__"])
def test_route_rejects_bad_doc_id_and_writes_nothing(doc_id):
    ctx = _route_ctx()
    asyncio.run(_route_cmd()(ctx, doc_id))
    body = "\n".join(c for c in ctx.sent if c)
    assert "Ungültige Dokument-ID" in body
    assert list((config.DATA_DIR / "runs").glob("*.json")) == []
    assert list(config.DATA_DIR.glob("*.json")) == []


def test_route_accepts_a_valid_id():
    ctx = _route_ctx()
    asyncio.run(_route_cmd()(ctx, "BAT_664"))
    body = "\n".join(c for c in ctx.sent if c)
    assert "Ungültige Dokument-ID" not in body


def test_route_validates_the_doc_id_in_source():
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    region = src[src.find("async def route_cmd"):src.find("async def votes_cmd")]
    assert "slug_violation" in region, "/route must validate the doc id up front"
