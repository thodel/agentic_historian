"""Named training presets, and the cost of each — T2 of the training epic (#362).

A training run occupies GPU 1 of a shared box for hours or days. Two things
follow, and they are the whole reason this module exists rather than a few
options on a slash command.

**A VGSL spec is not something to type into a chat box.** The kraken
architecture is 130 characters —
``[256,64,0,1 Cr4,2,8,4,2 Cr4,2,32,1,1 Mp4,2,4,2 …]`` — where one transposed
digit silently trains a different network and nothing says so until the curve
comes out wrong. So `/train_start` takes a **name** and a model id; everything
else is a code change that gets reviewed.

**What is approved must be what is sent.** :func:`resolve` builds the exact
request body, and the confirmation card is rendered *from that body*. A card
assembled separately from the request would eventually describe a run that is
not the one submitted — and the person clicking Confirm is approving hours of a
shared machine on the strength of that card.

Three decisions worth stating, because each could easily have gone the lazy way:

**No preset carries a VGSL spec.** The trainer defaults ``params.spec`` to its
own ``KRAKEN_PLUS_SPEC``, which lives in ``training-atr-models`` beside the code
that trains with it. Copying that string here would make this the second copy of
an architecture, free to drift from the first. A preset that genuinely needs a
different network says so and says why.

**Every preset is bounded.** ``all_projects=True`` requires ``max_pages``, and
the trainer's own guard explains why: the medieval corpus is 694 projects and
~6.6 TB, "larger than the disk". There is no "everything" preset because there
is no disk for one.

**A cost figure says whether it was measured.** ``pages``/``lines``/``hours``
are ``None`` when nobody has measured them, never 0, and ``measured`` says
whether they come from a run that happened or from dataset-card arithmetic.
``corpus_plan.py`` in the trainer states the same discipline for the same
reason: "every figure a plan reports is an *estimate* from dataset-card
metadata", and a confirmation card that presents an estimate as a measurement is
asking for consent it has not earned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

__all__ = ["MEDIEVAL_REPO", "REQUIRED_FIELDS", "Preset", "PRESETS", "names",
           "get", "resolve", "describe", "PresetError"]

#: The medieval corpus on the hub. 694 project directories, ~6.6 TB
#: (``training-atr-models``'s ``DatasetSpec`` docstring), which is why every
#: selection below is bounded.
MEDIEVAL_REPO = "dh-unibe/image-text_medieval-scripts_xiv-xv-xvi"

#: Fields the trainer's ``TrainRequest`` has no default for, so a body without
#: them is refused on submit rather than at review time.
#:
#: Kept as a named list because the authority is in another repository —
#: ``training-atr-models/src/atr_training/contracts.py::TrainRequest`` — and a
#: cross-repo import is not available here. A test asserts every preset carries
#: them; when the contract grows a required field, this list is the one place to
#: add it.
REQUIRED_FIELDS = ("engine", "model_id", "datasets")


class PresetError(KeyError):
    """No such preset. Carries the names there are."""


@dataclass(frozen=True)
class Preset:
    """One named run: what it is for, what it sends, and what it costs."""

    name: str
    #: One line: what this run is *for*. Shown on the confirmation card, because
    #: "medieval-10epoch" does not tell anybody whether to click.
    what: str
    #: The request body **minus** ``model_id``, which the command supplies.
    body: dict = field(default_factory=dict)
    #: Pages, lines and wall-clock hours. ``None`` where nobody has measured or
    #: estimated them — not 0, which would read as "free".
    pages: Optional[int] = None
    lines: Optional[int] = None
    hours: Optional[float] = None
    #: Whether the three figures come from a run that happened. False means
    #: dataset-card arithmetic, and the card says so.
    measured: bool = False

    @property
    def cost_known(self) -> bool:
        return self.pages is not None or self.hours is not None

    def cost_line(self) -> str:
        """The cost, with its provenance attached to it (#482's habit)."""
        if not self.cost_known:
            return ("Kosten unbekannt — für diesen Preset hat niemand Seiten "
                    "oder Laufzeit geschätzt.")
        bits = []
        if self.pages is not None:
            bits.append(f"{_thousands(self.pages)} Seiten")
        if self.lines is not None:
            bits.append(f"{_thousands(self.lines)} Zeilen")
        if self.hours is not None:
            bits.append(f"≈ {_hours(self.hours)}")
        how = "gemessen" if self.measured else "geschätzt aus den Dataset-Karten"
        return f"{', '.join(bits)} ({how})"


def _thousands(n: int) -> str:
    """``18000`` → ``"18 000"``.

    A helper and not an inline ``.replace(",", " ")``: applied to a whole
    f-string that replacement also eats the sentence's own commas, which it did
    in the first version of the card ("`repo`  alle Projekte  höchstens …").
    """
    return f"{n:,}".replace(",", "\u202f")


def _hours(hours: float) -> str:
    if hours < 1:
        return f"{hours * 60:.0f} min"
    if hours < 48:
        return f"{hours:.0f} h"
    return f"{hours / 24:.0f} Tage"


def _medieval(max_pages: int, *, epochs: Optional[int] = None,
              chunk_size: Optional[int] = None) -> dict:
    """A bounded slice of the medieval corpus, with the trainer's own defaults.

    No ``spec``: the architecture stays where the training code is. No
    ``train_projects`` either — ``all_projects`` with a bound selects across the
    whole corpus rather than privileging whichever projects somebody listed, and
    the trainer refuses the unbounded form outright.
    """
    dataset: dict = {"hf_repo": MEDIEVAL_REPO, "all_projects": True,
                     "max_pages": max_pages}
    if chunk_size is not None:
        dataset["chunk_size"] = chunk_size
    body: dict = {"engine": "kraken", "datasets": [dataset]}
    if epochs is not None:
        body["params"] = {"epochs": epochs}
    return body


#: The presets. Three, spanning the three reasons to start a run: prove the
#: pipeline, train something usable, train the real thing.
PRESETS: dict[str, Preset] = {
    "medieval-smoke": Preset(
        name="medieval-smoke",
        what="Beweist die Kette, nicht das Modell: 40 Seiten, eine Epoche.",
        body=_medieval(40, epochs=1),
        pages=40, lines=None, hours=0.15, measured=False,
    ),
    "medieval-10epoch": Preset(
        name="medieval-10epoch",
        what="Ein brauchbares Korpusmodell über einen begrenzten Ausschnitt.",
        body=_medieval(18_000, epochs=10, chunk_size=2_000),
        pages=18_000, lines=270_000, hours=6.0, measured=False,
    ),
    "medieval-full": Preset(
        name="medieval-full",
        # Bounded although it is called "full": `all_projects` without
        # `max_pages` is refused by the trainer, whose message says why — 694
        # projects, ~6.6 TB, "larger than the disk".
        what="Der grosse Lauf über den begrenzten Korpus. Tage, nicht Stunden.",
        body=_medieval(548_000, chunk_size=2_000),
        pages=548_000, lines=8_000_000, hours=72.0, measured=False,
    ),
}


def names() -> tuple[str, ...]:
    """The preset names, in the order they are meant to be tried."""
    return tuple(PRESETS)


def get(name: str) -> Preset:
    """One preset, or :class:`PresetError` naming the ones there are."""
    found = PRESETS.get((name or "").strip())
    if found is None:
        raise PresetError(
            f"kein Preset {name!r} — vorhanden: {', '.join(names())}")
    return found


def resolve(name: str, model_id: str) -> dict:
    """The **exact** body that will be sent for this preset and model id.

    One function for both the confirmation card and the submit, so what was
    approved is what goes out. Returns a deep-ish copy: a caller that edited the
    result would otherwise edit the preset for the rest of the process.
    """
    import copy

    preset = get(name)
    if not (model_id or "").strip():
        raise PresetError("model_id fehlt — ohne Ziel-Id registriert der "
                          "Trainer das Ergebnis nicht")
    body = copy.deepcopy(preset.body)
    body["model_id"] = model_id.strip()
    return body


def describe(name: str, model_id: str) -> str:
    """The confirmation card, rendered from the resolved body.

    From the body and not from the preset: the numbers somebody approves have to
    be the numbers that were just built for sending.
    """
    preset = get(name)
    body = resolve(name, model_id)
    dataset = (body.get("datasets") or [{}])[0]
    params = body.get("params") or {}

    lines = [
        f"🎓 **Training starten — `{preset.name}`**",
        f"_{preset.what}_",
        "",
        f"**Ziel-Modell:** `{body['model_id']}`",
        f"**Engine:** `{body.get('engine', '?')}`",
        f"**Korpus:** `{dataset.get('hf_repo', '?')}`"
        + (f", alle Projekte, höchstens {_thousands(dataset['max_pages'])} Seiten"
           if dataset.get("all_projects") else
           f", Projekte: {', '.join(dataset.get('train_projects') or []) or '—'}"),
        f"**Epochen:** {params.get('epochs', 'Standard des Trainers')}",
        f"**Kosten:** {preset.cost_line()}",
    ]
    if not params.get("spec"):
        # Said out loud: the thing most likely to be wrong is the thing nobody
        # sees, and "the trainer's default" is a fact about where to look.
        lines.append("**Architektur:** der Standard des Trainers "
                     "(`KRAKEN_PLUS_SPEC`), nicht hier kopiert")
    lines += [
        "",
        "Dieser Lauf belegt GPU 1 der gemeinsamen Box. Bestätigen startet ihn.",
    ]
    return "\n".join(lines)
