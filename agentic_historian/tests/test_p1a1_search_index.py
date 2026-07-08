"""
test_p1a1_search_index.py — P1-A1 (#221)

Tests for build_index.py:
  1. full pipeline.json → correct search-index.json record
  2. snippet truncation and whitespace collapsing
  3. malformed JSON in one doc → other docs still present, bad doc has empty fields
  4. determinism: two runs produce byte-identical search-index.json
  5. docs/index.md regression: existing table output unchanged

All tests run offline — stdlib only, no network.
"""

import importlib.util
import json
import pathlib
import os
import pytest

# Scripts have no __init__.py — use importlib to load without modifying sys.path
# PKG (parents[1]) is agentic_historian/agentic_historian/; scripts are at
# agentic_historian/output_site/scripts (two levels up from PKG)
PKG = pathlib.Path(__file__).resolve().parents[1]
# Scripts live at agentic_historian/output_site/scripts  (parents[1] / output_site / scripts)
_SCRIPTS = PKG / "output_site" / "scripts"
_spec = importlib.util.spec_from_file_location(
    "build_index", _SCRIPTS / "build_index.py"
)
bi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bi)


# ── helpers ──────────────────────────────────────────────────────────────────

def _pipeline(doc_id: str, **overrides) -> dict:
    """Minimal pipeline.json with fields set via keyword args."""
    d = {
        "doc_id": doc_id,
        "description": {"source_json": {}},
        "transcription": overrides.get("transcription", ""),
        "entities": {"entities": []},
    }
    sj = d["description"]["source_json"]
    for k in ("Datierung", "Sprache", "Schrift"):
        if k in overrides:
            sj[k] = overrides[k]
    for v in overrides.get("entities", []):
        d["entities"]["entities"].append(v)
    return d


def _write_entity(root: pathlib.Path, doc_id: str, normal: str = "",
                  text: str = "") -> None:
    """Write a single entity into doc_id's pipeline.json."""
    p = root / doc_id / "pipeline.json"
    existing = {}
    if p.exists():
        existing = json.loads(p.read_text(encoding="utf-8"))
    existing.setdefault("entities", {"entities": []})
    ent = {}
    if normal:
        ent["normalised"] = normal
    if text:
        ent["text"] = text
    if ent:
        existing["entities"]["entities"].append(ent)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")


# ── unit tests ────────────────────────────────────────────────────────────────

class TestCollapse:
    def test_whitespace_collapsed(self):
        assert bi._collapse("hello  \n  world") == "hello world"

    def test_truncates_at_limit(self):
        result = bi._collapse("a" * 600)
        assert len(result) == 301   # 300 + "…"

    def test_under_limit_unchanged(self):
        assert bi._collapse("short") == "short"

    def test_strips_edges(self):
        assert bi._collapse("  hello   ") == "hello"


class TestVal:
    def test_wert_unwrapped(self):
        assert bi._val({"wert": "1500"}) == "1500"

    def test_value_unwrapped(self):
        assert bi._val({"value": "1500"}) == "1500"

    def test_plain_str(self):
        assert bi._val("hello") == "hello"

    def test_none(self):
        assert bi._val(None) == ""


class TestEntitiesFromPipeline:
    def test_normalised_precedence(self):
        d = {"entities": {"entities": [
            {"normalised": "Hans von Wiler", "text": "Hans"},
        ]}}
        assert bi._entities_from_pipeline(d) == ["Hans von Wiler"]

    def test_deduplicated(self):
        d = {"entities": {"entities": [
            {"normalised": "Hans"}, {"text": "Hans"}, {"normalised": "Hans"},
        ]}}
        assert bi._entities_from_pipeline(d) == ["Hans"]

    def test_multiple_ordered(self):
        d = {"entities": {"entities": [
            {"normalised": "Anna"}, {"normalised": "Hans"},
        ]}}
        assert bi._entities_from_pipeline(d) == ["Anna", "Hans"]


# ── integration tests ─────────────────────────────────────────────────────────

class TestBuildIntegration:
    """Run build() / build_search_index() against a temp docs/ tree."""

    @pytest.fixture(autouse=True)
    def docs_root(self, tmp_path):
        """Create docs/ inside tmp_path; pass docs_dir= to build() explicitly."""
        docs = tmp_path / "docs"
        docs.mkdir()
        self._docs_root = docs
        yield docs

    def _write(self, doc_id: str, pipeline: dict) -> None:
        p = self._docs_root / doc_id / "pipeline.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(pipeline, ensure_ascii=False), encoding="utf-8")

    def test_both_docs_present_sorted(self):
        self._write("doc_b", _pipeline("doc_b", Datierung="1520"))
        self._write("doc_a", _pipeline("doc_a", Datierung="1500"))

        records = bi.build_search_index(self._docs_root)

        assert len(records) == 2
        assert [r["doc_id"] for r in records] == ["doc_a", "doc_b"]

    def test_fields_correct(self):
        self._write("doc_x", _pipeline("doc_x",
                                        Datierung="1485",
                                        Sprache="de",
                                        Schrift="kurrent",
                                        transcription="Dies ist ein Test."))

        records = bi.build_search_index(self._docs_root)
        r = records[0]
        assert r["doc_id"] == "doc_x"
        assert r["date"] == "1485"
        assert r["lang"] == "de"
        assert r["script"] == "kurrent"
        assert r["url"] == "doc_x/"
        assert r["snippet"] == "Dies ist ein Test."

    def test_entities_deduped_in_record(self):
        self._write("doc_e", {
            "doc_id": "doc_e",
            "description": {},
            "transcription": "",
            "entities": {"entities": [
                {"normalised": "Hans"}, {"text": "Hans"},
            ]},
        })
        records = bi.build_search_index(self._docs_root)
        assert records[0]["entities"] == ["Hans"]

    def test_snippet_truncated(self):
        self._write("doc_s", _pipeline("doc_s", transcription="a" * 600))
        records = bi.build_search_index(self._docs_root)
        assert len(records[0]["snippet"]) <= 301

    def test_malformed_json_doc_has_empty_fields(self):
        self._write("doc_ok", _pipeline("doc_ok", Datierung="1550"))
        bad = self._docs_root / "doc_bad" / "pipeline.json"
        bad.parent.mkdir()
        bad.write_text("{ invalid", encoding="utf-8")

        records = bi.build_search_index(self._docs_root)
        ids = {r["doc_id"] for r in records}
        assert "doc_ok" in ids
        assert "doc_bad" in ids
        bad_rec = next(r for r in records if r["doc_id"] == "doc_bad")
        assert bad_rec["date"] == ""
        assert bad_rec["entities"] == []
        assert bad_rec["snippet"] == ""

    def test_determinism_byte_identical(self):
        for i in range(3):
            self._write(f"doc_{i}", _pipeline(f"doc_{i}", Datierung=str(1500 + i)))

        bi.build(docs_dir=self._docs_root)
        first = (self._docs_root / "search-index.json").read_bytes()

        bi.build(docs_dir=self._docs_root)
        second = (self._docs_root / "search-index.json").read_bytes()

        assert first == second, "search-index.json must be byte-identical on every run"

    def test_index_md_regression_unchanged(self):
        self._write("doc_r", _pipeline("doc_r", Datierung="1550", Sprache="de"))
        bi.build(docs_dir=self._docs_root)

        idx = self._docs_root / "index.md"
        assert idx.exists()
        text = idx.read_text()
        assert "doc_r" in text
        assert "1550" in text
        assert "| Dokument |" in text
        assert "Verarbeitete Dokumente" in text
        assert "Entitäten" in text
