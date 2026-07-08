"""
entity_index.py — P1-A2 (#222)

Inverted index that aggregates all extracted entities across the document
corpus and produces:
  1. A machine-readable index (entity_index.json) for downstream consumers.
  2. Human-readable pages: docs/entities/<slug>/index.md and docs/entities/index.md.

The index is rebuilt fully on every run (idempotent).  It intentionally lives
in the top-level agentic_historian package so it is UI-agnostic (per #33).

Merging rules (#222 Task 1):
  - same GND id  → one EntityEntry, mentions merged, slug = gnd-<id>
  - else same normalised name + type  → one EntityEntry, mentions merged,
    slug = transliterated umlauts + filesystem-safe name
  - same name, different type  → two separate entries (type is part of the key)

Umlaut transliteration: ä→ae, ö→oe, ü→ue, ß→ss  (consistent with HBLS).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# ── slug helpers ─────────────────────────────────────────────────────────────

_UMLAUT = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
     "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}
)


def _slugify(name: str) -> str:
    """Filesystem/URL-safe slug from a raw name string."""
    s = name.translate(_UMLAUT)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _entity_slug(entry: EntityEntry) -> str:
    """Stable slug for one EntityEntry — gnd-<id> when available."""
    if entry.gnd:
        return f"gnd-{entry.gnd}"
    return _slugify(entry.name)


# ── data model ───────────────────────────────────────────────────────────────

@dataclass
class EntityMention:
    """One occurrence of an entity in one document."""
    doc_id:  str
    context: str     # surrounding text snippet
    page:    str = ""


@dataclass
class EntityEntry:
    """One aggregated entity after de-duplication."""
    name:     str
    type:     str
    gnd:      str = ""
    hls:      str = ""
    wikidata: str = ""
    mentions: list[EntityMention] = field(default_factory=list)

    def add_mention(self, doc_id: str, context: str, page: str = "") -> None:
        dup = any(m.doc_id == doc_id and m.context == context
                  for m in self.mentions)
        if not dup:
            self.mentions.append(EntityMention(doc_id=doc_id,
                                               context=context,
                                               page=page))


@dataclass
class EntityIndex:
    """Inverted index: entity_key (slug) → EntityEntry."""
    entries: dict[str, EntityEntry] = field(default_factory=dict)

    def by_gnd(self, gnd: str) -> EntityEntry | None:
        return next((e for e in self.entries.values() if e.gnd == gnd), None)

    def by_name_type(self, name: str, etype: str) -> EntityEntry | None:
        norm = _norm_name(name)
        return next(
            (e for e in self.entries.values()
             if _norm_name(e.name) == norm and e.type == etype),
            None,
        )

    def search(self, query: str) -> list[EntityEntry]:
        q = query.lower()
        return [e for e in self.entries.values() if q in e.name.lower()]


# ── normalisation (same as utils.entity_resolver._norm_name) ─────────────────

_PARTICLES = {"von", "van", "de", "di", "da", "der", "zu", "zer", "im", "am",
              "the", "of", "und"}


def _norm_name(name: str) -> str:
    """Lowercase + strip accents + remove particles."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().lower()
    words = [w for w in s.split() if w not in _PARTICLES]
    return " ".join(words)


# ── core index builder ───────────────────────────────────────────────────────

def build_index(entities_dir: str | Path) -> EntityIndex:
    """
    Walk ``entities_dir`` (recursively), load every *_entities.json,
    and return a fully-merged EntityIndex.

    File format: <doc_id>_entities.json → {"entities": [...]}
    Each entity dict: {text, type, normalised, context, page?, gnd_id?, hls_id?, wikidata_id?}

    Merging:
      1. Same GND → merge (slug = gnd-<id>)
      2. Else same normalised name + same type → merge (slug = normalised)
      3. Same name, different type → two separate entries
      4. No name → skip with a warning
    """
    entities_dir = Path(entities_dir)
    index: dict[str, EntityEntry] = {}
    norm_map: dict[tuple[str, str], str] = {}   # (norm_name, type) → slug

    for path in sorted(entities_dir.rglob("*_entities.json")):
        doc_id = path.stem.removesuffix("_entities")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            import logging
            logging.warning("[entity_index] skipping %s (%s)", path, exc)
            continue

        for ent in (data.get("entities") or []):
            gnd   = (ent.get("gnd_id") or ent.get("gnd") or "").strip()
            name  = (ent.get("normalised") or ent.get("text") or "").strip()
            etype = (ent.get("type") or "").strip()
            ctx   = (ent.get("context") or "").strip()
            page  = (ent.get("page") or ent.get("folio") or "").strip()

            if not name or not etype:
                continue

            if gnd:
                slug = f"gnd-{gnd}"
                if slug not in index:
                    index[slug] = EntityEntry(
                        name=name, type=etype, gnd=gnd,
                        hls=(ent.get("hls_id") or ent.get("hls") or "").strip(),
                        wikidata=(ent.get("wikidata_id")
                                  or ent.get("wikidata") or "").strip(),
                    )
                entry = index[slug]
                if len(name) > len(entry.name):
                    entry.name = name
            else:
                norm = _norm_name(name)
                if not norm:
                    continue
                key = (norm, etype)
                if key in norm_map:
                    slug = norm_map[key]
                else:
                    slug = _slugify(name)
                    # Resolve collision with existing name-only slug
                    while slug in index:
                        slug = f"{slug}-{len(index)}"
                    norm_map[key] = slug
                    index[slug] = EntityEntry(name=name, type=etype)
                entry = index[slug]

            entry.add_mention(doc_id=doc_id, context=ctx, page=page)

    return EntityIndex(entries=index)


