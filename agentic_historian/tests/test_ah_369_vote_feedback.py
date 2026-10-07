"""#369: after a Gate-2 vote, say what it taught the system.

Requested on the day the first bucket reached sufficiency. Until then voting was
a black hole — the historian picked a reading and nothing came back, which is a
poor bargain for the one contributor every metric in #326 depends on.

Three contracts, each with its own section:

**The bucket that was voted on, in one message.** A global figure hides exactly
the per-script/century differences #335 exists to find. A multi-page confirm
writes one event per page (#332), possibly in different buckets, and still gets
one message — N would hit the ~5 msg / 5 s channel limit and bury the card.

**The crossing is the news.** Passing MIN_COMPARISONS is the one genuinely new
event, unlike the 23rd comparison. "Was already sufficient" gets no celebration.

**The engine ranking stays out.** #334 measures how often the human agrees with
the automatic pick and #335 derives engine strength from the human's choices; a
display that steers the choice makes both metrics measure the display. The
ranking is reachable through `/votes_stats bereich:stärke` instead — which had
to be built, because before this all four report formatters were reachable from
no command at all.

Offline — Discord interaction stubbed. Run from the repo root:
    pytest agentic_historian/tests/test_ah_369_vote_feedback.py
"""

import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config          # noqa: E402
import path_compare    # noqa: E402
import preferences     # noqa: E402
import routing_report  # noqa: E402
import vote_feedback as vf  # noqa: E402
import votes_queue as vq    # noqa: E402
from runstate import RunState  # noqa: E402

PAGE = "BAT_664_r_00027.jpg"
MODELS = ("trocr/escriptmask", "trocr/kurrent-xvi-xvii", "kraken/catmus")
AUTO = MODELS[1]
K16 = {"script": "kurrent", "century": 16, "lang": "de"}
B15 = {"script": "bastarda", "century": 15, "lang": "de"}
NOWHERE = {"script": None, "century": None, "lang": None}


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "PREFERENCES_LOG_PATH", tmp_path / "preferences.jsonl")
    monkeypatch.setattr(config, "PSEUDONYM_SALT_PATH", tmp_path / ".salt")
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", False)
    return tmp_path


def _ev(doc_id="d-369", page=PAGE, criteria=K16, *, offered=MODELS,
        chosen=(MODELS[0],), rejected=False, auto_pick=AUTO):
    return preferences.PreferenceEvent(
        doc_id=doc_id, page=page,
        offered=[{"engine": o.split("/")[0], "model_id": o.split("/", 1)[1],
                  "local_model_id": o} for o in offered],
        chosen=list(chosen), rejected=rejected, criteria=dict(criteria),
        auto_pick=auto_pick)


def _history(criteria, n, *, prefix="old"):
    """n weighted comparisons in one bucket, on other documents."""
    return [_ev(f"{prefix}-{i}", "p", criteria, offered=MODELS[:2],
                chosen=(MODELS[0],)) for i in range(int(n))]


# ── the bucket that was voted on, in one message ─────────────────────────────

def test_the_message_names_the_bucket_not_a_global_average():
    """A global figure hides exactly the differences #335 exists to find."""
    new = [_ev(criteria=B15)]
    text = vf.after_vote(new, _history(K16, 20) + new)

    assert "bastarda×15.Jh×de" in text
    assert "kurrent" not in text, "another bucket's numbers leaked in"


def test_a_multi_page_confirm_is_one_message():
    """#332 writes one event per page; N messages would hit the channel's
    ~5 msg / 5 s limit and push the card out of view."""
    new = [_ev(page=f"p{i}") for i in range(4)]
    text = vf.after_vote(new, new)

    assert isinstance(text, str)
    assert text.count("📊") == 1


def test_pages_in_different_buckets_are_grouped_in_that_one_message():
    """Pass 1 runs blind and pass 2 with Agent B's criteria, so one confirm can
    legitimately span two buckets."""
    new = [_ev(page="p1", criteria=K16), _ev(page="p2", criteria=B15)]
    text = vf.after_vote(new, new)

    assert "kurrent×16.Jh×de" in text and "bastarda×15.Jh×de" in text
    assert text.count("📊") == 1


