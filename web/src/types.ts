export type TimeRange = [number, number];
export type Profile = 'auto' | 'action' | 'point';
export type Provider = 'gemini' | 'fixture';
export type InputMode = 'images' | 'video';
export type ResultBucket = 'matched' | 'uncertain' | 'rejected';

export interface Health {
  status: string;
  api_key_configured: boolean;
  default_model: string;
  ffmpeg_available: boolean;
  ffprobe_available: boolean;
  worker_alive: boolean;
}

export interface Media {
  id: string;
  filename: string;
  duration_us?: number;
  status: string;
  metadata?: Record<string, unknown>;
  is_demo?: boolean;
  original_url?: string;
  preview_url?: string;
  preview_audio_included?: boolean;
}

export interface QuerySpec {
  raw_query: string;
  event_kind: 'point' | 'interval';
  target_description: string;
  required_evidence: string[];
  exclusions: string[];
  occurrence_policy: 'all' | 'first' | 'last';
  anchor_rule?: string | null;
  start_rule?: string | null;
  end_rule?: string | null;
  defaults_used: string[];
}

export interface EventLocation {
  kind: 'point' | 'interval';
  start_us?: number | null;
  end_us?: number | null;
  anchor_us?: number | null;
  start_range_us?: TimeRange | null;
  end_range_us?: TimeRange | null;
  anchor_range_us?: TimeRange | null;
  open_left?: boolean;
  open_right?: boolean;
}

export interface EventResult {
  event_id: string;
  source_candidate_ids: string[];
  decision: 'supported' | 'rejected' | 'unresolved';
  boundary_status: 'bounded' | 'open' | 'unknown';
  result_bucket: ResultBucket;
  location: EventLocation;
  entity_key: string;
  evidence_refs: string[];
  reason: string;
  uncertainty_reasons: string[];
  duplicate_of?: string | null;
  clip?: {
    path: string;
    url?: string | null;
    cut_range_us: TimeRange;
    actual_range_us?: TimeRange | null;
    kind: 'event_with_context' | 'context_fallback';
    metadata: Record<string, unknown>;
  } | null;
  clip_status: 'pending' | 'succeeded' | 'failed' | 'not_required';
}

export interface Coverage {
  duration_us: number;
  covered_us: number;
  gaps: unknown[];
}

export interface Results {
  run_id: string;
  provider: string;
  model_id: string;
  query_spec: QuerySpec;
  events: EventResult[];
  scan_complete: boolean;
  coverage: Coverage;
  stats: Record<string, unknown>;
  limitations: string[];
  provisional?: boolean;
  errors?: { code: string; message?: string; task_id?: string; window_id?: string; stage?: string }[];
}

export interface Task {
  task_id: string;
  stage: string;
  status: string;
  window?: Record<string, unknown>;
  request_intent?: { created_at?: string; operation?: string; input_mode?: string; pricing?: Record<string, unknown> };
  attempt_id?: string;
  input_mode?: string;
  sampling?: Record<string, unknown>;
  usage?: Record<string, unknown>;
  request_metrics?: Record<string, unknown>;
  cost?: CallCost;
  replacement_task_ids?: string[];
  [key: string]: unknown;
}

export interface Run {
  id: string;
  media_id: string;
  query: string;
  config: Record<string, unknown>;
  query_spec?: QuerySpec | null;
  status: string;
  stage: string;
  last_stage?: string;
  diagnostics?: { issues: RunIssue[]; stopped_stage: string | null; unknown_requests: number; blocked_tasks: number; available_events: number; available_clips: number };
  progress: Record<string, unknown>;
  results?: Results | null;
  error?: unknown;
  created_at: string;
  started_at?: string;
  processing_elapsed_s?: number;
  updated_at: string;
  tasks?: Task[];
  cost_summary?: CostSummary;
  [key: string]: unknown;
}

export interface CallCost {
  status: 'estimated' | 'not_billable' | 'unknown';
  estimated_usd: number | null;
  currency?: string;
  provider?: string;
  model_id?: string;
  basis?: string;
  pricing_version?: string;
  pricing_source?: string;
  submitted_on?: string;
  effective_from?: string;
  rates_per_million_tokens?: Record<string, number> | null;
  token_counts?: Record<string, number> | null;
  reason?: string;
  [key: string]: unknown;
}

export interface CostSummary {
  currency: 'USD';
  estimated_usd: number | null;
  estimated_calls: number;
  unknown_calls: number;
  pending_calls: number;
  local_calls: number;
  total_calls: number;
  complete: boolean;
  basis: string;
}

export interface LogEntry {
  seq: number;
  time: string;
  stage: string;
  level: string;
  code: string;
  message: string;
  details: Record<string, unknown>;
  artifact_refs?: string[];
}

export interface RunIssue {
  task_id: string | null; stage: string; code: string; title: string; message: string;
  action: string; technical_message?: string | null; details: Record<string, unknown>;
  range_us?: TimeRange | null; artifact_refs: string[]; not_submitted: boolean;
}
