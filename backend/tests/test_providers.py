import io
import json
from types import SimpleNamespace

import httpx
import pytest
from app import providers
from app.config import Settings
from app.contracts import RunConfig
from app.providers import FixtureProvider, GeminiProvider, ProviderError, fixture_query
from google import genai
from google.genai import types


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
    assert seen["client"]["http_options"].timeout == 1_200_000
    assert seen["request"]["config"].response_json_schema
    assert seen["request"]["config"].max_output_tokens == 16384
    assert seen["request"]["config"].thinking_config.thinking_level.value == "LOW"
    assert seen["request"]["config"].temperature == 1
    assert seen["request"]["config"].automatic_function_calling.disable is True
    parts = seen["request"]["contents"][0].parts
    assert json.loads(parts[1].text) == {"frame_id": "f1", "local_time_s": 0.25}
    assert parts[2].inline_data.data == frame.read_bytes()
    assert "provided frame IDs" in seen["request"]["config"].system_instruction
    assert "evidence_times_s=[]" in seen["request"]["config"].system_instruction
    assert "test-secret" not in json.dumps(reply.raw)
    assert seen["closed"]
    assert reply.raw["request_metrics"]["frame_count"] == 1
    assert reply.raw["request_metrics"]["input_bytes"] == len(frame.read_bytes())
    assert reply.raw["request_metrics"]["elapsed_s"] >= 0
    assert reply.raw["request_metrics"]["input_mode"] == "images"
    assert reply.raw["request_metrics"]["video_duration_s"] is None


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
    assert error.details["timeout_s"] == 1200
    assert error.details["frame_count"] == 1 and error.details["input_bytes"] == 4
    assert "test-secret" not in json.dumps(error.details) + str(error)
    assert "private.invalid" not in json.dumps(error.details) + str(error)


def _reply():
    return SimpleNamespace(
        candidates=[SimpleNamespace(finish_reason="STOP")],
        text=json.dumps({"complete": True, "events": [], "candidate_dispositions": []}),
        usage_metadata=None, model_version="test-model", response_id="reply-1",
    )


def _video_request(tmp_path):
    path = tmp_path / "window.mp4"
    path.write_bytes(b"prepared mp4 bytes")
    return {
        "input_mode": "video", "sample_fps": 2,
        "input_origin_us": 5250000, "source_range_us": [5250000, 7250000],
        "raw_query": "find contacts",
        "frames": [{"frame_id": "DO_NOT_SEND_THIS_FRAME", "source_time_us": 5250000,
                    "local_time_s": 0, "path": "/missing/never-read.jpg"}],
        "video": {"path": str(path), "mime_type": "video/mp4", "duration_us": 2000000,
                  "size_bytes": path.stat().st_size, "actual_range_us": [5250000, 7250000]},
    }


@pytest.mark.parametrize("operation", ["propose", "verify_refine"])
def test_native_video_part_uses_bytes_mime_fps_and_clip_local_prompt(monkeypatch, tmp_path, operation):
    seen = {}

    class Client:
        def __init__(self, **kwargs):
            self.models = self
            seen["client"] = kwargs
        def generate_content(self, **kwargs):
            seen["request"] = kwargs
            return _reply()
        def close(self):
            pass

    monkeypatch.setattr(genai, "Client", Client)
    request = _video_request(tmp_path)
    provider = GeminiProvider(Settings(data_dir=tmp_path, gemini_api_key="test-secret"), RunConfig())
    reply = provider.analyze(operation, request)
    parts = seen["request"]["contents"][0].parts
    assert len(parts) == 2
    assert parts[0].inline_data.data == b"prepared mp4 bytes"
    assert parts[0].inline_data.mime_type == "video/mp4"
    assert parts[0].video_metadata.fps == 2
    assert parts[0].video_metadata.start_offset is None
    assert parts[0].video_metadata.end_offset is None
    assert not any(part.inline_data and part.inline_data.mime_type == "image/jpeg" for part in parts)
    assert "DO_NOT_SEND_THIS_FRAME" not in parts[1].text
    assert "window.mp4" not in parts[1].text
    prompt = seen["request"]["config"].system_instruction
    assert "evidence_frame_ids=[]" in prompt and "evidence_times_s" in prompt
    assert "Do NOT add input_origin_us" in prompt
    metrics = reply.raw["request_metrics"]
    assert metrics["input_mode"] == "video" and metrics["video_transport"] == "inline"
    assert metrics["video_duration_s"] == 2 and metrics["frame_count"] == 4
    assert metrics["input_bytes"] == len(b"prepared mp4 bytes")
    assert seen["client"]["http_options"].timeout == 1200000
    assert seen["client"]["http_options"].retry_options.attempts == 1
    assert "test-secret" not in json.dumps(reply.raw)


