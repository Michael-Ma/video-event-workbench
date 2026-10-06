"""User-facing diagnostics derived from the durable journal, including older runs."""
from __future__ import annotations

from datetime import datetime

ERROR_COPY = {
    "request_timeout": ("模型请求超时", "等待模型响应超时，后续模型请求已暂停。", "可延长请求超时或缩小窗口后手动新建运行。原请求未自动重试。"),
    "request_unknown": ("模型请求结果未知", "没有收到明确的模型响应，后续模型请求已暂停。", "先检查请求记录。重新运行会创建新的模型调用。"),
    "blocked_by_unknown_request": ("窗口未执行", "前一个模型请求的结果未知，因此此窗口没有提交。", "等待问题确认后再手动新建运行。"),
    "call_budget": ("达到模型调用上限", "本次运行已用完配置的模型调用次数。", "可提高调用上限后手动新建运行。"),
    "invalid_response": ("模型返回格式不完整", "模型响应被截断或不符合输出约定。", "检查响应记录，尝试缩小窗口或调整输出上限。"),
    "incomplete_response": ("模型输出未覆盖整个窗口", "窗口拆分后仍未获得完整输出。", "可缩小负责窗口后新建运行。"),
    "invalid_event": ("事件时间或证据无效", "返回的事件时间或证据无法对应本次送入的帧。", "检查原始响应与送入帧。"),
    "refinement_failed": ("事件核实失败", "局部核实没有完成；已有候选会保留为不确定项。", "查看对应候选和响应记录。"),
    "clip_failed": ("片段截取失败", "事件定位记录保留，但这个片段尚未成功生成。", "查看裁片任务的具体错误。"),
    "provider_http_error": ("模型服务返回错误", "模型 API 返回了错误状态。", "检查状态码及模型配置。"),
}


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


def run_diagnostics(run: dict, tasks: list[dict]) -> dict:
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
        title, message, action = ERROR_COPY.get(code, ("任务未完成", task.get("error") or "此步骤没有完成。", "查看任务记录了解具体原因。"))
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
        title, message, action = ERROR_COPY.get(code, ("运行未完成", error.get("message", "运行发生错误。"), "检查处理记录。"))
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
        title, message, action = ERROR_COPY.get(code, ("任务未完成", error.get("message", "此步骤没有完成。"), "查看处理记录。"))
        issues.append({"task_id": task_id, "stage": error.get("stage", "scan"), "code": code,
                       "title": title, "message": message, "action": action, "technical_message": error.get("message"),
                       "details": {}, "range_us": None, "artifact_refs": [], "not_submitted": False})
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

