"""#912: the strength report must not die on a partly unknown bucket.

``preference_strength`` keys buckets by ``(script, century, lang)`` and allows
a partly unknown key such as ``("kurrent", None, None)`` — the same coarse key
``preferences._criteria_for`` records. ``format_strength_stats`` sorted the thin
buckets by the tuple itself, and Python refuses to order ``None`` against a
string or a number. Seen on tei the moment the log held both shapes: the first
bucket ever to reach n≥10 was unreadable behind the exception.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_912_strength_report_partial_bucket.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import routing_report
from preferences import PreferenceEvent

OFFERED = [
    {"engine": "trocr", "model_id": "trocr-kurrent-xvi-xvii",
     "local_model_id": "dh-unibe/trocr-kurrent-XVI-XVII"},
    {"engine": "trocr", "model_id": "trocr-medieval-escriptmask",
     "local_model_id": "dh-unibe/trocr-medieval-escriptmask"},
    {"engine": "kraken", "model_id": "kraken-early_modern_german",
     "local_model_id": "10.5281/zenodo.15030337"},
]
CHOOSE = "trocr/trocr-medieval-escriptmask"


def _ev(script, century, lang):
    return PreferenceEvent(
        doc_id="d", page="p.jpg", offered=OFFERED, chosen=[CHOOSE],
        criteria={"script": script, "century": century, "lang": lang})


def test_a_partly_unknown_bucket_next_to_a_full_one_still_reports():
    """One thin bucket with None parts, one thin full bucket: the comparison
    that raised on tei."""
    events = [_ev("kurrent", None, None), _ev("kurrent", 16, "de"),
              _ev("bastarda", 15, None)]

    out = routing_report.format_strength_stats(events)

    assert "Noch zu dünn" in out
    assert "`kurrent/?/?`" in out and "`kurrent/16/de`" in out
    assert "`bastarda/15/?`" in out


def test_a_sufficient_bucket_is_listed_whatever_the_thin_ones_look_like():
    """The ranking that the exception hid: a bucket over the threshold shows
    its models while a None-keyed thin bucket sits in the same log."""
    events = [_ev("kurrent", 16, "de") for _ in range(12)] + [_ev(None, None, "fr")]

    out = routing_report.format_strength_stats(events)

    assert "`kurrent/16/de`" in out and "Stärke" in out
    assert "`?/?/fr`" in out


def test_the_order_key_treats_none_as_the_empty_string():
    assert routing_report._bucket_order(("kurrent", None, None)) == ("kurrent", "", "")
    assert routing_report._bucket_order((None, 16, "de")) == ("", "16", "de")
    ordered = sorted([("kurrent", None, None), ("bastarda", 15, "de"), (None, 16, "de")],
                     key=routing_report._bucket_order)
    assert ordered == [(None, 16, "de"), ("bastarda", 15, "de"), ("kurrent", None, None)]
