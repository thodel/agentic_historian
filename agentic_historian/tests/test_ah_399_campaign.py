"""#399 (G2): a bounded vote campaign on one holding, and a report of what it bought.

G1 ranks the pending Gate-2 pages; a ranking still waits for someone to come
looking. The epic's done-criterion is a number — after one campaign the dominant
bucket crosses n≥10 — so this is the deliberate push plus the measurement.

Four things here are contracts, and each has its own section:

**The scope is a doc_id prefix, not a holding.** The repository has two
disagreeing bestand conventions and neither fits the doc_id shapes that reach a
RunState; `provenance.bestand_of` even answers a flat id with the whole id. So
the scope is a prefix, said plainly, and a prefix that matches nothing offers
the ones that exist.

**Budget is in cards, decisions are in pages.** One Gate-2 card carries every
page of its document, so "budget 5" can be thirty decisions. Both numbers are
reported, because only one of them sounds like what was asked for.

**Completion is derived, never recorded.** The store holds the ask; G1's
`decided_pages` answers whether each asked page is done. A counter kept here
would be a second source of truth and would drift the first time a card was
answered outside the thread.

**The baseline is what makes "crossed" sayable.** Without the coverage snapshot
from the start, a report can only say a bucket *is* above ten, not that this
campaign got it there. And a finished campaign freezes its numbers, or it keeps
collecting credit for every comparison since.

Offline — no Discord, no engines. Run from the repo root:
    pytest agentic_historian/tests/test_ah_399_campaign.py
"""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import campaign as camp   # noqa: E402
import config             # noqa: E402
import preferences        # noqa: E402
import votes_queue as vq  # noqa: E402
import voting             # noqa: E402
from runstate import RunState  # noqa: E402

MODELS = ("trocr/escriptmask", "trocr/kurrent-xvi-xvii", "kraken/catmus")
K16 = {"script": "kurrent", "century": 16, "lang": "de"}
B15 = {"script": "bastarda", "century": 15, "lang": "de"}


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "PREFERENCES_LOG_PATH", tmp_path / "preferences.jsonl")
    monkeypatch.setattr(config, "VOTES_LOG_PATH", tmp_path / "votes.jsonl")
    monkeypatch.setattr(config, "PSEUDONYM_SALT_PATH", tmp_path / ".salt")
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", False)
    return tmp_path


# ── fixtures ─────────────────────────────────────────────────────────────────

def _state(doc_id, pages, *, n=3, cer=0.5, criteria=K16, save=True):
    """A RunState as the orchestrator leaves it after a no-merge (#313).

    Saved by default: `post_cards` re-reads each document by id right before
    posting it, which is deliberate (it picks up a decision made between
    planning and posting), so a fixture that only lived in memory would test a
    path production never takes.
    """
    st = RunState(doc_id=doc_id)
    paths, ctx = {}, {}
    for page in pages:
        for model in MODELS[:n]:
            paths[f"{page}:{model}"] = f"reading {model} of {page}"
        ctx[page] = {"max_pairwise_cer": cer, "criteria": dict(criteria)}
    st.artifacts["paths"] = paths
    st.gate_decisions["gate2_context"] = ctx
    st.gate_decisions["gate2_auto"] = {p: f"{p}:{MODELS[1]}" for p in pages}
    if save:
        st.save()
    return st


def _event(doc_id, page, criteria=K16, *, offered=MODELS, chosen=(MODELS[0],),
           rejected=False, ts="2099-01-01T00:00:00+00:00"):
    return preferences.PreferenceEvent(
        doc_id=doc_id, page=page,
        offered=[{"engine": o.split("/")[0], "model_id": o.split("/", 1)[1],
                  "local_model_id": o} for o in offered],
        chosen=list(chosen), rejected=rejected, criteria=dict(criteria),
        auto_pick=MODELS[1], ts=ts)


def _coverage(criteria, n, *, prefix="cov"):
    return [_event(f"{prefix}-{criteria['script']}-{i}", "p", criteria,
                   offered=MODELS[:2], chosen=(MODELS[0],))
            for i in range(int(n))]


