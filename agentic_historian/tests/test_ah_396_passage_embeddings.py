"""Q2: passage-level embeddings and semantic search (#396).

Keyword search over 0.22-CER transcriptions misses what it should find — the
scribe's orthography and the recogniser's errors defeat exact strings twice
over. `semantic.py` embeds whole documents, on the fly, never persisted, and a
whole document is the wrong unit: a 2400-character page that mentions alms once
is mostly about something else.

Offline throughout. The embedder is a seam and the fake is hash-based, so the
same text always gets the same vector and "did the cache hit" is answerable
without a network. Run from the repo root:
    pytest agentic_historian/tests/test_ah_396_passage_embeddings.py
"""

import hashlib
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import passage_embeddings as pe  # noqa: E402
import passage_index as pi  # noqa: E402


DIM = 16


class Fake:
    """A deterministic embedder. Each text's vector is derived from its own
    hash, so two texts are near-orthogonal and a text is always itself —
    which is what makes a cache hit observable."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        return [self.vector(t) for t in texts]

    @staticmethod
    def vector(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = np.frombuffer(digest[:DIM], dtype=np.uint8).astype(np.float32)
        return (raw / 255.0).tolist()

    @property
    def texts(self) -> list[str]:
        return [t for call in self.calls for t in call]


class Steered:
    """An embedder whose geometry a test can state: `near` texts all land on one
    axis, everything else on another. Lets ranking be asserted without claiming
    anything about a real model's space."""

    def __init__(self, near: tuple[str, ...]):
        self.near = near

    def __call__(self, texts):
        out = []
        for t in texts:
            v = [0.0] * DIM
            v[0 if any(n in t for n in self.near) else 1] = 1.0
            out.append(v)
        return out


def row(normalised, *, doc_id="doc-a", context="", entity_type="CARE_ACTION",
        start=0):
    return {"doc_id": doc_id, "page": 1, "entity_type": entity_type,
            "text": normalised, "normalised": normalised,
            "char_start": start, "char_end": start + len(normalised),
            "context": context}


@pytest.fixture
def index(tmp_path):
    pi._reset_test_db(tmp_path / "passages.db")
    try:
        yield pi
    finally:
        pi._teardown_test_db()


def seed(rows, doc_id="doc-a"):
    pi.upsert_passages(doc_id, rows)
    return pi.query_passages(doc_id=doc_id)


# ── what gets embedded ──────────────────────────────────────────────────────

def test_the_context_window_is_what_is_embedded():
    """A passage's own `text` is a word or two, and two words will not surface
    for a query sharing none of their characters — which is exactly what the
    live check asks for. The term is prepended so the thing the passage is
    *about* is in the vector even when the window is mostly surrounding prose.
    """
    text, basis = pe.passage_text(
        row("almosen", context="der vogt gab almosen den armen lüten"))

    assert basis == "context"
    assert text.startswith("almosen")
    assert "armen lüten" in text


def test_a_passage_with_no_window_says_so_rather_than_ranking_badly():
    """Still findable, just weakly. `basis` is what makes that visible instead
    of a mystery about why it never ranks."""
    text, basis = pe.passage_text(row("almosen"))

    assert (text, basis) == ("almosen", "text")


def test_a_passage_with_nothing_to_embed_is_empty_not_a_guess():
    assert pe.passage_text({}) == ("", "text")
    assert pe.passage_text({"normalised": "   ", "context": "  "}) == ("", "text")


def test_the_hash_is_of_the_text_not_the_passage():
    """Two passages with the same window are one embedding; a passage whose
    window changed is a different one."""
    same = pe.passage_text(row("almosen", context="gab almosen"))[0]
    also = pe.passage_text(row("almosen", doc_id="doc-b", start=99,
                               context="gab almosen"))[0]
    other = pe.passage_text(row("almosen", context="gab almuosen"))[0]

    assert pe.content_hash(same) == pe.content_hash(also)
    assert pe.content_hash(same) != pe.content_hash(other)


