"""training_report.py — ein Trainingsbericht, bei dem jede Zahl beschriftet ist (#638).

T5 of the training epic. The trainer writes `training.json` when a run ends; the
bot's job is to fetch it and commit it to the outputs repo, exactly as it does
for `pipeline.json` today. Drawing the curves belongs to the outputs repo's own
epic — `TRAINING_INTEGRATION_PLAN.md` §5 says so, and that is the whole of the
design question.

What is *not* settled by the plan, and is therefore the work: the outputs site
deliberately refuses unscoped accuracy figures, and a finished kraken run carries
**four** numbers that all look like "accuracy":

===========================  ========================  ==================
number                       scope                     unit
===========================  ========================  ==================
``metrics.cer`` /            validation/test split,    fraction /
``char_accuracy``            **line crops from the     percent
  (``ketos test``)           ground-truth
                             segmentation**
``metrics.benchmark_cer``    the named held-out        fraction
                             benchmark; the trainer's
                             contract calls this the
                             primary quality claim
``best.val_metric`` /        kraken's own validation   fraction, but an
``val_error``                during training, over     **accuracy** —
  (``training.json``)        the **ten best            higher is better
                             checkpoints only**
eval-harness CER             a full page through our   **not in this
                             own segmentation          record**
===========================  ========================  ==================

Three traps this module exists to defuse:

**The direction flips.** ``val_metric`` is an accuracy; every other number here
is an error rate. ``curves.py`` emits both values "so nobody has to remember
which way this one points", and this page does the same.

**The curve is incomplete by construction.** kraken keeps the top ten
checkpoints, so ``training.json`` is not every epoch — it says ``complete:
false`` and why. ``still_improving`` is the finding worth reading: checkpoints
41–50 mean the run was getting better when it stopped, 3–12 mean it peaked early
and got worse for forty epochs. Reading the curve as if it were complete is the
mistake ``curves.py`` was written to prevent.

**The missing number has to be visibly missing.** Leaving the eval-harness CER
out would let the ``ketos test`` number stand in for it, and those two measure
different things and will disagree. "Not measured" is the statement.

Everything here is pure: a job record and a curve payload in, a dict of files
out. No network, no clock.

Run: pytest agentic_historian/tests/test_ah_638_training_report.py -v
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

__all__ = [
    "RUN_PREFIX", "RUN_ID_RE", "RunIdRefused", "Number", "numbers",
    "curve_summary", "index_md", "files", "run_path",
]

#: Where a run lands in the outputs repo. `TRAINING_INTEGRATION_PLAN.md` §5.
RUN_PREFIX = "docs/training"

#: A run id becomes a permanent public path, so it is checked before it gets
#: one — the same character class serving's SEC-6 (#577) put on the gateway's
#: job-id parameter. Real ids (`20261012T090000Z-medieval-smoke`, a uuid) fit;
#: `../x` and `?q=1` do not.
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RunIdRefused(ValueError):
    """The run id may not become a URL."""


def run_path(run_id: str) -> str:
    """``docs/training/<run_id>`` — or a refusal.

    Refused here rather than published and retired later: the project has
    already had to retire published addresses built from unchecked ids
    (``utils/document_id``), and a path leading out of ``docs/`` is worse than a
    cosmetic one.
    """
    candidate = (run_id or "").strip()
    if candidate in (".", "..") or not RUN_ID_RE.match(candidate):
        raise RunIdRefused(
            f"run id {run_id!r} may not become a public path — allowed are "
            f"letters, digits, '.', '_' and '-', at most 64 characters")
    return f"{RUN_PREFIX}/{candidate}"


# ── one number, with everything needed to read it ───────────────────────────

@dataclass(frozen=True)
class Number:
    """A measurement, or the explicit absence of one.

    ``value is None`` is a value: it means *not measured*, and renders as that
    rather than as 0. The project has paid for the other choice four times
    (#119, #595, #631, #634).
    """

    label: str
    value: Optional[float]
    #: ``"fraction"`` | ``"percent"`` — what the number is in, not what it means.
    unit: str
    #: What it was measured against, and on what. The sentence a reader needs in
    #: order to compare this number with another one.
    scope: str
    #: ``"error"`` (lower is better) or ``"accuracy"`` (higher is better).
    direction: str = "error"
    note: str = ""

    @property
    def measured(self) -> bool:
        return self.value is not None

    def shown(self) -> str:
        if self.value is None:
            return "nicht gemessen"
        if self.unit == "percent":
            return f"{self.value:.2f} %"
        return f"{self.value:.4f} ({self.value * 100:.2f} %)"

    def arrow(self) -> str:
        """Which way is good. Written out, because the four numbers differ.

        A number that was not measured has no direction to read, and printing
        one anyway makes an absence look like a result with a property.
        """
        if self.value is None:
            return "—"
        return "niedriger ist besser" if self.direction == "error" \
            else "höher ist besser"

    def row(self) -> str:
        scope = f"{self.scope} — {self.note}" if self.note else self.scope
        return "| " + " | ".join(
            [self.label, self.shown(), self.arrow(), scope]) + " |"


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get(mapping: Any, *keys: str) -> Any:
    """``mapping[k1][k2]…``, or None at the first thing that is not a dict."""
    current = mapping
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def numbers(job: dict, curve: Optional[dict] = None) -> list[Number]:
    """The four, in the order a reader should meet them.

    The benchmark first, because the trainer's own contract calls it the primary
    quality claim and the split CER "a secondary check on the validation split,
    which overlaps training projects".
    """
    metrics = _get(job, "metrics") or {}
    curve = curve or {}
    best = curve.get("best") if isinstance(curve.get("best"), dict) else {}

    measured_on = metrics.get("measured_on")
    benchmark_scope = (f"gehaltener Benchmark `{measured_on}`" if measured_on
                       else "gehaltener Benchmark")
    samples = metrics.get("benchmark_samples")
    benchmark_note = f"{samples} Samples" if samples else ""
    if not measured_on:
        benchmark_note = ("kein Benchmark im Request benannt"
                          if benchmark_note == "" else benchmark_note)

    split_samples = metrics.get("samples")
    return [
        Number(
            label="Benchmark-CER",
            value=_float(metrics.get("benchmark_cer")),
            unit="fraction",
            scope=benchmark_scope,
            note=benchmark_note,
        ),
        Number(
            label="`ketos test`-CER",
            value=_float(metrics.get("cer")),
            unit="fraction",
            scope="Validierungssplit, **Zeilenbilder aus der GT-Segmentierung**",
            note=(f"{split_samples} Samples; der Split überlappt die "
                  f"Trainingsprojekte" if split_samples
                  else "der Split überlappt die Trainingsprojekte"),
        ),
        Number(
            label="`char_accuracy`",
            value=_float(metrics.get("char_accuracy")),
            unit="percent",
            scope="dieselbe Messung wie die Zeile darüber, in Prozent",
            direction="accuracy",
            note="die Einheit, auf die sich die Hub-Schwelle des Trainers bezieht",
        ),
        Number(
            label="krakens `val_metric` (bester Checkpoint)",
            value=_float(best.get("val_metric")),
            unit="fraction",
            scope="krakens eigene Validierung während des Trainings",
            direction="accuracy",
            note="nur über die zehn besten Checkpoints, siehe Kurve",
        ),
        Number(
            label="krakens `val_error` (bester Checkpoint)",
            value=_float(best.get("val_error")),
            unit="fraction",
            scope="dieselbe Messung, als Fehlerrate",
            note="`1 - val_metric`, damit die Richtung nicht zu merken ist",
        ),
        Number(
            label="Eval-Harness-CER",
            value=None,
            unit="fraction",
            scope="ganze Seite durch **unsere** Segmentierung",
            note="eine andere Messung als die `ketos test`-Zahl, und sie wird "
                 "von ihr abweichen",
        ),
    ]


def curve_summary(curve: Optional[dict]) -> list[str]:
    """What the curve says, including what it cannot say.

    Never "the run improved until epoch 50": the file holds the ten best
    checkpoints, so the honest statement is which epochs survived and what that
    implies — which is exactly ``still_improving``.
    """
    if not isinstance(curve, dict) or not curve:
        return ["Keine Kurve abgeholt."]
    points = curve.get("points") or []
    if not points:
        note = curve.get("note") or "keine Epochen aufgezeichnet"
        return [f"Keine Epochen in der Kurve: {note}"]

    epochs = [p.get("epoch") for p in points if isinstance(p, dict)]
    epochs = [e for e in epochs if isinstance(e, int)]
    lines = [f"- **Aufgezeichnete Epochen:** {len(points)}"
             + (f" (Epoche {min(epochs)}–{max(epochs)})" if epochs else "")]
    if curve.get("last_epoch") is not None:
        lines.append(f"- **Letzte Epoche im Record:** {curve['last_epoch']}")

    still = curve.get("still_improving")
    if still is True:
        lines.append("- **Lief beim Abbruch noch besser:** ja — die "
                     "überlebenden Checkpoints sind die späten Epochen. Länger "
                     "trainieren ist die naheliegende nächste Frage.")
    elif still is False:
        lines.append("- **Lief beim Abbruch noch besser:** nein — der Lauf hatte "
                     "seinen Höhepunkt früher. Länger zu trainieren hilft hier "
                     "nicht; die Daten oder die Konfiguration sind die Frage.")
    else:
        lines.append("- **Lief beim Abbruch noch besser:** unbekannt.")

    if curve.get("complete") is True:
        lines.append("- **Vollständig:** ja")
    else:
        note = curve.get("note") or ("kraken behält nur die zehn besten "
                                     "Checkpoints, also ist dies nicht jede Epoche")
        lines.append(f"- **Vollständig:** nein — {note}")
    if curve.get("live"):
        lines.append("- **Live gelesen** aus dem Checkpoint-Verzeichnis, während "
                     "der Lauf noch lief: kein Endstand.")
    if curve.get("source"):
        lines.append(f"- **Quelle:** `{curve['source']}`")
    return lines


# ── the page ───────────────────────────────────────────────────────────────

def _datasets(job: dict) -> list[str]:
    """One line per dataset in the request — the provenance of the training data."""
    specs = _get(job, "request", "datasets")
    if not isinstance(specs, list) or not specs:
        return ["- keine Datasets im Request"]
    lines = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        repo = spec.get("hf_repo") or "?"
        if spec.get("all_projects"):
            what = "alle Projekte"
        else:
            projects = spec.get("projects") or []
            what = ", ".join(str(p) for p in projects) or "?"
        caps = []
        if spec.get("max_pages") is not None:
            caps.append(f"max. {spec['max_pages']} Seiten")
        if spec.get("split"):
            caps.append(f"Split `{spec['split']}`")
        tail = f" ({', '.join(caps)})" if caps else ""
        lines.append(f"- `{repo}` — {what}{tail}")
    return lines or ["- keine Datasets im Request"]


def _wall_time(job: dict) -> str:
    started, finished = job.get("started_at"), job.get("finished_at")
    if not started or not finished:
        return "unbekannt"
    try:
        from datetime import datetime

        def _parse(value: str):
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        held = (_parse(finished) - _parse(started)).total_seconds()
    except (TypeError, ValueError):
        return "unbekannt"
    if held >= 3600:
        return f"{held / 3600:.1f} h"
    return f"{held / 60:.0f} min"


def _sentences(job: dict) -> list[str]:
    """What the run's own record says about registration, promotion and the hub.

    Copied verbatim, because each of these is already a sentence the trainer
    wrote about what it did — including when it did nothing (#88).
    """
    lines = []
    for key, label in (("registration", "Registrierung"),
                       ("published", "Hub"),
                       ("promotion_reason", "Promotion"),
                       ("convergence_override", "Konvergenz-Guard übergangen"),
                       ("geometry_override", "Geometrie-Guard übergangen")):
        value = job.get(key)
        if value:
            lines.append(f"- **{label}:** {value}")
    promoted = job.get("promoted")
    if promoted is not None:
        lines.append(f"- **Promoviert:** {'ja' if promoted else 'nein'}")
    return lines


def index_md(job: dict, curve: Optional[dict] = None) -> str:
    """The run's page. Every number carries its scope, its unit and its direction."""
    run_id = str(job.get("id") or "?")
    request = _get(job, "request") or {}
    model_id = request.get("model_id") or "?"
    engine = request.get("engine") or "?"
    status = job.get("status") or "?"

    lines = ["---", "layout: default", f"title: Training {run_id}", "---", "",
             f"# Trainingslauf `{run_id}`", ""]

    if status != "completed":
        lines += [f"> ⚠️ Dieser Lauf endete als `{status}`. Die Zahlen unten sind, "
                  f"was der Record trägt — kein fertiges Modell.", ""]

    lines += ["| | |", "|---|---|",
              f"| Modell | `{model_id}` |",
              f"| Engine | `{engine}` |",
              f"| Status | `{status}` |",
              f"| Laufzeit | {_wall_time(job)} |"]
    if job.get("model_path"):
        lines.append(f"| Modellpfad | `{job['model_path']}` |")
    code = _get(job, "code", "commit")
    if code:
        dirty = " (dirty)" if _get(job, "code", "dirty") else ""
        lines.append(f"| Code | `{str(code)[:12]}{dirty}` |")
    lines.append("")

    # The table this module exists for.
    measured = [n for n in numbers(job, curve)]
    lines += ["## Zahlen", "",
              "Jede Zeile nennt ihren Umfang, ihre Einheit und ihre Richtung. "
              "Die vier Messungen hier beantworten **verschiedene** Fragen und "
              "werden voneinander abweichen; eine ohne ihre Zeile daneben zu "
              "zitieren ist genau das, was dieser Katalog nicht tut.", "",
              "| Messung | Wert | Richtung | Umfang |",
              "|---|---|---|---|"]
    lines += [n.row() for n in measured]
    if not any(n.measured for n in measured):
        lines += ["", "> Dieser Lauf hat **keine** Zahl gemessen, die hier stehen "
                      "könnte. Das ist der Befund, nicht eine Null."]
    lines.append("")

    lines += ["## Kurve", ""] + curve_summary(curve) + [""]
    lines += ["## Trainingsdaten", ""] + _datasets(job) + [""]

    sentences = _sentences(job)
    if sentences:
        lines += ["## Was danach geschah", ""] + sentences + [""]

    params = _get(job, "request", "params")
    if isinstance(params, dict) and params:
        lines += ["## Reproduzierbarkeit", "",
                  "Der Request, mit dem dieser Lauf gestartet wurde:", "",
                  "```json",
                  json.dumps(request, indent=2, ensure_ascii=False,
                             sort_keys=True),
                  "```", ""]

    lines += ["## Dateien", "",
              "- [`training.json`](training.json) — die Kurve, wie der Trainer "
              "sie geschrieben hat",
              "- [`job.json`](job.json) — der vollständige Job-Record", ""]
    return "\n".join(lines)


def files(job: dict, curve: Optional[dict] = None) -> dict[str, bytes]:
    """``{remote path: bytes}`` for one run — the shape ``_commit_files`` takes.

    Raises :class:`RunIdRefused` when the id may not become a path. The raw
    ``training.json`` and ``job.json`` go beside the page unchanged: the page is
    a reading of them, and a reader who distrusts the reading needs the source.
    """
    base = run_path(str(job.get("id") or ""))
    out = {
        f"{base}/index.md": index_md(job, curve).encode("utf-8"),
        f"{base}/job.json": json.dumps(job, indent=2, ensure_ascii=False,
                                       sort_keys=True).encode("utf-8"),
    }
    if curve:
        out[f"{base}/training.json"] = json.dumps(
            curve, indent=2, ensure_ascii=False).encode("utf-8")
    return out