def test_nothing_recorded_means_no_message():
    """A failed preference write must not produce a message claiming otherwise —
    `record_selection` is best-effort by design (#313)."""
    assert vf.after_vote([]) is None
    assert vf.after_vote(None) is None


# ── the numbers come from the existing measurements ──────────────────────────

def test_coverage_comes_from_333():
    new = [_ev()]
    all_events = [_ev("a", "p"), _ev("b", "p", chosen=[], rejected=True)] + new
    text = vf.after_vote(new, all_events)

    assert "2/3" in text and "(67%)" in text


def test_agreement_comes_from_334():
    """The historian chose escriptmask, the auto pick was kurrent-xvi-xvii."""
    new = [_ev()]
    all_events = [_ev("a", "p", chosen=(AUTO,))] + new       # one agreement
    text = vf.after_vote(new, all_events)

    assert "1/2" in text and "(50%)" in text


def test_the_comparison_count_is_335s_denominator():
    new = [_ev()]                                   # 3 offered, 1 chosen → 2
    text = vf.after_vote(new, _history(K16, 7) + new)

    assert "**9**" in text
    assert "ab 10 auswertbar" in text
    assert vq.MIN_COMPARISONS == 10.0


def test_the_gain_is_shown_not_just_the_total():
    """"Your vote added two" is the part that answers "what did I just do"."""
    new = [_ev()]
    text = vf.after_vote(new, _history(K16, 7) + new)

    assert "(+2, ab 10 auswertbar)" in text


def test_before_is_derived_by_subtraction_not_by_filtering():
    """Identity-matching the new events against reloaded ones is fragile — a
    re-parsed event is a different object. The subtraction is exact because both
    sides come from the same weighted counter."""
    new = [_ev()]
    fb = vf.feedback_for(new, _history(K16, 5) + new)[0]

    assert (fb.before, fb.now, fb.gained) == (5.0, 7.0, 2.0)


# ── the crossing is the news ─────────────────────────────────────────────────

def test_crossing_the_threshold_is_called_out():
    new = [_ev()]
    text = vf.after_vote(new, _history(K16, 9) + new)

    assert "auswertbar geworden" in text
    assert "n≥10" in text


def test_a_bucket_that_was_already_sufficient_gets_no_celebration():
    """The 23rd comparison is not news. Without the before/after this message
    would announce a crossing on every vote in a saturated bucket."""
    new = [_ev()]
    text = vf.after_vote(new, _history(K16, 20) + new)

    assert "auswertbar geworden" not in text
    assert vf.feedback_for(new, _history(K16, 20) + new)[0].status == "already"


def test_a_bucket_still_short_says_nothing_about_crossing():
    new = [_ev()]
    text = vf.after_vote(new, _history(K16, 2) + new)

    assert "auswertbar geworden" not in text
    assert vf.feedback_for(new, _history(K16, 2) + new)[0].status == "still_short"


def test_the_crossing_rule_has_one_home():
    """#399's campaign report asks the same question; two implementations of
    "did this cross" would be two answers to it."""
    import campaign

    assert vq.crossing(9.0, 10.0) == "crossed"
    assert vq.crossing(10.0, 50.0) == "already"
    assert vq.crossing(1.0, 2.0) == "still_short"
    assert campaign.BucketLine(("x", 1, "de"), 9.0, 11.0).status == "crossed"


# ── the engine ranking stays out ─────────────────────────────────────────────

def test_the_message_never_names_an_engine_or_a_model():
    """#334 measures how often the human agrees with the automatic pick and #335
    derives engine strength from the human's choices. If the display steers the
    choice, both metrics start measuring the display."""
    new = [_ev()]
    text = vf.after_vote(new, _history(K16, 9) + new)

    for model in MODELS:
        engine, _, model_id = model.partition("/")
        assert model_id not in text, f"{model_id} leaked into the feedback"
    assert "escriptmask" not in text and "catmus" not in text
    assert "kraken" not in text


def test_the_message_says_the_ranking_is_withheld_and_where_it_is():
    """"Not here" is only honest if there is a "there"."""
    text = vf.after_vote([_ev()], [_ev()])

    assert "absichtlich nicht" in text
    assert vf.RANKING_COMMAND in text


