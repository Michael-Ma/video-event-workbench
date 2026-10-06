// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CostPanel } from './CostPanel';
import { FrozenRunConfig, frozenScanFps, InputModeControls } from './InputModes';
import { RunStatusPanel } from './RunStatusPanel';
import { collectCallRows, formatTokens, formatUsd, summarizeCosts } from './costs';
import { setLanguage } from './i18n';
import type { CallCost, EventResult, Health, Run, Task } from './types';

afterEach(() => { cleanup(); setLanguage('zh'); vi.restoreAllMocks(); vi.useRealTimers(); });

const health: Health = { status: 'ok', api_key_configured: true, default_model: 'test-model',
  ffmpeg_available: true, ffprobe_available: true, worker_alive: true };
const estimated: CallCost = { status: 'estimated', estimated_usd: 0.012345, provider: 'gemini',
  pricing_version: 'price-snapshot-1', model_id: 'test-model', pricing_source: 'https://example.test/pricing',
  effective_from: '2026-09-01', submitted_on: '2026-10-06',
  rates_per_million_tokens: { input: 1.5, cached_input: 0.15, output_including_thinking: 7.5 },
  token_counts: { prompt_token_count: 1234, cached_content_token_count: 234,
    candidates_token_count: 56, thoughts_token_count: 78 } };

function makeRun(status = 'completed', tasks: Task[] = []): Run {
  return { id: 'run-localization', media_id: 'media-localization', query: 'Find every contact',
    status, stage: status === 'completed' ? 'completed' : 'scanning', progress: {}, tasks,
    config: { provider: 'gemini', model_id: 'test-model', scan_input_mode: 'images',
      refine_input_mode: 'video', profile: 'action', refine_fps: 6, request_timeout_s: 1200,
      model_concurrency: 2, clip_concurrency: 2, thinking_level: 'low', max_output_tokens: 16384, temperature: 1 },
    created_at: '2026-10-06T17:00:00Z', started_at: '2026-10-06T17:00:10Z', updated_at: '2026-10-06T17:01:10Z' };
}

function makeTask(id: string, fields: Partial<Task> = {}): Task {
  return { task_id: id, attempt_id: `attempt-${id}`, stage: 'scan', status: 'succeeded',
    request_intent: { operation: 'propose', created_at: '2026-10-06T17:00:10Z' },
    request_metrics: { elapsed_s: 3.25 }, ...fields };
}

function withResults(run: Run, provisional = false): Run {
  const events: EventResult[] = [0, 1].map(i => ({ event_id: `event-${i}`, source_candidate_ids: [],
    decision: 'supported', boundary_status: 'bounded', result_bucket: 'matched',
    location: { kind: 'point', anchor_us: (i + 1) * 1000000 }, entity_key: 'ball', evidence_refs: [],
    reason: 'Raw model reason', uncertainty_reasons: [], clip_status: i ? 'pending' : 'succeeded' }));
  return { ...run, results: { run_id: run.id, provider: 'gemini', model_id: 'test-model',
    query_spec: { raw_query: run.query, event_kind: 'point', target_description: 'ball contacts',
      required_evidence: [], exclusions: [], occurrence_policy: 'all', defaults_used: [] },
    events, scan_complete: !provisional, provisional,
    coverage: { duration_us: 10000000, covered_us: 5000000, gaps: [] }, stats: {}, limitations: [] } };
}

const noFixedChinese = (node: HTMLElement) => expect(node.textContent).not.toMatch(/[\u3400-\u9fff]/);

