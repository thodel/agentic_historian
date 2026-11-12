"""Read a scorer artefact and score with it — the historian's side of #491 L3.

The scorer itself (``atr_training.scorer`` in training-atr-models, #110) is
trained and calibrated on the training host, on edition text and corrected
ground truth only, and saved as one JSON file per cell. This module reads that
file and reproduces the one number the artefact exists for: ``bits_per_char``
of a text under a character n-gram with stupid backoff, and the z-score of
that number against the cell's own held-out distribution.

It is a second implementation of the same formula, on purpose (decision of
12.11.2026): the pipeline on tei must not pull the training package and its GPU
dependencies into its venv for a few hundred lines of arithmetic. The formula
is pinned by a fixture the training code wrote — ``tests/fixtures/scorer`` —
and the expected numbers beside it; a change to either side shows up there.

What this module does not do: train, generate, rank. A scorer is a veto, not
a ranking (#491, pre-experiment): local fluency is not correctness, and the
fused text that #298 diagnosed as worse than its best input scored *best*
under the n-gram. ``fusion.fuse`` uses it only to drop the garbage edge.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

__all__ = ["ARTEFACT_KIND", "BOUNDARY", "Cell", "ScorerArtefact", "ScorerMatrix",
           "century_band", "document_of", "veto_scorer"]

#: ``kind`` the training side writes; anything else is not a scorer.
ARTEFACT_KIND = "char-ngram-scorer"
#: Document boundary marker, as in training.
BOUNDARY = "\n"


def century_band(century) -> Optional[str]:
    """Two centuries wide, as ``atr_training.scorer.century_band``: 15 and 16
    are ``"15-16"``, 19 and 20 ``"19-20"``."""
    if century is None:
        return None
    try:
        c = int(century)
    except (TypeError, ValueError):
        return None
    lo = c - ((c - 1) % 2)
    return f"{lo}-{lo + 1}"


def document_of(path) -> Optional[str]:
    """The Transkribus document id in a materialised page name, as
    ``atr_training.heldout.document_of`` reads it (``<collection>_<document>_…``
    with a numeric collection); None for any other name."""
    parts = Path(str(path)).name.split("_")
    if len(parts) <= 1 or not parts[0].isdigit():
        return None
    return parts[1] or None


@dataclass(frozen=True)
class Cell:
    """The key of one scorer; ``None`` means "any"."""

    lang: Optional[str] = None
    region: Optional[str] = None
    document_type: Optional[str] = None
    century_band: Optional[str] = None

    def key(self) -> str:
        return "|".join(str(v) if v is not None else "*"
                        for v in (self.lang, self.region, self.document_type, self.century_band))

    def backoff_chain(self) -> list["Cell"]:
        """This cell, then the model selector's fallbacks: drop the century
        band, then the genre, then the region, down to the language alone."""
        chain = [self]
        c = self
        for drop in ("century_band", "document_type", "region"):
            if getattr(c, drop) is not None:
                c = Cell(**{**c.__dict__, drop: None})
                chain.append(c)
        return chain

    @classmethod
    def from_criteria(cls, criteria) -> "Cell":
        """From a ``SourceCriteria``-shaped object or dict with ``lang``,
        ``region``, ``document_type`` and ``century``."""
        if isinstance(criteria, dict):
            get = criteria.get
        else:
            def get(k, d=None):
                return getattr(criteria, k, d)
        return cls(lang=get("lang"), region=get("region"), document_type=get("document_type"),
                   century_band=century_band(get("century")))


class ScorerArtefact:
    """One cell's scorer, read from the JSON the training side saved."""

    def __init__(self, data: dict):
        if data.get("kind") != ARTEFACT_KIND:
            raise ValueError(f"not a scorer artefact: kind={data.get('kind')!r}")
        self.cell = Cell(**{k: data["cell"].get(k) for k in ("lang", "region", "document_type",
                                                             "century_band")})
        self.order = int(data["order"])
        self.backoff = float(data["backoff"])
        self._counts: dict[str, Counter] = {ctx: Counter(c) for ctx, c in data["counts"].items()}
        self._totals: dict[str, int] = {ctx: sum(c.values()) for ctx, c in self._counts.items()}
        self._alphabet_size = len(data.get("alphabet", []))
        self.train_chars = int(data.get("train_chars", 0))
        self.documents: set[str] = set(data.get("documents", []))
        self.sources: list[str] = list(data.get("sources", []))
        self.gt_commit: str = data.get("gt_commit", "")
        self.heldout: dict = dict(data.get("heldout", {}))
        if not self._counts:
            raise ValueError(f"cell {self.cell.key()}: the artefact holds no counts")

    @classmethod
    def load(cls, path) -> "ScorerArtefact":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    # ── the formula, as in atr_training.scorer.CharNgramScorer ───────────

    def _prob(self, context: str, ch: str) -> float:
        weight = 1.0
        for k in range(len(context), -1, -1):
            ctx = context[len(context) - k:] if k else ""
            nxt = self._counts.get(ctx)
            if nxt:
                if ch in nxt:
                    return weight * nxt[ch] / self._totals[ctx]
            weight *= self.backoff
        return weight / (self._alphabet_size + 1)

    def bits_per_char(self, text: str, previous: str = "") -> float:
        """Mean bits per character of ``text`` after ``previous`` (the page's
        preceding line), so a line is not judged cold."""
        text = (text or "").strip()
        if not text:
            raise ValueError("nothing to score")
        padded = BOUNDARY + previous + BOUNDARY + text if previous else BOUNDARY + text
        start = len(padded) - len(text)
        bits = 0.0
        for i in range(start, len(padded)):
            context = padded[max(0, i - self.order + 1):i]
            bits -= math.log2(self._prob(context, padded[i]))
        return bits / len(text)

    def z(self, text: str, previous: str = "") -> tuple[float, float]:
        """(z against the cell's own held-out text, bits/char)."""
        if not self.heldout:
            raise ValueError(f"cell {self.cell.key()} is not calibrated — no held-out distribution")
        b = self.bits_per_char(text, previous)
        return (b - float(self.heldout["mean"])) / float(self.heldout["std"]), b

    def refuse_if_contaminated(self, document: Optional[str]) -> None:
        """A scorer that trained on a document must not score it (#23)."""
        if document is not None and document in self.documents:
            raise ValueError(
                f"cell {self.cell.key()}: document {document!r} is in this scorer's training "
                f"set — a perplexity there would describe text it has seen (#23)")


