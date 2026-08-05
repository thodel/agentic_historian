"""#320 (cause 1): Agent B must receive the page image on a single-doc /run.

The old guard read `image_path=str(img) if img != fp else None` — "withhold the
image whenever the file IS the image", which is exactly the /run case. Agent B ran
text-only on every single-doc run, so when Phase 1 collapsed into `uuuu` it took
the honest #276 refusal instead of looking at the page, and neither the image-only
path (#301) nor "send the image" (#308) could ever fire.

Offline — no GPUStack, no gateway. Run from the repo root:
    pytest agentic_historian/tests/test_ah_320_agent_b_image_on_single_run.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import orchestrator     # noqa: E402


def _img(tmp_path, name="BAT_664_r_00027.jpg"):
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff")          # enough to exist with a jpg suffix
    return p


# ── the reported bug ─────────────────────────────────────────────────────────

def test_the_image_is_passed_when_the_file_itself_is_the_page(tmp_path):
    """The /run case: image_path=None, so img == fp. The old guard returned None
    here — the single most common single-doc invocation."""
    fp = _img(tmp_path)
    assert orchestrator._vision_image_path(fp, fp) == str(fp)


def test_an_explicit_image_path_is_still_passed(tmp_path):
    """The grouped/reprocess case, which already worked."""
    fp = tmp_path / "order.pdf"
    fp.write_bytes(b"%PDF-1.4")
    img = _img(tmp_path)
    assert orchestrator._vision_image_path(img, fp) == str(img)


# ── what must still be withheld ──────────────────────────────────────────────

def test_a_pdf_is_never_handed_to_the_vision_model(tmp_path):
    """`fp` can be a PDF; chat_vision cannot read one, so withholding is correct
    here — the guard just had the wrong reason before."""
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert orchestrator._vision_image_path(pdf, pdf) is None


def test_a_missing_file_is_withheld(tmp_path):
    """A path that does not resolve would fail inside chat_vision instead of
    degrading to the text-only description."""
    ghost = tmp_path / "not-there.jpg"
    assert orchestrator._vision_image_path(ghost, ghost) is None


def test_an_unknown_container_is_withheld(tmp_path):
    odd = tmp_path / "scan.zip"
    odd.write_bytes(b"PK\x03\x04")
    assert orchestrator._vision_image_path(odd, odd) is None


def test_none_is_handled(tmp_path):
    assert orchestrator._vision_image_path(None, tmp_path / "x.jpg") is None


@pytest.mark.parametrize("ext", [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"])
def test_every_ingestible_image_type_reaches_agent_b(tmp_path, ext):
    """switchdrive.INGEST_EXTS accepts these, so the pipeline can receive them and
    Agent B must not silently drop a format the ingest layer allows."""
    p = tmp_path / f"page{ext}"
    p.write_bytes(b"\x00")
    assert orchestrator._vision_image_path(p, p) == str(p)


def test_case_is_ignored(tmp_path):
    p = tmp_path / "PAGE.JPG"
    p.write_bytes(b"\x00")
    assert orchestrator._vision_image_path(p, p) == str(p)


# ── the wiring ───────────────────────────────────────────────────────────────

def test_the_pipeline_uses_the_helper_not_the_old_identity_guard():
    """Guards the actual call site: the bug was that `img != fp` decided this.

    Asserted on the `image_path=` argument specifically, not on the bare expression
    — the helper's own docstring quotes the old guard to explain it, so a loose
    substring scan would match the documentation and fail forever.
    """
    src = Path(orchestrator.__file__).read_text(encoding="utf-8")
    assert "image_path=_vision_image_path(img, fp)" in src
    assert "image_path=str(img) if img != fp else None" not in src
