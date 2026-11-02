"""What a page looks like, as a few numbers — enough to say which page is typical (#774).

A stand-in until #773 delivers features from line crops (the hand, all against
all). These are cheap and deterministic, from the page image alone, on the CPU:

* the grey-value histogram (16 bins): paper, ink, how dark the hand writes;
* the ink density on a 4 × 4 grid: the layout — one column or two, margins,
  glosses, a page that is mostly blank;
* the line pitch, from the autocorrelation of the row profile: how tightly the
  lines sit, which changes with the hand and the writing campaign;
* the mean horizontal run of ink: a proxy for stroke width and letter size.

They see layout and density, not letterforms. That is why they choose which
page the VLM looks at and never judge the script themselves.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

__all__ = ["FEATURE_WIDTH", "image_features"]

#: The page is read at this width; the features are about proportions, not detail.
FEATURE_WIDTH = 256


def image_features(path: str | Path) -> list[float]:
    """The feature vector of one page image. Raises when the image cannot be read."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("L")
        w, h = im.size
        if w > FEATURE_WIDTH:
            im = im.resize((FEATURE_WIDTH, max(1, round(h * FEATURE_WIDTH / w))))
        g = np.asarray(im, dtype=np.float32) / 255.0

    hist, _ = np.histogram(g, bins=16, range=(0.0, 1.0))
    hist = hist / max(1, g.size)

    # Ink: darker than the page's own paper by a margin. A fixed threshold would
    # call a dark parchment all ink and a pale hand none.
    paper = float(np.percentile(g, 90))
    ink = g < paper * 0.75

    rows, cols = ink.shape
    grid = [float(ink[r * rows // 4:(r + 1) * rows // 4, c * cols // 4:(c + 1) * cols // 4].mean())
            if rows >= 4 and cols >= 4 else 0.0 for r in range(4) for c in range(4)]

    profile = ink.mean(axis=1) - ink.mean()
    pitch = 0.0
    if rows > 12 and profile.any():
        ac = np.correlate(profile, profile, mode="full")[rows - 1:]
        lo, hi = 3, max(4, rows // 4)
        if hi > lo:
            pitch = (lo + int(np.argmax(ac[lo:hi]))) / rows

    runs = []
    for row in ink[:: max(1, rows // 64)]:
        edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
        runs.extend((edges[1::2] - edges[0::2]).tolist())
    stroke = (float(np.mean(runs)) / cols) if runs else 0.0

    return [*hist.tolist(), *grid, pitch, stroke, float(ink.mean())]
