/* ─────────────────────────────────────────────────────────────
   Обращения к бэкенду ЦДС. В разработке vite проксирует /api на
   uvicorn (react/vite.config.ts), в сборке фронтенд и API живут
   на одном сервере — поэтому пути относительные.
   ───────────────────────────────────────────────────────────── */

export const TPL_ACCEPT = '.pptx,.potx,.pdf,.html,.htm';

export type TemplateSummary = {
  id: string;
  name: string;
  format: string;
  patterns: number;
  kinds: Record<string, number>;
  palette: string[];
  colors: Partial<Record<'background' | 'text' | 'accent', string>>;
  fonts: string[];
  service_slides: number;
  assets: number;
};

/* Сообщение сервера (detail) — уже по-русски, показываем как есть. */
async function fail(r: Response): Promise<never> {
  const body = await r.json().catch(() => null);
  throw new Error(body?.detail ?? `сервер ответил ${r.status}`);
}

export async function uploadTemplate(file: File): Promise<TemplateSummary> {
  const body = new FormData();
  body.append('file', file);
  let r: Response;
  try {
    r = await fetch('/api/templates', { method: 'POST', body });
  } catch {
    throw new Error('сервер недоступен');
  }
  return r.ok ? r.json() : fail(r);
}

export function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

/* Короткая подпись под миниатюрой: «25 образцов · Play». */
export function describeTemplate(t: TemplateSummary): string {
  const n = t.patterns;
  const font = t.fonts[0] ? ` · ${t.fonts[0]}` : '';
  return `${n} ${plural(n, 'образец', 'образца', 'образцов')}${font}`;
}
