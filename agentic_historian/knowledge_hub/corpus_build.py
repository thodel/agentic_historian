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

The vocabulary check is a gate (KG-12, #659)
---------------------------------------------
Before anything is written, the graph goes through
``knowledge_hub.ontology.validate``: every class and property must be in the
register (CIDOC/SDHSS as OntoMe and the CRM RDFS know them, W3C, or ours under
``shb:``), domain and range must fit, every core node must carry its
provenance, and the SHACL shapes must hold. A graph that fails is **not
written** and the command exits 2 — a published endpoint must not carry a term
that nobody defined, which is what the exporter did for 40 terms until KG-12.
KG-6 deliberately did not put this gate here because the exporter would have
failed it; now it does not, and the gate keeps it that way. ``--no-shacl``
skips only the SHACL part (the shapes are slow on a large graph); the register
check always runs.

Publication scope
-----------------
The corpus concerns Fürsorge / care history, so what may be published is an
owner decision, not a code default (#330). The scope below is configurable and
records what was approved:

    Approved by the project owner (T. Hodel) on 2026-07-30:
      - care-flagged documents  → INCLUDED
      - person nodes            → ALL published, authority id not required
      - endpoint                → public, read-only SELECT

    The graph says which of the three the classification was (#631): care, not
    care, or never classified. ``BuildReport.care`` counts them, so "3 of 20 are
    care-related" and "3 of 20, with 6 classifications that failed" can be told
    apart from the manifest of a published build.

    Rationale: the processed corpus is 14th–16th c. administrative material, so
    the "recent enough to matter" concern behind the issue's privacy note does
    not bite here.

Every knob remains switchable via config/env, so a narrower scope can be applied
later without touching this module — and ``BuildReport.withheld`` always states
how many documents a narrower scope removed, so an omission is never silent.

``include_care_flagged=False`` withholds two sets, counted separately (#631):
the documents classified as care-related, and the ones **never classified**. The
second is the point of that fix — an unclassified document may be care-related,
so publishing it under a scope chosen to exclude care documents is the opposite
of what the scope is for. It used to be published, because Agent B stored a
failed classification as ``is_care_related: False``.
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

import care_flag
from knowledge_hub import rdf_export as rx
from knowledge_hub.ontology import validate as ontology_validate

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


class ValidationError(RuntimeError):
    """The corpus graph uses a term the register does not know, or breaks a
    domain, range, provenance or shape rule — the gate refused the build."""

    def __init__(self, report: ontology_validate.Report):
        self.report = report
        super().__init__("corpus graph is not valid:\n" + report.render())


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
    #: ``{"yes": n, "no": n, "unknown": n}`` — how many documents were actually
    #: classified (#631). Reported even in the wide scope, where nothing is
    #: withheld: "3 of 20 are care-related" and "3 of 20, with 6 classifications
    #: that failed" are different statements about the same corpus.
    care: dict = field(default_factory=dict)
    #: The gate's numbers (KG-12): ``ok``, the property uses it checked, whether
    #: SHACL ran, and a count per finding kind. A build that reached the
    #: manifest passed, so ``ok`` is always true here — the point of recording
    #: it is that a reader of the manifest can see the check ran, and with what.
    validation: dict = field(default_factory=dict)

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
        if self.care:
            lines.append("  care        " + ", ".join(
                f"{k}={v}" for k, v in sorted(self.care.items())))
        if self.validation:
            lines.append("  validation  " + ", ".join(
                f"{k}={v}" for k, v in sorted(self.validation.items())))
        withheld = {k: v for k, v in self.withheld.items() if v}
        if withheld:
            lines.append("  withheld    " +
                         ", ".join(f"{k}={v}" for k, v in sorted(withheld.items())))
        return "\n".join(lines)


def _is_care_flagged(doc: dict) -> bool:
    """Whether this document **is** care-related, as far as anybody checked."""
    return care_flag.is_care_flagged(doc)


def _is_unclassified(doc: dict) -> bool:
    """Whether nobody ever classified it (#631).

    A separate question from the one above, and the narrower scope has to
    withhold both: a document that was never classified *may* be care-related,
    and not publishing those is the whole purpose of the narrow setting. Until
    #631 an unclassified document was stored as ``is_care_related: False`` and
    this check passed it through — the opposite of what the setting is for.
    """
    return care_flag.needs_classification(doc)


def _entities_of(doc: dict) -> list:
    raw = doc.get("entities")
    if isinstance(raw, dict):
        return list(raw.get("entities") or [])
    return list(raw or [])


def _apply_scope(doc: dict, scope: PublicationScope, withheld: dict) -> Optional[dict]:
    """Return the document as it may be published, or None to withhold it."""
    if not scope.include_care_flagged:
        if _is_care_flagged(doc):
            withheld["care_flagged_documents"] = (
                withheld.get("care_flagged_documents", 0) + 1)
            return None
        if _is_unclassified(doc):
            # Counted on its own line, because "we dropped 4 care documents" and
            # "we also dropped 11 we never classified" are different facts about
            # the same build — and `BuildReport.withheld` exists so that an
            # omission is never silent.
            withheld["unclassified_documents"] = (
                withheld.get("unclassified_documents", 0) + 1)
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

    # Care classification, three-valued and read straight off the graph like
    # everything else here (#631). Over the documents that were *published*,
    # which is the right denominator for a report about the published graph —
    # what a narrower scope withheld is on `BuildReport.withheld`.
    care = {
        "yes": sum(1 for o in g.objects(None, rx.SHB["careRelated"])
                   if bool(o.toPython())),
        "no": sum(1 for o in g.objects(None, rx.SHB["careRelated"])
                  if not bool(o.toPython())),
        "unknown": sum(1 for o in g.objects(None, rx.SHB["careClassified"])
                       if not bool(o.toPython())),
    }

    return {
        "triples": len(g),
        "documents": len(set(g.subjects(RDF.type, rx.CRM["E31_Document"]))),
        "readings": len(set(g.subjects(RDF.type, rx.CRM["E33_Linguistic_Object"]))),
        "mentions": len(list(g.triples((None, rx.CRM["P67_refers_to"], None)))),
        # An entity is what a reading refers to; the exporter mints an entity
        # node only as the target of a P67 (there is no `entityType` literal
        # to count any more — the type is an E55 on P2, KG-12).
        "entities": len(set(g.objects(None, rx.CRM["P67_refers_to"]))),
        "authority_links": authority,
        "care": care,
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


def validate_graph(g: Graph, *, shacl: bool = True) -> ontology_validate.Report:
    """The KG-6 check over the corpus graph. Raises :class:`ValidationError`
    when it does not pass — nothing downstream may run on an invalid graph."""
    rep = ontology_validate.validate(g, shacl=shacl)
    if not rep.ok:
        raise ValidationError(rep)
    return rep


def build(outputs_dir: str | Path,
          destination: str | Path = "build/corpus.ttl",
          scope: Optional[PublicationScope] = None,
          *,
          write_manifest: bool = True,
          shacl: bool = True) -> BuildReport:
    """Build the corpus Turtle plus its manifest. Idempotent.

    The vocabulary gate runs between building the graph and writing it: a
    graph that fails raises :class:`ValidationError` and leaves no file behind.
    """
    scope = scope or PublicationScope.from_config()
    g, withheld = build_graph(outputs_dir, scope)
    checked = validate_graph(g, shacl=shacl)

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
        validation={"ok": checked.ok, "checked": checked.checked_properties,
                    "shacl": shacl, **checked.counts()},
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
    parser.add_argument("--no-shacl", action="store_true",
                        help="skip the SHACL shapes in the vocabulary gate "
                             "(the register, domain/range and provenance checks still run)")
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
    try:
        report = build(outputs, args.out, scope, shacl=not args.no_shacl)
    except ValidationError as exc:
        print(f"corpus graph NOT written to {args.out} — the vocabulary gate refused it:",
              file=sys.stderr)
        print(exc.report.render(), file=sys.stderr)
        return 2
    print(report.render())
    if not scope.is_unrestricted:
        print("  note        a narrower publication scope is in effect")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
