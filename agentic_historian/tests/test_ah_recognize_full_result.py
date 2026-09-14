"""``/recognize`` must hand back the whole result, and errors must carry a status.

Two gaps this closes, both found while building the multi-model batch runner
(`atr_batch.py`):

1. ``KrakenHTTPClient.recognize`` projected the gateway's ``RecognitionResult``
   down to the four fields ``/ocr`` returns, dropping the per-line readings, the
   timing, and party's second opinion. For a batch that is not a detail: keeping
   only the concatenated page text means nobody can say afterwards *which* line a
   model got wrong, and finding out costs the whole run again.
2. ``KrakenClientError`` carried the HTTP status only inside its message. Anything
   that retries has to tell "the gateway is restarting" (retry) from "that model
   id does not exist" (never retry, and stop asking) — parsing that back out of a
   sentence works until somebody rewords the sentence.

Offline — httpx is mocked. Run from the repo root::

    pytest agentic_historian/tests/test_ah_recognize_full_result.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.kraken_client import (        # noqa: E402
    KrakenClientError,
    KrakenHTTPClient,
    KrakenResult,
)


class _Response:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload or {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


class _HttpxClient:
    def __init__(self, response):
        self.response = response
        self.posts: list[tuple[str, dict]] = []

    def post(self, path, files=None, data=None):
        self.posts.append((path, dict(data or {})))
        return self.response

    def close(self):
        pass


def _client_with(response) -> tuple[KrakenHTTPClient, _HttpxClient]:
    client = KrakenHTTPClient(base_url="http://gw.test", api_key="k")
    http = _HttpxClient(response)
    client._client = http
    return client, http


GATEWAY_RESULT = {
    "model": "qwen3vl-german-xix-v1",
    "engine": "vllm",
    "text": "Lieber Freund,\nich habe",
    "lines": [
        {"order": 0, "text": "Lieber Freund,", "bbox": [10, 10, 500, 60], "confidence": 0.94},
        {"order": 1, "text": "ich habe", "bbox": [10, 70, 400, 120], "confidence": 0.91},
    ],
    "confidence": 0.92,
    "timing_ms": 8410,
    "segmented_by": "kraken:blla",
    "version": "0.9.1",
    "second_opinion": {"engine": "party", "model": "party", "text": "Lieber Freundt"},
}


def test_recognize_keeps_the_lines_timing_and_second_opinion():
    client, http = _client_with(_Response(GATEWAY_RESULT))
    res = client.recognize(b"image-bytes", model="qwen3vl-german-xix-v1")

    assert http.posts[0][0] == "/recognize"
    assert http.posts[0][1] == {"model": "qwen3vl-german-xix-v1"}
    assert res.text.startswith("Lieber Freund")
    assert [ln["text"] for ln in res.lines] == ["Lieber Freund,", "ich habe"]
    assert res.lines[0]["bbox"] == [10, 10, 500, 60]
    assert res.engine == "vllm"
    assert res.segmented_by == "kraken:blla"
    assert res.timing_ms == 8410
    assert res.second_opinion["text"] == "Lieber Freundt"
    assert res.service_version == "0.9.1"


def test_a_page_level_engine_returns_no_lines_and_that_is_not_an_error():
    """kraken and party segment internally and report no line geometry. Empty is
    the honest answer there — it must not read as a failure."""
    client, _ = _client_with(_Response({"text": "…", "confidence": 0.7, "model": "party",
                                        "version": "0.9.1", "engine": "party"}))
    res = client.recognize(b"x", model="party")
    assert res.lines == [] and res.second_opinion is None and res.timing_ms == 0


def test_the_legacy_result_shape_is_unchanged():
    """Every existing caller reads only these four. Adding fields must not make
    them build differently."""
    legacy = KrakenResult(text="t", confidence=0.5, model_used="m")
    assert (legacy.text, legacy.confidence, legacy.model_used) == ("t", 0.5, "m")
    assert legacy.service_version == "?"
    assert legacy.lines == [] and legacy.second_opinion is None


def test_two_results_do_not_share_one_lines_list():
    """A mutable default on a dataclass is shared by every instance; here that
    would quietly merge one page's lines into the next page's result."""
    a, b = KrakenResult("a", 0.0, "m"), KrakenResult("b", 0.0, "m")
    a.lines.append({"order": 0})
    assert b.lines == []


@pytest.mark.parametrize("status", [400, 404, 422, 500, 502])
def test_an_http_error_carries_its_status_code(status):
    client, _ = _client_with(_Response(status_code=status, text="nope"))
    with pytest.raises(KrakenClientError) as err:
        client.recognize(b"x", model="whatever")
    assert err.value.status_code == status
    assert str(status) in str(err.value)


def test_a_request_that_never_got_an_answer_has_no_status():
    """None is the signal that distinguishes "no reply" from "a reply that said
    no" — the first is worth retrying and the second is not."""
    assert KrakenClientError("unreachable").status_code is None
