"""
utils/gpustack_client.py
GPUStack API-Client (OpenAI-kompatibel) für alle Agenten.
"""

import base64
import threading
from pathlib import Path
from typing import Optional, Sequence

from openai import OpenAI

import config
from utils import metrics

# Thread-local agent label — set by callers so metrics knows who's calling
_CURRENT_AGENT: Optional[str] = None
_agent_lock = threading.local()


def set_agent(agent_name: str):
    """Set the current agent name for token tracking."""
    global _CURRENT_AGENT
    _CURRENT_AGENT = agent_name


def clear_agent():
    """Clear the current agent name."""
    global _CURRENT_AGENT
    _CURRENT_AGENT = None


# Singleton-Client
_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
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
    if s.startswith("http://") or s.startswith("https://"):
        return s
    # Lokale Datei → base64
    return f"data:image/jpeg;base64,{encode_image(s)}"


def chat(
    prompt: str,
    model: Optional[str] = None,
    system: Optional[str] = None,
    temperature: float = 1.0,
    max_tokens: int = 4096,
    image_source: Optional[str] = None,
) -> str:
    """
    Generischer Chat-Call an GPUStack.

    Args:
        prompt: User-Prompt
        model: Modell-ID (text oder vision). None = Text-Default.
        system: Optionaler System-Prompt
        temperature, max_tokens: Sampling-Parameter
        image_source: Pfad/URL zu einem Bild (für Vision-Modelle)

    Returns:
        Modell-Antwort als String
    """
    client = get_client()
    model = model or config.GPUSTACK_MODEL_TEXT

    messages = []
    if system:
        messages.append({"role": "system", "content": system})

    if image_source:
        # Vision-Call: text + image als Content-Liste
        # Hinweis: System-Messages mit Listen-Inhalten verursachen bei diesem
        # GPUStack-Setup 400er Fehler — deshalb单独 als user content.
        img_url = image_url(image_source)
        if system:
            # Instructions in user text, nicht als system message
            user_content = [
                {"type": "text", "text": system + "\n\n" + prompt},
                {"type": "image_url", "image_url": {"url": img_url}},
            ]
        else:
            user_content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": img_url}},
            ]
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        top_p=1,
        max_tokens=max_tokens,
        frequency_penalty=0,
        presence_penalty=0,
    )
    # Track token usage for Agent E
    if response.usage:
        metrics.record_run(
            agent=_CURRENT_AGENT or "unknown",
            wall_clock_ms=0,
            prompt_tokens=response.usage.prompt_tokens or 0,
            completion_tokens=response.usage.completion_tokens or 0,
        )
    content = response.choices[0].message.content
    # gpt-oss-120b returns null content while reasoning
    if content is None:
        return ""
    return content


def chat_text(prompt: str, system: Optional[str] = None, **kwargs) -> str:
    """Text-only Chat-Call."""
    return chat(prompt, model=config.GPUSTACK_MODEL_TEXT, system=system, **kwargs)


def chat_vision(
    prompt: str,
    image_source: str,
    system: Optional[str] = None,
    **kwargs,
) -> str:
    """Vision-Call (internvl3) für Bildanalyse."""
    return chat(
        prompt,
        model=config.GPUSTACK_MODEL_VISION,
        system=system,
        image_source=image_source,
        **kwargs,
    )


# ── Embedding + Reranker (for Agent C entity linking) ────────────────────────

def embed(texts: str | list[str], model: Optional[str] = None) -> list[list[float]]:
    """
    Embed one or many texts using the GPUStack embedding model.
    Returns list of embedding vectors (each a list of floats).

    Uses config.GPUSTACK_MODEL_EMBEDDING ("qwen3-embedding-0.6b").
    """
    model = model or config.GPUSTACK_MODEL_EMBEDDING
    client = get_client()

    if isinstance(texts, str):
        texts = [texts]

    response = client.embeddings.create(
        model=model,
        input=texts,
    )
    return [item.embedding for item in response.data]


def rerank(
    query: str,
    documents: list[str],
    model: Optional[str] = None,
    top_n: int = 3,
) -> list[dict]:
    """
    Rerank documents for a query using the GPUStack reranker.
    Returns top_n results with scores, sorted by relevance descending.

    Uses config.GPUSTACK_MODEL_RERANKER ("jina-reranker-v2-base-multilingual").
    """
    model = model or config.GPUSTACK_MODEL_RERANKER
    client = get_client()

    try:
        response = client.rerank(
            model=model,
            query=query,
            documents=documents,
            top_n=top_n,
        )
        return [
            {
                "index": r.index,
                "document": documents[r.index],
                "score": r.relevance_score,
            }
            for r in response.results
        ]
    except Exception as e:
        # Graceful degradation: return first top_n documents with score 0
        from loguru import logger
        logger.warning(f"[gpustack] rerank failed: {e}")
        return [{"index": i, "document": d, "score": 0.0}
                for i, d in enumerate(documents[:top_n])]