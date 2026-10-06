// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { CostPanel } from './CostPanel';
import { FrozenRunConfig } from './InputModes';
import type { CallCost, Health, Media, Run, Task } from './types';

const health: Health = { status: 'ok', api_key_configured: true, default_model: 'gemini-test', ffmpeg_available: true, ffprobe_available: true, worker_alive: true };
const media: Media = { id: 'media-test', filename: 'source.mp4', duration_us: 10_000_000, status: 'ready' };
const estimated: CallCost = { status: 'estimated', estimated_usd: 0.012345, provider: 'gemini', pricing_version: 'test-price-1',
  pricing_source: 'https://example.test/pricing', token_counts: { prompt_token_count: 1000, cached_content_token_count: 200, candidates_token_count: 30, thoughts_token_count: 70 } };

const createRun = (tasks: Task[] = []): Run => ({ id: 'run-test', media_id: media.id, query: '找到每次触地',
  config: { provider: 'gemini', scan_input_mode: 'images', refine_input_mode: 'images', refine_fps: 6, request_timeout_s: 1200 },
  status: 'completed', stage: 'completed', progress: {}, tasks,
  created_at: '2026-10-05T17:00:00Z', updated_at: '2026-10-05T17:01:00Z' });

beforeEach(() => {
  const entries = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => entries.get(key) ?? null,
    setItem: (key: string, value: string) => entries.set(key, value),
    removeItem: (key: string) => entries.delete(key),
    clear: () => entries.clear(),
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

function mockApi() {
  let saved: Run = createRun();
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    let data: unknown;
    if (url === '/api/health') data = health;
    else if (url === '/api/media') data = [media];
    else if (url === '/api/runs' && init?.method === 'POST') {
      const body = JSON.parse(String(init.body));
      saved = { ...createRun(), query: body.query, config: body.config }; data = saved;
    } else if (url === '/api/runs') data = [];
    else if (url.startsWith('/api/runs/run-test/logs')) data = { items: [] };
    else if (url === '/api/runs/run-test') data = saved;
    else if (url === '/api/media/media-test') data = media;
    else throw new Error(`Unexpected test request: ${url}`);
    return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function fillRun() {
  await screen.findByLabelText('已导入的视频');
  fireEvent.change(screen.getByLabelText('已导入的视频'), { target: { value: media.id } });
  fireEvent.change(screen.getByLabelText('自然语言查询'), { target: { value: '找到每次触地' } });
}

describe('per-stage input modes and run configuration', () => {
  it('submits images/images with 6 FPS refine, 1200s timeout and 2/2 concurrency by default', async () => {
    const fetchMock = mockApi(); render(<App />); await fillRun();
    fireEvent.click(screen.getByRole('button', { name: '开始定位事件' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => url === '/api/runs' && init?.method === 'POST')).toBe(true));
    const request = fetchMock.mock.calls.find(([url, init]) => url === '/api/runs' && init?.method === 'POST')!;
    expect(JSON.parse(String(request[1]!.body)).config).toMatchObject({ scan_input_mode: 'images', refine_input_mode: 'images', refine_fps: 6,
      request_timeout_s: 1200, model_concurrency: 2, clip_concurrency: 2 });
    expect(JSON.parse(String(request[1]!.body)).config).not.toHaveProperty('scan_fps');
  });

  it('keeps scan/refine selections independent and sends overrides to POST config', async () => {
    const fetchMock = mockApi(); render(<App />); await fillRun();
    fireEvent.change(screen.getByLabelText('scan / propose 输入'), { target: { value: 'video' } });
    expect(screen.getByLabelText('scan FPS · 可覆盖预设').getAttribute('max')).toBe('24');
    expect((screen.getByLabelText('refine / verify_refine 输入') as HTMLSelectElement).value).toBe('images');
    fireEvent.change(screen.getByLabelText('refine / verify_refine 输入'), { target: { value: 'video' } });
    expect(screen.getByLabelText('refine FPS').getAttribute('max')).toBe('24');
    fireEvent.change(screen.getByLabelText('scan / propose 输入'), { target: { value: 'images' } });
    expect(screen.getByLabelText('scan FPS · 可覆盖预设').getAttribute('max')).toBe('30');
    fireEvent.change(screen.getByLabelText('scan FPS · 可覆盖预设'), { target: { value: '3' } });
    fireEvent.change(screen.getByLabelText('model 并行数'), { target: { value: '4' } });
    fireEvent.change(screen.getByLabelText('clip 并行数'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: '开始定位事件' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => url === '/api/runs' && init?.method === 'POST')).toBe(true));
    const request = fetchMock.mock.calls.find(([url, init]) => url === '/api/runs' && init?.method === 'POST')!;
    expect(JSON.parse(String(request[1]!.body)).config).toMatchObject({ scan_input_mode: 'images', refine_input_mode: 'video', scan_fps: 3, model_concurrency: 4, clip_concurrency: 3,
      thinking_level: 'low', max_output_tokens: 16384, temperature: 1 });
    const frozen = await screen.findByRole('region', { name: '本次运行的冻结配置' });
    expect(await screen.findByRole('button', { name: /images → video/ })).toBeTruthy();
    fireEvent.change(screen.getByLabelText('refine / verify_refine 输入'), { target: { value: 'images' } });
    expect(within(frozen).getByText('video')).toBeTruthy();
  });

  it('does not apply new input/FPS defaults to old run records', () => {
    const current = createRun(); current.config = { profile: 'action', provider: 'gemini', refine_fps: 12, request_timeout_s: 120 };
    render(<FrozenRunConfig run={current} />);
    expect(screen.getAllByText('未记录').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('12 FPS')).toBeTruthy();
    expect(screen.queryByText('2 FPS · action 默认')).toBeNull();
  });

  it('continues polling a cancelled run while an in-flight cost is unresolved', async () => {
    vi.useFakeTimers();
    localStorage.setItem('video-workbench-run', 'run-test');
    const current = createRun([{ task_id: 'scan-1', stage: 'scan', status: 'submitting', attempt_id: 'late', request_intent: { operation: 'propose' } }]);
    current.status = 'cancelled';
    current.cost_summary = { currency: 'USD', estimated_usd: null, estimated_calls: 0, unknown_calls: 0, pending_calls: 1,
      local_calls: 0, total_calls: 1, complete: false, basis: 'paid_standard_list_estimate' };
    let runReads = 0;
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      let data: unknown;
      if (url === '/api/health') data = health;
      else if (url === '/api/media') data = [media];
      else if (url === '/api/runs') data = [current];
      else if (url.endsWith('/logs?after=0&limit=200')) data = { items: [] };
      else if (url === '/api/media/media-test') data = media;
      else if (url === '/api/runs/run-test') {
        runReads += 1;
        if (runReads >= 4) {
          current.tasks![0].status = 'cancelled'; current.tasks![0].cost = estimated;
          current.cost_summary = { ...current.cost_summary!, estimated_usd: 0.012345, estimated_calls: 1, pending_calls: 0, complete: true };
        }
        data = current;
      } else throw new Error(`Unexpected test URL: ${url}`);
      return new Response(JSON.stringify(data), { status: 200 });
    }));
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getAllByText('等待用量').length).toBeGreaterThan(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(3700); });
    expect(runReads).toBeGreaterThanOrEqual(4);
    expect(screen.getAllByText('$0.012345')).toHaveLength(2);
  });
});

