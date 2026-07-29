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
class AuthorityLink:
    """One authority identifier for an entity — identity and trust only.

    Hard rule (KG-1, #327): *link, don't copy.* This record carries what
    **identifies** the entity (``source``/``id``/``uri``) and how far we trust
    that identification (``confidence``/``conflicts``). It must never carry
    source payload — no names, life dates, occupations or biographies. A
    consumer that needs the person's dates follows ``uri``; that is what linked
    data is for. Source fields may be fetched transiently to *score* a match,
    but they do not land here.

    ``uri`` is None for sources without a public URI (hgb / hbls / kf / eos).
    Never invent one — KG-2 (#328) mints local URIs in our own namespace.
    """
    source:     str                              # gnd | hls | wikidata | hbls | kf | eos
    id:         str                              # source-local identifier
    uri:        str | None = None                # public URI, or None
    confidence: str = "medium"                   # high | medium | low
    conflicts:  list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"source": self.source, "id": self.id, "uri": self.uri,
                "confidence": self.confidence, "conflicts": list(self.conflicts)}


@dataclass
class Resolution:
    """Which sources were consulted for one entity, and which were dark.

    ``queried`` minus ``unavailable`` are the sources that answered. A source
    that answered but returned nothing is a genuine **no match**; a source in
    ``unavailable`` told us nothing at all. Keeping them distinct is the point:
    `[search] sources unavailable: ['hbls']` must never read as "no match".
    """
    queried:     list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)

    @property
    def answered(self) -> list[str]:
        return [s for s in self.queried if s not in self.unavailable]

    def as_dict(self) -> dict:
        return {"queried": list(self.queried),
                "unavailable": list(self.unavailable)}


@dataclass
class EntityEntry:
    """One aggregated entity after de-duplication."""
    name:     str
    type:     str
    gnd:      str = ""
    hls:      str = ""
    wikidata: str = ""
    mentions: list[EntityMention] = field(default_factory=list)
    # KG-1: authority identifiers + the provenance of the resolution that found
    # them. `links` is the persisted identity record; `resolution` records which
    # sources were asked, so a dark source stays visible in the data.
    links:      list[AuthorityLink] = field(default_factory=list)
    resolution: Resolution | None = None

    def add_mention(self, doc_id: str, context: str, page: str = "") -> None:
        dup = any(m.doc_id == doc_id and m.context == context
                  for m in self.mentions)
        if not dup:
            self.mentions.append(EntityMention(doc_id=doc_id,
                                               context=context,
                                               page=page))

    def as_dict(self) -> dict:
        """Serialise the identity record.

        Only our own data plus authority ids — deliberately no source payload
        (see :class:`AuthorityLink`). Mention contexts come from *our* corpus.
        """
        return {
            "name": self.name,
            "type": self.type,
            "gnd": self.gnd,
            "hls": self.hls,
            "wikidata": self.wikidata,
            "links": [l.as_dict() for l in self.links],
            "resolution": self.resolution.as_dict() if self.resolution else None,
            "mentions": [{"doc_id": m.doc_id, "context": m.context, "page": m.page}
                         for m in self.mentions],
        }


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


# ── authority resolution via the MCP federation (KG-1, #327) ─────────────────

# Public URI templates, keyed by link source. A source ABSENT from this map has
# no public URI (hbls / kf / eos): its links carry ``uri: None`` plus the
# source-local id. Do not add a pattern you have not verified — a fabricated
# URI is worse than none, and KG-2 (#328) mints local URIs for these instead.
_URI_TEMPLATES: dict[str, str] = {
    "gnd":      "https://d-nb.info/gnd/{id}",
    "hls":      "https://hls-dhs-dss.ch/de/articles/{id}",
    "wikidata": "https://www.wikidata.org/wiki/{id}",
}

_CONF_ORDER = ("low", "medium", "high")


def authority_uri(source: str, ident: str) -> str | None:
    """Public URI for an authority id, or None if the source has no public URI."""
    tmpl = _URI_TEMPLATES.get(source)
    if not tmpl or not ident:
        return None
    return tmpl.format(id=ident)


def _lower(conf: str) -> str:
    """Drop one confidence notch (high→medium→low), floored at ``low``."""
    try:
        return _CONF_ORDER[max(0, _CONF_ORDER.index(conf) - 1)]
    except ValueError:
        return "low"


