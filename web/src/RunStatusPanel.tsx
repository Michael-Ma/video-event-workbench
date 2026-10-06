import { useEffect, useState } from 'react';
import type { Health, Media, Run, RunIssue } from './types';
import { artifactUrl, formatTime, rangeLabel, stageNames, terminalStatuses } from './utils';
import { t, useI18n } from './i18n';
import { panelMessage } from './messages-panels';

const phases = ['准备视频', '解析查询', '全片扫描', '核实事件', '截取片段'];
const phaseIndex: Record<string, number> = {
  queued: -1, preparing: 0, query: 1, normalizing_query: 1, planning: 2,
  scan: 2, scanning: 2, grouping: 3, refine: 3, refining: 3,
  reconciling: 3, clip: 4, clipping: 4, publishing: 4, completed: 5,
};
const count = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : 0;
export function workspaceTone(run: Run | null) {
  if (!run) return 'idle';
  if (!terminalStatuses.has(run.status)) return 'running';
  return run.status === 'completed' ? 'completed' : run.status;
}

function issuesFor(run: Run): RunIssue[] {
  if (run.diagnostics) return run.diagnostics.issues;
  const issues = (run.results?.errors ?? []).map(error => ({
    task_id: error.task_id ?? error.window_id ?? null, stage: error.stage ?? 'scan',
    code: error.code, title: t('任务未完成'), message: error.message ?? error.code,
    action: t('检查处理记录。'), details: {}, artifact_refs: [], not_submitted: false,
  }));
  if (run.error && typeof run.error === 'object') {
    const error = run.error as { code?: string; message?: string };
    issues.push({ task_id: null, stage: run.stage, code: error.code ?? 'run_failed',
      title: t('运行未完成'), message: error.message ?? t('发生错误。'), action: t('检查处理记录。'),
      details: {}, artifact_refs: [], not_submitted: false });
  }
  return issues;
}

function IssueRow({ issue }: { issue: RunIssue }) {
  const { t } = useI18n();
  const details = issue.details;
  return <article className={`run-issue ${issue.not_submitted ? 'issue-blocked' : ''}`}>
    <div className="issue-heading"><strong>{issue.title}</strong>
      <span>{t(stageNames[issue.stage] ?? issue.stage)}{issue.range_us ? ` · ${rangeLabel(issue.range_us, false)}` : ''}</span></div>
    <p>{issue.message}</p>
    {typeof details.elapsed_s === 'number' && <p className="issue-context">
      {typeof details.timeout_s === 'number' ? panelMessage('等待 {elapsed} 秒，超时上限 {timeout} 秒。', { elapsed: details.elapsed_s.toFixed(1), timeout: details.timeout_s }) : panelMessage('等待 {elapsed} 秒。', { elapsed: details.elapsed_s.toFixed(1) })}
      {details.possible_timeout === true && ` ${t('旧记录未保存异常类型，可能是超时或连接中断。')}`}
    </p>}
    {typeof details.exception_type === 'string' && <p className="issue-context">{t('异常类型：')} <code>{details.exception_type}</code></p>}
    <p className="issue-action">{issue.action}</p>
    <details><summary>{t('诊断详情')} <span className="mono">{issue.code}</span></summary>
      {issue.technical_message && <p className="technical-error">{issue.technical_message}</p>}
      <pre>{JSON.stringify({ task_id: issue.task_id, details }, null, 2)}</pre>
      <div className="artifact-links">{issue.artifact_refs.map(path => <a key={path} href={artifactUrl(path) ?? undefined}
        target="_blank" rel="noreferrer">{path.split('/').pop()} ↗</a>)}</div>
    </details>
  </article>;
}