def test_query_normalization_remains_text_only_even_with_visual_fields(monkeypatch, tmp_path):
    seen = {}
    class Client:
        def __init__(self, **_):
            self.models = self
        def generate_content(self, **kwargs):
            seen.update(kwargs)
            return _reply()
        def close(self):
            pass
    monkeypatch.setattr(genai, "Client", Client)
    provider = GeminiProvider(Settings(data_dir=tmp_path, gemini_api_key="test-secret"), RunConfig())
    reply = provider.analyze("normalize_query", _video_request(tmp_path))
    assert len(seen["contents"][0].parts) == 1
    assert seen["contents"][0].parts[0].text
    assert reply.raw["request_metrics"]["input_mode"] == "text"
    assert reply.raw["request_metrics"]["input_bytes"] == 0


def test_large_video_uses_files_api_active_poll_and_explicit_no_retry(monkeypatch, tmp_path):
    seen = {"upload": [], "get": []}
    monkeypatch.setattr(providers, "INLINE_REQUEST_LIMIT_BYTES", 1)
    monkeypatch.setattr(providers, "FILE_POLL_INTERVAL_S", 0)
    class Client:
        def __init__(self, **kwargs):
            self.models = self.files = self
            seen["client"] = kwargs
        def upload(self, **kwargs):
            seen["upload"].append(kwargs)
            return SimpleNamespace(name="files/window123", state="PROCESSING")
        def get(self, **kwargs):
            seen["get"].append(kwargs)
            return SimpleNamespace(name="files/window123", state="ACTIVE",
                                   uri="https://generativelanguage.googleapis.com/v1beta/files/window123")
        def generate_content(self, **kwargs):
            seen["request"] = kwargs
            return _reply()
        def close(self):
            pass
    monkeypatch.setattr(genai, "Client", Client)
    provider = GeminiProvider(Settings(data_dir=tmp_path, gemini_api_key="test-secret"), RunConfig())
    reply = provider.analyze("propose", _video_request(tmp_path))
    assert len(seen["upload"]) == len(seen["get"]) == 1
    for operation in ("upload", "get"):
        assert seen[operation][0]["config"].http_options.retry_options.attempts == 1
    part = seen["request"]["contents"][0].parts[0]
    assert part.file_data.mime_type == "video/mp4" and part.video_metadata.fps == 2
    assert part.inline_data is None
    metrics = reply.raw["request_metrics"]
    assert metrics["upload"]["file_name"] == "files/window123"
    assert metrics["upload"]["poll_count"] == 1 and metrics["upload"]["state"] == "ACTIVE"
    assert metrics["upload"]["elapsed_s"] >= 0
    assert "https://" not in json.dumps(metrics)


