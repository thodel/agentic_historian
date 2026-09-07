"""Unicode-block localisation and the normalisation ladder."""
from agentic_historian.eval import blocks as B


def test_block_of_covers_the_medieval_repertoire():
    assert B.block_of("a") == "Basic Latin"
    assert B.block_of("ö") == "Latin-1 Supplement"
    assert B.block_of("ſ") == "Latin Extended-A"          # long s
    assert B.block_of("ͤ") == "Combining Diacritical Marks"   # combining e
    assert B.block_of("·") == "Latin-1 Supplement"        # middle dot
    assert B.block_of("一") == "other"


def test_profile_attributes_ops_to_the_reference_character():
    # ſ substituted by s, and a lost middle dot
    p = B.profile([("ſo·", "so")])
    assert p["sub"]["Latin Extended-A"] == 1
    assert p["confusion"][("ſ", "s")] == 1
    assert p["del"]["Latin-1 Supplement"] == 1
    assert p["dropped"]["·"] == 1
    assert p["gt"]["Basic Latin"] == 1


def test_insertion_is_attributed_to_the_invented_character():
    p = B.profile([("ab", "aXb")])
    assert p["ins"]["Basic Latin"] == 1
    assert p["invented"]["X"] == 1
    assert sum(p["sub"].values()) == 0


def test_ladder_is_monotone_and_folds_the_long_s():
    pairs = [("ſo ſind ſie", "so sind sie")]
    text = B.ladder(pairs)
    rates = [float(x) for x in
             __import__("re").findall(r"(\d+\.\d+)%", text)]
    assert rates[0] > 0                      # raw: every long s counts
    assert rates[-1] == 0.0                  # fully folded: identical
    assert all(a >= b - 1e-9 for a, b in zip(rates, rates[1:]))


def test_show_makes_a_combining_mark_visible():
    assert B.show("́").startswith("◌")
    assert B.show(" ") == "␣"
