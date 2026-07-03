# OCR Pipeline — InternVL3-8B-Instruct

Text recognition pipeline powered by a Vision Language Model via GPUStack.

## Setup

```bash
pip install -r requirements.txt
export GPUSTACK_API_KEY="your-api-key-here"
```

## Usage

**Basic usage (local file):**
```bash
python ocr_pipeline.py /path/to/image.png
```

**From URL:**
```bash
python ocr_pipeline.py "https://example.com/image.png"
```

**With custom prompt:**
```bash
python ocr_pipeline.py /path/to/image.png --prompt "Extract only the dates and names"
```

**Programmatic usage:**
```python
from ocr_pipeline import OCRPipeline

pipeline = OCRPipeline(
    base_url="https://gpustack.unibe.ch/v1",
    api_key="your-api-key",
    model="internvl3-8b-instruct"
)

text = pipeline.recognize("/path/to/image.png")
print(text)
```

## Features

- **Local files** — pass a filesystem path to an image
- **Remote URLs** — pass an HTTP(S) URL directly
- **Custom prompts** — guide extraction with specific instructions
- **Base64 encoding** — local images are sent as base64 data URIs
- **Configurable params** — temperature, max_tokens, etc.