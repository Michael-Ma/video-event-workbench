import type { EventLocation, Task, TimeRange } from './types';

export const terminalStatuses = new Set(['completed', 'partial', 'failed', 'cancelled']);
export const statusNames: Record<string, string> = {
  queued: '排队中', pending: '待处理', running: '处理中', completed: '处理完成',
  partial: '部分完成', failed: '处理失败', cancelled: '已取消', succeeded: '已完成',
  interrupted: '未执行', skipped: '已跳过', request_unknown: '请求结果未知', submitting: '等待模型响应',
};
export const stageNames: Record<string, string> = {
  queued: '等待执行', preparing: '准备视频', prepare: '准备视频', prepare_media: '准备视频',
  query: '解析查询', parse_query: '解析查询', normalizing_query: '解析查询', planning: '规划窗口', plan: '规划窗口',
  scan: '全片扫描', scanning: '全片扫描', group: '整理候选', grouping: '整理候选',
  refine: '核实与精定位', refining: '核实与精定位', reconcile: '整理事件', reconciling: '整理事件',
  export: '截取片段', exporting: '截取片段', clip: '截取片段', clipping: '截取片段',
  publish: '发布结果', completed: '已结束', done: '已结束', cancelled: '已取消', failed: '失败', stopped: '已停止', recovering: '恢复任务',
};

export function formatTime(us: number | null | undefined, precise = false): string {
  if (us == null || !Number.isFinite(us)) return '未知';
  const millis = Math.max(0, Math.round(us / 1000));
  const seconds = Math.floor(millis / 1000);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const base = `${h ? `${h.toString().padStart(2, '0')}:` : ''}${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  return precise ? `${base}.${(millis % 1000).toString().padStart(3, '0')}` : base;
}

export function rangeLabel(range?: TimeRange | null, precise = true): string {
  return range ? `${formatTime(range[0], precise)} – ${formatTime(range[1], precise)}` : '未知';
}

export function locationLabel(location: EventLocation): string {
  if (location.kind === 'point') return formatTime(location.anchor_us, true);
  return `${formatTime(location.start_us, true)} – ${formatTime(location.end_us, true)}`;
}

export function eventStart(location: EventLocation): number | undefined {
  return location.anchor_us ?? location.anchor_range_us?.[0] ?? location.start_us ?? location.start_range_us?.[0] ?? undefined;
}

export function readRange(value: unknown): TimeRange | null {
  if (Array.isArray(value) && value.length === 2 && value.every(v => typeof v === 'number' && Number.isFinite(v))) return value as TimeRange;
  if (value && typeof value === 'object') {
    const o = value as Record<string, unknown>;
    const start = o.start_us ?? o.core_start_us;
    const end = o.end_us ?? o.core_end_us;
    if (typeof start === 'number' && typeof end === 'number' && Number.isFinite(start) && Number.isFinite(end)) return [start, end];
  }
  return null;
}

export function taskRange(task: Task): TimeRange | null {
  return readRange(task.window) ?? readRange(task.core_range_us) ?? readRange(task);
}

export function artifactUrl(path: string): string | null {
  if (path.startsWith('/api/artifacts/')) return path;
  if (!path || path.startsWith('/') || path.includes('://') || path.split('/').some(p => p === '..')) return null;
  return `/api/artifacts/${path.split('/').map(encodeURIComponent).join('/')}`;
}

export function artifactRefs(value: unknown): string[] {
  const refs = new Set<string>();
  function visit(v: unknown, key = '') {
    if (typeof v === 'string' && /(?:artifact_refs?|(?:request|response|error|manifest|result|plan|debug)_path|^path$|^url$)/i.test(key) && artifactUrl(v)) refs.add(v);
    else if (Array.isArray(v)) v.forEach(item => visit(item, key));
    else if (v && typeof v === 'object') Object.entries(v).forEach(([k, item]) => visit(item, k));
  }
  visit(value);
  return [...refs];
}

export function errorText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') {
    const o = value as Record<string, unknown>;
    if (typeof o.message === 'string') return o.message;
    if (o.error) return errorText(o.error);
    if (o.detail) return errorText(o.detail);
  }
  return value == null ? '未知错误' : JSON.stringify(value);
}
