"""#541: the bench that has to run before a second VLM joins the ensemble.

The cases below are mostly not about arithmetic. They are about the four
separations the report rests on, each of which exists because collapsing it
produced a wrong answer somewhere in this project already:

* **level** — handing a page to a line model is a different operation, so one
  median over both measures the segmentation;
* **collapse share** — a mean hides a collapse, and a collapse is how ``u-17``
  reached the site as a page of "uuuu";
* **coverage** — a median over the pages a candidate survived is not its median;
* **fusion** — #298 measured fusion voting the good reading down, so a candidate
  that reads well alone can still make the result worse.

Plus the refusal that matters most: on a set this size the bench must not order
two working models. #491 reached the same conclusion for perplexity and named
the failure — a signal used as a ranking certifies the step doing the damage.

Everything here runs offline. That is the design: ``run`` needs a GPU, ``score``
holds every judgement and needs nothing, so the analysis is reviewable and
testable without one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from eval import vlm_bench as vb  # noqa: E402

#: A real sentence from this material, so normalisation and folding behave the
#: way they will in production rather than on lorem ipsum.
TRUTH = ("Wir Johans von Habspurg tuon kunt allen den die disen brief ansehent "
         "oder hoerent lesen daz wir mit gutem willen und wolbedahtem muote "
         "gegeben haben dem closter ze Koenigsfelden zehen schillinge geltes")

NEAR = ("Wir Johans von Habspurg tuon kunt allen den die disen brief ansehnt "
        "oder horent lesen daz wir mit gutem willen und wolbedahtem muote "
        "gegeben haben dem closter ze Konigsfelden zehen schillinge geltes")

FAR = ("Wir Johann von Habsburg thun kund allen denen die diesen Brief sehen "
       "oder hoeren lesen dass wir mit gutem Willen gegeben haben dem Kloster "
       "zu Koenigsfelden zehn Schillinge Geldes jaehrlich")

COLLAPSE = "\n".join(["u", "uuu", "uu", "uuuu", "iuuu"] * 20)


def _write_run(tmp_path, models: dict[str, dict]) -> Path:
    """Build a run directory in the shape ``run`` produces.

    ``models`` is ``{model: {"level": str, "pages": {key: text|None}}}``; a
    ``None`` text means the candidate failed on that page, which writes a
    ``.json`` and no ``.txt`` — the distinction coverage is computed from.
    """
    run_dir = tmp_path / "run"
    for model, spec in models.items():
        out = run_dir / model
        out.mkdir(parents=True)
        for key, text in spec["pages"].items():
            (out / f"{key}.json").write_text(json.dumps({
                "key": key, "model_id": model, "backend": "gateway",
                "level": spec.get("level", "page"),
                "error": "" if text is not None else "EngineError: not resident",
                "elapsed_ms": 10, "chars": len(text or ""),
            }), encoding="utf-8")
            if text is not None:
                (out / f"{key}.txt").write_text(text, encoding="utf-8")
    return run_dir


class TestTheRunPhaseRecordsFailuresAsFailures:
    """An empty reading and a failed call must not look the same."""

    def test_a_failed_page_writes_no_text_file(self, tmp_path):
        """An empty .txt would score as 100 % CER and read as "it read nothing"."""
        candidate = vb.Candidate(model_id="qwen3vl-medieval-german-v3",
                                 backend="gateway", level="line")

        def boom(image, model_id):
            raise RuntimeError("model not resident on its card")

        reading = vb.transcribe_page(candidate, tmp_path / "p1.jpg",
                                     gpustack_fn=boom, gateway_fn=boom)
        written = vb.write_reading(tmp_path / "run", candidate, reading)

        assert written is None
        model_dir = tmp_path / "run" / "qwen3vl-medieval-german-v3"
        assert not list(model_dir.glob("*.txt")), (
            "a page the candidate failed on produced a reading file; the "
            "scorer cannot tell that from a model that read nothing"
        )
        record = json.loads((model_dir / "p1.json").read_text(encoding="utf-8"))
        assert "not resident" in record["error"]

    def test_a_failure_does_not_abort_the_run(self, tmp_path):
        """Seven models over eight pages must survive the sixth being down."""
        candidates = [vb.Candidate(model_id="good", backend="gateway"),
                      vb.Candidate(model_id="down", backend="gateway")]
        for i in (1, 2):
            (tmp_path / f"p{i}.jpg").write_bytes(b"x")

        def gateway(image, model_id):
            if model_id == "down":
                raise RuntimeError("503")
            return TRUTH

        readings = vb.run(candidates, sorted(tmp_path.glob("*.jpg")),
                          tmp_path / "run",
                          gpustack_fn=gateway, gateway_fn=gateway)

        assert len(readings) == 4
        assert sum(1 for r in readings if r.ok) == 2

    def test_the_backend_follows_the_candidate_not_the_name(self, tmp_path):
        """"vlm" and "vllm" are two backends; the field decides, not the id."""
        used = []
        (tmp_path / "p1.jpg").write_bytes(b"x")

        vb.run([vb.Candidate(model_id="qwen3.8-27b", backend="gpustack"),
                vb.Candidate(model_id="qwen3vl-8b-hebrew", backend="gateway")],
               [tmp_path / "p1.jpg"], tmp_path / "run",
               gpustack_fn=lambda i, m: used.append(("gpustack", m)) or TRUTH,
               gateway_fn=lambda i, m: used.append(("gateway", m)) or TRUTH)

        assert used == [("gpustack", "qwen3.8-27b"),
                        ("gateway", "qwen3vl-8b-hebrew")]

    def test_the_run_is_model_major(self, tmp_path):
        """The gateway's vLLM models are lazy on one GPU.

        Page-major order pays a model load per page. Not a micro-optimisation:
        it is the difference between a bench that finishes and one that does not.
        """
        order = []
        for i in (1, 2, 3):
            (tmp_path / f"p{i}.jpg").write_bytes(b"x")

        vb.run([vb.Candidate(model_id="a", backend="gateway"),
                vb.Candidate(model_id="b", backend="gateway")],
               sorted(tmp_path.glob("*.jpg")), tmp_path / "run",
               gpustack_fn=lambda i, m: TRUTH,
               gateway_fn=lambda i, m: order.append(m) or TRUTH)

        assert order == ["a", "a", "a", "b", "b", "b"], (
            "pages are interleaved across models, so every page pays a model "
            "load on a lazy single-GPU gateway"
        )


class TestAModelIdIsCheckedBeforeItBecomesAPath:
    """The #521 lesson, one repository over.

    ``write_reading`` puts the model id straight into ``<run>/<model_id>/``. An
    id with a separator in it writes into a nested directory, and ``load_run``
    iterates top-level directories only — so the run completes, costs the GPU
    time, and yields nothing. Measured: ``dh-unibe/qwen3vl`` wrote its files and
    ``load_run`` returned an empty dict.

    Not theoretical. The gateway reports an ``hf_repo`` beside every model
    (``dh-unibe/qwen3vl-medieval-german-v3``), and passing that where the id
    belongs is one line of a caller away.
    """

    def test_a_slashed_model_id_is_refused(self, tmp_path):
        import pytest

        candidate = vb.Candidate(model_id="dh-unibe/qwen3vl", backend="gateway")
        reading = vb.PageReading(key="p1", model_id=candidate.model_id, text=TRUTH)

        with pytest.raises(ValueError) as err:
            vb.write_reading(tmp_path / "run", candidate, reading)

        assert "dh-unibe/qwen3vl" in str(err.value)

    def test_a_dot_dot_model_id_is_refused(self, tmp_path):
        import pytest

        candidate = vb.Candidate(model_id="..", backend="gateway")
        with pytest.raises(ValueError):
            vb.write_reading(tmp_path / "run", candidate,
                             vb.PageReading(key="p1", model_id="..", text=TRUTH))

    def test_the_real_model_ids_are_accepted(self, tmp_path):
        """The refusal must not reject what the gateway actually serves."""
        for model_id in ("qwen3.8-27b", "internvl3-8b-instruct",
                         "qwen3vl-medieval-german-v3", "qwen3.5-4b-german-xix-v2",
                         "lightonocr-catmus-caroline"):
            candidate = vb.Candidate(model_id=model_id, backend="gateway")
            written = vb.write_reading(
                tmp_path / "run", candidate,
                vb.PageReading(key="p1", model_id=model_id, text=TRUTH))
            assert written is not None and written.exists()

        readings, _meta = vb.load_run(tmp_path / "run")
        assert len(readings) == 5


class TestWhitespaceIsNotAReading:

    def test_a_whitespace_only_answer_counts_as_no_text(self, tmp_path):
        """Otherwise it is recorded as a reading and scored at ~100 % CER."""
        import pytest

        for backend in (vb.gpustack_backend, vb.gateway_backend):
            assert callable(backend)

        candidate = vb.Candidate(model_id="m", backend="gateway")
        reading = vb.transcribe_page(
            candidate, tmp_path / "p1.jpg",
            gpustack_fn=lambda i, m: "   \n  ",
            gateway_fn=lambda i, m: "   \n  ")

        assert not reading.ok, (
            "a whitespace-only answer was recorded as a reading; it would be "
            "scored as a failed transcription instead of a failed call"
        )
        assert vb.write_reading(tmp_path / "run", candidate, reading) is None


class TestCoverageIsNotQuality:

    def test_a_candidate_that_answered_half_the_pages_says_so(self, tmp_path):
        run_dir = _write_run(tmp_path, {
            "spotty": {"pages": {"p1": TRUTH, "p2": None, "p3": None, "p4": TRUTH}},
        })
        readings, meta = vb.load_run(run_dir)
        scores = vb.score_candidates(readings, meta, {"p1": TRUTH, "p4": TRUTH})

        assert scores[0].attempted == 4
        assert scores[0].answered == 2
        assert scores[0].coverage == 0.5

    def test_a_candidate_with_no_reading_at_all_is_still_reported(self, tmp_path):
        """Dropping it would make a model that never answered look absent."""
        run_dir = _write_run(tmp_path, {
            "dead": {"pages": {"p1": None, "p2": None}},
        })
        readings, meta = vb.load_run(run_dir)
        scores = vb.score_candidates(readings, meta, {"p1": TRUTH})

        assert [s.model_id for s in scores] == ["dead"]
        assert scores[0].scored == 0
        assert scores[0].coverage == 0.0
        assert "`dead`" in vb.format_report(scores, vb.FusionEffect())

    def test_only_pages_with_ground_truth_are_scored(self, tmp_path):
        run_dir = _write_run(tmp_path, {
            "m": {"pages": {"p1": TRUTH, "p2": TRUTH, "p3": TRUTH}},
        })
        readings, meta = vb.load_run(run_dir)
        scores = vb.score_candidates(readings, meta, {"p1": TRUTH})

        assert scores[0].scored == 1, (
            "a candidate was credited for a page nobody can check"
        )
        assert scores[0].answered == 3


class TestTheCollapseIsReportedSeparately:

    def test_a_collapsed_page_is_counted(self, tmp_path):
        run_dir = _write_run(tmp_path, {
            "collapser": {"pages": {"p1": TRUTH, "p2": COLLAPSE}},
        })
        readings, meta = vb.load_run(run_dir)
        scores = vb.score_candidates(readings, meta, {"p1": TRUTH, "p2": TRUTH})

        assert scores[0].collapsed == 1
        assert scores[0].collapse_share == 0.5

    def test_real_text_is_not_counted_as_collapsed(self, tmp_path):
        run_dir = _write_run(tmp_path, {
            "fine": {"pages": {"p1": TRUTH, "p2": NEAR, "p3": FAR}},
        })
        readings, meta = vb.load_run(run_dir)
        scores = vb.score_candidates(readings, meta, {"p1": TRUTH})

        assert scores[0].collapsed == 0

    def test_the_collapse_uses_the_pipeline_definition(self):
        """Not a second rule written here — that is the #538 drift again."""
        from agents.text_recognition import _is_degenerate

        for text in (COLLAPSE, "u" * 400, ""):
            assert vb.is_degenerate(text) == _is_degenerate(text)

    def test_the_report_names_the_collapse(self, tmp_path):
        run_dir = _write_run(tmp_path, {
            "collapser": {"pages": {"p1": COLLAPSE, "p2": COLLAPSE}},
        })
        readings, meta = vb.load_run(run_dir)
        report = vb.format_report(
            vb.score_candidates(readings, meta, {"p1": TRUTH}), vb.FusionEffect())

        assert "Collapse" in report
        assert "2 of 2" in report