def test_the_message_carries_no_cer_figure():
    """`regret_cer` is a distance between two of our own candidates, bounded by
    the pool (#334) — not an accuracy. There is no way to put a bare percentage
    next to "what your vote taught us" without it being read as one, so it is
    omitted here and stays in `format_selection_stats` with its disclaimer."""
    text = vf.after_vote([_ev()], [_ev()])

    assert "CER" not in text and "regret" not in text.lower()


# ── three-valued throughout ──────────────────────────────────────────────────

def test_an_unmeasurable_agreement_is_not_reported_as_zero_percent():
    """No recorded auto-pick means there was nothing to agree with. 0 % would
    read as "the selector is always wrong"."""
    new = [_ev(auto_pick="")]
    fb = vf.feedback_for(new, new)[0]

    assert fb.agreement_rate is None
    assert "nicht messbar" in vf.after_vote(new, new)
    assert "(0%)" not in vf.after_vote(new, new)


def test_an_unbucketed_vote_says_its_comparison_only_feeds_the_average():
    """A comparison filed under (None, None, None) teaches one global average,
    and the historian should know their click bought less than it looks like."""
    new = [_ev(criteria=NOWHERE)]
    text = vf.after_vote(new, new)
    fb = vf.feedback_for(new, new)[0]

    assert not fb.bucketed
    assert "ohne Bucket" in text
    assert "globalen Durchschnitt" in text


def test_a_rejection_reports_no_new_comparison():
    """"None of these is usable" is a statement about the pool, not a preference
    within it — #335 counts nothing, and a silently unchanged number would look
    like the vote was lost."""
    new = [_ev(chosen=[], rejected=True)]
    text = vf.after_vote(new, _history(K16, 4) + new)

    assert "unverändert" in text
    assert "Abdeckungslücke" in text
    assert vf.feedback_for(new, new)[0].gained == 0.0


def test_a_rejection_still_moves_coverage():
    """It is the clearest statement that the ensemble produced nothing
    acceptable, and #333 is where that lands."""
    new = [_ev(chosen=[], rejected=True)]
    all_events = [_ev("a", "p"), _ev("b", "p")] + new
    text = vf.after_vote(new, all_events)

    assert "2/3" in text


def test_coverage_says_nothing_decided_rather_than_zero_percent():
    fb = vf.BucketFeedback(bucket=("kurrent", 16, "de"))

    assert fb.coverage_rate is None
    assert vf.format_feedback([fb]).count("noch nichts entschieden") == 1


def test_feedback_never_raises():
    """A courtesy after the click must not cost the click (#313)."""
    class Exploding:
        criteria = K16
        rejected = False
        chosen = [MODELS[0]]
        offered = property(lambda self: (_ for _ in ()).throw(OSError("boom")))

    assert vf.after_vote([Exploding()], None) is None


def test_the_default_source_is_the_real_log():
    config.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    with config.PREFERENCES_LOG_PATH.open("w", encoding="utf-8") as fh:
        import json
        for ev in _history(K16, 9):
            fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
    new = [_ev()]
    with config.PREFERENCES_LOG_PATH.open("a", encoding="utf-8") as fh:
        import json
        fh.write(json.dumps(new[0].to_dict(), ensure_ascii=False) + "\n")

    text = vf.after_vote(new)          # no all_events — reads the log

    # 9 one-pair events plus one pick over three candidates = 9 + 2.
    assert "**11**" in text, "the log on disk was not consulted"
    assert "(+2, ab 10 auswertbar)" in text
    assert "auswertbar geworden" in text, "the crossing was not detected"


# ── the click posts it ───────────────────────────────────────────────────────

PATHS = {
    f"{PAGE}:{MODELS[0]}": "unser frùntlich gruͦs vor liebe getrüwe",
    f"{PAGE}:{MODELS[1]}": "Vnser fründlich grus vor liebe getrune",
}


class _Interaction:
    """The real lifecycle: defer marks the response done, the card is edited via
    edit_original_response, and a message of its own goes through followup."""

    def __init__(self):
        self.user = SimpleNamespace(id=1, display_name="Anna", name="Anna")
        self.edited = []
        self.posted = []
        self._done = False

        async def _defer():
            self._done = True

        self.response = SimpleNamespace(
            edit_message=self._edit, defer=_defer,
            is_done=lambda: self._done)

        async def _send(content=None, **kw):
            self.posted.append(content)
            return SimpleNamespace(id=7, content=content)

        self.followup = SimpleNamespace(send=_send)

    async def edit_original_response(self, *, content=None, view=None):
        await self._edit(content=content, view=view)

    async def _edit(self, *, content=None, view=None):
        self.edited.append(content)


