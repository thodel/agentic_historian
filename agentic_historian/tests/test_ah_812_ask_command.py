"""/ask and publishing for Agent F (#812).

The agent itself is tested in test_ah_812_argument_agent.py; here, what
happens to an answer afterwards: the files it publishes (Jekyll front matter,
Liquid kept out), whether and why it was committed, the Discord digest, and
the command — that it answers in the channel rather than the expiring
interaction, and that an echoed ``@everyone`` pings nobody.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_812_ask_command.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import bot
import config
import evidence as ev
from agents import argument_agent as af
from utils import publish_github

SPITAL = "Item der spital ze Basel git järlich x ß umb almuosen den armen"


@pytest.fixture(autouse=True)
def _no_federation(monkeypatch):
    """`ask` reads each edition passage in its document (#812); offline, no
    document is fetched and the snippet stands."""
    monkeypatch.setattr(af, "fetch_document", lambda source, doc_id: None)


def _result(question="Wie wird Almosen begründet?", *, claims=1, quote=None,
            bound="almuosen den armen", complete=True):
    it = ev.edition_item("eos", "HGB-0815", quote or SPITAL, datierung="1438")
    llm_claims = [{"claim": f"Behauptung {i}", "evidence": [
        {"id": it.evidence_id, "quote": bound}]} for i in range(claims)]

    def llm(prompt, system, max_tokens):
        import json
        if "Zerlege die Frage" in prompt:
            return json.dumps({"subquestions": [{"text": "q", "terms": []}]}), {}
        if "Passagen (Kennung" in prompt:
            return json.dumps({"claims": llm_claims}), {}
        return json.dumps({"gaps": []}), {}

    def fulltext(query, *, bestand=None, limit=10):
        if not complete:
            raise LookupError("eos: down")
        return [it]

    return af.ask(question, llm=llm, retrievers={"fulltext": fulltext},
                  max_rounds=0, now="2026-11-05T21:00:00+00:00")


# ── the published files ──────────────────────────────────────────────────────

def test_publish_files_are_one_page_and_two_json_under_the_question():
    res = _result()
    files = af.publish_files(res)
    prefix = f"docs/questions/{res.question_id}"
    assert sorted(files) == [f"{prefix}/audit.json", f"{prefix}/index.md",
                             f"{prefix}/pool.json"]
    page = files[f"{prefix}/index.md"].decode("utf-8")
    assert page.startswith("---\nlayout: default\ntitle: ")
    assert "{% raw %}" in page and page.rstrip().endswith("{% endraw %}")
    assert "[audit.json](audit.json)" in page


def test_a_question_with_colons_and_quotes_keeps_valid_front_matter():
    import yaml

    res = _result(question='Was heisst "elemosina": Gabe oder Pflicht?')
    page = af.publish_files(res)[f"docs/questions/{res.question_id}/index.md"]
    front = page.decode("utf-8").split("---")[1]
    assert yaml.safe_load(front)["title"] == 'Frage: Was heisst "elemosina": Gabe oder Pflicht?'


def test_nothing_in_the_body_can_end_the_raw_block():
    # a quote that carries Liquid, including the one tag that would end raw
    nasty = "der {{ brief }} und {% endraw %} {%- endraw -%} almuosen den armen"
    res = _result(quote=nasty, bound=nasty)
    assert res.claims, "the quote must be bound to reach the page"
    page = af.publish_files(res)[f"docs/questions/{res.question_id}/index.md"].decode()
    inner = page.split("{% raw %}", 1)[1].rsplit("{% endraw %}", 1)[0]
    assert "{%" not in inner                     # every tag opener neutralised
    assert "{{ brief }}" in inner                # harmless inside raw, kept as is
    assert "{&#37; endraw %}" in inner           # shown as written


def test_a_question_id_that_is_not_one_never_becomes_a_path():
    for bad in ("../../etc", "q-123", "q-0123456789/../x", ""):
        with pytest.raises(af.QuestionIdRefused):
            af.question_path(bad)


# ── answer(): written always, published when asked and on ────────────────────

@pytest.fixture
def stub_ask(monkeypatch, tmp_path):
    res = _result()
    monkeypatch.setattr(af, "ask", lambda *a, **k: res)
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path)
    return res


def test_unpublished_answer_is_written_and_says_why(stub_ask, tmp_path):
    done = af.answer("q")
    assert (done.local_dir / "answer.md").exists()
    assert done.local_dir.parent == tmp_path / "questions"
    assert done.commit_url is None and done.not_published == "nicht angefordert"


def test_publish_switched_off_is_named(stub_ask, monkeypatch):
    monkeypatch.setattr(publish_github, "is_enabled", lambda: False)
    done = af.answer("q", publish=True)
    assert done.commit_url is None
    assert "ENABLE_GITHUB_PUBLISH" in done.not_published


def test_published_answer_links_its_page(stub_ask, monkeypatch):
    sent = {}
    monkeypatch.setattr(publish_github, "is_enabled", lambda: True)

    def fake_publish(qid, files):
        sent.update(qid=qid, files=files)
        return "https://github.com/o/r/commit/abc"

    monkeypatch.setattr(publish_github, "publish_question", fake_publish)
    monkeypatch.setattr(config, "CATALOGUE_BASE_URL", "https://example.org/out")
    done = af.answer("q", publish=True)
    assert sent["qid"] == stub_ask.question_id
    assert done.commit_url == "https://github.com/o/r/commit/abc"
    assert done.page_url == f"https://example.org/out/questions/{stub_ask.question_id}/"


def test_a_failed_commit_keeps_the_answer(stub_ask, monkeypatch):
    monkeypatch.setattr(publish_github, "is_enabled", lambda: True)
    monkeypatch.setattr(publish_github, "publish_question", lambda q, f: None)
    done = af.answer("q", publish=True)
    assert done.page_url is None and (done.local_dir / "answer.md").exists()
    assert "Commit" in done.not_published


def test_publish_question_is_non_fatal(monkeypatch):
    monkeypatch.setattr(publish_github, "is_enabled", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("502 from GitHub")

    monkeypatch.setattr(publish_github, "_commit_files", boom)
    assert publish_github.publish_question("q-0123456789", {"a": b"x"}) is None
    monkeypatch.setattr(publish_github, "_commit_files",
                        lambda files, msg, session=None: f"url:{msg}")
    assert publish_github.publish_question("q-0123456789", {"a": b"x"}) == \
        "url:Publish answer q-0123456789"
    monkeypatch.setattr(publish_github, "is_enabled", lambda: False)
    assert publish_github.publish_question("q-0123456789", {"a": b"x"}) is None


# ── the Discord digest ───────────────────────────────────────────────────────

def _done(res, **kw):
    return af.Answered(res, Path("/tmp/questions") / res.question_id, **kw)


def test_digest_names_claim_quote_and_kind_of_text():
    res = _result()
    msgs = af.format_discord(_done(res, page_url="https://x/questions/q/"),
                             mention="<@77>")
    text = "\n".join(msgs)
    assert msgs[0].startswith("<@77> 📜 **Wie wird Almosen begründet?**")
    assert "„almuosen den armen“ — HGB-0815 · Editionstext eos" in text
    assert "Ganze Antwort mit allen Belegen: https://x/questions/q/" in text


def test_digest_splits_under_the_limit_and_loses_nothing():
    res = _result(claims=30)
    msgs = af.format_discord(_done(res, not_published="nicht angefordert"))
    assert len(msgs) > 1
    assert all(len(m) <= 1900 for m in msgs)
    assert sum(m.count("Behauptung ") for m in msgs) == 30


def test_digest_says_incomplete_and_unpublished():
    res = _result(complete=False)
    text = "\n".join(af.format_discord(_done(res, not_published="nicht angefordert")))
    assert "**Unvollständig**" in text
    assert "Keine Behauptung" in text
    assert "Nicht publiziert (nicht angefordert)" in text


# ── the command ──────────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self):
        self.followups, self.channel_msgs = [], []
        self.guild = SimpleNamespace(id=1, owner_id=77)    # owner passes the gate
        self.author = SimpleNamespace(id=77, roles=[], mention="<@77>")

        async def _defer(*a, **k):
            pass

        async def _followup(content=None, **k):
            self.followups.append(content)

        async def _channel(content=None, **k):
            self.channel_msgs.append((content, k.get("allowed_mentions")))

        self.defer = _defer
        self.followup = SimpleNamespace(send=_followup)
        self.channel = SimpleNamespace(id=5, send=_channel)


def _ask_callback():
    return next(c for c in bot.bot.pending_application_commands
                if c.name == "ask").callback


def test_ask_is_registered_with_its_options():
    cmd = next((c for c in bot.bot.pending_application_commands if c.name == "ask"), None)
    assert cmd is not None
    assert [o.name for o in cmd.options] == ["frage", "bestand", "von", "bis"]


def test_ask_acknowledges_then_answers_in_the_channel(monkeypatch):
    res = _result()
    calls = {}

    def fake_answer(question, **kw):
        calls.update(question=question, **kw)
        return _done(res, page_url="https://x/q/")

    monkeypatch.setattr(af, "answer", fake_answer)
    ctx = _Ctx()
    asyncio.run(_ask_callback()(ctx, "Wer gibt @everyone Almosen?", "kf", 1400, None))

    assert calls == {"question": "Wer gibt @everyone Almosen?", "bestand": "kf",
                     "year_from": 1400, "year_to": None, "publish": True}
    assert len(ctx.followups) == 1 and "Frage angenommen" in ctx.followups[0]
    assert ctx.channel_msgs, "the answer goes to the channel"
    content, allowed = ctx.channel_msgs[0]
    assert content.startswith("<@77> 📜")
    assert allowed.everyone is False and allowed.roles is False


def test_ask_failure_says_so_without_the_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("token sk-secret at /etc/agentic_historian.env")

    monkeypatch.setattr(af, "answer", boom)
    ctx = _Ctx()
    asyncio.run(_ask_callback()(ctx, "q", None, None, None))
    content, _ = ctx.channel_msgs[-1]
    assert "liess sich nicht beantworten" in content
    assert "sk-secret" not in content and "/etc/" not in content
