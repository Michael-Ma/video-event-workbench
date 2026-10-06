import copy
import re

import pytest
from app import main
from app.config import Settings
from app.diagnostics import run_diagnostics
from app.localization import language_from_header
from app.main import APIError, create_app
from app.media import MediaError
from fastapi.testclient import TestClient


@pytest.mark.parametrize("header,expected", [
    (None, "zh"), ("", "zh"), ("en-US,en;q=0.9", "en"), ("zh-CN", "zh"),
    ("zh-Hant-TW,en;q=0.5", "zh"), ("fr-FR, en-GB;q=0.8, zh;q=0.6", "en"),
    ("en;q=0.2,zh;q=0.9", "zh"), ("en;q=0.9,zh;q=0.9", "en"),
    ("en;q=0,zh;q=0.5", "zh"), ("de-DE,*;q=0.1", "zh"),
    ("en;q=nan,zh;q=0.5", "zh"), ("en;q=2,zh;q=0.5", "zh"),
    ("zh;q=broken, EN-us;Q=0.8", "en"),
])
def test_language_negotiation_uses_supported_quality_and_chinese_fallback(header, expected):
    assert language_from_header(header) == expected


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path, max_upload_mb=1)))


def register(client, *, is_demo=True):
    return client.app.state.repo.create_media({"filename": "原片.mp4", "is_demo": is_demo,
                                              "duration_us": 12_000_000, "status": "ready"})


def fixture_body(media, query="Find every event"):
    return {"media_id": media["id"], "query": query, "config": {"provider": "fixture"}}


