import type { CallCost, LogEntry, Run } from './types';
import { collectCallRows, formatTokens, formatUsd, summarizeCosts } from './costs';
import type { CallRow } from './costs';
import { statusNames, terminalStatuses } from './utils';

const costStateNames = { estimated: 'estimated · 标准价估算', local: 'Fixture · 本地免费', pending: '等待用量', unknown: '费用未知', unrecorded: '费用未记录' };
const reasons: Record<string, string> = {
  request_outcome_unknown: '请求结果未知，可能已产生费用。',
  model_pricing_unavailable: '没有此模型的已记录价格。',
  usage_unavailable: '模型未返回完整用量。',
  invalid_usage: '用量记录无法用于可靠估算。',
  tool_usage_pricing_unavailable: '尚未覆盖工具调用价格。',
};

function PriceDetails({ cost }: { cost: CallCost | null }) {
  const rates = cost?.rates_per_million_tokens;
  const source = cost?.pricing_source && /^https?:\/\//.test(cost.pricing_source) ? cost.pricing_source : null;
  return <details className="call-price-details">
    <summary>价格来源与版本</summary>
    {cost ? <dl>
      <div><dt>计价依据</dt><dd>{cost.basis === 'local_fixture_no_remote_call' ? '本地 Fixture，无远程 API 调用' : '已记录的付费标准价估算，不是账单'}</dd></div>
      <div><dt>价格版本</dt><dd className="mono">{cost.pricing_version ?? '未记录'}</dd></div>
      <div><dt>模型</dt><dd className="mono">{cost.model_id ?? '未记录'}</dd></div>
      {cost.effective_from && <div><dt>价格生效日期</dt><dd>{cost.effective_from}</dd></div>}
      {cost.submitted_on && <div><dt>请求计价日期</dt><dd>{cost.submitted_on}</dd></div>}
      {rates && <div><dt>USD / 1M tokens</dt><dd>input {formatUsd(rates.input)} · cached input {formatUsd(rates.cached_input)} · output + thinking {formatUsd(rates.output_including_thinking)}</dd></div>}
      {source && <div><dt>价格来源</dt><dd><a href={source} target="_blank" rel="noreferrer">查看已记录的官方价格来源 ↗</a></dd></div>}
      {cost.reason && <div><dt>未能估算的原因</dt><dd>{reasons[cost.reason] ?? cost.reason}</dd></div>}
    </dl> : <p>此调用没有保存价格快照。历史记录不按当前价格补算。</p>}
  </details>;
}

function CostRow({ row }: { row: CallRow }) {
  return <tbody className={`cost-call cost-call-${row.costState}`}>
    <tr className="cost-call-main">
      <th scope="row" data-label="Operation"><strong className="mono">{row.operation}</strong><span className="cost-task-id mono">{row.taskId}</span></th>
      <td data-label="Input"><span className="input-mode-tag mono">{row.inputMode}</span></td>
      <td data-label="状态"><span className={`badge badge-${row.status}`}>{statusNames[row.status] ?? row.status}</span><span className={`cost-state cost-state-${row.costState}`}>{costStateNames[row.costState]}</span></td>
      <td className="numeric" data-label="Latency"><span>{row.latencyS !== null ? `${row.latencyS.toFixed(2)} s` : row.costState === 'pending' ? '待完成' : '未记录'}</span></td>
      <td className="token-cell" data-label="Tokens"><div className="token-counts">
        <span><small>input</small><b>{formatTokens(row.tokens.input)}</b></span>
        <span><small>cached</small><b>{formatTokens(row.tokens.cached)}</b></span>
        <span><small>output</small><b>{formatTokens(row.tokens.output)}</b></span>
        <span><small>thinking</small><b>{formatTokens(row.tokens.thinking)}</b></span>
      </div></td>
      <td className="numeric call-amount" data-label="Estimated USD"><strong>{row.estimatedUsd !== null ? formatUsd(row.estimatedUsd) : row.costState === 'pending' ? '待完成' : row.costState === 'unrecorded' ? '未记录' : '未知'}</strong></td>
    </tr>
    <tr className="cost-call-detail"><td colSpan={6}>
      <div className="call-provenance"><span className="mono">attempt {row.attemptId ?? '未记录'}</span>{row.source === 'log' && <span>来自已保存的费用日志</span>}{row.createdAt && <time dateTime={row.createdAt}>{new Date(row.createdAt).toLocaleString('zh-CN', { hour12: false })}</time>}</div>
      <PriceDetails cost={row.cost} />
    </td></tr>
  </tbody>;
}

