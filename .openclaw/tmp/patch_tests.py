with open("agentic_historian/tests/test_eval_harness.py") as f:
    src = f.read()

# Fix 1: test_insertion_counts
old1 = '    def test_insertion_counts(self):\n        # "abc" vs "abcd" — 1 insertion = 1/3\n        c = cer("abc", "abcd")\n        assert c == 1.0  # 1 insertion, ref len=3, 1/3 rounded? actually it is 1.0\n        # The formula: distance / m. m=3, dist=1+1 (insert at end+sub)? Let us compute:\n        # Actually: abc to abcd: insert d at end. Edit distance = 1. 1/3 = 0.333\n        # Hmm let me just check it rounds reasonably\n        assert 0.25 < c < 0.45'
new1 = '    def test_insertion_counts(self):\n        # "abc" vs "abcd" — 1 insertion, ref len=3 -> CER = 1/3\n        c = cer("abc", "abcd")\n        assert 0.25 < c < 0.45, f"Expected ~0.33, got {c}"'
if old1 in src:
    src = src.replace(old1, new1, 1)
    print("Fixed 1")
else:
    print("NOT FOUND 1")

# Fix 2: test_ignore_case_switch
old2 = '    def test_ignore_case_switch(self):\n        # With ignore_case=True (default): same\n        assert cer("Hallo", "hallo") == 0.0\n        # With ignore_case=False: different\n        assert cer("Hallo", "hallo", ignore_case=False) == 1.0'
new2 = '    def test_ignore_case_switch(self):\n        # With ignore_case=True (default): same\n        assert cer("Hallo", "hallo") == 0.0\n        # With ignore_case=False: H/h differ; 1 substitution / 5 chars = 0.2\n        assert cer("Hallo", "hallo", ignore_case=False) == 0.2'
if old2 in src:
    src = src.replace(old2, new2, 1)
    print("Fixed 2")
else:
    print("NOT FOUND 2")

# Fix 3: test_fusion_beats_best_true (the tricky triple-quote one)
# Use a marker-based approach
marker = "TESTFUSIONREPLACEME"
old3 = marker
new3 = marker
src2 = src

# Find the block between "def test_fusion_beats_best_true" and the next "def "
import re
m = re.search(r'(    def test_fusion_beats_best_true\(self\):.*?)(?=\n    def )', src, re.DOTALL)
if m:
    old3 = m.group(1)
    new3 = ('    def test_fusion_beats_best_true(self):\n'
            '        # vlm=0.125, kraken=0.25, fused=0 -> fused wins\n'
            '        result = cer_table(\n'
            '            recognitions={\n'
            '                "vlm": "Hans von Bernn",     # CER ~0.125\n'
            '                "kraken": "Hans von Bernnn", # CER ~0.25\n'
            '            },\n'
            '            fused="Hans von Bern",           # CER = 0\n'
            '            reference=self.REF,\n'
            '        )\n'
            '        assert result["fusion_beats_best"] is True')
    src2 = src[:m.start()] + new3 + src[m.end():]
    print("Fixed 3")
else:
    print("NOT FOUND 3")

with open("agentic_historian/tests/test_eval_harness.py", "w") as f:
    f.write(src2)
print("Done")