describe('input and frozen configuration localization', () => {
  it('switches mounted controls and aria labels without changing mode values', () => {
    const onScanChange = vi.fn();
    const view = render(<InputModeControls scanMode="images" refineMode="video" profile="auto"
      onScanChange={onScanChange} onRefineChange={vi.fn()} />);
    expect(screen.getByLabelText('scan / propose 输入')).toBeTruthy();
    act(() => setLanguage('en'));
    const scan = screen.getByRole('combobox', { name: 'scan / propose input' }) as HTMLSelectElement;
    expect(scan.value).toBe('images');
    expect((screen.getByRole('combobox', { name: 'refine / verify_refine input' }) as HTMLSelectElement).value).toBe('video');
    expect(screen.getByText(/Default scan: auto selects action 2 FPS or point 6 FPS/)).toBeTruthy();
    expect(screen.getByText(/Video input has no audio/)).toBeTruthy();
    noFixedChinese(view.container);
    fireEvent.change(scan, { target: { value: 'video' } });
    expect(onScanChange).toHaveBeenCalledWith('video');
    act(() => setLanguage('zh'));
    expect(screen.getByLabelText('refine / verify_refine 输入')).toBeTruthy();
  });

  it('translates frozen values and keeps missing historical settings missing', () => {
    setLanguage('en');
    const current = makeRun();
    current.config.scan_fps = 3;
    const view = render(<FrozenRunConfig run={current} />);
    const region = screen.getByRole('region', { name: 'Frozen configuration for this run' });
    expect(within(region).getByText('3 FPS · override')).toBeTruthy();
    expect(within(region).getByText('1200 s')).toBeTruthy();
    expect(within(region).getByText('Model concurrency')).toBeTruthy();
    noFixedChinese(region);
    const historical = makeRun(); historical.config = { provider: 'gemini', profile: 'action' };
    view.rerender(<FrozenRunConfig run={historical} />);
    expect(screen.getAllByText('Not recorded').length).toBeGreaterThan(2);
    expect(screen.getByText('FPS not recorded')).toBeTruthy();
    expect(screen.queryByText('2 FPS · action default')).toBeNull();
    act(() => setLanguage('zh'));
    expect(screen.getByRole('region', { name: '本次运行的冻结配置' })).toBeTruthy();
    expect(screen.getAllByText('未记录').length).toBeGreaterThan(2);
  });

  it('formats observed and preset FPS on demand in the selected locale', () => {
    const run = makeRun('completed', [makeTask('one', { window: { sample_fps: 2 } }),
      makeTask('two', { sampling: { requested_fps: 4 } })]);
    expect(frozenScanFps(run)).toBe('2 / 4 FPS · 已规划');
    setLanguage('en');
    expect(frozenScanFps(run)).toBe('2 / 4 FPS · planned');
    run.tasks = [];
    expect(frozenScanFps(run)).toBe('2 FPS · action default');
    run.config.profile = 'auto';
    expect(frozenScanFps(run)).toBe('Awaiting QuerySpec');
  });
});

