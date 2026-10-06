// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FileUploader } from './FileUploader';
import { RunStatusPanel, workspaceTone } from './RunStatusPanel';
import type { Health, Run, RunIssue } from './types';

afterEach(() => { cleanup(); vi.useRealTimers(); });
const health: Health = { status: 'ok', api_key_configured: true, default_model: 'test',
  ffmpeg_available: true, ffprobe_available: true, worker_alive: true };
const transfer = (files: File[]) => ({ files, types: ['Files'], dropEffect: 'copy' });

describe('video upload interaction', () => {
  it('accepts a dragged video, shows the drop state and clears it after dropping', async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined);
    const onError = vi.fn();
    render(<FileUploader busy={false} uploading={false} onUpload={onUpload} onError={onError} />);
    const region = screen.getByRole('region', { name: '视频上传区域' });
    const file = new File(['demo'], 'sample.MOV', { type: 'video/quicktime' });
    fireEvent.dragEnter(region, { dataTransfer: transfer([file]) });
    expect(region.classList.contains('is-dragging')).toBe(true);
    expect(screen.getByText('松开以导入视频')).toBeTruthy();
    fireEvent.drop(region, { dataTransfer: transfer([file]) });
    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(file));
    expect(region.classList.contains('is-dragging')).toBe(false);
    expect(onError).not.toHaveBeenCalled();
  });
  it('rejects images and multiple files without uploading', async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined), onError = vi.fn();
    render(<FileUploader busy={false} uploading={false} onUpload={onUpload} onError={onError} />);
    const region = screen.getByRole('region', { name: '视频上传区域' });
    fireEvent.drop(region, { dataTransfer: transfer([new File(['image'], 'photo.jpg', { type: 'image/jpeg' })]) });
    expect(onError).toHaveBeenLastCalledWith(expect.stringContaining('请选择视频文件'));
    fireEvent.drop(region, { dataTransfer: transfer([new File(['1'], 'one.mp4'), new File(['2'], 'two.mp4')]) });
    expect(onError).toHaveBeenLastCalledWith(expect.stringContaining('只支持一个视频'));
    expect(onUpload).not.toHaveBeenCalled();
  });
  it('does not submit a second file while uploading', () => {
    const onUpload = vi.fn(), onError = vi.fn();
    render(<FileUploader busy uploading onUpload={onUpload} onError={onError} />);
    const region = screen.getByRole('region', { name: '视频上传区域' });
    fireEvent.drop(region, { dataTransfer: transfer([new File(['1'], 'one.mp4')]) });
    expect(onUpload).not.toHaveBeenCalled();
    expect(screen.getByRole('button').hasAttribute('disabled')).toBe(true);
    expect(region.getAttribute('aria-busy')).toBe('true');
  });
});

function run(status = 'running'): Run {
  return { id: 'run-test', media_id: 'media-test', query: '找到每次动作', config: { provider: 'fixture', request_timeout_s: 120 },
    status, stage: 'scanning', progress: { scan_total_windows: 2, model_calls: 2,
      coverage: { duration_us: 55_000_000, covered_us: 0, gaps: [[0, 55_000_000]] } },
    created_at: '2026-10-05T17:00:00Z', updated_at: '2026-10-05T17:02:01Z',
    tasks: [{ task_id: 'window_00000', stage: 'scan', status: 'submitting',
      request_intent: { created_at: '2026-10-05T17:00:00Z' } }] };
}

describe('run visibility and recovery', () => {
  it('separates processing time from queue time for input-mode comparisons', () => {
    const current = run('completed');
    current.started_at = '2026-10-05T17:00:20Z';
    render(<RunStatusPanel run={current} health={health} canceling={false} onCancel={vi.fn()} />);
    expect(screen.getByText(/总耗时\s*02:01/)).toBeTruthy();
    expect(screen.getByText(/处理\s*01:41/)).toBeTruthy();
    expect(screen.getByText(/排队\s*00:20/)).toBeTruthy();
  });
  it('counts in-flight request intents even before completed-window progress updates', () => {
    const current = run();
    current.config.provider = 'gemini';
    current.progress.model_calls = 1;
    current.tasks = ['query', 'scan-one', 'scan-two'].map(task_id => ({
      task_id, stage: task_id === 'query' ? 'query' : 'scan', status: 'submitting',
      request_intent: { operation: task_id === 'query' ? 'normalize_query' : 'propose' },
    }));
    render(<RunStatusPanel run={current} health={health} canceling={false} onCancel={vi.fn()} />);
    expect(screen.getByText(/模型请求\s*3\s*次/)).toBeTruthy();
  });
  it('has distinct idle, running and completed states with an actionable cancel control', () => {
    expect(workspaceTone(null)).toBe('idle');
    expect(workspaceTone(run())).toBe('running');
    expect(workspaceTone(run('completed'))).toBe('completed');
    const onCancel = vi.fn();
    render(<RunStatusPanel run={run()} health={health} canceling={false} onCancel={onCancel} />);
    const cancel = screen.getByRole('button', { name: '取消运行' });
    expect(cancel.classList.contains('button-danger')).toBe(true);
    expect(screen.getByRole('region', { name: '整体运行状态' }).querySelector('.status-orbit')).toBeTruthy();
    expect(screen.getByText('正在全片扫描')).toBeTruthy();
    fireEvent.click(cancel);
    expect(onCancel).toHaveBeenCalledOnce();
  });
  it('shows the historical zero-result error prominently instead of suggesting clips exist', () => {
    const partial = run('partial');
    const issue: RunIssue = { task_id: 'window_00000', stage: 'scan', code: 'request_unknown',
      title: '模型请求结果未知', message: '后续模型请求已暂停。', action: '检查请求记录。',
      details: { elapsed_s: 121, timeout_s: 120, possible_timeout: true }, artifact_refs: [], not_submitted: false };
    partial.diagnostics = { issues: [issue], stopped_stage: 'scan', unknown_requests: 1,
      blocked_tasks: 1, available_events: 0, available_clips: 0 };
    render(<RunStatusPanel run={partial} health={health} canceling={false} onCancel={vi.fn()} />);
    expect(screen.getByText('处理已停止，尚无可用事件')).toBeTruthy();
    expect(screen.getByText('模型请求结果未知')).toBeTruthy();
    expect(screen.getByText(/等待 121.0 秒/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: '取消运行' })).toBeNull();
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('0');
  });
  it('offers a direct link to saved records and marks provisional results', () => {
    const current = run();
    current.results = { run_id: current.id, provider: 'fixture', model_id: 'test',
      query_spec: { raw_query: 'all', event_kind: 'point', target_description: 'all',
        required_evidence: [], exclusions: [], occurrence_policy: 'all', defaults_used: [] },
      events: [{ event_id: 'event1', source_candidate_ids: ['candidate1'], decision: 'supported',
        boundary_status: 'bounded', result_bucket: 'uncertain', location: { kind: 'point', anchor_us: 3_000_000 },
        entity_key: 'demo', evidence_refs: ['frame1'], reason: 'fixture', uncertainty_reasons: [], clip_status: 'pending' }],
      scan_complete: false, coverage: { duration_us: 55_000_000, covered_us: 0, gaps: [] },
      stats: {}, limitations: [], provisional: true };
    render(<RunStatusPanel run={current} health={health} canceling={false} onCancel={vi.fn()} />);
    expect(screen.getByRole('link', { name: /查看已保存结果/ }).getAttribute('href')).toBe('#results-title');
    expect(screen.getByText(/尚未完成最终去重/)).toBeTruthy();
  });
});