# ── persistence ─────────────────────────────────────────────────────────────

def test_a_vector_round_trips(index):
    vec = [0.5, -0.25, 1.0] + [0.0] * 13

    pe.store_vector("abc", vec)

    got = pe.vector_for("abc")
    assert got is not None
    assert np.allclose(got, np.asarray(vec, dtype=np.float32))


def test_a_vector_survives_a_reconnect(index, tmp_path):
    """Persisted, not held in memory: the point of the store is that the next
    run does not re-embed."""
    pe.store_vector("abc", Fake.vector("almosen"))
    pi._teardown_test_db()
    pi._reset_test_db(tmp_path / "passages.db")

    assert pe.vector_for("abc") is not None


def test_an_absent_vector_is_none_not_a_zero_vector(index):
    assert pe.vector_for("never-stored") is None


def test_a_truncated_blob_reads_as_absent(index):
    """A half-read vector ranks confidently and wrongly, so `dim` is kept
    beside the blob and a mismatch is treated as no vector at all."""
    pe.store_vector("abc", [1.0] * DIM)
    db = pe._db()
    db.execute("UPDATE passage_vectors SET dim = ? WHERE content_hash = ?",
               (DIM + 5, "abc"))
    db.commit()

    assert pe.vector_for("abc") is None


# ── the model is part of the key ────────────────────────────────────────────

def test_the_same_text_under_another_model_is_another_vector(index):
    """#396 asks for a content hash so a re-run does not re-embed unchanged
    text. But the same text under a different embedding model is a vector in a
    different space, and serving one for the other would compare numbers that
    do not belong together and return a plausible ranking."""
    pe.store_vector("abc", [1.0] * DIM, model="qwen3-embedding-0.6b")

    assert pe.vector_for("abc", model="qwen3-embedding-0.6b") is not None
    assert pe.vector_for("abc", model="some-other-embedder") is None


def test_switching_models_re_embeds(index, monkeypatch):
    rows = seed([row("almosen", context="gab almosen den armen")])
    fake = Fake()
    pe.embed_passages(rows, embedder=fake, model="model-a")
    assert len(fake.texts) == 1

    again = pe.embed_passages(rows, embedder=fake, model="model-b")

    assert again == pe.Embedded(cached=0, embedded=1, skipped=0)
    assert len(fake.texts) == 2


def test_the_count_is_per_model(index):
    pe.store_vector("a", [1.0] * DIM, model="model-a")
    pe.store_vector("b", [1.0] * DIM, model="model-a")
    pe.store_vector("c", [1.0] * DIM, model="model-b")

    assert pe.vector_count(model="model-a") == 2
    assert pe.vector_count(model="model-b") == 1


# ── the cache, which is the point of persisting ────────────────────────────

def test_a_second_run_embeds_nothing(index):
    rows = seed([row("almosen", context="gab almosen den armen lüten"),
                 row("vogt", context="der vogt zu Kungsfelt", start=50,
                     entity_type="ROLE")])
    fake = Fake()

    first = pe.embed_passages(rows, embedder=fake)
    second = pe.embed_passages(rows, embedder=fake)

    assert first == pe.Embedded(cached=0, embedded=2, skipped=0)
    assert second == pe.Embedded(cached=2, embedded=0, skipped=0)
    assert len(fake.calls) == 1, "the second run made no call at all"


def test_only_the_new_passage_is_embedded_on_a_later_run(index):
    rows = seed([row("almosen", context="gab almosen den armen")])
    fake = Fake()
    pe.embed_passages(rows, embedder=fake)

    rows = seed([row("almosen", context="gab almosen den armen"),
                 row("vaganten", context="die vaganten vor dem tor", start=60,
                     entity_type="SOCIAL_GROUP")])
    second = pe.embed_passages(rows, embedder=fake)

    assert second == pe.Embedded(cached=1, embedded=1, skipped=0)
    assert fake.calls[-1] == ["vaganten — die vaganten vor dem tor"]


