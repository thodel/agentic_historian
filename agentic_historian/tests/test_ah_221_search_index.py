"""Tests for #221: emit search-index.json in the output-repo build Action.

Offline — filesystem only, no network. Run from the repo root:
    pytest agentic_historian/tests/test_ah_221_search_index.py

Tests the build() function in output_site/scripts/build_index.py against
synthetic docs/ trees.
"""

import json
import sys
from pathlib import Path
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PKG / "output_site" / "scripts"


def _build_index_module():
    """Import build_index with SCRIPTS_DIR on sys.path (one-shot, cached)."""
    if "build_index" in sys.modules:
        return sys.modules["build_index"]
    saved = sys.path.copy()
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import build_index
    sys.path = saved
    sys.modules["build_index"] = build_index
    return build_index


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_pipeline(
    *,
    datierung: str = "",
    sprache: str = "",
    schrift: str = "",
    entities: list[dict] = None,
    transcription: str = "",
) -> dict:
    """Minimal pipeline.json dict with Agent B's {"wert": …} shape."""
    sj = {}
    if datierung:
        sj["Datierung"] = {"wert": datierung}
    if sprache:
        sj["Sprache"] = {"wert": sprache}
    if schrift:
        sj["Schrift"] = {"wert": schrift}
    return {
        "description": {"source_json": sj},
        "entities": {"entities": entities or []},
        "transcription": transcription,
    }


def write_docs(root: Path, docs: list[dict]) -> None:
    """Write a docs/ tree from a list of doc specs.

    Each spec: {"doc_id": str, "pipeline": dict | None}
    pipeline=None means create the dir with no pipeline.json (tests missing file).
    """
    for spec in docs:
        doc_dir = root / spec["doc_id"]
        doc_dir.mkdir(parents=True, exist_ok=True)
        pj = spec.get("pipeline")
        if pj is not None:
            (doc_dir / "pipeline.json").write_text(
                json.dumps(pj, ensure_ascii=False), encoding="utf-8")


# ── Test cases ────────────────────────────────────────────────────────────────

class TestEntitiesDedup:
    """Entities deduped by normalised/text, in document order."""

    def test_full_pipeline_entities_present(self, tmp_path):
        bi = _build_index_module()
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        write_docs(docs_root, [{
            "doc_id": "doc-a",
            "pipeline": make_pipeline(
                entities=[
                    {"normalised": "Hans von Wiler", "text": "Hans von Wiler"},
                    {"normalised": "Thun", "text": "Thun"},
                    {"normalised": "Hans von Wiler", "text": "Hans von Wiler"},  # dup
                ],
            )
        }])

        with mock.patch.object(bi, "DOCS", docs_root):
            bi.build()

        idx = json.loads((docs_root / "search-index.json").read_text(encoding="utf-8"))
        assert len(idx) == 1
        assert idx[0]["entities"] == ["Hans von Wiler", "Thun"]

    def test_entities_from_text_when_normalised_missing(self, tmp_path):
        bi = _build_index_module()
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        write_docs(docs_root, [{
            "doc_id": "doc-a",
            "pipeline": make_pipeline(
                entities=[{"text": "Johannes Brun", "normalised": ""}],
            )
        }])

        with mock.patch.object(bi, "DOCS", docs_root):
            bi.build()

        idx = json.loads((docs_root / "search-index.json").read_text(encoding="utf-8"))
        assert idx[0]["entities"] == ["Johannes Brun"]

    def test_two_docs_both_present(self, tmp_path):
        bi = _build_index_module()
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        write_docs(docs_root, [
            {"doc_id": "aaa-test", "pipeline": make_pipeline()},
            {"doc_id": "bbb-test", "pipeline": make_pipeline()},
        ])

        with mock.patch.object(bi, "DOCS", docs_root):
            bi.build()

        idx = json.loads((docs_root / "search-index.json").read_text(encoding="utf-8"))
        assert [r["doc_id"] for r in idx] == ["aaa-test", "bbb-test"]


