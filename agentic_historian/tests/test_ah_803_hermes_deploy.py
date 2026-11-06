"""#803 — the Hermes bundle under deploy/hermes: the two cron scripts and the skills.

The OpenClaw cron jobs were all failing (#797); what replaces them must be
testable without tei. These pin:

* ``digest_fetch.py`` reads RSS 2.0 and Atom, keeps only the last N days, at
  most five per source, newest first, and lists a feed that failed under
  ``errors`` instead of dropping it silently;
* ``snsf_check.sh`` prints nothing when nothing changed (a silent cron tick),
  creates the baseline on the first run, and reports every changed field —
  and only then — while updating the baseline;
* every skill has the frontmatter Hermes needs and a unique name; the bundle
  holds no value that looks like a secret.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BUNDLE = REPO / "deploy" / "hermes"
DIGEST = BUNDLE / "skills" / "dh-digest" / "scripts" / "digest_fetch.py"
SNSF = BUNDLE / "scripts" / "snsf_check.sh"

sys.path.insert(0, str(DIGEST.parent))
sys.dont_write_bytecode = True      # no __pycache__ in deploy/ (repo hygiene, #113)
import digest_fetch as df  # noqa: E402

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc)


def _rss(*items: tuple[str, datetime]) -> bytes:
    body = "".join(
        f"<item><title>{t}</title><link>https://x/{i}</link>"
        f"<pubDate>{d.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
        f"<dc:creator>Autor {i}</dc:creator><description>&lt;p&gt;Text {i}&lt;/p&gt;</description></item>"
        for i, (t, d) in enumerate(items))
    return (f'<?xml version="1.0"?><rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<channel><title>f</title>{body}</channel></rss>").encode()


def _atom(*items: tuple[str, datetime]) -> bytes:
    body = "".join(
        f"<entry><title>{t}</title><link rel=\"alternate\" href=\"https://a/{i}\"/>"
        f"<published>{d.isoformat()}</published><author><name>A {i}</name></author>"
        f"<summary>S {i}</summary></entry>" for i, (t, d) in enumerate(items))
    return f'<feed xmlns="http://www.w3.org/2005/Atom"><title>f</title>{body}</feed>'.encode()


# ── digest_fetch ─────────────────────────────────────────────────────────────

def test_rss_and_atom_are_read_and_only_the_window_is_kept():
    feeds = {"https://r": _rss(("alt", NOW - timedelta(days=9)), ("neu", NOW - timedelta(days=2))),
             "https://a": _atom(("atom-alt", NOW - timedelta(days=8)),
                                ("atom-neu", NOW - timedelta(hours=5)))}
    out = df.collect([("R", "https://r"), ("A", "https://a")], 7, fetch=feeds.__getitem__, now=NOW)
    assert [(i["source"], i["title"]) for i in out["items"]] == [("R", "neu"), ("A", "atom-neu")]
    assert out["errors"] == []
    rss = out["items"][0]
    assert rss["authors"] == ["Autor 1"] and rss["summary"] == "Text 1"
    assert rss["published"] == (NOW - timedelta(days=2)).isoformat(timespec="seconds")
    assert out["items"][1]["link"] == "https://a/1"


def test_at_most_five_per_source_newest_first():
    items = tuple((f"t{i}", NOW - timedelta(days=i)) for i in range(7))
    out = df.collect([("R", "u")], 7, fetch=lambda u: _rss(*items), now=NOW)
    assert [i["title"] for i in out["items"]] == ["t0", "t1", "t2", "t3", "t4"]


def test_a_failing_feed_is_listed_not_dropped():
    def fetch(url):
        if url == "down":
            raise OSError("connection refused")
        if url == "junk":
            return b"<html>not a feed</html>"
        return _rss(("ok", NOW))
    out = df.collect([("D", "down"), ("J", "junk"), ("G", "good")], 7, fetch=fetch, now=NOW)
    assert [i["source"] for i in out["items"]] == ["G"]
    assert [(e["source"], e["error"].split(":")[0]) for e in out["errors"]] == [
        ("D", "OSError"), ("J", "ValueError")]


def _rdf(*items: tuple[str, datetime]) -> bytes:
    """RSS 1.0 as OpenEdition and OJS serve it: items beside the channel, dc:date."""
    body = "".join(
        f'<item rdf:about="https://r1/{i}"><title>{t}</title><link>https://r1/{i}</link>'
        f"<dc:creator>C {i}</dc:creator><dc:date>{d.isoformat()}</dc:date>"
        f"<description>D {i}</description></item>" for i, (t, d) in enumerate(items))
    return (b'<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            b'xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            b"<channel rdf:about=\"https://r1\"><title>f</title><items/></channel>" + body.encode()
            + b"</rdf:RDF>")


def test_rss_one_point_zero_is_read_too():
    """Critical Digital Humanities and Histoire & mesure were the two 'errors' of
    the first live digest (04.11.2026): rdf:RDF, not rss."""
    out = df.collect([("H", "u")], 7, fetch=lambda u: _rdf(("alt", NOW - timedelta(days=30)),
                                                           ("neu", NOW - timedelta(days=1))),
                     now=NOW)
    assert out["errors"] == []
    assert [(i["title"], i["link"], i["authors"]) for i in out["items"]] == [
        ("neu", "https://r1/1", ["C 1"])]


def test_an_atom_link_without_rel_is_the_link():
    """Google Alert feeds write <link href="…"/> with no rel attribute."""
    raw = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>t</title>'
           b'<link href="https://alert/1"/><published>' + NOW.isoformat().encode()
           + b"</published></entry></feed>")
    assert df.collect([("A", "u")], 7, fetch=lambda u: raw, now=NOW)["items"][0]["link"] == "https://alert/1"


def test_an_item_without_a_date_is_not_a_new_item():
    raw = (b'<rss version="2.0"><channel><item><title>undated</title>'
           b"<link>https://x</link></item></channel></rss>")
    assert df.collect([("R", "u")], 7, fetch=lambda u: raw, now=NOW)["items"] == []


def test_the_cli_writes_the_json(tmp_path, monkeypatch):
    feeds = tmp_path / "feeds.txt"
    feeds.write_text("# comment\nMeine Quelle\thttps://r\n")
    monkeypatch.setattr(df, "http_fetch", lambda url, timeout=20.0: _rss(("x", datetime.now(timezone.utc))))
    out = tmp_path / "out.json"
    assert df.main(["--feeds", str(feeds), "--out", str(out), "--days", "3"]) == 0
    data = json.loads(out.read_text())
    assert data["days"] == 3 and data["items"][0]["source"] == "Meine Quelle"


def test_the_default_feeds_are_the_seventeen_of_the_openclaw_job():
    assert len(df.DEFAULT_FEEDS) == 17
    assert sum("google.com/alerts" in u for _, u in df.DEFAULT_FEEDS) == 4


# ── snsf_check.sh ────────────────────────────────────────────────────────────

@pytest.fixture
def fake_curl(tmp_path):
    """A `curl` that answers headers (-I) and a body from files the test controls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        "# -I/-sSIL answers with the headers; anything else is the body, to -o FILE or stdout\n"
        "all=\" $* \"; out=; while [ $# -gt 0 ]; do case \"$1\" in -o) out=$2; shift;; esac; shift; done\n"
        "case \"$all\" in *\" -sSIL \"*) cat \"$FAKE_HEADERS\";; "
        "*) if [ -n \"$out\" ]; then cat \"$FAKE_BODY\" > \"$out\"; else cat \"$FAKE_BODY\"; fi;; esac\n")
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    headers, body = tmp_path / "h.txt", tmp_path / "b.csv"

    def run(last_modified="Tue, 30 Jun 2026 07:08:45 GMT", length="426180169",
            first_line="GrantNumber;Title;Abstract"):
        headers.write_text(f"HTTP/2 200\r\nlast-modified: {last_modified}\r\n"
                           f"content-length: {length}\r\ncontent-type: text/csv\r\n\r\n")
        # 64 KiB and more, as the real range request returns: `head -2` closes
        # the pipe long before the writer is done, which is what broke the
        # first live run (curl exit 23, EPIPE) — a three-line body hides it.
        body.write_text(first_line + "\n1;x;y\n" + "".join(f"{i};z;w\n" for i in range(20_000)))
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
               "FAKE_HEADERS": str(headers), "FAKE_BODY": str(body),
               "SNSF_BASELINE": str(tmp_path / "base.json")}
        return subprocess.run(["bash", str(SNSF)], env=env, capture_output=True, text=True)

    run.baseline = tmp_path / "base.json"
    return run