# ── page generation ───────────────────────────────────────────────────────────

_AUTH_TEMPLATES = [
    ("gnd",      "GND",      "https://d-nb.info/gnd/{v}"),
    ("hls",      "HLS",      "https://hls-dhs-dss.ch/de/articles/{0}"),
    ("wikidata", "Wikidata", "https://www.wikidata.org/wiki/{v}"),
]


def _authority_links(entry: EntityEntry) -> str:
    parts = []
    for key, label, tmpl in _AUTH_TEMPLATES:
        v = getattr(entry, key, None) or ""
        if v:
            parts.append(f"[{label}]({tmpl.replace('{v}', v).replace('{0}', v)})")
    return " · ".join(parts)


def _mention_block(m: EntityMention) -> str:
    lines = [f"**[{m.doc_id}](../{m.doc_id}/index.md)**"]
    if m.page:
        lines[0] += f" — {m.page}"
    if m.context:
        lines.append(f"> {m.context}")
    return "\n".join(lines)


def write_entity_pages(
    index: EntityIndex,
    output_dir: str | Path,
) -> None:
    """
    Write per-entity pages and the A–Z register to ``output_dir``.

  - ``output_dir/docs/entities/<slug>/index.md`` — one page per entity
  - ``output_dir/docs/entities/index.md`` — A–Z register with mention counts

    Idempotent: all files are overwritten on every run.
    """
    output_dir = Path(output_dir)

    # ── per-entity pages ────────────────────────────────────────────────────
    for slug, entry in sorted(index.entries.items(),
                              key=lambda s_e: s_e[1].name.lower()):
        ep_dir = output_dir / "docs" / "entities" / slug
        ep_dir.mkdir(parents=True, exist_ok=True)
        path = ep_dir / "index.md"

        lines = [
            "---",
            "layout: default",
            f"title: {entry.name}",
            "---",
            "",
            f"# {entry.name}",
            "",
            f"**Type:** {entry.type}",
            "",
        ]
        auth = _authority_links(entry)
        if auth:
            lines += [auth, ""]

        if entry.mentions:
            lines += ["## Erwähnungen", ""]
            for m in sorted(entry.mentions, key=lambda x: x.doc_id):
                lines.append(_mention_block(m))
                lines.append("")
        else:
            lines.append("_Keine Erwähnungen gefunden._")
            lines.append("")

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── A–Z register ────────────────────────────────────────────────────────
    reg_dir = output_dir / "docs" / "entities"
    reg_dir.mkdir(parents=True, exist_ok=True)

    by_initial: dict[str, list[EntityEntry]] = {}
    for entry in index.entries.values():
        initial = entry.name[0].upper()
        by_initial.setdefault(initial, []).append(entry)

    reg_lines = [
        "---",
        "layout: default",
        "title: Entitäten",
        "---",
        "",
        "# Entitäten-Register",
        "",
        f"Gesamt: **{len(index.entries)}** Einträge",
        "",
    ]

    for letter in sorted(by_initial):
        entries = sorted(by_initial[letter], key=lambda e: e.name.lower())
        reg_lines.append(f"## {letter}")
        reg_lines.append("")
        for e in entries:
            slug = _entity_slug(e)
            count = len(e.mentions)
            auth = _authority_links(e)
            meta = f" ({auth})" if auth else ")"
            reg_lines.append(
                f"- [{e.name}]({slug}/index.md) — {e.type}, "
                f"{count} Erwähnung{'en' if count != 1 else ''}{meta}"
            )
        reg_lines.append("")

    (reg_dir / "index.md").write_text(
        "\n".join(reg_lines) + "\n", encoding="utf-8"
    )
