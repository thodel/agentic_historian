"""Tests for SEC-6 (#577): a job_id must not be able to steer the gateway URL.

`atr_status.job` and `atr_status.log` interpolate job_id into
`/train/jobs/{job_id}`. Unchecked, a role-holder's `../../models` turned the call
into `GET /models` (carrying the bot's API key), `?x=1` appended a query and `#`
truncated the path. The id is now validated against a tight pattern before it
reaches the URL, and the traversal specials `.` / `..` are refused outright.

Offline: no gateway is contacted — `_get` is monkeypatched to record the path it
would have requested, so a rejected id is proven to never produce a call.
"""

import asyncio
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_status  # noqa: E402


@pytest.fixture
def asked(monkeypatch):
    """Record the (path, params) of every gateway call that actually happens."""
    calls: list[tuple] = []

    async def _get(path, params=None):
        calls.append((path, params))
        return {}

    monkeypatch.setattr(atr_status, "_get", _get)
    return calls


# ── the validator ────────────────────────────────────────────────────────────

VALID = ["abc123", "train-20260921-1a2b", "job_42", "a", "x" * 64, "v1.2.3"]
INVALID = [
    "../../models",            # path traversal → GET /models
    "..",                      # the traversal special on its own
    ".",
    "x/../../y",               # internal slash
    "job?x=1",                 # query injection
    "job#frag",                # fragment truncation
    "job id",                  # space
    "job/log",                 # extra path segment
    "x" * 65,                  # too long
    "",                        # empty
    "jöb",                     # non-ASCII
]


@pytest.mark.parametrize("jid", VALID)
def test_safe_job_id_accepts_plausible_ids(jid):
    assert atr_status._safe_job_id(jid) == jid


@pytest.mark.parametrize("jid", INVALID)
def test_safe_job_id_rejects_steering_ids(jid):
    with pytest.raises(atr_status.AtrStatusError):
        atr_status._safe_job_id(jid)


# ── the calls that use it never emit a steered URL ───────────────────────────

def test_job_rejects_traversal_without_calling_the_gateway(asked):
    with pytest.raises(atr_status.AtrStatusError):
        asyncio.run(atr_status.job("../../models"))
    assert asked == [], "a rejected job_id must never reach _get"


def test_log_rejects_query_injection_without_calling_the_gateway(asked):
    with pytest.raises(atr_status.AtrStatusError):
        asyncio.run(atr_status.log("job?x=1"))
    assert asked == []


def test_job_passes_a_valid_id_through_to_the_expected_path(asked):
    asyncio.run(atr_status.job("train-123"))
    assert asked == [("/train/jobs/train-123", None)]


def test_log_passes_a_valid_id_through_to_the_expected_path(asked):
    asyncio.run(atr_status.log("train-123", lines=12))
    assert asked == [("/train/jobs/train-123/log", {"stage": "train", "lines": 12})]