def test_the_first_run_creates_the_baseline_and_says_so(fake_curl):
    r = fake_curl()
    assert r.returncode == 0 and "Baseline angelegt" in r.stdout
    base = json.loads(fake_curl.baseline.read_text())
    assert base["content_length"] == "426180169" and len(base["header_hash"]) == 64


def test_nothing_changed_is_silent(fake_curl):
    fake_curl()
    before = fake_curl.baseline.read_text()
    r = fake_curl()
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    after = json.loads(fake_curl.baseline.read_text())
    assert {k: v for k, v in after.items() if k != "checked_at"} == \
        {k: v for k, v in json.loads(before).items() if k != "checked_at"}


def test_a_change_is_reported_with_every_changed_field_and_the_baseline_moves(fake_curl):
    fake_curl()
    r = fake_curl(last_modified="Mon, 28 Sep 2026 06:00:00 GMT", length="430000000")
    assert "hat sich geändert" in r.stdout
    assert "last_modified: Tue, 30 Jun 2026 07:08:45 GMT → Mon, 28 Sep 2026 06:00:00 GMT" in r.stdout
    assert "content_length: 426180169 → 430000000" in r.stdout
    assert "Grösse: +3,819,831 Bytes" in r.stdout
    assert "header_hash" not in r.stdout, "the header line did not change"
    assert json.loads(fake_curl.baseline.read_text())["content_length"] == "430000000"
    again = fake_curl(last_modified="Mon, 28 Sep 2026 06:00:00 GMT", length="430000000")
    assert again.stdout == "", "the new state is the baseline now"


