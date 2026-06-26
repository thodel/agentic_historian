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
                "GPUSTACK_API_KEY is not set. Copy gpustack.env.example to "
                ".env.gpustack at the repo root and fill in a valid key."
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


def image_url(image_source: str | Path) -> str:
    """Gibt entweder eine URL oder einen base64-data-URI zurück."""
    s = str(image_source)
    if s.startswith(("http://", "https://")):
        return s
    # Lokale Datei → base64
    return f"data:image/jpeg;base64,{encode_image(s)}"


def _build_messages(prompt: str, system: Optional[str], image_source: Optional[str]) -> list[dict]:
    if image_source:
        # Vision-Call: System-Messages mit Listen-Inhalten verursachen bei diesem
        # GPUStack-Setup 400er Fehler — Instructions deshalb inline im user content.
        text = f"{system}\n\n{prompt}" if system else prompt
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": image_url(image_source)}},
                ],
            }
        ]
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


@retry(
    retry=retry_if_exception_type((APIConnectionError, RateLimitError, APIError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def _create(model: str, messages: list[dict], temperature: float, max_tokens: int) -> tuple[Optional[str], str]:
    client = get_client()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        top_p=1,
        max_tokens=max_tokens,
    )
    choice = response.choices[0]
    return choice.message.content, (choice.finish_reason or "")


def chat(
    prompt: str,
    model: Optional[str] = None,
    system: Optional[str] = None,
    temperature: float = 1.0,
    max_tokens: int = 4096,
    image_source: Optional[str] = None,
    agent_name: str = "unknown",
) -> str:
    """Generischer Chat-Call an GPUStack. Returns the model's text `content`."""
    model = model or config.GPUSTACK_MODEL_TEXT
    budget = max(max_tokens, MIN_MAX_TOKENS)
    messages = _build_messages(prompt, system, image_source)

    content, finish = _create(model, messages, temperature, budget)

    # Reasoning models can exhaust the budget on reasoning before emitting content.
    if not content and finish == "length":
        logger.warning(
            f"[{agent_name}] {model} returned empty content (finish=length); "
            f"retrying with doubled budget ({budget} → {budget * 2})."
        )
        content, finish = _create(model, messages, temperature, budget * 2)

    if not content:
        raise EmptyCompletion(
            f"{model} returned no content (finish_reason={finish!r}). "
            f"If this is a reasoning model, raise max_tokens."
        )
    return content


def chat_text(prompt: str, system: Optional[str] = None, **kwargs) -> str:
    """Text-only Chat-Call (general LLM, default gpt-oss-120b)."""
    return chat(prompt, model=config.GPUSTACK_MODEL_TEXT, system=system, **kwargs)


def chat_vision(prompt: str, image_source: str, system: Optional[str] = None, **kwargs) -> str:
    """Vision-Call (VLM, default qwen3-vl-30b-a3b-instruct)."""
    return chat(
        prompt,
        model=config.GPUSTACK_MODEL_VISION,
        system=system,
        image_source=image_source,
        **kwargs,
    )


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
