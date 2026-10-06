import type { CallCost, CostSummary, LogEntry, Run, Task } from './types';
import { localeTag, t } from './i18n';
import './messages-panels';

export type CostState = 'estimated' | 'local' | 'pending' | 'unknown' | 'unrecorded';

export interface CallRow {
  id: string;
  taskId: string;
  attemptId: string | null;
  operation: string;
  inputMode: string;
  status: string;
  createdAt: string | null;
  latencyS: number | null;
  tokens: { input: number | null; cached: number | null; output: number | null; thinking: number | null };
  cost: CallCost | null;
  costState: CostState;
  estimatedUsd: number | null;
  source: 'task' | 'log';
}

const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const string = (value: unknown): string | null => typeof value === 'string' && value.length > 0 ? value : null;
export const nonnegativeNumber = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
const tokenCount = (value: unknown): number | null => typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null;
const isRecord = (value: unknown) => Object.keys(record(value)).length > 0;

function callRow(run: Run, task: Partial<Task>, fallback: Record<string, unknown>, source: 'task' | 'log'): CallRow {
  const intent = record(task.request_intent);
  const taskId = string(task.task_id) ?? string(fallback.task_id) ?? '未记录 task';
  const attemptId = string(task.attempt_id) ?? string(fallback.attempt_id);
  const operation = string(intent.operation) ?? string(fallback.operation) ?? (task.stage === 'scan' ? 'propose' : task.stage === 'refine' ? 'verify_refine' : task.stage === 'query' ? 'normalize_query' : string(task.stage) ?? '未记录');
  const costValue = isRecord(task.cost) ? task.cost : fallback.cost;
  const cost = isRecord(costValue) ? costValue as CallCost : null;
  const metrics = { ...record(fallback.request_metrics), ...record(task.request_metrics) };
  const usage = { ...record(fallback.usage), ...record(task.usage), ...record(cost?.token_counts) };
  const sampling = record(task.sampling);
  const inputMode = operation === 'normalize_query' ? 'text' : string(task.input_mode) ?? string(sampling.input_mode) ?? string(intent.input_mode) ?? string(metrics.input_mode) ?? string(fallback.input_mode) ??
    (operation === 'propose' ? string(run.config.scan_input_mode) : operation === 'verify_refine' ? string(run.config.refine_input_mode) : null) ?? '未记录';
  const status = string(task.status) ?? string(fallback.status) ?? '已返回费用记录';
  const amount = nonnegativeNumber(cost?.estimated_usd);
  let costState: CostState;
  let estimatedUsd: number | null = null;
  if (cost?.status === 'estimated' && amount !== null) {
    costState = 'estimated'; estimatedUsd = amount;
  } else if (cost?.status === 'not_billable' && (cost.provider === 'fixture' || cost.basis === 'local_fixture_no_remote_call' || run.config.provider === 'fixture')) {
    costState = 'local'; estimatedUsd = 0;
  } else if (cost) {
    costState = 'unknown';
  } else if (['submitting', 'running', 'pending'].includes(status)) {
    costState = 'pending';
  } else if (run.config.provider === 'fixture' && status === 'succeeded') {
    costState = 'local'; estimatedUsd = 0;
  } else if (status === 'request_unknown' || (status === 'cancelled' && run.config.scan_input_mode)) {
    costState = 'unknown';
  } else {
    costState = 'unrecorded';
  }
  return {
    id: attemptId ? `attempt:${attemptId}` : `task:${taskId}`, taskId, attemptId, operation, inputMode, status,
    createdAt: string(intent.created_at) ?? string(fallback.created_at) ?? null,
    latencyS: nonnegativeNumber(metrics.elapsed_s),
    tokens: { input: tokenCount(usage.prompt_token_count), cached: tokenCount(usage.cached_content_token_count), output: tokenCount(usage.candidates_token_count), thinking: tokenCount(usage.thoughts_token_count) },
    cost, costState, estimatedUsd, source,
  };
}

