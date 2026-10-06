import type { InputMode, Profile, Run } from './types';

export function InputModeControls({ scanMode, refineMode, onScanChange, onRefineChange, profile, disabled = false }: {
  scanMode: InputMode;
  refineMode: InputMode;
  onScanChange: (value: InputMode) => void;
  onRefineChange: (value: InputMode) => void;
  profile: Profile;
  disabled?: boolean;
}) {
  return <div className="input-mode-controls">
    <div className="input-mode-fields">
      <div className="field">
        <label htmlFor="scan-input-mode">scan / propose 输入</label>
        <select id="scan-input-mode" value={scanMode} disabled={disabled} onChange={event => onScanChange(event.target.value as InputMode)}>
          <option value="images">images · 带时间戳的抽样帧</option>
          <option value="video">video · 本地裁切的视频</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="refine-input-mode">refine / verify_refine 输入</label>
        <select id="refine-input-mode" value={refineMode} disabled={disabled} onChange={event => onRefineChange(event.target.value as InputMode)}>
          <option value="images">images · 带时间戳的抽样帧</option>
          <option value="video">video · 本地裁切的视频</option>
        </select>
      </div>
    </div>
    <p className="input-mode-explanation">两步可独立比较。video 输入不含音频，也按设置的 FPS 静态采样（最高 24 FPS），不表示逐帧连续推断。</p>
    <p className="field-help">scan 默认：{profile === 'auto' ? 'auto 根据 QuerySpec 选择 action 2 FPS 或 point 6 FPS' : profile === 'point' ? 'point 6 FPS' : 'action 2 FPS'}；refine 默认 6 FPS。高级参数可覆盖。</p>
  </div>;
}

function modeLabel(value: unknown): string {
  return value === 'images' || value === 'video' ? value : '未记录';
}

export function frozenScanFps(run: Run): string {
  if (typeof run.config.scan_fps === 'number') return `${run.config.scan_fps} FPS · 覆写`;
  const observed = [...new Set((run.tasks ?? []).filter(task => task.stage === 'scan').map(task => {
    const fps = task.window?.sample_fps ?? task.sampling?.requested_fps;
    return typeof fps === 'number' ? fps : null;
  }).filter((fps): fps is number => fps !== null))];
  if (observed.length > 0) return `${observed.join(' / ')} FPS · 已规划`;
  // A historical run without the mode fields predates these preset defaults.
  if (!run.config.scan_input_mode) return '未记录';
  const profile = run.config.profile;
  if (profile === 'action') return '2 FPS · action 默认';
  if (profile === 'point') return '6 FPS · point 默认';
  if (run.query_spec) return run.query_spec.event_kind === 'point' ? '6 FPS · point 默认' : '2 FPS · action 默认';
  return '等待 QuerySpec';
}

export function FrozenRunConfig({ run }: { run: Run }) {
  return <section className="frozen-run-config" aria-label="本次运行的冻结配置">
    <div className="frozen-heading"><h3>本次输入组合</h3><span>运行创建时已冻结</span></div>
    <div className="frozen-mode-pair">
      <div><span>scan / propose</span><strong>{modeLabel(run.config.scan_input_mode)}</strong><small>{frozenScanFps(run)}</small></div>
      <span className="mode-pair-arrow" aria-hidden="true">→</span>
      <div><span>refine / verify_refine</span><strong>{modeLabel(run.config.refine_input_mode)}</strong><small>{typeof run.config.refine_fps === 'number' ? `${run.config.refine_fps} FPS` : 'FPS 未记录'}</small></div>
    </div>
    <div className="frozen-details">
      <span>model 并行 <b>{typeof run.config.model_concurrency === 'number' ? run.config.model_concurrency : '未记录'}</b></span>
      <span>clip 并行 <b>{typeof run.config.clip_concurrency === 'number' ? run.config.clip_concurrency : '未记录'}</b></span>
      <span>单次请求超时 <b>{typeof run.config.request_timeout_s === 'number' ? `${run.config.request_timeout_s} 秒` : '未记录'}</b></span>
      <span>thinking <b>{typeof run.config.thinking_level === 'string' ? run.config.thinking_level : '未记录'}</b></span>
      <span>output 上限 <b>{typeof run.config.max_output_tokens === 'number' ? run.config.max_output_tokens : '未记录'}</b></span>
      <span>temperature <b>{typeof run.config.temperature === 'number' ? run.config.temperature : '未记录'}</b></span>
    </div>
  </section>;
}
