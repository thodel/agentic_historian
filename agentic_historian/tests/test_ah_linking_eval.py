"""eval/linking.py (outremer #89): system-aware agreement and miss diagnosis
against Gate 3 decisions.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_linking_eval.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import entity_review as er  # noqa: E402
from eval import linking  # noqa: E402
from utils.entity_resolver import ResolvedEntity  # noqa: E402


def _ent(text, **fields):
    base = {"text": text, "type": "PERSON", "normalised": text, "gnd": "", "hls": "",
            "wikidata": "", "hub_confidence": "unverified", "link_method": "none"}
    base.update(fields)
    return base


# ── ids name their system ────────────────────────────────────────────────────

def test_a_pair_without_a_system_is_an_error_not_a_guess():
    with pytest.raises(ValueError, match="does not name its system"):
        linking.split_pairs_by_system([("Hans", "118000")])


def test_pairs_are_judged_only_against_their_own_system():
    """The 0.6761 incident: Wikidata pairs counted against the authority linker."""
    ents = [_ent("Hans Wiler", gnd="118000", link_method="mcp_federation")]
    accepted = [("Hans Wiler", "gnd:118000"), ("Hans Wiler", "wikidata:Q1")]
    res = linking.linking_agreement(ents, accepted, [])
    assert res["gnd"]["accept_hit"] == 1 and res["gnd"]["accept_miss"] == 0
    assert res["wikidata"]["accept_hit"] == 0 and res["wikidata"]["accept_miss"] == 1
    assert "hls" not in res, "a system without gold has no row"
    assert res["combined"]["reviewed_pairs"] == 2


# ── gold from Gate 3 ──────────────────────────────────────────────────────────

def test_a_confirmed_link_accepts_each_system_id_and_kein_link_rejects_only_the_offered():
    confirmed = _ent("Hans Wiler", link_method="mcp_federation", gnd="999")
    offered = [ResolvedEntity(name="Hans von Wiler", gnd_id="118000", hls_id=12345),
               ResolvedEntity(name="Hans Wyler", gnd_id="777")]
    confirmed["offered_candidates"] = [pid for c in offered for pid in er.candidate_ids(c)]
    er.apply_entity_link(confirmed, offered[0])

    declined = _ent("Unklar", link_method="hls_dhs", hls=42)
    declined["offered_candidates"] = ["gnd:1", "gnd:2"]
    er.apply_entity_link(declined, None)

    accepted, rejected = linking.gold_from_entities([confirmed, declined, _ent("Nobody")])
    assert accepted == [("Hans Wiler", "gnd:118000"), ("Hans Wiler", "hls:12345")]
    assert rejected == [("Unklar", "gnd:1"), ("Unklar", "gnd:2")]


def test_the_pipelines_proposal_survives_the_human_choice():
    ent = _ent("Hans Wiler", link_method="mcp_federation", gnd="999", hub_id="999")
    er.apply_entity_link(ent, ResolvedEntity(name="Hans von Wiler", gnd_id="118000"))
    assert ent["gnd"] == "118000" and ent["link_method"] == "human_confirmed"
    stash = ent["pipeline_link"]
    assert stash["gnd"] == "999" and stash["link_method"] == "mcp_federation"
    assert linking.pipeline_ids(ent) == {"gnd:999"}
    # a second decision does not overwrite the stash with the first human choice
    er.apply_entity_link(ent, ResolvedEntity(name="Other", gnd_id="555"))
    assert ent["pipeline_link"]["gnd"] == "999"


def test_a_decided_entity_without_a_stash_has_no_recoverable_proposal():
    ent = _ent("Hans Wiler", link_method="human_confirmed", gnd="118000")
    assert linking.pipeline_ids(ent) == set(), "the human choice must not score against itself"


# ── the null linker ──────────────────────────────────────────────────────────

def test_agreement_is_reported_next_to_the_null_linker():
    """Seven accepts, 48 rejects: silence scores 0.873 (outremer 2026-07-30)."""
    ents = [_ent(f"P{i}", link_method="none") for i in range(55)]
    accepted = [(f"P{i}", f"gnd:{i}") for i in range(7)]
    rejected = [(f"P{i}", f"gnd:x{i}") for i in range(7, 55)]
    res = linking.linking_agreement(ents, accepted, rejected)["gnd"]
    assert res["agreement"] == res["null_agreement"] == round(48 / 55, 4)
    assert res["lift_over_null"] == 0.0 and res["accept_rate"] == 0.0


# ── diagnosis ────────────────────────────────────────────────────────────────

def test_every_miss_gets_a_cause():
    ents = [
        _ent("Hit", link_method="mcp_federation", gnd="1", candidates=["gnd:1", "gnd:2"]),
        _ent("Ranked", link_method="mcp_federation", gnd="2", candidates=["gnd:2", "gnd:3"]),
        _ent("Missing", link_method="mcp_federation", gnd="4", candidates=["gnd:4"]),
        _ent("Silent", link_method="none"),
    ]
    accepted = [("Hit", "gnd:1"), ("Ranked", "gnd:3"), ("Missing", "gnd:5"),
                ("Silent", "gnd:6"), ("Vanished", "gnd:7")]
    causes = {r["mention"]: r["cause"] for r in linking.diagnose(ents, accepted, doc_id="d")}
    assert causes == {"Hit": "HIT", "Ranked": "ranked_below_top", "Missing": "candidate_missing",
                      "Silent": "no_candidates", "Vanished": "not_extracted"}


def test_offered_but_not_chosen_counts_as_ranked_below_top():
    """The review card's offer is a candidate list too."""
    ent = _ent("Hans", link_method="hls_dhs", hls=42,
               offered_candidates=["gnd:118000", "hls:42"])
    rows = linking.diagnose([ent], [("Hans", "gnd:118000")])
    assert rows[0]["cause"] == "ranked_below_top"