/** Canonical task costs win. A logged receipt can fill missing fields, not add the same attempt twice. */
export function collectCallRows(run: Run, logs: LogEntry[]): CallRow[] {
  const receipts = new Map<string, Record<string, unknown>>();
  for (const log of [...logs].sort((a, b) => a.seq - b.seq)) {
    if (log.code !== 'model_call_cost') continue;
    const details = record(log.details);
    const attemptId = string(details.attempt_id), taskId = string(details.task_id);
    if (!attemptId && !taskId) continue;
    receipts.set(attemptId ? `attempt:${attemptId}` : `task:${taskId}`, { ...details, stage: log.stage });
  }
  const rows = new Map<string, CallRow>();
  const consumedReceipts = new Set<string>();
  for (const task of run.tasks ?? []) {
    const modelStage = ['query', 'scan', 'refine'].includes(task.stage);
    if (!task.request_intent && !task.cost && !task.usage && !(modelStage && task.attempt_id)) continue;
    const key = task.attempt_id ? `attempt:${task.attempt_id}` : `task:${task.task_id}`;
    let fallbackKey = key;
    let fallback = receipts.get(fallbackKey);
    if (!fallback && task.attempt_id && receipts.has(`task:${task.task_id}`)) {
      fallbackKey = `task:${task.task_id}`;
      fallback = receipts.get(fallbackKey);
    }
    // Legacy task records sometimes lack attempt_id; only join when it is unambiguous.
    if (!fallback && !task.attempt_id) {
      const matches = [...receipts.entries()].filter(([, entry]) => entry.task_id === task.task_id);
      if (matches.length === 1) [fallbackKey, fallback] = matches[0];
      else if (matches.length > 1) continue;
    }
    const row = callRow(run, task, fallback ?? {}, 'task');
    rows.set(row.id, row);
    if (fallback) consumedReceipts.add(fallbackKey);
  }
  for (const [key, details] of receipts) {
    if (rows.has(key) || consumedReceipts.has(key)) continue;
    const row = callRow(run, { stage: string(details.stage) ?? undefined }, details, 'log');
    rows.set(row.id, row);
  }
  return [...rows.values()].sort((a, b) => {
    const aTime = a.createdAt ? Date.parse(a.createdAt) : NaN;
    const bTime = b.createdAt ? Date.parse(b.createdAt) : NaN;
    return Number.isFinite(aTime) && Number.isFinite(bTime) ? aTime - bTime : 0;
  });
}

export function summarizeCosts(run: Run, rows: CallRow[]): CostSummary & { source: 'server' | 'records'; unrecordedCalls: number } {
  const unrecordedCalls = rows.filter(row => row.costState === 'unrecorded').length;
  if (run.cost_summary) return { ...run.cost_summary, source: 'server', unrecordedCalls };
  const estimated = rows.filter(row => row.costState === 'estimated');
  const local = rows.filter(row => row.costState === 'local');
  const pending = rows.filter(row => row.costState === 'pending');
  const unknown = rows.filter(row => row.costState === 'unknown' || row.costState === 'unrecorded');
  const priorCount = nonnegativeNumber(run.progress.model_calls ?? run.results?.stats.model_calls) ?? 0;
  const missing = Math.max(0, priorCount - rows.length);
  return {
    currency: 'USD', estimated_usd: estimated.length + local.length > 0 ? rows.reduce((sum, row) => sum + (row.estimatedUsd ?? 0), 0) : null,
    estimated_calls: estimated.length, local_calls: local.length, pending_calls: pending.length,
    unknown_calls: unknown.length + missing, total_calls: rows.length + missing,
    complete: rows.length > 0 && pending.length + unknown.length + missing === 0,
    basis: 'paid_standard_list_estimate', source: 'records', unrecordedCalls: unrecordedCalls + missing,
  };
}

export function formatUsd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value < 0) return t('未知');
  if (value > 0 && value < 0.000001) return '< $0.000001';
  return `$${value.toFixed(6)}`;
}

export function formatTokens(value: number | null): string {
  return value === null ? '—' : value.toLocaleString(localeTag());
}
