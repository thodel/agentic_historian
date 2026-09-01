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
class ErrorProfile:
    """How a model is wrong, not just how much.

    CER alone cannot separate a model that reads badly from one that will not stop
    writing: both raise the number. A VLM asked for a line can produce a paragraph,
    a CTC model cannot — its output is bounded by the input width. The distinction
    is the point of comparing architectures at all (serving-atr#55).
    """
    length_ratio: float = 0.0       # hypothesis chars / reference chars
    over: int = 0                   # lines where the hypothesis is >1.5x the reference
    under: int = 0                  # lines where it is <0.5x
    empty: int = 0                  # lines returning nothing


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
    hyp_chars: int = 0
    texts: dict = field(default_factory=dict)         # image -> hypothesis, for the judge
    profile: ErrorProfile = field(default_factory=ErrorProfile)

    @property
    def cer_mean(self) -> Optional[float]:
        """Mean of the per-line rates — the column Hodel et al. (2021) report.

        Kept alongside the corpus rate because the published numbers for this very
        test set use it, and comparing a corpus CER against a mean CER would be
        comparing different quantities (the trap in serving-atr#80). On a corpus
        with 4-character and 90-character lines the two differ substantially.
        """
        vals = [c for _n, c in self.per_line]
        return (sum(vals) / len(vals)) if vals else None

    @property
    def cer_median(self) -> Optional[float]:
        vals = sorted(c for _n, c in self.per_line)
        return vals[len(vals) // 2] if vals else None

    @property
    def cer_worst(self) -> Optional[float]:
        """The 95th percentile — the paper's "upper bound (worst)" column."""
        vals = sorted(c for _n, c in self.per_line)
        return vals[min(len(vals) - 1, int(0.95 * len(vals)))] if vals else None

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
        s.hyp_chars += len(hyp)
        s.texts[ln.image.name] = hyp
        ratio = len(hyp) / len(ln.gt) if ln.gt else 0.0
        if not hyp.strip():
            s.profile.empty += 1
        elif ratio > 1.5:
            s.profile.over += 1
        elif ratio < 0.5:
            s.profile.under += 1
    s.profile.length_ratio = (s.hyp_chars / s.chars) if s.chars else 0.0
    return s


def format_scores(scores: list[ModelScore]) -> str:
    """A table, best CER first, with the caveats attached rather than assumed."""
    rows = sorted((s for s in scores if s.cer is not None), key=lambda s: s.cer)
    if not rows:
        return "linebench: keine auswertbaren Ergebnisse"
    out = [f"{'model':38} {'CER':>7} {'mean':>7} {'med':>7} {'p95':>7} "
           f"{'Zeilen':>6} {'Ausf':>5} {'s/Z':>6}"]
    for s in rows:
        out.append(f"{s.model[:38]:38} {s.cer:7.1%} {s.cer_mean:7.1%} "
                   f"{s.cer_median:7.1%} {s.cer_worst:7.1%} "
                   f"{s.lines:6} {s.failures:5} {(s.seconds / max(1, s.lines)):6.2f}")
    out.append("")
    out.append("CER = korpusweit (errors/chars). mean/med/p95 = über Zeilen, wie")
    out.append("Hodel et al. 2021 auf DIESEM Testset berichten (HTR+ M2: 3.43/2.76/9.13).")
    out.append("19. Jh. Kurrent — der Korpus dieses Projekts ist 14.-16. Jh.")
    out.append("Absolute Werte sind nicht auf die Zielmaterie übertragbar; die")
    out.append("Rangfolge zwischen den Modellen ist die belastbare Aussage.")
    return "\n".join(out)


# ── LLM as a judge ───────────────────────────────────────────────────────────

_JUDGE_PROMPT = """Du beurteilst Transkriptionen einer historischen Handschrift.

Unten stehen mehrere Lesarten DERSELBEN Zeile, von verschiedenen Systemen erzeugt.
Ordne sie von der besten zur schlechtesten Lesart.

Antworte NUR mit JSON: {"ranking": ["A", "C", "B"]}

Zeile:
"""


def judge_lines(lines: list[Line], scores: list[ModelScore], llm_fn,
                sample: int = 20) -> dict:
    """Ask an LLM to rank the candidate readings, and compare it with real CER.

    This is the experiment's centre and its weakest joint at once. The judge never
    sees the image — only strings — so it cannot check a reading against the page;
    it can only prefer the text that looks most like plausible German. That biases
    it toward MODERNISED spelling and toward fluent invention, which is precisely
    what a historical transcription must not be rewarded for.

    Returned: agreement between the judge's top pick and the CER-best reading, plus
    the cases where they part company — the disagreements are the finding, not the
    agreement rate.
    """
    import json as _json
    import random

    labels = [chr(65 + i) for i in range(len(scores))]
    by_label = dict(zip(labels, scores))
    picked = [ln for ln in lines if all(ln.image.name in s.texts for s in scores)]
    random.Random(4746342).shuffle(picked)
    picked = picked[:sample]

    agree = 0
    judged = 0
    disagreements: list[dict] = []
    for ln in picked:
        # Shuffle which model gets which letter, per line: a judge that always sees
        # the same system as "A" can learn a position preference from nothing.
        order = labels[:]
        random.Random(hash(ln.image.name) & 0xffff).shuffle(order)
        shown = {lab: by_label[lab].texts[ln.image.name] for lab in order}

        best_cer_label = min(
            labels, key=lambda l: cer(ln.gt, by_label[l].texts[ln.image.name],
                                      ignore_case=False, ignore_whitespace=False,
                                      ignore_punctuation=False))

        prompt = _JUDGE_PROMPT + "\n".join(f"{lab}: {shown[lab]!r}" for lab in order)
        try:
            raw = llm_fn(prompt)
            a, b = raw.find("{"), raw.rfind("}")
            ranking = _json.loads(raw[a:b + 1]).get("ranking") if a != -1 else None
            top = ranking[0] if ranking else None
        except Exception as e:
            logger.warning(f"[linebench] judge failed on {ln.image.name}: {e}")
            continue
        if top not in by_label:
            continue
        judged += 1
        if top == best_cer_label:
            agree += 1
        else:
            disagreements.append({
                "line": ln.image.name,
                "gt": ln.gt,
                "judge_picked": by_label[top].model,
                "judge_text": by_label[top].texts[ln.image.name],
                "cer_best": by_label[best_cer_label].model,
                "cer_best_text": by_label[best_cer_label].texts[ln.image.name],
                "judge_cer": round(cer(ln.gt, by_label[top].texts[ln.image.name],
                                       ignore_case=False, ignore_whitespace=False,
                                       ignore_punctuation=False), 3),
                "best_cer": round(cer(ln.gt, by_label[best_cer_label].texts[ln.image.name],
                                      ignore_case=False, ignore_whitespace=False,
                                      ignore_punctuation=False), 3),
            })
    return {"judged": judged, "agreed": agree,
            "rate": (agree / judged) if judged else None,
            "disagreements": disagreements}


def format_profiles(scores: list[ModelScore]) -> str:
    """Error shape per model — what CER cannot separate."""
    rows = [s for s in scores if s.lines]
    if not rows:
        return ""
    out = [f"{'model':40} {'len_ratio':>10} {'über':>6} {'unter':>6} {'leer':>6}"]
    for s in sorted(rows, key=lambda s: s.cer if s.cer is not None else 9):
        p = s.profile
        out.append(f"{s.model[:40]:40} {p.length_ratio:10.2f} {p.over:6} "
                   f"{p.under:6} {p.empty:6}")
    out.append("")
    out.append("len_ratio >1 = das System schreibt mehr als dasteht. Ein CTC-Modell")
    out.append("kann das kaum, ein VLM schon — CER allein trennt beides nicht.")
    return "\n".join(out)
