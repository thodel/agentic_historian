"""Agent A — HTR (Handwritten Text Recognition) pipeline.

Uses the local VLM (InternVL3-8B via GPUStack) for text recognition.
QA scoring and retry logic are applied to improve transcription quality.
"""

import os
import logging
from pathlib import Path
from PIL import Image
import base64
import io
import requests

logger = logging.getLogger(__name__)

# GPUStack VLM endpoint (configured in TOOLS.md)
VLM_ENDPOINT = os.getenv("VLM_ENDPOINT", "https://gpustack.unibe.ch/v1")
VLM_API_KEY = os.getenv("VLM_API_KEY", "")
VLM_MODEL = os.getenv("VLM_MODEL", "internvl3-8b-instruct")


def _image_to_base64(image: Image.Image) -> str:
    """Convert a PIL Image to a base64-encoded JPEG string."""
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _call_vlm(image: Image.Image, prompt: str) -> str:
    """Call the GPUStack VLM for text recognition."""
    payload = {
        "model": VLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_image_to_base64(image)}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 4096,
    }
    headers = {"Authorization": f"Bearer {VLM_API_KEY}", "Content-Type": "application/json"}
    response = requests.post(f"{VLM_ENDPOINT}/chat/completions", json=payload, headers=headers, timeout=120)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def run_ocr(image_path: Path, max_retries: int = 2) -> tuple[str, float]:
    """Run OCR on a single image. Returns (transcription, confidence_score)."""
    image = Image.open(image_path)
    prompt = (
        "Transcribe ALL text visible in this image. Preserve line breaks and spacing. "
        "If the text is handwritten, transcribe it literally. If it is printed, transcribe it exactly."
    )
    best_text = ""
    best_score = 0.0
    for attempt in range(max_retries + 1):
        try:
            text = _call_vlm(image, prompt)
            # Simple heuristic: longer transcriptions tend to be more complete
            score = len(text) / max(1, len(prompt) * 10)
            if score >= best_score:
                best_text = text
                best_score = score
        except Exception as e:
            logger.warning(f"OCR attempt {attempt + 1} failed for {image_path.name}: {e}")
    return best_text, min(best_score, 1.0)


def process_document(image_paths: list[Path], output_dir: Path) -> dict:
    """Process a list of image files and save transcriptions."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for img_path in image_paths:
        text, score = run_ocr(img_path)
        out_file = output_dir / f"{img_path.stem}.txt"
        out_file.write_text(text, encoding="utf-8")
        results.append({"file": str(img_path), "transcription": text, "score": score})
        logger.info(f"Saved transcription: {out_file} (score={score:.2f})")
    return {"documents": results}

