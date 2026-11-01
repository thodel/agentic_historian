"""Historian pins become a growing reference collection (#776).

When a historian pins the script at Gate 1, Agent B adopts it for that document
(#147) — and afterwards the knowledge is gone: the next document in the same hand
is guessed from scratch. Ferilli & Piscopo (CEUR Vol-4304) work incrementally
with prototypes instead: a misread sign is added as a prototype, and from then on
the system recognises more, without a training run and with a decision that
stays explicable because it rests on named exemplars.

So a pin on ``script`` files the document here as an **exemplar**: the pinned
label, the doc_id, who pinned it and when, what the VLM had said before (for the
leave-one-out comparison), and the features of a sample of its pages. A new
document is held against the collection, and the result is a **suggestion**: the
nearest labels with their distance and the exemplar documents behind them. It
stands beside the VLM's answer (``Schrift.referenz``); it never replaces it.

**The features are a stand-in.** ``page_features`` (#774) sees layout, density
and line pitch, not letterforms. When #773 gives features from line crops, they
go in under another ``kind`` and the collection compares like with like. Whether
the suggestion can stand beside the VLM at all is what :func:`leave_one_out`
measures; until it has, a contradiction is noted and Gate 1 opens only with
``REFERENCE_PINS_GATE=true``.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

import config

__all__ = ["Exemplar", "ReferenceCollection", "Suggestion", "backfill", "leave_one_out",
           "normalise_label"]

#: The feature set the vectors were made with; exemplars of another kind are not compared.
KIND = "page_features_v1"
#: Pages of a document whose features an exemplar keeps.
SAMPLE_PAGES = 5


def _path() -> Path:
    return config.DATA_DIR / "reference_pins.jsonl"


def normalise_label(label: str) -> str:
    """The script family a label names, as the model selector reads it."""
    try:
        from agent_a.model_selector import normalise_script
        return normalise_script(label) or label.strip().casefold()
    except Exception:  # noqa: BLE001 — selector unavailable: compare the text
        return label.strip().casefold()


@dataclass
class Exemplar:
    doc_id: str
    label: str
    user: str
    ts: str
    vectors: list[list[float]]
    pages: list[str]
    vlm_label: str = ""
    kind: str = KIND


@dataclass
class Suggestion:
    """The nearest labels, each with its distance and its exemplar documents."""

    label: str
    distance: float
    exemplar: str
    ranking: list[dict] = field(default_factory=list)

    def as_element(self) -> dict:
        return {"wert": self.label, "abstand": round(self.distance, 3),
                "vorbild": self.exemplar, "rangliste": self.ranking[:3]}


class ReferenceCollection:
    """The exemplars, one per document: a re-pinned document replaces its entry."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or _path()
        self.exemplars: dict[str, Exemplar] = {}
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        ex = Exemplar(**json.loads(line))
                        self.exemplars[ex.doc_id] = ex
                    except (TypeError, ValueError) as exc:
                        logger.warning(f"[reference] unreadable line skipped: {exc}")

    def __len__(self) -> int:
        return len(self.exemplars)

    def add(self, exemplar: Exemplar) -> None:
        self.exemplars[exemplar.doc_id] = exemplar
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(prefix=self.path.name, suffix=".part",
                                       dir=self.path.parent)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            for ex in sorted(self.exemplars.values(), key=lambda e: e.doc_id):
                out.write(json.dumps(asdict(ex), ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def suggest(self, vectors: Sequence[Sequence[float]], *,
                exclude: Iterable[str] = ()) -> Optional[Suggestion]:
        """The nearest label for a document's page vectors, or None without exemplars.

        A document is as far from an exemplar as its closest pair of pages: one
        page in the same hand is evidence, the cover of either is not. Features
        are z-scored over the exemplars and the query together, so that no feature
        outweighs the rest by its scale.
        """
        import numpy as np

        skip = set(exclude)
        pool = [ex for ex in self.exemplars.values()
                if ex.doc_id not in skip and ex.kind == KIND and ex.vectors]
        if not pool or not vectors:
            return None
        q = np.asarray(vectors, dtype=float)
        allv = np.vstack([q] + [np.asarray(ex.vectors, dtype=float) for ex in pool])
        mean, std = allv.mean(axis=0), allv.std(axis=0)
        std = np.where(std > 0, std, 1.0)
        qz = (q - mean) / std
        best: dict[str, tuple[float, str]] = {}
        for ex in pool:
            ez = (np.asarray(ex.vectors, dtype=float) - mean) / std
            d = float(np.sqrt(((qz[:, None, :] - ez[None, :, :]) ** 2).sum(axis=2)).min())
            label = normalise_label(ex.label)
            if label not in best or (d, ex.doc_id) < best[label]:
                best[label] = (d, ex.doc_id)
        ranking = sorted(({"wert": lab, "abstand": round(d, 3), "vorbild": doc}
                          for lab, (d, doc) in best.items()),
                         key=lambda r: (r["abstand"], r["wert"]))
        top = ranking[0]
        label = next(ex.label for ex in pool if ex.doc_id == top["vorbild"])
        return Suggestion(label=label, distance=top["abstand"], exemplar=top["vorbild"],
                          ranking=ranking)


def vectors_for(images: Sequence[str | Path],
                features: Optional[Callable[[Path], list[float]]] = None) -> tuple[list, list]:
    """Features of up to :data:`SAMPLE_PAGES` of the images, at even intervals."""
    import page_sample

    if features is None:
        from page_features import image_features as features
    out, names = [], []
    for i in page_sample.spread(len(images), SAMPLE_PAGES):
        try:
            out.append(list(features(Path(images[i]))))
            names.append(Path(images[i]).name)
        except Exception as exc:  # noqa: BLE001 — an unreadable page is not an exemplar
            logger.debug(f"[reference] {images[i]}: {exc}")
    return out, names


def images_of(state) -> list[str]:
    """The page images a run recorded, on this host: the order's pages, else the one image."""
    paths = state.artifacts.get("page_images") or []
    if not paths and state.artifacts.get("image_path"):
        paths = [state.artifacts["image_path"]]
    return [p for p in paths if p and Path(p).is_file()]


def add_from_pin(state, label: str, *, vlm_label: str = "", user: str = "",
                 collection: Optional[ReferenceCollection] = None,
                 features=None) -> Optional[Exemplar]:
    """File the document as an exemplar for ``label``. None when no image is on this host."""
    images = images_of(state)
    vectors, names = vectors_for(images, features)
    if not vectors:
        logger.info(f"[reference] {state.doc_id}: no page image on this host — "
                    "the pin is not an exemplar")
        return None
    ex = Exemplar(doc_id=state.doc_id, label=str(label), user=user or "",
                  ts=time.strftime("%Y-%m-%dT%H:%M:%S"), vectors=vectors, pages=names,
                  vlm_label=str(vlm_label or ""))
    (collection if collection is not None else ReferenceCollection()).add(ex)
    logger.info(f"[reference] {state.doc_id}: exemplar for {label!r} ({len(vectors)} pages)")
    return ex


def backfill(states: Iterable, *, collection: Optional[ReferenceCollection] = None,
             features=None) -> dict:
    """The collection from the existing ``human_overrides``: every pinned script.

    The VLM's own answer is whatever the run's description still says where it
    is not the historian's; a description that already carries the pin has none.
    Returns counts: added, without an image on this host, without a script pin.
    """
    coll = collection if collection is not None else ReferenceCollection()
    counts = {"added": 0, "no_image": 0, "no_pin": 0}
    for state in states:
        pins = [o for o in state.human_overrides if o.get("field") == "script"
                and o.get("value") is not None]
        if not pins:
            counts["no_pin"] += 1
            continue
        last = pins[-1]
        desc = (state.artifacts.get("description") or {}).get("source_json") or {}
        el = desc.get("Schrift") if isinstance(desc, dict) else None
        vlm = (el.get("wert") if isinstance(el, dict) and el.get("quelle") != "historiker"
               else "") or ""
        added = add_from_pin(state, last["value"], vlm_label=vlm, user=last.get("user") or "",
                             collection=coll, features=features)
        counts["added" if added else "no_image"] += 1
    return counts


def attach(description: Optional[dict], images, *,
           collection: Optional[ReferenceCollection] = None, features=None) -> Optional[dict]:
    """Put the collection's suggestion beside Agent B's script, as ``Schrift.referenz``.

    ``widerspruch`` says whether the suggestion names another script family than
    Agent B. Nothing when the collection is empty or no page could be measured.
    Never raises: a suggestion is an addition, and its failure must not cost the
    description.
    """
    try:
        coll = collection if collection is not None else ReferenceCollection()
        sj = (description or {}).get("source_json")
        if not len(coll) or not isinstance(sj, dict):
            return None
        paths = images if isinstance(images, (list, tuple)) else ([images] if images else [])
        vectors, _ = vectors_for([p for p in paths if p and Path(p).is_file()], features)
        doc_id = (description or {}).get("doc_id", "")
        sugg = coll.suggest(vectors, exclude=[doc_id] if doc_id else ())
        if sugg is None:
            return None
        el = sj.get("Schrift")
        if not isinstance(el, dict):
            el = sj["Schrift"] = {"wert": el} if el else {}
        ref = sugg.as_element()
        wert = el.get("wert")
        ref["widerspruch"] = bool(wert) and normalise_label(str(wert)) != normalise_label(sugg.label)
        el["referenz"] = ref
        return ref
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[reference] no suggestion: {exc}")
        return None


def leave_one_out(collection: ReferenceCollection) -> dict:
    """Each exemplar against the others: how often the suggestion names the pinned
    label, and how often the VLM did. Only exemplars with a recorded VLM answer
    count for the VLM's share, and only those with a suggestion for the other."""
    n = hits = vlm_n = vlm_hits = 0
    rows = []
    for ex in collection.exemplars.values():
        sugg = collection.suggest(ex.vectors, exclude=[ex.doc_id])
        truth = normalise_label(ex.label)
        if sugg is not None:
            n += 1
            hit = normalise_label(sugg.label) == truth
            hits += hit
        if ex.vlm_label:
            vlm_n += 1
            vlm_hits += normalise_label(ex.vlm_label) == truth
        rows.append({"doc_id": ex.doc_id, "label": ex.label,
                     "suggestion": sugg.label if sugg else None,
                     "vorbild": sugg.exemplar if sugg else None, "vlm": ex.vlm_label})
    return {"exemplars": len(collection), "suggested": n, "suggestion_hits": hits,
            "vlm_answered": vlm_n, "vlm_hits": vlm_hits, "rows": rows}
