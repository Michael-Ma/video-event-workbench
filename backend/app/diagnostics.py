"""User-facing diagnostics derived from the durable journal, including older runs."""
from __future__ import annotations

from datetime import datetime

from .localization import DIAGNOSTIC_COPY, diagnostic_copy

# Preserve the historical Chinese constant for callers; responses use read-time language.
ERROR_COPY = {code: copy[0] for code, copy in DIAGNOSTIC_COPY.items()}


def elapsed_s(task: dict) -> float | None:
    details = task.get("error_details") or {}
    if isinstance(details.get("elapsed_s"), (int, float)):
        return round(details["elapsed_s"], 1)
    start = (task.get("request_intent") or {}).get("created_at")
    end = task.get("updated_at")
    if not start or not end:
        return None
    try:
        return round(max(0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()), 1)
    except (TypeError, ValueError):
        return None


def run_diagnostics(run: dict, tasks: list[dict], language: str = "zh") -> dict:
    unresolved = [t for t in tasks if (t.get("status") == "request_unknown" or (t.get("status") == "submitting" and run["status"] in ("partial", "failed", "cancelled")))
                  and t.get("request_intent")]
    issues, seen = [], set()
    for task in tasks:
        if task.get("status") not in ("failed", "request_unknown", "interrupted", "skipped"):
            continue
        if task.get("replacement_task_ids"):
            continue  # Replaced parent failures are retained in debug, not unresolved failures.
        code = task.get("error_code", "task_failed")
        blocked = code == "blocked_by_unknown_request" or (
            code == "request_unknown" and not task.get("request_intent") and bool(unresolved))
        if blocked:
            code = "blocked_by_unknown_request"
        title, message, action = diagnostic_copy(code, language)
        details = dict(task.get("error_details") or {})
        duration = elapsed_s(task) if not blocked else None
        if duration is not None:
            details.setdefault("elapsed_s", duration)
        timeout = run.get("config", {}).get("request_timeout_s")
        if code in ("request_unknown", "request_timeout") and timeout:
            details.setdefault("timeout_s", timeout)
        if code == "request_unknown" and not details.get("exception_type"):
            details["cause_unavailable"] = True
            if duration is not None and timeout and duration >= timeout:
                details["possible_timeout"] = True
        issue = {
            "task_id": task["task_id"], "stage": task["stage"], "code": code,
            "title": title, "message": message, "action": action,
            "technical_message": task.get("error"), "details": details,
            "range_us": ([task["core_start_us"], task["core_end_us"]]
                         if "core_start_us" in task else
                         [task["read_start_us"], task["read_end_us"]] if "read_start_us" in task else task.get("requested_range_us")),
            "artifact_refs": [task[k] for k in ("error_path", "request_path", "response_path") if task.get(k)],
            "not_submitted": blocked,
        }
        issues.append(issue)
        seen.add(task["task_id"])
    error = run.get("error")
    if isinstance(error, dict) and not any(i["code"] == error.get("code") for i in issues):
        code = error.get("code", "pipeline_failed")
        title, message, action = diagnostic_copy(code, language, scope="run")
        issues.append({"task_id": None, "stage": run.get("last_stage") or run["stage"], "code": code,
                       "title": title, "message": message, "action": action, "technical_message": error.get("message"),
                       "details": error.get("details") or {}, "range_us": None, "artifact_refs": [], "not_submitted": False})
    for error in (run.get("results") or {}).get("errors", []):
        task_id = error.get("task_id") or error.get("window_id") or error.get("group_id")
        if task_id in seen or any(i["code"] == error.get("code") and task_id is None for i in issues):
            continue
        if task_id and any(task_id in t["task_id"] and t["task_id"] in seen for t in tasks):
            continue
        code = error.get("code", "task_failed")
        title, message, action = diagnostic_copy(code, language)
        issues.append({"task_id": task_id, "stage": error.get("stage", "scan"), "code": code,
                       "title": title, "message": message, "action": action, "technical_message": error.get("message"),
                       "details": dict(error.get("details") or {}), "range_us": None,
                       "artifact_refs": [], "not_submitted": False})
    # A blocked legacy task may retain request_unknown in the result envelope.
    unique = {}
    for issue in issues:
        key = issue["task_id"] or issue["code"]
        unique.setdefault(key, issue)
    issues = list(unique.values())
    events = (run.get("results") or {}).get("events", [])
    return {
        "issues": issues,
        "stopped_stage": next((i["stage"] for i in issues if not i["not_submitted"]), None),
        "unknown_requests": len(unresolved),
        "blocked_tasks": sum(i["not_submitted"] for i in issues),
        "available_events": sum(not e.get("duplicate_of") for e in events),
        "available_clips": sum(e.get("clip_status") == "succeeded" for e in events),
    }