def resolve_entity_links(
    name: str,
    *,
    search=None,
    limit: int = 20,
) -> tuple[list[AuthorityLink], Resolution]:
    """Resolve one entity name against the MCP federation → (links, resolution).

    Persists **identifiers only** (see :class:`AuthorityLink`). Source records
    are used transiently to cluster and score the match and are then discarded.

    ``search`` is the federated search callable ``(query, limit) -> FederatedResult``;
    it defaults to the live MCP client and is injected by tests to stay offline.

    Behaviour that matters:
      - a dead source degrades the result, never the run — it is recorded in
        ``Resolution.unavailable``, which is *not* the same as "no match";
      - two candidate ids for the same source are **both** emitted with the
        confidence lowered and ``multi_<source>`` named — never an arbitrary pick;
      - sources without a public URI get ``uri=None``, never a fabricated one.
    """
    # Lazy imports: keep `build_index`/page generation free of the httpx+pydantic
    # dependency chain, which offline consumers of this module do not need.
    from knowledge_hub import mcp_registry as reg

    queried = [s.name for s in reg.sources_for_kind("person") if not s.external]

    if search is None:
        from utils.mcp_client import search_persons_sync as search

    try:
        result = search(name, limit)
    except Exception:
        # Total federation failure: every source is dark, not "no match".
        return [], Resolution(queried=queried, unavailable=list(queried))

    resolution = Resolution(queried=queried,
                            unavailable=list(result.failed_sources))
    persons = list(result.persons)
    if not persons:
        return [], resolution

    from utils import entity_resolver as er
    clusters = er.resolve(persons)

    target = _norm_name(name)
    name_ok = any(_norm_name(c.name) == target for c in clusters)
    matching = [c for c in clusters if _norm_name(c.name) == target] or clusters

    base = matching[0].confidence if len(matching) == 1 else "low"
    shared: list[str] = []
    if len(matching) > 1:
        shared.append("ambiguous_identity")
    if not name_ok:
        base = _lower(base)
        shared.append("name_disagreement")

    # Collect candidate ids per source from the cluster MEMBERS. Reading the
    # merged cluster instead would hide conflicts: `entity_resolver._merge()`
    # keeps the first id it sees, which is precisely the arbitrary pick we must
    # not make.
    by_source: dict[str, list[str]] = {}

    def _add(src: str, ident) -> None:
        if not ident:
            return
        by_source.setdefault(src, [])
        if str(ident) not in by_source[src]:
            by_source[src].append(str(ident))

    for c in matching:
        for m in c.members:
            _add("gnd", m.gnd_id)
            _add("hls", m.hls_id)
            _add("wikidata", m.wikidata_id)
            _add(m.source, m.pid)      # source-local id (hbls/kf/eos → uri None)

    links: list[AuthorityLink] = []
    for src in sorted(by_source):
        idents = by_source[src]
        contested = len(idents) > 1
        conf = _lower(base) if contested else base
        conflicts = shared + ([f"multi_{src}"] if contested else [])
        for ident in idents:
            links.append(AuthorityLink(
                source=src, id=ident, uri=authority_uri(src, ident),
                confidence=conf, conflicts=list(conflicts),
            ))
    return links, resolution


def resolve_index(
    index: EntityIndex,
    *,
    search=None,
    types: tuple[str, ...] = ("PERSON",),
    limit: int = 20,
) -> EntityIndex:
    """Attach authority links to every entry of ``types``, in place.

    Scoped to PERSON by default: the MCP client exposes ``search_persons`` and
    ``search_fulltext`` but no place/org search, so PLACE and ORG cannot yet be
    resolved against the federation without inventing a lookup.
    """
    for entry in index.entries.values():
        if entry.type.upper() not in types:
            continue
        entry.links, entry.resolution = resolve_entity_links(
            entry.name, search=search, limit=limit)
        # Mirror onto the flat fields the slugs/pages use — but only when the id
        # is uncontested; a conflicted id must not be promoted to "the" id.
        for src in ("gnd", "hls", "wikidata"):
            cand = [l for l in entry.links if l.source == src]
            if len(cand) == 1 and not getattr(entry, src):
                setattr(entry, src, cand[0].id)
    return index


