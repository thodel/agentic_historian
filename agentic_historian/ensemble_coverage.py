"""How many of a document's planned readings it was actually made from.

The run this exists for: `missiven`, published 2026-10-09. From its
`pipeline.json`:

    20 recognitions: 4 with text, 16 with an error
      vlm 4/4 · kraken 0/10 · trocr 0/6
    "errors": []
    "a_meta": {"pages": 2, "qa_score": 0.0, "source": "grouped-ensemble-criteria"}

**`errors` is empty.** Sixteen of twenty readings failed against a kraken service
that was not running, the ensemble ran VLM-only, there was nothing to fuse, and
the publication went through. The catalogue page looks like any other result. The
only trace is `qa_score: 0.0`, a number nobody reads.

A page made from 4 of 20 readings is indistinguishable from one made from 20 of
20. That is the same shape as #482, #483, #406 and #546 — a value that claims
more than was measured — here by silence rather than by a wrong number. For a
tool whose whole purpose is quality without ground truth (#326) it is the most
expensive kind of quiet data loss: the numbers go on looking comparable after
they have stopped being comparable.

**What this module does not do:** decide whether the engines are up, or restart
them. That is #599, which gates a *run*. This one is about the product saying
what it is made of — including when nobody noticed the failure, and including for
documents produced before anything watched the engines at all.

Three distinctions the counting keeps, because each has already cost this project
something:

**Failed is not empty (#483).** A reading that came back with an error told us
nothing. A reading that came back with no text told us the engine found nothing
there — the address side of `StadtASG_Missive_49_98` is sparse by nature. Both
reduce what a transcription was made from; only one is a malfunction, and a
notice that calls the second one a failure teaches the reader to ignore it.

**Unmeasured is not zero.** A document with no recognitions at all — the VLM-only
path with `ENABLE_ENSEMBLE_HTR` off — has *nothing measured*, not nothing
answered. Its coverage is ``None``, it is never marked, and it is never refused.
The alternative would retroactively condemn every document produced before the
ensemble existed.

**Published is not unmarked.** The decision (2026-10-10) is to publish a partial
document and **mark** it, rather than withhold it: a VLM reading is better than
nothing and the run was expensive. Only a document where readings were attempted
and *none* produced anything, with no transcription to show either, is refused —
there is no product there to mark.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

__all__ = ["Coverage", "measure", "failures", "annotate", "notice",
           "may_publish", "from_meta", "META_KEYS"]

#: The keys :func:`annotate` writes into ``a_meta``. Named so a reader of
#: ``pipeline.json`` can tell this module's fields from the ones that were there
#: before, and so a test can assert the set rather than four strings.
META_KEYS = ("engines_planned", "engines_answered", "engines_failed",
             "engines_with_text", "coverage")


def _each(recognitions: Any):
    """The readings, or nothing at all when the value is not a list of them.

    A string is iterable, and ``measure("not a list")`` counted its eleven
    characters as eleven planned readings — found by the malformed-record test,
    which is the whole reason this module is supposed to be tolerant rather than
    trusting. Tolerant means "yields nothing", not "yields whatever iterates".
    """
    if recognitions is None or isinstance(recognitions, (str, bytes, dict)):
        return ()
    try:
        return tuple(recognitions)
    except TypeError:
        return ()


def _parts(rec: Any) -> tuple[str, str]:
    """``(text, error)`` from a RecognitionResult or a plain dict.

    Both shapes occur for real: the objects live in ``ctx.recognitions``, the
    dicts in ``pipeline.json``, and this module is read from both sides.
    """
    if isinstance(rec, dict):
        return (rec.get("text") or ""), (rec.get("error") or "")
    return (getattr(rec, "text", "") or ""), (getattr(rec, "error", "") or "")


def _field(rec: Any, name: str, default: str = "") -> str:
    if isinstance(rec, dict):
        return str(rec.get(name) or default)
    return str(getattr(rec, name, default) or default)


@dataclass(frozen=True)
class Coverage:
    """What was attempted, and what came back.

    Four counts rather than one ratio, because a ratio cannot be acted on: a
    document at 0.2 because the kraken service is down needs a different thing
    done about it than one at 0.2 because four engines read a blank page.
    """

    #: Readings attempted. The honest word is "planned": this is what the run
    #: meant to read with, which is the number a coverage figure must be against.
    planned: int = 0
    #: Came back without an error — whether or not it found text.
    answered: int = 0
    #: Came back with an error. Told us nothing.
    failed: int = 0
    #: Came back with text. ``answered - with_text`` read the page and found
    #: nothing there, which is a reading, not a malfunction (#483).
    with_text: int = 0

    @property
    def ratio(self) -> Optional[float]:
        """``answered / planned``, or ``None`` when nothing was attempted.

        ``None``, never ``0.0``: "no reading was attempted" and "every reading
        failed" are different facts, and a document from before the ensemble
        existed must not read as a total failure.
        """
        if self.planned <= 0:
            return None
        return round(self.answered / self.planned, 3)

    @property
    def measured(self) -> bool:
        """Whether anything was attempted at all."""
        return self.planned > 0

    @property
    def marked(self) -> bool:
        """Whether the published page must carry a notice.

        True when readings were attempted and not all of them produced text —
        whether because they failed or because they found nothing. Both reduce
        what the transcription was made from, and the notice says which.

        ``measured`` needs no separate clause: ``with_text < planned`` can only
        hold when ``planned >= 1``. It was written with one, and no mutation
        could make that clause matter — which is how a redundant guard comes to
        look load-bearing.
        """
        return self.with_text < self.planned

    @property
    def usable(self) -> bool:
        """Whether anything came back that a transcription could be made from."""
        return self.with_text > 0

    def to_meta(self) -> dict:
        """The ``a_meta`` fields. ``coverage`` may be ``None``; that is the point."""
        return {
            "engines_planned": self.planned,
            "engines_answered": self.answered,
            "engines_failed": self.failed,
            "engines_with_text": self.with_text,
            "coverage": self.ratio,
        }


def measure(recognitions: Iterable[Any] | None) -> Coverage:
    """Count one document's readings."""
    planned = answered = failed = with_text = 0
    for rec in _each(recognitions):
        text, error = _parts(rec)
        planned += 1
        if error:
            failed += 1
            continue
        answered += 1
        if text.strip():
            with_text += 1
    return Coverage(planned=planned, answered=answered, failed=failed,
                    with_text=with_text)