def _plan(states=None, *, scope="", budget=camp.DEFAULT_BUDGET, events=(),
          votes=()):
    """A plan over the saved run states, as the command builds one.

    ``states`` is accepted and ignored on purpose: the fixtures save, so the
    disk is the single source and the plan cannot be built from documents the
    posting step would then fail to find.
    """
    return camp.plan(scope, budget, events=list(events), votes=set(votes))


# ── the scope is a doc_id prefix, and says so ────────────────────────────────

def test_the_scope_is_a_case_insensitive_prefix():
    assert camp.matches("BAT_664_r_00027", "BAT")
    assert camp.matches("BAT_664_r_00027", "bat_664")
    assert not camp.matches("KOE_112", "BAT")


def test_an_empty_scope_is_the_whole_corpus():
    """"No restriction" is the honest reading of an empty argument, and it lets
    a campaign run over everything."""
    assert camp.matches("anything", "")
    plan = _plan([_state("BAT_664", ["p1"]), _state("KOE_112", ["p1"])])

    assert sorted(plan.cards) == ["BAT_664", "KOE_112"]


def test_the_rule_is_stated_in_every_message():
    """A surprising match has to be diagnosable from the message itself: there is
    no separately kept Bestand on a RunState, and two conventions in the repo
    disagree about how to derive one."""
    plan = _plan([_state("BAT_664", ["p1"])], scope="BAT")

    assert camp.SCOPE_RULE in "\n".join(camp.format_plan(plan))
    assert "doc_id" in camp.SCOPE_RULE


def test_a_prefix_that_matches_nothing_offers_the_ones_that_do():
    """"No document matched" and "the matching documents have no pending votes"
    are identical as an empty report and want different next actions."""
    plan = _plan([_state("BAT_664", ["p1"])], scope="Lassberg")
    why = plan.why_empty()

    assert plan.cards == []
    assert "Lassberg" in why
    assert "BAT_664" in why, "the historian is not told what does exist"


def test_known_scopes_offers_both_conventions():
    states = [_state("Marbach__letter-0001__001", ["p1"]),
              _state("BAT_664_r_00027", ["p1"])]
    scopes = camp.known_scopes(states)

    assert "Marbach" in scopes            # the __ convention
    assert "Marbach__letter-0001__001" in scopes
    assert "BAT_664_r_00027" in scopes    # a flat id is its own scope


def test_an_empty_corpus_is_told_apart_from_an_unmatched_prefix():
    """With nothing pending anywhere, G1's own three-way explanation is the right
    answer — the prefix is not the problem."""
    plan = _plan([], scope="BAT")

    assert "Keine Läufe gefunden" in plan.why_empty()


# ── budget in cards, decisions in pages ──────────────────────────────────────

def test_the_budget_counts_cards_not_pages():
    """One Gate-2 card carries every page of its document."""
    states = [_state("d-1", ["p1", "p2", "p3"]), _state("d-2", ["p1", "p2"])]
    plan = _plan(states, budget=1)

    assert len(plan.cards) == 1
    assert plan.pages == 3, "the card's other pages are part of the ask"


def test_both_numbers_are_reported_because_only_one_sounds_like_the_ask():
    states = [_state("d-1", ["p1", "p2", "p3"])]
    rendered = "\n".join(camp.format_plan(_plan(states, budget=5)))

    assert "1 Karte(n)" in rendered
    assert "**3 Seite(n)** zu entscheiden" in rendered
    assert "Budget 5 Karten heisst hier 3 Entscheidungen" in rendered


def test_the_mismatch_note_is_absent_when_there_is_none():
    rendered = "\n".join(camp.format_plan(_plan([_state("d-1", ["p1"])])))

    assert "Entscheidungen" not in rendered


def test_documents_beyond_the_budget_are_counted_not_dropped_silently():
    states = [_state(f"d-{i}", ["p1"]) for i in range(5)]
    plan = _plan(states, budget=2)

    assert (plan.matched, plan.skipped) == (5, 3)
    assert "3 weitere Dokument(e)" in "\n".join(camp.format_plan(plan))


