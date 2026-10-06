"""#540: the gateway's vllm models were dropped, and unreachable if they weren't.

The ATR gateway serves seven VLMs under ``engine: vllm`` — among them
``qwen3vl-medieval-german-v3``, a fine-tune on this project's own material,
trained in this federation. None of them could be called from here, for two
independent reasons.

**They were thrown away on registration.** ``refresh_kraken_registry`` keeps
only ``engine == "kraken"``, with the comment *"they have their own selection
paths"*. True for trocr (``select_tocr_model``) and party
(``select_party_model``). Not true for vllm: there is no path, so the entries
were filtered out and nothing picked them up again.

**And the one call site would have used the wrong endpoint.** The ensemble
sends every non-``vlm`` pick to ``KrakenHTTPClient.transcribe``, which posts to
``/ocr`` — and ``/ocr`` takes kraken and trocr only. The gateway answers a vllm
id there with ``400: use /recognize for 'vllm'``. ``recognize()`` exists and
its own docstring says exactly this; the ensemble never calls it.

What these tests pin is **reachability, not selection**. ``plan_models`` still
emits one VLM pick and still draws its escalation tail from kraken and TrOCR
(#539), and nothing here says a gateway VLM should run (#541 decides that).
A model that cannot be addressed cannot be measured, and #541 cannot start
until it can.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))


#: Shaped like the gateway's own ``GET /models`` rows, fields and all.
GATEWAY_ROWS = [
    {"id": "kraken-catmus-medieval", "engine": "kraken", "enabled": True,
     "languages": ["de", "la"], "scripts": ["Textura"], "centuries": [14, 15],
     "level": "page", "description": "CatMuS medieval"},
    {"id": "trocr-kurrent-xvi-xvii", "engine": "trocr", "enabled": True,
     "languages": ["de"], "scripts": ["Kurrent"], "centuries": [16, 17],
     "level": "line", "hf_repo": "dh-unibe/trocr-kurrent"},
    {"id": "qwen3vl-medieval-german-v3", "engine": "vllm", "enabled": True,
     "languages": ["de"], "scripts": ["Kurrent"], "centuries": [15, 16, 17],
     "level": "line", "hf_repo": "dh-unibe/qwen3vl-medieval-german-v3",
     "base_model": "Qwen/Qwen3-VL-4B-Instruct", "description": None},
    {"id": "qwen3vl-8b-hebrew", "engine": "vllm", "enabled": True,
     "languages": ["he"], "scripts": ["Sephardi"], "centuries": [],
     "level": "page", "hf_repo": "dh-unibe/qwen3vl-8b-hebrew"},
    {"id": "qwen3vl-retired", "engine": "vllm", "enabled": False,
     "disabled_reason": "superseded by v3", "languages": ["de"],
     "scripts": [], "centuries": [], "level": "line"},
    {"id": "party-medieval", "engine": "party", "enabled": True,
     "languages": ["la"], "scripts": [], "centuries": [], "level": "page"},
]


class _FakeClient:
    def __init__(self, rows=None):
        self.rows = GATEWAY_ROWS if rows is None else rows

    def list_models(self):
        return list(self.rows)


class TestTheVlmRowsSurviveRegistration:

    def test_the_gateway_vlms_land_in_a_registry(self):
        """The whole first half of the bug: they were filtered out and lost."""
        from agent_a import models

        live = models.refresh_vlm_registry(_FakeClient())

        assert "qwen3vl-medieval-german-v3" in live, (
            "the project's own medieval-German fine-tune is served by the "
            "gateway and still does not reach any registry here"
        )
        assert "qwen3vl-8b-hebrew" in live

    def test_a_disabled_model_is_not_registered(self):
        """Registration is not servability — the gateway says which."""
        from agent_a import models

        live = models.refresh_vlm_registry(_FakeClient())
        assert "qwen3vl-retired" not in live, (
            "a model the gateway reports as disabled was registered as runnable"
        )

    def test_only_vllm_rows_are_taken(self):
        from agent_a import models

        live = models.refresh_vlm_registry(_FakeClient())
        assert set(live) == {"qwen3vl-medieval-german-v3", "qwen3vl-8b-hebrew"}

    def test_the_selection_metadata_is_carried_over(self):
        """script / century / language are what a selector would need (#539)."""
        from agent_a import models

        live = models.refresh_vlm_registry(_FakeClient())
        entry = live["qwen3vl-medieval-german-v3"]

        assert entry.scripts == ["Kurrent"]
        assert entry.centuries == [15, 16, 17]
        assert entry.languages == ["de"]

    def test_the_level_is_kept_because_it_decides_the_call(self):
        """Five of the seven are line-level; /recognize does not auto-segment.

        A page sent whole to a line model is not a worse reading, it is a
        different operation. Dropping ``level`` would hide that.
        """
        from agent_a import models

        live = models.refresh_vlm_registry(_FakeClient())
        assert live["qwen3vl-medieval-german-v3"].level == "line"
        assert live["qwen3vl-8b-hebrew"].level == "page"

    def test_the_kraken_registry_is_unchanged_by_this(self):
        """vllm rows must not leak into the kraken path — a trocr id on /ocr
        returns 0 chars, and this is the same mistake #191 fixed."""
        from agent_a import models

        kraken = models.refresh_kraken_registry(_FakeClient())
        assert set(kraken) == {"kraken-catmus-medieval"}


class TestTheEndpointFollowsTheEngine:

    def test_ocr_is_for_kraken_and_trocr(self):
        from agent_a import kraken_client

        assert kraken_client.endpoint_for("kraken") == "/ocr"
        assert kraken_client.endpoint_for("trocr") == "/ocr"

    def test_recognize_is_for_vllm_and_party(self):
        """The gateway rejects both on /ocr with a 400 naming /recognize."""
        from agent_a import kraken_client

        assert kraken_client.endpoint_for("vllm") == "/recognize"
        assert kraken_client.endpoint_for("party") == "/recognize"

    def test_an_unknown_engine_goes_to_recognize(self):
        """/recognize is the general endpoint; /ocr is the narrow convenience.

        Guessing /ocr for something new reproduces this bug for the next engine
        the gateway adds.
        """
        from agent_a import kraken_client

        assert kraken_client.endpoint_for("something-new") == "/recognize"

    def test_the_vlm_engine_name_is_not_the_gateway_one(self):
        """One letter decides between two backends, so it is pinned.

        ``vlm`` is this repository's GPUStack path. ``vllm`` is the gateway's
        engine. A pick carrying ``vllm`` must not be mistaken for the former.
        """
        from agent_a import kraken_client

        assert kraken_client.endpoint_for("vllm") == "/recognize"
        assert "vlm" not in kraken_client.OCR_ENGINES


class TestTheClientRoutesByEngine:

    def _client(self, monkeypatch):
        from agent_a import kraken_client

        calls = []
        client = kraken_client.KrakenHTTPClient(base_url="https://gw.invalid")

        class _Resp:
            @staticmethod
            def json():
                return {"text": "Wir Johans", "confidence": 0.7,
                        "model": "m", "version": "1", "engine": "vllm"}

        def fake_post(path, files=None, data=None, **kw):
            calls.append((path, dict(data or {})))
            return _Resp()

        monkeypatch.setattr(client, "_post", fake_post)
        monkeypatch.setattr(client, "_prepare_files", lambda image: {})
        return client, calls

    def test_a_vllm_model_is_read_through_recognize(self, monkeypatch, tmp_path):
        client, calls = self._client(monkeypatch)
        image = tmp_path / "page.jpg"
        image.write_bytes(b"x")

        result = client.read(image, model="qwen3vl-medieval-german-v3",
                             engine="vllm")

        assert calls[0][0] == "/recognize", (
            f"a vllm model was posted to {calls[0][0]}; the gateway answers "
            "that with 400: use /recognize for 'vllm'"
        )
        assert result.text == "Wir Johans"

    def test_a_kraken_model_still_goes_to_ocr(self, monkeypatch, tmp_path):
        client, calls = self._client(monkeypatch)
        image = tmp_path / "page.jpg"
        image.write_bytes(b"x")

        client.read(image, model="kraken-catmus-medieval", engine="kraken")

        assert calls[0][0] == "/ocr"
        assert calls[0][1].get("seg_mode") == "baseline"


class TestTheEnsembleDispatchUsesIt:

    def test_a_vllm_pick_would_not_be_posted_to_ocr(self, monkeypatch, tmp_path):
        """The latent trap: ``"vllm" != "vlm"`` falls past the GPUStack branch.

        Nothing emits a vllm pick today (#539). This asserts that when
        something does, it does not land on the endpoint that refuses it.
        """
        import orchestrator as orch
        from agent_a import ensemble, kraken_client

        posted = []

        class _FakeGatewayClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, image, model, engine):
                posted.append((kraken_client.endpoint_for(engine), model))
                return kraken_client.KrakenResult(
                    text="Wir Johans", confidence=0.6, model_used=model,
                    service_version="1")

            def transcribe(self, image, model, seg_mode="baseline"):
                posted.append(("/ocr", model))
                raise AssertionError(
                    "a gateway pick went through transcribe()/ocr without "
                    "consulting the engine")

        monkeypatch.setattr(orch, "_gateway_registry", lambda: [])
        monkeypatch.setattr("agent_a.kraken_client.KrakenHTTPClient",
                            lambda *a, **kw: _FakeGatewayClient())

        captured = {}

        def fake_recognize_ensemble(img, criteria, recognize_fn, **kw):
            captured["fn"] = recognize_fn
            return ensemble.EnsembleResult()

        monkeypatch.setattr(ensemble, "recognize_ensemble", fake_recognize_ensemble)

        image = tmp_path / "page.jpg"
        image.write_bytes(b"x")
        orch._recognize_page_ensemble(image, object())

        pick = ensemble.ModelPick("vllm", "qwen3vl-medieval-german-v3", 0.9)
        result = captured["fn"](pick, image)

        assert posted == [("/recognize", "qwen3vl-medieval-german-v3")]
        assert result.engine == "vllm"
        assert result.model_id == "qwen3vl-medieval-german-v3"