def failures(recognitions: Iterable[Any] | None) -> list[dict]:
    """One entry per failed reading, for the document's ``errors``.

    A failed engine reading is an error **of the document**, not merely a field
    in a list entry nobody expands. Sixteen of them were invisible in the
    missiven run because they lived only in ``recognitions[i].error`` while
    ``errors`` said ``[]``.

    The error text is truncated: sixteen copies of the same gateway JSON body
    turn ``pipeline.json`` into something nobody reads, which is how the
    information got lost in the first place.
    """
    out: list[dict] = []
    for rec in _each(recognitions):
        _text, error = _parts(rec)
        if not error:
            continue
        out.append({
            "agent": "A",
            "phase": "recognition",
            "page": _field(rec, "page", "—"),
            "engine": _field(rec, "engine", "?"),
            "model_id": _field(rec, "model_id", "—"),
            "error": error[:300],
        })
    return out


def annotate(pipeline: dict) -> dict:
    """Fill a pipeline record's coverage fields and its document-level errors.

    Takes and returns the whole ``pipeline.json`` dict, because that is the one
    thing **both** write paths have in hand: the normal path builds the record
    from the RunState and the fallback from ``ctx.to_json()``, so anything
    derived inside either one would be missing from the other — and the missiven
    run went through the first.

    Idempotent: re-annotating an already-annotated record changes nothing, so
    ingest's re-export (#225) does not accumulate duplicate error entries.
    """
    if not isinstance(pipeline, dict):
        return pipeline
    recognitions = pipeline.get("recognitions") or []
    cov = measure(recognitions)

    a_meta = dict(pipeline.get("a_meta") or {})
    a_meta.update(cov.to_meta())
    pipeline["a_meta"] = a_meta

    known = pipeline.get("errors") or []
    if not isinstance(known, list):
        known = []
    fresh = [e for e in failures(recognitions) if e not in known]
    pipeline["errors"] = list(known) + fresh
    return pipeline