def test_one_text_repeated_is_embedded_once(index):
    """A term recurring across a holding often carries the same window text.
    Embedding it twenty times would be twenty times the cost for one vector."""
    same = "gab almosen den armen lüten"
    rows = seed([row("almosen", context=same),
                 row("almosen", context=same, start=40),
                 row("almosen", context=same, start=80)])
    fake = Fake()

    out = pe.embed_passages(rows, embedder=fake)

    assert len(fake.texts) == 1
    assert out.embedded == 1


def test_a_passage_with_nothing_to_embed_is_skipped_and_counted(index):
    rows = seed([row("almosen", context="gab almosen")])
    # Appended rather than seeded: a row with nothing to embed is what a
    # caller can hand `embed_passages` directly, and the store is not the only
    # source of rows.
    rows.append({"doc_id": "doc-a", "normalised": "", "context": "",
                 "entity_type": "ROLE"})
    fake = Fake()

    out = pe.embed_passages(rows, embedder=fake)

    assert out.skipped == 1
    assert out.total == len(rows)


# ── batching ────────────────────────────────────────────────────────────────

def test_the_calls_are_batched_not_one_per_passage(index):
    rows = seed([row(f"term{i:03d}", context=f"window number {i}", start=i * 20)
                 for i in range(10)])
    fake = Fake()

    pe.embed_passages(rows, embedder=fake, batch=4)

    assert [len(c) for c in fake.calls] == [4, 4, 2]
    assert len(fake.texts) == 10


def test_the_default_batch_sends_one_call_for_a_small_corpus(index):
    rows = seed([row(f"t{i}", context=f"w{i}", start=i * 10) for i in range(20)])
    fake = Fake()

    pe.embed_passages(rows, embedder=fake)

    assert len(fake.calls) == 1


def test_a_failed_batch_does_not_cost_the_others(index):
    rows = seed([row(f"t{i}", context=f"window {i}", start=i * 20)
                 for i in range(6)])
    calls = {"n": 0}

    def flaky(texts):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("gateway 502")
        return [Fake.vector(t) for t in texts]

    out = pe.embed_passages(rows, embedder=flaky, batch=3)

    assert out.embedded == 3
    assert calls["n"] == 2


def test_a_batch_that_returns_the_wrong_count_is_dropped_whole(index):
    """The one way a batch can corrupt the store. Vectors returned in a
    different count cannot be matched to their texts by position, and pairing
    them anyway would attach meanings to the wrong passages."""
    rows = seed([row(f"t{i}", context=f"window {i}", start=i * 20)
                 for i in range(3)])

    out = pe.embed_passages(rows, embedder=lambda ts: [Fake.vector(ts[0])])

    assert out.embedded == 0
    assert pe.vector_count() == 0


# ── search ──────────────────────────────────────────────────────────────────

def test_the_nearest_passage_ranks_first(index):
    rows = seed([row("almosen", context="der vogt gab almosen den armen lüten"),
                 row("mauer", context="die mauer am tor ward gebessert",
                     start=60, entity_type="ROLE")])
    # "armen" is in the query and in the alms window, and in neither the mauer
    # window nor its query — so the two that belong together land on one axis.
    near = Steered(near=("armen",))
    pe.embed_passages(rows, embedder=near)

    hits = pe.search("wer versorgte die armen", embedder=near)

    assert [h.passage["normalised"] for h in hits][0] == "almosen"
    assert hits[0].score > hits[-1].score
    assert hits[0].scored_by == "cosine"


def test_top_k_bounds_the_result(index):
    rows = seed([row(f"t{i}", context=f"window {i}", start=i * 20)
                 for i in range(8)])
    fake = Fake()
    pe.embed_passages(rows, embedder=fake)

    assert len(pe.search("almosen", top_k=3, embedder=fake)) == 3


