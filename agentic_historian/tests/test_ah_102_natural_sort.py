"""Tests for #102: grouped-order page sort must be natural, not lexicographic.

Run offline (no GPUStack/VPN) — file-level checks + functional tests.

Run:  python tests/test_ah_102_natural_sort.py   (or: pytest)
"""

import re


def _natural_key(name: str):
    """Natural sort key: splits 'page_10.jpg' into ['page_', 10, '.jpg']."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", name)]


def test_natural_sort_key_function():
    """_natural_key must produce correct ordering for page filenames."""
    names = ["p_2.jpg", "p_10.jpg", "p_1.jpg", "p_20.jpg", "p_3.jpg"]
    sorted_names = sorted(names, key=_natural_key)
    assert sorted_names == ["p_1.jpg", "p_2.jpg", "p_3.jpg", "p_10.jpg", "p_20.jpg"], (
        f"Natural sort failed: {sorted_names}"
    )


def test_natural_sort_against_lexicographic():
    """Lexicographic sort puts page_10 before page_2. Natural sort fixes this."""
    names = ["page_010.txt", "page_002.txt", "page_1.txt", "page_10.txt"]
    lexicographic = sorted(names)
    natural = sorted(names, key=_natural_key)

    # Lexicographic gives wrong order
    assert lexicographic[0] == "page_001.txt" or lexicographic[0] == "page_002.txt", (
        "Sanity: lexicographic should put page_002 before page_01"
    )
    # Natural gives correct order
    assert natural == ["page_1.txt", "page_002.txt", "page_010.txt", "page_10.txt"], (
        f"Sanity: natural sort, expected [page_1, page_2, page_10], got {natural}"
    )


def test_natural_sort_with_leading_zeros():
    """001, 002, 010 should sort as 1, 2, 10 (not 1, 10, 2)."""
    names = ["scan_010.tiff", "scan_002.tiff", "scan_1.tiff"]
    sorted_names = sorted(names, key=_natural_key)
    assert sorted_names == ["scan_1.tiff", "scan_002.tiff", "scan_010.tiff"], (
        f"Leading zeros should still sort numerically: {sorted_names}"
    )


def test_orchestrator_uses_natural_key():
    """orchestrator.py must define and use _natural_key for page sorting."""
    with open("agentic_historian/orchestrator.py") as f:
        src = f.read()

    assert "def _natural_key" in src, (
        "orchestrator.py must define _natural_key function"
    )
    assert "_natural_key(p.name)" in src, (
        "page sort must use _natural_key(p.name), not p.name alone"
    )
    assert "key=lambda p: p.name)" not in src, (
        "Broken lexicographic sort key= lambda p: p.name still present"
    )


if __name__ == "__main__":
    test_natural_sort_key_function()
    print("PASS: test_natural_sort_key_function")
    test_natural_sort_against_lexicographic()
    print("PASS: test_natural_sort_against_lexicographic")
    test_natural_sort_with_leading_zeros()
    print("PASS: test_natural_sort_with_leading_zeros")
    test_orchestrator_uses_natural_key()
    print("PASS: test_orchestrator_uses_natural_key")
    print("\nAll #102 tests passed.")