def from_meta(a_meta: Any) -> Coverage:
    """Read a :class:`Coverage` back out of an ``a_meta`` block.

    For the publishing side, which has ``pipeline.json`` and not the objects.
    A record without these keys yields an unmeasured Coverage — which is right:
    documents published before this existed said nothing about their coverage,
    and they still say nothing rather than claiming zero.
    """
    if not isinstance(a_meta, dict):
        return Coverage()

    def _int(key: str) -> int:
        value = a_meta.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    return Coverage(planned=_int("engines_planned"), answered=_int("engines_answered"),
                    failed=_int("engines_failed"), with_text=_int("engines_with_text"))


def notice(cov: Coverage) -> str:
    """The marking, in words — the action, not the status code (#369).

    Empty when there is nothing to mark, so a caller can write
    ``if text: lines.append(text)`` and a complete document carries nothing.
    """
    if not cov.marked:
        return ""
    bits = [f"Diese Transkription entstand aus **{cov.with_text} von "
            f"{cov.planned} geplanten Lesungen**."]
    if cov.failed:
        bits.append(f"{cov.failed} Lesung(en) schlugen fehl — die Engine "
                    f"antwortete nicht oder lehnte ab, es wurde also nichts "
                    f"gelesen.")
    silent = cov.answered - cov.with_text
    if silent:
        bits.append(f"{silent} Lesung(en) kamen ohne Text zurück: die Engine "
                    f"hat die Seite gelesen und nichts gefunden — das ist eine "
                    f"Lesung, keine Störung (#483).")
    if cov.with_text < 2:
        bits.append("Mit weniger als zwei Lesungen mit Text gibt es nichts zu "
                    "vergleichen: ein Übereinstimmungsmass ist für diese Seite "
                    "nicht messbar, und ein QA-Wert dazu wäre aus Abwesenheit "
                    "gemacht (#367).")
    bits.append("Ein Vergleich mit einer vollständig gelesenen Seite ist "
                "deshalb kein Vergleich zweier Ensembles.")
    return " ".join(bits)


def may_publish(pipeline: dict) -> tuple[bool, str]:
    """Whether this document may go into the catalogue, and why not.

    The decision (2026-10-10): **publish, but marked**. A partial reading is
    worth having — a VLM transcription is better than nothing and the run cost
    real time — as long as the page says what it is made of.

    Refused in exactly one case: readings were attempted, none of them produced
    anything, and there is no transcription to show either. There is no product
    there to mark, and a catalogue entry for it would be an entry about a
    failure, not about a document. A document with nothing measured is never
    refused — that is the whole of the VLM-only path and everything produced
    before the ensemble existed.
    """
    if not isinstance(pipeline, dict):
        return True, ""
    cov = measure(pipeline.get("recognitions") or [])
    if not cov.measured or cov.usable:
        return True, ""
    if (pipeline.get("transcription") or "").strip():
        # Something readable exists from another source or an earlier run.
        # Refusing here would throw away work to make a point.
        return True, ""
    return False, (
        f"keine der {cov.planned} geplanten Lesungen hat etwas ergeben "
        f"({cov.failed} fehlgeschlagen, {cov.answered - cov.with_text} ohne "
        f"Text) und es gibt keine Transkription — es gibt nichts zu "
        f"publizieren, was man markieren könnte. Ursache beheben und erneut "
        f"laufen lassen (#595)."
    )
