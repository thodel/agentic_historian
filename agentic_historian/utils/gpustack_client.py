"""
utils/gpustack_client.py
Single GPUStack client (OpenAI-compatible) for every agent.

Model roles (config.py):
  VISION  → Agent A (HTR), Agent B (description)   : GPUSTACK_MODEL_VISION
  TEXT    → Agent C (NER), corpus/meta, reconcile  : GPUSTACK_MODEL_TEXT
  ORCH    → future NL/SitL orchestrator (WP1)      : GPUSTACK_MODEL_ORCHESTRATOR

Reasoning models:
  The default TEXT model (gpt-oss-120b) emits `reasoning_content` separately and
  spends tokens on it *before* writing `content`. With too small a budget,
  `content` comes back null (finish_reason="length"). This client therefore:
    - enforces a minimum token budget (MIN_MAX_TOKENS),
    - reads `message.content` (never `reasoning_content`),
    - retries once with a doubled budget if content is empty due to length.

Public sync API (unchanged, backward-compatible): chat() / chat_text() / chat_vision()
Async API (for the async port): ask() / ask_structured()
"""

import asyncio
import base64
import threading
import requests
from pathlib import Path
from typing import Optional

from loguru import logger
from openai import OpenAI
from openai import APIConnectionError, APIError, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import config

# Floor for token budgets — protects reasoning models from being truncated before
# they emit any answer (existing agents pass max_tokens as low as 600).
MIN_MAX_TOKENS = int(config._get("GPUSTACK_MIN_MAX_TOKENS", "1024"))

# Singleton-Client
_client: Optional[OpenAI] = None


class EmptyCompletion(RuntimeError):
    """Raised when a model returns no usable `content`."""


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not config.GPUSTACK_API_KEY:
            raise RuntimeError(
                "GPUSTACK_API_KEY is not set. Set it in the bot's env file "
                "(deploy/systemd/dh-bot.env.example lists the keys) and fill in a "
                "valid key."
            )
        _client = OpenAI(
            base_url=config.GPUSTACK_BASE_URL,
            api_key=config.GPUSTACK_API_KEY,
        )
    return _client


def encode_image(path: str | Path) -> str:
    """Bild als Base64-String für Vision-API."""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _encode_scaled(path: str, max_px: int) -> Optional[str]:
    """The image with its long side at most ``max_px``, as base64 JPEG; None if PIL can't.

    Only for several pages in one call (#771): five full-resolution scans are
    five times the image tokens of one, and the call is about script and layout
    across the volume, not about reading a line.
    """
    try:
        import io

        from PIL import Image
        with Image.open(path) as im:
            if max(im.size) <= max_px:
                return None
            im = im.convert("RGB")
            im.thumbnail((max_px, max_px))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:                        # unreadable → send the original
        logger.debug(f"[gpustack] could not scale {path}: {e}")
        return None


def image_url(image_source: str | Path, max_px: Optional[int] = None) -> str:
    """Gibt entweder eine URL oder einen base64-data-URI zurück."""
    s = str(image_source)
    if s.startswith(("http://", "https://")):
        return s
    # Lokale Datei → base64
    scaled = _encode_scaled(s, max_px) if max_px else None
    return f"data:image/jpeg;base64,{scaled or encode_image(s)}"


def _build_messages(prompt: str, system: Optional[str], image_source,
                    image_max_px: Optional[int] = None) -> list[dict]:
    """``image_source`` is one image or a list of them (#771), in the order shown."""
    if image_source:
        # Vision-Call: System-Messages mit Listen-Inhalten verursachen bei diesem
        # GPUStack-Setup 400er Fehler — Instructions deshalb inline im user content.
        text = f"{system}\n\n{prompt}" if system else prompt
        sources = list(image_source) if isinstance(image_source, (list, tuple)) else [image_source]
        return [
            {
                "role": "user",
                "content": [{"type": "text", "text": text}] + [
                    {"type": "image_url", "image_url": {"url": image_url(src, image_max_px)}}
                    for src in sources
                ],
            }
        ]
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


_usage = threading.local()


def _record_usage(response) -> None:
    u = getattr(response, "usage", None)
    _usage.last = {k: getattr(u, k) for k in ("prompt_tokens", "completion_tokens")
                   if isinstance(getattr(u, k, None), int)} if u is not None else {}


def last_usage() -> dict:
    """Token counts of this thread's last completion, as far as the backend said (#771)."""
    return dict(getattr(_usage, "last", {}) or {})