class TestLevelsAreNeverMixed:

    def test_line_and_page_candidates_get_separate_tables(self, tmp_path):
        run_dir = _write_run(tmp_path, {
            "page-model": {"level": "page", "pages": {"p1": TRUTH}},
            "line-model": {"level": "line", "pages": {"p1": NEAR}},
        })
        readings, meta = vb.load_run(run_dir)
        report = vb.format_report(
            vb.score_candidates(readings, meta, {"p1": TRUTH}), vb.FusionEffect())

        assert "## Level: page" in report
        assert "## Level: line" in report
        assert "not comparable with each other" in report, (
            "two level tables were printed without saying they cannot be read "
            "against each other"
        )

    def test_one_level_prints_no_incomparability_warning(self, tmp_path):
        run_dir = _write_run(tmp_path, {
            "a": {"level": "page", "pages": {"p1": TRUTH}},
            "b": {"level": "page", "pages": {"p1": NEAR}},
        })
        readings, meta = vb.load_run(run_dir)
        report = vb.format_report(
            vb.score_candidates(readings, meta, {"p1": TRUTH}), vb.FusionEffect())

        assert "not comparable with each other" not in report

    def test_records_disagreeing_about_the_level_say_mixed(self, tmp_path):
        """Guessing would put the candidate in the wrong table."""
        run_dir = tmp_path / "run"
        (run_dir / "m").mkdir(parents=True)
        for key, level in (("p1", "line"), ("p2", "page")):
            (run_dir / "m" / f"{key}.json").write_text(json.dumps(
                {"key": key, "level": level, "error": ""}), encoding="utf-8")
            (run_dir / "m" / f"{key}.txt").write_text(TRUTH, encoding="utf-8")

        _readings, meta = vb.load_run(run_dir)
        assert meta["m"]["level"] == "mixed"


