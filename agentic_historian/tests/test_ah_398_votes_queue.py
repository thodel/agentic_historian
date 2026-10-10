"""#398 (G1): rank the pending Gate-2 votes by what the system would learn.

Nothing ordered the Gate-2 cards, so a historian with thirty minutes was shown
the arrival order of the scanner. This ranks the pending pages by measured
disagreement and by how starved the (script × century × lang) bucket is that the
vote would be filed under.

Three things here are contracts rather than conveniences, and each has its own
section below:

**The ordering.** Disagreement decides *within* a bucket; a starved bucket
outranks a saturated one however loudly the saturated page's engines disagree.
Both halves are asserted, because a single weighted sum gets one of them wrong
if the weight is off.

**The three-valued inputs.** `max_pairwise_cer` can be unmeasured and criteria
can be absent. Neither may be read as "measured, and it came out zero" — that is
the eight-times-repeated project defect, and here it would float every
criteria-less page to the top of the queue.

**No anchoring.** The Gate-2 card deliberately does not mark the selector's
pick, because at high disagreement that pick is a prior on fit-to-source rather
than on output quality. A queue entry that leaked it would anchor the judgement
the vote exists to collect, so the queue names no candidate at all.

Offline — no Discord, no engines, no filesystem except where the default source
is under test. Run from the repo root:
    pytest agentic_historian/tests/test_ah_398_votes_queue.py
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

import config              # noqa: E402
import preferences         # noqa: E402
import votes_queue as vq   # noqa: E402
import voting              # noqa: E402
from runstate import RunState  # noqa: E402

PAGE = "BAT_664_r_00027.jpg"
MODELS = ("trocr/escriptmask", "trocr/kurrent-xvi-xvii", "kraken/catmus")
#: The selector's pick. Never allowed to appear in rendered output.
AUTO = MODELS[1]

KURRENT16 = {"script": "kurrent", "century": 16, "lang": "de"}
BASTARDA15 = {"script": "bastarda", "century": 15, "lang": "de"}
#: A script deliberately unlike any model id, so the anchoring test cannot pass
#: by accident through the bucket label.
GOTHIC = {"script": "gothic", "century": 14, "lang": "la"}

_OMIT = object()


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "PREFERENCES_LOG_PATH", tmp_path / "preferences.jsonl")
    monkeypatch.setattr(config, "VOTES_LOG_PATH", tmp_path / "votes.jsonl")
    monkeypatch.setattr(config, "PSEUDONYM_SALT_PATH", tmp_path / ".salt")
    return tmp_path


# ── fixtures ─────────────────────────────────────────────────────────────────

def _state(doc_id, pages, *, applied=None):
    """A RunState as the orchestrator leaves it after a no-merge (#313).

    ``pages`` is a list of ``(page, n_candidates, cer, criteria)``; ``cer`` or
    ``criteria`` may be ``_OMIT`` to leave the key out entirely, and ``page`` may
    be ``""`` for a single-page run (whose context is filed under ``"_"``).
    """
    st = RunState(doc_id=doc_id)
    paths, ctx, auto = {}, {}, {}
    for page, n, cer, criteria in pages:
        for model in MODELS[:n]:
            label = f"{page}:{model}" if page else model
            paths[label] = f"reading {model} of {page or 'single'}"
        entry = {"ranks": {}, "local_ids": {}}
        if cer is not _OMIT:
            entry["max_pairwise_cer"] = cer
        if criteria is not _OMIT:
            entry["criteria"] = dict(criteria)
        ctx[page or "_"] = entry
        auto[page or "_"] = f"{page}:{AUTO}" if page else AUTO
    st.artifacts["paths"] = paths
    st.gate_decisions["gate2_context"] = ctx
    st.gate_decisions["gate2_auto"] = auto      # present, and never rendered
    if applied:
        st.gate_decisions["path"] = applied
    return st


def _one(doc_id="d-398", *, page=PAGE, n=3, cer=0.5, criteria=KURRENT16,
         applied=None):
    return _state(doc_id, [(page, n, cer, criteria)], applied=applied)


def _event(doc_id, page, criteria, *, offered=MODELS, chosen=(MODELS[0],),
           combined=False, rejected=False):
    return preferences.PreferenceEvent(
        doc_id=doc_id, page=page,
        offered=[{"engine": o.split("/")[0], "model_id": o.split("/", 1)[1],
                  "local_model_id": o} for o in offered],
        chosen=list(chosen), combined=combined, rejected=rejected,
        criteria=dict(criteria))


def _coverage_events(criteria, comparisons):
    """Events giving ``comparisons`` weighted comparisons in one bucket.

    Deliberately on *other* documents: coverage is a property of the bucket, not
    of the page being scored, and reusing the queue's doc id would also mark it
    decided and make every ordering test vacuous.
    """
    out = []
    for i in range(int(comparisons)):
        out.append(_event(f"cov-{criteria['script']}-{i}", "p", criteria,
                          offered=MODELS[:2], chosen=(MODELS[0],)))
    return out


def _q(states, events=(), votes=frozenset(), n=vq.TOP_N):
    return vq.build_queue(n, events=list(events), votes=set(votes),
                          states=list(states))


def _labels(queue):
    return [(e.doc_id, e.page) for e in queue.entries]


# ── the ordering ─────────────────────────────────────────────────────────────

def test_disagreement_decides_within_one_bucket():
    """Same bucket → the bucket term is identical, so the measured CER orders."""
    states = [_one("quiet", cer=0.08), _one("loud", cer=0.71),
              _one("middling", cer=0.33)]
    queue = _q(states, events=_coverage_events(KURRENT16, 4))

    assert [d for d, _p in _labels(queue)] == ["loud", "middling", "quiet"]


def test_a_starved_bucket_outranks_a_saturated_one():
    """The point of the whole module: a quiet page in a bucket with nothing in it
    teaches more than a loud page in a bucket that already has an estimate."""
    events = _coverage_events(KURRENT16, 14)        # saturated
    states = [_one("saturated-loud", cer=0.90, criteria=KURRENT16),
              _one("starved-quiet", cer=0.10, criteria=BASTARDA15)]
    queue = _q(states, events=events)

    assert [d for d, _p in _labels(queue)] == ["starved-quiet", "saturated-loud"]


def test_an_empty_bucket_beats_a_sufficient_one_at_total_disagreement():
    """The extreme case that fixes BUCKET_WEIGHT: CER is capped at 1.0, so the
    bucket term has to exceed 1.0 or a maximally-disagreeing saturated page wins
    and the starvation signal is decorative."""
    events = _coverage_events(KURRENT16, 50)
    states = [_one("saturated", cer=1.0, criteria=KURRENT16),
              _one("empty-bucket", cer=0.0, criteria=BASTARDA15)]
    queue = _q(states, events=events)

    assert _labels(queue)[0][0] == "empty-bucket"
    assert vq.BUCKET_WEIGHT > 1.0


def test_a_nearly_sufficient_bucket_does_not_outrank_a_loud_saturated_page():
    """The flip side, and the reason the term is graded rather than lexicographic:
    one more comparison in a bucket at 9/10 is worth less than a comparison that
    actually separates two readings."""
    events = (_coverage_events(KURRENT16, 20)          # saturated
              + _coverage_events(BASTARDA15, 9))       # one short
    states = [_one("saturated-loud", cer=0.95, criteria=KURRENT16),
              _one("almost-there-quiet", cer=0.02, criteria=BASTARDA15)]
    queue = _q(states, events=events)

    assert _labels(queue)[0][0] == "saturated-loud"


def test_starvation_is_monotone_in_coverage():
    for covered, expected in ((0.0, 1.0), (5.0, 0.5), (10.0, 0.0), (99.0, 0.0)):
        entry = vq.Pending("d", "p", 2, 0.5, ("kurrent", 16, "de"), covered)
        assert entry.starvation == pytest.approx(expected)


def test_the_order_is_deterministic_beyond_the_score():
    """A historian who runs the command twice must not get a reshuffled list."""
    states = [_one("zz"), _one("aa"), _one("mm")]
    first = _labels(_q(states))
    second = _labels(_q(list(reversed(states))))

    assert first == second == [("aa", PAGE), ("mm", PAGE), ("zz", PAGE)]


def test_the_fixture_actually_produces_different_scores():
    """Vacuity guard: if every page scored the same, every ordering test above
    would pass against a sort that does nothing."""
    queue = _q([_one("quiet", cer=0.08), _one("loud", cer=0.71)])
    scores = {round(e.score, 6) for e in queue.entries}

    assert len(scores) == 2


# ── the three-valued inputs ──────────────────────────────────────────────────

def test_an_unmeasured_cer_is_not_reported_as_zero_percent():
    """"Not measured" and "measured, and they agreed" are different facts. The
    first used to be indistinguishable from the second because both arrive as a
    falsy value."""
    entry = _q([_one(cer=None)]).entries[0]

    assert entry.disagreement is None
    assert "nicht gemessen" in entry.why()
    assert "CER 0%" not in entry.why()


def test_a_measured_zero_says_zero():
    entry = _q([_one(cer=0.0)]).entries[0]

    assert entry.disagreement == 0.0
    assert "CER 0%" in entry.why()
    assert "nicht gemessen" not in entry.why()


def test_an_absent_cer_key_reads_the_same_as_an_explicit_none():
    """A run state written before #332 recorded the context has no key at all."""
    assert _q([_one(cer=_OMIT)]).entries[0].disagreement is None


def test_an_unmeasured_page_is_still_queued():
    """Ranked low on disagreement, not hidden: it has ≥2 candidates, which is
    itself a reason to look at it."""
    assert len(_q([_one(cer=None)]).entries) == 1


def test_an_unbucketed_page_gets_no_starvation_bonus():
    """Criteria all absent → the vote lands in (None, None, None), which teaches
    #335 one global average. Reading that as "0 comparisons, maximally starved"
    would float every criteria-less page to the top — exactly backwards."""
    events = _coverage_events(KURRENT16, 20)
    states = [_one("unbucketed", cer=0.30,
                   criteria={"script": None, "century": None, "lang": None}),
              _one("bucketed-saturated", cer=0.40, criteria=KURRENT16)]
    queue = _q(states, events=events)

    unbucketed = next(e for e in queue.entries if e.doc_id == "unbucketed")
    assert unbucketed.covered is None
    assert unbucketed.starvation == 0.0
    assert unbucketed.score == pytest.approx(0.30)
    # and with no bonus it loses to the louder page rather than winning on nothing
    assert _labels(queue)[0][0] == "bucketed-saturated"


def test_an_unbucketed_page_says_why_it_got_no_bonus():
    why = _q([_one(criteria=_OMIT)]).entries[0].why()

    assert "Bucket unbekannt" in why
    assert "kein Abdeckungsbonus" in why


def test_a_partly_known_bucket_counts_as_bucketed():
    """``preferences._criteria_for`` files a preference whenever *any* field is
    known, and #335 estimates strengths for that coarse key. The queue has to use
    the same predicate or it promises coverage the log does not deliver."""
    partial = {"script": "kurrent", "century": None, "lang": None}
    entry = _q([_one(criteria=partial)]).entries[0]

    assert entry.bucketed
    assert entry.covered == 0.0
    assert vq.is_bucketed(("kurrent", None, None))
    assert not vq.is_bucketed((None, None, None))


def test_coverage_counts_against_the_exact_bucket_only():
    """A partial bucket is a different key from a full one — a comparison filed
    under ("kurrent", None, None) says nothing about ("kurrent", 16, "de")."""
    events = _coverage_events(KURRENT16, 12)
    partial = {"script": "kurrent", "century": None, "lang": None}

    assert _q([_one(criteria=partial)], events=events).entries[0].covered == 0.0
    assert _q([_one(criteria=KURRENT16)], events=events).entries[0].covered == 12.0


def test_the_bucket_label_is_readable_and_says_unknown_when_it_is():
    assert vq.bucket_label(("kurrent", 16, "de")) == "kurrent×16.Jh×de"
    assert vq.bucket_label((None, None, None)) == "unbekannt"
    assert vq.bucket_label(("kurrent", None, None)) == "kurrent×?×?"


# ── coverage counting ────────────────────────────────────────────────────────

def test_coverage_comes_from_the_same_counter_as_sufficiency():
    """The queue's "3.0/10" has to be the 3.0 that decides whether #335 reports a
    strength, so the count is delegated rather than re-derived."""
    events = _coverage_events(KURRENT16, 3)

    assert vq.bucket_coverage(events) == {("kurrent", 16, "de"): 3.0}
    assert vq.MIN_COMPARISONS == 10.0


def test_a_rejection_contributes_no_coverage():
    """"None of these is usable" is a statement about the pool, not a preference
    within it — #335 skips it, and so must the queue's denominator."""
    events = [_event("r1", "p", KURRENT16, chosen=[], rejected=True)]

    assert vq.bucket_coverage(events) == {}


def test_a_combine_counts_half():
    """Keeping two readings says "both usable", not "this beats that".

    Both events below yield exactly **two** comparisons, so the only thing that
    differs is the weight: one pick over three candidates compares the winner
    against two losers; two picks over three compare both winners against the one
    loser. Comparing a 1-of-2 against a 2-of-3 would have confounded the weight
    with the comparison count — my first version of this test did, and expected
    0.5 where the right answer is 1.0.
    """
    single = [_event("s", "p", KURRENT16, offered=MODELS, chosen=(MODELS[0],))]
    combined = [_event("c", "p", KURRENT16, offered=MODELS,
                       chosen=(MODELS[0], MODELS[1]), combined=True)]

    assert vq.bucket_coverage(single)[("kurrent", 16, "de")] == 2.0
    assert vq.bucket_coverage(combined)[("kurrent", 16, "de")] == 1.0


def test_coverage_falls_back_to_the_log_when_no_events_are_passed():
    config.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    with config.PREFERENCES_LOG_PATH.open("w", encoding="utf-8") as fh:
        for ev in _coverage_events(KURRENT16, 2):
            fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")

    assert vq.bucket_coverage()[("kurrent", 16, "de")] == 2.0


# ── what counts as already decided ───────────────────────────────────────────

def test_a_page_decided_by_a_selection_is_not_queued():
    events = [_event("d-398", PAGE, KURRENT16)]
    queue = _q([_one("d-398")], events=events)

    assert queue.entries == []
    assert queue.seen == 1 and queue.decided == 1


def test_a_rejected_page_is_not_queued():
    """The historian already spent the attention; re-offering it teaches nothing
    and the page is a recorded coverage failure, not an open question."""
    events = [_event("d-398", PAGE, KURRENT16, chosen=[], rejected=True)]
    queue = _q([_one("d-398")], events=events)

    assert queue.entries == []
    assert vq.decided_pages(events=events, votes=set(),
                            states=[])[("d-398", PAGE)] == "Ablehnung"


def test_a_page_decided_by_a_vote_is_not_queued():
    """``voting.apply_winner`` records a vote without a preference event."""
    queue = _q([_one("d-398")], votes={("d-398", PAGE)})

    assert queue.entries == []


def test_an_applied_path_retires_the_page_without_a_preference_event():
    """``record_selection`` is best-effort by design — it must never break the
    click (#313). A confirm whose preference write failed would otherwise
    re-offer the same page for ever."""
    state = _one("d-398", applied=f"{PAGE}:{MODELS[0]}")
    queue = _q([state])

    assert queue.entries == []
    assert vq.decided_pages(events=[], votes=set(),
                            states=[state])[("d-398", PAGE)] == "angewendet"


def test_a_document_level_rejection_flag_does_not_retire_pages():
    """``gate2_rejected`` cannot say *which* page, so on a multi-page card
    trusting it would silently retire pages the historian never saw."""
    state = _state("multi", [("p1", 3, 0.5, KURRENT16), ("p2", 3, 0.5, KURRENT16)])
    state.gate_decisions["gate2_rejected"] = True

    assert len(_q([state]).entries) == 2


def test_a_decided_page_scores_zero():
    """#398 words the exclusion as "zero for pages already voted"; both readings
    hold, so neither a ranking nor a filter can smuggle one back in."""
    entry = vq.Pending("d", PAGE, 3, 0.9, ("kurrent", 16, "de"), 0.0,
                       decided_by="Auswahl")

    assert entry.decided and entry.score == 0.0
    assert "entschieden" in entry.why()


def test_the_decided_count_is_reported_not_silently_dropped():
    states = [_one("open"), _one("done")]
    queue = _q(states, events=[_event("done", PAGE, KURRENT16)])

    assert (queue.docs, queue.seen, queue.decided) == (2, 2, 1)
    assert "1 von 2 schon entschieden" in vq.format_queue(queue)[0]


def test_the_vote_log_scan_is_one_pass_over_the_file():
    config.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    voting.record_vote("d-1", "trocr/a", "u1", page=PAGE)
    voting.record_vote("d-2", "trocr/b", "u2")
    config.VOTES_LOG_PATH.open("a", encoding="utf-8").write("{ not json\n")

    assert voting.voted_pages() == {("d-1", PAGE), ("d-2", "")}


def test_an_absent_vote_log_is_not_an_error():
    assert voting.voted_pages() == set()


# ── which pages are eligible at all ──────────────────────────────────────────

def test_a_single_candidate_page_is_not_a_comparison():
    queue = _q([_one(n=1)])

    assert queue.entries == [] and queue.seen == 0
    assert "keine Seite mit ≥2" in queue.why_empty()


def test_a_blank_candidate_does_not_count():
    """An emptied label from an earlier pass would promise a comparison that
    cannot be made."""
    state = _one(n=3)
    state.artifacts["paths"][f"{PAGE}:{MODELS[2]}"] = "   "

    assert _q([state]).entries[0].candidates == 2


def test_two_blanks_drop_the_page_entirely():
    state = _one(n=3)
    for model in MODELS[1:]:
        state.artifacts["paths"][f"{PAGE}:{model}"] = ""

    assert _q([state]).entries == []


def test_each_page_of_a_multi_page_doc_is_its_own_entry():
    """A comparison between a candidate from page 1 and one from page 2 is
    meaningless, so the unit of the queue is the page, as it is in #332."""
    state = _state("multi", [("p1", 3, 0.2, KURRENT16),
                             ("p2", 3, 0.8, KURRENT16)])
    queue = _q([state], events=_coverage_events(KURRENT16, 20))

    assert _labels(queue) == [("multi", "p2"), ("multi", "p1")]


def test_one_decided_page_does_not_retire_its_neighbour():
    state = _state("multi", [("p1", 3, 0.2, KURRENT16),
                             ("p2", 3, 0.8, KURRENT16)])
    queue = _q([state], events=[_event("multi", "p1", KURRENT16)])

    assert _labels(queue) == [("multi", "p2")]


def test_a_single_page_run_finds_its_context_under_the_underscore_key():
    """A single-page run records bare engine labels and files the context under
    ``"_"``. Getting that mapping wrong loses the CER and the criteria silently,
    and the page then merely looks unbucketed."""
    queue = _q([_one(page="", cer=0.44, criteria=KURRENT16)])
    entry = queue.entries[0]

    assert entry.page == ""
    assert entry.disagreement == pytest.approx(0.44)
    assert entry.bucket == ("kurrent", 16, "de")
    assert entry.where == "`d-398`"


# ── rendering ────────────────────────────────────────────────────────────────

def test_the_queue_never_names_a_candidate():
    """The no-anchoring rule. At high disagreement the selector picks by
    model-MATCH score — a prior on fit-to-source, not on output quality — so on
    BAT_664 the 0.20-scored engine read better than the 0.80 one. Naming the pick
    would anchor the judgement the vote exists to collect.

    ``GOTHIC`` here so the bucket label cannot contain a model id by accident.
    """
    state = _one(criteria=GOTHIC)
    assert state.gate_decisions["gate2_auto"]          # the pick is on the state
    rendered = "\n".join(vq.format_queue(_q([state])))

    for model in MODELS:
        engine, _, model_id = model.partition("/")
        assert model_id not in rendered, f"{model_id} leaked into the queue"
    assert "escriptmask" not in rendered and "catmus" not in rendered


def test_each_entry_shows_the_score_components():
    """#398: "the score's components are shown, because the historian should see
    why they're being asked"."""
    state = _one(cer=0.62, criteria=KURRENT16)
    rendered = "\n".join(vq.format_queue(
        _q([state], events=_coverage_events(KURRENT16, 3))))

    assert "CER 62%" in rendered
    assert "kurrent×16.Jh×de" in rendered
    assert "3.0/10 Vergleiche" in rendered


def test_each_entry_links_to_its_votes_card():
    rendered = "\n".join(vq.format_queue(_q([_one("BAT_664")])))

    assert "`/votes BAT_664`" in rendered


def test_the_renderer_returns_one_message_per_entry():
    """Discord caps a message; a formatter that returns messages keeps the
    splitting testable without a client."""
    states = [_one(f"d-{i}") for i in range(4)]
    messages = vq.format_queue(_q(states))

    assert len(messages) == 1 + 4 + 1          # header, entries, legend


def test_top_n_is_honoured_and_the_rest_is_counted():
    states = [_one(f"d-{i}", cer=i / 10) for i in range(8)]
    queue = _q(states, n=3)

    assert len(queue.top) == 3 and len(queue.entries) == 8
    assert "3 von 8 offenen Seite(n)" in vq.format_queue(queue)[0]


def test_an_n_larger_than_the_queue_is_not_an_error():
    queue = _q([_one()], n=99)

    assert len(queue.top) == 1


def test_a_nonsense_n_falls_back_to_the_default():
    assert vq.build_queue(0, events=[], votes=set(), states=[]).n == vq.TOP_N
    assert vq.build_queue(-5, events=[], votes=set(), states=[]).n == 1


def test_why_empty_distinguishes_three_silences():
    """Nothing ran, nothing disagreed, everything is decided — identical as
    silence, three different next actions (the #397 lesson)."""
    nothing = _q([])
    agreed = _q([_one(n=1)])
    done = _q([_one("d-398")], events=[_event("d-398", PAGE, KURRENT16)])

    assert "Keine Läufe gefunden" in nothing.why_empty()
    assert "keine Seite mit ≥2" in agreed.why_empty()
    assert "sind entschieden" in done.why_empty()
    for queue in (nothing, agreed, done):
        assert queue.why_empty() in "\n".join(vq.format_queue(queue))


# ── robustness ───────────────────────────────────────────────────────────────

def test_an_unreadable_state_costs_that_document_only():
    class Broken:
        doc_id = "broken"
        gate_decisions: dict = {}

        @property
        def artifacts(self):
            raise OSError("disk went away")

    queue = _q([Broken(), _one("fine")])

    assert _labels(queue) == [("fine", PAGE)]


def test_a_corrupt_preference_log_does_not_cost_the_queue():
    config.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    config.PREFERENCES_LOG_PATH.write_text("{ not json\n", encoding="utf-8")
    queue = vq.build_queue(votes=set(), states=[_one("fine")])

    assert _labels(queue) == [("fine", PAGE)]


def test_the_queue_writes_nothing():
    """Read-only over existing state — #398 is explicit that there is no new
    storage, and a sampling view that mutated the run states would change what it
    measures."""
    state = _one("d-398")
    before = state.model_dump_json()
    vq.format_queue(_q([state]))

    assert state.model_dump_json() == before
    assert not list(p for p in config.DATA_DIR.iterdir() if p.name != ".salt")


# ── the slash command, end to end ────────────────────────────────────────────

class _Ctx:
    """Minimal ApplicationContext stub: records the ephemeral followups."""

    def __init__(self):
        self.sent = []
        # Authorised caller for the fail-closed gate (#572): the guild owner.
        self.guild = SimpleNamespace(id=1, owner_id=77)
        self.author = SimpleNamespace(id=77, roles=[])

        async def _defer(ephemeral=False):
            pass

        async def _send(content=None, ephemeral=False):
            self.sent.append(content)
            return SimpleNamespace(id=1, content=content)

        self.defer = _defer
        self.followup = SimpleNamespace(send=_send)


def _cmd():
    import bot
    return next(c for c in bot.bot.pending_application_commands
                if c.name == "votes_queue").callback


def test_the_command_is_registered():
    import bot
    assert any(c.name == "votes_queue"
               for c in bot.bot.pending_application_commands)


def test_the_command_is_role_gated_below_the_slash_decorator():
    """The #105 decorator-order trap: with ``@require_role`` above
    ``@bot.slash_command`` py-cord registers the un-gated callback and the check
    becomes dead code."""
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    start = src.find('name="votes_queue"')
    assert start != -1
    assert "@require_role" in src[start:src.find("async def", start)]


def test_the_command_reads_the_real_run_states_and_logs():
    """The one test that exercises every default source: ``iter_run_states``
    over ``data/runs``, ``load_preferences`` and ``voted_pages``. Everything
    above injects them, so without this the wiring could be wrong in all three
    places and the suite would still be green."""
    _one("on-disk-open").save()
    _one("on-disk-chosen").save()
    _one("on-disk-voted").save()
    config.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    with config.PREFERENCES_LOG_PATH.open("w", encoding="utf-8") as fh:
        ev = _event("on-disk-chosen", PAGE, KURRENT16)
        fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
    voting.record_vote("on-disk-voted", MODELS[0], "u1", page=PAGE)

    ctx = _Ctx()
    asyncio.run(_cmd()(ctx, None))
    body = "\n".join(ctx.sent)

    assert "on-disk-open" in body
    assert "`/votes on-disk-open`" in body
    assert "on-disk-chosen" not in body, "the preference log was not consulted"
    assert "on-disk-voted" not in body, "the vote log was not consulted"


def test_the_command_honours_the_count():
    for i in range(5):
        _one(f"on-disk-{i}", cer=i / 10).save()

    ctx = _Ctx()
    asyncio.run(_cmd()(ctx, 2))

    # header + 2 entries + legend
    assert len(ctx.sent) == 4
    assert "2 von 5 offenen Seite(n)" in ctx.sent[0]


def test_the_command_says_something_when_there_is_nothing():
    ctx = _Ctx()
    asyncio.run(_cmd()(ctx, None))

    assert len(ctx.sent) == 1
    assert "Keine Läufe gefunden" in ctx.sent[0]