def test_a_passage_with_no_vector_is_left_out_not_ranked_at_zero(index):
    """Zero is a similarity. A row that was never embedded has none, and
    ranking it at zero would place it above everything genuinely dissimilar."""
    rows = seed([row("almosen", context="gab almosen den armen"),
                 row("mauer", context="die mauer am tor", start=60)])
    fake = Fake()
    pe.embed_passages([rows[0]], embedder=fake)      # only one of the two

    hits = pe.search("armen", embedder=fake)

    assert [h.passage["normalised"] for h in hits] == ["almosen"]


def test_an_empty_query_returns_nothing(index):
    rows = seed([row("almosen", context="gab almosen")])
    fake = Fake()
    pe.embed_passages(rows, embedder=fake)

    assert pe.search("", embedder=fake) == []
    assert pe.search("   ", embedder=fake) == []


def test_a_failed_query_embedding_returns_nothing_rather_than_raising(index):
    rows = seed([row("almosen", context="gab almosen")])
    pe.embed_passages(rows, embedder=Fake())

    def broken(texts):
        raise RuntimeError("gateway down")

    assert pe.search("armen", embedder=broken) == []


def test_an_empty_index_returns_nothing(index):
    assert pe.search("armen", embedder=Fake()) == []


# ── filter and rank compose ────────────────────────────────────────────────

def test_a_type_filter_narrows_before_ranking(index):
    rows = seed([row("almosen", context="gab almosen den armen"),
                 row("vogt", context="der vogt gab almosen", start=60,
                     entity_type="ROLE")])
    fake = Fake()
    pe.embed_passages(rows, embedder=fake)

    hits = pe.search("almosen", entity_type="ROLE", embedder=fake)

    assert [h.passage["normalised"] for h in hits] == ["vogt"]


def test_a_document_filter_narrows_before_ranking(index):
    pi.upsert_passages("Marbach__01", [row("almosen", doc_id="Marbach__01",
                                           context="gab almosen den armen")])
    pi.upsert_passages("Inzigkofen__01", [row("almosen", doc_id="Inzigkofen__01",
                                              context="gab almosen den armen")])
    fake = Fake()
    pe.embed_passages(embedder=fake)

    hits = pe.search("armen", doc_id="Marbach__01", embedder=fake)

    assert [h.passage["doc_id"] for h in hits] == ["Marbach__01"]


def test_a_doc_prefix_selects_a_holding(index):
    """How a holding is actually selected while the schema has no `bestand`
    column — see `FILTERABLE`."""
    for doc in ("Marbach__01", "Marbach__02", "Inzigkofen__01"):
        pi.upsert_passages(doc, [row("almosen", doc_id=doc,
                                     context=f"gab almosen in {doc}")])
    fake = Fake()
    pe.embed_passages(embedder=fake)

    hits = pe.search("almosen", doc_prefix="Marbach__", embedder=fake)

    assert len(hits) == 2
    assert all(h.passage["doc_id"].startswith("Marbach__") for h in hits)


def test_top_k_counts_rows_that_passed_the_filter(index):
    """Filtered first, ranked second. Otherwise `top_k` would count rows that
    happened to rank well before the filter and return fewer than asked."""
    rows = [row("almosen", context="gab almosen den armen")]
    rows += [row(f"r{i}", context=f"role window {i}", start=50 + i * 20,
                 entity_type="ROLE") for i in range(5)]
    seeded = seed(rows)
    fake = Fake()
    pe.embed_passages(seeded, embedder=fake)

    hits = pe.search("almosen", entity_type="ROLE", top_k=3, embedder=fake)

    assert len(hits) == 3
    assert {h.passage["entity_type"] for h in hits} == {"ROLE"}