class TestItRefusesToRankOnTooFewPages:

    def test_a_candidate_under_the_floor_is_marked(self, tmp_path):
        run_dir = _write_run(tmp_path, {
            "m": {"pages": {"p1": TRUTH, "p2": NEAR}},
        })
        readings, meta = vb.load_run(run_dir)
        scores = vb.score_candidates(readings, meta, {"p1": TRUTH, "p2": TRUTH})

        assert scores[0].scored == 2
        assert not scores[0].rankable

        report = vb.format_report(scores, vb.FusionEffect())
        assert "⚠" in report
        assert "order nothing" in report

    def test_the_floor_is_reached_not_exceeded(self, tmp_path):
        """8 pages — the Inzigkofen set — is exactly the edge, and counts."""
        pages = {f"p{i}": TRUTH for i in range(vb.RANKABLE_PAGES)}
        run_dir = _write_run(tmp_path, {"m": {"pages": pages}})
        readings, meta = vb.load_run(run_dir)
        scores = vb.score_candidates(readings, meta,
                                     {k: TRUTH for k in pages})

        assert scores[0].scored == vb.RANKABLE_PAGES
        assert scores[0].rankable

    def test_the_report_always_states_the_veto_not_rank_limit(self, tmp_path):
        """Even with plenty of pages. The limit is the sample, not the count."""
        pages = {f"p{i}": TRUTH for i in range(20)}
        run_dir = _write_run(tmp_path, {"m": {"pages": pages}})
        readings, meta = vb.load_run(run_dir)
        report = vb.format_report(
            vb.score_candidates(readings, meta, {k: TRUTH for k in pages}),
            vb.FusionEffect())

        assert "veto, not rank" in report
        assert "does not rank two working models" in report.replace("**", "")


