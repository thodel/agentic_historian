"""Tests for #207: Feed Agent B's structured source_json into SourceCriteria."""

import sys, os
sys.path.insert(0, os.path.expanduser("~/agentic_historian/agentic_historian"))
from agent_a.model_selector import SourceCriteria, normalise_script, normalise_lang, parse_century

def sj(**elements):
    """source_json with {wert, unsicher} wrappers."""
    return {k: {"wert": v, "unsicher": False} for k, v in elements.items()}

class TestScript:
    def test_kurrent(self):   assert SourceCriteria.from_source_json(sj(Schrift="Kurrent")).script == "kurrent"
    def test_caroline(self):  assert SourceCriteria.from_source_json(sj(Schrift="Caroline minuscule")).script == "caroline"
    def test_textura(self):   assert SourceCriteria.from_source_json(sj(Schrift="Gotische textura")).script == "textura"
    def test_wert_none(self): assert SourceCriteria.from_source_json({"Schrift": {"wert": None}}).script is None
    def test_empty_fallback(self):
        crit = SourceCriteria.from_source_json({}, fallback_description="Eine kurrent geschriebene Handschrift")
        assert crit.script == "kurrent"

class TestLang:
    def test_deutsch(self):   assert SourceCriteria.from_source_json(sj(Sprache="Deutsch")).lang == "de"
    def test_latein(self):    assert SourceCriteria.from_source_json(sj(Sprache="Latein")).lang == "la"
    def test_franzoesisch(self): assert SourceCriteria.from_source_json(sj(Sprache="Französisch")).lang == "fr"
    def test_fallback(self):
        crit = SourceCriteria.from_source_json({}, fallback_description="Sprache: Deutsch und Latein")
        assert crit.lang == "de"

class TestCentury:
    def test_14jh(self): assert SourceCriteria.from_source_json(sj(Datierung="14. Jahrhundert")).century == 14
    def test_16jh(self): assert SourceCriteria.from_source_json(sj(Datierung="16. Jh.")).century == 16
    def test_ca(self):   assert SourceCriteria.from_source_json(sj(Datierung="ca. 1350")).century == 14
    def test_range(self):assert SourceCriteria.from_source_json(sj(Datierung="1300-1350")).century == 14
    def test_fallback(self):
        crit = SourceCriteria.from_source_json({}, fallback_description="Datierung: 15. Jahrhundert")
        assert crit.century == 15

class TestDocType:
    def test_urbar(self):   assert SourceCriteria.from_source_json(sj(Inhalt="Zinsurbar aus dem 14. Jahrhundert")).document_type == "urbarium"
    def test_chronik(self): assert SourceCriteria.from_source_json(sj(Inhalt="Chronik der Stadt Basel")).document_type == "chronicle"
    def test_charter(self): assert SourceCriteria.from_source_json(sj(Inhalt="Diplom und Urkunde")).document_type == "charter"
    def test_none(self):    assert SourceCriteria.from_source_json({}).document_type is None

class TestFull:
    def test_all(self):
        crit = SourceCriteria.from_source_json(sj(Schrift="Fraktur", Sprache="Deutsch", Datierung="16. Jh.", Inhalt="Steuerregister"))
        assert crit.script == "fraktur"
        assert crit.lang == "de"
        assert crit.century == 16
        assert crit.document_type == "register"

class TestFromAgentBAndJson:
    def test_prefers_json(self):
        crit = SourceCriteria.from_agent_b_and_json("Gotische Textura, 14. Jh.", sj(Schrift="Kurrent"))
        assert crit.script == "kurrent"
    def test_falls_back(self):
        crit = SourceCriteria.from_agent_b_and_json("Kurrent, 15. Jahrhundert", None)
        assert crit.script == "kurrent" and crit.century == 15
    def test_partial(self):
        crit = SourceCriteria.from_agent_b_and_json("Kurrent, 13. Jahrhundert", sj(Schrift="Fraktur"))
        assert crit.script == "fraktur" and crit.century == 13
    def test_empty_dict(self):
        crit = SourceCriteria.from_agent_b_and_json("Caroline minuscule, 12. Jahrhundert", {})
        assert crit.script == "caroline" and crit.century == 12

class TestScriptAware:
    def test_kurrent_lang(self):
        crit = SourceCriteria.from_agent_b_and_json("Kurmainzische Kurrent", sj(Schrift="Kurrent", Sprache="Deutsch"))
        assert crit.script == "kurrent" and crit.lang == "de"
