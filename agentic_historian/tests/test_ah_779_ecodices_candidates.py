"""Choosing the e-codices volumes for #779 and #773 from the catalogue itself.

Offline: OAI-PMH pages, manifests and TEI are served by a fake ``get``. What
is pinned: ids are read from OAI identifiers across resumption tokens; the
choice is deterministic, spreads over centuries, takes several catalogued
hands first and ``saa-0428`` always; volumes without a date or with too few
pages are not taken; and the chosen volumes land in the layout ``fetch``
leaves, so ``run``, ``score`` and ``hand_consistency validate`` go on.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from eval import ecodices_bench as eb
from eval import ecodices_candidates as ec

OAI_PAGE = """<?xml version="1.0"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListIdentifiers>
{headers}{token}
</ListIdentifiers></OAI-PMH>"""


def _oai(ids, token=""):
    headers = "".join(f"<header><identifier>oai:e-codices.unifr.ch:{i}</identifier></header>"
                      for i in ids)
    tok = f"<resumptionToken>{token}</resumptionToken>" if token else "<resumptionToken/>"
    return OAI_PAGE.format(headers=headers, token=tok).encode()


def _tei(date_from, hands=None):
    hd = f'<handDesc hands="{hands}"><p>x</p></handDesc>' if hands else ""
    od = f'<origDate notBefore="{date_from}" notAfter="{date_from + 20}"/>' if date_from else ""
    return (f'<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><publicationStmt>'
            f'<availability><licence target="cc-by">CC BY</licence></availability>'
            f'</publicationStmt><sourceDesc><msDesc><physDesc>{hd}</physDesc>'
            f'<history><origin>{od}</origin></history></msDesc></sourceDesc></fileDesc>'
            f'</teiHeader></TEI>').encode()


def _manifest(vid, pages):
    return json.dumps({"seeAlso": f"https://e-codices.ch/xml/tei_published/{vid}_x.xml",
                       "sequences": [{"canvases": [
                           {"images": [{"resource": {"service": {"@id": f"https://iiif/{vid}/{i}"}}}]}
                           for i in range(pages)]}]}).encode()


VOLUMES = {   # id: (date_from, hands, pages)
    "saa-0428": (1350, 3, 200),
    "saa-0001": (1450, 2, 120),
    "saa-0002": (1550, 1, 80),
    "csg-0001": (850, 1, 300),
    "csg-0002": (1450, None, 50),
    "bge-0001": (1250, 2, 100),
    "bge-0002": (1550, 2, 4),        # too few pages
    "zbz-0001": (None, 1, 100),      # no date
}


def _get(calls=None):
    def get(url):
        if calls is not None:
            calls.append(url)
        if "verb=ListIdentifiers" in url:
            if "resumptionToken=" in url:
                return _oai(list(VOLUMES)[4:])
            return _oai(list(VOLUMES)[:4], token="next")
        for vid, (d, h, p) in VOLUMES.items():
            if url == eb.IIIF_MANIFEST.format(id=vid):
                return _manifest(vid, p)
            if url.endswith(f"/{vid}_x.xml"):
                return _tei(d, h)
        raise AssertionError(url)
    return get


def test_ids_come_from_oai_across_resumption_tokens():
    assert ec.list_ids_oai(_get()) == sorted(VOLUMES)
    assert ec.vol_id_of("oai:e-codices.unifr.ch:saa-0428") == "saa-0428"
    assert ec.vol_id_of("https://e-codices.ch/en/list/one/csg/1072") == "csg-1072"


def test_ids_are_read_from_e_codices_record_urls():
    """The identifiers e-codices actually sends (OAI, 6.11.2026)."""
    url = "oai:e-codices.ch:https://www.e-codices.ch/en/list/one/{}"
    assert ec.vol_id_of(url.format("bpun/A0021")) == "bpun-a0021"
    assert ec.vol_id_of(url.format("csg/0001")) == "csg-0001"
    assert ec.vol_id_of(url.format("saa/0428/")) == "saa-0428"
    assert ec.vol_id_of(url.format("ubb/AN-I-0008")) == "ubb-an-i-0008"
    assert ec.vol_id_of("oai:e-codices.ch:https://www.e-codices.ch/en/list/stibe") is None, \
        "a shelfmark alone is no id (the first run asked for an-i-0008 and got 400)"
    page = ('<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListIdentifiers>'
            + "".join(f"<header><identifier>{url.format(t)}</identifier><setSpec>x</setSpec></header>"
                      for t in ("bpun/A0021", "bpun/A0001", "csg/0001"))
            + '<resumptionToken completeListSize="3" cursor="0"/></ListIdentifiers></OAI-PMH>')
    assert ec.list_ids_oai(lambda u: page.encode()) == ["bpun-a0001", "bpun-a0021", "csg-0001"]


def test_an_oai_error_is_said_not_swallowed():
    err = b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><error code="badVerb">no</error></OAI-PMH>'
    with pytest.raises(RuntimeError, match="badVerb"):
        ec.list_ids_oai(lambda url: err)


def test_inspection_takes_must_and_preferred_first_then_a_spread():
    ids = [f"zzz-{i:04d}" for i in range(100)] + ["saa-0001", "saa-0002"]
    order = ec.inspection_order(ids, 12)
    assert order[:3] == ["saa-0428", "saa-0001", "saa-0002"]
    assert len(order) == 12 and order[3] == "zzz-0000" and order[-1] > "zzz-0080"


def test_selection_spreads_centuries_and_takes_several_hands_first():
    c = lambda i, d, h, p=50: ec.Candidate(i, p, d, d and d + 10, h, "", "", "")
    cands = [c("saa-0428", 1350, 3), c("x-1", 1450, 1), c("x-2", 1450, 2), c("x-3", 1450, None),
             c("x-4", 1550, 1), c("x-5", 1650, 2), c("x-6", 1250, None), c("x-7", 1450, 2, 3),
             c("x-8", None, 2)]
    chosen = ec.select(cands, n=5)
    assert chosen[0] == "saa-0428", "the volume Agent B already described comes first"
    assert "x-7" not in chosen and "x-8" not in chosen, "too few pages / no date"
    assert chosen[1:] == ["x-6", "x-2", "x-4", "x-5"], "one per century first, several hands before one"
    assert ec.select(list(reversed(cands)), n=5) == chosen, "deterministic"


def test_candidates_writes_the_table_and_the_fetch_layout(tmp_path):
    calls = []
    chosen = ec.candidates(tmp_path, get=_get(calls), n=4)
    assert chosen[0] == "saa-0428" and "bge-0002" not in chosen and "zbz-0001" not in chosen
    assert (tmp_path / "selected.txt").read_text().split() == chosen
    tsv = (tmp_path / "candidates.tsv").read_text().splitlines()
    assert tsv[0].startswith("id\tselected") and len(tsv) == 1 + len(VOLUMES)
    for vid in chosen:
        assert (tmp_path / vid / "tei.xml").exists() and (tmp_path / vid / "manifest.json").exists()
        assert eb.parse_tei((tmp_path / vid / "tei.xml").read_bytes(), vid).date_from
    n = len(calls)
    ec.candidates(tmp_path, get=_get(calls), n=4)
    assert len(calls) - n == 2, "a second run reads only the OAI list; manifests and TEI are cached"


def test_a_file_of_ids_replaces_the_oai_listing(tmp_path):
    calls = []
    chosen = ec.candidates(tmp_path, get=_get(calls), n=3, ids=["csg-0001", "bge-0001"])
    assert not any("ListIdentifiers" in u for u in calls)
    assert set(chosen) <= {"saa-0428", "csg-0001", "bge-0001"} and chosen[0] == "saa-0428"


def test_fetch_takes_ids_from_a_file(tmp_path, monkeypatch):
    (tmp_path / "ids.txt").write_text("# chosen\nsaa-0428\n\ncsg-0001\n")
    seen = []
    monkeypatch.setattr(eb, "fetch", lambda ids, d, **kw: seen.extend(ids) or list(ids))
    assert eb.main(["fetch", str(tmp_path / "b"), "--ids-from", str(tmp_path / "ids.txt")]) == 0
    assert seen == ["saa-0428", "csg-0001"]


# ── a guest on the library's server (6.11.2026: 300 requests, 503 after the first) ──

class _Resp:
    def __init__(self, status, body=b"", headers=None):
        self.status_code, self.content, self.headers = status, body, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    def __init__(self, answers):
        self.answers, self.urls = list(answers), []

    def get(self, url):
        self.urls.append(url)
        return self.answers.pop(0)


def test_the_getter_pauses_and_retries_a_refusal():
    slept = []
    client = _Client([_Resp(503), _Resp(429, headers={"Retry-After": "7"}), _Resp(200, b"ok"),
                      _Resp(200, b"two")])
    g = eb.PoliteGetter(delay=1.0, backoff=(5, 20), sleep=slept.append, client=client)
    assert g("u1") == b"ok"
    waits = [s for s in slept if s > 1]          # the fake sleep stops no clock: drop the pauses
    assert waits == [5, 7], "backoff first, then the server's Retry-After"
    assert g("u2") == b"two"
    assert 0 < slept[-1] <= 1.0, "a pause before the next request"


def test_the_getter_gives_up_and_does_not_retry_a_client_error():
    client = _Client([_Resp(503), _Resp(503), _Resp(503)])
    g = eb.PoliteGetter(delay=0, backoff=(1, 1), sleep=lambda s: None, client=client)
    with pytest.raises(RuntimeError, match="503"):
        g("u")
    client = _Client([_Resp(400)])
    g = eb.PoliteGetter(delay=0, backoff=(1, 1), sleep=lambda s: None, client=client)
    with pytest.raises(RuntimeError, match="400"):
        g("u")
    assert len(client.urls) == 1


def test_inspection_stops_when_the_server_refuses(tmp_path, capsys):
    ids = [f"zzz-{i:04d}" for i in range(50)]
    calls = []

    def refusing(url):
        calls.append(url)
        if "saa-0428" in url:
            return _get()(url)
        raise RuntimeError("HTTP 503")

    chosen = ec.candidates(tmp_path, get=refusing, n=5, ids=ids, max_failures=4)
    assert chosen == ["saa-0428"]
    assert len([u for u in calls if "zzz" in u]) == 4, "four refusals in a row, then stop"
    assert "e-codices is refusing" in capsys.readouterr().err


def test_an_incomplete_oai_listing_is_said(capsys):
    page = (b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListIdentifiers>'
            b'<header><identifier>oai:x:saa-0001</identifier></header>'
            b'<resumptionToken completeListSize="2900"/></ListIdentifiers></OAI-PMH>')
    assert ec.list_ids_oai(lambda url: page) == ["saa-0001"]
    assert "announced 2900 records" in capsys.readouterr().err


def test_a_resumption_token_is_url_encoded():
    urls = []
    first = _oai(["saa-0001"], token="a|b/c=d").decode().encode()

    def get(url):
        urls.append(url)
        return first if len(urls) == 1 else _oai(["saa-0002"])

    ec.list_ids_oai(get)
    assert urls[1].endswith("resumptionToken=a%7Cb%2Fc%3Dd")