class TestTheFusionQuestion:

    def test_fusion_is_measured_against_the_best_single_candidate(self):
        readings = {"a": {"p1": NEAR}, "b": {"p1": FAR}}
        effect = vb.fusion_effect(readings, {"p1": TRUTH},
                                  fuse_fn=lambda texts: NEAR)

        assert effect.pages == 1
        # The fused text here IS the best single one, so it does not beat it.
        assert effect.fusion_wins == 0
        assert effect.median_best_single_cer == effect.median_fused_cer

    def test_a_fusion_that_helps_is_counted_as_a_win(self):
        readings = {"a": {"p1": NEAR}, "b": {"p1": FAR}}
        effect = vb.fusion_effect(readings, {"p1": TRUTH},
                                  fuse_fn=lambda texts: TRUTH)

        assert effect.fusion_wins == 1
        assert effect.win_share == 1.0

    def test_a_page_with_one_candidate_is_not_counted(self):
        """There is nothing to fuse; counting it would dilute the share."""
        readings = {"a": {"p1": NEAR, "p2": NEAR}, "b": {"p1": FAR}}
        effect = vb.fusion_effect(readings, {"p1": TRUTH, "p2": TRUTH},
                                  fuse_fn=lambda texts: TRUTH)

        assert effect.pages == 1

    def test_an_empty_reading_does_not_count_as_a_candidate(self):
        readings = {"a": {"p1": NEAR}, "b": {"p1": "   "}}
        effect = vb.fusion_effect(readings, {"p1": TRUTH},
                                  fuse_fn=lambda texts: TRUTH)

        assert effect.pages == 0

    def test_the_default_fuser_makes_no_llm_call(self, monkeypatch):
        """A bench that needs a model to score a model is not reusable in CI."""
        import utils.gpustack_client as gs

        def forbidden(*a, **kw):
            raise AssertionError("the bench called an LLM")

        monkeypatch.setattr(gs, "chat", forbidden)
        monkeypatch.setattr(gs, "chat_text", forbidden)

        effect = vb.fusion_effect({"a": {"p1": NEAR}, "b": {"p1": FAR}},
                                  {"p1": TRUTH})
        assert effect.pages == 1

    def test_the_report_frames_fusion_as_416s_question(self):
        report = vb.format_report([], vb.FusionEffect(pages=3, fusion_wins=1))
        assert "No candidate produced a reading" in report

        report = vb.format_report(
            [vb.CandidateScore(model_id="m", level="page", attempted=3,
                               answered=3, scored=3, median_cer=0.1)],
            vb.FusionEffect(pages=3, fusion_wins=1, median_fused_cer=0.2,
                            median_best_single_cer=0.1))
        assert "33%" in report
        assert "#416" in report
        assert "#298" in report


