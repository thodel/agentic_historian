"""Q3: /find — reaching the passage index where the historians work (#397).

Q1 made passages addressable, Q2 searchable by meaning. Both are useless if the
only way in is a Python prompt on tei.

Offline throughout: the index is a temp SQLite file, the embedder is the
hash-based fake from the Q2 tests, and the renderer is a pure function returning
messages — so what a historian would see is checked without a Discord client,
as `atr_status.format_gpu_views` already allows.

Run from the repo root:
    pytest agentic_historian/tests/test_ah_397_find.py
"""

import hashlib
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import pytest  # noqa: E402

import agent_tools  # noqa: E402
import passage_embeddings as pe  # noqa: E402
import passage_find as pf  # noqa: E402
import passage_index as pi  # noqa: E402

DIM = 16


class Fake:
    """Deterministic embedder: each text's vector comes from its own hash."""

    def __call__(self, texts):
        return [self.vector(t) for t in texts]

    @staticmethod
    def vector(text: str):
        import numpy as np

        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return (np.frombuffer(digest[:DIM], dtype=np.uint8)
                .astype("float32") / 255.0).tolist()


class Steered:
    """An embedder whose geometry a test can state."""

    def __init__(self, *near):
        self.near = near

    def __call__(self, texts):
        out = []
        for t in texts:
            v = [0.0] * DIM
            v[0 if any(n in t for n in self.near) else 1] = 1.0
            out.append(v)
        return out


def passage(normalised, *, doc_id="Marbach__01", bestand="Marbach",
            entity_type="CARE_ACTION", context="", start=0, **extra):
    return {"doc_id": doc_id, "page": 1, "bestand": bestand,
            "entity_type": entity_type, "text": normalised,
            "normalised": normalised, "char_start": start,
            "char_end": start + len(normalised), "context": context, **extra}


@pytest.fixture
def index(tmp_path, monkeypatch):
    pi._reset_test_db(tmp_path / "passages.db")
    monkeypatch.setattr(pf.config, "CATALOGUE_BASE_URL", "", raising=False)
    monkeypatch.setattr(pf.config, "GITHUB_OUTPUT_REPO",
                        "thodel/agentic-historian-outputs", raising=False)
    try:
        yield pi
    finally:
        pi._teardown_test_db()


def seed(rows, doc_id=None):
    for row in rows:
        pi.upsert_passages(doc_id or row["doc_id"],
                           [r for r in rows if r["doc_id"] == (doc_id or row["doc_id"])])
    pe.embed_passages(embedder=Fake())
    return pi.query_passages()


# ── the anchor, which is a contract with the catalogue build ──────────────

def test_the_anchor_is_the_span():
    """`/find` deep-links into a catalogue built in another repository. If each
    side computed its own anchor they would agree until the day they did not,
    and the symptom would be a link that scrolls to the top of a long page —
    which looks like a working link."""
    assert pf.anchor_for(passage("almosen", start=120)) == "passage-120-127"


def test_two_entities_on_one_span_share_an_anchor():
    """Right, not a collision: the anchor names a *place in the text*, which is
    what a reader following a link wants to see. It is not an entity id."""
    role = passage("vogt", start=40, entity_type="ROLE")
    group = passage("vogt", start=40, entity_type="SOCIAL_GROUP")

    assert pf.anchor_for(role) == pf.anchor_for(group)


def test_a_passage_without_offsets_has_no_anchor():
    """A passage with no verbatim position (#466) has no place to point at, and
    an anchor resolving to nothing is a link that silently lands at the top."""
    assert pf.anchor_for({"char_start": None, "char_end": None}) is None
    assert pf.anchor_for({}) is None


# ── the catalogue url ────────────────────────────────────────────────────

def test_the_base_is_derived_from_the_output_repo(index):
    assert pf.catalogue_base() == "https://thodel.github.io/agentic-historian-outputs"


def test_a_custom_domain_overrides_the_convention(monkeypatch):
    """A wrong base makes every link wrong at once, so it is configurable."""
    monkeypatch.setattr(pf.config, "CATALOGUE_BASE_URL",
                        "https://quellen.example.org", raising=False)

    assert pf.catalogue_base() == "https://quellen.example.org"
    assert pf.catalogue_url("doc-a") == "https://quellen.example.org/doc-a/"