def test_the_budget_is_capped():
    """A campaign posts one card per document into one thread; beyond the cap the
    thread stops being readable and the posting alone takes minutes."""
    states = [_state(f"d-{i}", ["p1"]) for i in range(3)]

    assert _plan(states, budget=10_000).budget == camp.MAX_BUDGET
    assert _plan(states, budget=-1).budget == 0
    assert _plan(states, budget=0).cards == []
    assert "Budget ist 0" in _plan(states, budget=0).why_empty()


def test_cards_come_in_g1_order():
    """Documents rank by their BEST pending page: a document whose dull page
    would sink on its own still gets posted when another page is informative —
    the card carries all of them anyway."""
    _state("quiet", ["p1"], cer=0.05)
    _state("loud", ["p1"], cer=0.90)
    mixed = _state("mixed", ["p1"], cer=0.05, save=False)
    mixed.artifacts["paths"].update(
        {f"p2:{m}": f"reading {m} of p2" for m in MODELS})
    mixed.gate_decisions["gate2_context"]["p2"] = {
        "max_pairwise_cer": 0.80, "criteria": dict(K16)}
    mixed.save()
    plan = _plan(events=_coverage(K16, 20))

    assert plan.cards == ["loud", "mixed", "quiet"]
    assert ("mixed", "p1") in plan.roster and ("mixed", "p2") in plan.roster


def test_a_decided_page_is_never_asked_again():
    states = [_state("d-1", ["p1", "p2"])]
    plan = _plan(states, events=[_event("d-1", "p1")])

    assert plan.cards == ["d-1"]
    assert plan.roster == [("d-1", "p2")]


def test_a_fully_decided_document_is_not_posted():
    states = [_state("d-1", ["p1"]), _state("d-2", ["p1"])]
    plan = _plan(states, events=[_event("d-1", "p1")])

    assert plan.cards == ["d-2"]


# ── the record ───────────────────────────────────────────────────────────────

async def _post_all(plan, **kw):
    sent = []

    async def post(thread, state, paths, text):
        sent.append((getattr(thread, "id", None), state.doc_id, text))

    async def sleep(_s):
        pass

    c = await camp.post_cards(plan, post=post, sleep=sleep, **kw)
    return c, sent


def test_posting_records_the_ask_and_the_cards():
    plan = _plan([_state("d-1", ["p1", "p2"])])
    c, sent = asyncio.run(_post_all(plan))

    assert c.cards == ["d-1"] and len(sent) == 1
    assert c.roster == [("d-1", "p1"), ("d-1", "p2")]
    assert c.running and not c.ended_at


def test_the_record_survives_a_round_trip():
    plan = _plan([_state("d-1", ["p1"])])
    c, _ = asyncio.run(_post_all(plan))
    back = camp.Campaign.load(c.campaign_id)

    assert back is not None
    assert back.to_dict() == c.to_dict()
    assert back.schema == camp.CAMPAIGN_SCHEMA


def test_the_record_is_saved_before_the_first_card_goes_out():
    """A bot that dies mid-campaign must still have a campaign to report on. The
    other order loses the ask and with it the baseline, which cannot be
    reconstructed afterwards."""
    plan = _plan([_state("d-1", ["p1"])])
    seen = []

    async def post(thread, state, paths, text):
        seen.append(sorted(p.name for p in camp.campaigns_dir().glob("*.json")))

    async def sleep(_s):
        pass

    asyncio.run(camp.post_cards(plan, post=post, sleep=sleep))

    assert seen and seen[0], "no record on disk when the first card was posted"


def test_the_starter_is_pseudonymised():
    """Every voter id in this project is pseudonymous (#332); the person who
    starts a campaign is no different."""
    plan = _plan([_state("d-1", ["p1"])])
    c, _ = asyncio.run(_post_all(plan, started_by="1234567890"))

    assert c.started_by not in ("", "1234567890")
    assert c.started_by == preferences.pseudonym("1234567890")