def test_mentions_match_through_the_linkers_own_normalisation():
    ent = _ent("Hans von Wiler", link_method="mcp_federation", gnd="1")
    assert linking.linking_agreement([ent], [("hans wiler", "gnd:1")], [])["gnd"]["accept_hit"] == 1


# ── over a directory ─────────────────────────────────────────────────────────

def test_evaluate_dir_reads_entities_files_and_reports(tmp_path, capsys):
    ent = _ent("Hans Wiler", link_method="mcp_federation", gnd="999",
               candidates=["gnd:999", "gnd:118000"])
    ent["offered_candidates"] = ["gnd:118000"]
    er.apply_entity_link(ent, ResolvedEntity(name="Hans von Wiler", gnd_id="118000"))
    (tmp_path / "doc-a_entities.json").write_text(json.dumps({"entities": [ent]}))
    (tmp_path / "doc-b_entities.json").write_text(json.dumps({"entities": [_ent("Nobody")]}))
    report = linking.evaluate_dir(tmp_path)
    assert report["documents"] == 2 and report["gold"] == {"accepted": 1, "rejected": 0}
    assert report["agreement"]["gnd"]["accept_miss"] == 1
    assert report["causes"] == {"ranked_below_top": 1}
    assert linking.main(["--entities-dir", str(tmp_path), "--json", str(tmp_path / "r.json")]) == 0
    out = capsys.readouterr().out
    assert "ranked_below_top" in out and "gnd" in out
    assert json.loads((tmp_path / "r.json").read_text())["misses"][0]["doc_id"] == "doc-a"


def test_an_empty_gold_is_not_a_perfect_score(tmp_path, capsys):
    (tmp_path / "doc_entities.json").write_text(json.dumps({"entities": [_ent("X", gnd="1")]}))
    linking.main(["--entities-dir", str(tmp_path)])
    assert "no Gate 3 decisions" in capsys.readouterr().out


# ── the linker keeps its runners-up ──────────────────────────────────────────

def test_mcp_link_records_the_top_three_candidates(monkeypatch):
    from agents import entity_agent, search_agent
    from agents.search_agent import SearchResponse

    ents = [ResolvedEntity(name="Hans Wiler", gnd_id="1", sources=["hls"]),
            ResolvedEntity(name="Hans Wiler", hls_id=2),
            ResolvedEntity(name="Hans Wiler", wikidata_id="Q3"),
            ResolvedEntity(name="Hans Wiler", gnd_id="4")]
    monkeypatch.setattr(search_agent, "search_sync",
                        lambda q, limit=10: SearchResponse(query=q, entities=ents))
    link = entity_agent._mcp_link("Hans Wiler")
    assert link["gnd"] == "1" and link["candidates"] == ["gnd:1", "hls:2", "wikidata:Q3"]
