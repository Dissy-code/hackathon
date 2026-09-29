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

/* ── Аккаунт ── сессия живёт в HttpOnly-cookie, скрипту её не видно */
export type Me = { id: number; name: string; email: string; templates: number; decks: number };

export async function fetchMe(): Promise<Me | null> {
  try {
    const r = await fetch('/api/auth/me');
    return r.ok ? r.json() : null;
  } catch {
    return null;
  }
}

export async function logout(): Promise<void> {
  await fetch('/api/auth/logout', { method: 'POST' }).catch(() => undefined);
}

/* ── Картинки пользователя ── */
export type UploadedImage = { id: string; name: string; url: string };

export async function uploadImage(file: File): Promise<UploadedImage> {
  const body = new FormData();
  body.append('file', file);
  let r: Response;
  try {
    r = await fetch('/api/images', { method: 'POST', body });
  } catch {
    throw new Error('сервер недоступен');
  }
  return r.ok ? r.json() : fail(r);
}

/* ── Генерация ── */
export type DeckEvent = { stage: string; message?: string; progress: number; titles?: string[] };

/* Вариант колоды: свой план, текст и вёрстка по тому же брифу и шаблону. */
export type DeckVariant = {
  variant: number;
  label: string;
  pptx: string | null;
  pdf: string | null;
  slides: string[];
  warnings: { slide: number; text: string }[];
  render_error: string | null;
};

/* Колода по одному шаблону — три варианта. */
export type DeckByTemplate = { template_id: string; name: string; variants: DeckVariant[] };

export type DeckResult = { outline: Record<string, string[]>; decks: DeckByTemplate[]; warnings: string[] };

export async function createDeck(prompt: string, templateIds: string[], imageIds: string[]): Promise<string> {
  let r: Response;
  try {
    r = await fetch('/api/decks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, template_ids: templateIds, image_ids: imageIds }),
    });
  } catch {
    throw new Error('сервер недоступен');
  }
  if (!r.ok) return fail(r);
  return (await r.json()).id;
}

/* SSE: onEvent на каждое событие; промис завершается на done (результат) или error (исключение). */
export function followDeck(id: string, onEvent: (e: DeckEvent) => void): Promise<DeckResult> {
  return new Promise((resolve, reject) => {
    const es = new EventSource(`/api/decks/${id}/events`);
    es.onmessage = async (msg) => {
      const e: DeckEvent = JSON.parse(msg.data);
      if (e.stage === 'done') {
        es.close();
        const r = await fetch(`/api/decks/${id}`);
        const body = await r.json();
        resolve(body.result);
      } else if (e.stage === 'error') {
        es.close();
        reject(new Error(e.message || 'генерация не удалась'));
      } else {
        onEvent(e);
      }
    };
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) reject(new Error('связь с сервером прервалась'));
    };
  });
}