def _state(doc_id="d-369"):
    st = RunState(doc_id=doc_id)
    st.artifacts["paths"] = dict(PATHS)
    st.gate_decisions["gate2_auto"] = {PAGE: f"{PAGE}:{AUTO}"}
    st.gate_decisions["gate2_context"] = {
        PAGE: {"max_pairwise_cer": 0.71, "criteria": dict(K16)}}
    return st


def _btn(state, field):
    view = path_compare.build_view(state, PATHS)
    return next(b for b in view.children if b.custom_id.endswith(":" + field))


def test_a_confirm_is_followed_by_the_feedback_message():
    async def go():
        st = _state()
        await _btn(st, f"{PAGE}:{MODELS[0]}").callback(_Interaction())
        inter = _Interaction()
        await _btn(st, "__confirm__").callback(inter)
        return inter

    inter = asyncio.run(go())

    assert inter.edited, "the card was not collapsed"
    assert inter.posted, "#369's message never arrived"
    assert "Erfasst" in inter.posted[-1]
    assert "kurrent×16.Jh×de" in inter.posted[-1]


def test_a_rejection_is_followed_by_the_feedback_message():
    async def go():
        st = _state()
        inter = _Interaction()
        await _btn(st, "__reject__").callback(inter)
        return inter

    inter = asyncio.run(go())

    assert inter.posted and "Erfasst" in inter.posted[-1]
    assert "Abdeckungslücke" in inter.posted[-1]


def test_the_feedback_follows_the_collapsed_card_not_the_other_way_round():
    """#368 landed first for a reason: a "what you taught us" message before the
    click is visibly registered would still leave the click feeling inert."""
    order = []

    class _Ordered(_Interaction):
        async def _edit(self, *, content=None, view=None):
            order.append("card")
            await super()._edit(content=content, view=view)

    async def go():
        st = _state()
        await _btn(st, f"{PAGE}:{MODELS[0]}").callback(_Interaction())
        inter = _Ordered()

        async def _send(content=None, **kw):
            order.append("feedback")
            inter.posted.append(content)

        inter.followup = SimpleNamespace(send=_send)
        await _btn(st, "__confirm__").callback(inter)

    asyncio.run(go())

    assert order[-1] == "feedback"
    assert order.index("card") < order.index("feedback")


def test_an_interaction_without_a_followup_channel_does_not_break_the_click():
    async def go():
        st = _state()
        await _btn(st, f"{PAGE}:{MODELS[0]}").callback(_Interaction())
        inter = _Interaction()
        inter.followup = None
        await _btn(st, "__confirm__").callback(inter)
        return inter

    inter = asyncio.run(go())

    assert inter.edited, "the card must still collapse"


def test_a_failing_followup_does_not_break_the_click():
    async def go():
        st = _state()
        await _btn(st, f"{PAGE}:{MODELS[0]}").callback(_Interaction())
        inter = _Interaction()

        async def _boom(content=None, **kw):
            raise RuntimeError("Discord said no")

        inter.followup = SimpleNamespace(send=_boom)
        await _btn(st, "__confirm__").callback(inter)
        return inter

    inter = asyncio.run(go())

    assert inter.edited
    assert preferences.load_preferences(), "the preference was still recorded"


# ── the ranking, on demand ───────────────────────────────────────────────────

def test_the_strength_report_gates_on_sufficiency():
    """A confident-looking strength from three clicks would be worse than none —
    it would steer model selection on nothing (#335)."""
    thin = routing_report.format_strength_stats(_history(K16, 2))

    assert "zu dünn" in thin
    assert "escriptmask" not in thin, "a strength was shown below the threshold"


def test_the_strength_report_names_the_models_once_sufficient():
    """This is the one report that may — it is what the explicit command is
    for."""
    text = routing_report.format_strength_stats(_history(K16, 12))

    assert "escriptmask" in text
    assert "Stärke" in text
    assert "kurrent/16/de" in text


def test_the_strength_report_says_why_it_is_on_demand_only():
    text = routing_report.format_strength_stats(_history(K16, 12))

    assert "nur auf Abruf" in text
    assert "#369" in text