class TestSnippetTruncation:
    """Snippet is whitespace-collapsed and truncated at SNIPPET_MAX_CHARS."""

    def test_short_text_unchanged(self, tmp_path):
        bi = _build_index_module()
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        text = "Kurzer Text."
        write_docs(docs_root, [{"doc_id": "doc-a", "pipeline": make_pipeline(transcription=text)}])

        with mock.patch.object(bi, "DOCS", docs_root):
            bi.build()

        idx = json.loads((docs_root / "search-index.json").read_text(encoding="utf-8"))
        assert idx[0]["snippet"] == text

    def test_whitespace_collapsed(self, tmp_path):
        bi = _build_index_module()
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        write_docs(docs_root, [{
            "doc_id": "doc-a",
            "pipeline": make_pipeline(transcription="Hans    von\t\tWiler\n\nund   Thun."),
        }])

        with mock.patch.object(bi, "DOCS", docs_root):
            bi.build()

        idx = json.loads((docs_root / "search-index.json").read_text(encoding="utf-8"))
        assert idx[0]["snippet"] == "Hans von Wiler und Thun."

    def test_truncation_at_limit(self, tmp_path):
        bi = _build_index_module()
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        long_text = " ".join(["wort"] * 200)
        write_docs(docs_root, [{"doc_id": "doc-a", "pipeline": make_pipeline(transcription=long_text)}])

        with mock.patch.object(bi, "DOCS", docs_root):
            bi.build()

        idx = json.loads((docs_root / "search-index.json").read_text(encoding="utf-8"))
        assert len(idx[0]["snippet"]) <= bi.SNIPPET_MAX_CHARS
        assert idx[0]["snippet"].endswith("…")

    def test_truncation_splits_at_word_boundary(self, tmp_path):
        bi = _build_index_module()
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        # Build a string > 300 chars with the last word being "Grenouille"
        text = ("Hans von Wiler und der Rat von Thun sprachen über das Urteil "
                "betreffend die Stadt und ihre Freiheiten und Rechte und die "
                "umliegenden Dörfer und Güter sowie die Grenzen und Marchen "
                "und alle pertinenten Belange und die Zuständigkeiten und "
                "die Competenz und das Grenouille-Recht")  # long; will truncate before "Grenouille"

        with mock.patch.object(bi, "DOCS", docs_root):
            bi.build()

        idx = json.loads((docs_root / "search-index.json").read_text(encoding="utf-8"))
        snippet = idx[0]["snippet"]
        assert not snippet.endswith("Grenouille"), f"Should have truncated: {snippet}"
        assert snippet.endswith("…")


class TestMalformedPipeline:
    """Malformed/absent pipeline.json → record with empty fields, run succeeds."""

    def test_missing_pipeline_json_skipped(self, tmp_path):
        bi = _build_index_module()
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        # dir exists but no pipeline.json
        orphan_dir = docs_root / "orphan-doc"
        orphan_dir.mkdir()
        (orphan_dir / "transcription.txt").write_text("text", encoding="utf-8")

        with mock.patch.object(bi, "DOCS", docs_root):
            count = bi.build()

        idx = json.loads((docs_root / "search-index.json").read_text(encoding="utf-8"))
        assert [r["doc_id"] for r in idx] == []

    def test_malformed_json_empty_fields(self, tmp_path):
        bi = _build_index_module()
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        # Good doc
        write_docs(docs_root, [{"doc_id": "good-doc", "pipeline": make_pipeline(transcription="Good.")}])
        # Bad doc — not valid JSON
        bad_dir = docs_root / "bad-doc"
        bad_dir.mkdir()
        (bad_dir / "pipeline.json").write_text("{invalid json}", encoding="utf-8")

        with mock.patch.object(bi, "DOCS", docs_root):
            count = bi.build()  # must not raise

        idx = json.loads((docs_root / "search-index.json").read_text(encoding="utf-8"))
        doc_ids = [r["doc_id"] for r in idx]
        assert "good-doc" in doc_ids
        assert "bad-doc" in doc_ids
        bad_record = next(r for r in idx if r["doc_id"] == "bad-doc")
        assert bad_record["date"] == ""
        assert bad_record["entities"] == []
        assert bad_record["snippet"] == ""


class TestDeterminism:
    """Two runs produce byte-identical search-index.json."""

    def test_deterministic_output(self, tmp_path):
        bi = _build_index_module()
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        write_docs(docs_root, [
            {
                "doc_id": "bbb-doc",
                "pipeline": make_pipeline(
                    datierung="1300",
                    entities=[{"normalised": "Person A"}],
                    transcription="Some transcription text for bbb.",
                )
            },
            {
                "doc_id": "aaa-doc",
                "pipeline": make_pipeline(
                    sprache="Frühneuhochdeutsch",
                    transcription="Another transcription for aaa.",
                )
            },
        ])

        with mock.patch.object(bi, "DOCS", docs_root):
            bi.build()
            first = (docs_root / "search-index.json").read_bytes()
            bi.build()
            second = (docs_root / "search-index.json").read_bytes()

        assert first == second, "search-index.json must be deterministic"


class TestIndexMdUnchanged:
    """Regression: existing docs/index.md output is unchanged."""

    def test_index_md_preserved(self, tmp_path):
        bi = _build_index_module()
        docs_root = tmp_path / "docs"
        docs_root.mkdir()
        write_docs(docs_root, [{
            "doc_id": "doc-a",
            "pipeline": make_pipeline(
                datierung="1348",
                sprache="Frühneuhochdeutsch",
                schrift="Kursive",
                entities=[{"normalised": "Hans"}],
            )
        }])

        with mock.patch.object(bi, "DOCS", docs_root):
            bi.build()

        idx_md = (docs_root / "index.md").read_text(encoding="utf-8")
        assert "doc-a" in idx_md
        assert "1348" in idx_md
        assert "Frühneuhochdeutsch" in idx_md
        assert "Kursive" in idx_md
        assert "| Dokument |" in idx_md  # table header preserved