def test_same_saved_run_has_localized_diagnostics_without_rewriting_any_evidence(client):
    repo = client.app.state.repo
    media = register(client)
    raw_query = "找到每次抓取；do not translate my query"
    raw_results = {
        "events": [{"event_id": "event_1", "reason": "模型原始判断：手臂可能接触杯子。",
                    "uncertainty_reasons": ["原始模型 reason", "unknown_contact"],
                    "location": {"kind": "point", "anchor_us": 5_123_456},
                    "clip_status": "failed"}],
        "errors": [{"task_id": "old_custom", "code": "custom_external_error",
                    "message": "历史 SDK 原文 (not translated)",
                    "details": {"original": "原始详情", "elapsed_s": 3.75}}],
        "coverage": {"duration_us": 12_000_000, "covered_us": 0, "gaps": [[0, 12_000_000]]},
    }
    run, _ = repo.create_run({"media_id": media["id"], "query": raw_query,
                              "config": {"request_timeout_s": 120}, "status": "partial",
                              "stage": "completed", "results": raw_results,
                              "error": {"code": "pipeline_failed", "message": "原始 pipeline error",
                                        "details": {"original": "保留错误详情"}}})
    repo.upsert_task(run["id"], "timeout", "scan", "request_unknown",
                     error_code="request_unknown", error="Historical transport 原文",
                     request_intent={"created_at": "2026-10-05T17:00:00+00:00"},
                     updated_at="2026-10-05T17:02:01+00:00",
                     error_details={"elapsed_s": 121, "original": "历史技术详情"},
                     core_start_us=0, core_end_us=3_500_000,
                     request_path="runs/test/scan/request.json",
                     cost={"status": "unknown", "estimated_usd": None,
                           "reason": "request_outcome_unknown"})
    repo.upsert_task(run["id"], "blocked", "scan", "failed", error_code="request_unknown")
    repo.upsert_task(run["id"], "clip_event_1", "clip", "failed", error_code="clip_failed",
                     error="FFmpeg exit status 原文", error_details={"returncode": 1},
                     requested_range_us=[4_500_000, 6_000_000])
    repo.log(run["id"], "scan", "OLD_LOG", "原始日志不能翻译", details={"原始": "raw"})
    before_run = repo.get_run(run["id"])
    before_tasks = repo.list_tasks(run["id"])
    before_logs = repo.get_logs(run["id"])

    zh_response = client.get(f"/api/runs/{run['id']}")
    en_response = client.get(f"/api/runs/{run['id']}", headers={"Accept-Language": "en-US,en;q=0.9"})
    zh, en = zh_response.json(), en_response.json()
    assert zh_response.headers["content-language"] == "zh"
    assert en_response.headers["content-language"] == "en"
    assert en_response.headers["vary"] == "Accept-Language"
    assert zh["query"] == en["query"] == raw_query
    assert zh["results"] == en["results"] == raw_results
    assert zh["tasks"] == en["tasks"] == before_tasks
    assert zh["error"] == en["error"] == before_run["error"]
    assert zh["created_at"] == en["created_at"] == before_run["created_at"]
    assert zh["updated_at"] == en["updated_at"] == before_run["updated_at"]
    assert zh["cost_summary"] == en["cost_summary"]
    assert zh["diagnostics"]["unknown_requests"] == en["diagnostics"]["unknown_requests"] == 1
    assert en["diagnostics"]["blocked_tasks"] == 1
    zh_issues = {i["task_id"] or i["code"]: i for i in zh["diagnostics"]["issues"]}
    for issue in en["diagnostics"]["issues"]:
        paired = zh_issues[issue["task_id"] or issue["code"]]
        for field in ("title", "message", "action"):
            assert issue[field] != paired[field]
            assert not re.search(r"[\u4e00-\u9fff]", issue[field])
        for field in ("code", "details", "technical_message", "range_us", "artifact_refs"):
            assert issue[field] == paired[field]
    assert zh_issues["timeout"]["details"]["possible_timeout"] is True
    assert zh_issues["timeout"]["details"]["elapsed_s"] == 121
    assert zh_issues["timeout"]["range_us"] == [0, 3_500_000]
    assert zh_issues["old_custom"]["technical_message"] == "历史 SDK 原文 (not translated)"
    assert zh_issues["old_custom"]["details"] == raw_results["errors"][0]["details"]
    assert client.get(f"/api/runs/{run['id']}/logs", headers={"Accept-Language": "en"}).json()["items"] == before_logs
    assert repo.get_run(run["id"]) == before_run
    assert repo.list_tasks(run["id"]) == before_tasks
    assert repo.get_logs(run["id"]) == before_logs


def test_key_validation_empty_upload_and_size_errors_use_the_requested_locale(client):
    media = register(client)
    en = {"Accept-Language": "en"}
    missing_key = client.post("/api/runs", json={"media_id": media["id"], "query": "Find events"}, headers=en)
    assert missing_key.status_code == 409
    assert missing_key.json()["error"]["code"] == "api_key_missing"
    assert missing_key.json()["error"]["message"] == "Set GEMINI_API_KEY in the local .env file, then restart the service."
    invalid = client.post("/api/runs", json={"media_id": media["id"], "query": "Find events",
                                            "config": {"model_concurrency": 99}}, headers=en)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["message"] == "The request parameters are invalid."
    assert invalid.json()["error"]["details"][0]["input"] == 99
    empty = client.post("/api/media", files={"file": ("empty.mp4", b"", "video/mp4")}, headers=en)
    assert empty.status_code == 422
    assert empty.json()["error"]["message"] == "The uploaded video is empty."
    for header, expected in [("en-US", "The video exceeds the 1 MB upload limit."),
                             ("zh-CN", "视频超过 1 MB 限制。")]:
        too_big = client.post("/api/media", files={"file": ("big.mp4", b"x" * 1_048_577, "video/mp4")},
                              headers={"Accept-Language": header})
        assert too_big.status_code == 413
        assert too_big.json()["error"]["message"] == expected
        assert too_big.json()["error"]["details"] == {"max_upload_mb": 1}


