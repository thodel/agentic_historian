"""Which pages of a document Agent B looks at (#771).

Agent B used to see page 1 and nothing else, so everything only visible on the
page — script and dating, which drive model selection (#299), hands, layout,
material — came from page 1. In a copy book or a minute book that is often the
cover, an index or a later addition. The transcription was already sampled from
the beginning and the end; the image was not.

A single point of reference leaves its own trace in every claim derived from it
(Ferilli & Piscopo, IAI4CH 2026). So the sample is drawn at even intervals across
the whole document, not picked by what looks typical: deterministic, the first
and the last page always in it.
"""

from __future__ import annotations


def spread(n: int, k: int) -> list[int]:
    """Indices of ``k`` pages at even intervals over ``n``: first and last always in.

    ``n <= k`` gives every page; ``k == 1`` gives page 1, as before #771.
    """
    if n <= 0:
        return []
    if k <= 1:
        return [0]
    if n <= k:
        return list(range(n))
    # n > k makes the step (n-1)/(k-1) larger than 1, so no two indices round together.
    return [round(i * (n - 1) / (k - 1)) for i in range(k)]


# ── the typical and the untypical page (#774) ────────────────────────────────

#: At most this many pages are measured to find the typical one, at even
#: intervals: a 600-page order must not cost 600 image reads before Agent B starts.
TYPICALITY_POOL = 60

#: The roles a chosen page has, as they appear in ``page_roles``.
TYPICAL, UNTYPICAL, SAMPLE = "typisch", "untypisch", "stichprobe"


def typicality(vectors: "list[list[float]]") -> list[float]:
    """Each page's mean distance to all others, on z-scored features.

    Z-scored so that no single feature — the histogram has sixteen bins, the
    pitch one number — outweighs the rest by its count or its scale.
    """
    import numpy as np

    x = np.asarray(vectors, dtype=float)
    if len(x) < 2:
        return [0.0] * len(x)
    std = x.std(axis=0)
    z = (x - x.mean(axis=0)) / np.where(std > 0, std, 1.0)
    d = np.sqrt(((z[:, None, :] - z[None, :, :]) ** 2).sum(axis=2))
    return (d.sum(axis=1) / (len(x) - 1)).tolist()


def choose(n: int, k: int, how: str = "spread", features=None) -> list[tuple[int, str]]:
    """``(index, role)`` of the pages Agent B is shown, in document order.

    ``spread`` is #771: ``k`` pages at even intervals, all ``stichprobe``.
    ``typical`` (#774) puts the most typical page (smallest mean distance to the
    others) and the least typical one (where a second hand, an addition or a new
    writing campaign shows) among them, and fills the rest from the spread.
    ``features`` is ``index -> vector``; a page it cannot read is left out of the
    measurement, and with fewer than three measured pages the choice is the
    spread, because "typical" of two pages means nothing.

    Deterministic: ties go to the lower page index.
    """
    base = spread(n, k)
    if how != "typical" or features is None or n < 3 or k < 2:
        return [(i, SAMPLE) for i in base]
    measured: list[tuple[int, list[float]]] = []
    for i in spread(n, min(n, TYPICALITY_POOL)):
        try:
            measured.append((i, list(features(i))))
        except Exception:  # noqa: BLE001 — an unreadable page is not measured
            continue
    if len(measured) < 3:
        return [(i, SAMPLE) for i in base]
    score = typicality([v for _, v in measured])
    order = sorted(range(len(measured)), key=lambda j: (score[j], measured[j][0]))
    typical = measured[order[0]][0]
    roles = {typical: TYPICAL}
    far = max(range(len(measured)), key=lambda j: (score[j], -measured[j][0]))
    if score[far] > score[order[0]]:        # all alike: there is no untypical page
        roles[measured[far][0]] = UNTYPICAL
    for i in base:
        if len(roles) >= k:
            break
        roles.setdefault(i, SAMPLE)
    return sorted(roles.items())
