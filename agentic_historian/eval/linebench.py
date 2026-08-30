"""
eval/linebench.py — line-level accuracy against real ground truth.

Everything this project measures about quality has so far been *relative*: pairwise
CER between candidates, agreement with a historian's pick, engine strength from
preferences. That was deliberate — a Gate-2 selection is the closest available
reading, not truth, and measuring CER against it would certify our own errors
(#326).

This module adds the one thing that was missing: a corpus WITH ground truth
(Zenodo 4746342 — Swiss federal minutes, line images + PAGE XML, human-corrected to
`status="GT"`). With it, questions that were structurally unanswerable become
arithmetic:

  - does fusion beat the best single engine?          (#300's premise)
  - is the LLM arbitration worth its ~42s per page?   (#406)
  - does the model MATCH SCORE predict quality?       (#313's thesis)
  - is `regret_cer` a usable stand-in for real CER?   (#334)

**What this corpus does not license.** It is 19th-century Kurrent; this project's
material is 14th-16th c., and the model pool is trained accordingly. Absolute CER
here says little about the target corpus. The *relative* comparisons above are what
it supports — and even those inherit a period mismatch that should be stated
whenever a number from here is quoted.

One more caveat worth carrying: the ground truth was produced by post-editing a
Transkribus model's output (`German_Kurrent_XIX_comb-Huber_M2`). That is normal
practice and it is genuine GT, but post-edited references retain the source model's
conventions wherever a corrector did not object.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from loguru import logger

import config
from eval.metrics import cer


DEFAULT_ROOT = Path("data/eval/federal_minutes")


@dataclass
class Line:
    """One line image and its reference text."""
    image: Path
    gt: str
    doc: str = ""


@dataclass
class ModelScore:
    """Corpus-level accuracy for one model over a set of lines."""
    model: str
    engine: str
    lines: int = 0
    chars: int = 0
    errors: int = 0
    failures: int = 0
    seconds: float = 0.0
    per_line: list = field(default_factory=list)      # (image, cer) — for outliers

    @property
    def cer(self) -> Optional[float]:
        """Corpus CER: total errors / total reference characters.

        Corpus-level, not the mean of per-line rates — the same definition
        `ketos test` uses, so these numbers are comparable with the training side
        (serving-atr#80). A mean over lines would let a 4-character line weigh as
        much as a 90-character one, and this corpus has both.
        """
        return self.errors / self.chars if self.chars else None


def load(root: Path | str = DEFAULT_ROOT, limit: Optional[int] = None) -> list[Line]:
    """Read the manifest into Line records, skipping anything unusable.

    A missing image or an empty reference is dropped rather than scored as a
    failure: it says nothing about a model.
    """
    root = Path(root)
    manifest = root / "manifest.jsonl"
    if not manifest.exists():
        raise FileNotFoundError(f"no manifest at {manifest}")
    out: list[Line] = []
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        img = root / "lines" / d.get("image", "")
        gt = (d.get("gt") or "").strip()
        if not gt or not img.exists():
            continue
        out.append(Line(image=img, gt=gt, doc=d.get("doc", "")))
        if limit and len(out) >= limit:
            break
    return out


def score_model(lines: Iterable[Line], recognise: Callable[[Path], str],
                model: str, engine: str = "") -> ModelScore:
    """Run one recogniser over the lines and score it against the ground truth.

    A failed line counts as a FAILURE, not as a wrong reading: charging its whole
    reference length as errors would make an engine outage look like bad
    recognition, which is the confusion #367 exists to prevent.
    """
    import time

    s = ModelScore(model=model, engine=engine)
    for ln in lines:
        t0 = time.monotonic()
        try:
            hyp = recognise(ln.image) or ""
        except Exception as e:
            s.failures += 1
            s.seconds += time.monotonic() - t0
            logger.warning(f"[linebench] {model} failed on {ln.image.name}: {e}")
            continue
        s.seconds += time.monotonic() - t0
        s.lines += 1
        s.chars += len(ln.gt)
        c = cer(ln.gt, hyp, ignore_case=False, ignore_whitespace=False,
                ignore_punctuation=False)
        s.errors += round(c * len(ln.gt))
        s.per_line.append((ln.image.name, round(c, 4)))
    return s


def format_scores(scores: list[ModelScore]) -> str:
    """A table, best CER first, with the caveats attached rather than assumed."""
    rows = sorted((s for s in scores if s.cer is not None), key=lambda s: s.cer)
    if not rows:
        return "linebench: keine auswertbaren Ergebnisse"
    out = [f"{'model':40} {'CER':>8} {'Zeilen':>7} {'Fehler':>7} {'s/Zeile':>8}"]
    for s in rows:
        out.append(f"{s.model[:40]:40} {s.cer:8.1%} {s.lines:7} {s.failures:7} "
                   f"{(s.seconds / max(1, s.lines)):8.2f}")
    out.append("")
    out.append("19. Jh. Kurrent — der Korpus dieses Projekts ist 14.-16. Jh.")
    out.append("Absolute Werte sind nicht auf die Zielmaterie übertragbar; die")
    out.append("Rangfolge zwischen den Modellen ist die belastbare Aussage.")
    return "\n".join(out)
