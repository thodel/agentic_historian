"""
knowledge_hub/corpus_build.py — KG-4 (#330): build the corpus graph for QLever.

One command walks ``data/outputs/``, emits the corpus Turtle (the model from
KG-2 #328 and its provenance from KG-3 #329), and reports what it built:

    python -m knowledge_hub.corpus_build --out build/corpus.ttl

Deterministic and safe to re-run: the same inputs produce a byte-identical
``.ttl``, which is what makes rebuild-and-swap a sound refresh strategy and the
releases diffable.

Alongside the Turtle it writes ``<out>.manifest.json`` — triple count, build
time and per-class counts — so staleness of the served endpoint is *visible*
rather than inferred.

Publication scope
-----------------
The corpus concerns Fürsorge / care history, so what may be published is an
owner decision, not a code default (#330). The scope below is configurable and
records what was approved:

    Approved by the project owner (T. Hodel) on 2026-07-30:
      - care-flagged documents  → INCLUDED
      - person nodes            → ALL published, authority id not required
      - endpoint                → public, read-only SELECT

    Rationale: the processed corpus is 14th–16th c. administrative material, so
    the "recent enough to matter" concern behind the issue's privacy note does
    not bite here.

Every knob remains switchable via config/env, so a narrower scope can be applied
later without touching this module — and ``BuildReport.withheld`` always states
how many documents a narrower scope removed, so an omission is never silent.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rdflib import Graph, RDF

from knowledge_hub import rdf_export as rx

# Entity types treated as identifying a natural person.
_PERSON_TYPES = {"PERSON", "CARE_ACTOR"}


def _env_flag(name: str, default: bool) -> bool:
    """Read a boolean knob from config, falling back to `default`."""
    try:
        import config
    except Exception:
        return default
    raw = getattr(config, name, None)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class PublicationScope:
    """What may enter the published graph.

    Defaults encode the owner's approved scope (see the module docstring). They
    are deliberately explicit rather than implicit so that reading this class
    tells you exactly what is published.
    """
    include_care_flagged: bool = True
    include_person_nodes: bool = True
    require_authority_id: bool = False

    @classmethod
    def from_config(cls) -> "PublicationScope":
        return cls(
            include_care_flagged=_env_flag("KG_PUBLISH_CARE_FLAGGED", True),
            include_person_nodes=_env_flag("KG_PUBLISH_PERSON_NODES", True),
            require_authority_id=_env_flag("KG_PUBLISH_REQUIRE_AUTHORITY_ID", False),
        )

    @property
    def is_unrestricted(self) -> bool:
        return (self.include_care_flagged and self.include_person_nodes
                and not self.require_authority_id)


@dataclass
class BuildReport:
    """What the build produced — the answer to "is the endpoint current?"."""
    built_at: str = ""
    source_dir: str = ""
    destination: str = ""
    triples: int = 0
    documents: int = 0
    readings: int = 0
    mentions: int = 0
    entities: int = 0
    authority_links: dict = field(default_factory=dict)
    withheld: dict = field(default_factory=dict)
    scope: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        lines = [
            f"corpus graph → {self.destination}",
            f"  built at    {self.built_at}",
            f"  triples     {self.triples}",
            f"  documents   {self.documents}",
            f"  readings    {self.readings}",
            f"  mentions    {self.mentions}",
            f"  entities    {self.entities}",
        ]
        if self.authority_links:
            links = ", ".join(f"{k}={v}" for k, v in sorted(self.authority_links.items()))
            lines.append(f"  links       {links}")
        withheld = {k: v for k, v in self.withheld.items() if v}
        if withheld:
            lines.append("  withheld    " +
                         ", ".join(f"{k}={v}" for k, v in sorted(withheld.items())))
        return "\n".join(lines)


def _is_care_flagged(doc: dict) -> bool:
    desc = doc.get("description")
    if not isinstance(desc, dict):
        return False
    flag = desc.get("care_flag")
    return bool(isinstance(flag, dict) and flag.get("is_care_related"))


def _entities_of(doc: dict) -> list:
    raw = doc.get("entities")
    if isinstance(raw, dict):
        return list(raw.get("entities") or [])
    return list(raw or [])


def _apply_scope(doc: dict, scope: PublicationScope, withheld: dict) -> Optional[dict]:
    """Return the document as it may be published, or None to withhold it."""
    if not scope.include_care_flagged and _is_care_flagged(doc):
        withheld["care_flagged_documents"] = withheld.get("care_flagged_documents", 0) + 1
        return None

    entities = _entities_of(doc)
    kept = []
    for ent in entities:
        etype = str(ent.get("type") or "").upper()
        if etype in _PERSON_TYPES:
            if not scope.include_person_nodes:
                withheld["person_nodes"] = withheld.get("person_nodes", 0) + 1
                continue
            if scope.require_authority_id and not any(
                    (ent.get(f) or "") for f in ("gnd", "hls", "wikidata")):
                withheld["unresolved_persons"] = withheld.get("unresolved_persons", 0) + 1
                continue
        kept.append(ent)

    if len(kept) == len(entities):
        return doc
    trimmed = dict(doc)
    trimmed["entities"] = {"entities": kept}
    return trimmed


def _count(g: Graph) -> dict:
    """Per-class counts straight off the graph — no bookkeeping to drift."""
    authority: dict[str, int] = {}
    for obj in g.objects(None, rx.OWL["sameAs"]):
        text = str(obj)
        if text.startswith("https://d-nb.info/gnd/"):
            key = "gnd"
        elif "wikidata.org" in text:
            key = "wikidata"
        elif "hls-dhs-dss.ch" in text:
            key = "hls"
        else:
            key = "other"
        authority[key] = authority.get(key, 0) + 1

    return {
        "triples": len(g),
        "documents": len(set(g.subjects(RDF.type, rx.CRM["E31_Document"]))),
        "readings": len(set(g.subjects(RDF.type, rx.CRM["E33_Linguistic_Object"]))),
        "mentions": len(list(g.triples((None, rx.CRM["P67_refers_to"], None)))),
        "entities": len(set(g.subjects(rx.SDHSS["entityType"], None))),
        "authority_links": authority,
    }


def build_graph(outputs_dir: str | Path,
                scope: Optional[PublicationScope] = None) -> tuple[Graph, dict]:
    """Build the corpus graph under `scope`. Returns (graph, withheld counts)."""
    scope = scope or PublicationScope.from_config()
    withheld: dict[str, int] = {}

    g = Graph()
    rx._prefix_graph(g)
    for path in sorted(Path(outputs_dir).rglob("*_pipeline.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(doc, dict):
            continue
        publishable = _apply_scope(doc, scope, withheld)
        if publishable is not None:
            rx.document_to_rdf(g, publishable)
    return g, withheld


def build(outputs_dir: str | Path,
          destination: str | Path = "build/corpus.ttl",
          scope: Optional[PublicationScope] = None,
          *,
          write_manifest: bool = True) -> BuildReport:
    """Build the corpus Turtle plus its manifest. Idempotent."""
    scope = scope or PublicationScope.from_config()
    g, withheld = build_graph(outputs_dir, scope)

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(g.serialize(format="turtle"), encoding="utf-8")

    counts = _count(g)
    report = BuildReport(
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source_dir=str(outputs_dir),
        destination=str(destination),
        withheld=withheld,
        scope=asdict(scope),
        **counts,
    )
    if write_manifest:
        manifest_path(destination).write_text(
            json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    return report


def manifest_path(destination: str | Path) -> Path:
    return Path(str(destination) + ".manifest.json")


def read_manifest(destination: str | Path) -> Optional[dict]:
    """Last build's manifest, or None if never built."""
    path = manifest_path(destination)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus_build",
        description="Build the corpus RDF graph for QLever (KG-4, #330).")
    parser.add_argument("--outputs", default=None,
                        help="pipeline outputs dir (default: config.OUTPUTS_DIR)")
    parser.add_argument("--out", default="build/corpus.ttl",
                        help="destination .ttl (default: build/corpus.ttl)")
    parser.add_argument("--status", action="store_true",
                        help="print the last build's manifest and exit")
    args = parser.parse_args(argv)

    if args.status:
        manifest = read_manifest(args.out)
        if manifest is None:
            print(f"no build manifest at {manifest_path(args.out)}", file=sys.stderr)
            return 1
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    outputs = args.outputs
    if outputs is None:
        import config
        outputs = config.OUTPUTS_DIR

    scope = PublicationScope.from_config()
    report = build(outputs, args.out, scope)
    print(report.render())
    if not scope.is_unrestricted:
        print("  note        a narrower publication scope is in effect")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
