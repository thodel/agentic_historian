"""
test_ah_361_training_client.py — Offline tests for issue #361: TrainingClient.

Validates:
  1. TrainingClient context manager lifecycle mirrors KrakenHTTPClient.
  2. create_job() sends the correct JSON body and parses the response into TrainJob.
  3. get_job() fetches a single job by id.
  4. list_jobs() returns a list of TrainJob, with optional status filter.
  5. cancel_job() posts to /jobs/{id}/cancel and returns the updated TrainJob.
  6. get_log() fetches log text, with stage filter.
  7. TrainingClientError is raised on HTTP errors and network failures.
  8. TrainJob.from_dict() handles missing fields, unknown statuses, and ISO dates.
  9. JobStatus.is_terminal() and is_active() are correct.
 10. Convenience helpers (create_training_job, etc.) work.
 11. X-API-Key header is sent when api_key is configured.
 12. Base URL / timeout / api_key are configurable per-instance.

Run: pytest agentic_historian/tests/test_ah_361_training_client.py -v
"""

from __future__ import annotations

import json as _json
import httpx
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from agent_a.training_client import (
    JobStatus,
    Stage,
    TrainJob,
    TrainingClient,
    TrainingClientError,
    create_training_job,
    get_training_job,
    list_training_jobs,
)


# ── fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_httpx():
    """Patch httpx.Client so we can inject fake responses."""
    with patch("agent_a.training_client.httpx.Client") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_cls.return_value.__exit__  = MagicMock(return_value=None)
        yield mock_client


def _make_response(d: dict, status_code: int = 200) -> MagicMock:
    """Build a fake httpx.Response for the TrainingClient tests."""
    mock_resp = MagicMock(spec=httpx.Response)
    type(mock_resp).status_code = PropertyMock(return_value=status_code)
    mock_resp.json.return_value = d
    return mock_resp


# ── TrainJob.from_dict ────────────────────────────────────────────────────────

class TestTrainJobFromDict:
    """Parse a gateway JSON payload into a TrainJob."""

    def test_full_job_record(self):
        d = {
            "id":            "job-42",
            "status":        "training",
            "preset":        "catmus_medieval",
            "model_id":      "10.5281/zenodo.9999999",
            "created_at":    "2026-08-01T10:00:00Z",
            "started_at":    "2026-08-01T10:01:30Z",
            "queued_reason": "GPU 1 has 200 MB free",
            "cer":           0.043,
            "wer":           0.12,
        }
        job = TrainJob.from_dict(d)

        assert job.id == "job-42"
        assert job.status == JobStatus.TRAINING
        assert job.preset == "catmus_medieval"
        assert job.model_id == "10.5281/zenodo.9999999"
        assert job.created_at == datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)
        assert job.started_at == datetime(2026, 8, 1, 10, 1, 30, tzinfo=timezone.utc)
        assert job.queued_reason == "GPU 1 has 200 MB free"
        assert job.cer == pytest.approx(0.043)
        assert job.wer == pytest.approx(0.12)

    def test_minimal_job_record(self):
        """A reattach record from the watcher may be missing optional fields."""
        d = {"id": "job-99", "status": "queued", "preset": "fast", "model_id": "local/x"}
        job = TrainJob.from_dict(d)

        assert job.id == "job-99"
        assert job.status == JobStatus.QUEUED
        assert job.preset == "fast"
        assert job.model_id == "local/x"
        assert job.created_at is None
        assert job.started_at is None
        assert job.queued_reason is None
        assert job.cer is None

    def test_unknown_status_becomes_queued(self):
        """The trainer may add new statuses; we treat unknown as QUEUED."""
        d = {"id": "j1", "status": "super_new_status", "preset": "x", "model_id": "y"}
        job = TrainJob.from_dict(d)
        assert job.status == JobStatus.QUEUED

    def test_stages_parsed_correctly(self):
        d = {
            "id":       "j1",
            "status":   "training",
            "preset":   "x",
            "model_id": "y",
            "stages":   {
                "prepare": "downloading dataset...",
                "train":   "epoch 1/10 loss=0.5",
            },
        }
        job = TrainJob.from_dict(d)
        assert job.stages[Stage.PREPARE] == "downloading dataset..."
        assert job.stages[Stage.TRAIN] == "epoch 1/10 loss=0.5"
        assert Stage.COMPILE not in job.stages

    def test_invalid_stage_in_stages_dict_skipped(self):
        d = {
            "id":       "j1",
            "status":   "done",
            "preset":   "x",
            "model_id": "y",
            "stages": {"prepare": "ok", "unknown_stage": "skip me"},
        }
        job = TrainJob.from_dict(d)
        assert Stage.PREPARE in job.stages
        assert "unknown_stage" not in job.stages

    def test_empty_stages_handled(self):
        d = {"id": "j1", "status": "done", "preset": "x", "model_id": "y", "stages": {}}
        job = TrainJob.from_dict(d)
        assert job.stages == {}

    def test_none_stages_handled(self):
        d = {"id": "j1", "status": "done", "preset": "x", "model_id": "y", "stages": None}
        job = TrainJob.from_dict(d)
        assert job.stages == {}

    def test_iso_date_with_timezone_offset(self):
        d = {
            "id": "j1", "status": "done", "preset": "x", "model_id": "y",
            "created_at": "2026-08-01T10:00:00+02:00",
        }
        job = TrainJob.from_dict(d)
        assert job.created_at is not None
        assert job.created_at.tzinfo is not None

    def test_cer_wer_are_none_for_incomplete_job(self):
        d = {"id": "j1", "status": "training", "preset": "x", "model_id": "y"}
        job = TrainJob.from_dict(d)
        assert job.cer is None
        assert job.wer is None

    def test_cer_wer_invalid_values_become_none(self):
        d = {
            "id": "j1", "status": "done", "preset": "x", "model_id": "y",
            "cer": "not a number", "wer": None,
        }
        job = TrainJob.from_dict(d)
        assert job.cer is None
        assert job.wer is None


