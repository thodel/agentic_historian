"""
edition_text.py — the reading layer of a two-layer edition text (#812).

The Königsfelden edition answers its full-text search and ``get_entry`` with a
TEI text flattened into one string, in which every abbreviation stands next to
its expansion (StAAG U.17/0274, 1357)::

    Johans Eichi bˀg berg , schulth̄ schultheiss ze Bremgar tē ten
    … de eliā elemosina ꝓ pro infirmis …

Nobody quotes that string. A model asked to copy "elemosina pro infirmis" or
"schultheiss ze Bremgarten" writes the expanded words, and the binding check
rejected every such quote on the first Königsfelden-only run (06.11.2026:
0 of 5 claims, "quote not in the cited passage" ×8).

:func:`reading_layer` derives the expanded layer by a fixed rule, and keeps,
for every character of it, the offset it came from — so a quote found in the
reading layer is always shown with the edition's own characters beside it. A
quote is then checked **exactly** against this layer, apart from whitespace
(the edition splits a word at an abbreviation: ``Bremgar tē ten`` reads
``Bremgar ten``). Nothing here guesses or measures similarity.

The rule, token by token (tokens are the whitespace-separated runs):

* ``✳`` (the edition's separator) and ``¬`` (a line-break hyphen inside a
  word) are removed wherever they stand — ``✳`` usually alone, sometimes
  attached to a word (``✳das``); a token left empty is dropped;
* a token is an **abbreviation** when it carries an abbreviation mark — a
  combining mark (``tē``, ``un̄``, ``ḡl``), one of ``ˀ ₎ ꝓ ꝝ ƺ ꝑ ꝗ ꝙ ꝯ``, or a
  final period (``Königsf.``) — **and** the next token is its expansion: same
  first letter, and its letters (marks removed) appear in the next token in
  order (``bˀg`` → ``berg``, ``eliā`` → ``elemosina``, ``ꝓ`` → ``pro``). Such a
  token is dropped.

The second condition is what keeps ordinary characters: ``guͦt`` carries a
combining ``ͦ`` but is followed by ``zuͦ``, which is not its expansion, so it
stays. A token with no mark is never dropped, even when it is an
abbreviation (``mt mütt``): a missed abbreviation costs a longer quote, a
wrongly dropped word would change the text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

__all__ = ["ReadingLayer", "locate", "reading_layer", "search_variants"]

#: Letters that stand for an abbreviation and decompose to nothing useful;
#: mapped to the letter they abbreviate from, or to "" when they carry none.
_ABBR_LETTERS = {"ꝓ": "p", "ꝑ": "p", "ꝗ": "q", "ꝙ": "q", "ꝝ": "r", "ꝯ": "c",
                 "ƺ": "", "ˀ": "", "₎": ""}
#: Characters removed wherever they stand: the edition's separator and its
#: in-word line-break hyphen.
_REMOVE = {"✳", "¬"}
_TOKEN = re.compile(r"\S+")


@dataclass(frozen=True)
class ReadingLayer:
    """The reading text, and for each of its characters the offset in the
    original text it came from (a joining space points at the next token)."""

    text: str
    index: tuple[int, ...]


def _base(token: str) -> str:
    """The letters of a token: marks and abbreviation signs removed, lowercased."""
    out = []
    for ch in unicodedata.normalize("NFKD", token):
        if unicodedata.combining(ch):
            continue
        if ch in _ABBR_LETTERS:
            out.append(_ABBR_LETTERS[ch])
        elif ch.isalpha():
            out.append(ch.lower())
    return "".join(out)


def _has_mark(token: str) -> bool:
    if any(ch in _ABBR_LETTERS for ch in token):
        return True
    if any(unicodedata.combining(ch) for ch in unicodedata.normalize("NFKD", token)):
        return True
    return len(token) > 2 and token.endswith(".") and token[-2].isalpha()


def _in_order(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def _expands(token: str, nxt: str) -> bool:
    """Whether ``nxt`` is the expansion of the abbreviation ``token``."""
    if not _has_mark(token):
        return False
    a, b = _base(token), _base(nxt)
    if not a or not b or a[0] != b[0] or len(b) < len(a):
        return False
    if token.endswith(".") and not any(_ABBR_LETTERS.get(c) is not None for c in token):
        return b.startswith(a)          # "Königsf." → "Königsfelden"
    return _in_order(a, b)


def reading_layer(text: str) -> ReadingLayer:
    """The expanded reading of a two-layer edition text, with its index map."""
    text = unicodedata.normalize("NFC", text or "")
    tokens = []
    for m in _TOKEN.finditer(text):
        chars = [(ch, m.start() + k) for k, ch in enumerate(m.group()) if ch not in _REMOVE]
        if chars:
            tokens.append(("".join(c for c, _ in chars), [o for _, o in chars]))
    kept: list[tuple[str, list[int]]] = []
    for i, (tok, offs) in enumerate(tokens):
        nxt = tokens[i + 1][0] if i + 1 < len(tokens) else ""
        if nxt and _expands(tok, nxt):
            continue
        kept.append((tok, offs))
    out: list[str] = []
    index: list[int] = []
    for n, (tok, offs) in enumerate(kept):
        if n:
            out.append(" ")
            index.append(offs[0])
        out.append(tok)
        index.extend(offs)
    return ReadingLayer("".join(out), tuple(index))


def locate(quote: str, layer: ReadingLayer, original: str) -> list[tuple[int, int]]:
    """Every span of ``original`` whose reading equals ``quote``, whitespace aside.

    Exact otherwise: case, letters and punctuation as written. The span runs
    from the original offset of the first matched character to just past the
    last, so it carries the edition's characters of both layers in between.
    """
    q = "".join(unicodedata.normalize("NFC", quote or "").split())
    if not q:
        return []
    compact, cmap = [], []
    for k, ch in enumerate(layer.text):
        if not ch.isspace():
            compact.append(ch)
            cmap.append(layer.index[k])
    hay = "".join(compact)
    spans, i = [], hay.find(q)
    while i != -1:
        start, last = cmap[i], cmap[i + len(q) - 1]
        end = last + 1
        # a combining mark after the last letter belongs to it
        while end < len(original) and unicodedata.combining(original[end]):
            end += 1
        spans.append((start, end))
        i = hay.find(q, i + 1)
    return spans


# ── search: the edition's spelling of a normalised word ──────────────────────

#: Superscript vowels the edition writes as combining letters: ``uͦ`` (u with
#: o above, for "uo"), ``uͤ``/``oͤ``/``aͤ`` (e above, for "ue"/"oe"/"ae" and the
#: umlauts). A model searches "almuosen"; the edition writes ``almuͦsen`` and
#: the full-text search does not fold one into the other — 0 hits against 5 on
#: 07.11.2026, among them the charters that answered the question (#812).
_DIGRAPHS = (("uo", "u\u0366"), ("ue", "u\u0364"), ("oe", "o\u0364"), ("ae", "a\u0364"))
_UMLAUTS = (("ü", "u\u0364"), ("ö", "o\u0364"), ("ä", "a\u0364"))


def search_variants(term: str) -> list[str]:
    """``term`` and the edition's spellings of it, the term first, no repeats.

    One variant per rule set (digraphs, umlauts), each applied to every
    occurrence — a few searches, not every combination.
    """
    term = unicodedata.normalize("NFC", (term or "").strip())
    if not term:
        return []
    out = [term]
    for rules in (_DIGRAPHS, _UMLAUTS):
        v = term
        for plain, edition in rules:
            v = v.replace(plain, edition)
        out.append(v)
    return list(dict.fromkeys(out))