def test_the_url_carries_the_anchor(index):
    url = pf.catalogue_url("Marbach__01", anchor="passage-10-17")

    assert url.endswith("/Marbach__01/#passage-10-17")


def test_no_base_means_no_link(monkeypatch):
    """A relative link in a Discord message is not a link."""
    monkeypatch.setattr(pf.config, "CATALOGUE_BASE_URL", "", raising=False)
    monkeypatch.setattr(pf.config, "GITHUB_OUTPUT_REPO", "", raising=False)

    assert pf.catalogue_url("doc-a") is None


# ── searching ────────────────────────────────────────────────────────────

def test_the_nearest_passage_comes_first(index, monkeypatch):
    rows = [passage("almosen", context="der vogt gab almosen den armen lüten"),
            passage("mauer", context="die mauer am tor", start=80)]
    pi.upsert_passages("Marbach__01", rows)
    near = Steered("armen")
    pe.embed_passages(embedder=near)

    found = pf.find("wer versorgte die armen", embedder=near)

    assert [h.passage["normalised"] for h in found.hits][0] == "almosen"
    assert found.indexed == 2 and found.candidates == 2


def test_a_type_and_a_holding_narrow_the_search(index):
    pi.upsert_passages("Marbach__01", [
        passage("almosen", context="gab almosen"),
        passage("vogt", context="der vogt", start=40, entity_type="ROLE")])
    pi.upsert_passages("Inzigkofen__01", [
        passage("almosen", doc_id="Inzigkofen__01", bestand="Inzigkofen",
                context="gab almosen")])
    pe.embed_passages(embedder=Fake())

    by_type = pf.find("almosen", entity_type="ROLE", embedder=Fake())
    by_held = pf.find("almosen", bestand="Inzigkofen", embedder=Fake())

    assert [h.passage["normalised"] for h in by_type.hits] == ["vogt"]
    assert [h.passage["doc_id"] for h in by_held.hits] == ["Inzigkofen__01"]


def test_a_broken_search_comes_back_empty_rather_than_raising(index):
    """A surface that answers a historian must not hand them a traceback."""
    pi.upsert_passages("Marbach__01", [passage("almosen", context="gab almosen")])

    def broken(texts):
        raise RuntimeError("gateway down")

    found = pf.find("armen", embedder=broken)

    assert found.hits == []
    assert found.indexed == 1


# ── the empty result states its scope ────────────────────────────────────

def test_an_empty_index_says_the_index_is_empty(index):
    """#397: "nichts gefunden" states the searched scope, not just silence.
    Three situations look identical as silence and want three different next
    actions."""
    found = pf.find("almosen", embedder=Fake())

    assert found.hits == []
    assert "Index ist leer" in found.why_empty()
    assert "#467" in found.why_empty()


def test_a_filter_that_matched_nothing_says_so(index):
    pi.upsert_passages("Marbach__01", [passage("almosen", context="gab almosen")])
    pe.embed_passages(embedder=Fake())

    found = pf.find("almosen", bestand="Nowhere", embedder=Fake())

    why = found.why_empty()
    assert "1 Passage(n)" in why and "Nowhere" in why
    assert "leer" not in why, "the index is not empty — that is the point"


def test_passages_that_were_never_embedded_say_that(index):
    """The third situation, and the one whose next action is a command."""
    pi.upsert_passages("Marbach__01", [passage("almosen", context="gab almosen")])

    found = pf.find("almosen", embedder=Fake())

    assert found.candidates == 1 and found.hits == []
    assert "embed_passages" in found.why_empty()
    assert "#396" in found.why_empty()


def test_the_scope_names_every_filter():
    found = pf.Found(query="x", entity_type="ROLE", bestand="Marbach")

    assert "ROLE" in found.scope() and "Marbach" in found.scope()
    assert pf.Found(query="x").scope() == "das ganze Korpus"