def test_a_campaign_id_survives_an_awkward_scope():
    plan = _plan([_state("d-1", ["p1"])], scope="")
    c, _ = asyncio.run(_post_all(plan))

    assert c.campaign_id.startswith("korpus-")
    assert "/" not in c.campaign_id


def test_an_unreadable_record_costs_itself_not_the_listing():
    plan = _plan([_state("d-1", ["p1"])])
    c, _ = asyncio.run(_post_all(plan))
    (camp.campaigns_dir() / "broken.json").write_text("{ not json",
                                                      encoding="utf-8")

    assert [x.campaign_id for x in camp.Campaign.load_all()] == [c.campaign_id]


def test_latest_prefers_a_running_campaign_over_a_finished_one():
    plan = _plan([_state("d-1", ["p1"])], scope="d")
    old, _ = asyncio.run(_post_all(plan))
    camp.end(old)
    new, _ = asyncio.run(_post_all(plan))

    assert camp.latest("d").campaign_id == new.campaign_id


# ── posting: budget, spacing, resilience ─────────────────────────────────────

def test_the_cards_are_spaced_but_the_first_is_not_delayed():
    """Discord's per-channel budget is roughly five messages in five seconds. The
    first card waits for nothing — a delay before it only makes the command feel
    broken."""
    plan = _plan([_state(f"d-{i}", ["p1"]) for i in range(3)])
    slept = []

    async def post(thread, state, paths, text):
        pass

    async def sleep(s):
        slept.append(s)

    asyncio.run(camp.post_cards(plan, post=post, sleep=sleep, delay=1.5))

    assert slept == [1.5, 1.5], "three cards need two gaps, not three"


def test_the_spacing_is_configurable_and_can_be_switched_off():
    plan = _plan([_state(f"d-{i}", ["p1"]) for i in range(2)])
    slept = []

    async def post(thread, state, paths, text):
        pass

    async def sleep(s):
        slept.append(s)

    asyncio.run(camp.post_cards(plan, post=post, sleep=sleep, delay=0))

    assert slept == []


def test_the_thread_id_is_recorded():
    plan = _plan([_state("d-1", ["p1"])])

    async def open_thread(name):
        assert name
        return SimpleNamespace(id=9999)

    c, sent = asyncio.run(_post_all(plan, open_thread=open_thread))

    assert c.thread_id == 9999
    assert sent[0][0] == 9999, "the card did not go into the thread"


def test_a_failed_thread_does_not_cost_the_campaign():
    """A thread is where the campaign should live, not a precondition for
    collecting votes. Refusing to run would lose the campaign over cosmetics."""
    plan = _plan([_state("d-1", ["p1"])])

    async def open_thread(name):
        raise RuntimeError("missing permission")

    c, sent = asyncio.run(_post_all(plan, open_thread=open_thread))

    assert c.thread_id is None
    assert len(sent) == 1 and sent[0][0] is None


def test_one_card_that_will_not_post_does_not_cost_the_rest():
    plan = _plan([_state(f"d-{i}", ["p1"]) for i in range(3)])
    sent = []

    async def post(thread, state, paths, text):
        if state.doc_id == "d-1":
            raise RuntimeError("Discord said no")
        sent.append(state.doc_id)

    async def sleep(_s):
        pass

    c = asyncio.run(camp.post_cards(plan, post=post, sleep=sleep))

    assert sorted(sent) == ["d-0", "d-2"]
    assert ("d-1", "p1") in c.roster, "the page is still recorded as asked"


def test_a_document_whose_candidates_vanished_is_skipped():
    """Decided or re-run between planning and posting: a card for a document with
    nothing to show would ask for a vote on nothing."""
    good = _state("d-ok", ["p1"])
    gone = _state("d-gone", ["p1"])
    plan = _plan([good, gone])
    gone.artifacts["paths"] = {}
    sent = []

    async def post(thread, state, paths, text):
        sent.append(state.doc_id)

    async def sleep(_s):
        pass

    asyncio.run(camp.post_cards(plan, post=post, sleep=sleep,
                                states={"d-ok": good, "d-gone": gone}))

    assert sent == ["d-ok"]


