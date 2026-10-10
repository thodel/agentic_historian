"""
external_atr.py — a recogniser that reads a page through an external model API.

**Why this fits without touching the runner.** `atr_batch`'s recogniser is
`Callable[[Path, str], object]` and every field of the result is read with
`getattr(..., default)`. So an external reader is a second factory beside
`gateway_recogniser`, and it inherits the whole machinery: resumability by
presence, the outage circuits, `--keys-from`, `--letters-from`, the reports.

**OpenAI-compatible rather than a vendor SDK.** Google serves Gemini at
``https://generativelanguage.googleapis.com/v1beta/openai/``, and `openai` is
already a dependency because `utils/gpustack_client.py` talks to GPUStack the
same way. One client class, two base URLs, no new package to pin.

**Three things this records that a transcription alone would not.**

*Truncation.* A page that hits the output ceiling comes back as an ordinary
success that stops mid-sentence — the hazard the MCP server's own instructions
name first. ``finish_reason == "length"`` sets ``truncated``, so the run's report
counts it instead of a person noticing weeks later that a letter ends in the
middle of a word.

*Which prompt.* A prompt is an experimental parameter. Two readings of one page
under different prompts are different measurements, so the prompt's digest goes
into every record (through ``service_version``, which `atr_batch._result_payload`
writes as ``gateway_version``) and the prompt itself is a versioned file.

*What it cost.* Each call's token usage is accumulated and logged, because a
run against a metered API that cannot say what it spent is a run nobody can
authorise a second time.

**What it cannot do.** It returns page text and no line geometry — there are no
polygons in a chat completion. So these readings can be compared, published and
read, but they cannot feed `pagexml-hf` or train a line model: that needs the
PAGE XML the gateway's engines produce. ``lines`` is empty and says so rather
than inventing one line per newline, which would look like segmentation and be
nothing of the kind.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

import config

#: Where the prompts live, versioned beside the code.
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

#: The prompt this corpus is read with, unless another is named.
DEFAULT_PROMPT = "lassberg_atr.md"

#: The JSON the structured mode asks for. Two fields, because the prompt's last
#: rule — "Expansions go into the normalized field only" — needs a normalized
#: field to exist, and without one that rule has nowhere to land.
STRUCTURED_SCHEMA = {
    "type": "object",
    "properties": {
        "diplomatic": {"type": "string",
                       "description": "The text as written, letter for letter."},
        "normalized": {"type": "string",
                       "description": "The same text with abbreviations "
                                      "expanded. Empty when nothing was "
                                      "expanded."},
    },
    "required": ["diplomatic"],
    "additionalProperties": False,
}


@dataclass
class Reading:
    """One page, as an external model read it.

    The field names are `atr_batch`'s, so the existing record writer and every
    report work unchanged. ``lines`` stays empty on purpose — see the module
    docstring.
    """

    text: str = ""
    normalized: str = ""
    lines: list = field(default_factory=list)
    confidence: float = 0.0
    engine: str = "external"
    timing_ms: int = 0
    truncated: bool = False
    service_version: str = "?"
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class Spend:
    """What a run has cost so far, in tokens. Thread-safe because the batch
    reads pages concurrently."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, reading: Reading) -> None:
        with self._lock:
            self.calls += 1
            self.prompt_tokens += reading.prompt_tokens
            self.completion_tokens += reading.completion_tokens

    def __str__(self) -> str:
        return (f"{self.calls} call(s), {self.prompt_tokens:,} prompt + "
                f"{self.completion_tokens:,} completion token(s)")


def load_prompt(name: str = DEFAULT_PROMPT) -> tuple[str, str]:
    """``(text, digest)`` for a prompt file. The digest identifies the reading.

    A name under :data:`PROMPT_DIR`, never a path: a prompt that can come from
    anywhere is a prompt no record can identify, and the record is the point.
    """
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"a prompt is named, not pathed: {name!r}")
    path = PROMPT_DIR / name
    if not path.is_file():
        have = ", ".join(sorted(p.name for p in PROMPT_DIR.glob("*.md"))) or "none"
        raise FileNotFoundError(f"no prompt {name} in {PROMPT_DIR} (have: {have})")
    text = path.read_text(encoding="utf-8").strip()
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _image_url(path: Path) -> str:
    """The page as a data URI. The batch hands this a working copy — about
    1.5 MB — and never the 25 MB original."""
    import base64

    suffix = path.suffix.lower()
    kind = {".png": "png", ".tif": "tiff", ".tiff": "tiff"}.get(suffix, "jpeg")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{kind};base64,{data}"


def _answer(message, structured: bool) -> tuple[str, str]:
    """``(diplomatic, normalized)`` out of one completion message."""
    content = (getattr(message, "content", "") or "").strip()
    if not structured:
        return content, ""
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        # A model that was asked for JSON and answered prose has still read the
        # page; losing the reading to a parse error would be worse than keeping
        # it with the normalisation missing. Named, not silent.
        logger.warning("[external] structured answer was not JSON; keeping the "
                       "text as diplomatic")
        return content, ""
    return (str(data.get("diplomatic", "") or ""),
            str(data.get("normalized", "") or ""))


