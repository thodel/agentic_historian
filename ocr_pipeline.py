"""
OCR Pipeline using InternVL3-8B-Instruct via GPUStack
Receives images and extracts text through the VLM.
"""

import base64
import os
import dotenv
from pathlib import Path
from typing import Union

from openai import OpenAI

# Load .env file if present (allows overriding defaults)
dotenv.load_dotenv(os.path.join(os.path.dirname(__file__), ".env.gpustack"))


class OCRPipeline:
    """Text recognition pipeline using a Vision Language Model."""

    SYSTEM_PROMPT = (
        "You are an expert OCR system. Your task is to accurately extract and transcribe "
        "all visible text from the given image. Preserve the text exactly as it appears, "
        "maintaining spacing and line breaks. If no readable text is found, respond with "
        "an empty string."
    )

    def __init__(
        self,
        base_url: str = None,
        api_key: str = None,
        model: str = None,
    ):
        self.client = OpenAI(
            base_url=base_url or os.environ.get("GPUSTACK_BASE_URL", "https://gpustack.unibe.ch/v1"),
            api_key=api_key or os.environ.get("GPUSTACK_API_KEY"),
        )
        self.model = model or os.environ.get("GPUSTACK_MODEL", "internvl3-8b-instruct")

    def _encode_image(self, image_path: Union[str, Path]) -> str:
        """Encode an image file as base64."""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _build_messages(self, image_source: str, prompt: str = None) -> list[dict]:
        """Build messages list for chat completion."""
        user_content = []

        # Add image
        if image_source.startswith("http://") or image_source.startswith("https://"):
            user_content.append({
                "type": "image_url",
                "image_url": {"url": image_source},
            })
        else:
            # Assume it's a local file path
            image_data = self._encode_image(image_source)
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
            })

        # Add optional custom prompt
        text_prompt = prompt or "Extract all text from this image."
        user_content.append({
            "type": "text",
            "text": text_prompt,
        })

        return [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    def recognize(
        self,
        image_source: Union[str, Path],
        prompt: str = None,
        temperature: float = 1,
        max_tokens: int = 32768,
    ) -> str:
        """
        Perform OCR on an image.

        Args:
            image_source: Local file path or HTTP(S) URL to the image.
            prompt: Optional custom prompt to guide extraction.
            temperature: Sampling temperature (default 1).
            max_tokens: Max tokens in response.

        Returns:
            Extracted text as a string.
        """
        messages = self._build_messages(image_source, prompt)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            top_p=1,
            max_tokens=max_tokens,
            frequency_penalty=0,
            presence_penalty=0,
        )

        return response.choices[0].message.content


# --- Quick test ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OCR pipeline using InternVL3-8B-Instruct")
    parser.add_argument("image", help="Path or URL to the image")
    parser.add_argument("--prompt", default=None, help="Custom extraction prompt")
    parser.add_argument(
        "--api-key", default=os.environ.get("GPUSTACK_API_KEY"),
        help="GPUStack API key (defaults to env or .env.gpustack)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="GPUStack base URL (defaults to env or .env.gpustack)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name (defaults to env or .env.gpustack)",
    )

    args = parser.parse_args()

    pipeline = OCRPipeline(base_url=args.base_url, api_key=args.api_key, model=args.model)
    text = pipeline.recognize(args.image, prompt=args.prompt)
    print(text)