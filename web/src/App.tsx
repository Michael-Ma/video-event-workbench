import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { api, jsonRequest } from './api';
import type { Coverage, EventResult, Health, LogEntry, Media, Profile, Provider, ResultBucket, Run, Task } from './types';
import { artifactRefs, artifactUrl, errorText, eventStart, formatTime, locationLabel, rangeLabel, readRange, stageNames, statusNames, taskRange, terminalStatuses } from './utils';

const bucketNames: Record<ResultBucket, string> = { matched: '匹配', uncertain: '不确定', rejected: '已排除' };
const defaultQuery = '找出小球每次与地面接触的时刻。连续反弹分别保留，持续贴地或滚动不重复计数。';
const templates = [
  { title: '小球触地', profile: 'point' as Profile, query: defaultQuery },
  { title: '机械臂抓取', profile: 'action' as Profile, query: '找出机械臂每次抓取物体的完整尝试，从针对目标的最后接近动作开始，到抓持结果可判断或尝试终止。保留失败尝试，排除等待和空闲移动。' },
];

function Icon({ name, size = 18 }: { name: 'film' | 'upload' | 'play' | 'download' | 'arrow' | 'check' | 'alert' | 'code' | 'stop'; size?: number }) {
  const paths: Record<string, ReactNode> = {
    film: <><rect x="3" y="3" width="18" height="18" rx="3" /><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4" /></>,
    upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5" /></>,
    play: <path d="m8 4 12 8-12 8z" />,
    download: <><path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4" /></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    alert: <><path d="m12 3 10 18H2zM12 9v5" /><path d="M12 17h.01" /></>,
    code: <><path d="m7 7-5 5 5 5m10-10 5 5-5 5m-4-14-2 18" /></>,
    stop: <rect x="5" y="5" width="14" height="14" rx="2" />,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function Badge({ status, children }: { status: string; children?: ReactNode }) {
  return <span className={`badge badge-${status}`}>{children ?? statusNames[status] ?? status}</span>;
}

function Artifacts({ value }: { value: unknown }) {
  const refs = artifactRefs(value);
  return refs.length > 0 ? <div className="artifact-links">{refs.map(path => <a key={path} href={artifactUrl(path)!} target="_blank" rel="noreferrer"><Icon name="code" size={13} />{path.split('/').pop()}</a>)}</div> : null;
}

function CoveragePanel({ run, media }: { run: Run; media?: Media }) {
  const tasks = (run.tasks ?? []).filter(task => task.stage === 'scan');
  const coverage = run.results?.coverage ?? run.progress.coverage as Coverage | undefined;
  const duration = coverage?.duration_us ?? media?.duration_us ?? 0;
  const gaps = (coverage?.gaps ?? []).map(readRange).filter((v): v is [number, number] => v !== null);
  const complete = run.results?.scan_complete === true;
  const successful = tasks.filter(task => task.status === 'succeeded').length;
  const rangeTasks = tasks.filter(task => taskRange(task) !== null);
  return <section className="coverage-panel" aria-labelledby="coverage-title">
    <div className="section-line"><h3 id="coverage-title">全片扫描覆盖</h3><span className="muted small">{coverage ? `${formatTime(coverage.covered_us)} / ${formatTime(duration)}` : `${successful} / ${tasks.length} 个扫描任务完成`}</span></div>
    <div className="timeline" role="img" aria-label={complete ? '所有负责区间已完成扫描；这不等于找全全部事件。' : `扫描覆盖，${gaps.length} 段已知缺口。具体窗口状态见下方。`}>
      {duration > 0 && rangeTasks.map(task => {
        const range = taskRange(task)!;
        const left = Math.max(0, Math.min(100, range[0] / duration * 100));
        const width = Math.max(0.2, Math.min(100 - left, (range[1] - range[0]) / duration * 100));
        return <span key={task.task_id} className={`timeline-segment state-${task.status}`} style={{ left: `${left}%`, width: `${width}%` }} title={`${rangeLabel(range)} · ${statusNames[task.status] ?? task.status}`} />;
      })}
      {duration > 0 && gaps.map((range, i) => <span key={`gap-${i}`} className="timeline-gap" style={{ left: `${range[0] / duration * 100}%`, width: `${(range[1] - range[0]) / duration * 100}%` }} />)}
    </div>
    <div className="timeline-scale"><span>00:00</span><span>{formatTime(duration)}</span></div>
    <div className="coverage-footer"><div className="legend"><span><i className="legend-complete" />已扫描</span><span><i className="legend-active" />处理中</span><span><i className="legend-pending" />未完成</span><span><i className="legend-failed" />失败 / 缺口</span></div><span className="muted small">扫描完成 ≠ 全部事件已找出</span></div>
    {gaps.length > 0 && <div className="inline-warning"><Icon name="alert" size={15} /><span>未完成扫描的原视频范围：{gaps.map(range => rangeLabel(range, false)).join('、')}</span></div>}
    {tasks.length > 0 && <details className="window-details"><summary>查看 {tasks.length} 个窗口任务</summary><div className="task-table"><table><thead><tr><th>窗口</th><th>负责范围 · 原视频</th><th>状态</th></tr></thead><tbody>{tasks.map(task => <tr key={task.task_id}><td className="mono">{task.task_id}</td><td className="mono">{rangeLabel(taskRange(task))}</td><td><Badge status={task.status} /></td></tr>)}</tbody></table></div></details>}
  </section>;
}

function EventDetails({ event, seekSource }: { event: EventResult; seekSource: (time: number) => void }) {
  const clipUrl = event.clip?.url ?? (event.clip?.path ? artifactUrl(event.clip.path) : null);
  const start = eventStart(event.location);
  const [videoError, setVideoError] = useState(false);
  useEffect(() => setVideoError(false), [event.event_id, clipUrl]);
  return <div className="event-detail">
    <div className="detail-title"><div><span className="eyebrow">选中事件</span><h3>{event.location.kind === 'point' ? '事件时刻' : '动作区间'} <span className="mono">{locationLabel(event.location)}</span></h3></div><Badge status={event.result_bucket}>{bucketNames[event.result_bucket]}</Badge></div>
    {event.clip?.kind === 'context_fallback' && <div className="inline-warning"><Icon name="alert" size={16} /><span>此片段是上下文预览，事件的完整边界尚未确定。</span></div>}
    {clipUrl ? <video className="clip-player" key={clipUrl} controls preload="metadata" src={clipUrl} onError={() => setVideoError(true)} aria-label="选中事件的截取片段" /> : <div className="clip-placeholder"><Icon name="film" size={30} /><p>{event.clip_status === 'failed' ? '片段截取失败，识别记录仍保留' : event.result_bucket === 'rejected' ? '已排除的候选不生成片段' : event.clip_status === 'pending' ? '片段尚未生成' : '此事件没有可播放片段'}</p></div>}
    {videoError && <p className="error-text" role="alert">浏览器无法播放此片段。可下载文件，或在下方处理记录中检查产物。</p>}
    {event.clip?.metadata.audio_included === false && <p className="field-help">此片段仅含视频，原始上传保留音轨。</p>}
    <div className="clip-actions">{clipUrl && <a className="button button-secondary" href={clipUrl} download><Icon name="download" size={16} />下载片段</a>}{start !== undefined && <button className="button button-ghost" type="button" onClick={() => seekSource(start)}>在原视频定位 <Icon name="arrow" size={15} /></button>}</div>
    {event.reason && <p className="event-reason">{event.reason}</p>}
    {event.uncertainty_reasons.length > 0 && <div className="uncertainty"><strong>尚不确定</strong><ul>{event.uncertainty_reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul></div>}
    <dl className="metadata-grid"><div><dt>模型判定</dt><dd>{{ supported: '有匹配证据', rejected: '排除', unresolved: '尚未解决' }[event.decision]}</dd></div><div><dt>边界状态</dt><dd>{{ bounded: '已定位有限范围', open: '边界开放', unknown: '边界未知' }[event.boundary_status]}</dd></div>
      {event.location.kind === 'point' ? <div className="wide"><dt>事件可能发生范围 · 原视频</dt><dd className="mono">{rangeLabel(event.location.anchor_range_us)}</dd></div> : <><div><dt>开始不确定范围</dt><dd className="mono">{rangeLabel(event.location.start_range_us)}</dd></div><div><dt>结束不确定范围</dt><dd className="mono">{rangeLabel(event.location.end_range_us)}</dd></div></>}
      {event.clip && <><div className="wide"><dt>请求裁剪范围 · 原视频</dt><dd className="mono">{rangeLabel(event.clip.cut_range_us)}</dd></div><div className="wide"><dt>实际裁剪范围 · 原视频</dt><dd className="mono">{event.clip.actual_range_us ? rangeLabel(event.clip.actual_range_us) : '后端未提供'}</dd></div></>}
    </dl>
    <details className="trace-details"><summary>事件来源与证据</summary><dl><dt>事件 ID</dt><dd className="mono">{event.event_id}</dd><dt>来源候选</dt><dd className="mono">{event.source_candidate_ids.join(', ') || '未提供'}</dd><dt>主体 / 对象标识</dt><dd>{event.entity_key}</dd><dt>证据引用</dt><dd className="mono">{event.evidence_refs.join(', ') || '未提供'}</dd>{event.duplicate_of && <><dt>关联重复事件</dt><dd className="mono">{event.duplicate_of}</dd></>}</dl><Artifacts value={event} /></details>
  </div>;
}

function DebugPanel({ run, logs }: { run: Run; logs: LogEntry[] }) {
  return <details className="debug-panel"><summary><span><Icon name="code" size={18} />处理记录与调试</span><span className="muted small">{logs.length} 条已载入日志</span></summary>
    <div className="debug-body"><div className="debug-toolbar"><span className="mono">{run.id}</span><a href={`/api/runs/${encodeURIComponent(run.id)}`} target="_blank" rel="noreferrer">运行 JSON ↗</a>{run.results && <a href={`/api/runs/${encodeURIComponent(run.id)}/results`} target="_blank" rel="noreferrer">结果 JSON ↗</a>}</div>
      <details><summary>本次查询解释与冻结配置</summary><pre>{JSON.stringify({ query_spec: run.query_spec, config: run.config, progress: run.progress }, null, 2)}</pre></details>
      <div className="log-list">{logs.length === 0 ? <p className="muted">尚无处理日志。</p> : logs.map(log => <div className={`log-entry log-${log.level}`} key={log.seq}><div className="log-meta"><span className="mono">#{log.seq} · {new Date(log.time).toLocaleTimeString('zh-CN', { hour12: false })}</span><span>{stageNames[log.stage] ?? log.stage}</span><span>{log.level}</span></div><p>{log.message}</p><Artifacts value={{ artifact_refs: log.artifact_refs, details: log.details }} />{Object.keys(log.details ?? {}).length > 0 && <details><summary>查看参数与原始引用</summary><pre>{JSON.stringify(log.details, null, 2)}</pre></details>}</div>)}</div>
      {(run.tasks ?? []).length > 0 && <details><summary>全部任务与产物引用（{run.tasks!.length}）</summary>{run.tasks!.map(task => <TaskDetails key={task.task_id} task={task} />)}</details>}
    </div>
  </details>;
}

function TaskDetails({ task }: { task: Task }) {
  return <details className="task-detail"><summary><span className="mono">{task.task_id}</span> · {stageNames[task.stage] ?? task.stage} · {statusNames[task.status] ?? task.status}</summary><Artifacts value={task} /><pre>{JSON.stringify(task, null, 2)}</pre></details>;
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [media, setMedia] = useState<Media[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [mediaId, setMediaId] = useState('');
  const [runId, setRunId] = useState(() => { try { return localStorage.getItem('video-workbench-run') ?? ''; } catch { return ''; } });
  const [run, setRun] = useState<Run | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [query, setQuery] = useState('');
  const [provider, setProvider] = useState<Provider>('gemini');
  const [profile, setProfile] = useState<Profile>('auto');
  const [model, setModel] = useState('');
  const [scanFps, setScanFps] = useState('');
  const [coreWindow, setCoreWindow] = useState('');
  const [maxCalls, setMaxCalls] = useState('500');
  const [refineFps, setRefineFps] = useState('12');
  const [busy, setBusy] = useState<'upload' | 'demo' | 'create' | 'cancel' | null>(null);
  const [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [filter, setFilter] = useState<ResultBucket | 'all'>('all');
  const [eventId, setEventId] = useState('');
  const [sourceError, setSourceError] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const sourceVideo = useRef<HTMLVideoElement>(null);
  const logCursor = useRef(0);
  const formRef = useRef<HTMLFormElement>(null);
  const selectedMedia = media.find(item => item.id === mediaId);
  const runMedia = media.find(item => item.id === run?.media_id);
  const viewerMedia = run ? runMedia : selectedMedia;
  const active = run && !terminalStatuses.has(run.status);
  const allEventRecords = run?.results?.events ?? [];
  const events = allEventRecords.filter(event => !event.duplicate_of);
  const duplicateRecords = allEventRecords.filter(event => event.duplicate_of);
  const filteredEvents = filter === 'all' ? events : events.filter(event => event.result_bucket === filter);
  const selectedEvent = filteredEvents.find(event => event.event_id === eventId) ?? filteredEvents[0];

  async function refreshLists() {
    const responses = await Promise.allSettled([api<Health>('/health'), api<Media[]>('/media'), api<Run[]>('/runs')]);
    const [healthResponse, mediaResponse, runResponse] = responses;
    if (healthResponse.status === 'fulfilled') setHealth(healthResponse.value);
    if (mediaResponse.status === 'fulfilled') setMedia(mediaResponse.value);
    if (runResponse.status === 'fulfilled') setRuns(runResponse.value);
    if (responses.every(response => response.status === 'rejected')) setError('无法连接后端服务。请确认 API 已在 127.0.0.1:8000 启动，然后重新连接。');
  }

  useEffect(() => { void refreshLists(); const timer = setInterval(() => { void api<Health>('/health').then(setHealth).catch(() => setHealth(null)); }, 20000); return () => clearInterval(timer); }, []);
  useEffect(() => { if (health && !model) setModel(health.default_model); }, [health, model]);
  useEffect(() => {
    if (provider === 'fixture' && !selectedMedia?.is_demo) setProvider('gemini');
  }, [selectedMedia, provider]);
  useEffect(() => { setSourceError(false); }, [viewerMedia?.id]);
  useEffect(() => {
    setRun(null); setLogs([]); setEventId(''); setFilter('all'); setPollError(''); logCursor.current = 0;
    try { if (runId) localStorage.setItem('video-workbench-run', runId); else localStorage.removeItem('video-workbench-run'); } catch { /* local storage may be unavailable */ }
    if (!runId) return;
    const controller = new AbortController();
    let timeout: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const current = await api<Run>(`/runs/${encodeURIComponent(runId)}`, { signal: controller.signal });
        const entries = await api<{ items: LogEntry[] }>(`/runs/${encodeURIComponent(runId)}/logs?after=${logCursor.current}&limit=200`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setRun(current); setPollError('');
        setRuns(prior => [current, ...prior.filter(item => item.id !== current.id)].slice(0, 30));
        if (entries.items.length > 0) {
          logCursor.current = Math.max(logCursor.current, ...entries.items.map(entry => entry.seq));
          setLogs(prior => { const previousIds = new Set(prior.map(entry => entry.seq)); return [...prior, ...entries.items.filter(entry => !previousIds.has(entry.seq))]; });
        }
        const source = await api<Media>(`/media/${encodeURIComponent(current.media_id)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setMedia(prior => [source, ...prior.filter(item => item.id !== source.id)]);
        if (!terminalStatuses.has(current.status) || entries.items.length === 200) timeout = setTimeout(poll, 1200);
      } catch (e) {
        if (controller.signal.aborted) return;
        setPollError(`刷新运行失败：${errorText(e)}。正在重连。`);
        timeout = setTimeout(poll, 4000);
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timeout); };
  }, [runId]);

  async function upload(file?: File) {
    if (!file) return;
    setBusy('upload'); setError('');
    try {
      const body = new FormData(); body.append('file', file);
      const item = await api<Media>('/media', { method: 'POST', body });
      setMedia(prior => [item, ...prior.filter(m => m.id !== item.id)]); setMediaId(item.id); setRunId('');
    } catch (e) { setError(`上传失败：${errorText(e)}`); }
    finally { setBusy(null); if (fileInput.current) fileInput.current.value = ''; }
  }

  async function loadDemo() {
    setBusy('demo'); setError('');
    try {
      const item = await api<Media>('/demo', { method: 'POST' });
      setMedia(prior => [item, ...prior.filter(m => m.id !== item.id)]); setMediaId(item.id); setRunId(''); setProvider('fixture'); setProfile('point'); setQuery(defaultQuery);
    } catch (e) { setError(`测试视频生成失败：${errorText(e)}`); }
    finally { setBusy(null); }
  }

  async function createRun(e: FormEvent) {
    e.preventDefault();
    if (!selectedMedia || !query.trim()) return;
    setBusy('create'); setError('');
    try {
      const config = {
        provider, model_id: model || health?.default_model || 'gemini-3.8-flash', profile,
        max_calls: Number(maxCalls), refine_fps: Number(refineFps),
        ...(scanFps ? { scan_fps: Number(scanFps) } : {}), ...(coreWindow ? { core_window_s: Number(coreWindow) } : {}),
      };
      const item = await api<Run>('/runs', jsonRequest({ media_id: selectedMedia.id, query: query.trim(), config }, { 'Idempotency-Key': crypto.randomUUID() }));
      setRunId(item.id); setRuns(prior => [item, ...prior.filter(r => r.id !== item.id)]);
    } catch (e) { setError(`无法开始处理：${errorText(e)}`); }
    finally { setBusy(null); }
  }

  async function cancelRun() {
    if (!run) return;
    setBusy('cancel'); setError('');
    try { setRun(await api<Run>(`/runs/${encodeURIComponent(run.id)}/cancel`, { method: 'POST' })); }
    catch (e) { setError(`取消失败：${errorText(e)}`); }
    finally { setBusy(null); }
  }

  function seekSource(time: number) {
    if (!sourceVideo.current) return;
    sourceVideo.current.currentTime = time / 1_000_000;
    sourceVideo.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    sourceVideo.current.focus();
  }

  const cannotStart = !selectedMedia || query.trim().length < 2 || busy !== null || !health || (provider === 'gemini' && !health.api_key_configured) || !health.ffmpeg_available || !health.ffprobe_available;
  const sourceUrl = viewerMedia?.preview_url ?? viewerMedia?.original_url;
  const isFixtureRun = run?.config.provider === 'fixture';

  return <div className="app-shell">
    <a className="skip-link" href="#main-workspace">跳到工作区</a>
    <header className="topbar"><a className="brand" href="#" onClick={(e) => { e.preventDefault(); setRunId(''); }}><span className="brand-mark"><Icon name="film" size={23} /></span><div><strong>视频事件工作台</strong><span>VIDEO EVENT WORKBENCH</span></div></a><div className="system-health"><span className={`health-dot ${health ? 'online' : ''}`} /><span>{health ? '本地服务已连接' : '等待本地服务'}</span><span className="version">PROTOTYPE / 01</span></div></header>
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-heading"><span className="eyebrow">新建任务</span><h1>从视频里，<br />找出你关心的事件。</h1><p>上传视频，描述要找的动作或时刻。<br />逐段扫描，保留匹配与不确定结果。</p></div>
        <form ref={formRef} onSubmit={createRun} className="run-form">
          <section className="form-section"><div className="step-label"><span>01</span><h2>选择视频</h2></div>
            <input ref={fileInput} id="video-file" className="visually-hidden" type="file" accept="video/*,.mkv,.mov,.webm,.avi" disabled={busy !== null} onChange={e => void upload(e.target.files?.[0])} aria-label="选择要上传的视频文件" />
            <button type="button" className="upload-button" disabled={busy !== null} onClick={() => fileInput.current?.click()}><Icon name="upload" size={21} /><span>{busy === 'upload' ? '正在上传并检查视频…' : '上传本地视频'}<small>MP4、MOV 或其他可解码视频</small></span></button>
            {media.length > 0 && <div className="field"><label htmlFor="media-select">已导入的视频</label><select id="media-select" value={mediaId} onChange={e => { setMediaId(e.target.value); setRunId(''); }} disabled={busy !== null}><option value="">选择一个视频</option>{media.map(item => <option key={item.id} value={item.id}>{item.is_demo ? '[测试素材] ' : ''}{item.filename}</option>)}</select></div>}
            {selectedMedia && <div className="media-selected"><Icon name="film" size={16} /><span>{formatTime(selectedMedia.duration_us)}<span className="divider">/</span>{selectedMedia.is_demo ? '内置工程测试素材' : '已导入'}</span></div>}
            <button type="button" className="text-button demo-button" disabled={busy !== null} onClick={() => void loadDemo()}>{busy === 'demo' ? '正在生成测试视频…' : '没有素材？载入内置工程测试视频'} <Icon name="arrow" size={14} /></button>
          </section>
          <section className="form-section"><div className="step-label"><span>02</span><h2>描述目标事件</h2></div>
            <label className="visually-hidden" htmlFor="query">自然语言查询</label><textarea id="query" value={query} onChange={e => setQuery(e.target.value)} required minLength={2} maxLength={4000} rows={5} placeholder="例如：找出机械臂每次抓取物体的完整尝试，保留失败尝试，排除等待。" />
            <div className="template-row"><span>试试</span>{templates.map(template => <button type="button" key={template.title} onClick={() => { setQuery(template.query); setProfile(template.profile); }}>{template.title}</button>)}</div>
            <div className="field"><label htmlFor="profile">处理预设</label><select id="profile" value={profile} onChange={e => setProfile(e.target.value as Profile)}><option value="auto">自动 · 由查询选择</option><option value="action">动作区间 · 接近、抓取等过程</option><option value="point">短促时刻 · 触地、接触等事件</option></select><p className="field-help">预设控制扫描密度；要找的内容始终以查询为准。</p></div>
          </section>
          <section className="form-section"><div className="step-label"><span>03</span><h2>选择处理方式</h2></div>
            <label className={`provider-option ${provider === 'gemini' ? 'selected' : ''}`}><input type="radio" name="provider" value="gemini" checked={provider === 'gemini'} onChange={() => setProvider('gemini')} /><span><strong>Gemini 视频识别</strong><small>将视频抽样内容发送到模型 API</small></span><Badge status={health?.api_key_configured ? 'succeeded' : 'pending'}>{health?.api_key_configured ? '已配置密钥' : '未配置密钥'}</Badge></label>
            <label className={`provider-option ${provider === 'fixture' ? 'selected fixture' : ''} ${!selectedMedia?.is_demo ? 'disabled' : ''}`}><input type="radio" name="provider" value="fixture" checked={provider === 'fixture'} disabled={!selectedMedia?.is_demo} onChange={() => setProvider('fixture')} /><span><strong>Fixture 工程测试</strong><small>仅限内置素材，使用预设结果</small></span></label>
            {provider === 'fixture' && <p className="fixture-notice"><strong>非真实 AI 识别。</strong> 此模式用于验证处理、截取和显示流程，结果不能代表模型效果。</p>}
            {provider === 'gemini' && health && !health.api_key_configured && <p className="field-help">请在后端 .env 中配置 GEMINI_API_KEY。界面不接收或显示密钥。</p>}
          </section>
          <details className="advanced"><summary>高级参数 <span>可选</span></summary><div className="advanced-fields"><div className="field"><label htmlFor="model">模型 ID</label><input id="model" value={model} onChange={e => setModel(e.target.value)} disabled={provider === 'fixture'} /></div><div className="field-pair"><div className="field"><label htmlFor="scan-fps">扫描 FPS</label><input id="scan-fps" type="number" min="0.1" max="30" step="0.1" placeholder="按预设" value={scanFps} onChange={e => setScanFps(e.target.value)} /></div><div className="field"><label htmlFor="window-length">窗口负责时长 / 秒</label><input id="window-length" type="number" min="0.1" max="120" step="0.1" placeholder="按预设" value={coreWindow} onChange={e => setCoreWindow(e.target.value)} /></div></div><div className="field-pair"><div className="field"><label htmlFor="refine-fps">精定位 FPS</label><input id="refine-fps" type="number" min="0.1" max="60" step="0.1" required value={refineFps} onChange={e => setRefineFps(e.target.value)} /></div><div className="field"><label htmlFor="max-calls">最大模型调用次数</label><input id="max-calls" type="number" min="1" max="5000" required value={maxCalls} onChange={e => setMaxCalls(e.target.value)} /></div></div><p className="field-help">每次运行冻结配置；修改后新建运行。较密采样会增加处理成本。</p></div></details>
          {health && (!health.ffmpeg_available || !health.ffprobe_available) && <p className="error-text">后端缺少 FFmpeg 或 FFprobe，暂时无法处理视频。</p>}
          {health && !health.worker_alive && <p className="field-help">后台 Worker 尚未在线。新任务会留在队列中，等待 Worker 启动。</p>}
          {!health && <button type="button" className="text-button" onClick={() => { setError(''); void refreshLists(); }}>重新连接后端</button>}
          <button className="button button-primary start-button" type="submit" disabled={cannotStart}>{busy === 'create' ? '正在创建任务…' : '开始定位事件'}<Icon name="arrow" /></button>
          <p className="form-footnote">自动识别结果可供检查与下载，未经人工标注确认。</p>
        </form>
        {runs.length > 0 && <section className="history"><div className="section-line"><h2>最近运行</h2><span className="muted small">{runs.length}</span></div>{runs.slice(0, 8).map(item => <button type="button" key={item.id} className={`history-item ${runId === item.id ? 'selected' : ''}`} onClick={() => setRunId(item.id)}><span className="history-query">{item.query}</span><span className="history-meta"><time>{new Date(item.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}</time><Badge status={item.status} /></span></button>)}</section>}
      </aside>
      <main id="main-workspace" className="workspace" tabIndex={-1}>
        {(error || pollError) && <div className="error-banner" role="alert"><Icon name="alert" /><div>{error || pollError}</div>{error && <button type="button" className="text-button" onClick={() => setError('')} aria-label="关闭错误提示">关闭</button>}</div>}
        <div className="workspace-title"><div><span className="eyebrow">{run ? '运行工作区' : '准备工作区'}</span><h2>{run ? '从原片到事件片段' : '先选择视频，再开始定位'}</h2></div>{run && <Badge status={run.status} />}</div>
        {run && <section className="run-overview"><p>{run.query}</p><div className="run-meta"><span>{isFixtureRun ? 'FIXTURE · 工程测试' : String(run.config.model_id ?? 'Gemini')}</span><span className="mono">{run.id.slice(0, 16)}</span><span>{stageNames[run.stage] ?? run.stage}</span>{active && <button type="button" className="text-button cancel-button" disabled={busy === 'cancel'} onClick={() => void cancelRun()}><Icon name="stop" size={13} />{busy === 'cancel' ? '正在取消…' : '取消运行'}</button>}</div></section>}
        {isFixtureRun && <div className="fixture-banner"><Icon name="alert" /><p><strong>工程测试模式 · 非真实 AI 识别</strong><span>当前结果来自内置测试规则，用于检查时间映射、片段截取和界面流程。</span></p></div>}
        {run?.status === 'partial' && <div className="inline-warning"><Icon name="alert" /><span>本次运行仅部分完成。请检查扫描缺口、失败任务及片段状态；已生成的结果仍可查看。</span></div>}
        {run?.status === 'cancelled' && <div className="inline-warning"><Icon name="stop" /><span>运行已取消，已有记录保留。在途模型请求可能仍产生费用。</span></div>}
        {run?.error != null && <div className="error-banner" role="alert"><Icon name="alert" /><span>{errorText(run.error)}</span></div>}
        <section className="source-section" aria-labelledby="source-title"><div className="section-line"><h3 id="source-title">原视频</h3>{viewerMedia && <span className="source-filename">{viewerMedia.filename}</span>}</div>
          {sourceUrl ? <video ref={sourceVideo} key={viewerMedia?.id} className="source-player" src={sourceUrl} controls preload="metadata" aria-label="原视频播放器" onError={() => setSourceError(true)} /> : <div className="source-placeholder"><span className="placeholder-film"><Icon name="film" size={40} /></span><h3>{viewerMedia ? '正在准备可播放视频' : '视频与定位结果会显示在这里'}</h3><p>{viewerMedia ? '保留当前任务，等待视频处理完成。' : '支持长视频中的重复动作、短促时刻和完整活动区间。'}</p><div className="placeholder-flow"><span>导入视频</span><i>→</i><span>定位事件</span><i>→</i><span>查看片段</span></div></div>}
          {sourceError && <p className="error-text">浏览器暂时无法播放原片。代理视频准备好后可重新打开本次运行。</p>}
          {viewerMedia && <div className="source-footer"><span>{formatTime(viewerMedia.duration_us)}<span className="divider">/</span>{viewerMedia.is_demo ? '内置测试视频' : '上传原片'}</span>{viewerMedia.original_url && <a href={viewerMedia.original_url} download>下载原视频 ↗</a>}</div>}
        </section>
        {run && <CoveragePanel run={run} media={runMedia} />}
        {run && <section className="results-section" aria-labelledby="results-title"><div className="section-line"><h2 id="results-title">事件结果 <span className="count">{events.length}</span></h2><span className="muted small">模型结果 · 只读</span></div>
          <div className="filters" aria-label="按识别结果筛选">{(['all', 'matched', 'uncertain', 'rejected'] as const).map(bucket => <button type="button" key={bucket} aria-pressed={filter === bucket} className={filter === bucket ? 'active' : ''} onClick={() => { setFilter(bucket); setEventId(''); }}>{bucket === 'all' ? '全部' : bucketNames[bucket]}<span>{bucket === 'all' ? events.length : events.filter(event => event.result_bucket === bucket).length}</span></button>)}</div>
          {filteredEvents.length > 0 ? <div className="results-grid"><div className="event-list" aria-label="事件列表">{filteredEvents.map((event, index) => <button type="button" key={event.event_id} onClick={() => setEventId(event.event_id)} className={`event-card ${selectedEvent?.event_id === event.event_id ? 'selected' : ''}`} aria-pressed={selectedEvent?.event_id === event.event_id}><div className="event-card-top"><span className="event-number">{String(index + 1).padStart(2, '0')}</span><Badge status={event.result_bucket}>{bucketNames[event.result_bucket]}</Badge></div><strong className="mono">{locationLabel(event.location)}</strong><p>{event.reason || (event.location.kind === 'point' ? '事件时刻' : '动作区间')}</p><span className="event-card-foot">{event.clip?.kind === 'context_fallback' ? '上下文预览' : event.clip_status === 'succeeded' ? '片段已就绪' : event.clip_status === 'failed' ? '截取失败' : event.result_bucket === 'rejected' ? '保留排除理由' : '等待片段'}<Icon name="arrow" size={14} /></span></button>)}</div>{selectedEvent && <EventDetails event={selectedEvent} seekSource={seekSource} />}</div> : <div className="results-empty"><Icon name={active ? 'film' : 'check'} size={28} /><h3>{active ? '正在定位事件' : events.length ? '此分类没有事件' : run.status === 'completed' ? '处理完成，没有返回匹配事件' : '暂无事件结果'}</h3><p>{active ? '扫描与局部核实的进展会持续显示在覆盖范围和处理记录中。' : events.length ? '切换到其他分类查看结果。' : run.status === 'completed' ? '空结果仅表示本次模型未返回事件；可调整查询或采样配置后重新运行。' : '查看处理记录了解取消、失败或未完成的范围。'}</p></div>}
          {(run.results?.limitations ?? []).length > 0 && <details className="limitations"><summary>本次运行的限制与说明</summary><ul>{run.results!.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></details>}
          {duplicateRecords.length > 0 && <details className="limitations"><summary>重复来源记录（{duplicateRecords.length} 条，不计为独立事件）</summary><ul>{duplicateRecords.map(item => <li key={item.event_id}><span className="mono">{item.event_id}</span> → <span className="mono">{item.duplicate_of}</span>{item.reason && <p>{item.reason}</p>}</li>)}</ul></details>}
        </section>}
        {run && <DebugPanel run={run} logs={logs} />}
        {!run && <div className="getting-started"><div><span className="eyebrow">输入保持简单</span><h3>说清“什么发生了”，<br />也可以指定开始和结束。</h3></div><p>系统保留每个事件的原视频时间、匹配理由和处理记录。<br />看不清或定位不完整的事件会单独列出。</p></div>}
        <footer className="workspace-footer"><span>本地原型 · 自动定位与截取</span><span>原视频时间为所有结果的共同依据</span></footer>
      </main>
    </div>
  </div>;
}
