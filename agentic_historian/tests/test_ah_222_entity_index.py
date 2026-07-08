"""
test_ah_222_entity_index.py — P1-A2 (#222)

Tests for entity_index.py:
  - Index building (GND merge, name+type merge, type discrimination, umlaut slugs)
  - Page generation (entity page contains links + doc refs; register sorted; idempotent)
  - Regression: by_gnd, by_name_type, search helpers

All tests run offline — no network, no VPN, no GitHub API.
"""

import json
import pathlib
import sys

PKG = str(pathlib.Path(__file__).resolve().parents[1])
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from entity_index import (
    build_index,
    write_entity_pages,
    _slugify,
    _entity_slug,
    EntityIndex,
    EntityEntry,
    EntityMention,
)


# ── fixture helpers ──────────────────────────────────────────────────────────

def make_entity_file(root: pathlib.Path, doc_id: str, entities: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{doc_id}_entities.json"
    path.write_text(json.dumps({"entities": entities}, ensure_ascii=False), encoding="utf-8")


# ── slug tests ───────────────────────────────────────────────────────────────

class TestSlugify:
    def test_umlaut_ae(self):
        assert _slugify("Müller") == "mueller"

    def test_umlaut_oe_ue(self):
        assert _slugify("Böhm") == "boehm"
        assert _slugify("Übel") == "uebel"

    def test_eszett(self):
        assert _slugify("Groß") == "gross"

    def test_mixed(self):
        assert _slugify("Käßböhl") == "kaessboehl"

    def test_non_latin(self):
        assert _slugify("José") == "jose"

    def test_special_chars(self):
        assert _slugify("Hans (der Ältere)") == "hans-der-aeltere"

    def test_collision_resolved_in_build_index(self, tmp_path):
        """Müller vs Mueller get different slugs when built through build_index."""
        import json
        for doc_id, name in [("doc1", "Müller"), ("doc2", "Mueller")]:
            p = tmp_path / f"{doc_id}_entities.json"
            p.write_text(json.dumps({"entities": [
                {"text": name, "type": "PERSON", "normalised": name, "context": ""}
            ]}), encoding="utf-8")

        idx = build_index(tmp_path)
        slugs = list(idx.entries.keys())
        # Both produce "mueller" base slug; build_index disambiguates
        assert len(slugs) == 2
        assert slugs[0] != slugs[1]


class TestEntitySlug:
    def test_gnd_preference(self):
        e = EntityEntry(name="Gutenberg", type="PERSON", gnd="118695253")
        assert _entity_slug(e) == "gnd-118695253"

    def test_no_gnd_uses_name(self):
        e = EntityEntry(name="Gutenberg", type="PERSON")
        assert _entity_slug(e) == "gutenberg"


# ── index building ───────────────────────────────────────────────────────────

class TestBuildIndex:
    def test_same_gnd_merges(self, tmp_path):
        """Same GND across two docs → one entity with two mentions."""
        make_entity_file(tmp_path, "doc1", [
            {"text": "Johannes Gutenberg", "type": "PERSON",
             "normalised": "Johannes Gutenberg", "context": "der Trucker",
             "gnd_id": "118695253"},
        ])
        make_entity_file(tmp_path, "doc2", [
            {"text": "Gutenberg", "type": "PERSON",
             "normalised": "Gutenberg", "context": "urkundlich 1450",
             "gnd_id": "118695253"},
        ])

        idx = build_index(tmp_path)
        entries = list(idx.entries.values())
        assert len(entries) == 1
        e = entries[0]
        assert e.gnd == "118695253"
        assert len(e.mentions) == 2
        assert {m.doc_id for m in e.mentions} == {"doc1", "doc2"}

    def test_same_name_different_type_stays_separate(self, tmp_path):
        """Same normalised name but different type → two entries."""
        make_entity_file(tmp_path, "doc1", [
            {"text": "Thun", "type": "PLACE", "normalised": "Thun", "context": ""},
        ])
        make_entity_file(tmp_path, "doc2", [
            {"text": "Thun", "type": "ORG", "normalised": "Thun", "context": ""},
        ])

        idx = build_index(tmp_path)
        entries = list(idx.entries.values())
        assert len(entries) == 2
        assert {e.type for e in entries} == {"PLACE", "ORG"}

    def test_umlaut_sluggable(self, tmp_path):
        make_entity_file(tmp_path, "doc1", [
            {"text": "Hans Müller", "type": "PERSON",
             "normalised": "Hans Müller", "context": ""},
        ])

        idx = build_index(tmp_path)
        slugs = list(idx.entries.keys())
        assert any("mueller" in s for s in slugs)

    def test_missing_authority_ids_tolerated(self, tmp_path):
        make_entity_file(tmp_path, "doc1", [
            {"text": "Unbekannte Person", "type": "PERSON",
             "normalised": "Unbekannte Person", "context": ""},
        ])
        idx = build_index(tmp_path)
        assert len(idx.entries) == 1

    def test_norm_name_particle_dropping(self, tmp_path):
        """'von' is dropped in normalisation → names merge."""
        make_entity_file(tmp_path, "doc1", [
            {"text": "Heinrich von Wiler", "type": "PERSON",
             "normalised": "Heinrich von Wiler", "context": ""},
        ])
        make_entity_file(tmp_path, "doc2", [
            {"text": "Heinrich Wiler", "type": "PERSON",
             "normalised": "Heinrich Wiler", "context": ""},
        ])

        idx = build_index(tmp_path)
        # Both normalise to "heinerich wiler" → merged
        assert len(idx.entries) == 1

    def test_empty_entities_file_skipped(self, tmp_path):
        (tmp_path / "empty_doc_entities.json").write_text("{}", encoding="utf-8")
        idx = build_index(tmp_path)
        assert idx.entries == {}

    def test_duplicate_mention_deduplicated(self, tmp_path):
        """Same doc + same context → mention appears once."""
        make_entity_file(tmp_path, "doc1", [
            {"text": "Hans", "type": "PERSON",
             "normalised": "Hans", "context": "Hans der Bäcker"},
        ])
        make_entity_file(tmp_path, "doc1", [
            {"text": "Hans", "type": "PERSON",
             "normalised": "Hans", "context": "Hans der Bäcker"},
        ])

        idx = build_index(tmp_path)
        assert len(idx.entries) == 1
        assert len(list(idx.entries.values())[0].mentions) == 1


# ── page generation ──────────────────────────────────────────────────────────

class TestWriteEntityPages:
    def test_entity_page_contains_gnd_hls_links(self, tmp_path):
        idx = EntityIndex()
        idx.entries["gnd-118695253"] = EntityEntry(
            name="Johannes Gutenberg", type="PERSON",
            gnd="118695253", hls="010012", mentions=[],
        )
        write_entity_pages(idx, tmp_path)

        page = (tmp_path / "docs" / "entities" / "gnd-118695253" / "index.md").read_text()
        assert "Johannes Gutenberg" in page
        assert "https://d-nb.info/gnd/118695253" in page
        assert "https://hls-dhs-dss.ch/de/articles/010012" in page

    def test_entity_page_contains_both_doc_references(self, tmp_path):
        idx = EntityIndex()
        e = EntityEntry(name="Gutenberg", type="PERSON")
        e.mentions = [
            EntityMention(doc_id="doc_1450", context="erfunden", page="f.1r"),
            EntityMention(doc_id="doc_1460", context="bekannt", page="p.3"),
        ]
        idx.entries["gutenberg"] = e
        write_entity_pages(idx, tmp_path)

        page = (tmp_path / "docs" / "entities" / "gutenberg" / "index.md").read_text()
        assert "doc_1450" in page
        assert "doc_1460" in page
        assert "f.1r" in page

    def test_register_sorted_alphabetically(self, tmp_path):
        idx = EntityIndex()
        for name in ["Zwingli", "Albrecht", "Müller"]:
            slug = _slugify(name)
            idx.entries[slug] = EntityEntry(name=name, type="PERSON")
        write_entity_pages(idx, tmp_path)

        reg = (tmp_path / "docs" / "entities" / "index.md").read_text()
        names_in_order = [
            l.lstrip("- []").split("]")[0].lstrip("- [")
            for l in reg.split("\n")
            if l.startswith("- [")
        ]
        assert names_in_order == sorted(names_in_order, key=str.lower)

    def test_idempotent_second_run_byte_identical(self, tmp_path):
        idx1 = EntityIndex()
        idx1.entries["müller"] = EntityEntry(name="Hans Müller", type="PERSON")
        idx1.entries["albrecht"] = EntityEntry(name="Albrecht", type="PERSON")
        write_entity_pages(idx1, tmp_path)

        paths_files = {p: p.read_bytes()
                       for p in (tmp_path / "docs" / "entities").rglob("*.md")}

        idx2 = EntityIndex()
        idx2.entries["müller"] = EntityEntry(name="Hans Müller", type="PERSON")
        idx2.entries["albrecht"] = EntityEntry(name="Albrecht", type="PERSON")
        write_entity_pages(idx2, tmp_path)

        for p, orig in paths_files.items():
            assert p.read_bytes() == orig, f"{p} changed on second run"


# ── regression: existing helpers ─────────────────────────────────────────────

class TestRegressionHelpers:
    def test_by_gnd_returns_entry(self):
        idx = EntityIndex()
        e = EntityEntry(name="Gutenberg", type="PERSON", gnd="118695253")
        idx.entries["gnd-118695253"] = e
        assert idx.by_gnd("118695253") is e
        assert idx.by_gnd("wrong") is None

    def test_by_name_type(self):
        idx = EntityIndex()
        idx.entries["thun-place"] = EntityEntry(name="Thun", type="PLACE")
        idx.entries["thun-org"] = EntityEntry(name="Thun", type="ORG")
        assert idx.by_name_type("Thun", "PLACE") is idx.entries["thun-place"]
        assert idx.by_name_type("Thun", "ORG") is idx.entries["thun-org"]
        assert idx.by_name_type("Thun", "PERSON") is None

    def test_search_substring_match(self):
        idx = EntityIndex()
        idx.entries["gutenberg"] = EntityEntry(name="Johannes Gutenberg", type="PERSON")
        idx.entries["mueller"] = EntityEntry(name="Hans Müller", type="PERSON")
        results = idx.search("GUTEN")
        assert len(results) == 1
        assert results[0].name == "Johannes Gutenberg"
