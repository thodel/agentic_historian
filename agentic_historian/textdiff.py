"""textdiff.py — what a reader got wrong, as edits rather than a rate.

A CER says how much; these say what. ``confusions`` counts, over the edit
operations, which character a reader writes for which character the ground
truth has — ``s → ſ`` a hundred times is notation, a spread of letters is
reading. ``inline_diff`` shows one text against the other. Both work on
whitespace-collapsed text, the same strings the pipeline's CER scores.
Used by ``ruemlang/pipeline.py eval`` (pages) and ``external_lineset`` (lines).
"""

from __future__ import annotations

import re
from collections import Counter


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def inline_diff(ref: str, hyp: str) -> str:
    """``ref`` with the reader's changes marked: ``[-gt-]{+reader+}``."""
    from rapidfuzz.distance import Levenshtein
    ref, hyp = _flat(ref), _flat(hyp)
    out = []
    for op in Levenshtein.opcodes(ref, hyp):
        a, b = ref[op.src_start:op.src_end], hyp[op.dest_start:op.dest_end]
        if op.tag == "equal":
            out.append(a)
        else:
            out.append((f"[-{a}-]" if a else "") + (f"{{+{b}+}}" if b else ""))
    return "".join(out)


def confusions(ref: str, hyp: str) -> Counter:
    """``Counter[(gt char, reader char)]``, ``""`` for none on that side."""
    from rapidfuzz.distance import Levenshtein
    ref, hyp = _flat(ref), _flat(hyp)
    c: Counter = Counter()
    for op in Levenshtein.editops(ref, hyp):
        if op.tag == "replace":
            c[(ref[op.src_pos], hyp[op.dest_pos])] += 1
        elif op.tag == "delete":
            c[(ref[op.src_pos], "")] += 1
        else:
            c[("", hyp[op.dest_pos])] += 1
    return c


def show(pairs, top: int = 12) -> str:
    """``'s'→'ſ' 40, ∅→'.' 12`` for the most frequent of a Counter of pairs."""
    sym = lambda ch: repr(ch) if ch else "∅"
    return ", ".join(f"{sym(a)}→{sym(b)} {k}" for (a, b), k in pairs.most_common(top))


def normalize_convention(s: str) -> str:
    """Both sides mapped onto one notation-free form, for a **diagnostic** CER
    that separates "could it read the page" from "did it write our notation"
    (the trainer reports the same as ``convention_normalized``). Lower case;
    u/v and i/j by letter, not by sound; diacritics and superscript letters off
    (ů, ü, ú, ä → u, u, u, a); ſ and ß as s; punctuation, virgules and
    hyphenation marks dropped; whitespace collapsed. Never the headline: a GT
    that normalises u/v is a choice the reader should meet, and this hides it."""
    import unicodedata
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.translate(str.maketrans({"v": "u", "j": "i", "ſ": "s", "ß": "s", "ÿ": "y"}))
    s = re.sub(r"[^\w\s]|_", "", s)
    return _flat(s)