def test_the_empty_message_carries_the_reason(index):
    messages = pf.format_found(pf.find("almosen", embedder=Fake()))

    assert len(messages) == 1
    assert "nichts gefunden" in messages[0]
    assert "Index ist leer" in messages[0]


# ── rendering ────────────────────────────────────────────────────────────

def test_a_hit_shows_window_document_and_link(index):
    pi.upsert_passages("Marbach__01", [
        passage("almosen", context="der vogt gab almosen den armen lüten",
                start=12, hub_id="almosen")])
    pe.embed_passages(embedder=Fake())
    found = pf.find("almosen", embedder=Fake())

    rendered = pf.format_hit(found.hits[0])

    assert "almosen" in rendered
    assert "der vogt gab almosen den armen lüten" in rendered
    assert "Marbach__01" in rendered
    assert "#passage-12-19" in rendered
    assert "hub `almosen`" in rendered


def test_a_hit_without_an_anchor_says_so_instead_of_linking_to_the_top(index):
    pi.upsert_passages("Marbach__01", [passage("almosen", context="gab almosen")])
    pe.embed_passages(embedder=Fake())
    found = pf.find("almosen", embedder=Fake())
    found.hits[0].passage["char_start"] = None
    found.hits[0].passage["char_end"] = None

    rendered = pf.format_hit(found.hits[0])

    assert "kein Anker" in rendered
    assert "#passage" not in rendered


def test_a_long_window_is_cut_with_an_ellipsis(index):
    long = "almosen " * 80
    pi.upsert_passages("Marbach__01", [passage("almosen", context=long)])
    pe.embed_passages(embedder=Fake())

    rendered = pf.format_hit(pf.find("almosen", embedder=Fake()).hits[0])

    assert "…" in rendered
    assert len(rendered) < 600


def test_the_header_counts_the_hits_and_the_scope(index):
    pi.upsert_passages("Marbach__01", [
        passage(f"t{i}", context=f"window {i}", start=i * 20) for i in range(3)])
    pe.embed_passages(embedder=Fake())

    messages = pf.format_found(pf.find("almosen", bestand="Marbach",
                                       embedder=Fake()))

    assert "3 Treffer" in messages[0] and "Marbach" in messages[0]


def test_the_unverified_link_caveat_is_stated_once(index):
    """Whether a catalogue page exists is not knowable from the passage index:
    it says what was recognised, not what was published."""
    pi.upsert_passages("Marbach__01", [passage("almosen", context="gab almosen")])
    pe.embed_passages(embedder=Fake())

    messages = pf.format_found(pf.find("almosen", embedder=Fake()))

    assert sum("nicht \nveröffentlichtes" in m or "veröffentlichtes" in m
               for m in messages) == 1


def test_a_caller_that_knows_what_is_published_gets_no_caveat(index):
    pi.upsert_passages("Marbach__01", [passage("almosen", context="gab almosen")])
    pe.embed_passages(embedder=Fake())
    found = pf.find("almosen", embedder=Fake())

    messages = pf.format_found(found, published={"Marbach__01"})

    assert not any("veröffentlichtes" in m for m in messages)
    assert any("thodel.github.io" in m for m in messages)


def test_an_unpublished_document_is_named_without_a_link(index):
    pi.upsert_passages("Marbach__01", [passage("almosen", context="gab almosen")])
    pe.embed_passages(embedder=Fake())
    found = pf.find("almosen", embedder=Fake())

    rendered = pf.format_hit(found.hits[0], published=set())

    assert "`Marbach__01`" in rendered
    assert "thodel.github.io" not in rendered


# ── pagination ───────────────────────────────────────────────────────────

def test_a_page_holds_five_hits(index):
    pi.upsert_passages("Marbach__01", [
        passage(f"t{i:02d}", context=f"window number {i}", start=i * 30)
        for i in range(12)])
    pe.embed_passages(embedder=Fake())

    found = pf.find("almosen", embedder=Fake())

    assert len(found.hits) == 12
    assert found.pages == 3
    assert len(found.on_page) == 5