def write_index_json(index: EntityIndex, path: str | Path) -> Path:
    """Persist the identity records as ``entity_index.json`` (deterministic)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {slug: entry.as_dict()
               for slug, entry in sorted(index.entries.items())}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


# ── page generation ───────────────────────────────────────────────────────────

_AUTH_LABELS = [("gnd", "GND"), ("hls", "HLS"), ("wikidata", "Wikidata")]


def _authority_links(entry: EntityEntry) -> str:
    parts = []
    for key, label in _AUTH_LABELS:
        v = getattr(entry, key, None) or ""
        uri = authority_uri(key, v) if v else None
        if uri:
            parts.append(f"[{label}]({uri})")
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


# ── /entity command core (#224, P1-A4) ────────────────────────────────────────
# Pure lookup / suggest / format helpers over an EntityIndex. The bot stays a
# thin shell (#33): it calls build_index() then these. All offline-testable.

# German umlaut transliteration so a query for "Müller" also matches the "Mueller"
# spelling (build_index may keep either as the canonical name). Applied before
# _norm_name, which otherwise strips ü→u ("muller") and leaves "ue" alone.
_UMLAUT_TRANSLIT = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue", "ß": "ss",
})


def _match_keys(name: str) -> set[str]:
    """All lookup keys for a name — the accent-stripped form AND the umlaut-
    transliterated form — so a "Müller" entity matches queries "Müller",
    "Muller" (ü→u) and "Mueller" (ü→ue). Match = key-sets intersect."""
    base = _norm_name(name)
    translit = _norm_name((name or "").translate(_UMLAUT_TRANSLIT))
    return {k for k in (base, translit) if k}


def lookup(index: "EntityIndex", name: str) -> "EntityEntry | None":
    """Exact entity lookup, case-, diacritic- and umlaut-spelling-insensitive
    (particles ignored).

    ``build_index`` already merges by GND / normalised-name, so the returned
    entry carries every document that mentions the person under any spelling.
    On several entries whose keys match (different types), the one with the most
    mentions wins.
    """
    qkeys = _match_keys(name)
    if not qkeys:
        return None
    hits = [e for e in index.entries.values() if _match_keys(e.name) & qkeys]
    if not hits:
        return None
    return max(hits, key=lambda e: len(e.mentions))


def suggest(index: "EntityIndex", name: str, n: int = 3) -> list[str]:
    """The ``n`` closest entity names to a miss (difflib), for a 'did you mean'."""
    import difflib
    key_to_name: dict[str, str] = {}
    for e in index.entries.values():
        key_to_name.setdefault(_norm_name(e.name), e.name)
    close = difflib.get_close_matches(_norm_name(name), list(key_to_name), n=n, cutoff=0.5)
    return [key_to_name[c] for c in close]


def pages_site_base() -> str:
    """GitHub Pages base URL for the output repo (``owner.github.io/name``), or
    "" if not configured."""
    import config
    owner, _, name = (getattr(config, "GITHUB_OUTPUT_REPO", "") or "").partition("/")
    return f"https://{owner}.github.io/{name}" if owner and name else ""


def format_entity(entry: "EntityEntry", site_base: str | None = None,
                  max_chars: int = 2000) -> str:
    """Discord-ready summary of one entity: canonical name + type, authority
    links (GND/HLS/Wikidata), the documents that mention it (each with a
    ``/route <doc_id>`` hint), and — when ``site_base`` is given — the catalogue
    page link. Capped at ``max_chars`` (the doc list is trimmed with a
    "… und N weitere" note; never split a link)."""
    head = [f"**{entry.name}** · _{entry.type}_"]
    links = _authority_links(entry)                 # reuse the Pages link builder
    if links:
        head.append(links)
    if site_base:
        head.append(f"🔗 [Katalogseite]({site_base}/entities/{_entity_slug(entry)}/)")

    docs = sorted({m.doc_id for m in entry.mentions})
    head.append("")
    head.append(f"📄 Erwähnt in {len(docs)} Dokument(en):")
    header = "\n".join(head)

    doc_lines = [f"• `{d}` — `/route {d}`" for d in docs]
    kept: list[str] = []
    for i, dl in enumerate(doc_lines):
        remaining = len(docs) - (i + 1)
        note = f"\n… und {remaining} weitere" if remaining else ""
        candidate = header + "\n" + "\n".join(kept + [dl]) + note
        if len(candidate) > max_chars:
            break
        kept.append(dl)

    result = header + ("\n" + "\n".join(kept) if kept else "")
    hidden = len(docs) - len(kept)
    if hidden:
        result += f"\n… und {hidden} weitere"
    return result[:max_chars]