def test_the_filter_names_only_what_the_schema_has():
    """#396 asks to filter by "the Q1 dimensions (type, bestand, date range)".
    Only the first exists: #395's table has no `bestand` column and no date at
    all. Named rather than silently dropped."""
    columns = {c for c in pe.FILTERABLE if c != "doc_prefix"}

    assert columns <= {"doc_id", "page", "entity_type", "text", "normalised",
                       "char_start", "char_end", "context", "hub_id",
                       "gnd_id", "hls_id", "created_at"}
    assert "bestand" not in pe.FILTERABLE
    assert not any("date" in f for f in pe.FILTERABLE)


# ── the reranker ───────────────────────────────────────────────────────────

def test_the_reranker_reorders_the_shortlist(index):
    rows = seed([row("almosen", context="gab almosen den armen"),
                 row("mauer", context="die mauer am tor", start=60)])
    fake = Fake()
    pe.embed_passages(rows, embedder=fake)
    before = [h.passage["normalised"] for h in pe.search("armen", embedder=fake)]

    def reversing(query, documents, top_n=3):
        return [{"index": i, "score": 1.0 - i * 0.1}
                for i in reversed(range(len(documents)))]

    after = [h.passage["normalised"] for h in
             pe.search("armen", embedder=fake, rerank=True,
                       reranker=reversing)]

    assert after == list(reversed(before))
    assert before != after, "the fixture must actually reorder"


def test_a_reranked_hit_says_it_was_reranked(index):
    """A reranked list and a cosine list are not the same evidence."""
    rows = seed([row("almosen", context="gab almosen den armen")])
    fake = Fake()
    pe.embed_passages(rows, embedder=fake)

    hits = pe.search("armen", embedder=fake, rerank=True,
                     reranker=lambda q, d, top_n=3: [{"index": 0, "score": 0.9}])

    assert hits[0].scored_by == "rerank"
    assert hits[0].score == pytest.approx(0.9)


def test_a_failed_rerank_degrades_to_the_cosine_order(index):
    rows = seed([row("almosen", context="gab almosen den armen"),
                 row("mauer", context="die mauer am tor", start=60)])
    fake = Fake()
    pe.embed_passages(rows, embedder=fake)
    cosine = [h.passage["normalised"] for h in pe.search("armen", embedder=fake)]

    def broken(query, documents, top_n=3):
        raise RuntimeError("no reranker served")

    hits = pe.search("armen", embedder=fake, rerank=True, reranker=broken)

    assert [h.passage["normalised"] for h in hits] == cosine
    assert all(h.scored_by == "cosine" for h in hits)


def test_an_empty_rerank_reply_degrades_rather_than_emptying_the_result(index):
    rows = seed([row("almosen", context="gab almosen den armen")])
    fake = Fake()
    pe.embed_passages(rows, embedder=fake)

    hits = pe.search("armen", embedder=fake, rerank=True,
                     reranker=lambda q, d, top_n=3: [])

    assert len(hits) == 1
    assert hits[0].scored_by == "cosine"


def test_a_rerank_index_out_of_range_is_ignored(index):
    rows = seed([row("almosen", context="gab almosen den armen")])
    fake = Fake()
    pe.embed_passages(rows, embedder=fake)

    hits = pe.search("armen", embedder=fake, rerank=True,
                     reranker=lambda q, d, top_n=3: [{"index": 99, "score": 1.0},
                                                     {"index": 0, "score": 0.5}])

    assert len(hits) == 1 and hits[0].passage["normalised"] == "almosen"


# ── no rerank unless asked ─────────────────────────────────────────────────

def test_rerank_is_off_by_default(index):
    rows = seed([row("almosen", context="gab almosen den armen")])
    fake = Fake()
    pe.embed_passages(rows, embedder=fake)
    called = {"n": 0}

    def counting(query, documents, top_n=3):
        called["n"] += 1
        return []

    pe.search("armen", embedder=fake, reranker=counting)

    assert called["n"] == 0


