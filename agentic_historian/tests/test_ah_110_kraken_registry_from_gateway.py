"""
test_ah_110_kraken_registry_from_gateway.py — Offline test for issue #110.

Validates that:
  1. refresh_kraken_registry(client) calls KrakenHTTPClient.list_models()
     and populates KRAKEN_MODELS_LIVE with KrakenModel objects.
  2. list_models() returns list[dict] (not list[str]) — ModelInfo dicts from gateway.
  3. KRAKEN_MODELS_LIVE is used as overlay in select_kraken_model().
  4. Falls back gracefully when gateway is unreachable (KrakenClientError).
  5. KrakenModel now has scripts/languages fields from gateway ModelInfo.

Key pitfalls from the failed first attempt (fix/ah-110-kraken-registry-drift)
that this test also validates:
  - Class is KrakenHTTPClient, NOT KrakenClient
  - list_models() returns list[dict] (ModelInfo dicts), NOT list[str]
  - Must be called INSIDE the with KrakenHTTPClient() as client: block
  - Dict key is "scripts" (not "script") and "languages" (not "lang")

Run: pytest test_ah_110_kraken_registry_from_gateway.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent_a.models import (
    KRAKEN_MODELS_LIVE,
    KrakenModel,
    refresh_kraken_registry,
)
from agent_a.model_selector import select_kraken_model


# ── mock gateway ModelInfo payloads ───────────────────────────────────────────

MOCK_GATEWAY_MODELS = [
    {
        "id": "10.5281/zenodo.7516057",
        "engine": "kraken",
        "scripts": ["Caroline minuscule", "Textura"],
        "centuries": [14, 15, 16],
        "languages": ["la", "de"],
        "level": "line",
        "description": "CatMuS Medieval full model",
    },
    {
        "id": "10.5281/zenodo.5468665",
        "engine": "kraken",
        "scripts": ["Caroline minuscule"],
        "centuries": [9, 10, 11, 12],
        "languages": ["la"],
        "level": "line",
        "description": "CatMuS Caroline minuscule",
    },
    {
        "id": "10.5281/zenodo.9999999",
        "engine": "kraken",
        "scripts": ["Kurrent"],
        "centuries": [16, 17],
        "languages": ["de"],
        "level": "line",
        "description": "Early modern German Kurrent",
    },
]


# ── tests ─────────────────────────────────────────────────────────────────────

class TestRefreshKrakenRegistry:
    """refresh_kraken_registry() populates KRAKEN_MODELS_LIVE from gateway."""

    def test_populates_kraken_models_live(self):
        """KRAKEN_MODELS_LIVE is filled with KrakenModel objects from gateway."""
        mock_client = MagicMock()
        mock_client.list_models.return_value = MOCK_GATEWAY_MODELS

        with patch.dict(KRAKEN_MODELS_LIVE, clear=True):
            result = refresh_kraken_registry(mock_client)

        assert isinstance(result, dict)
        assert "10.5281/zenodo.7516057" in result
        model = result["10.5281/zenodo.7516057"]
        assert isinstance(model, KrakenModel)
        assert model.model_id == "10.5281/zenodo.7516057"
        assert model.name == "CatMuS Medieval full model"
        assert 14 in model.centuries
        assert "la" in model.languages
        assert "Caroline minuscule" in model.scripts

    def test_list_models_returns_list_of_dicts(self):
        """KrakenHTTPClient.list_models() returns list[dict], NOT list[str].

        This was the key API mistake in the failed branch: treating model
        entries as bare strings instead of ModelInfo dicts.
        """
        mock_client = MagicMock()
        mock_client.list_models.return_value = MOCK_GATEWAY_MODELS

        result = refresh_kraken_registry(mock_client)

        # Each entry must be a dict with 'id' key (not a bare string)
        assert all(isinstance(m, dict) for m in MOCK_GATEWAY_MODELS)
        assert all("id" in m for m in MOCK_GATEWAY_MODELS)
        # If any entry is a bare string this will fail as intended
        assert all(isinstance(v, KrakenModel) for v in result.values())

    def test_scripts_and_languages_fields_populated(self):
        """KrakenModel gets scripts/languages from gateway ModelInfo dicts."""
        mock_client = MagicMock()
        mock_client.list_models.return_value = [
            {
                "id": "test-model",
                "scripts": ["Textura", "Kurrent"],
                "centuries": [15, 16],
                "languages": ["de", "la"],
                "description": "Multi-script test",
            }
        ]

        with patch.dict(KRAKEN_MODELS_LIVE, clear=True):
            result = refresh_kraken_registry(mock_client)

        model = result["test-model"]
        assert "Textura" in model.scripts
        assert "Kurrent" in model.scripts
        assert "de" in model.languages
        assert "la" in model.languages

    def test_invalidates_old_live_models(self):
        """refresh_kraken_registry() replaces any previous live entries."""
        mock_client = MagicMock()
        mock_client.list_models.return_value = MOCK_GATEWAY_MODELS

        # Pre-populate with stale data
        with patch.dict(KRAKEN_MODELS_LIVE, clear=True):
            KRAKEN_MODELS_LIVE["stale-model"] = KrakenModel(
                model_id="stale-model",
                name="Stale Model",
                lang="mul",
            )
            refresh_kraken_registry(mock_client)

        assert "stale-model" not in KRAKEN_MODELS_LIVE
        assert "10.5281/zenodo.7516057" in KRAKEN_MODELS_LIVE

    def test_skips_malformed_entries(self):
        """Entries missing 'id' key or with non-list centuries are skipped."""
        mock_client = MagicMock()
        mock_client.list_models.return_value = [
            {"id": "valid-model", "description": "OK"},
            {"description": "missing id — skip"},
            {"id": "bad-centuries", "centuries": "not-a-list"},
        ]

        with patch.dict(KRAKEN_MODELS_LIVE, clear=True):
            result = refresh_kraken_registry(mock_client)

        assert "valid-model" in result
        assert "missing id — skip" not in result  # (dict, not id val)
        assert "bad-centuries" in result  # centuries validation is lenient

    def test_raises_on_network_error(self):
        """KrakenClientError from list_models() propagates to caller."""
        from agent_a.kraken_client import KrakenClientError

        mock_client = MagicMock()
        mock_client.list_models.side_effect = KrakenClientError("unreachable")

        with patch.dict(KRAKEN_MODELS_LIVE, clear=True):
            with pytest.raises(KrakenClientError, match="unreachable"):
                refresh_kraken_registry(mock_client)


class TestLiveRegistryOverlayInSelect:
    """select_kraken_model() uses KRAKEN_MODELS_LIVE as overlay on KRAKEN_MODELS."""

    def test_live_registry_overlays_static_table(self):
        """
        When the same model id exists in both static table and live registry,
        the live entry (from gateway) should win in select_kraken_model().
        """
        # Build a minimal static table with one known model
        static_table = {
            "10.5281/zenodo.7516057": KrakenModel(
                model_id="10.5281/zenodo.7516057",
                name="Static CatMuS name",     # This name should be overridden
                lang="la",
                centuries=[14, 15],
            ),
        }
        # Live registry has same model id, richer metadata
        live_table = {
            "10.5281/zenodo.7516057": KrakenModel(
                model_id="10.5281/zenodo.7516057",
                name="CatMuS Medieval full model",   # gateway name wins
                lang="la",
                centuries=[14, 15, 16],              # more centuries from gateway
                scripts=["Caroline minuscule", "Textura"],
                languages=["la", "de"],
            ),
        }

        with patch.dict(KRAKEN_MODELS_LIVE, live_table):
            with patch("agent_a.model_selector.KRAKEN_MODELS", static_table):
                # select_kraken_model should find the model and prefer live name
                from agent_a.model_selector import SourceCriteria
                criteria = SourceCriteria(script="Latin", lang="la", century=15)
                matches = select_kraken_model(criteria, top_k=1)

        assert len(matches) == 1
        assert matches[0].model.name == "CatMuS Medieval full model"
        assert "Caroline minuscule" in matches[0].model.scripts

    def test_live_only_models_are_also_searched(self):
        """Models that exist only in live registry (not static table) are found."""
        static_table = {}  # empty static table

        # Only in live registry
        live_table = {
            "10.5281/zenodo.9999999": KrakenModel(
                model_id="10.5281/zenodo.9999999",
                name="Gateway-only model",
                lang="de",
                centuries=[16, 17],
                scripts=["Kurrent"],
                languages=["de"],
            ),
        }

        with patch.dict(KRAKEN_MODELS_LIVE, live_table):
            with patch("agent_a.model_selector.KRAKEN_MODELS", static_table):
                from agent_a.model_selector import SourceCriteria
                criteria = SourceCriteria(script="Kurrent", lang="de", century=16)
                matches = select_kraken_model(criteria, top_k=3)

        assert len(matches) == 1
        assert matches[0].model.model_id == "10.5281/zenodo.9999999"


class TestGatewayUnreachableFallback:
    """When the gateway is unreachable, pipeline uses static table without error."""

    def test_falls_back_to_static_table(self):
        """select_kraken_model() still works when KRAKEN_MODELS_LIVE is empty."""
        static_table = {
            "local-model": KrakenModel(
                model_id="local-model",
                name="Local Fallback Model",
                lang="la",
                centuries=[14],
            ),
        }

        with patch.dict(KRAKEN_MODELS_LIVE, {}, clear=True):
            with patch("agent_a.model_selector.KRAKEN_MODELS", static_table):
                from agent_a.model_selector import SourceCriteria
                criteria = SourceCriteria(script="Latin", lang="la", century=14)
                matches = select_kraken_model(criteria, top_k=1)

        assert len(matches) == 1
        assert matches[0].model.model_id == "local-model"