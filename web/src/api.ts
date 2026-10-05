import { errorText } from './utils';

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  const text = await response.text();
  let data: unknown;
  try { data = text ? JSON.parse(text) : null; } catch { data = null; }
  if (!response.ok) throw new Error(data ? errorText(data) : `服务请求失败（HTTP ${response.status}）`);
  if (data == null) throw new Error('API 没有返回有效 JSON，请检查后端服务是否已启动。');
  return data as T;
}

export const jsonRequest = (body: unknown, extraHeaders: Record<string, string> = {}): RequestInit => ({
  method: 'POST', headers: { 'Content-Type': 'application/json', ...extraHeaders }, body: JSON.stringify(body),
});