def test_fixture_query_idempotency_artifact_and_not_ready_messages(client):
    en = {"Accept-Language": "en"}
    normal = register(client, is_demo=False)
    fixture_error = client.post("/api/runs", json=fixture_body(normal), headers=en)
    assert fixture_error.json()["error"]["message"] == "Fixture mode only supports the built-in demo video."
    demo = register(client)
    empty_query = client.post("/api/runs", json=fixture_body(demo, "  "), headers=en)
    assert empty_query.json()["error"]["message"] == "Enter the event you want to locate."
    headers = {**en, "Idempotency-Key": "same-key"}
    run = client.post("/api/runs", json=fixture_body(demo), headers=headers).json()
    conflict = client.post("/api/runs", json=fixture_body(demo, "Find last event"), headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["message"] == "This request identifier has already been used with different parameters."
    not_ready = client.get(f"/api/runs/{run['id']}/results", headers=en)
    assert not_ready.json()["error"]["message"] == "Results are not available for this run yet."
    missing = client.get("/api/runs/no-such-run", headers=en)
    assert missing.status_code == 404
    assert missing.json()["error"]["message"] == "The requested record was not found."
    artifact = client.get("/api/artifacts/media/missing.mp4", headers=en)
    assert artifact.json()["error"]["message"] == "The requested file was not found."
    unknown_route = client.get("/api/no-such-route", headers=en)
    assert unknown_route.status_code == 404
    assert unknown_route.json()["error"]["code"] == "not_found"
    assert unknown_route.headers["content-language"] == "en"


def test_known_media_errors_translate_copy_but_keep_technical_details(client, monkeypatch):
    raw = "Decoder reported original 位置 error"
    details = {"returncode": 1, "elapsed_s": 1.25, "original": "原始内容"}

    def failing_probe(_):
        raise MediaError("decode_failed", raw, copy.deepcopy(details))

    monkeypatch.setattr(main, "probe_video", failing_probe)
    for language, expected in [("en", "The video could not be decoded."), ("zh", "视频解码失败。")]:
        response = client.post("/api/media", files={"file": ("source.mp4", b"fake", "video/mp4")},
                               headers={"Accept-Language": language})
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["message"] == expected
        assert error["technical_message"] == raw
        assert error["details"] == details


def test_unknown_external_api_error_is_preserved_verbatim(client):
    @client.app.get("/test-unknown-error")
    def error_route():
        raise APIError("external_extension_error", "外部安全原文: original failure", 502,
                       {"original": "不要翻译"})

    response = client.get("/test-unknown-error", headers={"Accept-Language": "en"})
    assert response.status_code == 502
    assert response.json()["error"] == {
        "code": "external_extension_error", "message": "外部安全原文: original failure",
        "details": {"original": "不要翻译"},
    }


@pytest.mark.parametrize("code", [
    "request_timeout", "blocked_by_unknown_request", "coverage_gap", "cancelled",
    "clip_failed", "clip_timestamp_mismatch", "video_processing_timeout", "missing_api_key",
])
def test_historical_diagnostic_codes_localize_all_system_fields_but_preserve_parameters(code):
    details = {"elapsed_s": 1200.5, "timeout_s": 1200, "gaps": [[1_000_000, 2_000_000]],
               "original": "安全技术原文"}
    run = {"status": "partial", "stage": "stopped", "config": {"request_timeout_s": 1200},
           "results": None, "error": {"code": code, "message": "保存的原始 message", "details": details}}
    original = copy.deepcopy(run)
    zh = run_diagnostics(run, [])["issues"][0]
    en = run_diagnostics(run, [], language="en")["issues"][0]
    for field in ("title", "message", "action"):
        assert zh[field] != en[field]
        assert not re.search(r"[\u4e00-\u9fff]", en[field])
    assert en["code"] == zh["code"] == code
    assert en["details"] == zh["details"] == details
    assert en["technical_message"] == zh["technical_message"] == "保存的原始 message"
    assert run == original
