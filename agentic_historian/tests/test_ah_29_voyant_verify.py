"""#29: Voyant export URL generation + verify helper (offline; requests mocked).

The live "against a real corpus" acceptance is run on-host via
``corpus_analysis.verify_voyant()``; here we lock the URL contract and the
verify helper's ok/not-ok logic without the network.

Run from the repo root:
    pytest agentic_historian/tests/test_ah_29_voyant_verify.py
"""

import sys
from pathlib import Path
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agents import corpus_analysis as ca  # noqa: E402


def _get(ok, url):
    return mock.Mock(ok=ok, url=url, status_code=200)


def test_voyant_url_returns_text_link():
    """GET with ?text= returns the shareable Voyant URL."""
    with mock.patch.object(ca.requests, "get",
                           return_value=_get(True, "https://tei.dh.unibe.ch/voyant/?text=abc")):
        assert ca._voyant_url("text", "default") == "https://tei.dh.unibe.ch/voyant/?text=abc"


def test_voyant_url_empty_on_non_ok():
    with mock.patch.object(ca.requests, "get", return_value=_get(False, "https://x/err")):
        assert ca._voyant_url("text", "default") == ""


def test_voyant_url_returns_any_ok_url():
    """Any 200 OK response from Voyant is returned as the shareable URL.

    The new GET-based approach returns whatever URL Voyant resolves to,
    which may be a ?text= embed or a ?corpus= stable ID.  The caller
    (verify_voyant) validates the specific parameter.
    """
    with mock.patch.object(ca.requests, "get",
                           return_value=_get(True, "https://tei.dh.unibe.ch/voyant/?text=abc")):
        url = ca._voyant_url("text", "default")
        assert url == "https://tei.dh.unibe.ch/voyant/?text=abc"


def test_voyant_url_empty_on_exception():
    with mock.patch.object(ca.requests, "get", side_effect=RuntimeError("down")):
        assert ca._voyant_url("text", "default") == ""


def test_verify_ok_when_text_link_returned():
    """Voyant 2.4 returns a ?text= URL — accepted as valid."""
    with mock.patch.object(ca.requests, "get",
                           return_value=_get(True, "https://tei.dh.unibe.ch/voyant/?text=z")):
        r = ca.verify_voyant()
    assert r["ok"] is True and "text=" in r["url"]
    assert r["endpoint"].endswith("/")


def test_verify_ok_when_corpus_link_returned():
    """Legacy ?corpus= URLs from older Voyant versions still accepted."""
    with mock.patch.object(ca.requests, "get",
                           return_value=_get(True, "https://tei.dh.unibe.ch/voyant/?corpus=z")):
        r = ca.verify_voyant()
    assert r["ok"] is True and "corpus=" in r["url"]


def test_verify_not_ok_when_endpoint_broken():
    with mock.patch.object(ca.requests, "get", side_effect=RuntimeError("timeout")):
        r = ca.verify_voyant()
    assert r["ok"] is False and r["url"] == "" and "unreachable" in r["reason"]
