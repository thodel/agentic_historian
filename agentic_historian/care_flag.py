"""care_flag.py — "nein" und "nicht geprüft" sind zwei Antworten (#631).

Agent B classifies every document as care-related or not. Its failure path
returned the *same* dict as a real negative::

    default = {"is_care_related": False, "care_context": "", ...}
    try:    ... ask the model ...
    except: return dict(default)          # ← indistinguishable

So a stored `False` meant either "the model looked and said no" or "the call
never came back" — a timeout, the token ceiling, no JSON in the reasoning
output. The project has paid for this shape before (`Progress` three-valued in
#119, `cer`/`timing_ms` in #595): **a value that claims more than was
measured.** Here it claimed a judgement about a welfare record that nobody made.

What it cost, concretely:

* `corpus_build`'s narrower scope (``KG_PUBLISH_CARE_FLAGGED=false``) treated an
  unclassified document as unsuspicious and published it — the opposite of what
  that setting is for.
* KG-5's graph could only ever assert the positive (#331), so no query could say
  how many documents had actually been checked. "3 of 20 are care-related" and
  "3 of 20, with 6 classifications that failed" are different findings.
* The description markdown wrote "**Care-relevant:** Nein" either way.

**Three values, and no migration needed.** New records carry ``classified``;
records written before this module do not. That key is the version marker, so a
vintage record is read by the asymmetry that was always true of it: ``True`` was
only ever set by a successful classification and stays ``True``; ``False`` could
be either and therefore reads as **unknown**. Nothing has to be re-run and no
cutoff date has to be guessed — a re-run of Agent B is an *improvement* that
turns unknowns into real negatives, not a prerequisite for reading the corpus.

Everything here is pure, and every consumer goes through it, so the vintage rule
exists once instead of in three modules that can drift apart.

Run: pytest agentic_historian/tests/test_ah_631_care_flag.py -v
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

__all__ = ["YES", "NO", "UNKNOWN", "CareFlag", "read", "classified",
           "unclassified", "is_care_flagged", "needs_classification",
           "VINTAGE_REASON", "count_states"]

#: The three answers. ``UNKNOWN`` is not a shade of ``NO``: one says the model
#: looked, the other says nobody did.
YES, NO, UNKNOWN = "yes", "no", "unknown"

#: Why a pre-#631 record reads as unknown rather than as the ``False`` it holds.
VINTAGE_REASON = ("vor #631 geschrieben — ein gespeichertes Nein ist dort "
                  "nicht von einem Fehlschlag zu unterscheiden")


@dataclass(frozen=True)
class CareFlag:
    """One document's care classification, however old the record."""

    state: str
    context: str = ""
    types: tuple[str, ...] = ()
    #: Names the model read out of the care context. Kept because the record has
    #: them, never published: they are not the NER output the graph's person
    #: nodes come from, so publishing them would be an invention (#331).
    participants: tuple[str, ...] = ()
    #: Why it is unknown — the error, or the pipeline path that skipped it.
    #: Empty for a measured flag.
    reason: str = ""
    #: True when the record predates the three-valued field.
    vintage: bool = False

    @property
    def measured(self) -> bool:
        """Whether anybody actually classified this document."""
        return self.state in (YES, NO)

    @property
    def is_care_related(self) -> Optional[bool]:
        """``True`` / ``False`` / **None** — the three-valued answer."""
        if self.state == YES:
            return True
        if self.state == NO:
            return False
        return None

    def line(self) -> str:
        """The one-line answer, for a description or a message.

        Names the state, and for an unknown the reason, because "ungeprüft"
        without a why reads as a bug rather than as a fact about the run.
        """
        if self.state == YES:
            context = f" — {self.context}" if self.context else ""
            return f"Ja{context}"
        if self.state == NO:
            return "Nein"
        return f"ungeprüft ({self.reason})" if self.reason else "ungeprüft"