def test_the_posted_text_is_the_real_gate2_card():
    """Not a campaign-specific rendering: the historian must see exactly the card
    `/votes` would show, or the campaign is a different instrument."""
    state = _state("d-1", ["p1"])
    plan = _plan([state])
    _c, sent = asyncio.run(_post_all(plan, states={"d-1": state}))

    import path_compare
    assert sent[0][2] == path_compare.render_vote_card(
        state, state.artifacts["paths"])


# ── completion is derived, never recorded ────────────────────────────────────

def test_progress_is_derived_from_the_preference_log():
    state = _state("d-1", ["p1", "p2"])
    plan = _plan([state])
    c, _ = asyncio.run(_post_all(plan))

    prog = camp.progress(c, events=[_event("d-1", "p1")], votes=set(),
                         states=[state])

    assert (prog.asked, prog.decided, prog.pending) == (2, 1, 1)
    assert prog.accepted == 1 and prog.rejected == 0
    assert not prog.done


def test_a_vote_outside_the_thread_still_counts_as_done():
    """The whole reason completion is derived: a card answered anywhere must not
    leave the campaign reporting it as open."""
    state = _state("d-1", ["p1"])
    c, _ = asyncio.run(_post_all(_plan([state])))

    prog = camp.progress(c, events=[], votes={("d-1", "p1")}, states=[state])

    assert prog.done


def test_a_rejection_counts_as_decided_and_as_a_coverage_gap():
    state = _state("d-1", ["p1"])
    c, _ = asyncio.run(_post_all(_plan([state])))
    events = [_event("d-1", "p1", chosen=[], rejected=True)]

    prog = camp.progress(c, events=events, votes=set(), states=[state])

    assert prog.done and prog.rejected == 1 and prog.accepted == 0


def test_the_rate_is_none_when_nothing_was_asked():
    """"Nothing was asked" and "nothing was answered" are different claims."""
    c = camp.Campaign(campaign_id="x", scope="", budget=0)

    assert camp.progress(c, events=[], votes=set(), states=[]).rate is None


def test_only_the_campaigns_own_decisions_are_credited():
    """A page decided before the campaign started would inflate exactly the
    number the epic is measured on."""
    state = _state("d-1", ["p1"])
    c, _ = asyncio.run(_post_all(_plan([state])))
    early = _event("d-1", "p1", ts="2000-01-01T00:00:00+00:00")

    assert camp.campaign_events(c, [early]) == []
    assert camp.campaign_events(c, [_event("d-1", "p1")])


def test_an_event_with_no_timestamp_is_not_attributed():
    state = _state("d-1", ["p1"])
    c, _ = asyncio.run(_post_all(_plan([state])))

    assert camp.campaign_events(c, [_event("d-1", "p1", ts="")]) == []


def test_a_decision_on_a_page_outside_the_roster_is_not_credited():
    state = _state("d-1", ["p1"])
    c, _ = asyncio.run(_post_all(_plan([state])))

    assert camp.campaign_events(c, [_event("other", "p1")]) == []


# ── the baseline is what makes "crossed" sayable ─────────────────────────────

def _started_with(baseline_events, state):
    """A campaign started with a given coverage history already in the log."""
    config.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    with config.PREFERENCES_LOG_PATH.open("w", encoding="utf-8") as fh:
        for ev in baseline_events:
            fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
    return asyncio.run(_post_all(_plan([state], events=baseline_events)))[0]


def test_a_bucket_the_campaign_pushed_over_the_line_reads_crossed():
    state = _state("d-1", ["p1"])
    c = _started_with(_coverage(K16, 9), state)
    after = _coverage(K16, 9) + _coverage(K16, 3, prefix="new")

    line = next(b for b in camp.bucket_report(c, events=after)
                if b.bucket == ("kurrent", 16, "de"))

    assert (line.before, line.now) == (9.0, 12.0)
    assert line.status == "crossed"


