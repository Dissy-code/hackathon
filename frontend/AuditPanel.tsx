/* ─────────────────────────────────────────────────────────────
   Панель аудита в режиме демонстрации.

   Слева от неё — холст со слайдом: нажатие на находку листает к её
   слайду и обводит проблемное место рамкой. Находки двух видов:
   детерминированные (по файлу — всегда тот же ответ) и смысловые
   (отвечает модель по картинке слайда, запускаются отдельно).
   Пользователь отмечает, что исправить, и для каждой находки
   выбирает способ; «Исправить» даёт новую ревизию колоды.
   ───────────────────────────────────────────────────────────── */
import { useEffect, useMemo, useState } from 'react';
import { plural, type AuditIssue, type AuditState } from './api.ts';

const GROUP: Record<AuditIssue['group'], string> = {
  layout: 'вёрстка',
  template: 'шаблон',
  density: 'плотность',
  integrity: 'целостность',
  content: 'смысл',
};

type Props = {
  audit: AuditState | null;
  busy: 'load' | 'ctx' | 'fix' | null;
  error: string | null;
  note: string | null;                                  // итог последнего исправления
  slide: number;                                        // текущий слайд (с 0)
  focus: string | null;                                 // находка, обведённая на холсте
  onPick: (issue: AuditIssue) => void;
  onContextual: () => void;
  onFix: (choices: { issue: string; fix: string }[]) => void;
};

export default function AuditPanel({ audit, busy, error, note, slide, focus, onPick, onContextual, onFix }: Props) {
  const [onlySlide, setOnlySlide] = useState(false);
  const [picked, setPicked] = useState<Record<string, string>>({});    // находка -> выбранное исправление

  // новая ревизия — старые отметки теряют смысл (находки пересчитаны)
  useEffect(() => { setPicked({}); }, [audit?.rev, audit?.contextual]);

  const issues = useMemo(() => {
    const list = audit?.issues ?? [];
    const shown = onlySlide ? list.filter((i) => i.slide === slide + 1) : list;
    return [...shown].sort((a, b) => a.slide - b.slide
      || (a.severity === b.severity ? 0 : a.severity === 'error' ? -1 : 1));
  }, [audit, onlySlide, slide]);

  const fixable = issues.filter((i) => i.fixes.length);
  const chosen = Object.entries(picked).map(([issue, fix]) => ({ issue, fix }));
  const toggle = (i: AuditIssue) => setPicked((p) => {
    const next = { ...p };
    if (next[i.id]) delete next[i.id]; else next[i.id] = i.fixes[0].id;
    return next;
  });
  const allPicked = fixable.length > 0 && fixable.every((i) => picked[i.id]);
  const pickAll = () => setPicked(allPicked ? {} : Object.fromEntries(fixable.map((i) => [i.id, i.fixes[0].id])));

  const s = audit?.summary;
  const ctxLabel = busy === 'ctx' ? 'модель смотрит…'
    : audit?.contextual === 'done' ? 'смысл проверен ↻'
      : audit?.contextual === 'stale' ? 'смысл: проверить снова' : 'проверить смысл';

  return (
    <aside className="audit" aria-label="Аудит слайдов">
      <header className="audit__head">
        <b className="audit__title">Аудит</b>
        {s && (
          <span className="audit__sum">
            <i className="audit__dot audit__dot--error" />{s.errors} {plural(s.errors, 'ошибка', 'ошибки', 'ошибок')}
            <i className="audit__dot audit__dot--warning" />{s.warnings} {plural(s.warnings, 'замечание', 'замечания', 'замечаний')}
          </span>
        )}
      </header>

      <div className="audit__tools">
        <button type="button" className={onlySlide ? 'chip is-on' : 'chip'} onClick={() => setOnlySlide((v) => !v)}>
          {onlySlide ? `слайд ${slide + 1}` : 'все слайды'}
        </button>
        <button type="button" className={busy === 'ctx' ? 'chip chip--ai is-busy' : 'chip chip--ai'}
                disabled={busy !== null} onClick={onContextual}
                title="Модель смотрит на каждый слайд: вывод в заголовке, факты из источников, мусор, опечатки">
          {ctxLabel}
        </button>
      </div>

      <div className="audit__list" role="list">
        {busy === 'load' && <p className="audit__empty">проверяем…</p>}
        {error && <p className="audit__empty audit__empty--err">{error}</p>}
        {busy !== 'load' && !error && issues.length === 0 && (
          <p className="audit__empty">{onlySlide ? 'на этом слайде чисто' : 'находок нет'}</p>
        )}
        {issues.map((i) => {
          const fix = picked[i.id];
          return (
            <div key={i.id} role="listitem"
                 className={`audit__item audit__item--${i.severity}${focus === i.id ? ' is-focus' : ''}${fix ? ' is-picked' : ''}`}>
              <button type="button" className="audit__check" disabled={!i.fixes.length}
                      aria-pressed={Boolean(fix)} aria-label={fix ? 'не исправлять' : 'исправить'}
                      title={i.fixes.length ? undefined : 'автоматического исправления нет — поправьте в PowerPoint'}
                      onClick={() => toggle(i)} />
              <button type="button" className="audit__body" onClick={() => onPick(i)}>
                <span className="audit__meta">
                  <b>сл. {i.slide}</b>
                  <em className={i.mode === 'contextual' ? 'audit__tag audit__tag--ai' : 'audit__tag'}>
                    {GROUP[i.group]}{i.mode === 'contextual' ? ' · модель' : ''}
                  </em>
                </span>
                <span className="audit__msg">{i.message}</span>
              </button>
              {fix && i.fixes.length > 1 && (
                <span className="audit__fixes">
                  {i.fixes.map((f) => (
                    <button key={f.id} type="button" className={f.id === fix ? 'chip is-on' : 'chip'}
                            onClick={() => setPicked((p) => ({ ...p, [i.id]: f.id }))}>{f.label}</button>
                  ))}
                </span>
              )}
              {fix && i.fixes.length === 1 && <span className="audit__fix1">{i.fixes[0].label}</span>}
            </div>
          );
        })}
      </div>

      <footer className="audit__foot">
        {note && <p className="audit__note" role="status">{note}</p>}
        <div className="audit__actions">
          <button type="button" className="chip" disabled={!fixable.length || busy !== null} onClick={pickAll}>
            {allPicked ? 'снять все' : 'отметить все'}
          </button>
          <button type="button" className="chip chip--go audit__go" disabled={!chosen.length || busy !== null}
                  onClick={() => onFix(chosen)}>
            {busy === 'fix' ? 'исправляем…' : `Исправить${chosen.length ? ` (${chosen.length})` : ''}`}
          </button>
        </div>
      </footer>
    </aside>
  );
}
