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
