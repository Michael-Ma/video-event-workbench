import { useId, useRef, useState } from 'react';
import type { DragEvent } from 'react';

export function FileUploader({ busy, uploading, onUpload, onError }: {
  busy: boolean; uploading: boolean; onUpload: (file: File) => Promise<void>; onError: (message: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const depth = useRef(0);
  const locked = useRef(false);
  const [dragging, setDragging] = useState(false);
  const hint = useId();

  async function accept(files: File[]) {
    if (busy || locked.current) return;
    if (files.length !== 1) { onError('一次只支持一个视频，请只拖入一个文件。'); return; }
    const file = files[0];
    if (!file.type.startsWith('video/') && !/\.(mp4|mov|mkv|webm|avi|m4v)$/i.test(file.name)) {
      onError('请选择视频文件，例如 MP4、MOV、MKV、WebM 或 AVI。'); return;
    }
    if (file.size === 0) { onError('这个文件为空，请选择有效视频。'); return; }
    locked.current = true;
    try { await onUpload(file); }
    finally { locked.current = false; if (input.current) input.current.value = ''; }
  }
  function over(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.dataTransfer.dropEffect = busy ? 'none' : 'copy';
  }
  return <div role="region" aria-label="视频上传区域" aria-busy={uploading}
    className={`upload-dropzone ${dragging && !busy ? 'is-dragging' : ''} ${uploading ? 'is-uploading' : ''}`}
    onDragEnter={event => { event.preventDefault(); depth.current += 1; if (!busy) setDragging(true); }}
    onDragOver={over}
    onDragLeave={event => { event.preventDefault(); depth.current = Math.max(0, depth.current - 1); if (!depth.current) setDragging(false); }}
    onDrop={event => { event.preventDefault(); depth.current = 0; setDragging(false); void accept(Array.from(event.dataTransfer.files)); }}>
    <input ref={input} className="visually-hidden" type="file" accept="video/*,.mkv,.mov,.webm,.avi,.m4v"
      disabled={busy} tabIndex={-1} aria-label="选择要上传的视频文件"
      onChange={event => { const files = Array.from(event.target.files ?? []); if (files.length) void accept(files); }} />
    <button type="button" className="upload-button" disabled={busy} aria-describedby={hint}
      onClick={() => input.current?.click()}>
      {uploading ? <span className="upload-spinner" aria-hidden="true" /> :
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
          <path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5" />
        </svg>}
      <span>{uploading ? '正在上传并检查视频…' : dragging ? '松开以导入视频' : '拖入视频，或点击选择'}
        <small id={hint}>MP4、MOV、MKV、WebM · 每次一个文件</small></span>
    </button>
  </div>;
}
