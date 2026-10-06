import { describe, expect, it } from 'vitest';
import { collectCallRows, formatUsd, summarizeCosts } from './costs';
import type { CallCost, LogEntry, Run, Task } from './types';

const cost: CallCost = { status: 'estimated', estimated_usd: 0.012345, provider: 'gemini',
  token_counts: { prompt_token_count: 1000, cached_content_token_count: 200, candidates_token_count: 30, thoughts_token_count: 70 },
  pricing_version: 'test-standard-1', pricing_source: 'https://example.test/pricing' };

function run(tasks: Task[] = []): Run {
  return { id: 'run', media_id: 'media', status: 'completed', stage: 'completed',
    query: 'Find every contact', config: { provider: 'gemini', scan_input_mode: 'images', refine_input_mode: 'video' },
    progress: {}, created_at: '2026-10-05T17:00:00Z', updated_at: '2026-10-05T17:02:00Z', tasks };
}

function task(fields: Partial<Task> = {}): Task {
  return { task_id: 'scan-1', stage: 'scan', status: 'succeeded', attempt_id: 'attempt-1',
    request_intent: { operation: 'propose', created_at: '2026-10-05T17:00:00Z' }, ...fields };
}

function log(seq = 1, fields: Record<string, unknown> = {}): LogEntry {
  return { seq, stage: 'scan', code: 'model_call_cost', level: 'info', time: '2026-10-05T17:01:00Z', message: 'Saved cost',
    details: { task_id: 'scan-1', attempt_id: 'attempt-1', operation: 'propose', cost, ...fields } };
}

describe('per-call cost accounting', () => {
  it('excludes local clip attempts from the model-call ledger', () => {
    const current = run([task({ cost }),
      { task_id: 'clip-event', stage: 'clip', status: 'succeeded', attempt_id: 'local-clip-attempt' }]);
    expect(collectCallRows(current, [])).toHaveLength(1);
    expect(summarizeCosts(current, collectCallRows(current, []))).toMatchObject({
      total_calls: 1, estimated_calls: 1, unknown_calls: 0,
    });
  });
  it('uses task receipts and never adds the same attempt again from incremental logs', () => {
    const current = run([task({ cost, request_metrics: { elapsed_s: 3.2 }, input_mode: 'video' })]);
    const rows = collectCallRows(current, [log(), log(2)]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ inputMode: 'video', operation: 'propose', latencyS: 3.2,
      estimatedUsd: 0.012345, source: 'task', tokens: { input: 1000, cached: 200, output: 30, thinking: 70 } });
    expect(summarizeCosts(current, rows).estimated_usd).toBe(0.012345);
  });

  it('falls back to the matching log when the task has no saved usage or cost', () => {
    const current = run([task()]);
    const rows = collectCallRows(current, [log(1, { request_metrics: { elapsed_s: 8 } })]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ costState: 'estimated', latencyS: 8 });
  });

  it('does not duplicate a legacy task-only receipt alongside its canonical attempt', () => {
    const current = run([task()]);
    const rows = collectCallRows(current, [log(1, { attempt_id: undefined })]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ attemptId: 'attempt-1', estimatedUsd: 0.012345 });
  });

  it('keeps distinct attempts and does not turn unknown calls into zero', () => {
    const current = run([task({ cost }), task({ task_id: 'refine-1', stage: 'refine', attempt_id: 'attempt-2', status: 'request_unknown',
      request_intent: { operation: 'verify_refine' }, cost: { status: 'unknown', estimated_usd: null, reason: 'request_outcome_unknown' } })]);
    const rows = collectCallRows(current, []);
    expect(rows[1]).toMatchObject({ inputMode: 'video', costState: 'unknown', estimatedUsd: null });
    expect(summarizeCosts(current, rows)).toMatchObject({ estimated_usd: 0.012345, total_calls: 2, unknown_calls: 1, complete: false });
  });

  it('leaves pending requests and runs with no calls without a dollar estimate', () => {
    const current = { ...run([task({ status: 'submitting' })]), status: 'running' };
    expect(collectCallRows(current, [])[0].costState).toBe('pending');
    expect(summarizeCosts(current, collectCallRows(current, []))).toMatchObject({ estimated_usd: null, pending_calls: 1 });
    const queued = { ...run([{ task_id: 'scan-1', stage: 'scan', status: 'pending' }]), status: 'queued' };
    expect(collectCallRows(queued, [])).toHaveLength(0);
    expect(summarizeCosts(queued, []).estimated_usd).toBeNull();
  });

  it('marks historical missing costs as unrecorded even when a call succeeded', () => {
    const current = run([task()]); current.config = { provider: 'gemini' };
    const rows = collectCallRows(current, []);
    expect(rows[0]).toMatchObject({ costState: 'unrecorded', estimatedUsd: null, inputMode: '未记录' });
    expect(summarizeCosts(current, rows)).toMatchObject({ estimated_usd: null, unknown_calls: 1, unrecordedCalls: 1 });
  });

  it('does not suppress paid usage when a run is cancelled', () => {
    const current = { ...run([task({ status: 'cancelled', cost })]), status: 'cancelled' };
    expect(summarizeCosts(current, collectCallRows(current, [])).estimated_usd).toBe(0.012345);
  });

  it('keeps a request in flight after run cancellation until its receipt arrives', () => {
    const current = { ...run([task({ status: 'submitting' })]), status: 'cancelled' };
    expect(collectCallRows(current, [])[0]).toMatchObject({ costState: 'pending', estimatedUsd: null });
    current.tasks![0].status = 'cancelled';
    current.tasks![0].cost = cost;
    expect(collectCallRows(current, [])[0]).toMatchObject({ costState: 'estimated', estimatedUsd: 0.012345 });
  });

  it('shows explicit Fixture calls as zero and query normalization as text', () => {
    const current = run([task({ stage: 'query', request_intent: { operation: 'normalize_query' },
      cost: { status: 'not_billable', estimated_usd: 0, provider: 'fixture', basis: 'local_fixture_no_remote_call' } })]);
    current.config.provider = 'fixture';
    const rows = collectCallRows(current, []);
    expect(rows[0]).toMatchObject({ inputMode: 'text', estimatedUsd: 0, costState: 'local' });
    expect(summarizeCosts(current, rows)).toMatchObject({ local_calls: 1, estimated_usd: 0, unknown_calls: 0 });
  });

  it('keeps the server summary authoritative when only part of the incremental log has arrived', () => {
    const current = run([task({ cost })]);
    current.cost_summary = { currency: 'USD', estimated_usd: 0.5, estimated_calls: 8, unknown_calls: 1,
      pending_calls: 1, local_calls: 0, total_calls: 10, complete: false, basis: 'paid_standard_list_estimate' };
    expect(summarizeCosts(current, collectCallRows(current, [log()]))).toMatchObject({ estimated_usd: 0.5, total_calls: 10, source: 'server' });
  });

  it('preserves tiny nonzero prices and makes missing values explicit', () => {
    expect(formatUsd(null)).toBe('未知');
    expect(formatUsd(0)).toBe('$0.000000');
    expect(formatUsd(0.0000004)).toBe('< $0.000001');
  });
});
