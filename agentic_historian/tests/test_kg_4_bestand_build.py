"""KG-4 phase 2 (#330): the Bestandsgraph for QLever on idhefix, in one command.

Offline: the exporters are replaced by a runner that writes a part and its
manifest the way ``_common`` does; what is pinned is what ``bestand_build``
decides around them — what is planned, what is skipped and said so, when
nothing is rebuilt, that a failure swaps nothing in, and that the endpoint
check compares the served count with the build. Plus the deploy files: no
token in the repository, loopback only, the nginx admin guard.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
REPO = PKG.parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from knowledge_hub import bestand_build as bb   # noqa: E402

QDIR = REPO / "deploy" / "qlever"


def _dbs(tmp_path: Path, names=("dodis", "kf", "mls", "sikart", "bullinger", "escher")) -> Path:
    d = tmp_path / "mcp-data"
    d.mkdir()
    for n in names:
        (d / f"{n}.db").write_bytes(b"SQLite " + n.encode())
    return d


def _env(tmp_path: Path, **extra) -> dict:
    # The holdings' tests run without the Human Lineage layer; it has its own
    # section below (#830).
    env = {"KG_SOURCE_DB_DIR": str(_dbs(tmp_path)), "KG_HUMANLINEAGE": "0"}
    reg = tmp_path / "ssrq__fuseki_042810.ttl"
    reg.write_text("# register\n", encoding="utf-8")
    env["KG_SSRQ_REGISTER"] = str(reg)
    env.update(extra)
    return env


class FakeRunner:
    """Writes ``<out>`` and ``<out>.manifest.json`` like an exporter; fails for
    the sources in ``fail``."""

    def __init__(self, fail=(), triples=None):
        self.calls: list[list[str]] = []
        self.fail = set(fail)
        self.triples = triples or {}

    def __call__(self, argv: list[str]) -> int:
        self.calls.append(argv)
        source = argv[argv.index("-m") + 1].rsplit(".", 1)[-1]
        if source in self.fail:
            return 1
        if "--graph" in argv:                       # a layer reads the staged holdings
            for p in argv[argv.index("--graph") + 1:argv.index("--out")]:
                assert Path(p).exists(), p
        out = Path(argv[argv.index("--out") + 1])
        n = self.triples.get(source, 10)
        out.write_text("".join(f"<urn:{source}:{i}> <urn:p> <urn:o> .\n" for i in range(n)),
                       encoding="utf-8")
        Path(str(out) + ".manifest.json").write_text(json.dumps(
            {"source": source, "triples": n, "built_at": "2026-11-16T03:17:00+00:00",
             "counts": {"documents": n}, "withheld": {"dangling": 2 if source == "kf" else 0}}),
            encoding="utf-8")
        return 0


# ── plan ─────────────────────────────────────────────────────────────────────

def test_the_plan_names_every_holding_and_what_is_missing(tmp_path):
    env = {"KG_SOURCE_DB_DIR": str(_dbs(tmp_path, names=("dodis", "kf")))}
    planned = {p.name: p for p in bb.plan(env)}
    assert list(planned) == ["dodis", "kf", "ssrq", "mls", "sikart", "bullinger", "escher"]
    assert planned["dodis"].runnable and planned["dodis"].argv[0] == "--db"
    assert planned["ssrq"].missing == ["KG_SSRQ_REGISTER not set"]
    assert any("mls.db missing" in m for m in planned["mls"].missing)
    assert "humanlineage" not in planned and "corpus" not in planned     # a layer, not a holding; corpus opt-in


def test_ssrq_tei_is_optional_and_a_db_can_be_pointed_elsewhere(tmp_path):
    other = tmp_path / "elsewhere.db"
    other.write_bytes(b"x")
    env = _env(tmp_path, KG_DODIS_DB=str(other), KG_SSRQ_TEI=str(tmp_path / "no-such-dir"))
    planned = {p.name: p for p in bb.plan(env)}
    assert planned["dodis"].argv == ["--db", str(other)]
    assert planned["ssrq"].runnable and "--tei" not in planned["ssrq"].argv
    tei = tmp_path / "editio-data"
    tei.mkdir()
    planned = {p.name: p for p in bb.plan({**env, "KG_SSRQ_TEI": str(tei)})}
    assert planned["ssrq"].argv[-2:] == ["--tei", str(tei)]


# ── build ────────────────────────────────────────────────────────────────────

def test_a_build_runs_each_exporter_and_swaps_the_parts_in(tmp_path):
    env = _env(tmp_path)
    run = FakeRunner(triples={"dodis": 30, "kf": 5})
    out = tmp_path / "kg"
    m = bb.build(out, env=env, commit="abc1234", runner=run, python="py")
    assert m["status"] == "built"
    assert len(run.calls) == 7
    assert run.calls[0][:3] == ["py", "-m", "knowledge_hub.sources.dodis"]
    assert run.calls[0][-2:] == ["--commit", "abc1234"]
    assert sorted(p.name for p in (out / "parts").glob("*.nt")) == sorted(
        f"{s}.nt" for s in ("dodis", "kf", "ssrq", "mls", "sikart", "bullinger", "escher"))
    assert m["triples"] == 30 + 5 + 5 * 10
    kf = next(r for r in m["sources"] if r["source"] == "kf")
    assert kf["withheld"] == {"dangling": 2}
    assert not (out / "parts.new").exists()
    assert bb.read_manifest(out)["fingerprint"] == m["fingerprint"]


def test_unchanged_inputs_build_nothing_and_a_changed_one_rebuilds(tmp_path):
    env = _env(tmp_path)
    out = tmp_path / "kg"
    bb.build(out, env=env, commit="abc", runner=FakeRunner())
    again = FakeRunner()
    assert bb.build(out, env=env, commit="abc", runner=again)["status"] == "unchanged"
    assert again.calls == []
    assert bb.build(out, env=env, commit="def", runner=again)["status"] == "built"   # new code
    db = Path(env["KG_SOURCE_DB_DIR"]) / "kf.db"
    db.write_bytes(b"SQLite kf, a newer ingest")
    os.utime(db, ns=(db.stat().st_atime_ns, db.stat().st_mtime_ns + 10**9))
    third = FakeRunner()
    assert bb.build(out, env=env, commit="def", runner=third)["status"] == "built"
    assert len(third.calls) == 7
    assert bb.build(out, env=env, commit="def", runner=FakeRunner(), force=True)["status"] == "built"


def test_a_failed_exporter_swaps_nothing_and_keeps_the_served_graph(tmp_path):
    env = _env(tmp_path)
    out = tmp_path / "kg"
    first = bb.build(out, env=env, commit="abc", runner=FakeRunner())
    served = (out / "parts" / "dodis.nt").read_text(encoding="utf-8")
    m = bb.build(out, env=env, commit="new", runner=FakeRunner(fail={"ssrq"}, triples={"dodis": 99}))
    assert m["status"] == "failed" and "ssrq" in m["reason"]
    assert (out / "parts" / "dodis.nt").read_text(encoding="utf-8") == served
    assert bb.read_manifest(out)["fingerprint"] == first["fingerprint"]
    assert not (out / "parts.new").exists()


def test_a_holding_that_shrinks_by_half_fails_unless_allowed(tmp_path):
    env = _env(tmp_path)
    out = tmp_path / "kg"
    bb.build(out, env=env, commit="a", runner=FakeRunner(triples={"dodis": 100}))
    m = bb.build(out, env=env, commit="b", runner=FakeRunner(triples={"dodis": 49}))
    assert m["status"] == "failed" and "dodis" in m["reason"]
    row = next(r for r in m["sources"] if r["source"] == "dodis")
    assert "shrank from 100 to 49" in row["reason"]
    assert bb.build(out, env=env, commit="b", runner=FakeRunner(triples={"dodis": 50}))["status"] == "built"
    assert bb.build(out, env=env, commit="c", runner=FakeRunner(triples={"dodis": 10}),
                    allow_shrink=True)["status"] == "built"


def test_a_missing_source_is_skipped_by_name_and_nothing_at_all_is_a_failure(tmp_path):
    env = _env(tmp_path)
    os.remove(Path(env["KG_SOURCE_DB_DIR"]) / "mls.db")
    m = bb.build(tmp_path / "kg", env=env, commit="abc", runner=FakeRunner())
    mls = next(r for r in m["sources"] if r["source"] == "mls")
    assert m["status"] == "built" and mls["status"] == "skipped" and "mls.db missing" in mls["reason"]
    assert "mls" in bb.render(m) and "skipped" in bb.render(m)
    empty = bb.build(tmp_path / "kg2", env={"KG_SOURCE_DB_DIR": str(tmp_path / "nowhere")},
                     commit="abc", runner=FakeRunner())
    assert empty["status"] == "failed" and not (tmp_path / "kg2" / "parts").exists()


def test_the_corpus_graph_joins_as_sorted_ntriples(tmp_path):
    ttl = tmp_path / "corpus.ttl"
    body = "".join(f'ex:s{i:02d} ex:p "{i}" .\n' for i in range(40, 0, -1))
    ttl.write_text("@prefix ex: <urn:ex:> .\n" + body, encoding="utf-8")
    env = _env(tmp_path, KG_CORPUS_TTL=str(ttl))
    out = tmp_path / "kg"
    m = bb.build(out, env=env, commit="abc", runner=FakeRunner())
    lines = (out / "parts" / "corpus.nt").read_text(encoding="utf-8").splitlines()
    assert lines == sorted(lines) and len(lines) == 40 and lines[0].startswith("<urn:ex:s01>")
    assert next(r for r in m["sources"] if r["source"] == "corpus")["triples"] == 40
    gone = bb.plan({**env, "KG_CORPUS_TTL": str(tmp_path / "absent.ttl")})
    assert next(p for p in gone if p.name == "corpus").missing


# ── the endpoint check and the CLI ───────────────────────────────────────────

def test_the_check_compares_the_served_count_with_the_build(tmp_path):
    out = tmp_path / "kg"
    assert bb.check_endpoint(out, "http://x/", count=lambda e: 1)[0] is False   # nothing built
    bb.build(out, env=_env(tmp_path), commit="abc", runner=FakeRunner())
    ok, msg = bb.check_endpoint(out, "http://x/", count=lambda e: 70)
    assert ok and "70" in msg
    ok, msg = bb.check_endpoint(out, "http://x/", count=lambda e: 69)
    assert not ok and "stale" in msg

    def down(e):
        raise OSError("connection refused")
    ok, msg = bb.check_endpoint(out, "http://x/", count=down)
    assert not ok and "does not answer" in msg


def test_the_exit_codes_refresh_reads(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path)
    monkeypatch.setattr(bb, "plan", lambda e=None, _env=env, _p=bb.plan: _p(_env))
    monkeypatch.setattr(bb, "_run", FakeRunner())
    monkeypatch.setattr(bb, "_commit", lambda: "abc")
    out = tmp_path / "kg"
    assert bb.main(["--out", str(out), "--status"]) == 1                 # nothing built yet
    assert bb.main(["--out", str(out)]) == 0                              # built → reindex
    assert bb.main(["--out", str(out)]) == 3                              # unchanged → nothing
    assert bb.main(["--out", str(out), "--status"]) == 0
    assert "triples" in capsys.readouterr().out


# ── the deploy files ─────────────────────────────────────────────────────────

def test_no_access_token_in_the_repository_and_loopback_only():
    compose = (QDIR / "docker-compose.bestand.yml").read_text(encoding="utf-8")
    assert "QLEVER_ACCESS_TOKEN:?" in compose                 # required, no default
    assert "ah-corpus-admin" not in compose
    assert '"127.0.0.1:7019:7019"' in compose
    assert "index.new" in compose and "parts/*.nt" in compose
    refresh = (QDIR / "refresh_bestand.sh").read_text(encoding="utf-8")
    assert "bestand_build" in refresh and "--check http://127.0.0.1:7019/" in refresh
    assert 'mv "$KG_DIR/index.new" "$KG_DIR/index"' in refresh
    assert "QLEVER_ACCESS_TOKEN" in refresh


def test_nginx_refuses_admin_parameters_and_strips_the_bearer_header():
    conf = (QDIR / "nginx-qlever.conf").read_text(encoding="utf-8")
    m = re.search(r'if \(\$args ~\* "([^"]+)"\) \{ return 403; \}', conf)
    assert m
    rx = re.compile(m.group(1), re.I)
    for bad in ("access-token=x", "query=q&cmd=clear-cache", "access_token=x&query=q"):
        assert rx.search(bad), bad
    for ok in ("query=SELECT%20*", "query=x&format=json", "query=commander"):
        assert not rx.search(ok), ok
    assert 'proxy_set_header   Authorization     "";' in conf


def test_the_units_run_the_refresh_and_keep_the_tunnel_up():
    unit = (QDIR / "systemd" / "qlever-refresh.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=%h/.config/qlever/env" in unit and "refresh_bestand.sh" in unit
    assert "OnCalendar=" in (QDIR / "systemd" / "qlever-refresh.timer").read_text(encoding="utf-8")
    tunnel = (QDIR / "systemd" / "sparql-tunnel.service").read_text(encoding="utf-8")
    assert "-L 127.0.0.1:7019:127.0.0.1:7019" in tunnel
    assert "ExitOnForwardFailure=yes" in tunnel and "BatchMode=yes" in tunnel
    assert "Restart=always" in tunnel


# ── the Human Lineage layer: its own part, its own graph, its own licence (#830) ──

HL_GRAPH = "https://tei.dh.unibe.ch/id/graph/humanlineage"


def test_the_layer_is_built_after_the_holdings_from_their_staged_parts(tmp_path):
    run = FakeRunner(triples={"humanlineage": 4})
    out = tmp_path / "kg"
    m = bb.build(out, env=_env(tmp_path, KG_HUMANLINEAGE="1"), commit="abc", runner=run, python="py")
    assert m["status"] == "built" and len(run.calls) == 8
    hl = run.calls[-1]
    assert hl[:3] == ["py", "-m", "knowledge_hub.sources.humanlineage"]
    read = [Path(p).name for p in hl[hl.index("--graph") + 1:hl.index("--out")]]
    assert read == ["kf.nt", "ssrq.nt", "mls.nt", "sikart.nt", "bullinger.nt", "escher.nt"]  # no Dodis
    assert hl[-2:] == ["--commit", "abc"]
    assert (out / "parts" / "humanlineage.nt.graph").read_text().strip() == HL_GRAPH
    assert not (out / "parts" / "kf.nt.graph").exists()            # holdings: default graph
    row = next(r for r in m["sources"] if r["source"] == "humanlineage")
    assert (row["status"], row["triples"], row["licence"]) == ("built", 4, "CC BY-SA 4.0")
    assert m["triples"] == 7 * 10 + 4
    assert m["graphs"][HL_GRAPH]["licence_uri"] == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert "Human Lineage" in m["graphs"][HL_GRAPH]["attribution"]
    assert "humanlineage" not in m and "licence open" not in json.dumps(m)


def test_the_layer_graph_is_in_the_project_namespace():
    from knowledge_hub.bestandsgraph import AH
    assert bb.LAYERS[0].graph == str(AH) + "graph/humanlineage"


def test_the_layer_is_on_by_default_and_can_be_switched_off_or_narrowed(tmp_path):
    assert [layer.name for layer, _ in bb.layers_enabled({})] == ["humanlineage"]
    assert bb.layers_enabled({"KG_HUMANLINEAGE": "off"}) == []
    (_, reads), = bb.layers_enabled({"KG_HUMANLINEAGE_SOURCES": "kf, ssrq"})
    assert reads == ("kf", "ssrq")
    planned = bb.plan(_env(tmp_path))
    assert bb.fingerprint(planned, "c", bb.layers_enabled({})) != bb.fingerprint(planned, "c", [])


def test_a_failed_layer_keeps_its_previous_build_and_never_holds_the_holdings_back(tmp_path):
    env = _env(tmp_path, KG_HUMANLINEAGE="1")
    out = tmp_path / "kg"
    m = bb.build(out, env=env, commit="a", runner=FakeRunner(fail={"humanlineage"}))
    row = next(r for r in m["sources"] if r["source"] == "humanlineage")
    assert m["status"] == "built" and row["status"] == "failed" and "absent" in row["reason"]
    assert not (out / "parts" / "humanlineage.nt").exists() and m["triples"] == 70
    assert HL_GRAPH not in m["graphs"]

    bb.build(out, env=env, commit="b", runner=FakeRunner(triples={"humanlineage": 6}))
    served = (out / "parts" / "humanlineage.nt").read_text(encoding="utf-8")
    m = bb.build(out, env=env, commit="c", runner=FakeRunner(fail={"humanlineage"}, triples={"kf": 12}))
    row = next(r for r in m["sources"] if r["source"] == "humanlineage")
    assert m["status"] == "built" and row["status"] == "kept" and row["triples"] == 6
    assert (out / "parts" / "humanlineage.nt").read_text(encoding="utf-8") == served
    assert (out / "parts" / "humanlineage.nt.graph").read_text().strip() == HL_GRAPH
    assert m["triples"] == 6 * 10 + 12 + 6 and HL_GRAPH in m["graphs"]
    assert "kept" in bb.render(m)


def test_a_failed_holding_builds_no_layer(tmp_path):
    run = FakeRunner(fail={"kf"})
    m = bb.build(tmp_path / "kg", env=_env(tmp_path, KG_HUMANLINEAGE="1"), commit="a", runner=run)
    assert m["status"] == "failed"
    assert not any("humanlineage" in " ".join(c) for c in run.calls)


def test_the_check_wants_the_layer_in_its_own_graph(tmp_path):
    out = tmp_path / "kg"
    bb.build(out, env=_env(tmp_path, KG_HUMANLINEAGE="1"), commit="a",
             runner=FakeRunner(triples={"humanlineage": 4}))
    ok, msg = bb.check_endpoint(out, "http://x/", count=lambda e: 74, count_graph=lambda e, g: 4)
    assert ok, msg
    # indexed into the default graph: the total matches, the graph is empty
    ok, msg = bb.check_endpoint(out, "http://x/", count=lambda e: 74, count_graph=lambda e, g: 0)
    assert not ok and HL_GRAPH in msg and "own graph" in msg


def test_the_index_step_puts_each_part_in_the_graph_its_sidecar_names():
    import yaml
    compose = (QDIR / "docker-compose.bestand.yml").read_text(encoding="utf-8")
    services = yaml.safe_load(compose)["services"]
    index, serve = services["index"]["command"][0], services["qlever"]["command"][0]
    # renamed in QLever: IndexBuilderMain → qlever-index, ServerMain → qlever-server
    assert "qlever-index " in index and "IndexBuilderMain" not in index
    assert serve.startswith("qlever-server ") and "ServerMain" not in serve
    assert '.graph"' in index and '-g "$$g"' in index
    # the image is pinned: an unpinned :latest renamed the binaries under us (17.11.2026)
    for svc in services.values():
        assert re.fullmatch(r"\$\{QLEVER_IMAGE:-adfreiburg/qlever@sha256:[0-9a-f]{64}\}", svc["image"])


def test_humanlineage_takes_the_commit_like_every_exporter():
    import argparse

    from knowledge_hub.sources import humanlineage as hl
    seen = {}
    real = argparse.ArgumentParser.parse_args

    def spy(self, argv=None, namespace=None):
        ns = real(self, argv, namespace)
        seen.update(vars(ns))
        raise SystemExit(0)
    argparse.ArgumentParser.parse_args = spy
    try:
        try:
            hl.main(["--graph", "a.nt", "--out", "b.nt", "--commit", "abc1234"])
        except SystemExit:
            pass
    finally:
        argparse.ArgumentParser.parse_args = real
    assert seen["commit"] == "abc1234"


def test_each_compose_command_is_one_script_for_bash_c():
    """Compose splits a string ``command`` into words; ``bash -c`` then runs only
    the first (``set``) and the index step "succeeds" without indexing anything
    — seen 17.11.2026 with Compose v5. One list element is the whole script."""
    import yaml
    services = yaml.safe_load((QDIR / "docker-compose.bestand.yml").read_text(encoding="utf-8"))["services"]
    for name in ("index", "qlever"):
        svc = services[name]
        assert svc["entrypoint"] == ["bash", "-c"]
        assert isinstance(svc["command"], list) and len(svc["command"]) == 1, name
    assert services["index"]["command"][0].lstrip().startswith("set -euo pipefail")
    assert services["qlever"]["command"][0].startswith("qlever-server ")