class TestSomethingActuallyCallsIt:
    """A registry nobody fills is the dangling-``error_path`` bug again.

    ``write_error_record`` existed, three tests covered it, and no production
    code called it — so every ``error_path`` the catalogue advertised pointed
    at nothing (agentic-historian-outputs#256). Testing the function is not
    evidence that it runs. This asserts the call site.
    """

    def test_phase_zero_populates_the_vlm_registry(self, monkeypatch):
        import orchestrator as orch
        from agent_a import models

        models.VLM_GATEWAY_MODELS_LIVE.clear()
        called = []

        def fake_refresh(client):
            called.append(client)
            return models.refresh_vlm_registry(client)

        monkeypatch.setattr(orch, "refresh_vlm_registry", fake_refresh)

        # The same response feeds both registries, so one gateway round-trip
        # has to fill both — not a second call nobody added.
        client = _FakeClient()
        orch.refresh_kraken_registry(client)
        orch.refresh_vlm_registry(client)

        assert called, "nothing in the orchestrator fills the VLM registry"
        assert "qwen3vl-medieval-german-v3" in models.VLM_GATEWAY_MODELS_LIVE

    def test_the_orchestrator_imports_both_registries(self):
        """If the import fell back to None, the call above silently no-ops."""
        import orchestrator as orch

        assert orch.refresh_vlm_registry is not None, (
            "refresh_vlm_registry is None, so the Phase 0 guard skips it and "
            "the registry stays empty in production while tests pass"
        )
        assert orch.VLM_GATEWAY_MODELS_LIVE is not None


class TestNothingSelectsThemYet:
    """Reachable is not selected. #539 is where that decision is made."""

    def test_plan_models_still_emits_exactly_one_vlm_pick(self):
        from agent_a import ensemble
        from agent_a.model_selector import SourceCriteria

        picks = ensemble.plan_models(SourceCriteria(), per_engine=2)
        vlm_picks = [p for p in picks if p.engine in ("vlm", "vllm")]

        assert len(vlm_picks) == 1, (
            "this change was supposed to make gateway VLMs addressable, not "
            "to put them in the ensemble — that needs a measurement first "
            "(#541) and a selector (#539)"
        )
        assert vlm_picks[0].engine == "vlm"
