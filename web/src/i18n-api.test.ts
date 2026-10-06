// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, APIRequestError, jsonRequest } from './api';
import { setLanguage } from './i18n';
import { errorText } from './utils';

afterEach(() => { setLanguage('zh'); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('localized API boundary', () => {
  it('sends the selected locale on each request and preserves caller headers and form bodies', async () => {
    const fetchMock = vi.fn(async () => new Response('{"ok":true}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    setLanguage('en');
    await api('/runs', jsonRequest({ query: '原始查询' }, { 'Idempotency-Key': 'same-request' }));
    const first = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const headers = new Headers(first[1].headers);
    expect(headers.get('Accept-Language')).toBe('en');
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(headers.get('Idempotency-Key')).toBe('same-request');
    expect(first[1].body).toBe('{"query":"原始查询"}');
    setLanguage('zh');
    const body = new FormData(); body.set('file', new Blob(['fixture']), 'demo.mp4');
    await api('/media', { method: 'POST', body });
    const second = fetchMock.mock.calls[1] as unknown as [string, RequestInit];
    expect(new Headers(second[1].headers).get('Accept-Language')).toBe('zh');
    expect(new Headers(second[1].headers).has('Content-Type')).toBe(false);
    expect(second[1].body).toBe(body);
  });

  it('relocalizes saved API errors by stable code, preserving their payload and dynamic limit', () => {
    const payload = { error: { code: 'upload_too_large', message: 'Original system copy', details: { max_upload_mb: 2048 } } };
    setLanguage('en');
    const error = new APIRequestError(payload, 413);
    expect(errorText(error)).toBe('The video exceeds the 2048 MB upload limit.');
    setLanguage('zh');
    expect(errorText(error)).toBe('视频超过 2048 MB 限制。');
    expect(error.payload).toBe(payload);
    expect(error.status).toBe(413);
  });

  it('relocalizes known media errors and retains unknown external messages verbatim', () => {
    const known = new APIRequestError({ error: { code: 'decode_failed', message: '技术原文', technical_message: '技术原文' } }, 422);
    const unknown = new APIRequestError({ error: { code: 'external_error', message: 'Provider 原始错误' } }, 502);
    setLanguage('en'); expect(errorText(known)).toBe('The video could not be decoded.');
    expect(errorText(unknown)).toBe('Provider 原始错误');
    setLanguage('zh'); expect(errorText(known)).toBe('视频解码失败。');
    expect(errorText(unknown)).toBe('Provider 原始错误');
  });
});
