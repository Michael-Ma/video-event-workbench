from app.diagnostics import run_diagnostics


def legacy_run():
    return {"id": "run", "status": "partial", "stage": "completed", "error": None,
            "config": {"request_timeout_s": 120}, "results": {
                "events": [], "errors": [{"window_id": "window_00000", "code": "request_unknown"},
                                        {"window_id": "window_00001", "code": "request_unknown"}]}}


def test_historical_unknown_request_is_not_reported_as_existing_partial_clips():
    tasks = [
        {"task_id": "window_00000", "stage": "scan", "status": "request_unknown",
         "error_code": "request_unknown", "request_intent": {"created_at": "2026-10-05T17:00:00+00:00"},
         "updated_at": "2026-10-05T17:02:01+00:00", "core_start_us": 0, "core_end_us": 30_000_000},
        {"task_id": "window_00001", "stage": "scan", "status": "failed", "error_code": "request_unknown"},
    ]
    diagnostics = run_diagnostics(legacy_run(), tasks)
    assert diagnostics["available_events"] == diagnostics["available_clips"] == 0
    assert diagnostics["unknown_requests"] == diagnostics["blocked_tasks"] == 1
    assert diagnostics["stopped_stage"] == "scan"
    assert len(diagnostics["issues"]) == 2
    first, second = diagnostics["issues"]
    assert first["details"]["elapsed_s"] == 121
    assert first["details"]["possible_timeout"] is True
    assert first["details"]["cause_unavailable"] is True
    assert second["not_submitted"] and second["code"] == "blocked_by_unknown_request"


def test_an_ordinary_pending_request_is_not_an_unknown_error():
    run = legacy_run() | {"status": "running", "results": None}
    diagnostics = run_diagnostics(run, [{"task_id": "query", "stage": "query", "status": "submitting",
                                       "request_intent": {"created_at": "now"}}])
    assert diagnostics["issues"] == [] and diagnostics["unknown_requests"] == 0


def test_resolved_split_parent_does_not_make_completed_run_look_failed():
    run = legacy_run() | {"status": "completed", "results": {"events": [], "errors": []}}
    diagnostics = run_diagnostics(run, [
        {"task_id": "window", "stage": "scan", "status": "failed", "error_code": "invalid_response",
         "replacement_task_ids": ["window_a", "window_b"]},
        {"task_id": "window_a", "stage": "scan", "status": "succeeded"},
        {"task_id": "window_b", "stage": "scan", "status": "succeeded"},
    ])
    assert diagnostics["issues"] == []
