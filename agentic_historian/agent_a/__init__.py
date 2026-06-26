"""
agent_a/ — Two-pronged HTR/OCR pipeline.

Pathway 1 (VLM):
  InternVL3-8B via GPUStack. Prompt guided by Agent B source description.

Pathway 2 (kraken + HF):
  2a) kraken baseline detection + OCR with community models
  2b) HuggingFace end-to-end or line-level OCR models (e.g. LightOnOCR)

Both transcriptions are reconciled via LLM into a final text.

Usage:
  from agent_a import transcribe_dual
  result = transcribe_dual("path/to/image.jpg", lang="de")
"""

from agent_a.dual_pipeline import transcribe_dual, DualTranscriptionResult

__all__ = ["transcribe_dual", "DualTranscriptionResult"]