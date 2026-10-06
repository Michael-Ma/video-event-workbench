"""Small read-time system copy catalog. Never translates saved user/model/journal text."""
from __future__ import annotations

import math
from typing import Literal

Language = Literal["zh", "en"]


def language_from_header(header: str | None) -> Language:
    """Choose the highest-quality supported language; preserve Chinese as the fallback."""
    choices: list[tuple[float, int, Language]] = []
    for index, item in enumerate((header or "").split(",")):
        parts = [part.strip() for part in item.split(";")]
        primary = parts[0].lower().replace("_", "-").split("-", 1)[0]
        if primary not in ("en", "zh"):
            continue
        quality = 1.0
        for parameter in parts[1:]:
            name, separator, value = parameter.partition("=")
            if separator and name.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0
                break
        if math.isfinite(quality) and 0 < quality <= 1:
            choices.append((quality, -index, primary))
    return max(choices)[2] if choices else "zh"


# Each pair is (Chinese, English). Codes remain stable across locales.
API_MESSAGES: dict[str, tuple[str, str]] = {
    "invalid_request": ("请求参数无效。", "The request parameters are invalid."),
    "not_found": ("记录不存在。", "The requested record was not found."),
    "method_not_allowed": ("不支持此请求方法。", "This request method is not supported."),
    "empty_upload": ("上传的视频为空。", "The uploaded video is empty."),
    "upload_too_large": ("视频超过上传大小限制。", "The video exceeds the upload size limit."),
    "invalid_video": ("视频没有可用时长。", "The video has no usable duration."),
    "media_not_ready": ("视频尚未准备好。", "The video is not ready yet."),
    "empty_query": ("请输入要定位的事件。", "Enter the event you want to locate."),
    "api_key_missing": ("请在本地 .env 中配置 GEMINI_API_KEY，然后重启服务。",
                        "Set GEMINI_API_KEY in the local .env file, then restart the service."),
    "fixture_requires_demo": ("流程测试模式只支持内置示例视频。",
                              "Fixture mode only supports the built-in demo video."),
    "invalid_idempotency_key": ("无效的请求标识。", "The request identifier is invalid."),
    "idempotency_conflict": ("同一请求标识已用于不同参数。",
                             "This request identifier has already been used with different parameters."),
    "results_not_ready": ("该任务还没有结果。", "Results are not available for this run yet."),
    "invalid_artifact_path": ("无效的文件路径。", "The artifact path is invalid."),
    "artifact_not_found": ("文件不存在。", "The requested file was not found."),
    "tool_missing": ("缺少必需的视频处理工具。", "A required video-processing tool is unavailable."),
    "media_timeout": ("本地视频处理超时。", "Local video processing timed out."),
    "media_process_failed": ("本地视频处理失败。", "Local video processing failed."),
    "invalid_media_path": ("视频文件路径无效。", "The media file path is invalid."),
    "media_not_found": ("原视频文件不存在。", "The original video file is unavailable."),
    "media_invalid": ("视频文件无法读取或格式无效。", "The video cannot be read or its format is invalid."),
    "missing_frame_timestamp": ("视频帧缺少显示时间戳。", "A video frame has no display timestamp."),
    "non_monotonic_timestamps": ("视频显示时间戳重复或未递增。",
                                 "The video display timestamps repeat or are not increasing."),
    "decode_failed": ("视频解码失败。", "The video could not be decoded."),
    "unsupported_rotation": ("当前仅支持直角方向的视频旋转。",
                             "Only video rotations in multiples of 90 degrees are supported."),
    "preview_mapping_failed": ("预览视频与原视频的时间映射不一致。",
                               "The preview does not preserve the source frame timing or count."),
    "media_not_prepared": ("视频尚未完成预处理。", "Video preparation has not completed."),
    "frame_index_missing": ("源帧索引不存在或无效。", "The source frame index is missing or invalid."),
    "frame_index_mismatch": ("源帧索引与当前视频或版本不匹配。",
                             "The source frame index does not match this video or version."),
    "invalid_time_range": ("视频时间范围无效。", "The video time range is invalid."),
    "invalid_sampling_config": ("采样配置无效。", "The sampling configuration is invalid."),
    "sampling_limit_exceeded": ("窗口采样量超过配置上限，需要拆分窗口。",
                                "The window exceeds the sampling budget and needs to be split."),
    "no_observed_frames": ("请求的时间范围内没有可用源帧。",
                           "No source frames are available in the requested time range."),
    "frame_mapping_failed": ("解码结果无法复现已索引的源帧。",
                             "Decoding did not reproduce the indexed source frames."),
    "clip_frame_mismatch": ("导出片段与请求的源帧不一致。",
                            "The exported clip does not contain the requested source frames."),
    "clip_timestamp_mismatch": ("导出片段改变了源帧时间。",
                                "The exported clip changed the source frame timing."),
    "video_duration_mismatch": ("模型输入视频改变了源视频显示时长。",
                                "The prepared model input changed the source display duration."),
    "video_origin_mismatch": ("模型输入视频没有从时间零点开始。",
                              "The prepared model input does not start at timestamp zero."),
    "provider_init_failed": ("模型服务初始化失败，请检查配置。",
                             "The model provider could not be initialized. Check its configuration."),
    "input_mode_invalid": ("不支持此模型输入模式。", "This model input mode is not supported."),
    "input_invalid": ("模型输入文件或采样参数无效。", "The model input or its sampling parameters are invalid."),
    "input_unavailable": ("已准备的模型输入不存在或无效。",
                          "A prepared model input is unavailable or invalid."),
    "input_limit": ("模型输入超过配置上限。", "The model input exceeds the configured limit."),
    "upload_response_invalid": ("模型服务未返回有效的视频文件引用。",
                                "The model provider did not return a valid video file reference."),
    "video_processing_failed": ("模型服务无法处理上传的视频。",
                                "The model provider could not process the uploaded video."),
    "video_processing_timeout": ("等待模型服务准备视频时超时。",
                                 "The uploaded video was not ready before the processing timeout."),
    "unsupported_fixture_query": ("流程测试模式仅支持全部、首次或最后一次中的一种选择。",
                                  "Fixture mode supports one occurrence policy: all, first, or last."),
}
API_MESSAGES["missing_api_key"] = API_MESSAGES["api_key_missing"]
API_MESSAGES["fixture_forbidden"] = API_MESSAGES["fixture_requires_demo"]


