"""Locate errors in Unicode blocks, and separate misreading from convention.

CER says how much is wrong. The edit operations additionally say *which character*.
This module attributes every substitution, deletion and insertion to the Unicode
block of the reference character involved — for insertions, of the invented one —
and normalises by how often that block occurs in the reference. Without that
normalisation Basic Latin always wins: it is around nine tenths of the text.

The second half answers a different question. Folding one transcription convention
at a time and re-scoring the same output, with nothing recognised again, separates
"cannot read the hand" from "does not follow our conventions". On 15th-century
bastarda that is two fifths of the measured error of the best model; on 19th-century
Kurrent it is under a tenth. The share scales with how much of the reference lies
outside plain ASCII.

Normalisation of this kind is a diagnostic, never a deliverable: an edition without
long s and diacritics is worthless.
"""
import unicodedata
from collections import Counter, defaultdict

try:
    from rapidfuzz.distance import Levenshtein as L
except ImportError:  # pragma: no cover
    import Levenshtein as L

# Only the blocks that occur in Latin-script manuscripts; everything else falls
# into "other". Order is search order.
BLOCKS = [
    (0x0000, 0x001F, "Control"),
    (0x0020, 0x007F, "Basic Latin"),
    (0x0080, 0x00FF, "Latin-1 Supplement"),
    (0x0100, 0x017F, "Latin Extended-A"),
    (0x0180, 0x024F, "Latin Extended-B"),
    (0x0250, 0x02AF, "IPA Extensions"),
    (0x02B0, 0x02FF, "Spacing Modifiers"),
    (0x0300, 0x036F, "Combining Diacritical Marks"),
    (0x1E00, 0x1EFF, "Latin Extended Additional"),
    (0x2000, 0x206F, "General Punctuation"),
    (0x2070, 0x209F, "Super-/Subscripts"),
    (0x20A0, 0x20CF, "Currency Symbols"),
    (0x2100, 0x214F, "Letterlike Symbols"),
    (0x2150, 0x218F, "Number Forms"),
    (0x2190, 0x21FF, "Arrows"),
    (0x2200, 0x22FF, "Mathematical Operators"),
    (0xA700, 0xA7FF, "Latin Extended-D"),      # medieval abbreviation marks
    (0xE000, 0xF8FF, "Private Use Area"),      # legacy MUFI code points
    (0xFB00, 0xFB4F, "Alphabetic Presentation"),  # ﬀ ﬁ ﬂ …
]


def block_of(ch):
    cp = ord(ch)
    for lo, hi, name in BLOCKS:
        if lo <= cp <= hi:
            return name
    return "other"


def charname(ch):
    try:
        return unicodedata.name(ch)
    except ValueError:
        return "U+%04X" % ord(ch)


def show(ch):
    """Printable form: a combining mark needs a carrier to be visible."""
    if unicodedata.combining(ch):
        return "◌" + ch
    if ch == " ":
        return "␣"
    if ord(ch) < 0x20:
        return "U+%04X" % ord(ch)
    return ch


def profile(pairs):
    """pairs: iterable of (reference, hypothesis).

    Returns, per block: characters in the reference, substitutions, deletions and
    insertions; plus the confusion pairs, the characters lost and the ones invented.
    """
    gt_count = Counter()
    sub = Counter()
    dele = Counter()
    ins = Counter()
    confusion = Counter()
    dropped = Counter()      # characters the hypothesis lost
    invented = Counter()     # characters it added

    for ref, hyp in pairs:
        for ch in ref:
            gt_count[block_of(ch)] += 1
        for op, i, j in L.editops(ref, hyp):
            if op == "replace":
                sub[block_of(ref[i])] += 1
                confusion[(ref[i], hyp[j])] += 1
            elif op == "delete":
                dele[block_of(ref[i])] += 1
                dropped[ref[i]] += 1
            else:  # insert — the character exists only in the hypothesis
                ins[block_of(hyp[j])] += 1
                invented[hyp[j]] += 1

    return {"gt": gt_count, "sub": sub, "del": dele, "ins": ins,
            "confusion": confusion, "dropped": dropped, "invented": invented}


