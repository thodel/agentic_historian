"""#28: embedding-based semantic retrieval + reproducible clustering.

Offline — gs.embed and gs.chat_text are mocked with deterministic vectors.
Run from the repo root:
    pytest agentic_historian/tests/test_ah_28_semantic.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import semantic as sem  # noqa: E402

# Two well-separated groups in 2-D: A-cluster near (1,0), B-cluster near (0,1).
_VECS = {
    "a1": [1.0, 0.0], "a2": [0.9, 0.1], "a3": [1.0, 0.05],
    "b1": [0.0, 1.0], "b2": [0.1, 0.9], "b3": [0.05, 1.0],
}


def _embed(monkeypatch, mapping):
    """Mock gs.embed to return the mapped vector per text (text == key)."""
    monkeypatch.setattr(sem.gs, "embed", lambda texts: [mapping[t] for t in texts])


def test_embed_corpus_skips_empty(monkeypatch):
    _embed(monkeypatch, {"x": [1.0, 2.0]})
    out = sem.embed_corpus({"d1": "x", "d2": "   ", "d3": ""})
    assert set(out) == {"d1"} and out["d1"] == [1.0, 2.0]


def test_search_ranks_by_cosine(monkeypatch):
    _embed(monkeypatch, {"q": [1.0, 0.0]})
    ranked = sem.search("q", _VECS, top_k=3)
    assert [d for d, _ in ranked] == ["a1", "a3", "a2"]      # closest to (1,0) first
    assert ranked[0][1] > ranked[-1][1]


def test_clustering_separates_two_groups_reproducibly():
    c1 = sem.cluster_corpus(_VECS, k=2, seed=0)
    c2 = sem.cluster_corpus(_VECS, k=2, seed=0)
    assert c1 == c2                                          # reproducible
    # a* share a cluster; b* share a cluster; the two differ
    assert c1["a1"] == c1["a2"] == c1["a3"]
    assert c1["b1"] == c1["b2"] == c1["b3"]
    assert c1["a1"] != c1["b1"]


def test_kmeans_k_capped_to_n():
    import numpy as np
    labels = sem.kmeans(np.array([[0.0, 0.0], [1.0, 1.0]]), k=5)
    assert len(labels) == 2                                  # no crash when k > n


def test_label_cluster_uses_llm(monkeypatch):
    monkeypatch.setattr(sem.gs, "chat_text", lambda *a, **k: "  Armenfürsorge Basel  ")
    assert sem.label_cluster(["arme lüt ...", "spital ..."]) == "Armenfürsorge Basel"


def test_cluster_and_label_end_to_end(monkeypatch):
    docs = {k: k for k in _VECS}                             # doc text is its own key
    _embed(monkeypatch, {t: _VECS[t] for t in _VECS})        # text == vector key
    monkeypatch.setattr(sem.gs, "chat_text", lambda *a, **k: "Label")
    clusters = sem.cluster_and_label(docs, k=2, seed=0)
    assert len(clusters) == 2
    assert all(c["label"] == "Label" for c in clusters.values())
    all_ids = {d for c in clusters.values() for d in c["doc_ids"]}
    assert all_ids == set(_VECS)