def test_the_second_page_continues_where_the_first_stopped(index):
    pi.upsert_passages("Marbach__01", [
        passage(f"t{i:02d}", context=f"window number {i}", start=i * 30)
        for i in range(12)])
    pe.embed_passages(embedder=Fake())

    first = pf.find("almosen", page=1, embedder=Fake())
    second = pf.find("almosen", page=2, embedder=Fake())

    # By name, not by object: `Hit` has no `__eq__`, so comparing instances
    # from two separate calls compares identities and never matches.
    names = [h.passage["normalised"] for h in first.hits]
    assert [h.passage["normalised"] for h in first.on_page] == names[:5]
    assert [h.passage["normalised"] for h in second.on_page] == names[5:10]


def test_the_header_says_which_hits_are_shown(index):
    pi.upsert_passages("Marbach__01", [
        passage(f"t{i:02d}", context=f"window {i}", start=i * 30)
        for i in range(12)])
    pe.embed_passages(embedder=Fake())

    messages = pf.format_found(pf.find("almosen", page=2, embedder=Fake()))

    assert "6–10" in messages[0]
    assert "Seite 2/3" in messages[0]


def test_a_page_past_the_end_says_so_rather_than_showing_nothing(index):
    pi.upsert_passages("Marbach__01", [passage("almosen", context="gab almosen")])
    pe.embed_passages(embedder=Fake())

    messages = pf.format_found(pf.find("almosen", page=9, embedder=Fake()))

    assert "Seite 9" in messages[0] and "leer" in messages[0]


def test_one_message_per_hit_plus_the_header(index):
    """Discord caps a message, so the formatter returns messages and the
    command loops — the splitting stays testable without a client."""
    pi.upsert_passages("Marbach__01", [
        passage(f"t{i}", context=f"window {i}", start=i * 30) for i in range(3)])
    pe.embed_passages(embedder=Fake())

    messages = pf.format_found(pf.find("almosen", embedder=Fake()),
                               published={"Marbach__01"})

    assert len(messages) == 1 + 3


# ── the NL orchestrator tool ─────────────────────────────────────────────

def test_the_search_is_registered_as_an_agent_tool():
    """So "finde Stellen zu X" routes here — the retrieval half of
    Scholar-in-the-Loop (#397)."""
    tool = agent_tools.get_tool("find_passages")

    assert tool.fn_name == "run_find_passages"
    assert tool.parameters["query"]["required"] is True
    assert not tool.parameters["bestand"]["required"]


def test_the_tool_resolves_on_the_orchestrator():
    """`agent_tools` resolves every tool as an attribute of `orchestrator`, so
    a registry entry without one there is a registry entry that cannot run."""
    import orchestrator

    assert callable(getattr(orchestrator, "run_find_passages"))


def test_the_tool_returns_plain_data(index, monkeypatch):
    """The NL orchestrator feeds a tool result back to a model, and a `Hit`
    object would arrive as a repr."""
    import json

    import orchestrator

    pi.upsert_passages("Marbach__01", [
        passage("almosen", context="gab almosen den armen", start=5)])
    pe.embed_passages(embedder=Fake())
    # The real seam: `pe.search` reaches for the GPUStack client when no
    # embedder is passed, and the tool passes none — it is called by a model,
    # not by a test. Patching the client is what makes the tool's own path
    # testable; patching `pe.search` from inside a lambda that calls it is
    # infinite recursion, which is how I wrote this the first time.
    from utils import gpustack_client as gs

    monkeypatch.setattr(gs, "embed", Fake())

    out = orchestrator.run_find_passages("almosen")

    json.dumps(out)                      # must survive serialisation
    assert out["total"] == 1
    assert out["passages"][0]["char_start"] == 5
    assert out["passages"][0]["url"].endswith("#passage-5-12")


def test_the_tool_explains_an_empty_result(index):
    import orchestrator

    out = orchestrator.run_find_passages("almosen")

    assert out["total"] == 0
    assert "Index ist leer" in out["why_empty"]


def test_the_tool_rejects_an_unknown_parameter():
    """The registry validates before invoking, so a model that invents an
    argument gets an error rather than a silently ignored filter."""
    with pytest.raises(ValueError):
        agent_tools.call_tool("find_passages", query="x", bestandd="Marbach")
