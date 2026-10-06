import { useSyncExternalStore } from 'react';

export type Language = 'en' | 'zh';
export type Messages = Record<string, string>;
const catalogs: Messages[] = [];
const listeners = new Set<() => void>();
const storageKey = 'video-workbench-language';
// Directly rendered components/tests retain the legacy locale; main initializes preferences.
let language: Language = 'zh';
export function registerMessages(messages: Messages): void { catalogs.push(messages); }
export function getLanguage(): Language { return language; }
export function localeTag(): string { return language === 'en' ? 'en-US' : 'zh-CN'; }
export function setLanguage(next: Language): void {
  language = next;
  try { localStorage.setItem(storageKey, next); } catch { /* storage is optional */ }
  if (typeof document !== 'undefined') {
    document.documentElement.lang = next === 'en' ? 'en' : 'zh-CN';
    document.title = next === 'en' ? 'Video Event Workbench' : '视频事件工作台';
  }
  listeners.forEach(listener => listener());
}
export function initializeLanguage(): void {
  let saved: string | null = null;
  try { saved = localStorage.getItem(storageKey); } catch { /* storage is optional */ }
  setLanguage(saved === 'en' || saved === 'zh' ? saved :
    typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en');
}
export function t(message: string): string {
  if (language === 'zh') return message;
  for (const catalog of catalogs) if (Object.prototype.hasOwnProperty.call(catalog, message)) return catalog[message];
  return message;
}
export function useI18n() {
  const selected = useSyncExternalStore(listener => { listeners.add(listener); return () => listeners.delete(listener); }, getLanguage, () => 'en' as Language);
  return { language: selected, setLanguage, t, localeTag };
}