class TestTheRegistriesFeedTheBench:

    def test_both_backends_become_candidates(self):
        from agent_a.models import GatewayVLMModel, VLMModel

        gpustack = {"q": VLMModel(name="Q", endpoint="e", model_id="qwen3.8-27b",
                                  api_key_env="K")}
        gateway = {"v3": GatewayVLMModel(
            model_id="qwen3vl-medieval-german-v3", name="v3", level="line",
            scripts=["Kurrent"], languages=["de"], centuries=[15, 16, 17])}

        cands = vb.candidates_from_registries(gpustack, gateway)

        assert {(c.model_id, c.backend, c.level) for c in cands} == {
            ("qwen3.8-27b", "gpustack", "page"),
            ("qwen3vl-medieval-german-v3", "gateway", "line"),
        }

    def test_the_order_is_stable(self):
        from agent_a.models import GatewayVLMModel

        gateway = {k: GatewayVLMModel(model_id=k, name=k)
                   for k in ("c", "a", "b")}
        first = [c.model_id for c in vb.candidates_from_registries({}, gateway)]
        second = [c.model_id for c in vb.candidates_from_registries({}, gateway)]

        assert first == second == ["a", "b", "c"], (
            "an unstable order makes two runs of the same registries produce "
            "run directories that cannot be diffed"
        )

    def test_an_unknown_level_falls_back_to_page(self):
        from agent_a.models import GatewayVLMModel

        gateway = {"x": GatewayVLMModel(model_id="x", name="x", level="weird")}
        assert vb.candidates_from_registries({}, gateway)[0].level == "page"