@dataclass
class ScorerMatrix:
    """The cells that exist on this host, and the backoff between them."""

    scorers: dict

    @classmethod
    def load_dir(cls, directory) -> "ScorerMatrix":
        m = cls(scorers={})
        for path in sorted(Path(directory).glob("*.scorer.json")):
            try:
                s = ScorerArtefact.load(path)
            except (ValueError, KeyError, json.JSONDecodeError) as exc:
                logger.warning(f"[scorer] {path.name} skipped: {exc}")
                continue
            m.scorers[s.cell.key()] = s
        return m

    def resolve(self, cell: Cell) -> tuple[Optional[ScorerArtefact], bool]:
        """The scorer for ``cell`` or the nearest fallback; the flag says
        whether it fell back."""
        for i, candidate in enumerate(cell.backoff_chain()):
            s = self.scorers.get(candidate.key())
            if s is not None:
                return s, i > 0
        return None, True


_MATRIX: dict = {}


def _matrix(directory) -> ScorerMatrix:
    key = str(directory)
    if key not in _MATRIX:
        _MATRIX[key] = ScorerMatrix.load_dir(directory) if Path(directory).is_dir() \
            else ScorerMatrix(scorers={})
    return _MATRIX[key]


def veto_scorer(criteria, *, page=None, directory=None, enabled=None) -> Optional[ScorerArtefact]:
    """The scorer the fusion may veto with for a page classed by ``criteria``,
    or None: switch off (``FUSION_SCORER_VETO``), no cell for the criteria nor
    any fallback, or a scorer that trained on the page's document. Never raises;
    a veto that cannot be given is a page fused as before.
    """
    try:
        import config
        if enabled is None:
            enabled = bool(getattr(config, "FUSION_SCORER_VETO", False))
        if directory is None:
            directory = getattr(config, "FUSION_SCORER_DIR", "")
    except Exception:                               # pragma: no cover — defensive
        enabled = bool(enabled)
    if not enabled or not directory:
        return None
    try:
        cell = Cell.from_criteria(criteria or {})
        scorer, fallback = _matrix(directory).resolve(cell)
        if scorer is None:
            logger.info(f"[scorer] no cell for {cell.key()} nor any fallback in {directory}; no veto")
            return None
        scorer.refuse_if_contaminated(document_of(page) if page else None)
        if fallback:
            logger.info(f"[scorer] {cell.key()} answered by {scorer.cell.key()} (fallback)")
        return scorer
    except Exception as exc:
        logger.warning(f"[scorer] no veto for this page: {exc}")
        return None