@pytest.mark.parametrize("fail_phase", ["upload", "process"])
def test_file_transport_failure_is_unknown_safe_and_never_generates(monkeypatch, tmp_path, fail_phase):
    calls = []
    monkeypatch.setattr(providers, "INLINE_REQUEST_LIMIT_BYTES", 1)
    monkeypatch.setattr(providers, "FILE_POLL_INTERVAL_S", 0)
    class Client:
        def __init__(self, **_):
            self.models = self.files = self
        def upload(self, **_):
            calls.append("upload")
            if fail_phase == "upload":
                raise TimeoutError("https://private.invalid/upload?key=test-secret")
            return SimpleNamespace(name="files/window123", state="PROCESSING")
        def get(self, **_):
            calls.append("process")
            raise TimeoutError("https://private.invalid/get?key=test-secret")
        def generate_content(self, **_):
            pytest.fail("Generation must not run after upload/processing failure")
        def close(self):
            pass
    monkeypatch.setattr(genai, "Client", Client)
    provider = GeminiProvider(Settings(data_dir=tmp_path, gemini_api_key="test-secret"), RunConfig())
    with pytest.raises(ProviderError) as caught:
        provider.analyze("propose", _video_request(tmp_path))
    error = caught.value
    assert error.request_unknown
    assert error.details["phase"] == fail_phase
    assert error.details["generation_submitted"] is False
    assert calls.count(fail_phase) == 1
    safe_text = str(error) + json.dumps(error.details)
    assert "test-secret" not in safe_text and "private.invalid" not in safe_text


def test_active_poll_can_be_cancelled_before_generate(monkeypatch, tmp_path):
    cancelled = [False]
    monkeypatch.setattr(providers, "INLINE_REQUEST_LIMIT_BYTES", 1)
    class Client:
        def __init__(self, **_):
            self.models = self.files = self
        def upload(self, **_):
            cancelled[0] = True
            return SimpleNamespace(name="files/window123", state="PROCESSING")
        def get(self, **_):
            pytest.fail("Cancellation should stop ACTIVE polling")
        def generate_content(self, **_):
            pytest.fail("Cancellation should prevent generation")
        def close(self):
            pass
    monkeypatch.setattr(genai, "Client", Client)
    provider = GeminiProvider(Settings(data_dir=tmp_path, gemini_api_key="test-secret"), RunConfig())
    provider.bind_cancelled(lambda: cancelled[0])
    with pytest.raises(ProviderError) as caught:
        provider.analyze("propose", _video_request(tmp_path))
    assert caught.value.code == "cancelled"
    assert caught.value.details["generation_submitted"] is False


def test_sdk_chunk_upload_manual_retry_loop_is_stopped_by_response_hook():
    calls = []
    def transport(request):
        calls.append(request)
        return httpx.Response(503, json={"error": {"message": "private-url-and-secret"}})
    client_http = httpx.Client(transport=httpx.MockTransport(transport), event_hooks={
        "response": [providers._reject_unconfirmed_upload_response],
    })
    options = types.HttpOptions(httpx_client=client_http, timeout=1000,
                                retry_options=types.HttpRetryOptions(attempts=1))
    client = genai.Client(api_key="test-key", http_options=options)
    try:
        with pytest.raises(providers.UploadResponseUnknown) as caught:
            # Exercise the real SDK path containing its separate MAX_RETRY_COUNT loop.
            client._api_client.upload_file(io.BytesIO(b"abc"), "https://upload.invalid/test", 3,
                                           http_options=options)
        assert len(calls) == 1
        assert "private-url-and-secret" not in str(caught.value)
    finally:
        client.close()
        client_http.close()


def test_fixture_video_uses_timestamps_without_jpeg_evidence():
    media = {"is_demo": True, "fixture_events": [
        {"kind": "point", "anchor_us": 3000000, "entity_key": "demo_ball"},
    ]}
    reply = FixtureProvider(media, RunConfig(provider="fixture")).analyze("propose", {
        "input_mode": "video", "input_origin_us": 2500000,
        "source_range_us": [2500000, 3500000], "query_spec": {"event_kind": "point"},
        "frames": [{"frame_id": "local-index-only", "source_time_us": 3000000}],
    })
    assert reply.payload["events"][0]["evidence_frame_ids"] == []
    assert reply.payload["events"][0]["evidence_times_s"] == [0.5]