def test_a_bucket_that_was_already_sufficient_gets_no_credit():
    """Without the baseline a report could only say the bucket IS above ten, and
    would hand this campaign a crossing it did not cause."""
    state = _state("d-1", ["p1"])
    c = _started_with(_coverage(K16, 14), state)
    after = _coverage(K16, 14) + _coverage(K16, 2, prefix="new")

    line = next(b for b in camp.bucket_report(c, events=after)
                if b.bucket == ("kurrent", 16, "de"))

    assert line.status == "already"
    rendered = "\n".join(camp.format_report(
        c, camp.progress(c, events=after, votes=set(), states=[state]),
        camp.bucket_report(c, events=after), events=after))
    assert "in dieser Kampagne\nüberschritten" not in rendered
    assert "Kein Bucket hat die Schwelle" in rendered


def test_the_threshold_is_the_one_335_reports_against():
    """A campaign with its own idea of "enough" would declare a bucket evaluable
    that `preference_strength` still calls insufficient."""
    assert camp.vq.MIN_COMPARISONS is vq.MIN_COMPARISONS
    from agent_a.preference_strength import MIN_COMPARISONS
    assert vq.MIN_COMPARISONS == MIN_COMPARISONS == 10.0

    state = _state("d-1", ["p1"])
    c = _started_with(_coverage(K16, 9), state)
    after = _coverage(K16, 9) + _coverage(K16, 1, prefix="new")
    line = next(b for b in camp.bucket_report(c, events=after)
                if b.bucket == ("kurrent", 16, "de"))

    assert line.now == MIN_COMPARISONS and line.status == "crossed"


def test_a_bucket_still_short_says_so():
    state = _state("d-1", ["p1"])
    c = _started_with(_coverage(K16, 2), state)
    after = _coverage(K16, 2) + _coverage(K16, 3, prefix="new")

    line = next(b for b in camp.bucket_report(c, events=after)
                if b.bucket == ("kurrent", 16, "de"))

    assert line.status == "still_short" and line.gained == 3.0


def test_a_bucket_the_campaign_created_from_nothing_is_listed():
    """It is in neither baseline nor an obvious key list, and a silently absent
    bucket is the one a report must not drop."""
    state = _state("d-1", ["p1"])
    c = _started_with(_coverage(K16, 3), state)
    after = _coverage(K16, 3) + _coverage(B15, 4)

    buckets = [b.bucket for b in camp.bucket_report(c, events=after)]

    assert ("bastarda", 15, "de") in buckets


def test_an_unbucketed_comparison_keeps_its_none_through_the_store():
    """`("", 16, "de")` and `(None, 16, "de")` are different keys in the
    preference log, so the JSON round trip may not confuse them."""
    for bucket in (("kurrent", 16, "de"), (None, None, None),
                   ("kurrent", None, None)):
        assert camp._bucket_tuple(camp._bucket_key(bucket)) == bucket


def test_ending_freezes_the_numbers():
    """A finished campaign read a month later must report its own numbers, not
    every comparison the project has collected since."""
    state = _state("d-1", ["p1"])
    c = _started_with(_coverage(K16, 4), state)
    at_end = _coverage(K16, 4) + _coverage(K16, 8, prefix="during")
    camp.end(c, events=at_end)

    much_later = at_end + _coverage(K16, 90, prefix="later")
    line = next(b for b in camp.bucket_report(c, events=much_later)
                if b.bucket == ("kurrent", 16, "de"))

    assert line.now == 12.0, "the report kept collecting after the campaign ended"
    assert not c.running


def test_ending_is_idempotent():
    state = _state("d-1", ["p1"])
    c = _started_with([], state)
    camp.end(c, events=_coverage(K16, 5))
    first = (c.ended_at, dict(c.final or {}))
    camp.end(c, events=_coverage(K16, 50))

    assert (c.ended_at, dict(c.final or {})) == first


def test_a_running_campaign_reads_the_live_log():
    state = _state("d-1", ["p1"])
    c = _started_with([], state)

    line = next(b for b in camp.bucket_report(c, events=_coverage(K16, 6))
                if b.bucket == ("kurrent", 16, "de"))

    assert c.final is None and line.now == 6.0


