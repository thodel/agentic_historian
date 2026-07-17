"""#29: Voyant export URL generation + verify helper (offline; requests mocked).

The URL is built from Voyant's **Trombone API** — `POST /trombone` returns JSON
carrying `corpus.metadata.id`, and the shareable link is `?corpus=<id>`. Verified
live on tei 2026-07-17:

    POST /voyant/?text=   → 500  (JSP/JasperException — the old contract)
    POST /voyant/trombone → 200  {"corpus": {"metadata": {"id": "..."}}}
    GET  /voyant/?corpus=<id> → 200

These tests mock that JSON contract; the "against a real corpus" acceptance is
`corpus_analysis.verify_voyant()` run on-host.

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


def _trombone(status=200, corpus_id="abc123"):
    """A Trombone CorpusMetadata response, shaped like the real one."""
    body = {"corpus": {"metadata": {"id": corpus_id}}} if corpus_id is not None else {}
    m = mock.Mock(status_code=status)
    m.json.return_value = body
    m.raise_for_status = mock.Mock(
        side_effect=None if status == 200 else RuntimeError(f"HTTP {status}"))
    return m


# ── _voyant_url: build ?corpus= from the Trombone id ─────────────────────────

def test_voyant_url_builds_corpus_link_from_trombone_id():
    with mock.patch.object(ca.requests, "post", return_value=_trombone(corpus_id="abc")):
        url = ca._voyant_url("text", "default")
    assert url.endswith("/?corpus=abc")


def test_voyant_url_posts_to_trombone_not_the_ui_shell():
    """The bug was POSTing to /?text= (the JSP UI → 500). Must hit /trombone."""
    with mock.patch.object(ca.requests, "post", return_value=_trombone()) as post:
        ca._voyant_url("some corpus text", "default")
    endpoint = post.call_args.args[0]
    assert endpoint.endswith("/trombone")
    data = post.call_args.kwargs["data"]
    assert data["tool"] == "corpus.CorpusMetadata"
    assert data["input"] == "some corpus text"


def test_voyant_url_empty_on_http_error():
    with mock.patch.object(ca.requests, "post", return_value=_trombone(status=500)):
        assert ca._voyant_url("text", "default") == ""


def test_voyant_url_empty_when_no_corpus_id():
    with mock.patch.object(ca.requests, "post", return_value=_trombone(corpus_id=None)):
        assert ca._voyant_url("text", "default") == ""


def test_voyant_url_empty_on_exception():
    with mock.patch.object(ca.requests, "post", side_effect=RuntimeError("down")):
        assert ca._voyant_url("text", "default") == ""


def test_voyant_url_truncates_to_50k():
    with mock.patch.object(ca.requests, "post", return_value=_trombone()) as post:
        ca._voyant_url("x" * 100_000, "default")
    assert len(post.call_args.kwargs["data"]["input"]) == 50_000


# ── verify_voyant: the on-host acceptance helper ─────────────────────────────
#
# verify_voyant makes TWO Trombone calls: create the corpus, then retrieve it by
# id. ok=True means BOTH succeeded — the corpus persisted, not just that a link
# string was built.

def test_verify_ok_requires_corpus_to_be_retrievable():
    # 1st POST creates (id=z), 2nd POST retrieves the same id → persisted.
    with mock.patch.object(ca.requests, "post",
                           side_effect=[_trombone(corpus_id="z"), _trombone(corpus_id="z")]):
        r = ca.verify_voyant()
    assert r["ok"] is True and r["retrievable"] is True and "corpus=z" in r["url"]


def test_verify_not_ok_when_link_built_but_corpus_did_not_persist():
    # created fine, but retrieval comes back with a DIFFERENT/empty id → ephemeral.
    with mock.patch.object(ca.requests, "post",
                           side_effect=[_trombone(corpus_id="z"), _trombone(corpus_id=None)]):
        r = ca.verify_voyant()
    assert r["ok"] is False and r["retrievable"] is False
    assert "not retrievable" in r["reason"] or "persist" in r["reason"]


def test_verify_not_ok_when_trombone_broken():
    with mock.patch.object(ca.requests, "post", side_effect=RuntimeError("timeout")):
        r = ca.verify_voyant()
    assert r["ok"] is False and r["url"] == ""
    assert "unreachable" in r["reason"] or "contract" in r["reason"]


def test_verify_reason_does_not_overclaim_the_browser():
    """ok=True must not read as 'a human can open this' — the reason names the
    limit (#315), because no server-side check can prove the SPA renders."""
    with mock.patch.object(ca.requests, "post",
                           side_effect=[_trombone(corpus_id="z"), _trombone(corpus_id="z")]):
        r = ca.verify_voyant()
    assert "315" in r["reason"] or "not checked" in r["reason"]