describe('cost localization preserves accounting', () => {
  it('renders known, unknown, pending, historical and local costs without treating unknown as zero', () => {
    setLanguage('en');
    const run = makeRun('cancelled', [
      makeTask('paid', { cost: estimated }),
      makeTask('unknown', { status: 'request_unknown', cost: { status: 'unknown', estimated_usd: null, reason: 'request_outcome_unknown' } }),
      makeTask('pending', { status: 'submitting' }),
      makeTask('historical'),
      makeTask('fixture', { cost: { status: 'not_billable', estimated_usd: 0, provider: 'fixture', basis: 'local_fixture_no_remote_call' } }),
    ]);
    const beforeRows = collectCallRows(run, []), beforeSummary = summarizeCosts(run, beforeRows);
    const view = render(<CostPanel run={run} logs={[]} />);
    expect(screen.getByRole('region', { name: 'Model call costs' })).toBeTruthy();
    expect(screen.getByText('Known estimated subtotal')).toBeTruthy();
    expect(screen.getAllByText('$0.012345')).toHaveLength(2);
    expect(screen.getByText('$0.000000')).toBeTruthy();
    expect(screen.getByText('Cost unknown')).toBeTruthy();
    expect(screen.getByText('Cost not recorded')).toBeTruthy();
    expect(screen.getByText('Awaiting usage')).toBeTruthy();
    expect(screen.getByText(/They are not counted as \$0/)).toBeTruthy();
    expect(screen.getByText(/Cancelling a run does not erase/)).toBeTruthy();
    expect(screen.getByText('The request outcome is unknown; charges may have been incurred.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'View the recorded official pricing source ↗' }).getAttribute('href')).toBe('https://example.test/pricing');
    expect(screen.getByText('price-snapshot-1')).toBeTruthy();
    noFixedChinese(view.container);
    act(() => setLanguage('zh'));
    expect(screen.getByRole('region', { name: '模型调用费用' })).toBeTruthy();
    expect(screen.getByText('已知估算小计')).toBeTruthy();
    expect(screen.getByText(/不计为 \$0/)).toBeTruthy();
    expect(collectCallRows(run, [])).toEqual(beforeRows);
    expect(summarizeCosts(run, collectCallRows(run, []))).toEqual(beforeSummary);
  });

  it('translates idle and missing history explanations', () => {
    setLanguage('en');
    const view = render(<CostPanel run={null} logs={[]} />);
    expect(screen.getByText('Not started')).toBeTruthy();
    expect(screen.getByText(/Planned tasks that have not been submitted/)).toBeTruthy();
    view.rerender(<CostPanel run={makeRun()} logs={[]} />);
    expect(screen.getByText('Not recorded')).toBeTruthy();
    expect(screen.getByText(/This does not mean the cost was zero/)).toBeTruthy();
    noFixedChinese(view.container);
  });

  it('formats unknown amounts and token numbers using current locale without altering dollar precision', () => {
    const spy = vi.spyOn(Number.prototype, 'toLocaleString');
    expect(formatUsd(null)).toBe('未知');
    formatTokens(1234);
    expect(spy).toHaveBeenLastCalledWith('zh-CN');
    setLanguage('en');
    expect(formatUsd(null)).toBe('Unknown');
    expect(formatUsd(Number.NaN)).toBe('Unknown');
    expect(formatUsd(-1)).toBe('Unknown');
    expect(formatUsd(0)).toBe('$0.000000');
    expect(formatUsd(0.0000004)).toBe('< $0.000001');
    expect(formatUsd(0.012345)).toBe('$0.012345');
    expect(formatTokens(1234)).toBe('1,234');
    expect(spy).toHaveBeenLastCalledWith('en-US');
    expect(formatTokens(null)).toBe('—');
  });
});

describe('run status localization', () => {
  it('updates a mounted running panel, wait text, cancel control and stage labels', () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-06T17:00:20Z'));
    const run = withResults(makeRun('running', [makeTask('scan', { status: 'submitting' })]), true);
    const view = render(<RunStatusPanel run={run} health={health} canceling={false} onCancel={vi.fn()} />);
    expect(screen.getByText('正在全片扫描')).toBeTruthy();
    act(() => setLanguage('en'));
    expect(screen.getByRole('region', { name: 'Overall run status' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Cancel run' })).toBeTruthy();
    expect(screen.getByText('Waiting for model response: 10 s / limit 1200 s')).toBeTruthy();
    expect(screen.getByText('Model requests: 1')).toBeTruthy();
    expect(screen.getByRole('list', { name: 'Processing stages' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'View saved results (2) ↓' })).toBeTruthy();
    expect(screen.getByRole('progressbar', { name: 'Source video scan coverage' }).getAttribute('aria-valuenow')).toBe('50');
    expect(screen.getByText(/Final deduplication and ordering filters are not complete/)).toBeTruthy();
    noFixedChinese(view.container);
    view.rerender(<RunStatusPanel run={run} health={health} canceling onCancel={vi.fn()} />);
    expect((screen.getByRole('button', { name: 'Cancelling…' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it.each([
    ['queued', 'Run queued'], ['completed', 'Completed'],
    ['partial', 'Partially completed; existing results are saved'],
    ['failed', 'Failed'], ['cancelled', 'Run cancelled'],
  ])('renders %s status with translated counts and labels', (status, title) => {
    setLanguage('en');
    const run = withResults(makeRun(status));
    const view = render(<RunStatusPanel run={run} health={{ ...health, worker_alive: false }} canceling={false} onCancel={vi.fn()} />);
    expect(screen.getByRole('heading', { name: title })).toBeTruthy();
    expect(screen.getByText('Event records')).toBeTruthy();
    expect(screen.getByText('Clip ready')).toBeTruthy();
    if (status === 'completed') expect(screen.getByText(/Saved event records: 2; clips: 1/)).toBeTruthy();
    if (status === 'cancelled') expect(screen.getByText(/Retained event records: 2; clips: 1/)).toBeTruthy();
    noFixedChinese(view.container);
  });

  it('translates diagnostic chrome while preserving backend text, technical details and user query', () => {
    setLanguage('en');
    const run = makeRun('partial');
    run.query = '用户原始查询不翻译';
    run.diagnostics = { stopped_stage: 'scan', unknown_requests: 1, blocked_tasks: 0, available_events: 0, available_clips: 0,
      issues: [{ task_id: 'scan', stage: 'scan', code: 'request_unknown', title: 'Backend diagnostic title',
        message: 'Backend diagnostic message', action: 'Backend diagnostic action', technical_message: 'RAW SDK: 原始错误',
        details: { elapsed_s: 1201, timeout_s: 1200, exception_type: 'TimeoutError', possible_timeout: true },
        range_us: [2000000, 4000000], artifact_refs: [], not_submitted: false }] };
    render(<RunStatusPanel run={run} health={health} canceling={false} onCancel={vi.fn()} />);
    expect(screen.getByText('Backend diagnostic title')).toBeTruthy();
    expect(screen.getByText('Backend diagnostic message')).toBeTruthy();
    expect(screen.getByText('Backend diagnostic action')).toBeTruthy();
    expect(screen.getByText('RAW SDK: 原始错误')).toBeTruthy();
    expect(screen.getByText('用户原始查询不翻译')).toBeTruthy();
    expect(screen.getByText(/Waited 1201.0 s; timeout limit: 1200 s/)).toBeTruthy();
    expect(screen.getByText(/The historical record has no exception type/)).toBeTruthy();
    expect(screen.getByText('Why processing did not finish')).toBeTruthy();
    expect(screen.getByText(/Diagnostic details/)).toBeTruthy();
    expect(screen.getByText(/00:02 – 00:04/)).toBeTruthy();
  });
});
