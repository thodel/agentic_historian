"""
semantic.py — embedding-based semantic retrieval + clustering over the corpus (#28).

Uses the GPUStack embedding model (config.GPUSTACK_MODEL_EMBEDDING via
gpustack_client.embed) to embed documents, then supports cosine-similarity search
and **reproducible** k-means clustering for topic/care grouping, with LLM-generated
cluster labels (Agent D / Epic 6).

Acceptance (#28): clusters are reproducible (deterministic, seeded); labels via LLM.
"""

from __future__ import annotations

import numpy as np
from loguru import logger

from utils import gpustack_client as gs


def embed_corpus(docs: dict[str, str]) -> dict[str, list[float]]:
    """Embed ``{doc_id: text}`` → ``{doc_id: vector}`` (skips empty texts)."""
    ids = [d for d, t in docs.items() if (t or "").strip()]
    if not ids:
        return {}
    vectors = gs.embed([docs[i] for i in ids])
    return {i: v for i, v in zip(ids, vectors)}


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na and nb else 0.0


def search(query: str, corpus: dict[str, list[float]], top_k: int = 5) -> list[tuple[str, float]]:
    """Rank corpus docs by cosine similarity to ``query`` (embedded on the fly)."""
    if not corpus:
        return []
    qv = np.asarray(gs.embed([query])[0], dtype=float)
    scored = [(doc_id, _cosine(qv, np.asarray(vec, dtype=float)))
              for doc_id, vec in corpus.items()]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def kmeans(X: np.ndarray, k: int, seed: int = 0, iters: int = 100) -> np.ndarray:
    """Deterministic k-means → cluster label per row. Seeded init makes the
    result reproducible for the same input and seed (#28 acceptance)."""
    n = len(X)
    k = max(1, min(k, n))
    rng = np.random.default_rng(seed)
    centroids = X[rng.choice(n, size=k, replace=False)].astype(float).copy()
    labels = np.full(n, -1)
    for _ in range(iters):
        dist = ((X[:, None, :] - centroids[None, :, :]) ** 2).sum(-1)
        new = dist.argmin(1)
        if np.array_equal(new, labels):
            break
        labels = new
        for c in range(k):
            members = X[labels == c]
            if len(members):
                centroids[c] = members.mean(0)
    return labels


def cluster_corpus(corpus: dict[str, list[float]], k: int, seed: int = 0) -> dict[str, int]:
    """Cluster embedded docs → ``{doc_id: cluster_id}`` (reproducible for a seed)."""
    if not corpus:
        return {}
    ids = list(corpus)
    X = np.asarray([corpus[i] for i in ids], dtype=float)
    labels = kmeans(X, k, seed=seed)
    return {ids[i]: int(labels[i]) for i in range(len(ids))}


def label_cluster(texts: list[str], max_chars: int = 1600) -> str:
    """LLM label (3–6 words) summarising a cluster's shared theme."""
    joined = "\n---\n".join((t or "")[:400] for t in texts)[:max_chars]
    if not joined.strip():
        return ""
    prompt = ("Fasse das gemeinsame Thema dieser historischen Textausschnitte in "
              "3–6 Wörtern zusammen. Antworte NUR mit dem Label:\n\n" + joined)
    try:
        return gs.chat_text(prompt, system=None, max_tokens=40).strip()
    except Exception as e:
        logger.warning(f"[semantic] cluster label failed: {e}")
        return ""


def cluster_and_label(docs: dict[str, str], k: int, seed: int = 0) -> dict[int, dict]:
    """End-to-end: embed → cluster → label. Returns
    ``{cluster_id: {"doc_ids": [...], "label": str}}``."""
    corpus = embed_corpus(docs)
    assignment = cluster_corpus(corpus, k, seed=seed)
    clusters: dict[int, dict] = {}
    for doc_id, cid in assignment.items():
        clusters.setdefault(cid, {"doc_ids": [], "label": ""})["doc_ids"].append(doc_id)
    for cid, info in clusters.items():
        info["label"] = label_cluster([docs[d] for d in info["doc_ids"]])
    return clusters
