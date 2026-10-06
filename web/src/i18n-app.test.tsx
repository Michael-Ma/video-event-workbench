// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { FileUploader } from './FileUploader';
import { initializeLanguage, setLanguage } from './i18n';
import type { Health, Media, Run } from './types';

const health: Health = { status: 'ok', api_key_configured: true, default_model: 'gemini-test',
  ffmpeg_available: true, ffprobe_available: true, worker_alive: true };
const source: Media = { id: 'media-language', filename: '用户原始文件名.mp4', duration_us: 10_000_000, status: 'ready' };

beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
    clear: () => storage.clear(),
  });
  setLanguage('zh');
});
afterEach(() => { cleanup(); setLanguage('zh'); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

function makeRun(): Run {
  return { id: 'run-language', media_id: source.id, query: '原始用户查询，不应被语言切换重写',
    status: 'completed', stage: 'completed', progress: {},
    config: { provider: 'gemini', scan_input_mode: 'images', refine_input_mode: 'video', refine_fps: 6, request_timeout_s: 1200 },
    created_at: '2026-10-06T12:00:00Z', updated_at: '2026-10-06T12:01:00Z',
    cost_summary: { currency: 'USD', estimated_usd: 0.023, estimated_calls: 1, unknown_calls: 0, pending_calls: 0,
      local_calls: 0, total_calls: 1, complete: true, basis: 'paid_standard_list_estimate' },
    tasks: [{ task_id: 'scan-one', stage: 'scan', status: 'succeeded', window: { core_start_us: 0, core_end_us: 10_000_000 } }],
    results: { run_id: 'run-language', provider: 'gemini', model_id: 'gemini-test',
      query_spec: { raw_query: '原始用户查询', event_kind: 'point', target_description: '目标球体', required_evidence: [], exclusions: [], occurrence_policy: 'all', defaults_used: [] },
      coverage: { duration_us: 10_000_000, covered_us: 10_000_000, gaps: [] }, scan_complete: true, stats: {}, limitations: [],
      events: [
        { event_id: 'event-matched', source_candidate_ids: ['candidate-one'], decision: 'supported', boundary_status: 'bounded',
          result_bucket: 'matched', location: { kind: 'point', anchor_us: 2_000_000, anchor_range_us: [1_950_000, 2_050_000] }, entity_key: 'ball', evidence_refs: [], reason: '模型原文：确认接触', uncertainty_reasons: [], clip_status: 'not_required' },
        { event_id: 'event-uncertain', source_candidate_ids: ['candidate-two'], decision: 'unresolved', boundary_status: 'unknown',
          result_bucket: 'uncertain', location: { kind: 'point', anchor_range_us: [6_000_000, 6_500_000] }, entity_key: 'ball', evidence_refs: [], reason: '模型原文：接触被遮挡', uncertainty_reasons: ['模型原文：球体出画'], clip_status: 'not_required' },
      ] },
  };
}

function mockApi(run?: Run, demoError = false) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    let value: unknown;
    if (url === '/api/health') value = health;
    else if (url === '/api/media') value = [source];
    else if (url === '/api/runs') value = run ? [run] : [];
    else if (url === '/api/media/media-language') value = source;
    else if (url.startsWith('/api/runs/run-language/logs')) value = { items: [] };
    else if (url === '/api/runs/run-language') value = run;
    else if (url === '/api/demo' && init?.method === 'POST') {
      if (demoError) throw new Error('Controlled backend error');
      value = { ...source, id: 'fixture-language', filename: 'demo.mp4', is_demo: true };
    } else throw new Error(`Unexpected request in locale test: ${url}`);
    return new Response(JSON.stringify(value), { status: 200 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

describe('application language selection', () => {
  it('updates header, forms and upload controls immediately without changing draft values or creating a run', async () => {
    const fetchMock = mockApi();
    const view = render(<App />);
    await screen.findByLabelText('已导入的视频');
    fireEvent.change(screen.getByLabelText('已导入的视频'), { target: { value: source.id } });
    const query = screen.getByLabelText('自然语言查询');
    fireEvent.change(query, { target: { value: 'Keep this 用户 query exactly.' } });
    const scanMode = view.container.querySelector<HTMLSelectElement>('#scan-input-mode')!;
    fireEvent.change(scanMode, { target: { value: 'video' } });
    const timeout = screen.getByLabelText('模型请求超时 / 秒');
    fireEvent.change(timeout, { target: { value: '1500' } });
    const inputFile = screen.getByLabelText('选择要上传的视频文件');

    fireEvent.change(screen.getByLabelText('界面语言'), { target: { value: 'en' } });
    expect(screen.getByText('Video Event Workbench', { selector: 'strong' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Start locating events' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Load built-in fixture demo' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Drop a video or choose a file/ })).toBeTruthy();
    expect(screen.getByText('Fixture engineering test')).toBeTruthy();
    expect((screen.getByLabelText('Natural-language query') as HTMLTextAreaElement).value).toBe('Keep this 用户 query exactly.');
    expect((screen.getByLabelText('Imported videos') as HTMLSelectElement).value).toBe(source.id);
    expect(scanMode.value).toBe('video');
    expect((screen.getByLabelText('Model timeout / seconds') as HTMLInputElement).value).toBe('1500');
    expect(screen.getByLabelText('Choose a video file to upload')).toBe(inputFile);
    expect(document.documentElement.lang).toBe('en');
    expect(localStorage.getItem('video-workbench-language')).toBe('en');

    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh' } });
    expect(screen.getByRole('button', { name: '开始定位事件' })).toBeTruthy();
    expect((screen.getByLabelText('自然语言查询') as HTMLTextAreaElement).value).toBe('Keep this 用户 query exactly.');
    expect(document.documentElement.lang).toBe('zh-CN');
    expect(fetchMock.mock.calls.filter(([url, init]) => url === '/api/runs' && init?.method === 'POST')).toHaveLength(0);
  });

  it('generates templates in the selected language only when the user clicks a template', async () => {
    mockApi(); setLanguage('en'); render(<App />);
    await screen.findByLabelText('Imported videos');
    fireEvent.click(screen.getByRole('button', { name: 'Robot grasp' }));
    const englishQuery = (screen.getByLabelText('Natural-language query') as HTMLTextAreaElement).value;
    expect(englishQuery).toContain('Include failed attempts');
    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh' } });
    expect((screen.getByLabelText('自然语言查询') as HTMLTextAreaElement).value).toBe(englishQuery);
    fireEvent.click(screen.getByRole('button', { name: '小球触地' }));
    expect((screen.getByLabelText('自然语言查询') as HTMLTextAreaElement).value).toContain('连续反弹分别保留');
  });

  it('refreshes translated run wrappers while preserving filters, selection, raw model evidence and cost', async () => {
    const run = makeRun();
    localStorage.setItem('video-workbench-run', run.id);
    const fetchMock = mockApi(run);
    render(<App />);
    await screen.findByRole('heading', { name: /事件结果/ });
    fireEvent.click(screen.getByRole('button', { name: /^不确定/ }));
    const oldRunReads = fetchMock.mock.calls.filter(([url]) => url === '/api/runs/run-language').length;
    fireEvent.change(screen.getByLabelText('界面语言'), { target: { value: 'en' } });
    expect(screen.getByRole('heading', { name: /Event results/ })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Full-video scan coverage' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /^Uncertain/ }).getAttribute('aria-pressed')).toBe('true');
    expect(within(screen.getByLabelText('Event list')).getAllByRole('button')).toHaveLength(1);
    expect(screen.getByText('Event provenance and evidence')).toBeTruthy();
    expect(screen.getAllByText('模型原文：接触被遮挡')).toHaveLength(2);
    expect(screen.getByText('模型原文：球体出画')).toBeTruthy();
    expect(screen.getByText('event-uncertain')).toBeTruthy();
    expect(screen.getAllByText(run.query).length).toBeGreaterThan(0);
    expect(screen.getByText('$0.023000')).toBeTruthy();
    await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === '/api/runs/run-language').length).toBeGreaterThan(oldRunReads));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
  });

  it('relocalizes an existing product error prefix without replacing its original cause', async () => {
    mockApi(undefined, true); setLanguage('en'); render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Load built-in fixture demo' }));
    expect(await screen.findByText('Fixture demo generation failed: Controlled backend error')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh' } });
    expect(screen.getByText('测试视频生成失败：Controlled backend error')).toBeTruthy();
  });

  it('renders uploader validation in the current locale and keeps it translatable', async () => {
    const fetchMock = mockApi(); setLanguage('en'); render(<App />);
    await screen.findByLabelText('Imported videos');
    fireEvent.drop(screen.getByRole('region', { name: 'Video upload area' }), {
      dataTransfer: { files: [new File(['not a video'], 'photo.jpg', { type: 'image/jpeg' })], types: ['Files'] },
    });
    expect(screen.getByText('Choose a video file such as MP4, MOV, MKV, WebM, or AVI.')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'zh' } });
    expect(screen.getByText('请选择视频文件，例如 MP4、MOV、MKV、WebM 或 AVI。')).toBeTruthy();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(0);
  });

  it('uses the persisted language at application initialization', () => {
    localStorage.setItem('video-workbench-language', 'en');
    initializeLanguage();
    expect(document.documentElement.lang).toBe('en');
    expect(document.title).toBe('Video Event Workbench');
    localStorage.setItem('video-workbench-language', 'zh');
    initializeLanguage();
    expect(document.documentElement.lang).toBe('zh-CN');
  });
});

describe('standalone upload language', () => {
  it('updates active drag labels without remounting the file input', () => {
    const onUpload = vi.fn().mockResolvedValue(undefined);
    render(<FileUploader busy={false} uploading={false} onUpload={onUpload} onError={vi.fn()} />);
    const input = screen.getByLabelText('选择要上传的视频文件');
    const area = screen.getByRole('region', { name: '视频上传区域' });
    fireEvent.dragEnter(area, { dataTransfer: { types: ['Files'] } });
    expect(screen.getByText('松开以导入视频')).toBeTruthy();
    act(() => setLanguage('en'));
    expect(screen.getByRole('region', { name: 'Video upload area' })).toBe(area);
    expect(screen.getByText('Drop to import video')).toBeTruthy();
    expect(screen.getByLabelText('Choose a video file to upload')).toBe(input);
    expect(onUpload).not.toHaveBeenCalled();
  });
});