def render(p, title, min_gt=1):
    out = [title, "=" * len(title), ""]
    total_gt = sum(p["gt"].values())
    out.append("  %-30s %9s %7s %7s %7s %9s" %
               ("Unicode block", "in ref", "sub", "del", "ins", "error%"))
    rows = []
    for b, n in p["gt"].items():
        if n < min_gt:
            continue
        s, d, i = p["sub"][b], p["del"][b], p["ins"][b]
        rows.append((100 * (s + d) / n, b, n, s, d, i))
    for rate, b, n, s, d, i in sorted(rows, key=lambda r: -r[0]):
        out.append("  %-30s %9d %7d %7d %7d %8.1f%%" % (b, n, s, d, i, rate))
    out.append("  %-30s %9d" % ("total", total_gt))
    return "\n".join(out)


def render_confusions(p, n=18):
    out = ["", "  most frequent confusions (reference -> hypothesis)"]
    for (a, b), c in p["confusion"].most_common(n):
        out.append("   %5d  %-3s → %-3s   %s  →  %s"
                   % (c, show(a), show(b), charname(a)[:34], charname(b)[:34]))
    out.append("")
    out.append("  most frequently lost characters")
    for ch, c in p["dropped"].most_common(10):
        out.append("   %5d  %-3s  %s" % (c, show(ch), charname(ch)[:46]))
    out.append("")
    out.append("  most frequently invented characters")
    for ch, c in p["invented"].most_common(10):
        out.append("   %5d  %-3s  %s" % (c, show(ch), charname(ch)[:46]))
    return "\n".join(out)


# -- normalisation ladder ---------------------------------------------------
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
PUNCT = {"·": ".", "•": ".", "–": "-", "—": "-", "‧": ".",
         "„": '"', "“": '"', "”": '"', "‚": "'", "‘": "'", "’": "'", "‹": "'", "›": "'"}


def step_none(s):
    return s


def step_case(s):
    return s.lower()


def step_longs(s):
    return s.replace("ſ", "s").replace("ʒ", "z")


def step_punct(s):
    for a, b in PUNCT.items():
        s = s.replace(a, b)
    return s


def step_lig(s):
    for a, b in LIGATURES.items():
        s = s.replace(a, b)
    return s


def step_combining(s):
    d = unicodedata.normalize("NFD", s)
    return "".join(c for c in d if not unicodedata.combining(c))


def step_space(s):
    return " ".join(s.split())


LADDER = [
    ("raw", []),
    ("+ whitespace", [step_space]),
    ("+ lower case", [step_space, step_case]),
    ("+ long s -> s", [step_space, step_case, step_longs]),
    ("+ ligatures", [step_space, step_case, step_longs, step_lig]),
    ("+ punctuation", [step_space, step_case, step_longs, step_lig, step_punct]),
    ("+ diacritics folded", [step_space, step_case, step_longs, step_lig, step_punct, step_combining]),
]


def ladder(pairs):
    pairs = list(pairs)
    out = ["", "  normalisation ladder - how much of the error is convention?",
           "  %-24s %9s %9s" % ("step", "CER", "delta")]
    prev = None
    rows = []
    for label, steps in LADDER:
        e = n = 0
        for ref, hyp in pairs:
            r, h = ref, hyp
            for f in steps:
                r, h = f(r), f(h)
            e += L.distance(r, h)
            n += len(r)
        c = 100 * e / max(1, n)
        rows.append((label, c, None if prev is None else c - prev))
        prev = c
    for label, c, d in rows:
        out.append("  %-24s %8.2f%% %9s" % (label, c, "" if d is None else "%+.2f" % d))
    return "\n".join(out)