class TestScoringIsOfflineAndDeterministic:

    def test_the_module_imports_without_a_gateway_or_a_key(self):
        """`score` must run on a laptop and in CI. The split is the design."""
        import importlib

        importlib.reload(vb)
        assert hasattr(vb, "score_candidates")

    def test_the_same_run_scores_the_same_twice(self, tmp_path):
        run_dir = _write_run(tmp_path, {
            "a": {"pages": {"p1": NEAR, "p2": FAR}},
            "b": {"pages": {"p1": FAR, "p2": NEAR}},
        })
        refs = {"p1": TRUTH, "p2": TRUTH}
        readings, meta = vb.load_run(run_dir)

        first = vb.format_report(vb.score_candidates(readings, meta, refs),
                                 vb.fusion_effect(readings, refs))
        second = vb.format_report(vb.score_candidates(readings, meta, refs),
                                  vb.fusion_effect(readings, refs))
        assert first == second

    def test_a_run_directory_that_is_not_one_fails_loudly(self, tmp_path):
        import pytest

        with pytest.raises(NotADirectoryError):
            vb.load_run(tmp_path / "nope")

    def test_the_cli_answers_a_bad_path_with_a_sentence(self, tmp_path, capsys):
        """A typo is the likeliest way here; a traceback answers it worse."""
        code = vb.main(["score", "--run", str(tmp_path / "nope"),
                        "--gt", str(tmp_path)])

        assert code == 2
        assert "not a run directory" in capsys.readouterr().err


