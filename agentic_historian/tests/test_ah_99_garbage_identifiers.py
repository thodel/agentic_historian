"""Tests for #99: garbage identifiers/keys in model_selector.py.

Purged: CJK token 秘书体, CJK-in-identifier date自由, the leading-space key
" syrisch" (never matched), and duplicate dict keys hindi/sanskrit.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_99_garbage_identifiers.py
"""

import re
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

MS_PATH = PKG / "agent_a" / "model_selector.py"
SRC = MS_PATH.read_text(encoding="utf-8")


def test_no_cjk_or_arabic_in_source():
    garbage = re.findall(r"[一-鿿؀-ۿ]", SRC)
    assert not garbage, f"garbage tokens still present: {set(garbage)}"


def test_no_date自由_identifier():  # noqa: N802 (the whole point is this name is gone)
    assert "date自由" not in SRC, "the CJK identifier date自由 must be renamed"
    assert "date_raw" in SRC, "expected the renamed date_raw field"


def test_no_leading_space_syrisch_key():
    assert '" syrisch"' not in SRC, "leading-space key ' syrisch' never matched — fix it"


def test_no_duplicate_hindi_sanskrit_keys():
    assert '"hindi": "hi", "hindi"' not in SRC, "duplicate hindi key"
    assert '"sanskrit": "sa", "sanskrit"' not in SRC, "duplicate sanskrit key"


# ── Functional ───────────────────────────────────────────────────────────────

def test_syrisch_now_resolves():
    from agent_a import model_selector as ms
    assert ms.LANG_ALIASES.get("syrisch") == "syr", (
        "'syrisch' must resolve now that the leading space is removed"
    )


def test_date_raw_field_populated():
    from agent_a import model_selector as ms
    c = ms.SourceCriteria.from_agent_b("Gotische Kursive deutsch 15. Jh.")
    assert hasattr(c, "date_raw")
    assert c.date_raw == "Gotische Kursive deutsch 15. Jh."


def test_selection_still_works():
    from agent_a import model_selector as ms
    c = ms.SourceCriteria.from_agent_b("Gotische Kursive deutsch 15. Jh.")
    matches = ms.select_kraken_model(c, top_k=2)
    assert matches and matches[0].model is not None