def _flag_dict(source: Any) -> Optional[dict]:
    """The ``care_flag`` dict, from a pipeline record, a description, or itself.

    Three shapes because three callers have three things in hand: the exporter
    has the whole pipeline record, Agent B has its own result, and a test has
    the flag. Guessing wrong returns None, which reads as unknown — never as a
    negative.
    """
    if not isinstance(source, dict):
        return None
    if isinstance(source.get("care_flag"), dict):
        return source["care_flag"]
    description = source.get("description")
    if isinstance(description, dict) and isinstance(description.get("care_flag"), dict):
        return description["care_flag"]
    if "is_care_related" in source or "classified" in source:
        return source
    return None


def read(source: Any) -> CareFlag:
    """Read any vintage of record into a :class:`CareFlag`. Never raises."""
    flag = _flag_dict(source)
    if flag is None:
        # No flag at all: an older pipeline record, or a path that never ran the
        # classification. Not a negative.
        return CareFlag(state=UNKNOWN, reason="kein care_flag im Record")

    context = str(flag.get("care_context") or "")
    types = tuple(str(t) for t in (flag.get("care_types") or []) if str(t).strip())
    participants = tuple(str(b) for b in (flag.get("beteiligte") or []) if str(b).strip())
    reason = str(flag.get("error") or "")
    value = flag.get("is_care_related")

    if "classified" not in flag:
        # Vintage. `True` was only ever written by a successful classification,
        # so it survives; `False` is the ambiguous one and reads as unknown.
        if value is True:
            return CareFlag(state=YES, context=context, types=types,
                            participants=participants, vintage=True)
        return CareFlag(state=UNKNOWN, context=context, types=types,
                        participants=participants,
                        reason=reason or VINTAGE_REASON, vintage=True)

    if not flag.get("classified") or value is None:
        return CareFlag(state=UNKNOWN, context=context, types=types,
                        participants=participants,
                        reason=reason or "nicht klassifiziert")
    return CareFlag(state=YES if value else NO, context=context, types=types,
                    participants=participants)


# ── what Agent B stores ─────────────────────────────────────────────────────

def classified(is_care_related: bool, *, context: str = "",
               types: Optional[list] = None,
               participants: Optional[list] = None) -> dict:
    """The record for a classification that **happened**."""
    return {
        "is_care_related": bool(is_care_related),
        "care_context": context or "",
        "care_types": list(types or []),
        "beteiligte": list(participants or []),
        # The marker that makes the three values readable. Its presence is also
        # what tells a reader this record is not a vintage one.
        "classified": True,
    }


def unclassified(reason: str) -> dict:
    """The record for a classification that did **not** happen.

    ``is_care_related: None`` rather than ``False``: the field answers "is this
    care-related", and the answer is that nobody knows. ``reason`` is kept so
    the next run — or a person reading the record — can tell a timeout from a
    path that deliberately skipped the classification.
    """
    return {
        "is_care_related": None,
        "care_context": "",
        "care_types": [],
        "beteiligte": [],
        "classified": False,
        "error": str(reason or "nicht klassifiziert"),
    }


# ── the questions the consumers ask ─────────────────────────────────────────

def is_care_flagged(source: Any) -> bool:
    """Whether this document **is** care-related. Unknown is not care-related.

    The predicate for "which documents are about care". Deliberately not the
    predicate for "which documents may be published under a narrow scope" —
    see :func:`needs_classification`, and ``corpus_build``, which withholds both.
    """
    return read(source).state == YES


def needs_classification(source: Any) -> bool:
    """Whether a re-run of Agent B would learn something about this document.

    Also the set a narrower publication scope has to withhold: a document that
    was never classified *may* be care-related, and the whole point of the
    narrow setting is not to publish those.
    """
    return read(source).state == UNKNOWN


def count_states(sources) -> dict:
    """``{"yes": n, "no": n, "unknown": n}`` over many records.

    So a corpus report can say "3 of 20 care-related, 6 never classified"
    instead of a number that silently folds the six into the seventeen.
    """
    counts = {YES: 0, NO: 0, UNKNOWN: 0}
    for source in sources:
        counts[read(source).state] += 1
    return counts