def api_message(code: str, original: str, language: str = "zh", *, parameters: dict | None = None) -> str:
    lang = language_from_header(language)
    if code == "upload_too_large":
        limit = (parameters or {}).get("max_upload_mb")
        if isinstance(limit, (int, float)) and not isinstance(limit, bool) and math.isfinite(limit):
            number = f"{limit:g}"
            return (f"The video exceeds the {number} MB upload limit." if lang == "en" else
                    f"视频超过 {number} MB 限制。")
    copy = API_MESSAGES.get(code)
    return copy[1 if lang == "en" else 0] if copy else original


# title / explanation / next step. Technical messages and structured details stay raw.
DIAGNOSTIC_COPY: dict[str, tuple[tuple[str, str, str], tuple[str, str, str]]] = {
    "request_timeout": (
        ("模型请求超时", "等待模型响应超时，后续模型请求已暂停。", "可延长请求超时或缩小窗口后手动新建运行。原请求未自动重试。"),
        ("Model request timed out", "The model response timed out. Further model requests were paused.",
         "Increase the timeout or reduce the window size, then start a new run. The original request was not automatically retried.")),
    "request_unknown": (
        ("模型请求结果未知", "没有收到明确的模型响应，后续模型请求已暂停。", "先检查请求记录。重新运行会创建新的模型调用。"),
        ("Model request outcome unknown", "No definite model response was received. Further model requests were paused.",
         "Check the request record first. Starting another run creates new model calls.")),
    "blocked_by_unknown_request": (
        ("窗口未执行", "前一个模型请求的结果未知，因此此窗口没有提交。", "等待问题确认后再手动新建运行。"),
        ("Window was not submitted", "A previous model request has an unknown outcome, so this window was not submitted.",
         "Review the unresolved request before starting another run.")),
    "call_budget": (
        ("达到模型调用上限", "本次运行已用完配置的模型调用次数。", "可提高调用上限后手动新建运行。"),
        ("Model call limit reached", "This run reached its configured model call limit.",
         "Increase the call limit, then start a new run.")),
    "invalid_response": (
        ("模型返回格式不完整", "模型响应被截断或不符合输出约定。", "检查响应记录，尝试缩小窗口或调整输出上限。"),
        ("Invalid model response", "The model response was truncated or did not meet the output contract.",
         "Check the response record. Try a smaller window or adjust the output limit.")),
    "incomplete_response": (
        ("模型输出未覆盖整个窗口", "窗口拆分后仍未获得完整输出。", "可缩小负责窗口后新建运行。"),
        ("Incomplete window response", "The output remained incomplete after the window was split.",
         "Reduce the core window size, then start a new run.")),
    "invalid_event": (
        ("事件时间或证据无效", "返回的事件时间或证据无法对应本次送入的帧。", "检查原始响应与送入帧。"),
        ("Invalid event timing or evidence", "The returned event times or evidence could not be mapped to the submitted input.",
         "Inspect the original response and submitted input.")),
    "refinement_failed": (
        ("事件核实失败", "局部核实没有完成；已有候选会保留为不确定项。", "查看对应候选和响应记录。"),
        ("Event verification failed", "Local verification did not complete. Existing candidates remain uncertain.",
         "Inspect the corresponding candidates and response records.")),
    "clip_failed": (
        ("片段截取失败", "事件定位记录保留，但这个片段尚未成功生成。", "查看裁片任务的具体错误。"),
        ("Clip export failed", "The event location was retained, but its clip was not generated successfully.",
         "Inspect the clip task's technical error.")),
    "provider_http_error": (
        ("模型服务返回错误", "模型 API 返回了错误状态。", "检查状态码及模型配置。"),
        ("Model provider returned an error", "The model API returned an error status.",
         "Check the HTTP status and model configuration.")),
    "coverage_gap": (
        ("扫描存在缺口", "部分源视频范围没有完成扫描。已有结果仍然保留。", "查看缺口范围和对应窗口任务，再决定是否新建运行。"),
        ("Scan coverage is incomplete", "Some source ranges were not scanned successfully. Existing results were retained.",
         "Inspect the uncovered ranges and window tasks before starting another run.")),
    "cancelled": (
        ("运行已取消", "后续任务已停止；已发出的模型请求仍可能返回并产生费用。", "查看已保存结果，或按需新建运行。"),
        ("Run cancelled", "Further work was stopped. Requests already sent may still return and incur charges.",
         "View saved results or start a new run when needed.")),
    "scan_failed": (
        ("窗口处理未完成", "这个视频窗口没有完成处理。", "查看窗口任务和原始错误记录。"),
        ("Window processing did not complete", "This video window was not processed successfully.",
         "Inspect the window task and original error record.")),
    "worker_interrupted_after_intent": (
        ("请求发送后服务中断", "服务重启时发现尚未结算的模型请求，未自动重新提交。", "查看已保存的请求记录；原请求可能已经产生费用。"),
        ("Worker stopped with an unsettled request", "An unsettled model request was found after a restart and was not automatically resent.",
         "Inspect its saved request record. The original request may already have incurred charges.")),
    "stale_attempt": (
        ("旧尝试的结果未发布", "此任务已由更新的尝试接管，旧结果没有覆盖新结果。", "查看当前尝试的任务记录。"),
        ("Stale attempt was not published", "A newer attempt owns this task. The old result did not replace it.",
         "Inspect the current attempt's task record.")),
    "task_in_flight": (
        ("任务请求仍在执行", "同一任务已有在途请求，没有重复发送。", "等待已有请求完成，或查看其状态。"),
        ("Task request is already in flight", "This task already has an active request. No duplicate was sent.",
         "Wait for the existing request or inspect its status.")),
    "pipeline_failed": (
        ("运行未完成", "处理流程发生错误。原始技术信息保留在详情中。", "检查处理记录。"),
        ("Run did not complete", "The processing pipeline encountered an error. Original technical information is available in the details.",
         "Inspect the processing records.")),
    "task_failed": (
        ("任务未完成", "此步骤没有完成。原始技术信息保留在详情中。", "查看任务记录了解具体原因。"),
        ("Task did not complete", "This step did not complete. Original technical information is available in the details.",
         "Inspect the task record for the cause.")),
}
DIAGNOSTIC_COPY["run_cancelled"] = DIAGNOSTIC_COPY["cancelled"]
DIAGNOSTIC_COPY["scan_incomplete"] = DIAGNOSTIC_COPY["coverage_gap"]
DIAGNOSTIC_COPY["adapter_unknown"] = DIAGNOSTIC_COPY["request_unknown"]
DIAGNOSTIC_COPY["prior_request_failed"] = DIAGNOSTIC_COPY["task_failed"]