def test_the_strength_report_handles_an_empty_log():
    text = routing_report.format_strength_stats([])

    assert "noch keine Vergleiche" in text


def test_votes_stats_is_registered_and_role_gated():
    import bot
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    start = src.find('name="votes_stats",')

    assert any(c.name == "votes_stats"
               for c in bot.bot.pending_application_commands)
    assert start != -1
    assert "@require_role" in src[start:src.find("async def", start)]


def _stats_cmd():
    import bot
    return next(c for c in bot.bot.pending_application_commands
                if c.name == "votes_stats").callback


class _Ctx:
    def __init__(self):
        self.sent = []
        self.guild = SimpleNamespace(id=1)
        self.author = SimpleNamespace(roles=[])

        async def _defer(ephemeral=False):
            pass

        async def _send(content=None, ephemeral=False):
            self.sent.append(content)
            return SimpleNamespace(id=1, content=content)

        self.defer = _defer
        self.followup = SimpleNamespace(send=_send)


def test_votes_stats_surfaces_every_report():
    """All four existed and no command reached any of them — only /agent_e
    posted the routing embed as a side effect of a whole meta report."""
    ctx = _Ctx()
    asyncio.run(_stats_cmd()(ctx, "alles"))
    body = "\n".join(c for c in ctx.sent if c)

    for marker in ("Abdeckungs-Report", "Auswahl-Report", "Engine-Stärke"):
        assert marker in body, f"{marker} not surfaced"


def test_votes_stats_can_show_the_ranking_alone():
    ctx = _Ctx()
    asyncio.run(_stats_cmd()(ctx, "stärke"))
    body = "\n".join(c for c in ctx.sent if c)

    assert "Engine-Stärke" in body
    assert "Abdeckungs-Report" not in body


def test_the_command_actually_splits_a_long_report(monkeypatch):
    """`_chunks` being correct is not the same as the command using it: a report
    sent as `text[:1900]` passes every unit test of the splitter and still loses
    its tail at the API."""
    long_text = "\n".join(f"Bucket {i} " + "x" * 80 for i in range(80))
    monkeypatch.setattr(routing_report, "format_strength_stats",
                        lambda *a, **k: long_text)

    ctx = _Ctx()
    asyncio.run(_stats_cmd()(ctx, "stärke"))

    assert len(ctx.sent) > 1, "the report was sent as one over-long message"
    assert "Bucket 79" in "\n".join(ctx.sent), "the tail was lost"


def test_a_failing_report_does_not_cost_the_others(monkeypatch):
    def _boom(*a, **k):
        raise OSError("log unreadable")

    monkeypatch.setattr(routing_report, "format_coverage_stats", _boom)

    ctx = _Ctx()
    asyncio.run(_stats_cmd()(ctx, "alles"))
    body = "\n".join(c for c in ctx.sent if c)

    assert "nicht verfügbar" in body
    assert "Engine-Stärke" in body


def test_a_long_report_is_split_rather_than_truncated_by_discord():
    import bot
    long_text = "\n".join(f"line {i} " + "x" * 80 for i in range(80))
    chunks = bot._chunks(long_text)

    assert len(chunks) > 1
    assert all(len(c) <= 1900 for c in chunks)
    assert "\n".join(chunks) == long_text


def test_a_single_overlong_line_is_cut_rather_than_dropped():
    import bot
    chunks = bot._chunks("y" * 5000)

    assert len(chunks) == 1 and chunks[0].endswith("…")
    assert len(chunks[0]) <= 1900


def test_chunking_an_empty_report_is_not_an_error():
    import bot

    assert bot._chunks("") == [""]


def test_the_feedback_and_the_report_agree_on_the_threshold():
    """A message that says "ab 10 auswertbar" while the report gates at another
    number would be two answers to "is this bucket evaluable"."""
    from agent_a.preference_strength import MIN_COMPARISONS

    text = vf.after_vote([_ev()], _history(K16, 9) + [_ev()])
    assert f"ab {MIN_COMPARISONS:.0f} auswertbar" in text
    assert re.search(rf"unter {MIN_COMPARISONS:.0f} Vergleichen",
                     routing_report.format_strength_stats(_history(K16, 2)))