class TestTrainJobHelpers:
    """is_terminal, is_active, stage_description."""

    @pytest.mark.parametrize("status,expected", [
        (JobStatus.DONE,        True),
        (JobStatus.FAILED,      True),
        (JobStatus.CANCELLED,   True),
        (JobStatus.QUEUED,      False),
        (JobStatus.TRAINING,    False),
        (JobStatus.PREPARING,   False),
        (JobStatus.COMPILING,   False),
        (JobStatus.TESTING,     False),
        (JobStatus.REGISTERING, False),
    ])
    def test_is_terminal(self, status, expected):
        job = TrainJob(id="x", status=status, preset="", model_id="")
        assert job.is_terminal() is expected

    @pytest.mark.parametrize("status,expected", [
        (JobStatus.DONE,      False),
        (JobStatus.FAILED,    False),
        (JobStatus.CANCELLED, False),
        (JobStatus.QUEUED,    True),
        (JobStatus.TRAINING,  True),
    ])
    def test_is_active(self, status, expected):
        job = TrainJob(id="x", status=status, preset="", model_id="")
        assert job.is_active() is expected

    def test_stage_description_for_failed_includes_error(self):
        job = TrainJob(id="x", status=JobStatus.FAILED, preset="", model_id="",
                       error="OOM during compile")
        assert "OOM" in job.stage_description()
        assert "failed" in job.stage_description().lower()

    def test_stage_description_for_queued(self):
        job = TrainJob(id="x", status=JobStatus.QUEUED, preset="", model_id="")
        assert job.stage_description() == "queued"


# ── TrainingClient.__init__ ───────────────────────────────────────────────────

