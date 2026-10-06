// System error copy mirrors stable API codes; saved technical text remains untouched.
import { getLanguage } from './i18n';

const apiErrors: Record<string, readonly [string, string]> = {
  "invalid_request": [
    "请求参数无效。",
    "The request parameters are invalid."
  ],
  "not_found": [
    "记录不存在。",
    "The requested record was not found."
  ],
  "method_not_allowed": [
    "不支持此请求方法。",
    "This request method is not supported."
  ],
  "empty_upload": [
    "上传的视频为空。",
    "The uploaded video is empty."
  ],
  "upload_too_large": [
    "视频超过上传大小限制。",
    "The video exceeds the upload size limit."
  ],
  "invalid_video": [
    "视频没有可用时长。",
    "The video has no usable duration."
  ],
  "media_not_ready": [
    "视频尚未准备好。",
    "The video is not ready yet."
  ],
  "empty_query": [
    "请输入要定位的事件。",
    "Enter the event you want to locate."
  ],
  "api_key_missing": [
    "请在本地 .env 中配置 GEMINI_API_KEY，然后重启服务。",
    "Set GEMINI_API_KEY in the local .env file, then restart the service."
  ],
  "fixture_requires_demo": [
    "流程测试模式只支持内置示例视频。",
    "Fixture mode only supports the built-in demo video."
  ],
  "invalid_idempotency_key": [
    "无效的请求标识。",
    "The request identifier is invalid."
  ],
  "idempotency_conflict": [
    "同一请求标识已用于不同参数。",
    "This request identifier has already been used with different parameters."
  ],
  "results_not_ready": [
    "该任务还没有结果。",
    "Results are not available for this run yet."
  ],
  "invalid_artifact_path": [
    "无效的文件路径。",
    "The artifact path is invalid."
  ],
  "artifact_not_found": [
    "文件不存在。",
    "The requested file was not found."
  ],
  "tool_missing": [
    "缺少必需的视频处理工具。",
    "A required video-processing tool is unavailable."
  ],
  "media_timeout": [
    "本地视频处理超时。",
    "Local video processing timed out."
  ],
  "media_process_failed": [
    "本地视频处理失败。",
    "Local video processing failed."
  ],
  "invalid_media_path": [
    "视频文件路径无效。",
    "The media file path is invalid."
  ],
  "media_not_found": [
    "原视频文件不存在。",
    "The original video file is unavailable."
  ],
  "media_invalid": [
    "视频文件无法读取或格式无效。",
    "The video cannot be read or its format is invalid."
  ],
  "missing_frame_timestamp": [
    "视频帧缺少显示时间戳。",
    "A video frame has no display timestamp."
  ],
  "non_monotonic_timestamps": [
    "视频显示时间戳重复或未递增。",
    "The video display timestamps repeat or are not increasing."
  ],
  "decode_failed": [
    "视频解码失败。",
    "The video could not be decoded."
  ],
  "unsupported_rotation": [
    "当前仅支持直角方向的视频旋转。",
    "Only video rotations in multiples of 90 degrees are supported."
  ],
  "preview_mapping_failed": [
    "预览视频与原视频的时间映射不一致。",
    "The preview does not preserve the source frame timing or count."
  ],
  "media_not_prepared": [
    "视频尚未完成预处理。",
    "Video preparation has not completed."
  ],
  "frame_index_missing": [
    "源帧索引不存在或无效。",
    "The source frame index is missing or invalid."
  ],
  "frame_index_mismatch": [
    "源帧索引与当前视频或版本不匹配。",
    "The source frame index does not match this video or version."
  ],
  "invalid_time_range": [
    "视频时间范围无效。",
    "The video time range is invalid."
  ],
  "invalid_sampling_config": [
    "采样配置无效。",
    "The sampling configuration is invalid."
  ],
  "sampling_limit_exceeded": [
    "窗口采样量超过配置上限，需要拆分窗口。",
    "The window exceeds the sampling budget and needs to be split."
  ],
  "no_observed_frames": [
    "请求的时间范围内没有可用源帧。",
    "No source frames are available in the requested time range."
  ],
  "frame_mapping_failed": [
    "解码结果无法复现已索引的源帧。",
    "Decoding did not reproduce the indexed source frames."
  ],
  "clip_frame_mismatch": [
    "导出片段与请求的源帧不一致。",
    "The exported clip does not contain the requested source frames."
  ],
  "clip_timestamp_mismatch": [
    "导出片段改变了源帧时间。",
    "The exported clip changed the source frame timing."
  ],
  "video_duration_mismatch": [
    "模型输入视频改变了源视频显示时长。",
    "The prepared model input changed the source display duration."
  ],
  "video_origin_mismatch": [
    "模型输入视频没有从时间零点开始。",
    "The prepared model input does not start at timestamp zero."
  ],
  "provider_init_failed": [
    "模型服务初始化失败，请检查配置。",
    "The model provider could not be initialized. Check its configuration."
  ],
  "input_mode_invalid": [
    "不支持此模型输入模式。",
    "This model input mode is not supported."
  ],
  "input_invalid": [
    "模型输入文件或采样参数无效。",
    "The model input or its sampling parameters are invalid."
  ],
  "input_unavailable": [
    "已准备的模型输入不存在或无效。",
    "A prepared model input is unavailable or invalid."
  ],
  "input_limit": [
    "模型输入超过配置上限。",
    "The model input exceeds the configured limit."
  ],
  "upload_response_invalid": [
    "模型服务未返回有效的视频文件引用。",
    "The model provider did not return a valid video file reference."
  ],
  "video_processing_failed": [
    "模型服务无法处理上传的视频。",
    "The model provider could not process the uploaded video."
  ],
  "video_processing_timeout": [
    "等待模型服务准备视频时超时。",
    "The uploaded video was not ready before the processing timeout."
  ],
  "unsupported_fixture_query": [
    "流程测试模式仅支持全部、首次或最后一次中的一种选择。",
    "Fixture mode supports one occurrence policy: all, first, or last."
  ],
  "missing_api_key": [
    "请在本地 .env 中配置 GEMINI_API_KEY，然后重启服务。",
    "Set GEMINI_API_KEY in the local .env file, then restart the service."
  ],
  "fixture_forbidden": [
    "流程测试模式只支持内置示例视频。",
    "Fixture mode only supports the built-in demo video."
  ]
};

export function localizedAPIError(code: string, details?: unknown): string | undefined {
  const pair = apiErrors[code];
  if (!pair) return undefined;
  if (code === 'upload_too_large' && details && typeof details === 'object') {
    const limit = (details as Record<string, unknown>).max_upload_mb;
    if (typeof limit === 'number' && Number.isFinite(limit)) return getLanguage() === 'en'
      ? `The video exceeds the ${limit} MB upload limit.` : `视频超过 ${limit} MB 限制。`;
  }
  return pair[getLanguage() === 'en' ? 1 : 0];
}
