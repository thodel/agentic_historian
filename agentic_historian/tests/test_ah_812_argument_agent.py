"""Agent F: questions answered from evidence (#812).

The model is a script and the retrievers return real evidence items: the
BAT_664 page from the #811 fixture (published reading contested, TrOCR
escriptmask reading the same words differently) and edition snippets shaped
like the federation's full-text hits. Nothing here talks to GPUStack, the
passage index or MCP.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_812_argument_agent.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import agent_tools as at
import evidence as ev
import orchestrator
from agents import argument_agent as af

FIX = PKG / "tests" / "fixtures" / "evidence"
ESCRIPT = "trocr/trocr-medieval-escriptmask"

SPITAL = "Item der spital ze Basel git järlich x ß umb almuosen den armen"
ELEMOSINA = "pro elemosina pauperum in hospitali dari solebat"


@pytest.fixture(autouse=True)
def _no_federation(monkeypatch):
    """`ask` reads each edition passage in its document (#812); offline, no
    document is fetched and the snippet stands."""
    monkeypatch.setattr(af, "fetch_document", lambda source, doc_id: None)


@pytest.fixture(scope="module")
def bat_rs():
    p = json.loads((FIX / "BAT_664_r_00027.json").read_text(encoding="utf-8"))
    return ev.reading_set(p["doc_id"], pipeline=p)


@pytest.fixture
def items(bat_rs):
    pub = ev.item_for_span(bat_rs, ev.PUBLISHED, 0, 21)           # contested
    esc = ev.item_for_span(bat_rs, ESCRIPT, 0, 41)
    hgb = ev.edition_item("eos", "HGB-0815", SPITAL, datierung="1438")
    lat = ev.edition_item("eos", "HGB-0907", ELEMOSINA, datierung="1512")
    return {"pub": pub, "esc": esc, "hgb": hgb, "lat": lat}


def _pool(*its):
    return {i.evidence_id: i for i in its}


class ScriptedLLM:
    """Answers by stage; records every prompt. ``usage`` per call."""

    def __init__(self, *, plan=None, binds=(), reflects=(), usage=None, fail=()):
        self.plan = plan if plan is not None else {
            "subquestions": [{"text": "Wer gibt Almosen?", "terms": ["almuosen"]},
                             {"text": "Wem?", "terms": ["armen"]}],
            "period": {"from": 1400, "to": 1499}, "languages": ["de"]}
        self.binds, self.reflects = list(binds), list(reflects)
        self.usage = usage if usage is not None else {"prompt_tokens": 100,
                                                      "completion_tokens": 50}
        self.fail = set(fail)
        self.prompts = []

    def __call__(self, prompt, system, max_tokens):
        self.prompts.append(prompt)
        stage = ("plan" if "Zerlege die Frage" in prompt else
                 "bind" if "Passagen (Kennung" in prompt else "reflect")
        if stage in self.fail:
            raise ConnectionError("GPUStack 403 (off VPN)")
        if stage == "plan":
            out = self.plan
        elif stage == "bind":
            out = self.binds.pop(0) if self.binds else {"claims": []}
        else:
            out = self.reflects.pop(0) if self.reflects else {"gaps": []}
        text = out if isinstance(out, str) else "Gedanken…\n" + json.dumps(out)
        return text, dict(self.usage)


def _retrievers(*its, fail=False):
    def own(query, *, bestand=None, limit=10):
        if fail:
            raise LookupError("Der Passagen-Index ist leer")
        return [i for i in its if i.reading["status"] != ev.EDITION]

    def fulltext(query, *, bestand=None, limit=10):
        return [i for i in its if i.reading["status"] == ev.EDITION]

    return {"passages": own, "fulltext": fulltext}


# ── the binding check ────────────────────────────────────────────────────────

def test_a_verbatim_quote_from_the_cited_passage_is_accepted(items):
    pool = _pool(*items.values())
    ok, bad = af.check_bindings([{"claim": "Das Spital gibt Almosen.",
                                  "evidence": [{"id": items["hgb"].evidence_id,
                                                "quote": "git järlich x ß umb almuosen"}]}],
                                pool)
    assert len(ok) == 1 and not bad
    assert ok[0].bindings[0].ok


@pytest.mark.parametrize("quote,reason", [
    ("gibt jährlich zehn Schilling", "quote not in the cited passage"),   # invented
    ("git jerlich x ß", "quote not in the cited passage"),                # near miss
    ("", "empty quote"),
])
def test_a_quote_that_is_not_there_is_rejected(items, quote, reason):
    pool = _pool(*items.values())
    ok, bad = af.check_bindings([{"claim": "x", "evidence": [
        {"id": items["hgb"].evidence_id, "quote": quote}]}], pool)
    assert not ok
    assert bad[0].bindings[0].reason == reason


def test_a_quote_from_another_passage_names_where_it_is(items):
    pool = _pool(*items.values())
    ok, bad = af.check_bindings([{"claim": "x", "evidence": [
        {"id": items["hgb"].evidence_id, "quote": "pro elemosina pauperum"}]}], pool)
    assert not ok
    assert bad[0].bindings[0].reason == (
        f"quote is in {items['lat'].evidence_id}, not in the cited passage")


def test_a_translated_quote_is_not_a_quote(items):
    pool = _pool(*items.values())
    ok, bad = af.check_bindings([{"claim": "Almosen für die Armen im Spital", "evidence": [
        {"id": items["lat"].evidence_id, "quote": "als Almosen für die Armen"}]}], pool)
    assert not ok and bad[0].bindings[0].reason == "quote not in the cited passage"


def test_unknown_and_unshown_ids_are_rejected(items):
    pool = _pool(*items.values())
    ok, bad = af.check_bindings([{"claim": "x", "evidence": [
        {"id": "ev-000000000000", "quote": "almuosen"},
        {"id": items["lat"].evidence_id, "quote": "elemosina"}]}],
        pool, allowed={items["hgb"].evidence_id})
    assert not ok
    assert [b.reason for b in bad[0].bindings] == ["unknown id", "not shown"]


def test_one_good_binding_carries_a_claim_and_the_bad_one_stays_visible(items):
    pool = _pool(*items.values())
    ok, _ = af.check_bindings([{"claim": "x", "evidence": [
        {"id": items["hgb"].evidence_id, "quote": "almuosen den armen"},
        {"id": items["hgb"].evidence_id, "quote": "almosen"}]}], pool)
    assert len(ok) == 1
    assert [b.ok for b in ok[0].bindings] == [True, False]


def test_the_quote_is_checked_against_the_readings_own_characters(items):
    # the published reading writes "geñs" as n + U+0303; a typed ñ is a miss
    pool = _pool(*items.values())
    eid = items["pub"].evidence_id
    ok, _ = af.check_bindings([{"claim": "x", "evidence": [
        {"id": eid, "quote": "femitlich geñs"}]}], pool)
    assert not ok
    ok, _ = af.check_bindings([{"claim": "x", "evidence": [
        {"id": eid, "quote": "femitlich geñs"}]}], pool)
    assert ok


# ── the summary ──────────────────────────────────────────────────────────────

def _claims(items):
    ok, _ = af.check_bindings([{"claim": "Spital", "evidence": [
        {"id": items["hgb"].evidence_id, "quote": "almuosen den armen"}]}],
        _pool(*items.values()))
    return ok


def test_summary_quoting_only_bound_text_stands(items):
    s, why = af.check_summary("Das Spital gibt „almuosen den armen“ [1].", _claims(items))
    assert s and not why


@pytest.mark.parametrize("summary", [
    "Das Spital gibt „zehn Pfund jährlich“ [1].",       # unbound quotation
    'Laut Quelle "gibt es Almosen" [1].',
    "Siehe [2].",                                        # no claim 2
])
def test_summary_that_quotes_or_cites_beyond_the_claims_is_withheld(items, summary):
    s, why = af.check_summary(summary, _claims(items))
    assert s == "" and why


def test_an_apostrophe_is_not_a_quotation(items):
    s, _ = af.check_summary("Des Spitals' Rechte sind belegt [1].", _claims(items))
    assert s


# ── plan, period, sample ─────────────────────────────────────────────────────

def test_a_plan_the_model_does_not_give_falls_back_and_says_so():
    audit = af.Audit()
    llm = ScriptedLLM(plan="keine Ahnung")
    p = af.plan("Wie wird Almosen begründet?", llm=llm, audit=audit)
    assert not p.from_model
    assert p.subquestions == [{"id": "q1", "text": "Wie wird Almosen begründet?",
                               "terms": []}]
    assert any("no usable plan" in e for e in audit.events)


def test_plan_is_clipped_and_the_callers_period_wins():
    llm = ScriptedLLM(plan={"subquestions": [{"text": f"t{i}", "terms": list("abcde")}
                                             for i in range(6)],
                            "period": {"from": 1200, "to": 1299}})
    p = af.plan("q", llm=llm, audit=af.Audit(), period={"from": 1400, "to": 1499})
    assert len(p.subquestions) == af.MAX_SUBQUESTIONS
    assert all(len(s["terms"]) == af.MAX_TERMS for s in p.subquestions)
    assert p.period == {"from": 1400, "to": 1499}


@pytest.mark.parametrize("datierung,period,want", [
    ("27. Juni 1429", {"from": 1400, "to": 1499}, "inside"),
    ("27. Juni 1429", {"from": 1500, "to": 1599}, "outside"),
    ("14. Jh.", {"from": 1300, "to": 1399}, "inside"),
    (None, {"from": 1300, "to": 1399}, "undated"),
    ("1429", None, "unchecked"),
])
def test_period_check_needs_no_model(datierung, period, want):
    it = ev.edition_item("eos", "d", "text", datierung=datierung)
    assert af.period_check(it, period) == want


def test_sample_drops_the_out_of_period_and_balances_holdings():
    pool = {}
    for i in range(10):
        it = ev.edition_item("eos", f"A{i}", f"Text A {i}", datierung="1450")
        it.retrieval = {"subquestion": "q1", "score": 0.9 - i / 100}
        pool[it.evidence_id] = it
    for i in range(2):
        it = ev.edition_item("kf", f"B{i}", f"Text B {i}", datierung="1460")
        it.retrieval = {"subquestion": "q1", "score": 0.1}
        pool[it.evidence_id] = it
    late = ev.edition_item("kf", "C", "später", datierung="1620")
    late.retrieval = {"subquestion": "q1", "score": 1.0}
    pool[late.evidence_id] = late

    plan_ = af.Plan("q", [{"id": "q1", "text": "q", "terms": []}],
                    period={"from": 1400, "to": 1499})
    picked, dropped = af.sample(pool, plan_, size=4)
    holdings = [pool[e].source["bestand"] for e in picked]
    assert holdings.count("kf") == 2          # the low-scoring holding is heard
    assert late.evidence_id not in picked
    assert {"evidence_id": late.evidence_id,
            "reason": "outside the period asked"} in dropped
    assert sum(d["reason"] == "sample full" for d in dropped) == 8
    assert af.sample(pool, plan_, size=4)[0] == picked      # deterministic


# ── the whole run ────────────────────────────────────────────────────────────

def _good_bind(items):
    return {"claims": [
        {"claim": "Das Spital in Basel gab jährlich Almosen an die Armen.",
         "evidence": [{"id": items["hgb"].evidence_id, "quote": "almuosen den armen"}]},
        {"claim": "Der Brief beginnt mit einer Grussformel.",
         "evidence": [{"id": items["pub"].evidence_id, "quote": "Iuser femitlich"}]},
        {"claim": "Erfunden.",
         "evidence": [{"id": items["hgb"].evidence_id, "quote": "zehn Pfund"}]}],
        "summary": "Das Spital gab „almuosen den armen“ [1]."}


def test_ask_answers_with_checked_claims_and_counts_its_cost(items):
    llm = ScriptedLLM(binds=[_good_bind(items)])
    res = af.ask("Wie wird Almosen begründet?",
                 llm=llm, retrievers=_retrievers(*items.values()),
                 max_rounds=1, now="2026-11-05T20:00:00+00:00")
    assert [c.text for c in res.claims] == [
        "Das Spital in Basel gab jährlich Almosen an die Armen.",
        "Der Brief beginnt mit einer Grussformel."]
    assert [c.text for c in res.rejected_claims] == ["Erfunden."]
    assert res.summary.startswith("Das Spital")
    assert len(res.audit.calls) == 3                      # plan, bind, reflect
    assert res.audit.tokens == 3 * 150
    assert res.complete
    assert res.question_id.startswith("q-")

    md = af.render_markdown(res)
    assert "„almuosen den armen“" in md
    assert "Editionstext" in md
    assert "Engines uneinig an dieser Stelle" in md      # the BAT_664 span
    assert "automatisch gewählt, ungeprüft" in md
    assert "keine Ground Truth" in md


def test_the_binding_prompt_says_what_kind_of_text_each_passage_is(items):
    llm = ScriptedLLM(binds=[_good_bind(items)])
    af.ask("q", llm=llm, retrievers=_retrievers(*items.values()), max_rounds=0)
    bind_prompt = next(p for p in llm.prompts if "Passagen (Kennung" in p)
    assert "Edition eos" in bind_prompt
    assert "Lesung pipeline/published: auto_selected" in bind_prompt
    assert "Engines uneinig an dieser Stelle" in bind_prompt
    assert "ohne Übersetzung" in bind_prompt


def test_a_failed_search_is_a_gap_not_a_crash(items):
    llm = ScriptedLLM(binds=[_good_bind(items)])
    res = af.ask("q", llm=llm, retrievers=_retrievers(*items.values(), fail=True),
                 max_rounds=0)
    failed = [c for c in res.coverage if c["status"] == "failed"]
    assert failed and "Passagen-Index ist leer" in failed[0]["error"]
    assert not res.complete
    assert res.claims                                  # the editions still answered
    assert "Unvollständig" in af.render_markdown(res)


def test_the_budget_stops_before_the_call_and_says_which(items):
    llm = ScriptedLLM(binds=[_good_bind(items)])
    res = af.ask("q", llm=llm, retrievers=_retrievers(*items.values()),
                 max_calls=1)
    assert len(llm.prompts) == 1                       # the plan, nothing after
    assert res.audit.stopped_because == "call budget (1) reached"
    assert res.audit.skipped == ["bind"]
    assert not res.claims and not res.complete


def test_token_budget_counts_what_the_backend_reported(items):
    llm = ScriptedLLM(binds=[_good_bind(items)],
                      usage={"prompt_tokens": 900, "completion_tokens": 200})
    res = af.ask("q", llm=llm, retrievers=_retrievers(*items.values()),
                 max_tokens=1000)
    assert res.audit.tokens == 1100
    assert res.audit.stopped_because == "token budget (1000) reached"


def test_missing_usage_is_unknown_not_zero(items):
    llm = ScriptedLLM(binds=[_good_bind(items)], usage={})
    res = af.ask("q", llm=llm, retrievers=_retrievers(*items.values()), max_rounds=0)
    assert res.audit.tokens == 0
    assert res.audit.unknown_usage_calls == len(res.audit.calls) == 2


def test_a_model_that_cannot_be_reached_is_recorded(items):
    llm = ScriptedLLM(fail={"plan", "bind"})
    res = af.ask("q", llm=llm, retrievers=_retrievers(*items.values()), max_rounds=0)
    assert not res.plan.from_model and not res.complete
    assert any("model call failed" in e for e in res.audit.events)
    assert not res.claims


def test_reflection_retrieves_again_and_rebinds_at_most_max_rounds(items):
    extra = ev.edition_item("eos", "HGB-0999", "von des almuosens wegen", datierung="1470")
    calls = {"n": 0}

    def fulltext(query, *, bestand=None, limit=10):
        calls["n"] += 1
        base = [items["hgb"]]
        return base + ([extra] if query == "almuosens" else [])

    llm = ScriptedLLM(
        binds=[_good_bind(items),
               {"claims": [{"claim": "Almosen wird als Grund genannt.", "evidence": [
                   {"id": extra.evidence_id, "quote": "des almuosens wegen"}]}]}],
        reflects=[{"gaps": [{"text": "Begründung", "terms": ["almuosens"], "why": "fehlt"}]},
                  {"gaps": [{"text": "noch mehr", "terms": ["nichts"], "why": "x"}]}])
    res = af.ask("q", llm=llm, retrievers={"fulltext": fulltext}, max_rounds=2)
    assert extra.evidence_id in res.pool
    assert [c.text for c in res.claims] == ["Almosen wird als Grund genannt."]
    assert res.audit.rounds == 2
    assert [g["terms"] for g in res.gaps] == [["almuosens"], ["nichts"]]
    assert any("found nothing new" in e for e in res.audit.events)


def test_write_result_writes_three_files(items, tmp_path):
    llm = ScriptedLLM(binds=[_good_bind(items)])
    res = af.ask("q", llm=llm, retrievers=_retrievers(*items.values()), max_rounds=0)
    d = af.write_result(res, tmp_path)
    assert sorted(p.name for p in d.iterdir()) == ["answer.md", "audit.json", "pool.json"]
    audit = json.loads((d / "audit.json").read_text(encoding="utf-8"))
    assert audit["claims"][0]["bindings"][0]["ok"] is True
    assert audit["rejected_claims"][0]["bindings"][0]["reason"] == \
        "quote not in the cited passage"
    pool = json.loads((d / "pool.json").read_text(encoding="utf-8"))
    assert pool[items["pub"].evidence_id]["agreement"] == "contested"


# ── evidence additions ───────────────────────────────────────────────────────

def test_edition_item_claims_no_comparison():
    it = ev.edition_item("eos", "HGB-1", SPITAL)
    assert it.reading["status"] == ev.EDITION
    assert it.agreement == "single" and it.page_band == "unmeasurable"
    assert it.quote == SPITAL
    other = ev.edition_item("kf", "HGB-1", SPITAL)
    assert other.evidence_id != it.evidence_id          # the source is in the id
    assert ev.edition_item("eos", "x", "  ") is None


def test_a_passage_row_widens_to_its_lines():
    text = "erste zeile\nder spital git almuosen\ndritte zeile\nvierte"
    pipeline = {"doc_id": "d", "transcription": text}
    start = text.index("almuosen")
    row = {"doc_id": "d", "char_start": start, "char_end": start + 8, "text": "almuosen"}
    assert ev.item_from_passage_row(row, pipeline=pipeline).quote == "almuosen"
    wide = ev.item_from_passage_row(row, pipeline=pipeline, widen_lines=1)
    assert wide.quote == "erste zeile\nder spital git almuosen\ndritte zeile"
    assert wide.anchor == f"passage-0-{len(wide.quote)}"


# ── the tool ─────────────────────────────────────────────────────────────────

def test_ask_is_a_registered_tool(monkeypatch):
    assert "ask" in {t["name"] for t in at.list_tools()}
    monkeypatch.setattr(orchestrator, "run_ask", lambda **kw: {"got": kw})
    assert at.call_tool("ask", question="Wer?", bestand="kf") == {
        "got": {"question": "Wer?", "bestand": "kf"}}


# ── the federation: a source that fails is not a source with nothing ────────

def _two_sources(monkeypatch):
    from knowledge_hub.mcp_registry import MCPSource
    from utils import mcp_client

    srcs = [MCPSource(name="eos", title="EOS", kinds=("fulltext",)),
            MCPSource(name="ssrq", title="SSRQ", kinds=("fulltext",))]
    monkeypatch.setattr(mcp_client.reg, "sources_for_kind", lambda kind: srcs)


def test_an_unreachable_federation_is_failed_not_empty(monkeypatch):
    _two_sources(monkeypatch)

    async def down(source, tool, args):
        raise ConnectionError("403 off VPN")

    with pytest.raises(LookupError, match="eos: 403 off VPN"):
        af.federation_fulltext("almuosen", call_tool=down)


def test_one_source_down_keeps_the_others_hits(monkeypatch):
    _two_sources(monkeypatch)

    async def half(source, tool, args):
        if source.name == "ssrq":
            raise ConnectionError("timeout")
        return [{"doc_id": "HGB-1", "snippet": SPITAL, "score": 0.7}]

    with pytest.raises(af.PartialRetrieval) as e:
        af.federation_fulltext("almuosen", call_tool=half)
    assert [i.quote for i in e.value.items] == [SPITAL]
    assert e.value.errors == ["ssrq: timeout"]


def test_a_partial_search_is_recorded_and_its_hits_used(items):
    def partial(query, *, bestand=None, limit=10):
        raise af.PartialRetrieval([items["hgb"]], ["ssrq: timeout"])

    llm = ScriptedLLM(binds=[_good_bind(items)])
    res = af.ask("q", llm=llm, retrievers={"fulltext": partial}, max_rounds=0)
    assert {c["status"] for c in res.coverage} == {"partial"}
    assert items["hgb"].evidence_id in res.pool
    assert res.claims and not res.complete
    md = af.render_markdown(res)
    assert "partial — ssrq: timeout" in md
