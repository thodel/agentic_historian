"""Correctness trio 2: Voyant endpoint, JSON fence stripping, HF model class.

1. Voyant was dead code: config read the env var under a misspelled name
   (`Voyant_API_URL`), defaulted to the public voyant-tools.org API instead of
   the self-hosted instance documented in the README, and Agent D posted to a
   `/Corpus` endpoint that does not exist. The documented flow is
   POST <voyant>/?text= with form data; Voyant redirects to /?corpus=<id>.
2. entity_agent cleaned LLM output with str.strip("```json"), which treats the
   argument as a character SET and can swallow legitimate leading/trailing
   j/s/o/n characters of the JSON payload.
3. The HF OCR path loaded models with AutoModelForCTC, but the deployed
   registry models (TrOCR family, LightOnOCR) are seq2seq vision-encoder-
   decoder models that must be decoded via generate().

Run:  pytest agentic_historian/tests/test_voyant_fence_hf.py  (from repo root)
"""

import pathlib
import sys
from unittest import mock

PKG = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

import config  # noqa: E402

config.ensure_dirs()

from agents import corpus_analysis, entity_agent  # noqa: E402


def read(rel: str) -> str:
    return (PKG / rel).read_text(encoding="utf-8")


# ── 1. Voyant ────────────────────────────────────────────────────────────────

def test_config_reads_correctly_cased_env_var():
    src = read("config.py")
    assert '_get("VOYANT_API_URL"' in src, (
        "config.py must read VOYANT_API_URL (the misspelled Voyant_API_URL "
        "could never be set from a normal environment)"
    )


def test_voyant_default_is_selfhosted_instance():
    assert "tei.dh.unibe.ch" in read("config.py"), (
        "Voyant default must point at the self-hosted instance from the README"
    )


def test_agent_d_does_not_call_nonexistent_corpus_endpoint():
    src = read("agents/corpus_analysis.py")
    assert '"/Corpus"' not in src and "'/Corpus'" not in src, (
        "Voyant has no /Corpus endpoint — use the documented ?text= form POST"
    )


def test_voyant_url_returns_redirect_url():
    fake = mock.Mock(ok=True, url="https://tei.dh.unibe.ch/voyant/?corpus=abc123")
    with mock.patch.object(corpus_analysis.requests, "post", return_value=fake) as post:
        url = corpus_analysis._voyant_url("Erstes Beispiel.", "default")
    assert url == "https://tei.dh.unibe.ch/voyant/?corpus=abc123"
    endpoint = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs.get("url")
    assert endpoint.endswith("/"), "must POST to the Voyant root, not a sub-endpoint"


def test_voyant_url_empty_on_failure():
    fake = mock.Mock(ok=False, url="https://tei.dh.unibe.ch/voyant/")
    with mock.patch.object(corpus_analysis.requests, "post", return_value=fake):
        assert corpus_analysis._voyant_url("text", "default") == ""


# ── 2. Code-fence stripping ──────────────────────────────────────────────────

def test_no_character_set_strip_footgun():
    src = read("agents/entity_agent.py")
    assert 'raw.strip().strip("```json")' not in src, (
        'str.strip("```json") strips a character SET, not a prefix'
    )


def test_fence_stripping_plain_and_fenced():
    payload = '{"entities": [{"text": "Thun"}]}'
    assert entity_agent._strip_code_fences(payload) == payload
    assert entity_agent._strip_code_fences(f"```json\n{payload}\n```") == payload
    assert entity_agent._strip_code_fences(f"```\n{payload}\n```") == payload


def test_fence_stripping_preserves_json_letters_at_edges():
    # Starts with 'n' / ends with 'j' — characters inside the old strip set.
    payload = 'null'
    assert entity_agent._strip_code_fences(payload) == payload
    payload2 = '{"name": "sonj"}'
    assert entity_agent._strip_code_fences(payload2) == payload2


# ── 3. HF OCR model class ────────────────────────────────────────────────────

def test_hf_path_uses_seq2seq_not_ctc():
    src = read("agent_a/dual_pipeline.py")
    assert "AutoModelForCTC" not in src, (
        "registry HF models are seq2seq (TrOCR/VisionEncoderDecoder) — "
        "CTC decoding produces garbage or crashes"
    )
    assert "AutoModelForVision2Seq" in src and ".generate(" in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("✅ all voyant/fence/hf tests passed")