class TestLocatingIsNotPoisonedByTheCandidatesUnderTest:
    """Found by running the bench, not by reading it.

    ``gt_score.Scored.located`` requires the readings to agree on which page a
    ground-truth file is. That is right for a corpus run, where every reading is
    a plausible attempt at every page. A bench is the opposite case *by design*:
    it contains candidates that may collapse, and candidates that answered on
    two pages out of twenty.

    Both vote, and both vote wrong in the same way. A collapsed reading is the
    same "uuuu" on every page, so whichever page comes first matches every
    ground-truth file. A reading that holds one page matches that page for every
    ground-truth file, because it is the only option it has. Either one breaks
    agreement, and the page is then scored for nobody — on the first real run of
    this bench that cost 2 of 3 pages for *all four* candidates.

    So locating and scoring are separated: a candidate can be excluded from
    deciding which page this is and still be measured on it. That is the whole
    point — the collapse is the finding, not a reason to lose the page.
    """

    def _gt(self, tmp_path, pages: dict[str, list[str]]):
        ns = ("xmlns='http://schema.primaresearch.org/PAGE/gts/pagecontent/"
              "2013-07-15'")
        out = tmp_path / "gt"
        out.mkdir()
        for i, (page_id, lines) in enumerate(pages.items(), start=1):
            body = "".join(
                f"<TextLine id='l{j}'><TextEquiv><Unicode>{t}</Unicode>"
                f"</TextEquiv></TextLine>" for j, t in enumerate(lines))
            region = f"<TextEquiv><Unicode>{chr(10).join(lines)}</Unicode></TextEquiv>"
            (out / f"doc9_page{i}.xml").write_text(
                (f"<?xml version='1.0' encoding='UTF-8'?><PcGts {ns}>"
                 f"<Metadata><TranskribusMetadata docId='9' pageId='{page_id}' "
                 f"pageNr='1' status='FINAL'/></Metadata>"
                 f"<Page imageFilename='{page_id}.tif' imageWidth='1' "
                 f"imageHeight='1'><TextRegion id='tr'>{body}{region}"
                 f"</TextRegion></Page></PcGts>").replace("'", '"'),
                encoding="utf-8")
        return out

    #: Three pages that do not read alike, so a correct match is unambiguous.
    PAGES = {
        "p1": ["Wir Johans von Habspurg tuon kunt allen den die disen brief",
               "ansehent oder hoerent lesen daz wir gegeben haben dem closter"],
        "p2": ["Item mer ze Bern von dem hus und hofstat gelegen an der matten",
               "daz wilunt was Heinrichs des Schmides seligen und sinen erben"],
        "p3": ["Hut genennt die brief die das Closter ze kuenge welt haben sol",
               "und die abschrift der brief sol man heschen nach ordnung als sy"],
    }

    def _readings(self, extra: dict) -> dict:
        good = {k: "\n".join(v) for k, v in self.PAGES.items()}
        return {"good": good, **extra}

    def test_a_collapsed_candidate_does_not_cost_the_other_pages(self, tmp_path):
        """The case that broke the first real run."""
        gt = self._gt(tmp_path, self.PAGES)
        readings = self._readings(
            {"collapser": {k: COLLAPSE for k in self.PAGES}})

        references, unlocated = vb.references_from_ground_truth([str(gt)], readings)

        assert set(references) == {"p1", "p2", "p3"}, (
            f"a collapsed candidate cost the run {3 - len(references)} page(s); "
            "the collapse is the finding, not a reason to lose the page"
        )
        assert not unlocated

    def test_a_candidate_with_one_page_does_not_vote(self, tmp_path):
        """Its only page matches every ground-truth file — that is not evidence."""
        gt = self._gt(tmp_path, self.PAGES)
        readings = self._readings(
            {"spotty": {"p1": "\n".join(self.PAGES["p1"])}})

        references, _ = vb.references_from_ground_truth([str(gt)], readings)

        assert set(references) == {"p1", "p2", "p3"}

    def test_the_excluded_candidate_is_still_scored(self, tmp_path):
        """Excluded from locating, not from measurement. That is the separation."""
        gt = self._gt(tmp_path, self.PAGES)
        readings = self._readings(
            {"collapser": {k: COLLAPSE for k in self.PAGES}})

        references, _ = vb.references_from_ground_truth([str(gt)], readings)
        meta = {m: {"level": "page", "attempted": 3, "answered": len(p)}
                for m, p in readings.items()}
        scores = {s.model_id: s for s in
                  vb.score_candidates(readings, meta, references)}

        assert scores["collapser"].scored == 3
        assert scores["collapser"].collapsed == 3
        assert scores["collapser"].median_cer > 1.0

    def test_a_page_no_usable_candidate_places_is_not_invented(self, tmp_path):
        """When everything collapsed there is no page, and that is the answer."""
        gt = self._gt(tmp_path, self.PAGES)
        readings = {"a": {k: COLLAPSE for k in self.PAGES},
                    "b": {k: COLLAPSE for k in self.PAGES}}

        references, unlocated = vb.references_from_ground_truth([str(gt)], readings)

        assert references == {}
        assert len(unlocated) == 3

    def test_disagreeing_usable_candidates_leave_the_page_unlocated(self, tmp_path):
        """Two good readings placing it differently means we do not know."""
        gt = self._gt(tmp_path, {"p1": self.PAGES["p1"], "p2": self.PAGES["p2"]})
        shifted = {"p1": "\n".join(self.PAGES["p2"]),
                   "p2": "\n".join(self.PAGES["p1"])}
        readings = self._readings({"shifted": shifted})

        references, unlocated = vb.references_from_ground_truth([str(gt)], readings)

        assert references == {}
        assert len(unlocated) == 2