@dataclass(frozen=True)
class Preflight:
    """Whether an external model id exists at the API, before any page is sent.

    The gateway's preflight exists because a model id it does not have makes
    every page take the same 404, and learning that from the 404s costs a cold
    start and a half-filled run directory (#482). An external API has exactly
    that failure mode and one worse: the 404s are free but the *successes* are
    billed, so a typo that happens to name a real but wrong model spends money
    on the wrong measurement.

    The OpenAI-compatible surface exposes a models route, so this is a real
    check and not a skip. A probe that cannot reach the API at all is not a
    refusal: the network may be slow and the pages are still worth trying, which
    is how the gateway's own probe behaves.
    """

    model: str
    listed: Optional[bool]        # None = the API could not be asked
    detail: str = ""

    @property
    def refused(self) -> bool:
        return self.listed is False

    @property
    def line(self) -> str:
        if self.listed:
            return f"  ok {self.model}: listed at {config.GEMINI_BASE_URL}"
        if self.listed is None:
            return (f"  ?  {self.model}: could not ask "
                    f"{config.GEMINI_BASE_URL} ({self.detail}) — trying anyway")
        return (f"  NO {self.model}: not listed at {config.GEMINI_BASE_URL}"
                + (f" (have: {self.detail})" if self.detail else "")
                + " — every page would take the same 404")


def preflight(model: str, *, base_url: Optional[str] = None,
              api_key: Optional[str] = None) -> Preflight:
    """Ask the API whether it serves this model id. No page is sent."""
    from openai import OpenAI

    key = api_key or config.GEMINI_API_KEY
    if not key:
        return Preflight(model, False, "no GEMINI_API_KEY")
    try:
        client = OpenAI(base_url=(base_url or config.GEMINI_BASE_URL),
                        api_key=key, timeout=30)
        ids = {m.id for m in client.models.list()}
    except Exception as exc:          # noqa: BLE001 — a probe is never fatal
        return Preflight(model, None, f"{type(exc).__name__}: {exc}")
    if model in ids:
        return Preflight(model, True)
    # Model ids carry prefixes on some surfaces ("models/gemini-…"), so a
    # suffix match is reported as found and the exact id is named back.
    near = sorted(i for i in ids if i.rsplit("/", 1)[-1] == model)
    if near:
        return Preflight(model, True, near[0])
    sample = ", ".join(sorted(ids)[:6]) or "nothing listed"
    return Preflight(model, False, sample)


def openai_recogniser(*, base_url: Optional[str] = None,
                      api_key: Optional[str] = None,
                      prompt: str = DEFAULT_PROMPT,
                      max_tokens: Optional[int] = None,
                      structured: bool = False,
                      timeout: Optional[float] = None,
                      retries: int = 3,
                      spend: Optional[Spend] = None):
    """A recogniser backed by an OpenAI-compatible chat API.

    The model is the batch's own ``--models`` entry, so one run can compare two
    external models exactly as it compares two of the gateway's.

    Retries are for the transport — a 429 or a 5xx — and never for a refusal or
    a bad request, which would only burn quota. The page's own failure is the
    batch's business: it has circuits for a model that has stopped answering,
    and this must not hide them behind retries of its own.
    """
    from openai import OpenAI
    from openai import APIConnectionError, APIStatusError, RateLimitError

    key = api_key or config.GEMINI_API_KEY
    if not key:
        raise RuntimeError(
            "no API key — set GEMINI_API_KEY in .env.gpustack. Never on the "
            "command line: argv is in `ps` and in the shell history")
    text, digest = load_prompt(prompt)
    client = OpenAI(base_url=(base_url or config.GEMINI_BASE_URL),
                    api_key=key,
                    timeout=timeout if timeout is not None
                    else config.GEMINI_TIMEOUT_S)
    budget = int(max_tokens if max_tokens is not None
                 else config.GEMINI_MAX_TOKENS)
    tally = spend if spend is not None else Spend()
    logger.info(f"[external] {config.GEMINI_BASE_URL}  prompt {prompt} "
                f"@{digest}  max_tokens {budget}"
                + ("  structured" if structured else ""))

    def _recognise(image: Path, model: str) -> Reading:
        body = {
            "model": model,
            "max_tokens": budget,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url",
                     "image_url": {"url": _image_url(Path(image))}},
                ],
            }],
        }
        if structured:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "transcription", "strict": True,
                                "schema": STRUCTURED_SCHEMA},
            }
        started = time.monotonic()
        last: Optional[Exception] = None
        for attempt in range(1, max(1, retries) + 1):
            try:
                answer = client.chat.completions.create(**body)
                break
            except (RateLimitError, APIConnectionError) as exc:
                last = exc
            except APIStatusError as exc:
                if exc.status_code < 500:
                    raise                     # a refusal is not a retry
                last = exc
            if attempt < max(1, retries):
                pause = min(2 ** attempt, 30)
                logger.warning(f"[external] {type(last).__name__} on "
                               f"{Path(image).name}, retrying in {pause}s")
                time.sleep(pause)
        else:
            raise last if last else RuntimeError("no answer and no error")

        choice = answer.choices[0]
        diplomatic, normalized = _answer(choice.message, structured)
        usage = getattr(answer, "usage", None)
        reading = Reading(
            text=diplomatic, normalized=normalized,
            engine="external",
            timing_ms=int((time.monotonic() - started) * 1000),
            truncated=(getattr(choice, "finish_reason", "") == "length"),
            service_version=f"{model}@prompt-{digest}"
                            + ("+structured" if structured else ""),
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0))
        if reading.truncated:
            logger.warning(
                f"[external] {Path(image).name} hit the {budget}-token ceiling "
                f"and stops mid-sentence — raise GEMINI_MAX_TOKENS")
        tally.add(reading)
        return reading

    _recognise.spend = tally                  # type: ignore[attr-defined]
    _recognise.close = lambda: logger.info(f"[external] spent {tally}")  # type: ignore[attr-defined]
    return _recognise