class TestTrainingClientInit:
    """Config defaults and per-instance overrides."""

    def test_defaults_from_config(self, mock_httpx):
        with patch("agent_a.training_client.config") as cfg:
            cfg.ATR_GATEWAY_URL  = "https://asteraiX.example.com"
            cfg.ATR_API_KEY      = "***"
            cfg.ATR_HTTP_TIMEOUT = 180.0

            with TrainingClient() as client:
                assert client.base_url == "https://asteraiX.example.com"
                assert client.timeout  == 180.0
                assert client.api_key  == "***"

    def test_per_instance_override(self, mock_httpx):
        with TrainingClient(base_url="http://localhost:8200",
                            timeout=30.0,
                            api_key="***") as client:
            assert client.base_url == "http://localhost:8200"
            assert client.timeout  == 30.0
            assert client.api_key  == "***"

    def test_api_key_empty_string_no_header_sent(self, mock_httpx):
        """Empty api_key must not send an X-API-Key header to httpx.Client."""
        with patch("agent_a.training_client.httpx.Client") as mock_cls:
            mock_inner = MagicMock()
            mock_cls.return_value.__enter__ = MagicMock(return_value=mock_inner)
            mock_cls.return_value.__exit__  = MagicMock(return_value=None)

            with patch("agent_a.training_client.config") as cfg:
                cfg.ATR_GATEWAY_URL  = "http://localhost"
                cfg.ATR_API_KEY      = ""
                cfg.ATR_HTTP_TIMEOUT = 300.0

                with TrainingClient() as client:
                    pass

            # When api_key is empty, headers dict should not include X-API-Key
            call_kwargs = mock_cls.call_args[1]
            headers = call_kwargs.get("headers", {})
            assert "X-API-Key" not in headers

    def test_base_url_trailing_slash_stripped(self, mock_httpx):
        with TrainingClient(base_url="http://localhost:8200/") as client:
            assert client.base_url == "http://localhost:8200"


# ── TrainingClient.create_job ─────────────────────────────────────────────────

class TestCreateJob:
    """POST /train/jobs."""

    def test_sends_preset_and_model_id(self, mock_httpx):
        mock_httpx.post.return_value = _make_response({
            "id": "job-new", "status": "queued",
            "preset": "catmus_medieval", "model_id": "10.5281/zenodo.1",
        })
        with TrainingClient(base_url="http://localhost:8200") as client:
            client._client = mock_httpx
            job = client.create_job(preset="catmus_medieval",
                                    model_id="10.5281/zenodo.1")

        mock_httpx.post.assert_called_once()
        call_args = mock_httpx.post.call_args
        assert call_args[0][0] == "/train/jobs"
        assert call_args[1]["json"] == {
            "preset": "catmus_medieval",
            "model_id": "10.5281/zenodo.1",
        }
        assert job.id == "job-new"
        assert job.status == JobStatus.QUEUED

    def test_extra_fields_merged_into_body(self, mock_httpx):
        mock_httpx.post.return_value = _make_response({
            "id": "j1", "status": "queued", "preset": "x", "model_id": "y",
        })
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            client.create_job(preset="x", model_id="y", extra={"epochs": 50})

        body = mock_httpx.post.call_args[1]["json"]
        assert body["epochs"] == 50
        assert body["preset"] == "x"

    def test_raises_on_http_error(self, mock_httpx):
        mock_httpx.post.return_value = _make_response({}, status_code=507)
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            with pytest.raises(TrainingClientError, match="507"):
                client.create_job("preset", "model-id")

    def test_raises_on_network_error(self, mock_httpx):
        # _check will read status_code first (returns 200 from PropertyMock),
        # then _post calls json() which raises RequestError
        mock_httpx.post.side_effect = httpx.RequestError("connection refused")
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            with pytest.raises(TrainingClientError, match="error"):
                client.create_job("preset", "model-id")


# ── TrainingClient.get_job ────────────────────────────────────────────────────

class TestGetJob:
    """GET /train/jobs/{job_id}."""

    def test_calls_correct_path(self, mock_httpx):
        mock_httpx.get.return_value = _make_response({
            "id": "job-abc", "status": "training",
            "preset": "x", "model_id": "y",
        })
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            job = client.get_job("job-abc")

        mock_httpx.get.assert_called_once_with("/train/jobs/job-abc", params=None)
        assert job.id == "job-abc"
        assert job.status == JobStatus.TRAINING

    def test_raises_on_404(self, mock_httpx):
        mock_httpx.get.return_value = _make_response({}, status_code=404)
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            with pytest.raises(TrainingClientError, match="404"):
                client.get_job("nonexistent")