# ── the report ───────────────────────────────────────────────────────────────

def test_the_report_names_the_threshold_and_the_crossing():
    state = _state("d-1", ["p1"])
    c = _started_with(_coverage(K16, 9), state)
    after = _coverage(K16, 9) + _coverage(K16, 4, prefix="new")
    prog = camp.progress(c, events=after, votes=set(), states=[state])
    rendered = "\n".join(camp.format_report(
        c, prog, camp.bucket_report(c, events=after), events=after))

    assert "kurrent×16.Jh×de" in rendered
    assert "9.0 → 13.0 (+4.0)" in rendered
    assert "n≥10" in rendered
    assert "ENABLE_ROUTING_PRIOR" in rendered


def test_the_report_says_plainly_when_no_bucket_crossed():
    state = _state("d-1", ["p1"])
    c = _started_with(_coverage(K16, 1), state)
    after = _coverage(K16, 1) + _coverage(K16, 2, prefix="new")
    prog = camp.progress(c, events=after, votes=set(), states=[state])
    rendered = "\n".join(camp.format_report(
        c, prog, camp.bucket_report(c, events=after), events=after))

    assert "Kein Bucket hat die Schwelle" in rendered


def test_the_report_distinguishes_no_votes_from_no_crossing():
    state = _state("d-1", ["p1"])
    c = _started_with([], state)
    prog = camp.progress(c, events=[], votes=set(), states=[state])
    rendered = "\n".join(camp.format_report(
        c, prog, camp.bucket_report(c, events=[]), events=[]))

    assert "noch keine Stimme eingegangen" in rendered
    assert "0/1" in rendered


def test_the_report_carries_selection_agreement_for_this_campaign_only():
    """#334's number at campaign level, delegated to #334's computation rather
    than re-derived — the project-wide figure is already in /routing_stats."""
    state = _state("d-1", ["p1"])
    c = _started_with([], state)
    # auto_pick is MODELS[1]; the historian chose MODELS[0] → disagreement
    mine = [_event("d-1", "p1")]
    elsewhere = [_event("other", "p9", chosen=(MODELS[1],))]
    prog = camp.progress(c, events=mine + elsewhere, votes=set(), states=[state])
    rendered = "\n".join(camp.format_report(
        c, prog, camp.bucket_report(c, events=mine + elsewhere),
        events=mine + elsewhere))

    assert "0/1 (0%)" in rendered, "another campaign's agreement leaked in"


def test_agreement_says_when_it_has_nothing_rather_than_showing_zero():
    state = _state("d-1", ["p1"])
    c = _started_with([], state)
    prog = camp.progress(c, events=[], votes=set(), states=[state])
    rendered = "\n".join(camp.format_report(
        c, prog, camp.bucket_report(c, events=[]), events=[]))

    assert "noch keine entschiedene Seite" in rendered


def test_the_report_returns_one_message_per_section():
    state = _state("d-1", ["p1"])
    c = _started_with([], state)
    prog = camp.progress(c, events=[], votes=set(), states=[state])

    assert len(camp.format_report(c, prog)) == 3


def test_a_finished_campaign_is_marked_as_finished():
    state = _state("d-1", ["p1"])
    c = _started_with([], state)
    camp.end(c, events=[])
    prog = camp.progress(c, events=[], votes=set(), states=[state])

    assert "beendet" in camp.format_report(c, prog)[0]


# ── the preference log is not extended ───────────────────────────────────────

def test_the_campaign_writes_nothing_to_the_preference_log():
    """#399: the format is methodology. A campaign that wrote its own rows there
    would make the comparisons unreadable as comparisons."""
    state = _state("d-1", ["p1"])
    config.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    config.PREFERENCES_LOG_PATH.write_text("", encoding="utf-8")
    c, _ = asyncio.run(_post_all(_plan([state])))
    camp.end(c)

    assert config.PREFERENCES_LOG_PATH.read_text(encoding="utf-8") == ""


