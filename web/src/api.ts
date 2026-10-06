import { errorText } from './utils';
import { getLanguage, t } from './i18n';

export class APIRequestError extends Error {
  constructor(public readonly payload: unknown, public readonly status: number) { super(errorText(payload)); }
  get code(): string | undefined {
    const body = this.payload as { error?: { code?: string } } | null;
    return body?.error?.code;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('Accept-Language', getLanguage());
  const response = await fetch(`/api${path}`, { ...init, headers });
  const text = await response.text();
  let data: unknown;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!response.ok) throw new APIRequestError(data ?? { message: getLanguage() === 'en' ?
    `Service request failed (HTTP ${response.status}).` : `服务请求失败（HTTP ${response.status}）` }, response.status);
  if (data == null) throw new Error(t('API 没有返回有效 JSON，请检查后端服务是否已启动。'));
  return data as T;
}

export const jsonRequest = (body: unknown, extraHeaders: Record<string, string> = {}): RequestInit => ({
  method: 'POST', headers: { 'Content-Type': 'application/json', ...extraHeaders }, body: JSON.stringify(body),
});
