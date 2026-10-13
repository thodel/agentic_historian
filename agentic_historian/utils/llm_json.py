"""utils/llm_json.py — JSON out of a reasoning model's answer (#633).

Every text call in this repository goes to ``GPUSTACK_MODEL_TEXT``, which is
``gpt-oss-120b`` — a **reasoning** model. It spends tokens on reasoning before it
emits content, and what comes back around the JSON is therefore prose, fences,
or both. ``config.py`` already says so twice (the ``GPUSTACK_TEXT_MAX_TOKENS``
comment, and ``FUSION_ARBITRATE_MAX_TOKENS`` with the measurement from #406).

``json.loads`` straight on that answer raises, so a caller that does it has an
exception path it will take routinely — and in Agent D that path returned an
empty result which reads as a finding (#634). ``agents/source_description.py``
had learned the lesson and carried a private ``_extract_balanced_json``; this is
that function, shared, so the next caller does not write a fourth copy.

**It is string-aware**, which the private copy was not: a brace inside a JSON
string value ended the scan early and truncated the object. A German care
context containing "Spital {sic}" is unlikely, but a parser that silently
returns half an object is the kind of thing nobody finds by reading output.

Run: pytest agentic_historian/tests/test_ah_633_corpus_analysis.py -v
"""

from __future__ import annotations

import json
from typing import Any, Optional

__all__ = ["LlmJsonError", "extract_balanced_json", "loads"]


class LlmJsonError(ValueError):
    """No JSON object in the answer, or one that will not parse.

    A ``ValueError``, so ``except (ValueError, ...)`` in an existing caller
    still catches it, and ``json.JSONDecodeError`` (also a ValueError) stays in
    the same family.
    """


def extract_balanced_json(text: str) -> str:
    """The first balanced JSON object in *text*, or ``""``.

    Braces are counted only outside string literals, and a backslash escape is
    skipped, so ``{"a": "}"}`` and ``{"a": "\\\\"}`` both come back whole.

    An object that is an **array element** is not returned. A model that answers
    ``[{"name": "Fürsorge"}]`` instead of ``{"topics": [...]}`` has given the
    wrong shape, and handing back its first element would turn that into a
    plausible-looking answer object with the wrong keys in it — the caller then
    merges ``name`` into a result that should have had ``topics``. Returning
    nothing makes the caller take its failure path, which is the truth.
    """
    depth = 0
    start: Optional[int] = None
    in_string = False
    escaped = False

    for index, char in enumerate(text or ""):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            # A quote before any brace is not part of an object we want.
            if start is not None:
                in_string = True
            continue
        if char == "{":
            if start is None:
                if _preceded_by_open_bracket(text, index):
                    return ""
                start = index
            depth += 1
        elif char == "}" and start is not None:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def _preceded_by_open_bracket(text: str, index: int) -> bool:
    """Whether the brace at *index* opens an element of a JSON array.

    Looks back over whitespace only: ``[ {`` and ``[{`` are an array, while a
    prose preamble that happens to end in ``]`` or any other character is not.
    """
    for char in reversed(text[:index]):
        if char.isspace():
            continue
        return char == "["
    return False


def loads(text: str, *, what: str = "answer") -> Any:
    """Parse the JSON object out of a model answer, or raise ``LlmJsonError``.

    The error names *what* was being parsed and carries a short excerpt of what
    came back instead, because "Expecting value: line 1 column 1" in a log says
    nothing about which call failed or why — and these calls fail often enough
    that the log is where the diagnosis has to happen.
    """
    body = extract_balanced_json(text or "")
    if not body:
        excerpt = (text or "").strip().replace("\n", " ")[:200] or "<leer>"
        raise LlmJsonError(f"kein JSON-Objekt in der {what}: {excerpt!r}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise LlmJsonError(
            f"unlesbares JSON in der {what}: {exc}; {body[:200]!r}") from exc