export function CostPanel({ run, logs }: { run: Run | null; logs: LogEntry[] }) {
  const rows = run ? collectCallRows(run, logs) : [];
  const summary = run ? summarizeCosts(run, rows) : null;
  const hasCalls = !!summary && summary.total_calls > 0;
  const hasKnownAmount = !!summary && summary.estimated_usd !== null && (summary.estimated_calls > 0 || summary.local_calls > 0);
  const missingHistory = !!run && !run.cost_summary && rows.length === 0 && terminalStatuses.has(run.status);
  const amountLabel = !run ? '尚未开始' : missingHistory ? '未记录' : !hasCalls ? '尚未开始调用' : hasKnownAmount ? formatUsd(summary!.estimated_usd) : summary!.unrecordedCalls > 0 ? '未记录' : summary!.pending_calls > 0 && summary!.unknown_calls === 0 ? '等待用量' : '费用未知';

  return <section className="cost-panel" aria-label="模型调用费用">
    <div className="cost-heading"><div><h3>模型调用费用</h3><p>按每次调用保存的用量与价格版本更新。</p></div><span className="cost-basis-label">estimated · USD</span></div>
    <div className="cost-overview">
      <div className="cost-total"><span>已知估算小计</span><strong className={hasKnownAmount ? 'mono' : 'cost-not-available'}>{amountLabel}</strong></div>
      <dl className="cost-counters">
        <div><dt>estimated</dt><dd>{summary?.estimated_calls ?? '—'}</dd></div>
        <div><dt>未知 / 未记录</dt><dd>{summary?.unknown_calls ?? '—'}</dd></div>
        <div><dt>在途</dt><dd>{summary?.pending_calls ?? '—'}</dd></div>
        <div><dt>Fixture</dt><dd>{summary?.local_calls ?? '—'}</dd></div>
      </dl>
    </div>
    <p className="cost-explanation">{summary?.local_calls && summary.local_calls === summary.total_calls ? 'Fixture 是本地工程测试，无远程 API 费用。' : '按付费标准价估算，不是实际账单；未计入免费额度、折扣或未返回的用量。'}{summary && (summary.unknown_calls > 0 || summary.pending_calls > 0) ? ` 另有 ${summary.unknown_calls} 次费用未知 / 未记录、${summary.pending_calls} 次在途，不计为 $0。` : ''}{run?.status === 'cancelled' ? ' 取消运行不会清除已产生或晚到的费用。' : ''}</p>
    {rows.length > 0 ? <div className="cost-table-scroll" tabIndex={0} role="region" aria-label="每次模型调用费用明细，可滚动">
      <table className="cost-table"><caption className="visually-hidden">每次模型调用的输入方式、状态、耗时、token 用量和估算费用。cached 是 input 中的缓存部分；output 和 thinking 分别列出。</caption><thead><tr><th scope="col">Operation / task</th><th scope="col">Input</th><th scope="col">状态</th><th scope="col">Latency</th><th scope="col">Tokens</th><th scope="col">Estimated USD</th></tr></thead>{rows.map(row => <CostRow key={row.id} row={row} />)}</table>
    </div> : <p className="cost-empty">{missingHistory || hasCalls ? '这次运行没有可用的逐次费用记录，不能推断为零费用。' : '开始调用后，这里会显示逐次记录。尚未提交的计划任务不计为调用。'}</p>}
    {rows.length > 0 && <div className="cost-table-footer"><span>{rows.length} 条调用明细 · 按 attempt 去重</span><span>cached 包含在 input 中；output 不含单列的 thinking</span></div>}
  </section>;
}
