"""Tests for SEC-13 (#584): votes.jsonl must not hold raw Discord identities.

`voting.record_vote` wrote the raw Discord user id as the voter identity and the
display name into data/feedback/votes.jsonl — plaintext personal data, while the
preference log and RDF export are pseudonymised. The voter is now stored through
`preferences.pseudonym` and the display name is never persisted; the campaign log
already pseudonymises `started_by`.

Offline: the votes log is redirected to tmp_path.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config          # noqa: E402
import preferences     # noqa: E402
import voting          # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_votes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "VOTES_LOG_PATH", tmp_path / "votes.jsonl")
    return tmp_path


def _lines(path):
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_the_raw_id_and_name_are_not_written(_tmp_votes):
    raw_id, name = "817396581317738546", "Alice Historian"
    voting.record_vote("d1", "trocr-kurrent-xvi-xvii", voter=raw_id, voter_name=name)

    blob = config.VOTES_LOG_PATH.read_text(encoding="utf-8")
    assert raw_id not in blob, "the raw Discord id must not be persisted"
    assert name not in blob, "the display name must not be persisted"

    [row] = _lines(config.VOTES_LOG_PATH)
    assert row["voter"] == preferences.pseudonym(raw_id)   # pseudonym, consistent with the pref log
    assert row["voter_name"] == ""


def test_the_returned_vote_keeps_the_name_for_the_live_card(_tmp_votes):
    vote = voting.record_vote("d2", "vlm", voter="123", voter_name="Bob")
    assert vote.voter_name == "Bob"          # in-memory, for an immediate render
    assert vote.voter == preferences.pseudonym("123")


def test_dedup_still_works_across_pseudonymisation(_tmp_votes):
    """The same person voting twice must still collapse to one effective vote —
    the pseudonym is stable, so last-vote-per-voter is unchanged."""
    voting.record_vote("d3", "vlm", voter="u1")
    voting.record_vote("d3", "trocr-kurrent-xvi-xvii", voter="u1")   # changed mind
    votes = voting.load_votes("d3")
    assert len(votes) == 1 and voting.tally(votes) == {"trocr-kurrent-xvi-xvii": 1}


def test_distinct_voters_stay_distinct(_tmp_votes):
    voting.record_vote("d4", "vlm", voter="u1")
    voting.record_vote("d4", "vlm", voter="u2")
    assert voting.tally(voting.load_votes("d4")) == {"vlm": 2}


def test_campaign_started_by_is_pseudonymised():
    """Regression guard: the campaign log pseudonymises started_by (already in
    place), so the two voter-identity sinks stay consistent."""
    src = (PKG / "campaign.py").read_text(encoding="utf-8")
    assert "started_by=preferences.pseudonym(started_by)" in src
