"""#541 — measure a VLM before it enters the ensemble.

This project has measured exactly one VLM comparison, and it is the reason this
module exists rather than a `--add-model` flag. AH-11, 07.09.2026, Inzigkofen
ground truth, 291 lines over 8 pages:

    qwen3.8-27b     27.7 % CER
    internvl3-8b   189.8 % CER

A factor of nearly seven between two models both carried as "vision-capable".
Putting a second VLM into the ensemble front without that number is a coin with
that spread. And #298 shows the downside is not confined to the bad candidate's
own row: fusion can vote the good reading down, so a worse candidate makes the
*fused* text worse too. More candidates are not monotonically better, and that
is measured in this repository, not assumed.

#540 made the gateway's seven fine-tuned VLMs addressable. This is what turns
"addressable" into "known".

Two phases, deliberately separate
---------------------------------
``run`` needs a GPU and the gateway; ``score`` needs neither. Splitting them
means the expensive half runs once, on the machine that can, and the analysis
can be re-run, reviewed and tested anywhere — including in CI, which is why
every judgement in this module lives in ``score`` and none of it in ``run``.

``run`` writes the project's standard run-dir shape, ``<run>/<model>/<key>.txt``,
so ``compare-runs`` and ``score-gt`` read it with no new loader. A page the
candidate failed on gets a ``.json`` record and **no ``.txt``**: an empty file
would score as 100 % CER and make "the service was down" indistinguishable from
"the model read nothing", which is the same conflation #537 was about one layer
up.

What the report separates, and why each separation is load-bearing
------------------------------------------------------------------
**Line-level from page-level.** Five of the gateway's seven VLMs are
``level: line``; ``qwen3.8-27b`` reads a page. ``/recognize`` does not
auto-segment the way ``/ocr`` does for TrOCR, so handing a whole page to a line
model is a different operation, not a worse reading. One median over both
measures the segmentation and calls it model quality.

**The collapse share from the central tendency.** ``internvl3-8b`` did not read
slightly worse, it collapsed — and that is how ``u-17`` reached publication with
a page of "uuuu". ``docs/EVALUATION_HARNESS.md`` records the same effect from the
other side: two prompts for one model differed by 17 points corpus-wide and by
0.3 in the median, and the gap was two collapsed pages. A mean hides it; a share
does not.

**Coverage from quality.** A candidate that answered on 3 of 8 pages has a
median over 3 pages. Reporting that next to one over 8 without saying so ranks a
model on the pages it happened to survive.

**The fused text from the best single one.** The question is never "is this VLM
good" but "does the fused result improve when it is present". ``eval.harness
.cer_table`` already answers it, and ``#416`` is the open question it feeds.

What this module refuses to do
------------------------------
**It does not rank on a small sample.** Below ``RANKABLE_PAGES`` a candidate's
figures are reported and explicitly marked as not ordering anything. The
Inzigkofen set is 8 pages: enough to see a collapse, not enough to separate two
working models by a few points. #491 reached the same conclusion for perplexity
and put it well — the signal may say **veto**, not **rank** — and ignoring it
here would rebuild the error that issue documents.

**It does not score against a closest reading.** ``cer_table`` refuses that
already (#326/#336); this module only ever passes it hand-corrected text.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

PKG = Path(__file__).resolve().parent.parent
if str(PKG) not in sys.path:                        # `python -m eval.vlm_bench`
    sys.path.insert(0, str(PKG))

from eval.harness import cer_table                                  # noqa: E402
from eval.metrics import cer                                        # noqa: E402

#: Below this many located pages a candidate's numbers are reported but order
#: nothing. Not a tuned threshold: the Inzigkofen set is 8 pages, and the point
#: is that 8 is already at the edge. A number with 3 pages behind it is an
#: anecdote with a decimal point.
RANKABLE_PAGES = 8

#: Levels a candidate can be served at. The gateway reports this per model.
LEVELS = ("page", "line")


# ── What is being measured ───────────────────────────────────────────────────

@dataclass
class Candidate:
    """One VLM to measure, and how to reach it.

    ``backend`` is ``"gpustack"`` or ``"gateway"`` and decides the call, not the
    name: the two differ by one letter in the engine (``vlm`` vs ``vllm``) and by
    everything else in the protocol (#540).
    """
    model_id: str
    backend: str                                    # "gpustack" | "gateway"
    level: str = "page"                             # "page" | "line"
    scripts: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    centuries: list[int] = field(default_factory=list)

    @property
    def label(self) -> str:
        """The run-dir name. The model id, which is what a reader will cite."""
        return self.model_id


@dataclass
class PageReading:
    """What one candidate produced for one page."""
    key: str
    model_id: str
    text: str = ""
    error: str = ""
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return not self.error


def candidates_from_registries(gpustack: dict, gateway: dict) -> list[Candidate]:
    """Every VLM this repository can address, from the two registries.

    ``gpustack`` is ``agent_a.models.VLM_MODELS`` and ``gateway`` is
    ``VLM_GATEWAY_MODELS_LIVE`` (#540). Taking both rather than one is the point:
    the comparison that matters is across backends, and a bench that could only
    see one of them would compare a model to itself.
    """
    out: list[Candidate] = [
        Candidate(model_id=m.model_id, backend="gpustack", level="page")
        for m in gpustack.values()
    ]
    out += [
        Candidate(model_id=m.model_id, backend="gateway",
                  level=m.level if m.level in LEVELS else "page",
                  scripts=list(m.scripts), languages=list(m.languages),
                  centuries=list(m.centuries))
        for m in gateway.values()
    ]
    # Stable order so two runs of the same registries produce the same run dir.
    return sorted(out, key=lambda c: (c.backend, c.model_id))


# ── Phase 1: run (needs the GPU; no judgement lives here) ────────────────────

def transcribe_page(candidate: Candidate, image: Path,
                    *, gpustack_fn: Callable, gateway_fn: Callable) -> PageReading:
    """One candidate, one page. Both backends are injected so this is testable.

    An exception becomes a recorded error rather than an aborted run: measuring
    seven models over eight pages must not be lost because the sixth model is
    not resident on its card.
    """
    started = time.monotonic()
    try:
        if candidate.backend == "gpustack":
            text = gpustack_fn(image, candidate.model_id)
        else:
            text = gateway_fn(image, candidate.model_id)
        text = text or ""
        # Whitespace is not a reading. Recorded as text it becomes a page scored
        # at ~100 % CER, which reads as "the model transcribed badly" when what
        # happened is that it returned nothing — the same conflation the
        # no-``.txt``-on-failure rule exists to prevent.
        error = "" if text.strip() else "empty answer (whitespace only)"
    except Exception as exc:                        # noqa: BLE001 — recorded, not raised
        text, error = "", f"{type(exc).__name__}: {exc}"
    return PageReading(
        key=image.stem, model_id=candidate.model_id, text=text,
        error=error, elapsed_ms=int((time.monotonic() - started) * 1000))


def directory_refusal(model_id: str) -> str:
    """Why *model_id* cannot be a run-directory name, or "" if it can.

    The #521 lesson one repository over: an id goes straight into a path, so it
    is checked at the point it would become one. Here the cost of not checking
    is not a bad public URL but a silently empty run — ``dh-unibe/qwen3vl``
    writes into a nested directory and ``load_run``, which iterates top-level
    directories, returns nothing. The GPU time is spent and the data is gone.

    Not a hypothetical: the gateway reports an ``hf_repo`` beside every model
    (``dh-unibe/qwen3vl-medieval-german-v3``), and passing that where the id
    belongs is one line of a caller away.
    """
    if not model_id or not model_id.strip():
        return "is empty"
    if model_id != model_id.strip():
        return "has leading or trailing whitespace"
    if model_id in (".", ".."):
        return "is a path component, not a name"
    bad = [ch for ch in ("/", "\\", "\0") if ch in model_id]
    if bad:
        return ("contains a path separator, so it would write into a nested "
                "directory that load_run does not look in")
    return ""


def write_reading(run_dir: Path, candidate: Candidate,
                  reading: PageReading) -> Optional[Path]:
    """Persist one reading in the shape ``compare_runs.load_reading`` expects.

    The ``.json`` is always written and the ``.txt`` only on success. A failed
    page therefore has a record saying what happened and contributes no reading
    — rather than an empty one, which the scorer cannot tell from a model that
    produced nothing from a page it did read.
    """
    refusal = directory_refusal(candidate.label)
    if refusal:
        raise ValueError(
            f"model id {candidate.label!r} {refusal}. It is the run directory's "
            "name, so it is refused here rather than producing a run that "
            "looks complete and loads as empty.")
    out = run_dir / candidate.label
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{reading.key}.json").write_text(json.dumps({
        "key": reading.key,
        "model_id": reading.model_id,
        "backend": candidate.backend,
        "level": candidate.level,
        "error": reading.error,
        "elapsed_ms": reading.elapsed_ms,
        "chars": len(reading.text),
    }, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if not reading.ok:
        return None
    path = out / f"{reading.key}.txt"
    path.write_text(reading.text, encoding="utf-8")
    return path


def run(candidates: Iterable[Candidate], images: Iterable[Path], run_dir: Path,
        *, gpustack_fn: Callable, gateway_fn: Callable,
        on_page: Optional[Callable] = None) -> list[PageReading]:
    """Transcribe every page with every candidate into ``run_dir``.

    Model-major, not page-major, and that is not a style choice: the gateway's
    vLLM models are lazy on one GPU, so interleaving them pays a model load per
    page. The ATR server's own guidance says to iterate model-major for exactly
    this reason.
    """
    images = [Path(p) for p in images]
    readings: list[PageReading] = []
    for candidate in candidates:
        for image in images:
            reading = transcribe_page(candidate, image,
                                      gpustack_fn=gpustack_fn,
                                      gateway_fn=gateway_fn)
            write_reading(run_dir, candidate, reading)
            readings.append(reading)
            if on_page:
                on_page(candidate, reading)
    return readings


# ── Phase 2: score (pure; every judgement is here) ───────────────────────────

@dataclass
class CandidateScore:
    """One candidate's result, with everything needed to distrust it."""
    model_id: str
    level: str
    attempted: int = 0                 # pages it was asked for
    answered: int = 0                  # pages it produced text for
    scored: int = 0                    # pages that were also located in the GT
    median_cer: float = 0.0
    p90_cer: float = 0.0
    best_cer: float = 0.0
    worst_cer: float = 0.0
    collapsed: int = 0                 # answered pages whose text is degenerate

    @property
    def coverage(self) -> float:
        return self.answered / self.attempted if self.attempted else 0.0

    @property
    def collapse_share(self) -> float:
        return self.collapsed / self.answered if self.answered else 0.0

    @property
    def rankable(self) -> bool:
        """Whether these figures may be used to order this candidate at all."""
        return self.scored >= RANKABLE_PAGES


@dataclass
class FusionEffect:
    """Whether fusing the candidates beat the best single one, and how often."""
    pages: int = 0
    fusion_wins: int = 0
    median_fused_cer: float = 0.0
    median_best_single_cer: float = 0.0

    @property
    def win_share(self) -> float:
        return self.fusion_wins / self.pages if self.pages else 0.0


def is_degenerate(text: str) -> bool:
    """The project's own collapse test, not a second one written here.

    Imported from ``agents.text_recognition`` rather than reimplemented: a bench
    with its own definition of "collapsed" would drift from the pipeline's, and
    a second hand-kept copy of a rule is exactly what #538 was about.
    """
    from agents.text_recognition import _is_degenerate

    return _is_degenerate(text)


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile. Mirrors ``gt_score._quantile``."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def load_run(run_dir: Path) -> tuple[dict[str, dict[str, str]], dict[str, dict]]:
    """``({model: {key: text}}, {model: meta})`` from a run directory.

    ``meta`` carries the level and the attempted/answered counts, which the
    ``.txt`` files alone cannot give: a page the candidate failed on has a
    ``.json`` and no ``.txt``, and that difference is the coverage number.
    """
    readings: dict[str, dict[str, str]] = {}
    meta: dict[str, dict] = {}
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise NotADirectoryError(f"not a run directory: {run_dir}")

    for model_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        model = model_dir.name
        pages = {p.stem: p.read_text(encoding="utf-8")
                 for p in sorted(model_dir.glob("*.txt"))}
        records = []
        for rec in sorted(model_dir.glob("*.json")):
            try:
                records.append(json.loads(rec.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        if not pages and not records:
            continue
        levels = {r.get("level") for r in records if r.get("level")}
        readings[model] = pages
        meta[model] = {
            # A model whose records disagree about its level is reported as
            # mixed rather than silently assigned one: the level decides which
            # table it belongs in, and guessing puts it in the wrong one.
            "level": levels.pop() if len(levels) == 1 else ("mixed" if levels else "page"),
            "attempted": len(records) or len(pages),
            "answered": len(pages),
            "errors": [r.get("error") for r in records if r.get("error")],
        }
    return readings, meta


def score_candidates(readings: dict[str, dict[str, str]], meta: dict[str, dict],
                     references: dict[str, str]) -> list[CandidateScore]:
    """CER per candidate against ``references`` (page key → hand-corrected text).

    Only pages present in ``references`` are scored, and ``scored`` says how
    many that was. A candidate is never credited for a page nobody can check.
    """
    out: list[CandidateScore] = []
    for model, pages in readings.items():
        info = meta.get(model, {})
        cers = [cer(references[key], text)
                for key, text in pages.items() if key in references]
        collapsed = sum(1 for text in pages.values() if is_degenerate(text))
        out.append(CandidateScore(
            model_id=model,
            level=str(info.get("level", "page")),
            attempted=int(info.get("attempted", len(pages))),
            answered=int(info.get("answered", len(pages))),
            scored=len(cers),
            median_cer=_quantile(cers, 0.5),
            p90_cer=_quantile(cers, 0.9),
            best_cer=min(cers) if cers else 0.0,
            worst_cer=max(cers) if cers else 0.0,
            collapsed=collapsed,
        ))
    # Rankable candidates first, then the rest, then by median. The ⚠ in the
    # report says a candidate's figures order nothing; a sort that puts a
    # one-page 0 % above an eight-page 1 % contradicts it, and a reader takes
    # the order of a table as the ranking whatever the footnote says.
    #
    # Inside the unrankable group nothing orders anything either, so the
    # tie-break is the number of pages rather than the median: between two
    # figures that both mean little, the one resting on more of them is the
    # less misleading to read first.
    return sorted(out, key=lambda s: (
        s.level,
        not s.rankable,
        0 if s.rankable else -s.scored,
        s.median_cer if s.scored else 9.9,
    ))


def fusion_effect(readings: dict[str, dict[str, str]], references: dict[str, str],
                  fuse_fn: Optional[Callable] = None) -> FusionEffect:
    """Does the fused text beat the best single candidate, page by page?

    The question #416 asks, restricted to the candidates on the bench. Fusion
    runs with arbitration off so the answer is deterministic and makes no LLM
    call — a bench that needed a model to score a model would not be reusable
    in CI, and the one measurement there is (#406) found arbitration a coin
    flip anyway.

    Pages where fewer than two candidates answered are skipped: there is
    nothing to fuse, and counting them would dilute the share with cases the
    question does not apply to.
    """
    if fuse_fn is None:
        def fuse_fn(texts: dict[str, str]) -> str:
            from fusion import fuse

            recognitions = [{"engine": name, "model_id": name, "text": text}
                            for name, text in texts.items()]
            return fuse(recognitions, arbitrate=False).text

    effect = FusionEffect()
    fused_cers: list[float] = []
    best_cers: list[float] = []

    for key, reference in sorted(references.items()):
        texts = {model: pages[key] for model, pages in readings.items()
                 if key in pages and pages[key].strip()}
        if len(texts) < 2:
            continue
        table = cer_table(texts, fuse_fn(texts), reference)
        effect.pages += 1
        if table["fusion_beats_best"]:
            effect.fusion_wins += 1
        fused_cers.append(table["fused"]["cer"])
        best_cers.append(table["best"]["cer"])

    effect.median_fused_cer = _quantile(fused_cers, 0.5)
    effect.median_best_single_cer = _quantile(best_cers, 0.5)
    return effect


# ── The report ───────────────────────────────────────────────────────────────

def _candidate_table(scores: list[CandidateScore]) -> list[str]:
    rows = ["| model | pages scored | median CER | p90 | best | worst | "
            "collapsed | coverage |",
            "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s in scores:
        if not s.scored:
            rows.append(
                f"| `{s.model_id}` | 0 | — | — | — | — | "
                f"{s.collapsed}/{s.answered} | {s.coverage:.0%} |")
            continue
        mark = "" if s.rankable else " ⚠"
        rows.append(
            f"| `{s.model_id}`{mark} | {s.scored} | {s.median_cer:.1%} | "
            f"{s.p90_cer:.1%} | {s.best_cer:.1%} | {s.worst_cer:.1%} | "
            f"{s.collapsed}/{s.answered} | {s.coverage:.0%} |")
    return rows


def format_report(scores: list[CandidateScore], effect: FusionEffect) -> str:
    """The report, written so a reader cannot take more from it than it holds."""
    lines = ["# VLM-Bench (#541)", ""]

    if not scores:
        return "\n".join(lines + [
            "No candidate produced a reading. Nothing is measured here — this "
            "is not a result about the models.", ""])

    by_level: dict[str, list[CandidateScore]] = {}
    for s in scores:
        by_level.setdefault(s.level, []).append(s)

    for level in sorted(by_level):
        lines += [f"## Level: {level}", ""]
        lines += _candidate_table(by_level[level])
        lines.append("")

    if len(by_level) > 1:
        lines += [
            "**The level tables are not comparable with each other.** A "
            "line-level model handed a whole page is doing a different "
            "operation from a page-level one, and `/recognize` does not "
            "auto-segment. Reading a line model's median against a page "
            "model's measures the segmentation.", ""]

    unrankable = [s for s in scores if s.scored and not s.rankable]
    if unrankable:
        lines += [
            f"⚠ marks a candidate scored on fewer than {RANKABLE_PAGES} located "
            "pages. Its figures are printed, and they order nothing.", ""]

    collapsed = [s for s in scores if s.collapsed]
    if collapsed:
        lines += ["### Collapse", "",
                  "A collapse is not a worse reading, it is a different "
                  "failure, and a median hides it — two collapsed pages moved "
                  "one corpus-wide rate by 17 points and the median by 0.3 "
                  "(`docs/EVALUATION_HARNESS.md`). This is how `u-17` reached "
                  "the site as a page of \"uuuu\".", ""]
        for s in collapsed:
            lines.append(f"- `{s.model_id}`: {s.collapsed} of {s.answered} "
                         f"answered pages ({s.collapse_share:.0%})")
        lines.append("")

    lines += ["## Does fusion beat the best single candidate?", ""]
    if not effect.pages:
        lines += ["Not measurable: no page had two candidates to fuse.", ""]
    else:
        lines += [
            f"- pages with ≥2 candidates: **{effect.pages}**",
            f"- fusion beat the best single candidate on **{effect.fusion_wins}** "
            f"of them ({effect.win_share:.0%})",
            f"- median fused CER **{effect.median_fused_cer:.1%}** against "
            f"median best-single **{effect.median_best_single_cer:.1%}**",
            "",
            "This is #416's question on this bench's candidates. A candidate "
            "that reads well alone and makes the fused text worse has not "
            "earned a place in the ensemble: #298 measured the fusion voting "
            "the good reading down.", ""]

    lines += [
        "## What this does not say", "",
        "**It does not rank two working models.** The reference set here is "
        "small — the Inzigkofen comparison that motivated this is 291 lines "
        "over 8 pages — and a few points between two plausible candidates is "
        "inside the noise. What a set this size *can* do is rule out the "
        "collapse, which is the case that matters: 27.7 % against 189.8 % is "
        "not a close call. The signal says veto, not rank — the same limit "
        "#491 found for perplexity, where treating a veto as a ranking "
        "certified the step doing the damage.",
        "",
        "**Coverage is not quality.** A candidate that answered on half the "
        "pages has a median over the half it survived.",
        "",
        "**CER here is against hand-corrected text**, so unlike every other "
        "comparison in this project it really is accuracy. It is never scored "
        "against a closest reading, which is an editorial selection of our own "
        "output (#326/#336).", ""]
    return "\n".join(lines)


# ── Backends (the only part that touches a network) ──────────────────────────

def gpustack_backend(image: Path, model_id: str) -> str:
    """Transcribe through GPUStack, naming the model (#537 made that possible)."""
    from agent_a.dual_pipeline import _run_vlm

    text, _score, ran = _run_vlm(Path(image), model=model_id)
    if ran != model_id:                             # pragma: no cover — defensive
        raise RuntimeError(
            f"asked for {model_id!r} and the call reports {ran!r}; refusing to "
            "record a reading under a name that did not produce it (#537)")
    if not text:
        raise RuntimeError(f"{model_id} returned no text")
    return text


def gateway_backend(image: Path, model_id: str) -> str:
    """Transcribe through the ATR gateway's ``/recognize`` (#540).

    ``engine="vllm"`` rather than ``"vlm"``: one letter, and it decides between
    the gateway and GPUStack. ``read()`` routes on it.
    """
    from agent_a.kraken_client import KrakenHTTPClient

    with KrakenHTTPClient() as client:
        result = client.read(Path(image), model=model_id, engine="vllm")
    if not result.text:
        raise RuntimeError(f"{model_id} returned no text")
    return result.text


def live_candidates() -> list[Candidate]:
    """Ask both registries what there is to measure.

    Refreshing the gateway registry here rather than trusting a warm one: a
    bench that silently measured yesterday's model list would answer a question
    nobody asked.
    """
    from agent_a import models
    from agent_a.kraken_client import KrakenHTTPClient

    try:
        with KrakenHTTPClient() as client:
            gateway = models.refresh_vlm_registry(client)
    except Exception as exc:                        # noqa: BLE001 — reported, not fatal
        print(f"[vlm-bench] gateway registry unavailable, GPUStack only: {exc}",
              file=sys.stderr)
        gateway = {}
    return candidates_from_registries(models.VLM_MODELS, gateway)


#: A reading needs at least this many usable pages before its match counts as
#: evidence of which page a ground-truth file is. With one page the answer is
#: forced — that page matches every ground-truth file, because it is the only
#: option — and ``gt_score.Match.confident`` calls it confident, correctly,
#: because there is no runner-up to be worse.
LOCATING_MIN_PAGES = 2


def locating_readings(readings: dict[str, dict[str, str]],
                      ) -> dict[str, dict[str, str]]:
    """The readings whose opinion about *which page this is* is worth having.

    Dropped: empty and collapsed pages, and then any reading left with fewer
    than ``LOCATING_MIN_PAGES``.

    This exists because a bench is not a corpus run. ``gt_score`` decides a page
    is located when the readings agree, which is right when every reading is a
    plausible attempt at every page. A bench deliberately holds candidates that
    collapse and candidates that answered twice out of twenty, and both vote the
    same wrong way: a collapsed reading is the same text on every page, so the
    first key matches every ground-truth file; a one-page reading has only one
    answer to give. On the first real run of this bench the two of them between
    them cost 2 of 3 pages — for every candidate, including the good ones.

    Excluded from locating, still scored. That separation is the point: the
    collapse is the finding.
    """
    out: dict[str, dict[str, str]] = {}
    for model, pages in readings.items():
        usable = {key: text for key, text in pages.items()
                  if text.strip() and not is_degenerate(text)}
        if len(usable) >= LOCATING_MIN_PAGES:
            out[model] = usable
    return out


def references_from_ground_truth(gt_paths: Iterable[str],
                                 readings: dict[str, dict[str, str]],
                                 ) -> tuple[dict[str, str], list[str]]:
    """``{page key: hand-corrected text}``, located through ``gt_score``.

    Reuses the content matching rather than assuming a filename convention:
    Transkribus ground truth carries Transkribus identities and shares no key
    with our pages, so the page is found by content and every match reports its
    runner-up. A page whose identity is unsettled is **left out** and named in
    the second return value — scoring it would measure the distance to whichever
    page happened to be least unlike it.

    Only ``locating_readings`` vote; see there for why the candidates under test
    cannot all be trusted with that question.
    """
    import gt_score

    files = gt_score.expand_gt_paths(gt_paths)
    voters = locating_readings(readings)
    unusable: list = []
    if not voters:
        # Every candidate collapsed or answered too little. There is no page
        # here, and inventing one would score each reading against whichever
        # ground-truth file it happened to be least unlike.
        return {}, [Path(f).name for f in files]

    scored = gt_score.score(files, voters, unusable=unusable)

    references: dict[str, str] = {}
    unlocated: list[str] = []
    for item in scored:
        key = item.located
        if key:
            references[key] = item.gt.text
        else:
            unlocated.append(item.gt.source.name)
    return references, unlocated + [u.source.name for u in unusable]


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="vlm-bench",
        description="Measure a VLM against ground truth before it joins the "
                    "ensemble (#541).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="transcribe pages with every candidate "
                                       "(needs the GPU and the gateway)")
    p_run.add_argument("--pages", nargs="+", required=True,
                       help="image files, or directories of them")
    p_run.add_argument("--out", type=Path, required=True, help="run directory")
    p_run.add_argument("--models", default="",
                       help="comma-separated model ids; default is every "
                            "candidate both registries report")

    p_score = sub.add_parser("score", help="score a run directory (offline)")
    p_score.add_argument("--run", type=Path, required=True)
    p_score.add_argument("--gt", nargs="+", required=True,
                         help="PageXML files or directories of them")
    p_score.add_argument("--out", type=Path, default=None,
                         help="write the report here instead of stdout")

    args = parser.parse_args(argv)

    if args.cmd == "run":
        images: list[Path] = []
        for raw in args.pages:
            p = Path(raw).expanduser()
            if p.is_dir():
                images.extend(sorted(
                    q for q in p.rglob("*")
                    if q.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff")))
            elif p.exists():
                images.append(p)
            else:
                print(f"[vlm-bench] no such page: {p}", file=sys.stderr)
                return 2
        if not images:
            print("[vlm-bench] no page images found", file=sys.stderr)
            return 2

        candidates = live_candidates()
        if args.models:
            wanted = {m.strip() for m in args.models.split(",") if m.strip()}
            missing = wanted - {c.model_id for c in candidates}
            if missing:
                print(f"[vlm-bench] not served: {', '.join(sorted(missing))}",
                      file=sys.stderr)
                return 2
            candidates = [c for c in candidates if c.model_id in wanted]
        if not candidates:
            print("[vlm-bench] no candidates to measure", file=sys.stderr)
            return 2

        print(f"[vlm-bench] {len(candidates)} candidate(s) × {len(images)} page(s) "
              f"→ {args.out}", file=sys.stderr)

        def _progress(candidate: Candidate, reading: PageReading) -> None:
            state = f"{len(reading.text)} chars" if reading.ok else reading.error
            print(f"  {candidate.model_id:40s} {reading.key:24s} {state}",
                  file=sys.stderr)

        run(candidates, images, args.out,
            gpustack_fn=gpustack_backend, gateway_fn=gateway_backend,
            on_page=_progress)
        print(f"[vlm-bench] written. Score it with:\n"
              f"  python -m eval.vlm_bench score --run {args.out} --gt <pagexml>",
              file=sys.stderr)
        return 0

    try:
        readings, meta = load_run(args.run)
    except NotADirectoryError as exc:
        # A typo in a path is the most likely way to reach this, and a
        # traceback answers it worse than a sentence does.
        print(f"[vlm-bench] {exc}", file=sys.stderr)
        return 2
    if not readings:
        print(f"[vlm-bench] no model output under {args.run}", file=sys.stderr)
        return 2

    references, skipped = references_from_ground_truth(args.gt, readings)
    if not references:
        print("[vlm-bench] no ground-truth page could be located in this run — "
              "scoring it would compare each page to whichever one was least "
              "unlike it", file=sys.stderr)
        return 2
    if skipped:
        print(f"[vlm-bench] {len(skipped)} ground-truth file(s) left out: "
              f"{', '.join(skipped[:5])}", file=sys.stderr)

    report = format_report(score_candidates(readings, meta, references),
                           fusion_effect(readings, references))
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"[vlm-bench] {args.out}", file=sys.stderr)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