# ── the sweep: vectors nothing can reach any more ──────────────────────────

def embedded(rows, embedder=None):
    pe.embed_passages(rows, embedder=embedder or Fake())


def test_a_fresh_store_has_nothing_to_sweep(index):
    rows = seed([row("almosen", context="gab almosen den armen")])
    embedded(rows)

    assert pe.sweep() == pe.Sweep(live=1, orphans=0, stale=0, deleted=0)


def test_a_deleted_passage_leaves_an_orphan(index):
    """The gap this closes. Vectors are keyed by `(content_hash, model)` and
    passages by document, so deleting a document's passages leaves its vectors
    behind — not wrong, unreachable."""
    rows = seed([row("almosen", context="gab almosen den armen"),
                 row("vogt", context="der vogt zu Kungsfelt", start=60)])
    embedded(rows)

    seed([row("almosen", context="gab almosen den armen")])   # vogt goes

    found = pe.sweep()
    assert (found.live, found.orphans) == (1, 1)
    assert found.deleted == 0, "a report must not delete"


def test_sweeping_removes_the_orphan_and_keeps_the_rest(index):
    rows = seed([row("almosen", context="gab almosen den armen"),
                 row("vogt", context="der vogt zu Kungsfelt", start=60)])
    embedded(rows)
    seed([row("almosen", context="gab almosen den armen")])
    live_hash = pe.content_hash(
        pe.passage_text(row("almosen", context="gab almosen den armen"))[0])

    done = pe.sweep(delete=True)

    assert done.deleted == 1
    assert pe.vector_count() == 1
    assert pe.vector_for(live_hash) is not None


def test_a_changed_context_window_orphans_the_old_vector(index):
    """The other way a vector goes unreachable: the passage is still there but
    its window changed, so it hashes to something else."""
    embedded(seed([row("almosen", context="gab almosen den armen")]))

    seed([row("almosen", context="gab almuosen den armen lüten")])
    embedded(pi.query_passages(doc_id="doc-a"))

    found = pe.sweep()
    assert (found.live, found.orphans) == (1, 1)


def test_the_report_is_the_default(index):
    """A maintenance function whose default is destructive gets run by accident
    exactly once."""
    rows = seed([row("almosen", context="gab almosen"),
                 row("vogt", context="der vogt", start=40)])
    embedded(rows)
    seed([row("almosen", context="gab almosen")])

    pe.sweep()

    assert pe.vector_count() == 2, "nothing was removed"


# ── orphan and stale are different facts ──────────────────────────────────

def test_a_vector_from_another_model_is_stale_not_orphaned(index):
    """The text is still live; the vector belongs to a model that is not
    running. Dead weight only until somebody switches back — and switching back
    would otherwise re-embed the corpus."""
    rows = seed([row("almosen", context="gab almosen den armen")])
    embedded(rows)
    text = pe.passage_text(rows[0])[0]
    pe.store_vector(pe.content_hash(text), [0.5] * DIM, model="older-embedder")

    found = pe.sweep()

    assert (found.live, found.stale, found.orphans) == (1, 1, 0)


def test_a_default_sweep_leaves_the_other_model_alone(index):
    rows = seed([row("almosen", context="gab almosen den armen")])
    embedded(rows)
    text_hash = pe.content_hash(pe.passage_text(rows[0])[0])
    pe.store_vector(text_hash, [0.5] * DIM, model="older-embedder")

    done = pe.sweep(delete=True)

    assert done.deleted == 0
    assert pe.vector_for(text_hash, model="older-embedder") is not None


def test_drop_stale_removes_it_and_says_so(index):
    rows = seed([row("almosen", context="gab almosen den armen")])
    embedded(rows)
    text_hash = pe.content_hash(pe.passage_text(rows[0])[0])
    pe.store_vector(text_hash, [0.5] * DIM, model="older-embedder")

    done = pe.sweep(delete=True, drop_stale=True)

    assert done.deleted == 1
    assert pe.vector_for(text_hash, model="older-embedder") is None
    assert pe.vector_for(text_hash) is not None, "the running model's vector stays"


