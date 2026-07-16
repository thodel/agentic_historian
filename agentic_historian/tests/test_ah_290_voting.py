"""#290: one-or-many human votes decide the winning transcription.

Offline — the votes log is redirected to tmp_path; apply is asserted to delegate to
Gate 2's apply_path_choice. Run from the repo root:
    pytest agentic_historian/tests/test_ah_290_voting.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config          # noqa: E402
import voting          # noqa: E402
from runstate import RunState  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_votes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "VOTES_LOG_PATH", tmp_path / "votes.jsonl")
    monkeypatch.setattr(config, "VOTING_MIN_VOTES", 1)
    return tmp_path


PATHS = {
    "trocr-kurrent-xvi-xvii": "unser fruntlich gruos vor liebe getruwe",
    "vlm": "Infer fremdlichs grue vor liebe getrune",
    "kraken-catmus_medieval": "duser feunilite grus vor liebe gerrmreuon",
}


# ── recording + effective votes ──────────────────────────────────────────────

def test_single_vote_decides_by_default():
    """min_votes=1 preserves today's Gate-2 behaviour: one click decides."""
    voting.record_vote("d1", "trocr-kurrent-xvi-xvii", voter="hist1")
    assert voting.winner(voting.load_votes("d1")) == ("trocr-kurrent-xvi-xvii", 1)


def test_many_votes_majority_wins():
    for voter, cand in [("a", "trocr-kurrent-xvi-xvii"), ("b", "trocr-kurrent-xvi-xvii"),
                        ("c", "vlm")]:
        voting.record_vote("d2", cand, voter=voter)
    assert voting.tally(voting.load_votes("d2")) == {"trocr-kurrent-xvi-xvii": 2, "vlm": 1}
    assert voting.winner(voting.load_votes("d2"), min_votes=3) == ("trocr-kurrent-xvi-xvii", 2)


def test_revote_replaces_earlier_vote():
    """A voter clicking twice must count once — the later vote wins."""
    voting.record_vote("d3", "vlm", voter="hist1")
    voting.record_vote("d3", "trocr-kurrent-xvi-xvii", voter="hist1")   # changed mind
    votes = voting.load_votes("d3")
    assert len(votes) == 1
    assert voting.tally(votes) == {"trocr-kurrent-xvi-xvii": 1}


def test_tie_is_undecided():
    voting.record_vote("d4", "vlm", voter="a")
    voting.record_vote("d4", "trocr-kurrent-xvi-xvii", voter="b")
    assert voting.winner(voting.load_votes("d4"), min_votes=2) is None


def test_below_quorum_is_undecided():
    voting.record_vote("d5", "vlm", voter="a")
    assert voting.winner(voting.load_votes("d5"), min_votes=3) is None


def test_votes_are_isolated_per_page():
    voting.record_vote("d6", "vlm", voter="a", page="p1.jpg")
    voting.record_vote("d6", "trocr-kurrent-xvi-xvii", voter="a", page="p2.jpg")
    assert voting.tally(voting.load_votes("d6", page="p1.jpg")) == {"vlm": 1}
    assert voting.tally(voting.load_votes("d6", page="p2.jpg")) == {"trocr-kurrent-xvi-xvii": 1}


def test_votes_are_isolated_per_doc():
    voting.record_vote("d7", "vlm", voter="a")
    assert voting.load_votes("other-doc") == []


# ── robustness ────────────────────────────────────────────────────────────────

def test_missing_log_returns_no_votes():
    assert voting.load_votes("never-voted") == []


def test_corrupt_line_is_skipped_not_fatal(_tmp_votes):
    voting.record_vote("d8", "vlm", voter="a")
    with config.VOTES_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert voting.tally(voting.load_votes("d8")) == {"vlm": 1}


# ── applying (delegates to Gate 2) ───────────────────────────────────────────

def test_apply_winner_delegates_to_gate2_and_invalidates(monkeypatch):
    """The decided winner must go through path_compare.apply_path_choice so the
    existing invalidation + routing feedback happen unchanged."""
    import path_compare
    seen = {}

    def fake_apply(state, choice, paths, *, decided_by="human", span_index=None):
        seen["choice"] = choice
        seen["decided_by"] = decided_by
        return paths[choice]

    monkeypatch.setattr(path_compare, "apply_path_choice", fake_apply)

    voting.record_vote("d9", "trocr-kurrent-xvi-xvii", voter="hist1")
    state = RunState(doc_id="d9")
    text = voting.apply_winner(state, PATHS)

    assert text == PATHS["trocr-kurrent-xvi-xvii"]
    assert seen["choice"] == "trocr-kurrent-xvi-xvii"
    assert "vote" in seen["decided_by"]          # provenance: decided by vote(N)


def test_apply_winner_returns_none_when_undecided(monkeypatch):
    import path_compare
    monkeypatch.setattr(path_compare, "apply_path_choice",
                        lambda *a, **k: pytest.fail("must not apply while undecided"))
    voting.record_vote("d10", "vlm", voter="a")
    assert voting.apply_winner(RunState(doc_id="d10"), PATHS, min_votes=5) is None


def test_apply_winner_skips_a_winner_with_no_text(monkeypatch):
    import path_compare
    monkeypatch.setattr(path_compare, "apply_path_choice",
                        lambda *a, **k: pytest.fail("must not apply an empty candidate"))
    voting.record_vote("d11", "party", voter="a")     # not in PATHS
    assert voting.apply_winner(RunState(doc_id="d11"), PATHS) is None