export function RunStatusPanel({ run, media, health, canceling, onCancel }: {
  run: Run | null; media?: Media; health: Health | null; canceling: boolean; onCancel: () => void;
}) {
  const { t } = useI18n();
  const [now, setNow] = useState(Date.now());
  const active = !!run && !terminalStatuses.has(run.status);
  useEffect(() => { setNow(Date.now()); if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer);
  }, [active, run?.id]);
  const tone = workspaceTone(run);
  const issues = run ? issuesFor(run) : [];
  const stoppedStage = run?.diagnostics?.stopped_stage ?? (issues[0]?.stage || null);
  const stage = !active ? stoppedStage ?? run?.last_stage ?? run?.stage ?? 'queued' : run?.stage ?? 'queued';
  const current = phaseIndex[stage] ?? (active ? 0 : -1);
  const tasks = (run?.tasks ?? []).filter(task => !task.replacement_task_ids);
  const modelCalls = run?.tasks ? run.tasks.filter(task => !!task.request_intent).length :
    count(run?.progress.model_calls ?? run?.results?.stats.model_calls);
  const scans = tasks.filter(task => task.stage === 'scan');
  const scanDone = scans.filter(task => task.status === 'succeeded').length;
  const scanTotal = count(run?.progress.scan_total_windows) || scans.length;
  const events = (run?.results?.events ?? []).filter(event => !event.duplicate_of);
  const clips = events.filter(event => event.clip_status === 'succeeded').length;
  const cov = run?.results?.coverage ?? run?.progress.coverage as { duration_us: number; covered_us: number } | undefined;
  const duration = cov?.duration_us ?? media?.duration_us ?? 0;
  const percent = duration ? Math.max(0, Math.min(100, (cov?.covered_us ?? 0) / duration * 100)) : 0;
  const end = active ? now : Date.parse(run?.updated_at ?? '') || now;
  const elapsed = run ? Math.max(0, end - Date.parse(run.created_at)) * 1000 : 0;
  const started = typeof run?.started_at === 'string' ? Date.parse(run.started_at) : NaN;
  const processingElapsed = !active && typeof run?.processing_elapsed_s === 'number' ?
    Math.max(0, run.processing_elapsed_s) * 1_000_000 :
    run && Number.isFinite(started) ? Math.max(0, end - started) * 1000 : null;
  const queueElapsed = run && Number.isFinite(started) ?
    Math.max(0, started - Date.parse(run.created_at)) * 1000 : null;
  const waiting = tasks.slice().reverse().find(task => task.status === 'submitting');
  const intent = waiting?.request_intent;
  const waitingSeconds = intent?.created_at ? Math.max(0, Math.floor((now - Date.parse(intent.created_at)) / 1000)) : 0;

  let title = t('准备开始定位事件');
  let description = t('选择视频并描述目标。开始后，这里会显示当前步骤与已保存的结果。');
  if (run) {
    if (active) {
      title = run.status === 'queued' ? t('任务已入队') : panelMessage('正在{stage}', { stage: t(stageNames[run.stage] ?? run.stage) });
      description = t(run.status === 'queued' ? health?.worker_alive ? '等待后台执行，任务记录已保存。' : '后台 worker 未在线，任务保留在队列中。' :
        waiting ? '正在等待模型响应。' :
        '处理进行中，已经保存的事件和片段会持续显示。');
    } else if (run.status === 'completed') {
      title = t('处理完成');
      description = panelMessage('已保存 {events} 个事件记录和 {clips} 个片段。扫描完成不等于保证找全全部事件。', { events: events.length, clips });
    } else if (run.status === 'partial') {
      title = t(events.length ? '部分完成，已有结果已保留' : '处理已停止，尚无可用事件');
      description = panelMessage('停在{stage}。{resultStatus}', { stage: t(stageNames[stage] ?? stage), resultStatus: t(events.length ? '下方可查看已保存结果。' : '本次尚未返回可展示的事件，具体原因见下方。') });
    } else if (run.status === 'failed') {
      title = t('处理失败'); description = t(events.length ? '已有结果仍可查看，失败原因列在下方。' : '任务未能完成，请查看下方原因。');
    } else {
      title = t('运行已取消'); description = panelMessage('已有 {events} 个事件记录和 {clips} 个片段保留。在途模型请求可能仍产生费用。', { events: events.length, clips });
    }
  }

  return <section className={`run-status-panel status-${tone}`} aria-label={t('整体运行状态')}>
    <div className="status-panel-head">
      <div className="status-heading">
        <span className={`status-emblem ${active ? 'is-active' : ''}`} aria-hidden="true">
          {active ? <span className="status-orbit" /> : tone === 'completed' ? '✓' : tone === 'idle' ? '→' : '!'}
        </span>
        <div role="status" aria-live="polite"><span className="eyebrow">{t('整体状态')} · {t(run ? run.status === 'partial' ? '部分完成' : active ? '进行中' : '已结束' : '开始前')}</span>
          <h2>{title}</h2><p>{description}</p></div>
      </div>
      {active && <button className="button button-danger cancel-run-button" type="button" onClick={onCancel}
        disabled={canceling}>{t(canceling ? '正在取消…' : '取消运行')}</button>}
    </div>
    {run && <>
      <div className="status-query">{run.query}<span className="status-meta">
        <span>{run.config.provider === 'fixture' ? t('FIXTURE · 工程测试') : String(run.config.model_id)}</span>
        <span className="mono">{run.id.slice(0, 16)}</span><span>{panelMessage('总耗时 {time}', { time: formatTime(elapsed) })}</span>
        {processingElapsed !== null && <span>{panelMessage('处理 {time}', { time: formatTime(processingElapsed) })}</span>}
        {queueElapsed !== null && <span>{panelMessage('排队 {time}', { time: formatTime(queueElapsed) })}</span>}
        <span>{panelMessage(run.config.provider === 'fixture' ? '工程测试调用 {count} 次' : '模型请求 {count} 次', { count: modelCalls })}</span>
      </span></div>
      {active && waiting && <p className="status-wait" aria-live="off">{panelMessage('等待模型响应 {elapsed} 秒 / 上限 {limit} 秒', { elapsed: waitingSeconds, limit: String(run.config.request_timeout_s ?? 120) })}</p>}
      {events.length > 0 && <a className="status-results-link" href="#results-title">{panelMessage('查看已保存结果（{count}） ↓', { count: events.length })}</a>}
      <ol className="run-phases" aria-label={t('处理阶段')}>{phases.map((label, index) => {
        const done = index < current || run.status === 'completed';
        const at = index === current;
        const state = done ? 'done' : at ? active ? 'active' : 'halted' : 'waiting';
        return <li key={label} className={`phase-${state}`} aria-current={at && active ? 'step' : undefined}>
          <span>{done ? '✓' : index + 1}</span><strong>{t(label)}</strong>
          <small>{t(done ? '已完成' : at ? active ? '进行中' : '已停止' : '待处理')}</small></li>;
      })}</ol>
      <div className="run-status-metrics">
        <div><span>{t('扫描窗口')}</span><strong>{scanDone} <small>/ {scanTotal || t('待规划')}</small></strong></div>
        <div><span>{t(run.results?.provisional ? '中间事件记录' : '事件记录')}</span><strong>{events.length}</strong></div>
        <div><span>{t('片段已就绪')}</span><strong>{clips}</strong></div>
        <div><span>{t('原视频扫描覆盖')}</span><strong>{formatTime(cov?.covered_us ?? 0)} <small>/ {formatTime(duration)}</small></strong></div>
      </div>
      <div className="status-coverage" role="progressbar" aria-label={t('原视频扫描覆盖')} aria-valuemin={0}
        aria-valuemax={100} aria-valuenow={Math.round(percent)}><span style={{ width: `${percent}%` }} /></div>
      {run.results?.provisional && events.length > 0 && <p className="provisional-notice">{t('这些是已保存的中间结果，尚未完成最终去重与次序筛选。')}</p>}
      {issues.length > 0 && <div className="run-issues" aria-label={t('未完成原因')}><h3>{t('为什么没有完成')}</h3>
        {issues.map((issue, index) => <IssueRow key={issue.task_id ?? `issue-${index}`} issue={issue} />)}</div>}
    </>}
  </section>;
}