@retry(
    retry=retry_if_exception_type((APIConnectionError, RateLimitError, APIError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _create(
    model: str,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    frequency_penalty: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    seed: Optional[int] = None,
) -> tuple[Optional[str], str]:
    client = get_client()
    request = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        top_p=1,
        max_tokens=max_tokens,
    )
    # Seed is best-effort: the OpenAI-compatible schema accepts it, but a backend
    # is free to ignore it, and vLLM only honours it per-request on some versions.
    # Sent when asked for, never relied on — temperature 0 does the actual work.
    if seed is not None:
        request["seed"] = seed
    # Omit unset OpenAI-compatible options entirely.  This keeps every existing
    # caller byte-for-byte equivalent while allowing the HTR VLM call to opt in
    # to anti-repetition decoding (#275).
    if frequency_penalty is not None:
        request["frequency_penalty"] = frequency_penalty
    if presence_penalty is not None:
        request["presence_penalty"] = presence_penalty
    response = client.chat.completions.create(**request)
    _record_usage(response)
    choice = response.choices[0]
    return choice.message.content, (choice.finish_reason or "")


def chat(
    prompt: str,
    model: Optional[str] = None,
    system: Optional[str] = None,
    temperature: float = 1.0,
    max_tokens: int = 4096,
    image_source=None,
    agent_name: str = "unknown",
    frequency_penalty: Optional[float] = None,
    presence_penalty: Optional[float] = None,
    seed: Optional[int] = None,
    image_max_px: Optional[int] = None,
) -> str:
    """Generischer Chat-Call an GPUStack. Returns the model's text `content`.

    ``image_source`` is one image or a list (several pages in one call, #771);
    ``image_max_px`` scales local images down to that long side first.
    """
    model = model or config.GPUSTACK_MODEL_TEXT
    budget = max(max_tokens, MIN_MAX_TOKENS)
    messages = _build_messages(prompt, system, image_source, image_max_px)

    content, finish = _create(
        model,
        messages,
        temperature,
        budget,
        frequency_penalty,
        presence_penalty,
        seed,
    )

    # Reasoning models can exhaust the budget on reasoning before emitting content.
    if not content and finish == "length":
        logger.warning(
            f"[{agent_name}] {model} returned empty content (finish=length); "
            f"retrying with doubled budget ({budget} → {budget * 2})."
        )
        content, finish = _create(
            model,
            messages,
            temperature,
            budget * 2,
            frequency_penalty,
            presence_penalty,
        )

    if not content:
        raise EmptyCompletion(
            f"{model} returned no content (finish_reason={finish!r}). "
            f"If this is a reasoning model, raise max_tokens."
        )
    return content


def chat_text(prompt: str, system: Optional[str] = None, **kwargs) -> str:
    """Text-only Chat-Call (general LLM, default gpt-oss-120b)."""
    return chat(prompt, model=config.GPUSTACK_MODEL_TEXT, system=system, **kwargs)


def chat_vision(prompt: str, image_source, system: Optional[str] = None,
                model: Optional[str] = None, **kwargs) -> str:
    """Vision-Call (VLM). ``model`` defaults to ``GPUSTACK_MODEL_VISION``.

    The parameter exists because the caller has to be able to *say* which VLM
    it is using (#537). Without it ``_run_vlm`` took a model argument it could
    not act on, and the reading was recorded under a name nobody had asked the
    gateway for.
    """
    return chat(
        prompt,
        model=model or config.GPUSTACK_MODEL_VISION,
        system=system,
        image_source=image_source,
        **kwargs,
    )


# ── Embedding + Reranker (Agent C semantic entity linking) ───────────────────

def embed(texts: str | list[str], model: Optional[str] = None) -> list[list[float]]:
    """
    Embed one or many texts. Returns list of embedding vectors.
    Uses GPUSTACK_MODEL_EMBEDDING (qwen3-embedding-4b, 2560 d).
    """
    model = model or config.GPUSTACK_MODEL_EMBEDDING
    client = get_client()
    if isinstance(texts, str):
        texts = [texts]
    response = client.embeddings.create(model=model, input=texts)
    return [item.embedding for item in response.data]


def rerank(
    query: str,
    documents: list[str],
    model: Optional[str] = None,
    top_n: int = 3,
) -> list[dict]:
    """
    Rerank documents for a query. Returns top_n results sorted by relevance.
    Uses GPUSTACK_MODEL_RERANKER (jina-reranker-v2-base-multilingual).
    Gracefully degrades to score=0.0 on failure.
    """
    model = model or config.GPUSTACK_MODEL_RERANKER
    # Reranking is not an OpenAI-SDK method; GPUStack exposes it at POST /v1/rerank
    # (jina/cohere-style). Call it directly.
    try:
        resp = requests.post(
            f"{config.GPUSTACK_BASE_URL.rstrip('/')}/rerank",
            headers={
                "Authorization": f"Bearer {config.GPUSTACK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": model, "query": query, "documents": documents, "top_n": top_n},
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        out = []
        for r in results:
            idx = r.get("index")
            if idx is None or not (0 <= idx < len(documents)):
                continue
            score = r.get("relevance_score", r.get("score", 0.0))
            out.append({"index": idx, "document": documents[idx], "score": score})
        if out:
            return out
    except Exception as e:
        logger.warning(f"[gpustack] rerank failed: {e}")
    return [{"index": i, "document": d, "score": 0.0}
            for i, d in enumerate(documents[:top_n])]


# ── Async interface (target for the async agent port) ────────────────────────

async def ask(
    system: str,
    user_text: str,
    image_path: Optional[str | Path] = None,
    model: Optional[str] = None,
    max_tokens: int = 4096,
    agent_name: str = "unknown",
) -> str:
    """Async wrapper over chat(); runs the blocking SDK call in a thread."""
    selected = model or (config.GPUSTACK_MODEL_VISION if image_path else config.GPUSTACK_MODEL_TEXT)
    return await asyncio.to_thread(
        chat,
        user_text,
        model=selected,
        system=system,
        max_tokens=max_tokens,
        image_source=str(image_path) if image_path else None,
        agent_name=agent_name,
    )


async def ask_structured(
    system: str,
    user_text: str,
    image_path: Optional[str | Path] = None,
    model: Optional[str] = None,
    max_tokens: int = 4096,
    agent_name: str = "unknown",
) -> str:
    """Like ask(), but instructs the model to return JSON only."""
    json_system = f"{system}\n\nALWAYS respond with valid JSON only. No markdown, no preamble."
    return await ask(json_system, user_text, image_path, model, max_tokens, agent_name)