# ── TrainingClient.list_jobs ──────────────────────────────────────────────────

class TestListJobs:
    """GET /train/jobs."""

    def test_returns_list_of_train_job(self, mock_httpx):
        mock_httpx.get.return_value = _make_response({
            "jobs": [
                {"id": "j1", "status": "done",     "preset": "a", "model_id": "x"},
                {"id": "j2", "status": "training", "preset": "b", "model_id": "y"},
            ]
        })
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            jobs = client.list_jobs()

        assert len(jobs) == 2
        assert jobs[0].id == "j1"
        assert jobs[1].id == "j2"
        assert jobs[1].status == JobStatus.TRAINING

    def test_limit_capped_at_100(self, mock_httpx):
        mock_httpx.get.return_value = _make_response({"jobs": []})
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            client.list_jobs(limit=500)

        params = mock_httpx.get.call_args[1]["params"]
        assert params["limit"] == 100

    def test_status_filter_passed_to_params(self, mock_httpx):
        mock_httpx.get.return_value = _make_response({"jobs": []})
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            client.list_jobs(status=JobStatus.TRAINING)

        params = mock_httpx.get.call_args[1]["params"]
        assert params["status"] == "training"

    def test_non_list_response_returns_empty_list(self, mock_httpx):
        """Malformed gateway response: 'jobs' key missing or not a list."""
        mock_httpx.get.return_value = _make_response({"jobs": "not a list"})
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            jobs = client.list_jobs()

        assert jobs == []

    def test_raises_on_http_error(self, mock_httpx):
        mock_httpx.get.return_value = _make_response({}, status_code=500)
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            with pytest.raises(TrainingClientError, match="500"):
                client.list_jobs()


# ── TrainingClient.cancel_job ─────────────────────────────────────────────────

class TestCancelJob:
    """POST /train/jobs/{job_id}/cancel."""

    def test_sends_empty_json(self, mock_httpx):
        mock_httpx.post.return_value = _make_response({
            "id": "job-42", "status": "cancelled",
            "preset": "x", "model_id": "y",
        })
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            job = client.cancel_job("job-42")

        call_args = mock_httpx.post.call_args
        assert call_args[0][0] == "/train/jobs/job-42/cancel"
        assert call_args[1]["json"] == {}
        assert job.status == JobStatus.CANCELLED

    def test_raises_on_404(self, mock_httpx):
        mock_httpx.post.return_value = _make_response({}, status_code=404)
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            with pytest.raises(TrainingClientError, match="404"):
                client.cancel_job("nonexistent")


# ── TrainingClient.get_log ────────────────────────────────────────────────────

class TestGetLog:
    """GET /train/jobs/{job_id}/log."""

    def test_returns_log_text(self, mock_httpx):
        mock_httpx.get.return_value = _make_response({
            "log": "epoch 1 loss=0.5\nepoch 2 loss=0.3\n"
        })
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            log = client.get_log("job-1")

        assert "epoch 1" in log
        assert "epoch 2" in log

    def test_stage_param_added_when_specified(self, mock_httpx):
        mock_httpx.get.return_value = _make_response({"log": "compiling..."})
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            client.get_log("job-1", stage=Stage.COMPILE)

        params = mock_httpx.get.call_args[1]["params"]
        assert params["stage"] == "compile"

    def test_no_stage_param_when_none(self, mock_httpx):
        mock_httpx.get.return_value = _make_response({"log": ""})
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            client.get_log("job-1")

        params = mock_httpx.get.call_args[1]["params"]
        assert "stage" not in params

    def test_empty_log_returns_empty_string(self, mock_httpx):
        mock_httpx.get.return_value = _make_response({"log": ""})
        with TrainingClient(base_url="http://localhost") as client:
            client._client = mock_httpx
            log = client.get_log("job-1", stage=Stage.TRAIN)

        assert log == ""


# ── X-API-Key header ─────────────────────────────────────────────────────────