def test_the_hash_covers_the_header_and_the_first_row(fake_curl):
    """'header + first 2 lines' in the OpenClaw job: a changed first row is a change."""
    fake_curl()
    r = fake_curl(first_line="GrantNumber;Title;Abstract\n1;CHANGED;y")
    assert "header_hash" in r.stdout


def test_a_changed_header_line_alone_is_a_change(fake_curl):
    fake_curl()
    r = fake_curl(first_line="GrantNumber;Title;Abstract;Keywords")
    assert "header_hash" in r.stdout and "content_length" not in r.stdout


# ── the skills ───────────────────────────────────────────────────────────────

def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    import yaml
    return yaml.safe_load(text.split("---\n", 2)[1])


def test_every_skill_has_what_hermes_needs_and_a_unique_name():
    skills = sorted((BUNDLE / "skills").glob("*/SKILL.md"))
    assert len(skills) == 3
    names = []
    for path in skills:
        fm = _frontmatter(path)
        assert fm["name"] == path.parent.name
        assert fm["description"] and fm["version"]
        assert fm["metadata"]["hermes"]["blueprint"]["schedule"]
        names.append(fm["name"])
    assert len(set(names)) == 3


def test_the_digest_skill_calls_the_script_it_ships():
    text = (BUNDLE / "skills" / "dh-digest" / "SKILL.md").read_text()
    assert "${HERMES_SKILL_DIR}/scripts/digest_fetch.py" in text
    assert DIGEST.is_file()


def test_the_queue_skill_keeps_the_protocol_rules():
    text = (BUNDLE / "skills" / "dh-bot-queue" / "SKILL.md").read_text()
    for rule in ("Teil von #", "500 changed lines", "never `git add -A`", "claude/",
                 "No merge, no approve", "--remove-label dh-bot", "Hand-back"):
        assert rule in text, rule
    assert "/home/dh" not in text, "the worker must not know the production checkouts"


def test_no_skill_asks_hermes_for_a_token_it_refuses_to_pass():
    # The first cron run (2026-11-05): "refusing to register Hermes provider
    # credential 'GH_TOKEN'". gh is signed in on its own; a skill that declares
    # the variable only logs a warning per run and misleads the next operator.
    for path in sorted((BUNDLE / "skills").glob("*/SKILL.md")):
        fm = _frontmatter(path)
        declared = [v["name"] for v in fm.get("required_environment_variables", [])]
        assert not [v for v in declared if v.endswith(("_TOKEN", "_KEY"))], (path, declared)
    assert "GH_TOKEN=" not in (BUNDLE / "env.example").read_text()


def test_the_cron_run_keeps_deny_mode_and_allows_exactly_what_the_worker_needs():
    import yaml
    cfg = yaml.safe_load((BUNDLE / "config.yaml.example").read_text())
    assert cfg["approvals"]["cron_mode"] == "deny"
    assert cfg["command_allowlist"] == ["script execution via heredoc", "git branch force delete"]
    atr = cfg["mcp_servers"]["atr"]
    assert atr["auth"] == "oauth"
    include = atr["tools"]["include"]                      # the documented key; include_tools is ignored
    assert "read_pages" in include and "ubelix_queue" in include
    assert not {"start_batch", "stop_job", "kraken_train", "upload_hf"} & set(include)
    skill = (BUNDLE / "skills" / "dh-bot-queue" / "SKILL.md").read_text()
    assert "`execute_code` is blocked" in skill
    assert "<<'EOF'" in skill


def test_bots_md_lists_every_slash_command_of_the_historian():
    # docs/BOTS.md (H5, #801) names each Historian command with its gate; a new
    # command without a row is a boundary nobody decided.
    bot = (REPO / "agentic_historian" / "bot.py").read_text(encoding="utf-8")
    names = set(re.findall(r'slash_command\((?:[^)]|\n)*?name="([a-z_]+)"', bot))
    assert len(names) >= 30
    doc = (REPO / "docs" / "BOTS.md").read_text(encoding="utf-8")
    missing = sorted(n for n in names if f"| `/{n}` |" not in doc)
    assert not missing, missing
    assert "`start_batch`" in doc and "lesend" in doc
    rules = (BUNDLE / "agents-boundaries.md").read_text(encoding="utf-8")
    for phrase in ("fehlt absichtlich", "biete nicht an", "Lesen kostet"):
        assert phrase in rules, phrase


def test_the_bundle_holds_no_secret_values():
    pattern = re.compile(r"(ghp_[A-Za-z0-9]{20,}|MT[A-Za-z0-9]{22}\.[A-Za-z0-9_-]{6}\.|gpustack_[A-Za-z0-9]{16,}"
                         r"|(?i:token|key|secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9+/_-]{24,})")
    files = [p for p in BUNDLE.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    assert len(files) >= 9
    for path in files:
        assert not pattern.search(path.read_text(encoding="utf-8")), path
