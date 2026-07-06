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


def _post(ok, url):
    return mock.Mock(ok=ok, url=url)


def test_voyant_url_returns_corpus_redirect():
    with mock.patch.object(ca.requests, "post",
                           return_value=_post(True, "https://tei.dh.unibe.ch/voyant/?corpus=abc")):
        assert ca._voyant_url("text", "default") == "https://tei.dh.unibe.ch/voyant/?corpus=abc"


def test_voyant_url_empty_on_non_ok():
    with mock.patch.object(ca.requests, "post", return_value=_post(False, "https://x/err")):
        assert ca._voyant_url("text", "default") == ""


def test_voyant_url_empty_without_corpus_param():
    with mock.patch.object(ca.requests, "post", return_value=_post(True, "https://x/no-corpus")):
        assert ca._voyant_url("text", "default") == ""


def test_voyant_url_empty_on_exception():
    with mock.patch.object(ca.requests, "post", side_effect=RuntimeError("down")):
        assert ca._voyant_url("text", "default") == ""


def test_verify_ok_when_corpus_link_returned():
    with mock.patch.object(ca.requests, "post",
                           return_value=_post(True, "https://tei.dh.unibe.ch/voyant/?corpus=z")):
        r = ca.verify_voyant()
    assert r["ok"] is True and "corpus=" in r["url"]
    assert r["endpoint"].endswith("/")


def test_verify_not_ok_when_endpoint_broken():
    with mock.patch.object(ca.requests, "post", side_effect=RuntimeError("timeout")):
        r = ca.verify_voyant()
    assert r["ok"] is False and r["url"] == "" and "unreachable" in r["reason"]