MEDIA_CODES = {
    "tool_missing", "media_timeout", "media_process_failed", "invalid_media_path", "media_not_found",
    "media_invalid", "missing_frame_timestamp", "non_monotonic_timestamps", "decode_failed",
    "unsupported_rotation", "preview_mapping_failed", "media_not_prepared", "frame_index_missing",
    "frame_index_mismatch", "invalid_time_range", "invalid_sampling_config", "sampling_limit_exceeded",
    "no_observed_frames", "frame_mapping_failed", "clip_frame_mismatch", "clip_timestamp_mismatch",
    "video_duration_mismatch", "video_origin_mismatch",
}


def diagnostic_copy(code: str, language: str = "zh", *, scope: str = "task") -> tuple[str, str, str]:
    lang = language_from_header(language)
    index = 1 if lang == "en" else 0
    if code in DIAGNOSTIC_COPY:
        return DIAGNOSTIC_COPY[code][index]
    if code in API_MESSAGES:
        if code in MEDIA_CODES:
            title = "Media processing did not complete" if lang == "en" else "媒体处理未完成"
            action = ("Inspect the media task, source timestamps, and input configuration."
                      if lang == "en" else "查看媒体任务、源时间戳和输入配置。")
        else:
            title = "Input or provider setup needs attention" if lang == "en" else "输入或模型配置需要检查"
            action = ("Check the task details and configuration before starting a new run."
                      if lang == "en" else "检查任务详情与配置后再新建运行。")
        return title, API_MESSAGES[code][index], action
    # Unknown external errors stay in technical_message/details, never sent to a translator.
    return DIAGNOSTIC_COPY["pipeline_failed" if scope == "run" else "task_failed"][index]
