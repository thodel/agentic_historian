#!/usr/bin/env python3
"""Fetch the DH digest feeds and write the items of the last N days as JSON (#799).

The weekly digest failed five times in OpenClaw with ``timed out
(model-call-started)``: seventeen feeds fetched by the agent, one tool call
each, inside a 300 s budget shared with the model. This script does the
fetching outside the agent — stdlib only, one process, a timeout per feed —
and leaves the agent one job: to read ``digest_items.json`` and rate it.

    python3 digest_fetch.py --days 7 --out ~/.hermes/cron/digest_items.json
    python3 digest_fetch.py --feeds feeds.txt --days 7       # JSON to stdout

RSS 2.0 and Atom are both read. A feed that cannot be fetched or parsed is
listed under ``errors`` with its reason, never silently dropped: a digest that
says "nothing this week" because a feed was down is the failure this exists
to make visible.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Optional

#: The feeds of the OpenClaw job "DH Weekly Digest", verbatim, with their labels.
DEFAULT_FEEDS = [
    ("Reviews in DH", "https://reviewsindh.pubpub.org/rss.xml"),
    ("DSH (OUP)", "https://academic.oup.com/rss/site_5447/3308.xml"),
    ("ZfdG", "https://zfdg.de/zfdg_beitraege.xml"),
    ("DH Now", "https://digitalhumanitiesnow.org/feed/"),
    ("arXiv cs.DL", "https://export.arxiv.org/rss/cs.DL"),
    ("arXiv cs.IR", "https://export.arxiv.org/rss/cs.IR"),
    ("Digital Studies / champ numérique",
     "https://www.google.com/alerts/feeds/12326514175916915572/8059671739515192766"),
    ("DHQ", "https://www.google.com/alerts/feeds/12326514175916915572/2170710756314080150"),
    ("Open Humanities Data",
     "https://www.google.com/alerts/feeds/12326514175916915572/4303239240162766721"),
    ("magazén", "https://www.google.com/alerts/feeds/12326514175916915572/14639773446571128045"),
    ("JDMDH", "https://api.episciences.org/api/feed/rss/jdmdh"),
    ("Transformations (DARIAH)", "https://api.episciences.org/api/feed/rss/transformations"),
    ("Journal of Digital History (Springer)",
     "https://link.springer.com/search.rss?facet-journal-id=42803&facet-content-type=Article"),
    ("Code4Lib Journal", "https://journal.code4lib.org/feed"),
    ("Critical Digital Humanities",
     "https://submission.wavehills.org/index.php/cdh/gateway/plugin/WebFeedGatewayPlugin/rss"),
    ("Histoire & mesure",
     "https://journals.openedition.org/histoiremesure/backend?format=rssdocuments"),
    ("Acta historica et archaeologica mediaevalia",
     "https://raco.cat/index.php/ActaHistorica/gateway/plugin/WebFeedGatewayPlugin/rss"),
]

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
USER_AGENT = "dh-bot-digest/1 (+https://tei.dh.unibe.ch)"
SUMMARY_CHARS = 600

Fetch = Callable[[str], bytes]


def http_fetch(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_date(text: Optional[str]) -> Optional[datetime]:
    """RFC 822 (RSS) or ISO 8601 (Atom); None when absent or unreadable."""
    if not text or not text.strip():
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _text(el: Optional[ET.Element]) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _strip_html(text: str) -> str:
    out, keep = [], True
    for ch in text:
        if ch == "<":
            keep = False
        elif ch == ">":
            keep = True
        elif keep:
            out.append(ch)
    return " ".join("".join(out).split())


def parse_feed(raw: bytes) -> list[dict]:
    """The entries of one RSS 2.0 or Atom document, dates as UTC datetimes."""
    root = ET.fromstring(raw)
    items: list[dict] = []
    if root.tag == f"{ATOM}feed":
        for e in root.findall(f"{ATOM}entry"):
            link = ""
            for a in e.findall(f"{ATOM}link"):
                if a.get("rel") in (None, "alternate"):
                    link = a.get("href", "")
                    break
            items.append({
                "title": _text(e.find(f"{ATOM}title")),
                "link": link,
                "published": parse_date(_text(e.find(f"{ATOM}published"))
                                        or _text(e.find(f"{ATOM}updated"))),
                "authors": [_text(n) for n in e.findall(f"{ATOM}author/{ATOM}name") if _text(n)],
                "summary": _strip_html(_text(e.find(f"{ATOM}summary"))
                                       or _text(e.find(f"{ATOM}content")))[:SUMMARY_CHARS],
            })
        return items
    channel = root.find("channel")
    if channel is None:
        raise ValueError(f"neither RSS nor Atom: root <{root.tag}>")
    for it in channel.findall("item"):
        authors = [_text(n) for n in it.findall(f"{DC}creator")] or [_text(it.find("author"))]
        items.append({
            "title": _text(it.find("title")),
            "link": _text(it.find("link")) or (it.find("guid").text or "").strip()
            if it.find("guid") is not None else _text(it.find("link")),
            "published": parse_date(_text(it.find("pubDate")) or _text(it.find(f"{DC}date"))),
            "authors": [a for a in authors if a],
            "summary": _strip_html(_text(it.find("description")))[:SUMMARY_CHARS],
        })
    return items


def collect(feeds: list[tuple[str, str]], days: int, *, fetch: Fetch = http_fetch,
            now: Optional[datetime] = None, per_source: int = 5) -> dict:
    """Items published within ``days`` of ``now``, newest first, at most
    ``per_source`` per feed; feeds that failed under ``errors``."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    out: dict = {"generated_at": now.isoformat(timespec="seconds"), "days": days,
                 "items": [], "errors": []}
    for label, url in feeds:
        try:
            entries = parse_feed(fetch(url))
        except (urllib.error.URLError, OSError, ET.ParseError, ValueError) as exc:
            out["errors"].append({"source": label, "url": url,
                                  "error": f"{type(exc).__name__}: {exc}"})
            continue
        fresh = [e for e in entries if e["published"] is not None and e["published"] >= since]
        fresh.sort(key=lambda e: e["published"], reverse=True)
        for e in fresh[:per_source]:
            out["items"].append({"source": label, **e,
                                 "published": e["published"].isoformat(timespec="seconds")})
    return out


def read_feeds_file(path: Path) -> list[tuple[str, str]]:
    """``label<TAB>url`` or just ``url`` per line; ``#`` comments."""
    feeds = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        label, _, url = line.rpartition("\t")
        feeds.append((label or url, url))
    return feeds


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--feeds", type=Path, help="label<TAB>url per line; default: the 17 built in")
    p.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    p.add_argument("--per-source", type=int, default=5)
    args = p.parse_args(argv)
    feeds = read_feeds_file(args.feeds) if args.feeds else DEFAULT_FEEDS
    result = collect(feeds, args.days, fetch=http_fetch, per_source=args.per_source)
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"{len(result['items'])} items from {len(feeds) - len(result['errors'])}/"
              f"{len(feeds)} feeds -> {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
