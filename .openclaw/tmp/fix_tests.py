with open("agentic_historian/tests/test_eval_harness.py") as f:
    src = f.read()

# Fix 1: test_insertion_counts
old = """    def test_insertion_counts(self):
        # "abc" vs "abcd" — 1 insertion = 1/3
        c = cer("abc", "abcd")
        assert c == 1.0  # 1 insertion, ref len=3, 1/3 rounded? actually it's 1.0
        # The formula: distance / m. m=3, dist=1+1 (insert at end+sub)? Let's compute:
        # Actually: abc→abcd: insert 'd' at end. Edit distance = 1. 1/3 ≈ 0.333
        # Hmm let me just check it rounds reasonably
        assert 0.25 < c < 0.45"""

new = """    def test_insertion_counts(self):
        # "abc" vs "abcd" — 1 insertion, ref len=3 -> CER = 1/3
        c = cer("abc", "abcd")
        assert 0.25 < c < 0.45, f"Expected ~0.33, got {c}" """

assert old in src, "test_insertion_counts not found"
src = src.replace(old, new, 1)

# Fix 2: test_ignore_case_switch
old = """    def test_ignore_case_switch(self):
        # With ignore_case=True (default): same
        assert cer("Hallo", "hallo") == 0.0
        # With ignore_case=False: different
        assert cer("Hallo", "hallo", ignore_case=False) == 1.0"""

new = """    def test_ignore_case_switch(self):
        # With ignore_case=True (default): same
        assert cer("Hallo", "hallo") == 0.0
        # With ignore_case=False: H/h differ; "Hallo" (5 chars) -> 1 substitution -> CER=0.2
        assert cer("Hallo", "hallo", ignore_case=False) == 0.2"""

assert old in src, "test_ignore_case_switch not found"
src = src.replace(old, new, 1)

# Fix 3: test_fusion_beats_best_true
old = """    def test_fusion_beats_best_true(self):
        """Fused beats the best single engine."""
        result = cer_table(
            recognitions={
                "vlm": "Hans von Bern",
                "kraken": "Hans von Bernn",
            },
            fused="Hans von Bern",  # fused is also perfect
            reference=self.REF,
        )
        assert result["fusion_beats_best"] is True"""

new = """    def test_fusion_beats_best_true(self):
        # Fused has strictly lower CER than the best single engine.
        # vlm has 1 error, kraken has 2 errors; fused is perfect -> wins.
        result = cer_table(
            recognitions={
                "vlm": "Hans von Bernn",     # CER ~0.125 (1 insertion)
                "kraken": "Hans von Bernnn", # CER ~0.25  (2 insertions)
            },
            fused="Hans von Bern",           # CER = 0 — beats both
            reference=self.REF,
        )
        assert result["fusion_beats_best"] is True"""

assert old in src, "test_fusion_beats_best_true not found"
src = src.replace(old, new, 1)

with open("agentic_historian/tests/test_eval_harness.py", "w") as f:
    f.write(src)
print("Done")