import type { InputMode, Profile, Run } from './types';
import { t, useI18n } from './i18n';
import { panelMessage } from './messages-panels';

export function InputModeControls({ scanMode, refineMode, onScanChange, onRefineChange, profile, disabled = false }: {
  scanMode: InputMode;
  refineMode: InputMode;
  onScanChange: (value: InputMode) => void;
  onRefineChange: (value: InputMode) => void;
  profile: Profile;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  return <div className="input-mode-controls">
    <div className="input-mode-fields">
      <div className="field">
        <label htmlFor="scan-input-mode">{t('scan / propose 输入')}</label>
        <select id="scan-input-mode" value={scanMode} disabled={disabled} onChange={event => onScanChange(event.target.value as InputMode)}>
          <option value="images">{t('images · 带时间戳的抽样帧')}</option>
          <option value="video">{t('video · 本地裁切的视频')}</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="refine-input-mode">{t('refine / verify_refine 输入')}</label>
        <select id="refine-input-mode" value={refineMode} disabled={disabled} onChange={event => onRefineChange(event.target.value as InputMode)}>
          <option value="images">{t('images · 带时间戳的抽样帧')}</option>
          <option value="video">{t('video · 本地裁切的视频')}</option>
        </select>
      </div>
    </div>
    <p className="input-mode-explanation">{t('两步可独立比较。video 输入不含音频，也按设置的 FPS 静态采样（最高 24 FPS），不表示逐帧连续推断。')}</p>
    <p className="field-help">{panelMessage('scan 默认：{scan}；refine 默认 6 FPS。高级参数可覆盖。', {
      scan: profile === 'auto' ? t('auto 根据 QuerySpec 选择 action 2 FPS 或 point 6 FPS') : profile === 'point' ? 'point 6 FPS' : 'action 2 FPS',
    })}</p>
  </div>;
}

function modeLabel(value: unknown): string {
  return value === 'images' || value === 'video' ? value : t('未记录');
}

export function frozenScanFps(run: Run): string {
  if (typeof run.config.scan_fps === 'number') return panelMessage('{fps} FPS · 覆写', { fps: run.config.scan_fps });
  const observed = [...new Set((run.tasks ?? []).filter(task => task.stage === 'scan').map(task => {
    const fps = task.window?.sample_fps ?? task.sampling?.requested_fps;
    return typeof fps === 'number' ? fps : null;
  }).filter((fps): fps is number => fps !== null))];
  if (observed.length > 0) return panelMessage('{fps} FPS · 已规划', { fps: observed.join(' / ') });
  // A historical run without the mode fields predates these preset defaults.
  if (!run.config.scan_input_mode) return t('未记录');
  const profile = run.config.profile;
  if (profile === 'action') return t('2 FPS · action 默认');
  if (profile === 'point') return t('6 FPS · point 默认');
  if (run.query_spec) return t(run.query_spec.event_kind === 'point' ? '6 FPS · point 默认' : '2 FPS · action 默认');
  return t('等待 QuerySpec');
}

export function FrozenRunConfig({ run }: { run: Run }) {
  const { t } = useI18n();
  return <section className="frozen-run-config" aria-label={t('本次运行的冻结配置')}>
    <div className="frozen-heading"><h3>{t('本次输入组合')}</h3><span>{t('运行创建时已冻结')}</span></div>
    <div className="frozen-mode-pair">
      <div><span>scan / propose</span><strong>{modeLabel(run.config.scan_input_mode)}</strong><small>{frozenScanFps(run)}</small></div>
      <span className="mode-pair-arrow" aria-hidden="true">→</span>
      <div><span>refine / verify_refine</span><strong>{modeLabel(run.config.refine_input_mode)}</strong><small>{typeof run.config.refine_fps === 'number' ? `${run.config.refine_fps} FPS` : t('FPS 未记录')}</small></div>
    </div>
    <div className="frozen-details">
      <span>{t('model 并行')} <b>{typeof run.config.model_concurrency === 'number' ? run.config.model_concurrency : t('未记录')}</b></span>
      <span>{t('clip 并行')} <b>{typeof run.config.clip_concurrency === 'number' ? run.config.clip_concurrency : t('未记录')}</b></span>
      <span>{t('单次请求超时')} <b>{typeof run.config.request_timeout_s === 'number' ? panelMessage('{seconds} 秒', { seconds: run.config.request_timeout_s }) : t('未记录')}</b></span>
      <span>thinking <b>{typeof run.config.thinking_level === 'string' ? run.config.thinking_level : t('未记录')}</b></span>
      <span>{t('output 上限')} <b>{typeof run.config.max_output_tokens === 'number' ? run.config.max_output_tokens : t('未记录')}</b></span>
      <span>temperature <b>{typeof run.config.temperature === 'number' ? run.config.temperature : t('未记录')}</b></span>
    </div>
  </section>;
}
