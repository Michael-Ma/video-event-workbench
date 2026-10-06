import json
from types import SimpleNamespace

import pytest
from app.config import Settings
from app.contracts import RunConfig
from app.providers import FixtureProvider, GeminiProvider, ProviderError, fixture_query
from google import genai


def test_fixture_never_accepts_real_upload():
    with pytest.raises(ProviderError, match="only allowed"):
        FixtureProvider({"is_demo": False}, RunConfig(provider="fixture"))


def test_missing_key_is_configuration_failure():
    with pytest.raises(ProviderError) as caught:
        GeminiProvider(Settings(data_dir="unused"), RunConfig())
    assert caught.value.code == "missing_api_key"


def test_fixture_parser_discloses_limitations_and_preserves_query():
    raw = "找最后一次小球触地，包括很轻的一次"
    spec = fixture_query(raw, RunConfig(provider="fixture"))
    assert spec.raw_query == raw
    assert spec.event_kind == "point" and spec.occurrence_policy == "last"
    assert spec.required_evidence == []
    assert "not semantic inference" in spec.defaults_used[0]


def test_gemini_sdk_call_is_explicit_frames_schema_and_no_implicit_retry(monkeypatch, tmp_path):
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            seen["client"] = kwargs
            self.models = self

        def generate_content(self, **kwargs):
            seen["request"] = kwargs
            return SimpleNamespace(
                candidates=[SimpleNamespace(finish_reason="STOP")],
                text=json.dumps({"complete": True, "events": [], "candidate_dispositions": []}),
                usage_metadata=None, model_version="test-model", response_id="reply-1",
            )

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr(genai, "Client", Client)
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"local JPEG test payload")
    provider = GeminiProvider(Settings(data_dir=tmp_path, gemini_api_key="test-secret"), RunConfig())
    reply = provider.analyze("propose", {"raw_query": "find events", "frames": [
        {"frame_id": "f1", "local_time_s": 0.25, "path": str(frame)}]})
    assert reply.payload["events"] == []
    assert seen["client"]["http_options"].retry_options.attempts == 1
    assert seen["request"]["config"].response_json_schema
    parts = seen["request"]["contents"][0].parts
    assert json.loads(parts[1].text) == {"frame_id": "f1", "local_time_s": 0.25}
    assert parts[2].inline_data.data == frame.read_bytes()
    assert "test-secret" not in json.dumps(reply.raw)
    assert seen["closed"]


def test_transport_failure_is_unknown_and_not_retried(monkeypatch, tmp_path):
    calls = []

    class Client:
        def __init__(self, **_):
            self.models = self

        def generate_content(self, **_):
            calls.append(1)
            raise TimeoutError("request URL may contain test-secret")

        def close(self):
            pass

    monkeypatch.setattr(genai, "Client", Client)
    provider = GeminiProvider(Settings(data_dir=tmp_path, gemini_api_key="test-secret"), RunConfig())
    with pytest.raises(ProviderError) as caught:
        provider.analyze("normalize_query", {"raw_query": "find something"})
    assert caught.value.request_unknown is True
    assert len(calls) == 1


def test_timeout_diagnostics_are_precise_without_exposing_sdk_exception_text(monkeypatch, tmp_path):
    class Client:
        def __init__(self, **_):
            self.models = self
        def generate_content(self, **_):
            raise TimeoutError("https://private.invalid/?key=test-secret")
        def close(self):
            pass
    monkeypatch.setattr(genai, "Client", Client)
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg")
    provider = GeminiProvider(Settings(data_dir=tmp_path, gemini_api_key="test-secret"), RunConfig())
    with pytest.raises(ProviderError) as caught:
        provider.analyze("propose", {"frames": [{"frame_id": "frame1", "local_time_s": 0, "path": str(frame)}]})
    error = caught.value
    assert error.code == "request_timeout" and error.request_unknown
    assert error.details["exception_type"] == "TimeoutError"
    assert error.details["timeout_s"] == 120
    assert error.details["frame_count"] == 1 and error.details["input_bytes"] == 4
    assert "test-secret" not in json.dumps(error.details) + str(error)
    assert "private.invalid" not in json.dumps(error.details) + str(error)
