"""
mcp_atr/gt.py — foreign ground truth through the gateway (training-atr-models#200).

The trainer on asteraix offers the import path in two steps, and the gateway on
idhefix forwards them under ``/train/gt/*`` (serving-atr-inference#267):

* **survey** reads any GitHub repository or Zenodo record — licence and where it
  is written, size, image path, commit or versioned DOI — and drafts a register
  entry. It uploads nothing and decides nothing: a contradiction between two
  places is reported with both, never resolved.
* **import** takes only an id that is already in the register
  (``training-atr-models/src/atr_training/foreign_gt.py``). The register grows by
  pull request, after a person has read a survey. There is no way to make a
  dataset public from here; that is done by hand in the HF interface once the
  image rights are clear (training-atr-models#193).

This module validates before it sends, like :mod:`mcp_atr.ubelix`: a refused
request costs no round trip, and nothing a caller types becomes part of a path
unchecked. The trainer and the gateway check again; each side holds the line on
its own.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx

import config

#: A register id as ``foreign_gt.py`` writes them (``reichsanzeiger-gt``,
#: ``Weisthuemer``, ``charlottenburger-amtsschrifttum``).
SOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
#: An import job id as the trainer makes it: ``<UTC stamp>-<source>``.
JOB_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9._-]{1,80}$")
PART_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
#: What the survey accepts, checked here so a typo does not cost a round trip.
#: The trainer is the authority on the exact forms.
URL_RE = re.compile(r"^(https://(github\.com|zenodo\.org|doi\.org)/\S+|10\.5281/zenodo\.[0-9]+)$")

#: The gateway gives a survey 28 s (several requests to GitHub or Zenodo in a
#: row); the other routes answer within its usual 20. Both inside the broker's 60.
SURVEY_TIMEOUT_S = 35.0
TIMEOUT_S = 25.0


class GtError(ValueError):
    """A request refused before anything was sent."""


def _headers() -> dict:
    return {"X-API-Key": config.ATR_API_KEY} if config.ATR_API_KEY else {}


def _call(method: str, path: str, *, timeout: float = TIMEOUT_S,
          transport: Optional[httpx.BaseTransport] = None, **kwargs) -> dict:
    """One gateway request. Never raises: the answer, or what went wrong."""
    url = f"{config.ATR_GATEWAY_URL}/train/gt{path}"
    try:
        with httpx.Client(timeout=timeout, headers=_headers(), transport=transport) as client:
            response = client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        return {"ok": False, "error": f"the gateway did not answer within {timeout:g} s ({url})"}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"gateway unreachable at {url}: {exc}"}
    try:
        body = response.json()
    except ValueError:
        body = {"body": response.text[:800]}
    if response.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else body
        return {"ok": False, "status": response.status_code, "error": detail}
    if isinstance(body, dict):
        return {"ok": True, **body}
    return {"ok": True, "result": body}


def sources(check_hf: bool = True, *, transport=None) -> dict:
    """The register: what :func:`start_import` accepts."""
    return _call("GET", "/sources", params={"check_hf": "true" if check_hf else "false"},
                 transport=transport)


def survey(url: str, *, transport=None) -> dict:
    """Read-only survey of a GitHub repository or Zenodo record."""
    text = (url or "").strip()
    if len(text) > 500 or not URL_RE.match(text):
        return {"ok": False, "error": (
            f"not a GitHub repository or Zenodo record: {url!r} (e.g. "
            "https://github.com/UB-Mannheim/Fibeln, https://zenodo.org/records/11046062, "
            "10.5281/zenodo.11046062)")}
    return _call("POST", "/survey", json={"url": text}, timeout=SURVEY_TIMEOUT_S,
                 transport=transport)


def start_import(source_id: str, dry_run: bool = True, parts: Optional[list] = None,
                 all_parts: bool = False, *, transport=None) -> dict:
    """Start an import of a registered source. ``dry_run`` unless set false."""
    if not isinstance(source_id, str) or not SOURCE_RE.match(source_id):
        return {"ok": False, "error": f"not a register id: {source_id!r} (see gt_sources)"}
    names = list(parts or [])
    bad = [p for p in names if not isinstance(p, str) or not PART_RE.match(p)]
    if bad or len(names) > 10:
        return {"ok": False, "error": f"not part names: {bad or names!r}"}
    body = {"id": source_id, "dry_run": bool(dry_run), "parts": names,
            "all_parts": bool(all_parts)}
    return _call("POST", "/import", json=body, transport=transport)


def import_status(job_id: str, *, transport=None) -> dict:
    if not isinstance(job_id, str) or not JOB_RE.match(job_id):
        return {"ok": False, "error": f"not an import job id: {job_id!r} "
                                      "(e.g. 20261113T101500Z-weisthuemer)"}
    return _call("GET", f"/import/{job_id}", transport=transport)
