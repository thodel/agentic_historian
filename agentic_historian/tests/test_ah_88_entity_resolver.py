"""Tests for #88 (KH-2): cross-source entity resolver/merger.

Offline, pure logic. Run from the repo root:
    pytest agentic_historian/tests/test_ah_88_entity_resolver.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from utils.mcp_client import PersonResult
from utils import entity_resolver as er


def P(source, **kw):
    kw.setdefault("pid", source + "-1")
    kw.setdefault("name", "")
    return PersonResult(source=source, **kw)


# ── helpers ──────────────────────────────────────────────────────────────────

def test_date_parsing_and_overlap():
    assert er._parse_dates("1300–1370") == (1300, 1370)
    assert er._parse_dates("fl. 1348") == (1348, 1348)
    assert er._parse_dates("um 1350") == (1350, 1350)
    assert er._parse_dates(None) is None
    assert er._dates_overlap((1300, 1370), (1360, 1400)) is True
    assert er._dates_overlap((1300, 1320), (1360, 1400)) is False
    assert er._dates_overlap((1300, 1370), None) is None


def test_name_normalisation_drops_particles_and_accents():
    assert er._norm_name("Hans von Wiler") == "hans wiler"
    assert er._norm_name("Johann de Bâle") == "johann bale"


# ── pairwise match ───────────────────────────────────────────────────────────

def test_shared_gnd_id_is_high():
    a = P("hls", name="H. Wiler", gnd_id="118000")
    b = P("hbls", name="Hans Wyler", gnd_id="118000")
    assert er.match(a, b) == "high"


def test_same_name_overlapping_dates_high():
    a = P("hls", name="Hans von Wiler", life_dates="1300–1370")
    b = P("kf", name="Hans Wiler", life_dates="1350")
    assert er.match(a, b) == "high"


def test_same_name_no_dates_medium():
    a = P("hls", name="Hans Wiler")
    b = P("kf", name="Hans Wiler")
    assert er.match(a, b) == "medium"


def test_same_name_conflicting_dates_no_merge():
    a = P("hls", name="Hans Wiler", life_dates="1300–1320")
    b = P("kf", name="Hans Wiler", life_dates="1450–1490")
    assert er.match(a, b) is None


def test_surname_plus_variant_plus_dates_medium():
    a = P("hls", name="Johann Wiler", surname="Wiler", forename="Johann",
          life_dates="1300–1360", variants=["Hans Wiler"])
    b = P("kf", name="Hans Wiler", surname="Wiler", forename="Hans",
          life_dates="1340")
    assert er.match(a, b) == "medium"


def test_surname_only_no_dates_no_merge():
    a = P("hls", name="Anna Wiler", surname="Wiler")
    b = P("kf", name="Peter Wiler", surname="Wiler")
    assert er.match(a, b) is None


# ── clustering / merge ───────────────────────────────────────────────────────

def test_resolve_merges_by_id_and_attributes_sources():
    a = P("hls", name="H. Wiler", gnd_id="118000", hls_id=12345)
    b = P("hbls", name="Hans von Wiler", gnd_id="118000", life_dates="1300–1370")
    c = P("kf", name="Peter Muster")  # unrelated
    out = er.resolve([a, b, c])
    assert len(out) == 2
    merged = next(e for e in out if len(e.members) == 2)
    assert merged.sources == ["hbls", "hls"]
    assert merged.confidence == "high"
    assert merged.gnd_id == "118000" and merged.hls_id == 12345
    assert merged.life_dates == "1300–1370"          # filled from whichever member had it
    assert merged.name == "Hans von Wiler"           # longest surface form
    assert not merged.needs_review


def test_resolve_transitive_cluster():
    """a~b (id), b~c (name+dates) → all one cluster."""
    a = P("hls", name="Hans Wiler", gnd_id="G1")
    b = P("hbls", name="Hans Wiler", gnd_id="G1", life_dates="1300–1360")
    c = P("kf", name="Hans Wiler", life_dates="1340")
    out = er.resolve([a, b, c])
    assert len(out) == 1 and len(out[0].members) == 3


def test_single_source_is_high_and_not_flagged():
    out = er.resolve([P("hls", name="Solo Person")])
    assert len(out) == 1 and out[0].confidence == "high" and not out[0].needs_review


def test_medium_merge_flags_review():
    a = P("hls", name="Hans Wiler")
    b = P("kf", name="Hans Wiler")
    out = er.resolve([a, b])
    assert len(out) == 1 and out[0].confidence == "medium" and out[0].needs_review


def test_empty_input():
    assert er.resolve([]) == []
