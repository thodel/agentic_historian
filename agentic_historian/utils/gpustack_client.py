"""
utils/gpustack_client.py
GPUStack API-Client (OpenAI-kompatibel) für alle Agenten.
"""

import base64
import os
from pathlib import Path
from typing import Optional

from openai import OpenAI

import config


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
    return response.choices[0].message.content


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