describe('cost visibility', () => {
  it('never presents an unstarted run as a free call', () => {
    render(<CostPanel run={null} logs={[]} />);
    expect(screen.getByText('尚未开始')).toBeTruthy();
    expect(screen.queryByText('$0.000000')).toBeNull();
  });

  it('does not infer that a completed historical run made no calls when cost records are absent', () => {
    render(<CostPanel run={createRun()} logs={[]} />);
    expect(screen.getByText('未记录')).toBeTruthy();
    expect(screen.getByText(/不能推断为零费用/)).toBeTruthy();
    expect(screen.queryByText('尚未开始调用')).toBeNull();
  });

  it('shows known subtotal, unknown count and a price snapshot without hiding cancelled usage', () => {
    const current = createRun([
      { task_id: 'scan-1', stage: 'scan', status: 'cancelled', attempt_id: 'paid', request_intent: { operation: 'propose' }, cost: estimated, request_metrics: { elapsed_s: 6.25 }, input_mode: 'video' },
      { task_id: 'refine-1', stage: 'refine', status: 'request_unknown', attempt_id: 'unknown', request_intent: { operation: 'verify_refine' }, cost: { status: 'unknown', estimated_usd: null } },
    ]); current.status = 'cancelled';
    render(<CostPanel run={current} logs={[]} />);
    expect(screen.getAllByText('$0.012345')).toHaveLength(2);
    expect(screen.getByText(/另有 1 次费用未知/)).toBeTruthy();
    expect(screen.getByText('6.25 s')).toBeTruthy();
    expect(screen.getByText('1,000')).toBeTruthy();
    expect(screen.getByText('test-price-1')).toBeTruthy();
    expect(screen.getByRole('link', { name: /官方价格来源/ }).getAttribute('href')).toBe('https://example.test/pricing');
    expect(screen.getByText(/取消运行不会清除/)).toBeTruthy();
  });

  it('shows old records as unrecorded and explicit Fixture calls as zero', () => {
    const current = createRun([{ task_id: 'old', stage: 'scan', status: 'succeeded', request_intent: { operation: 'propose' } }]);
    const view = render(<CostPanel run={current} logs={[]} />);
    expect(screen.getByText('费用未记录')).toBeTruthy();
    expect(screen.queryByText('$0.000000')).toBeNull();
    current.config.provider = 'fixture';
    current.tasks![0].cost = { status: 'not_billable', estimated_usd: 0, provider: 'fixture' };
    view.rerender(<CostPanel run={current} logs={[]} />);
    expect(screen.getAllByText('$0.000000')).toHaveLength(2);
    expect(screen.getByText('Fixture · 本地免费')).toBeTruthy();
  });

  it('preserves all cost fields and expandable provenance at a mobile viewport', () => {
    vi.stubGlobal('innerWidth', 390);
    const current = createRun([{ task_id: 'scan-1', stage: 'scan', status: 'succeeded', request_intent: { operation: 'propose' }, cost: estimated }]);
    render(<CostPanel run={current} logs={[]} />);
    const region = screen.getByRole('region', { name: /每次模型调用费用明细/ });
    expect(within(region).getByRole('table')).toBeTruthy();
    for (const label of ['input', 'cached', 'output', 'thinking']) expect(within(region).getByText(label)).toBeTruthy();
    const summary = screen.getByText('价格来源与版本');
    fireEvent.click(summary);
    expect(summary.closest('details')?.open).toBe(true);
    expect(screen.getByText('test-price-1')).toBeTruthy();
  });
});
