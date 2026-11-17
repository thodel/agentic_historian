"""bestand_build.py — the Bestandsgraph for QLever, in one command (KG-4, #330).

    python -m knowledge_hub.bestand_build --out /srv/kg            # build if anything changed
    python -m knowledge_hub.bestand_build --out /srv/kg --status   # what is built, from when
    python -m knowledge_hub.bestand_build --out /srv/kg --check http://127.0.0.1:7019/

Phase 2 of #325 builds the graph from the holdings themselves (KG-7 … KG-16):
Dodis, Königsfelden, SSRQ, the Musiklexikon, SIKART, Bullinger and Escher, plus
the corpus graph of the pipeline (KG-2/KG-3) when its ``corpus.ttl`` is given.
Each exporter already writes sorted N-Triples and a manifest; this module runs
them one after the other, each in its own process (a million-triple rdflib
graph is freed when the process ends), and puts the parts where QLever indexes
them: ``<out>/parts/<source>.nt``.

Three rules carried over from KG-4 phase 1 (``corpus_build``, ``refresh.sh``):

* **Rebuild-and-swap.** The parts are built into ``parts.new/`` and replace
  ``parts/`` only when every attempted source succeeded; a failed exporter
  leaves the previous graph serving.
* **Nothing is silent.** A source whose input is missing is listed as
  ``skipped`` with the reason, never just absent; the manifest says which
  sources the served graph holds.
* **A holding does not silently shrink.** A part with less than half the
  triples of its last build fails the build (``--allow-shrink`` to accept it):
  an ingest that broke a database must not replace the graph it came from.
* **Only rebuild what changed.** The exporters stamp their build time into the
  graph (``prov:Activity``), so two builds are never byte-identical. The
  decision to rebuild is taken on the inputs instead: path, size and mtime of
  every input file, plus the commit. Unchanged inputs, unchanged graph — the
  refresh timer can run nightly at no cost.

**Layers.** Human Lineage (HL-9, #830) is not a holding but a layer over
them: kinship and ``sameAs`` from a CC BY-SA 4.0 dataset. Decided 17.11.2026
(#830, option a): it is built as its own part, after the holdings and from
their freshly built parts, and indexed into its own **named graph**
(``https://tei.dh.unibe.ch/id/graph/humanlineage``), so the share-alike
terms attach to that graph and not to the holdings. A query without
``GRAPH`` sees the union (checked against QLever 17.11.2026); ``FROM`` or
``MINUS { GRAPH … }`` selects or leaves out the layer. ``KG_HUMANLINEAGE=0``
switches it off.

A layer that fails (the Human Lineage MCP does not answer) does not hold the
holdings back: the previous build of the layer is kept and said so
(``kept``), or, without one, the layer is ``failed`` and absent. The graph
IRI travels next to the part as ``<part>.nt.graph``; the index step reads it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

__all__ = ["LAYERS", "SOURCES", "Layer", "Planned", "Source", "build", "check_endpoint",
           "fingerprint", "layers_enabled", "plan", "read_manifest", "MANIFEST"]

#: The manifest next to the parts; ``--status`` and ``--check`` read it.
MANIFEST = "bestand.manifest.json"

#: Where the SQLite databases of the MCP servers lie on idhefix.
DEFAULT_DB_DIR = "/home/tobias/mcp/data"


@dataclass(frozen=True)
class Source:
    """One exporter: its module under ``knowledge_hub.sources`` and its inputs
    as ``(cli flag, env var, default, required)``."""

    name: str
    module: str
    inputs: tuple[tuple[str, str, str, bool], ...]
    issue: str


def _db(name: str, issue: str, env: str) -> Source:
    return Source(name, name, (("--db", env, f"{{db_dir}}/{name}.db", True),), issue)


SOURCES: tuple[Source, ...] = (
    _db("dodis", "KG-7", "KG_DODIS_DB"),
    _db("kf", "KG-8", "KG_KF_DB"),
    Source("ssrq", "ssrq", (("--register", "KG_SSRQ_REGISTER", "", True),
                            ("--tei", "KG_SSRQ_TEI", "", False)), "KG-9"),
    _db("mls", "KG-13", "KG_MLS_DB"),
    _db("sikart", "KG-14", "KG_SIKART_DB"),
    _db("bullinger", "KG-15", "KG_BULLINGER_DB"),
    _db("escher", "KG-16", "KG_ESCHER_DB"),
)

@dataclass(frozen=True)
class Layer:
    """A part built *over* the holdings, into its own named graph under its
    own licence. ``reads`` are the holdings whose parts it is given."""

    name: str
    module: str
    graph: str
    licence: str
    licence_uri: str
    attribution: str
    switch: str                 # env var; 0/false/no/off switches the layer off
    reads: tuple[str, ...]
    reads_env: str              # env var overriding ``reads`` (comma-separated)
    issue: str


#: Dodis is read since its persons carry Wikidata/GND/HLS from Wikidata's own
#: P701 (``crosswalks/dodis_wikidata.json``); ``humanlineage`` reads only the
#: person lines of each part, so Dodis's document triples cost nothing.
LAYERS: tuple[Layer, ...] = (
    Layer("humanlineage", "humanlineage", "https://tei.dh.unibe.ch/id/graph/humanlineage",
          "CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/",
          "Human Lineage, https://humanlineage.org (Zenodo, concept DOI 10.5281/zenodo.23284943)",
          "KG_HUMANLINEAGE", ("dodis", "kf", "ssrq", "mls", "sikart", "bullinger", "escher"),
          "KG_HUMANLINEAGE_SOURCES", "HL-9 (#830)"),
)
#: Next to a part: the named graph it goes into. Without one, the default graph.
GRAPH_SUFFIX = ".graph"
_OFF = {"0", "false", "no", "off"}


def layers_enabled(env: dict | None = None) -> list[tuple[Layer, tuple[str, ...]]]:
    """The layers to build, each with the holdings it reads."""
    env = dict(os.environ if env is None else env)
    out = []
    for layer in LAYERS:
        if env.get(layer.switch, "1").strip().lower() in _OFF:
            continue
        reads = tuple(x.strip() for x in env.get(layer.reads_env, "").split(",") if x.strip())
        out.append((layer, reads or layer.reads))
    return out


#: The pipeline's own corpus graph (Turtle from ``corpus_build``), converted to
#: sorted N-Triples so every part has one format.
CORPUS_ENV = "KG_CORPUS_TTL"


@dataclass
class Planned:
    name: str
    argv: list[str] = field(default_factory=list)       # without --out / --commit
    inputs: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        return not self.missing


def plan(env: Optional[dict] = None) -> list[Planned]:
    """What would be built from ``env`` (default ``os.environ``): one entry per
    source, with its arguments and what is missing. The corpus graph is
    planned only when ``KG_CORPUS_TTL`` names a file."""
    env = dict(os.environ if env is None else env)
    db_dir = env.get("KG_SOURCE_DB_DIR", DEFAULT_DB_DIR)
    out: list[Planned] = []
    for src in SOURCES:
        p = Planned(src.name)
        for flag, var, default, required in src.inputs:
            value = env.get(var) or default.format(db_dir=db_dir)
            if not value:
                if required:
                    p.missing.append(f"{var} not set")
                continue
            path = Path(value)
            if not path.exists():
                if required:
                    p.missing.append(f"{path} missing")
                continue
            p.argv += [flag, str(path)]
            p.inputs.append(path)
        out.append(p)
    corpus = env.get(CORPUS_ENV)
    if corpus:
        p = Planned("corpus")
        path = Path(corpus)
        if path.exists():
            p.inputs.append(path)
        else:
            p.missing.append(f"{path} missing")
        out.append(p)
    return out


def _stat_key(path: Path) -> list:
    """Size and mtime of a file, or of every file under a directory (the SSRQ
    TEI tree). Content hashing would read 400 MB of SQLite each night."""
    if path.is_dir():
        rows = []
        for f in sorted(path.rglob("*")):
            if f.is_file():
                st = f.stat()
                rows.append([str(f.relative_to(path)), st.st_size, st.st_mtime_ns])
        return [str(path), hashlib.sha256(json.dumps(rows).encode()).hexdigest()]
    st = path.stat()
    return [str(path), st.st_size, st.st_mtime_ns]


def fingerprint(planned: list[Planned], commit: str,
                layers: list[tuple[Layer, tuple[str, ...]]] | None = None) -> str:
    """One hash over the commit, the inputs of every runnable source and the
    layers switched on. The same inputs and code give the same fingerprint,
    whatever the clock says. (A new Human Lineage snapshot is not an input
    here: ``--force`` after one.)"""
    body = {"commit": commit,
            "sources": {p.name: [_stat_key(i) for i in p.inputs] for p in planned if p.runnable}}
    if layers:
        body["layers"] = {layer.name: list(reads) for layer, reads in layers}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def read_manifest(out_dir: Path) -> Optional[dict]:
    path = Path(out_dir) / MANIFEST
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL, text=True,
                                       cwd=Path(__file__).resolve().parent).strip()
    except Exception:                               # noqa: BLE001 — no checkout is a fact, not an error
        return ""


def _corpus_to_nt(ttl: Path, out: Path) -> int:
    from rdflib import Graph
    from knowledge_hub.sources._common import write_sorted_ntriples
    g = Graph()
    g.parse(ttl, format="turtle")
    return write_sorted_ntriples(g, out)


Runner = Callable[[list[str]], int]

#: A part below this share of its previous build fails the build.
SHRINK_LIMIT = 0.5


def _previous_triples(manifest: Optional[dict], source: str) -> int:
    for row in (manifest or {}).get("sources", []):
        if row.get("source") == source and row.get("status") in ("built", "kept"):
            return int(row.get("triples", 0))
    return 0


def _run(argv: list[str]) -> int:
    # From the package directory, so ``-m knowledge_hub.sources.<x>`` resolves
    # wherever the timer starts us.
    return subprocess.run(argv, check=False, cwd=Path(__file__).resolve().parents[1]).returncode


def build(out_dir: str | Path, *, env: Optional[dict] = None, commit: Optional[str] = None,
          force: bool = False, allow_shrink: bool = False, runner: Optional[Runner] = None,
          python: str = sys.executable) -> dict:
    """Build the parts into ``<out>/parts.new`` and swap them in.

    Returns the manifest, with ``status`` one of ``unchanged`` (inputs and
    commit as last time, nothing done), ``built`` (swapped in) or ``failed``
    (nothing swapped; the previous parts and manifest stay)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    runner = runner or _run
    commit = _commit() if commit is None else commit
    planned = plan(env)
    layers = layers_enabled(env)
    fp = fingerprint(planned, commit, layers)
    previous = read_manifest(out_dir)
    parts = out_dir / "parts"
    if (not force and previous and previous.get("fingerprint") == fp
            and previous.get("status") == "built" and parts.is_dir()):
        return {**previous, "status": "unchanged"}

    staging = out_dir / "parts.new"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    started = _now()
    rows: list[dict] = []
    failed: list[str] = []
    for p in planned:
        if not p.runnable:
            rows.append({"source": p.name, "status": "skipped", "reason": "; ".join(p.missing)})
            continue
        part = staging / f"{p.name}.nt"
        if p.name == "corpus":
            try:
                triples = _corpus_to_nt(p.inputs[0], part)
                rows.append({"source": "corpus", "status": "built", "triples": triples,
                             "input": str(p.inputs[0])})
            except Exception as exc:                # noqa: BLE001 — reported, then nothing swapped
                failed.append("corpus")
                rows.append({"source": "corpus", "status": "failed", "reason": str(exc)[:300]})
            continue
        argv = [python, "-m", f"knowledge_hub.sources.{p.name}", *p.argv, "--out", str(part)]
        if commit:
            argv += ["--commit", commit]
        code = runner(argv)
        man = Path(str(part) + ".manifest.json")
        if code != 0 or not part.exists():
            failed.append(p.name)
            rows.append({"source": p.name, "status": "failed", "reason": f"exit {code}"})
            continue
        report = json.loads(man.read_text(encoding="utf-8")) if man.exists() else {}
        triples = int(report.get("triples", 0))
        before = _previous_triples(previous, p.name)
        if not allow_shrink and before and triples < before * SHRINK_LIMIT:
            failed.append(p.name)
            rows.append({"source": p.name, "status": "failed",
                         "reason": f"shrank from {before:,} to {triples:,} triples (--allow-shrink to accept)"})
            continue
        rows.append({"source": p.name, "status": "built", "triples": triples,
                     "built_at": report.get("built_at", ""), "counts": report.get("counts", {}),
                     "withheld": {k: v for k, v in (report.get("withheld") or {}).items() if v}})

    built = [r for r in rows if r["status"] == "built"]
    if built and not failed:
        for layer, reads in layers:
            rows.append(_build_layer(layer, reads, staging, parts, previous, runner=runner,
                                     python=python, commit=commit, allow_shrink=allow_shrink))
    served = [r for r in rows if r["status"] in ("built", "kept")]
    manifest = {"schema": "bestandsgraph/1", "fingerprint": fp, "commit": commit,
                "started_at": started, "built_at": _now(),
                "triples": sum(r.get("triples", 0) for r in served),
                "sources": rows,
                "graphs": {"default": "the holdings, each under its own terms",
                           **{r["graph"]: {"layer": r["source"], "licence": r["licence"],
                                           "licence_uri": r["licence_uri"],
                                           "attribution": r["attribution"]}
                              for r in served if r.get("graph")}}}
    if failed or not built:
        shutil.rmtree(staging, ignore_errors=True)
        reason = (f"failed: {', '.join(failed)}" if failed else "no source could be built")
        return {**manifest, "status": "failed", "reason": reason}

    old = out_dir / "parts.old"
    if old.exists():
        shutil.rmtree(old)
    if parts.exists():
        parts.rename(old)
    staging.rename(parts)
    shutil.rmtree(old, ignore_errors=True)
    manifest["status"] = "built"
    (out_dir / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8")
    return manifest


def _build_layer(layer: Layer, reads: tuple[str, ...], staging: Path, parts: Path,
                 previous: dict | None, *, runner: Runner, python: str, commit: str,
                 allow_shrink: bool) -> dict:
    """Build one layer from the staged holdings. A failure keeps the previous
    build of the layer, if there is one; it never fails the holdings."""
    part = staging / f"{layer.name}.nt"
    meta = {"source": layer.name, "graph": layer.graph, "licence": layer.licence,
            "licence_uri": layer.licence_uri, "attribution": layer.attribution,
            "reads": list(reads)}
    inputs = [staging / f"{s}.nt" for s in reads if (staging / f"{s}.nt").exists()]
    if not inputs:
        return {**meta, "status": "skipped", "reason": "none of the holdings it reads was built"}
    argv = [python, "-m", f"knowledge_hub.sources.{layer.module}", "--graph",
            *[str(i) for i in inputs], "--out", str(part)]
    if commit:
        argv += ["--commit", commit]
    code = runner(argv)
    man = Path(str(part) + ".manifest.json")
    reason = ""
    if code != 0 or not part.exists():
        reason = f"exit {code}"
    else:
        report = json.loads(man.read_text(encoding="utf-8")) if man.exists() else {}
        triples = int(report.get("triples", 0))
        before = _previous_triples(previous, layer.name)
        if not allow_shrink and before and triples < before * SHRINK_LIMIT:
            reason = f"shrank from {before:,} to {triples:,} triples (--allow-shrink to accept)"
        else:
            Path(str(part) + GRAPH_SUFFIX).write_text(layer.graph + "\n", encoding="utf-8")
            return {**meta, "status": "built", "triples": triples,
                    "built_at": report.get("built_at", ""), "counts": report.get("counts", {}),
                    "withheld": {k: v for k, v in (report.get("withheld") or {}).items() if v}}
    for leftover in (part, man):
        leftover.unlink(missing_ok=True)
    old = parts / f"{layer.name}.nt"
    if old.exists():
        for suffix in ("", ".manifest.json"):
            src = Path(str(old) + suffix)
            if src.exists():
                shutil.copy2(src, Path(str(part) + suffix))
        Path(str(part) + GRAPH_SUFFIX).write_text(layer.graph + "\n", encoding="utf-8")
        return {**meta, "status": "kept", "triples": _previous_triples(previous, layer.name),
                "reason": f"{reason}; the previous build of the layer is kept"}
    return {**meta, "status": "failed", "reason": f"{reason}; no previous build, the layer is absent"}


COUNT_QUERY = "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"
GRAPH_COUNT_QUERY = "SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{graph}> {{ ?s ?p ?o }} }}"


def _sparql_count(endpoint: str, timeout: float = 30.0, *, graph: str | None = None) -> int:
    query = GRAPH_COUNT_QUERY.format(graph=graph) if graph else COUNT_QUERY
    url = endpoint.rstrip("/") + "/?" + urllib.parse.urlencode({"query": query})
    req = urllib.request.Request(url, headers={"Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 — our own endpoint
        data = json.loads(resp.read().decode("utf-8"))
    return int(data["results"]["bindings"][0]["n"]["value"])


def _graph_count(endpoint: str, graph: str) -> int:
    return _sparql_count(endpoint, graph=graph)


def check_endpoint(out_dir: str | Path, endpoint: str, *,
                   count: Callable[[str], int] = _sparql_count,
                   count_graph: Callable[[str, str], int] = _graph_count) -> tuple[bool, str]:
    """Does the endpoint serve the graph the manifest describes? The served
    triple count must equal the built one; anything else is a stale index.
    Each layer must sit in its own named graph with its own count — a layer
    indexed into the default graph would carry its licence into the holdings."""
    manifest = read_manifest(Path(out_dir))
    if not manifest:
        return False, f"no {MANIFEST} in {out_dir} — nothing built yet"
    try:
        served = count(endpoint)
    except Exception as exc:                        # noqa: BLE001 — the answer is the finding
        return False, f"{endpoint} does not answer: {exc}"
    built = int(manifest.get("triples", 0))
    if served != built:
        return False, (f"{endpoint} serves {served:,} triples, the build from "
                       f"{manifest.get('built_at')} has {built:,} — the index is stale")
    for row in manifest.get("sources", []):
        if row.get("graph") and row.get("status") in ("built", "kept"):
            try:
                in_graph = count_graph(endpoint, row["graph"])
            except Exception as exc:                # noqa: BLE001 — the answer is the finding
                return False, f"{endpoint} does not answer for <{row['graph']}>: {exc}"
            if in_graph != int(row.get("triples", 0)):
                return False, (f"<{row['graph']}> holds {in_graph:,} triples, the layer "
                               f"{row['source']} has {int(row.get('triples', 0)):,} — not in its "
                               "own graph, or stale")
    return True, f"{endpoint} serves {served:,} triples = build {manifest.get('built_at')}"


def render(manifest: dict) -> str:
    lines = [f"Bestandsgraph — {manifest.get('status')}  "
             f"(built {manifest.get('built_at', '–')}, commit {manifest.get('commit') or '–'})",
             f"  triples  {manifest.get('triples', 0):,}"]
    for r in manifest.get("sources", []):
        layer = f"  <{r['graph']}>, {r['licence']}" if r.get("graph") else ""
        if r["status"] == "built":
            lines.append(f"  {r['source']:<10} {r.get('triples', 0):>12,}{layer}")
        elif r["status"] == "kept":
            lines.append(f"  {r['source']:<10} {r.get('triples', 0):>12,}{layer}  kept: {r.get('reason', '')}")
        else:
            lines.append(f"  {r['source']:<10} {r['status']}: {r.get('reason', '')}")
    if manifest.get("reason"):
        lines.append(f"  {manifest['reason']} — the previous graph stays")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True, help="directory with parts/ and the manifest")
    ap.add_argument("--status", action="store_true", help="show what is built, change nothing")
    ap.add_argument("--force", action="store_true", help="rebuild even if no input changed")
    ap.add_argument("--allow-shrink", action="store_true",
                    help="accept a holding with less than half its previous triples")
    ap.add_argument("--check", metavar="ENDPOINT", help="compare the served triple count with the build")
    args = ap.parse_args(argv)
    if args.status:
        manifest = read_manifest(args.out)
        print(render(manifest) if manifest else f"nothing built in {args.out}")
        return 0 if manifest else 1
    if args.check:
        ok, message = check_endpoint(args.out, args.check)
        print(message)
        return 0 if ok else 1
    manifest = build(args.out, force=args.force, allow_shrink=args.allow_shrink)
    print(render(manifest))
    # Exit codes refresh_bestand.sh reads: 0 built (reindex), 3 unchanged (nothing to do), 1 failed.
    return {"built": 0, "unchanged": 3}.get(manifest["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
