"""_common.py — was alle Bestandsexporte teilen: Ausgabe, Manifest, Prüfung."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rdflib import Graph


@dataclass
class ExportReport:
    source: str = ""
    built_at: str = ""
    db: str = ""
    destination: str = ""
    commit: str = ""
    triples: int = 0
    counts: dict = field(default_factory=dict)      # was gebaut wurde, je Art
    withheld: dict = field(default_factory=dict)    # was die Quelle nicht hergab
    validation: dict = field(default_factory=dict)  # Befunde der Prüfung, wenn gelaufen
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        lines = [f"{self.source} → {self.destination}",
                 f"  built at    {self.built_at}  ({self.seconds:.0f} s)",
                 f"  triples     {self.triples}"]
        for k, v in self.counts.items():
            lines.append(f"  {k:<11} {v}")
        held = {k: v for k, v in self.withheld.items() if v}
        if held:
            lines.append("  withheld    " + ", ".join(f"{k}={v}" for k, v in sorted(held.items())))
        if self.validation:
            lines.append("  validation  " + ", ".join(f"{k}={v}" for k, v in sorted(self.validation.items()))
                         + ("  VALIDE" if self.validation.get("ok") else "  NICHT VALIDE"))
        return "\n".join(lines)


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return ""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_sorted_ntriples(g: Graph, out: Path) -> int:
    """Sortierte N-Tripel: dieselbe Eingabe ergibt dieselben Bytes, was den
    Diff zwischen zwei Bauten lesbar macht (die Regel aus KG-2/KG-4). Turtle
    wäre kürzer, aber rdflib sortiert dort nicht verlässlich."""
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = g.serialize(format="nt").splitlines()
    lines = sorted(l for l in lines if l.strip())
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def write_manifest(report: ExportReport, out: Path) -> Path:
    p = Path(str(out) + ".manifest.json")
    p.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                 encoding="utf-8")
    return p


def validate_graph(g: Graph, *, shacl: bool) -> dict:
    """Die KG-6-Prüfung als Zahlen fürs Manifest. SHACL ist auf einem
    Millionen-Tripel-Graphen Stunden; darum schaltbar — im Test läuft es auf
    der Fixture mit, im Vollbau ohne."""
    from knowledge_hub.ontology import validate as v
    rep = v.validate(g, shacl=shacl)
    out = {"ok": rep.ok, "checked": rep.checked_properties, "shacl": shacl}
    out.update(rep.counts())
    return out