def test_the_campaign_store_is_the_only_thing_the_campaign_creates():
    """#399 adds storage — the ask and the baseline — and nothing else. A
    campaign that also touched the run states or the feedback logs would be
    changing what it measures."""
    _state("d-1", ["p1"])
    before = {p.name for p in config.DATA_DIR.iterdir()}
    asyncio.run(_post_all(_plan()))
    after = {p.name for p in config.DATA_DIR.iterdir()}

    assert after - before == {"campaigns"}


# ── the slash commands ───────────────────────────────────────────────────────

class _Ctx:
    def __init__(self):
        self.sent = []
        self.threads = []
        self.guild = SimpleNamespace(id=1)
        self.author = SimpleNamespace(id=77, roles=[])

        async def _defer(ephemeral=False):
            pass

        async def _send(content=None, view=None, ephemeral=False):
            self.sent.append(content)
            return SimpleNamespace(id=4242, content=content)

        async def _create_thread(name=None, type=None):
            self.threads.append(name)
            parent = self

            class _T:
                id = 555

                async def send(self, content=None, view=None):
                    parent.sent.append(content)
                    return SimpleNamespace(id=4243, content=content)

            return _T()

        self.defer = _defer
        self.followup = SimpleNamespace(send=_send)
        self.channel = SimpleNamespace(id=9, create_thread=_create_thread)


def _cmd(name):
    import bot
    return next(c for c in bot.bot.pending_application_commands
                if c.name == name).callback


def test_both_commands_are_registered():
    import bot
    names = {c.name for c in bot.bot.pending_application_commands}

    assert {"campaign", "campaign_status"} <= names


def test_campaign_is_admin_gated_and_status_is_role_gated():
    """The #105 decorator-order trap: above `@bot.slash_command` py-cord
    registers the un-gated callback and the check becomes dead code."""
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    for name, gate in (("campaign", "@admin_only"),
                       ("campaign_status", "@require_role")):
        start = src.find(f'name="{name}",')
        assert start != -1
        assert gate in src[start:src.find("async def", start)]


def test_the_command_posts_into_a_thread_and_reports_the_id():
    _state("BAT_664", ["p1"]).save()

    ctx = _Ctx()
    asyncio.run(_cmd("campaign")(ctx, "BAT", 5))
    body = "\n".join(c for c in ctx.sent if c)

    assert ctx.threads and "BAT" in ctx.threads[0]
    assert "1 Karte(n)" in body
    assert "läuft" in body
    assert camp.Campaign.load_all(), "no campaign recorded"


def test_the_command_declines_without_posting_when_the_prefix_misses():
    _state("BAT_664", ["p1"]).save()

    ctx = _Ctx()
    asyncio.run(_cmd("campaign")(ctx, "Lassberg", 5))

    assert ctx.threads == []
    assert "BAT_664" in "\n".join(c for c in ctx.sent if c)


def test_status_without_a_campaign_says_how_to_start_one():
    ctx = _Ctx()
    asyncio.run(_cmd("campaign_status")(ctx, None, None))

    assert "/campaign" in ctx.sent[0]


def test_status_reports_the_latest_campaign_and_can_end_it():
    _state("BAT_664", ["p1"]).save()
    ctx = _Ctx()
    asyncio.run(_cmd("campaign")(ctx, "BAT", 5))

    ctx2 = _Ctx()
    asyncio.run(_cmd("campaign_status")(ctx2, "BAT", True))
    body = "\n".join(c for c in ctx2.sent if c)

    assert "beendet" in body
    assert camp.latest("BAT").final is not None


def test_status_reads_the_real_store_and_logs():
    """The one test over every default source: the campaign store, the preference
    log and the vote log, none of them injected."""
    state = _state("BAT_664", ["p1", "p2"])
    state.save()
    ctx = _Ctx()
    asyncio.run(_cmd("campaign")(ctx, "BAT", 5))
    voting.record_vote("BAT_664", MODELS[0], "u1", page="p1")

    ctx2 = _Ctx()
    asyncio.run(_cmd("campaign_status")(ctx2, None, None))

    assert "Entschieden: **1/2**" in "\n".join(c for c in ctx2.sent if c)