class TestTheTableDoesNotPutAnUnrankableCandidateOnTop:
    """Also found by running it: a 1-page 0 % sorted above a 3-page 1 %.

    The ⚠ was there and the sort contradicted it. A reader takes the order of a
    table as the ranking whatever the footnote says, so the order has to carry
    the same claim the mark does.
    """

    def test_rankable_candidates_sort_above_unrankable_ones(self, tmp_path):
        many = {f"p{i}": NEAR for i in range(vb.RANKABLE_PAGES)}
        run_dir = _write_run(tmp_path, {
            "lucky-one-page": {"pages": {"p0": TRUTH}},
            "measured": {"pages": many},
        })
        readings, meta = vb.load_run(run_dir)
        refs = {k: TRUTH for k in many}
        scores = vb.score_candidates(readings, meta, refs)

        assert [s.model_id for s in scores] == ["measured", "lucky-one-page"], (
            "a candidate scored on one page sorted above one scored on eight, "
            "which reads as a ranking however it is marked"
        )

    def test_among_unrankable_candidates_more_pages_come_first(self, tmp_path):
        """Both order nothing, so the tie-break is evidence, not the number.

        A one-page 0 % above a three-page 1 % reads as a result when neither is.
        """
        run_dir = _write_run(tmp_path, {
            "one-page": {"pages": {"p0": TRUTH}},
            "three-pages": {"pages": {"p0": NEAR, "p1": NEAR, "p2": NEAR}},
        })
        readings, meta = vb.load_run(run_dir)
        scores = vb.score_candidates(
            readings, meta, {"p0": TRUTH, "p1": TRUTH, "p2": TRUTH})

        assert all(not s.rankable for s in scores)
        assert [s.model_id for s in scores] == ["three-pages", "one-page"]

    def test_within_a_group_the_order_is_still_by_median(self, tmp_path):
        many = {f"p{i}": NEAR for i in range(vb.RANKABLE_PAGES)}
        worse = {f"p{i}": FAR for i in range(vb.RANKABLE_PAGES)}
        run_dir = _write_run(tmp_path, {"better": {"pages": many},
                                        "worse": {"pages": worse}})
        readings, meta = vb.load_run(run_dir)
        scores = vb.score_candidates(readings, meta, {k: TRUTH for k in many})

        assert [s.model_id for s in scores] == ["better", "worse"]


class TestEndToEndOnASyntheticBench:
    """One pass through the whole offline half, on a corpus with a known shape."""

    def test_the_collapsing_candidate_is_distinguishable_from_the_weak_one(
            self, tmp_path):
        """The case the bench exists for: 27.7 % vs 189.8 % is not a close call.

        ``weak`` reads badly, ``collapser`` does not read. Both have a high
        median; only one has a collapse share, and that is the difference the
        report has to make visible.
        """
        pages = [f"p{i}" for i in range(10)]
        run_dir = _write_run(tmp_path, {
            "strong": {"pages": {k: NEAR for k in pages}},
            "weak": {"pages": {k: FAR for k in pages}},
            "collapser": {"pages": {k: COLLAPSE for k in pages}},
        })
        refs = {k: TRUTH for k in pages}
        readings, meta = vb.load_run(run_dir)
        scores = vb.score_candidates(readings, meta, refs)
        by_id = {s.model_id: s for s in scores}

        assert by_id["strong"].median_cer < by_id["weak"].median_cer
        assert by_id["weak"].median_cer < by_id["collapser"].median_cer
        assert by_id["weak"].collapsed == 0
        assert by_id["collapser"].collapsed == 10, (
            "the collapse is invisible in the table, so a reader has only the "
            "median — which is what hid it the first time"
        )

        report = vb.format_report(scores, vb.fusion_effect(readings, refs))
        assert "`strong`" in report and "`collapser`" in report
        assert "veto, not rank" in report
