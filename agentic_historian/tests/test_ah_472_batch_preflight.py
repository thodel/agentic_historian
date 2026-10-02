"""The batch asks for room before it walks the share, not after page 1 (#472).

The order of a corpus run was: 24 minutes enumerating the Nextcloud share, the
first page, the vLLM cold start, and **then**

    GPU 1 has 9742 MB free, qwen3.5-4b-german-xix-v2 needs 15848 MB

That is the arrangement that maximises the price of the answer. The question
takes 200 ms and was asked after 25 minutes — and the run left a half-filled
output directory and an exit code nobody could read without the log.

The assertion that holds the 24 minutes is `test_a_refused_model_never_reaches
_the_share`: `list_pages` must not be called. The rest is the shape of the
refusal, and the two things it must not do — block on *unknown*, and take the
other models down with one that does not fit.
"""

import functools
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_batch as batch  # noqa: E402
from utils.atr_gpu import Headroom, Occupant  # noqa: E402


@functools.lru_cache(maxsize=1)
def entry():
    """The CLI module, loaded by path.

    `import __main__` under pytest is pytest's own entry point, not this
    package's — and the module guard means loading it under another name runs
    nothing.
    """
    spec = importlib.util.spec_from_file_location("ah_cli", PKG / "__main__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

XIX = "qwen3.5-4b-german-xix-v2"
MEDIEVAL = "qwen3vl-medieval-german-v3"

#: The incident of 21.09.2026, with the numbers it actually reported.
PARTY = Occupant(pid=3010685, used_mib=11858, service="atr-party.service",
                 user="tobias", age_s=6000.0)
TROCR = Occupant(pid=3012388, used_mib=2970, service="atr-trocr.service",
                 user="tobias", age_s=6000.0)
#: Four gunicorn workers of another user on idhefix card 0 — in the report,
#: never an option.
STRANGER = Occupant(pid=999, used_mib=10392, service="gunicorn", user="change",
                    age_s=3_542_400.0)


def too_small(model=XIX) -> Headroom:
    return Headroom(model=model, card=1, needed_mib=15848, free_mib=9742,
                    shortfall_mib=6106, fits=False, ours=[PARTY, TROCR],
                    theirs=[STRANGER],
                    reason="card 1 has 9,742 MiB free and needs 15,848")


def roomy(model=MEDIEVAL) -> Headroom:
    return Headroom(model=model, card=1, needed_mib=15848, free_mib=40000,
                    shortfall_mib=0, fits=True, ours=[], theirs=[])


def unknown(model=XIX) -> Headroom:
    """A genuine unknown: the gateway has the model, the registry is incomplete.

    This is the case #472 insists must still run — the one where somebody forgot
    to fill in `vram_mb`. `registered=True`, because /models listed it.
    """
    return Headroom(model=model, card=1, registered=True,
                    reason=f"{model} declares no vram_mb; "
                           "how much it needs is unrecorded, not zero")


def unlisted(model="kraken-bohemian_19th_v2") -> Headroom:
    """The 23 minutes of 2026-10-01: an id the gateway answered about and does
    not have. One character off `kraken-bohemian_19th`."""
    return Headroom(model=model, registered=False,
                    reason=f"{model} is not in /models — "
                           "it is not registered, or not servable on this host")


def checked(*headrooms) -> batch.Preflight:
    """A preflight over the models these headrooms name, with no gateway."""
    by_model = {h.model: h for h in headrooms}
    return batch.preflight(list(by_model), probe={},
                           headroom=lambda model, _probe: by_model[model])


# ── the verdict ─────────────────────────────────────────────────────────────
def test_a_model_that_does_not_fit_is_not_runnable():
    result = checked(too_small())

    assert result.runnable == []
    assert [v.model for v in result.refused] == [XIX]
    assert result.nothing_runs


def test_a_model_that_fits_is_runnable():
    result = checked(roomy())

    assert result.runnable == [MEDIEVAL]
    assert result.refused == []
    assert not result.nothing_runs


def test_unknown_runs_and_is_not_a_refusal():
    """#472 is explicit: unknown must not block, or it blocks the first model
    whose `vram_mb` somebody forgot to fill in — and then the check gets
    removed rather than the field filled."""
    result = checked(unknown())

    assert result.runnable == [XIX]
    assert result.refused == []
    assert [v.model for v in result.unknown] == [XIX]
    assert not result.nothing_runs


def test_of_two_models_the_one_that_fits_goes_on():
    """Model-major: a model that does not fit does not take the others with it,
    the same rule the 404 follows."""
    result = checked(too_small(), roomy())

    assert result.runnable == [MEDIEVAL]
    assert [v.model for v in result.refused] == [XIX]
    assert not result.nothing_runs


def test_nothing_runs_only_when_every_model_was_refused():
    assert checked(too_small(), too_small(MEDIEVAL)).nothing_runs
    assert not checked(too_small(), unknown(MEDIEVAL)).nothing_runs


def test_an_empty_model_list_is_not_a_refusal():
    """`--models` empty is caught earlier, with its own message; this must not
    claim the cards refused something nobody asked for."""
    assert not batch.preflight([], probe={}).nothing_runs


# ── the message has to make somebody able to act ───────────────────────────
def test_the_refusal_names_card_need_free_and_shortfall():
    text = "\n".join(checked(too_small()).lines())

    assert "card 1" in text
    assert "15,848 MiB" in text and "9,742 MiB" in text
    assert "6,106 MiB short" in text


def test_the_refusal_names_our_services_largest_first():
    """So that "what would I stop" needs no further research."""
    text = "\n".join(checked(too_small()).lines())

    assert "atr-party.service 11,858 MiB" in text
    assert text.index("atr-party") < text.index("atr-trocr")


def test_the_refusal_separates_what_is_not_ours():
    """Four gunicorn workers of another user are in the report and are never an
    option. A line that summed them with ours would invite exactly the mistake
    V4 must not make."""
    text = "\n".join(checked(too_small()).lines())

    assert "not ours" in text
    assert "gunicorn (change) 10,392 MiB" in text


def test_the_refusal_names_the_way_past_it():
    assert "--no-preflight" in "\n".join(checked(too_small()).lines())


def test_a_fitting_model_says_so_in_one_line():
    text = "\n".join(checked(roomy()).lines())

    assert "ok qwen3vl-medieval-german-v3" in text
    assert "--no-preflight" not in text


def test_an_unknown_model_says_why_and_that_it_runs():
    text = "\n".join(checked(unknown()).lines())

    assert "declares no vram_mb" in text
    assert "running anyway" in text


# ── the probe is taken once ────────────────────────────────────────────────
def test_every_model_is_judged_against_one_probe():
    """`gpu_headroom`'s own docstring: asking the gateway twice for the same two
    reports is two chances for them to disagree."""
    seen = []

    def spy(model, probe):
        seen.append((model, id(probe)))
        return roomy(model)

    probe = {"models": {}, "gpu_serving": {}}
    batch.preflight([XIX, MEDIEVAL], probe=probe, headroom=spy)

    assert [m for m, _ in seen] == [XIX, MEDIEVAL]
    assert len({pid for _, pid in seen}) == 1


def test_a_gateway_that_cannot_be_reached_is_unknown_not_an_exception(monkeypatch):
    """A preflight that throws is one that gets taken out."""
    def explode(model, probe):
        assert probe == {}
        return unknown(model)

    monkeypatch.setitem(sys.modules, "mcp_atr.server",
                        SimpleNamespace(gateway_probe=lambda: (_ for _ in ()).throw(
                            OSError("no route to host"))))

    result = batch.preflight([XIX], headroom=explode)

    assert result.runnable == [XIX]


# ── the whole point: nothing is read and nothing is written ────────────────
#: What the stubbed share raises. The CLI catches it and reports it, so the
#: marker in stderr is the proof that the gate opened — an exception escaping
#: would only mean the CLI stopped handling its own errors.
WALKED = "the share was walked"


def reached_the_share(captured) -> bool:
    return WALKED in captured.err


@pytest.fixture
def cli(monkeypatch, tmp_path):
    """`__main__.atr_batch` with the share and the gateway replaced.

    `list_pages` raises: #472's assertion is that it is never reached, and a
    stub that merely records could be reached and still pass something weaker.
    """
    from utils import nextcloud

    cli_module = entry()

    class NeverWalks:
        def __init__(self, *a, **k):
            pass

        def list_pages(self, *a, **k):
            raise OSError(WALKED)

    monkeypatch.setattr(nextcloud, "WebdavPageSource", NeverWalks)
    monkeypatch.setattr(nextcloud, "is_remote_source", lambda s: True)
    monkeypatch.setattr(nextcloud, "remote_source_root", lambda s: "Lassberg")
    out_root = tmp_path / "runs" / "r1"

    def invoke(headrooms, **over):
        by_model = {h.model: h for h in headrooms}
        monkeypatch.setattr(batch, "preflight", lambda models, *a, **k: batch.Preflight(
            [batch.ModelVerdict(model=m, headroom=by_model[m]) for m in models]))
        args = SimpleNamespace(
            source="dav:Lassberg", models=",".join(by_model), run="r1",
            out_root=str(out_root), limit=None, sample=None, seed=1,
            concurrency=1, retries=0, cache_dir=str(tmp_path / "cache"),
            no_listing_cache=False, dry_run=False, no_preflight=False)
        for key, value in over.items():
            setattr(args, key, value)
        return cli_module.atr_batch(args), out_root

    return invoke


def test_a_refused_model_never_reaches_the_share(cli, capsys):
    """The assertion that holds the 24 minutes."""
    code, _ = cli([too_small()])

    assert code == 1
    assert "6,106 MiB short" in capsys.readouterr().err


def test_a_refused_run_leaves_no_output_directory(cli):
    """No report.md, no run directory — the run did not start."""
    _, out_root = cli([too_small()])

    assert not out_root.exists()


def test_a_run_whose_models_fit_goes_on_to_the_share(cli, capsys):
    """The other half: the preflight must not become the thing that stops runs.
    Reaching the share is success here — it means the gate opened."""
    cli([roomy()])

    assert reached_the_share(capsys.readouterr())


def test_no_preflight_starts_even_when_nothing_fits(cli, capsys):
    """A check without an exit gets removed at the first false alarm instead of
    corrected (#472)."""
    cli([too_small()], no_preflight=True)

    assert reached_the_share(capsys.readouterr())


def test_no_preflight_does_not_even_ask(cli, capsys):
    cli([too_small()], no_preflight=True)

    assert "preflight:" not in capsys.readouterr().out


def test_unknown_does_not_stop_the_run(cli, capsys):
    cli([unknown()])

    assert reached_the_share(capsys.readouterr())


def test_one_refused_model_does_not_stop_the_other(cli, capsys):
    cli([too_small(), roomy()])

    assert reached_the_share(capsys.readouterr())


def test_the_refused_model_is_dropped_and_the_other_is_submitted(
        monkeypatch, tmp_path):
    """The wiring, end to end: `models` reaching `run_batch` is the runnable
    set, not the list the operator typed. The share answers here instead of
    raising, so the run gets far enough to show what it would do."""
    from utils import nextcloud

    class OnePage:
        def __init__(self, *a, **k):
            self.hits = self.misses = self.source_bytes = 0

        def list_pages(self, *a, **k):
            return ["Lassberg/doc/0001.jpg"]

    monkeypatch.setattr(nextcloud, "WebdavPageSource", OnePage)
    monkeypatch.setattr(nextcloud, "is_remote_source", lambda s: True)
    monkeypatch.setattr(nextcloud, "remote_source_root", lambda s: "Lassberg")

    by_model = {XIX: too_small(), MEDIEVAL: roomy()}
    monkeypatch.setattr(batch, "preflight", lambda models, *a, **k: batch.Preflight(
        [batch.ModelVerdict(model=m, headroom=by_model[m]) for m in models]))

    submitted: list[list[str]] = []

    def record(pages, models, *a, **k):
        submitted.append(list(models))
        return batch.BatchReport(run="r1", out_root=tmp_path, pages=len(pages))

    monkeypatch.setattr(batch, "run_batch", record)
    monkeypatch.setattr(batch, "gateway_recogniser", lambda *a, **k: object())
    monkeypatch.setattr(batch, "write_report", lambda report: tmp_path / "report.md")
    monkeypatch.setattr(batch, "format_report", lambda report: "")

    code = entry().atr_batch(SimpleNamespace(
        source="dav:Lassberg", models=f"{XIX},{MEDIEVAL}", run="r1",
        out_root=str(tmp_path / "out"), limit=None, sample=None, seed=1,
        concurrency=1, retries=0, cache_dir=str(tmp_path / "cache"),
        no_listing_cache=False, dry_run=False, no_preflight=False))

    assert code == 0
    assert submitted == [[MEDIEVAL]]


def test_the_flag_exists_on_the_parser():
    parsed = entry().build_parser().parse_args(
        ["atr-batch", "--source", "x", "--models", "m", "--no-preflight"])

    assert parsed.no_preflight is True


def test_the_flag_defaults_to_checking():
    parsed = entry().build_parser().parse_args(
        ["atr-batch", "--source", "x", "--models", "m"])

    assert parsed.no_preflight is False


# ── a model id the gateway does not have (2026-10-01) ────────────────────────
#
# `--models kraken-bohemian_19th_v2`, one character off `kraken-bohemian_19th`.
# The preflight said
#
#     ?  kraken-bohemian_19th_v2: is not in /models — running anyway
#
# and the run then enumerated the whole 6742-page share for 23 minutes before
# taking a 404 on its first page. It would have taken that 404 on all 6742.
#
# `fits=None` had come to carry four situations and only three are doubt:
# /models silent, /gpu silent, no `vram_mb` declared — and this, where the
# gateway answered, listed the ids it has, and this was not among them. That is
# not a missing registry field. It is the gateway saying no.

def test_an_unlisted_model_is_refused_not_run_anyway():
    result = checked(unlisted())

    assert result.runnable == []
    assert [v.model for v in result.refused] == ["kraken-bohemian_19th_v2"]
    assert result.nothing_runs


def test_an_unlisted_model_is_not_filed_as_unknown():
    """The distinction is the whole fix: `unknown` means run anyway."""
    result = checked(unlisted())

    assert result.unknown == []
    assert [v.model for v in result.unlisted] == ["kraken-bohemian_19th_v2"]


def test_a_missing_vram_field_still_runs():
    """The other half, unchanged: #472's case must not be caught by this."""
    result = checked(unknown())

    assert result.runnable == [XIX]
    assert result.unlisted == []
    assert [v.model for v in result.unknown] == [XIX]


def test_the_good_models_in_a_mixed_list_still_run():
    """A typo in the third model must not throw away the two that exist."""
    result = checked(roomy(), unlisted())

    assert result.runnable == [MEDIEVAL]
    assert not result.nothing_runs


def test_the_advice_is_not_to_disable_the_preflight():
    """`--no-preflight` would move the 404 from once to 6742 times."""
    lines = "\n".join(checked(unlisted()).lines())

    assert "gateway_models" in lines
    assert "Stop one of ours" not in lines


def test_a_full_card_still_gets_the_old_advice():
    lines = "\n".join(checked(too_small()).lines())

    assert "Stop one of ours, or run with --no-preflight." in lines
    assert "gateway_models" not in lines


def test_the_line_says_every_page_would_fail():
    line = checked(unlisted()).verdicts[0].line()

    assert line.startswith("NO ")
    assert "404" in line


def test_an_unlisted_model_never_reaches_the_share(cli, capsys):
    """The 23 minutes. Reaching the share is the failure here."""
    code, out_root = cli([unlisted()])

    assert code == 1
    assert not reached_the_share(capsys.readouterr())
    assert not out_root.exists()


def test_the_error_names_the_id_not_the_card(cli, capsys):
    """"no model fits on its card" was the wrong sentence: the card was fine."""
    cli([unlisted()])

    err = capsys.readouterr().err
    assert "the gateway has none of these model ids" in err
    assert "fits on its card" not in err
