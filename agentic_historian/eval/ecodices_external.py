"""#844 — a second reader on the e-codices bench: Agent B's prompt, another model.

The third run (7.–8.11.2026) dated no volume before the 13th century and
never named Carolingian minuscule. Two explanations fit: the gateway's VLM
cannot date early hands, or the prompt pulls every answer towards the 15th
century. Sending the *same* prompt and the *same* pages to an external model
(Gemini, as `external_atr` reads pages) separates them: if the second reader
dates the Carolingian volumes right, the fault is the model's; if it does not,
the prompt is the first suspect.

What is held fixed: the prompt text (``source_description.image_only_prompt``,
the one production sends), the pages and their scaling (``AGENT_B_PAGE_MAX_PX``),
the parsing and the grounding of the answer. What changes: the API and the
model. The run file keeps the model, the tokens and whether the answer was cut
off, because a reading on a metered API that cannot say what it cost is one
nobody can authorise a second time.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

import config
from external_atr import REASONING_LEVELS, preflight


def describer(model: str, *, reasoning: Optional[str] = None,
              max_tokens: Optional[int] = None, base_url: Optional[str] = None,
              api_key: Optional[str] = None, client=None) -> Callable:
    """A ``describe`` for ``ecodices_bench.run`` that reads through an
    OpenAI-compatible chat API. ``client`` replaces the OpenAI client in tests.

    Raises before the first page when there is no key, when ``reasoning`` is
    not a level the reader knows, or when the API does not list the model —
    the 404s would be free, but a typo that names a real, wrong model is paid.
    """
    from agents import source_description as agent_b
    from utils import gpustack_client as gs

    key = api_key or config.GEMINI_API_KEY
    if client is None and not key:
        raise RuntimeError("no API key — set GEMINI_API_KEY in .env.gpustack, never on the "
                           "command line")
    effort = (config.GEMINI_REASONING if reasoning is None else reasoning).strip()
    if effort and effort not in REASONING_LEVELS:
        raise ValueError(f"reasoning {effort!r} is not one of {', '.join(REASONING_LEVELS)}")
    budget = int(max_tokens if max_tokens is not None else config.GEMINI_MAX_TOKENS)
    if client is None:
        check = preflight(model, base_url=base_url, api_key=key)
        logger.info(f"[external] {check.line}")
        if check.refused:
            raise RuntimeError(check.line.strip())
        from openai import OpenAI
        client = OpenAI(base_url=base_url or config.GEMINI_BASE_URL, api_key=key,
                        timeout=config.GEMINI_TIMEOUT_S)

    def describe(doc_id: str, images: list, page_count: int, page_roles=None) -> dict:
        images = [str(p) for p in images]
        prompt = agent_b.image_only_prompt(images, None, page_count, page_roles)
        body = {
            "model": model,
            "max_tokens": budget,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}] + [
                {"type": "image_url",
                 "image_url": {"url": gs.image_url(p, config.AGENT_B_PAGE_MAX_PX)}}
                for p in images]}],
        }
        if effort:
            body["reasoning_effort"] = effort
        t0 = time.monotonic()
        answer = client.chat.completions.create(**body)
        choice = answer.choices[0]
        raw = (getattr(choice.message, "content", "") or "").strip()
        usage = getattr(answer, "usage", None)
        truncated = getattr(choice, "finish_reason", "") == "length"
        if truncated:
            logger.warning(f"[external] {doc_id}: answer hit the {budget}-token ceiling")
        source_json, description_md = agent_b._parse_response(raw)
        source_json = agent_b._ground(agent_b._rank_script(source_json), images=images,
                                      page_count=page_count, text_seen=False, roles=page_roles)
        cost = {"seiten": len(images), "sekunden": round(time.monotonic() - t0, 1),
                "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0)}
        logger.info(f"[external] {doc_id}: {model}, {cost['seiten']} Seite(n), "
                    f"{cost['sekunden']} s, Tokens {cost['prompt_tokens']}+"
                    f"{cost['completion_tokens']}")
        return {"doc_id": doc_id, "source_json": source_json,
                "source_description": description_md,
                "pages_seen": [Path(p).name for p in images], "cost": cost,
                "truncated": truncated, "model": model, "source": "external"}

    describe.settings = {"describe": "external", "model": model,       # type: ignore[attr-defined]
                         "reasoning": effort or "default", "max_tokens": budget}
    return describe