class TestAuthHeader:
    """X-API-Key is sent when api_key is set."""

    def test_header_sent_when_api_key_set(self, mock_httpx):
        """TrainingClient.__enter__ passes X-API-Key to the httpx.Client ctor."""
        mock_httpx.get.return_value = _make_response({"jobs": []})
        with TrainingClient(base_url="http://localhost", api_key="***") as client:
            # verify X-API-Key was captured from api_key param
            assert client.api_key == "***"
            # trigger a call so mock_httpx.get() is invoked
            client.list_jobs()

        # Verify get was called
        mock_httpx.get.assert_called_once()
        # Verify the X-API-Key header was passed when constructing httpx.Client
        construct_kwargs = mock_httpx.call_args[1]
        assert construct_kwargs.get("headers", {}).get("X-API-Key") == "my-secret"

    def test_no_header_when_api_key_empty(self, mock_httpx):
        """Empty string api_key → no X-API-Key in headers dict."""
        mock_httpx.get.return_value = _make_response({"jobs": []})
        with TrainingClient(base_url="http://localhost", api_key="") as client:
            client.list_jobs()

        construct_kwargs = mock_httpx.call_args[1]
        headers = construct_kwargs.get("headers", {})
        assert "X-API-Key" not in headers


# ── Convenience helpers ───────────────────────────────────────────────────────

class TestConvenienceHelpers:
    """create_training_job, get_training_job, list_training_jobs."""

    def test_create_training_job(self):
        with patch("agent_a.training_client.config") as cfg, \
             patch("agent_a.training_client.httpx.Client") as mock_cls:
            cfg.ATR_GATEWAY_URL  = "http://localhost"
            cfg.ATR_API_KEY      = ""
            cfg.ATR_HTTP_TIMEOUT = 300.0

            mock_client = MagicMock()
            mock_response = _make_response({
                "id": "j1", "status": "queued", "preset": "x", "model_id": "y"
            })
            mock_client.post.return_value = mock_response
            mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_cls.return_value.__exit__  = MagicMock(return_value=None)

            job = create_training_job("preset-x", "model-y")

        assert job.id == "j1"
        mock_client.post.assert_called_once_with("/train/jobs", json={
            "preset": "preset-x", "model_id": "model-y"
        })

    def test_get_training_job(self):
        with patch("agent_a.training_client.config") as cfg, \
             patch("agent_a.training_client.httpx.Client") as mock_cls:
            cfg.ATR_GATEWAY_URL  = "http://localhost"
            cfg.ATR_API_KEY      = ""
            cfg.ATR_HTTP_TIMEOUT = 300.0

            mock_client = MagicMock()
            mock_response = _make_response({
                "id": "j5", "status": "training", "preset": "x", "model_id": "y"
            })
            mock_client.get.return_value = mock_response
            mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_cls.return_value.__exit__  = MagicMock(return_value=None)

            job = get_training_job("j5")

        assert job.id == "j5"
        mock_client.get.assert_called_once()

    def test_list_training_jobs(self):
        with patch("agent_a.training_client.config") as cfg, \
             patch("agent_a.training_client.httpx.Client") as mock_cls:
            cfg.ATR_GATEWAY_URL  = "http://localhost"
            cfg.ATR_API_KEY      = ""
            cfg.ATR_HTTP_TIMEOUT = 300.0

            mock_client = MagicMock()
            mock_response = _make_response({"jobs": []})
            mock_client.get.return_value = mock_response
            mock_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_cls.return_value.__exit__  = MagicMock(return_value=None)

            jobs = list_training_jobs(limit=5, status=JobStatus.DONE)

        assert jobs == []
        call_kwargs = mock_client.get.call_args[1]
        assert call_kwargs["params"]["limit"] == 5
        assert call_kwargs["params"]["status"] == "done"


# ── httpx importable in module ────────────────────────────────────────────────

def test_httpx_importable():
    """The module imports httpx without error (verifies no syntax/import error)."""
    import httpx as _httpx
    assert hasattr(_httpx, "Client")
    assert hasattr(_httpx, "RequestError")