def test_an_orphan_of_another_model_is_an_orphan_not_stale(index):
    """Unreachable beats "not the running model": no passage carries this text,
    so switching models back would not make it usable either."""
    embedded(seed([row("almosen", context="gab almosen")]))
    pe.store_vector(pe.content_hash("a window no passage has"), [0.5] * DIM,
                    model="older-embedder")

    found = pe.sweep()

    assert (found.orphans, found.stale) == (1, 0)


# ── the footgun ───────────────────────────────────────────────────────────

def test_an_empty_index_refuses_rather_than_wiping_the_store(index):
    """A passage index that is empty — a wrong DATA_DIR, a test path left set,
    a database not yet written — makes *every* vector an orphan, and the sweep
    would correctly, by its own logic, delete a corpus of embeddings that cost
    hours. So that is a question about the configuration, not an answer about
    the vectors."""
    rows = seed([row("almosen", context="gab almosen den armen")])
    embedded(rows)

    seed([])                                   # the index loses everything

    done = pe.sweep(delete=True)

    assert done.deleted == 0
    assert done.refused and "DATA_DIR" in done.refused
    assert pe.vector_count() == 1, "the store survived"


def test_the_refusal_still_reports_the_counts(index):
    """Refusing to act is not refusing to answer: the counts are what tell you
    whether the index or the store is the surprising one."""
    embedded(seed([row("almosen", context="gab almosen")]))
    seed([])

    done = pe.sweep(delete=True)

    assert done.orphans == 1 and done.total == 1


def test_an_empty_index_with_an_empty_store_is_not_a_refusal(index):
    """Nothing to protect, so nothing to refuse — otherwise a clean install
    would report a configuration problem it does not have."""
    done = pe.sweep(delete=True)

    assert done.refused is None
    assert done == pe.Sweep()


def test_a_report_against_an_empty_index_is_not_refused(index):
    """Only deleting is dangerous. Counting is how you diagnose the very
    situation the refusal is about."""
    embedded(seed([row("almosen", context="gab almosen")]))
    seed([])

    found = pe.sweep()

    assert found.refused is None and found.orphans == 1


# ── what counts as reachable ──────────────────────────────────────────────

def test_reachability_is_defined_by_what_gets_embedded(index):
    """`live_hashes` goes through `passage_text`, because that function *is*
    the definition of what gets embedded. Computed any other way, the two could
    disagree and the sweep would delete vectors `search` still wants."""
    rows = seed([row("almosen", context="gab almosen den armen"),
                 row("vogt", start=60)])          # no window: term only

    found = pe.live_hashes(rows)

    assert pe.content_hash("almosen — gab almosen den armen") in found
    assert pe.content_hash("vogt") in found
    assert len(found) == 2


def test_a_passage_with_nothing_to_embed_claims_no_hash(index):
    assert pe.live_hashes([{"normalised": "", "context": ""}]) == set()


def test_two_passages_sharing_a_window_claim_one_hash(index):
    same = "gab almosen den armen lüten"
    rows = [row("almosen", context=same), row("almosen", context=same, start=90)]

    assert len(pe.live_hashes(rows)) == 1


def test_the_swept_store_still_answers_a_search(index):
    """The point of all of it: sweeping must not break retrieval."""
    rows = seed([row("almosen", context="der vogt gab almosen den armen lüten"),
                 row("mauer", context="die mauer am tor", start=60)])
    near = Steered(near=("armen",))
    pe.embed_passages(rows, embedder=near)
    seed([row("almosen", context="der vogt gab almosen den armen lüten")])

    pe.sweep(delete=True)
    hits = pe.search("wer versorgte die armen", embedder=near)

    assert [h.passage["normalised"] for h in hits] == ["almosen"]
