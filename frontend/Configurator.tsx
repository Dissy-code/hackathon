/* ─────────────────────────────────────────────────────────────
   Конфигуратор ЦДС — то же самое, что configurator.html + .js,
   собранное одним React-компонентом.
   ─────────────────────────────────────────────────────────────
   Три состояния одного окна:

     edit  — редактор: шаблоны, картинки и промт для нейросети
     gen   — генерация: надпись ЦДС закрашивается по диагонали,
             под ней меняются слова, точки бегут
     show  — демонстрация: лента миниатюр и холст

   Разметка и состояния живут в React, а сетка на фоне, кольцо
   курсора и прогресс генерации — в эффектах: это покадровая
   работа, и гонять её через setState на каждый кадр незачем.

   Стили берутся из того же configurator.css, что и у статической
   версии, — дублировать их не нужно.
   ───────────────────────────────────────────────────────────── */
import {
  useCallback, useEffect, useLayoutEffect, useRef, useState,
  type ChangeEvent, type CSSProperties, type PointerEvent as ReactPointerEvent,
} from 'react';
import './configurator.css';

/* ── Константы композиции ── */
const CW = 750;            // ширина композиции
const CH = 422;            // высота композиции
const GRID_CELL = 24;      // шаг сетки в px композиции
const MAX = 3;             // максимум вложений каждого вида
const NAV_X = [4.6, 182.8] as const;   // левые края режимов в px композиции
const LENS_HALF = 89.4;                // половина ширины линзы
const GEN_MS = 7200;                   // сколько идёт «генерация»

/* Качество: если кадры начинают опаздывать, опускаемся на уровень ниже. */
const QUAL = [
  { glow: 0.6, blur: 16, bg: 40 },
  { glow: 0.5, blur: 12, bg: 55 },
  { glow: 0.4, blur: 9, bg: 70 },
  { glow: 0.35, blur: 6, bg: 90 },
] as const;

const WORDS = ['читаем задачу', 'подбираем макеты', 'пишем текст',
  'раскладываем слайды', 'наводим красоту'] as const;

const LOOKS = ['a', 'b', 'c'] as const;
type Look = typeof LOOKS[number];

type View = 'edit' | 'gen' | 'show';
type Kind = 'tpl' | 'img';
type Attachment = { name: string; url?: string };

const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));

/* Карты смещения для #lensGlass: красный канал — профиль по X, зелёный — по Y.
   Профиль линзовый: смещение тянет фон к центру тем сильнее, чем дальше от
   него точка, у самой кромки нарастает резко. */
const LENS_STOPS = [255, 221, 197, 180, 168, 159, 152, 145, 139, 134, 128,
  122, 117, 111, 104, 97, 88, 76, 59, 35, 1];

function mapUri(vertical: boolean): string {
  const stops = LENS_STOPS.map((v, i) => {
    const c = vertical ? `rgb(0,${v},0)` : `rgb(${v},0,0)`;
    return `<stop offset='${i / (LENS_STOPS.length - 1)}' stop-color='${c}'/>`;
  }).join('');
  const [x2, y2] = vertical ? ['0', '1'] : ['1', '0'];
  const svg =
    `<svg xmlns='http://www.w3.org/2000/svg' width='200' height='120'><defs>` +
    `<linearGradient id='g' x1='0' y1='0' x2='${x2}' y2='${y2}'>${stops}</linearGradient>` +
    `</defs><rect width='200' height='120' fill='url(#g)'/></svg>`;
  return 'data:image/svg+xml,' + svg
    .replace(/</g, '%3C').replace(/>/g, '%3E').replace(/#/g, '%23');
}
const MAP_X = mapUri(false);
const MAP_Y = mapUri(true);

/* ── Мелкие куски разметки ── */
function TplMini({ look }: { look: Look }) {
  return (
    <span className="tpl-mini">
      <i className="t-bar" /><i className="t-box" />
      <i className="t-line" /><i className="t-line short" />
      {look === 'b' && <i className="t-line" />}
    </span>
  );
}

function DeckSlide({ look }: { look: Look }) {
  return (
    <span className={`deck-slide deck-slide--${look}`}>
      <i className="d-bar" />
      <b className="d-box" />
      {look === 'b' && <b className="d-box2" />}
      <i className="d-line" /><i className="d-line short" />
      {look === 'c' && <b className="d-num">+38 %</b>}
    </span>
  );
}

/* Ячейки вложений: пока есть свободное место, крайняя левая — «добавить»,
   остальные свободные стоят пустыми, как в макете. */
function Slots({ kind, list, onAdd, onDrop }: {
  kind: Kind;
  list: Attachment[];
  onAdd: () => void;
  onDrop: (at: number) => void;
}) {
  const cells: (Attachment | null | undefined)[] = list.length < MAX ? [null, ...list] : [...list];
  while (cells.length < MAX) cells.push(undefined);

  return (
    <div className={kind === 'img' ? 'slots slots--img' : 'slots'}>
      {cells.map((item, i) => {
        if (item === null) {
          return (
            <button
              key="add"
              type="button"
              className="slot slot--add"
              aria-label={kind === 'tpl' ? 'Добавить шаблон' : 'Добавить картинку'}
              onClick={onAdd}
            />
          );
        }
        if (!item) return <div key={`empty-${i}`} className="slot" />;
        const at = list.indexOf(item);
        return (
          <div key={`${item.name}-${at}`} className="slot">
            {kind === 'img' && item.url
              ? <img className="slot__img" src={item.url} alt="" />
              : <TplMini look={LOOKS[at % LOOKS.length]} />}
            <span className="slot__name">{item.name}</span>
            <button
              type="button"
              className="slot__kill"
              aria-label={`Убрать ${item.name}`}
              onClick={() => onDrop(at)}
            />
          </div>
        );
      })}
    </div>
  );
}

/* ═══════════════════ Компонент ═══════════════════ */
export default function Configurator() {
  const sceneRef = useRef<HTMLDivElement>(null);
  const vortexRef = useRef<HTMLCanvasElement>(null);
  const navRef = useRef<HTMLElement>(null);
  const lensRef = useRef<HTMLElement>(null);
  const curRef = useRef<HTMLDivElement>(null);
  const curDotRef = useRef<HTMLDivElement>(null);
  const genLogoRef = useRef<HTMLDivElement>(null);
  const genWordRef = useRef<HTMLSpanElement>(null);
  const genCaretRef = useRef<HTMLElement>(null);
  const tplInputRef = useRef<HTMLInputElement>(null);
  const imgInputRef = useRef<HTMLInputElement>(null);

  const [view, setView] = useState<View>('edit');
  const [navAt, setNavAt] = useState(0);
  const [accOpen, setAccOpen] = useState(false);
  const [tpl, setTpl] = useState<Attachment[]>([]);
  const [img, setImg] = useState<Attachment[]>([]);
  const [slide, setSlide] = useState(0);
  const deckReady = useRef(false);

  /* Размер сцены нужен и обработчикам, и кадровому циклу — держим в ref. */
  const geom = useRef({ S: 1, W: 0, H: 0 });
  const dragRef = useRef<{ id: number; left: number; moved: boolean; x0: number } | null>(null);

  /* ══════════ Масштаб сцены и фон ══════════ */
  useLayoutEffect(() => {
    const scene = sceneRef.current, vortex = vortexRef.current;
    if (!scene || !vortex) return;

    const DPR = Math.min(2, devicePixelRatio || 1);
    const params = new URLSearchParams(location.search);
    const qLock = params.has('q');
    let qi = clamp(+(params.get('q') ?? 0) || 0, 0, QUAL.length - 1);

    const gctx = vortex.getContext('2d')!;
    const base = document.createElement('canvas');      // готовая белая сетка
    const bctx = base.getContext('2d')!;
    const glow = document.createElement('canvas');      // оранжевый слой под маску
    const glctx = glow.getContext('2d')!;
    const spot = { x: -1e4, y: -1e4, k: 0 };
    const mouse = { x: 0, y: 0 };
    let gridPath = new Path2D();
    let gridW = -1, gridH = -1;
    let curX = 0, curY = 0, curScale = 1, shown = false;

    const applyQuality = () =>
      document.documentElement.style.setProperty('--win-blur', `${QUAL[qi].blur}px`);

    function layout() {
      const W = innerWidth, H = innerHeight;
      const S = Math.min(W / CW, H / CH);
      geom.current = { S, W, H };
      scene!.style.transform =
        `translate(${(W - CW * S) / 2}px, ${(H - CH * S) / 2}px) scale(${S})`;
      const cw = Math.round(W * DPR), ch = Math.round(H * DPR);
      if (vortex!.width !== cw || vortex!.height !== ch) { vortex!.width = cw; vortex!.height = ch; }
    }

    function buildGrid() {
      const { S, W, H } = geom.current;
      const cw = Math.round(W * DPR), ch = Math.round(H * DPR);
      if (base.width !== cw || base.height !== ch) { base.width = cw; base.height = ch; }
      const cell = GRID_CELL * S;
      const path = new Path2D();
      for (let x = (W / 2) % cell - cell; x < W + cell; x += cell) { path.moveTo(x, -cell); path.lineTo(x, H + cell); }
      for (let y = (H / 2) % cell - cell; y < H + cell; y += cell) { path.moveTo(-cell, y); path.lineTo(W + cell, y); }
      gridPath = path;
      bctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      bctx.clearRect(0, 0, W, H);
      bctx.lineWidth = 1.2;
      bctx.strokeStyle = 'rgba(255,255,255,.42)';
      bctx.stroke(path);
    }

    /* Статичная сетка копируется целиком, а подсветка рисуется только в
       рамке пятна и маскируется радиальным градиентом. */
    function drawGrid(time: number) {
      const { W, H } = geom.current;
      if (gridW !== W || gridH !== H) { gridW = W; gridH = H; buildGrid(); }

      let tx = mouse.x, ty = mouse.y, want = shown ? 1 : 0;
      if (!shown) {                                     // без курсора пятно плывёт само
        tx = W / 2 + Math.sin(time * 0.5) * W * 0.32;
        ty = H / 2 + Math.cos(time * 0.67) * H * 0.28;
        want = 0.7;
      }
      if (spot.x < -1e3) { spot.x = tx; spot.y = ty; }
      spot.x += (tx - spot.x) * 0.2;
      spot.y += (ty - spot.y) * 0.2;
      spot.k += (want - spot.k) * 0.15;

      gctx.setTransform(1, 0, 0, 1, 0, 0);
      gctx.clearRect(0, 0, vortex!.width, vortex!.height);
      gctx.drawImage(base, 0, 0);
      gctx.setTransform(DPR, 0, 0, DPR, 0, 0);

      if (spot.k <= 0.02) return;
      const gs = QUAL[qi].glow;
      const gw = Math.round(W * DPR * gs), gh = Math.round(H * DPR * gs);
      if (glow.width !== gw || glow.height !== gh) { glow.width = gw; glow.height = gh; }

      const rad = Math.min(W, H) * 0.24 * 1.15;
      const bl = Math.max(0, spot.x - rad), bt = Math.max(0, spot.y - rad);
      const bw = Math.min(W, spot.x + rad) - bl, bh = Math.min(H, spot.y + rad) - bt;
      if (bw <= 1 || bh <= 1) return;

      glctx.setTransform(DPR * gs, 0, 0, DPR * gs, 0, 0);
      glctx.globalCompositeOperation = 'source-over';
      glctx.clearRect(bl, bt, bw, bh);
      glctx.save();
      glctx.beginPath(); glctx.rect(bl, bt, bw, bh); glctx.clip();
      glctx.lineWidth = 6; glctx.strokeStyle = 'rgba(190,97,18,.5)';  // мягкий ореол
      glctx.stroke(gridPath);
      glctx.lineWidth = 1.8; glctx.strokeStyle = '#ffa04a';           // яркая сердцевина
      glctx.stroke(gridPath);
      const mask = glctx.createRadialGradient(spot.x, spot.y, 0, spot.x, spot.y, rad);
      mask.addColorStop(0, `rgba(0,0,0,${spot.k})`);
      mask.addColorStop(0.5, `rgba(0,0,0,${0.62 * spot.k})`);
      mask.addColorStop(1, 'rgba(0,0,0,0)');
      glctx.globalCompositeOperation = 'destination-in';
      glctx.fillStyle = mask; glctx.fillRect(bl, bt, bw, bh);
      glctx.restore();
      const k = DPR * gs;
      gctx.drawImage(glow, bl * k, bt * k, bw * k, bh * k, bl, bt, bw, bh);
    }

    /* ── Свой курсор: точка под указателем, кольцо догоняет в кадре ── */
    const fine = matchMedia('(hover: hover) and (pointer: fine)').matches;
    let mouseTs = -1e4;

    const onMove = (e: MouseEvent) => {
      mouse.x = e.clientX; mouse.y = e.clientY; mouseTs = performance.now();
      if (!fine) return;
      curDotRef.current!.style.transform = `translate3d(${e.clientX}px, ${e.clientY}px, 0)`;
      if (!shown) {
        shown = true; curX = e.clientX; curY = e.clientY;
        curRef.current!.classList.add('is-vis');
        curDotRef.current!.classList.add('is-vis');
      }
    };
    const onLeave = () => {
      shown = false;
      curRef.current?.classList.remove('is-vis');
      curDotRef.current?.classList.remove('is-vis');
    };
    const onOver = (e: MouseEvent) => {
      const t = e.target as Element | null;
      const hot = !!t?.closest?.('a, button, .slot, .thumb, textarea');
      curRef.current?.classList.toggle('is-on', hot);
    };

    if (fine) document.documentElement.classList.add('has-cur');
    addEventListener('mousemove', onMove, { passive: true });
    document.addEventListener('mouseleave', onLeave);
    document.addEventListener('mouseover', onOver, { passive: true });
    addEventListener('resize', layout);

    applyQuality();
    layout();

    /* ── Кадровый цикл ── */
    let raf = 0, bgTs = 0, prevTs = performance.now(), fpsEma = 16.7, slow = 0, fast = 0;
    const frame = (now: number) => {
      if (fine && shown) {
        curX += (mouse.x - curX) * 0.2;
        curY += (mouse.y - curY) * 0.2;
        const want = curRef.current?.classList.contains('is-on') ? 1.5 : 1;
        curScale += (want - curScale) * 0.15;
        curRef.current!.style.transform =
          `translate3d(${curX}px, ${curY}px, 0) scale(${curScale})`;
      }

      /* Окно — большое стекло: каждая перерисовка сетки заставляет браузер
         заново размывать его фон. Пока курсор стоит, сбавляем темп. */
      const gap = now - mouseTs > 700 ? QUAL[qi].bg : 0;
      if (now - bgTs >= gap) { bgTs = now; drawGrid(now / 1000); }

      /* Меряем интервал между кадрами, а не время работы JS: размытие
         ложится на GPU уже после колбэка. 16,7 мс — это 60 кадров/с. */
      if (!qLock) {
        const dt = now - prevTs; prevTs = now;
        if (dt > 4 && dt < 200) fpsEma += (dt - fpsEma) * 0.05;
        if (fpsEma > 21 && qi < QUAL.length - 1) {
          if (++slow > 40) { qi++; slow = fast = 0; applyQuality(); }
        } else slow = 0;
        if (fpsEma < 14 && qi > 0) {
          if (++fast > 360) { qi--; slow = fast = 0; applyQuality(); }
        } else fast = 0;
      }
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      removeEventListener('mousemove', onMove);
      removeEventListener('resize', layout);
      document.removeEventListener('mouseleave', onLeave);
      document.removeEventListener('mouseover', onOver);
      document.documentElement.classList.remove('has-cur');
    };
  }, []);

  /* ══════════ Генерация ══════════
     Прогресс закрашивает надпись ЦДС по диагонали, слова печатаются по букве
     и сменяют друг друга. Обе величины пишем прямо в узлы: гонять их через
     setState по шестьдесят раз в секунду незачем. */
  useEffect(() => {
    if (view !== 'gen') return;
    const logo = genLogoRef.current, word = genWordRef.current, caret = genCaretRef.current;
    if (!logo || !word || !caret) return;

    const t0 = performance.now();
    let raf = 0, at = -1, wordT0 = 0, done = false;

    const step = (now: number) => {
      const t = clamp((now - t0) / GEN_MS, 0, 1);
      // лёгкая неравномерность — как у настоящей очереди задач
      const eased = t < 1 ? clamp(t + Math.sin(t * 7.3) * 0.035, 0, 1) : 1;
      logo.style.setProperty('--fill', `${(eased * 100).toFixed(1)}%`);

      const next = Math.min(WORDS.length - 1, Math.floor(t * WORDS.length));
      if (next !== at) { at = next; wordT0 = now; }
      const text = WORDS[at];
      const cut = Math.min(text.length, Math.floor((now - wordT0) / 34));
      const shown = text.slice(0, cut);
      if (word.textContent !== shown) word.textContent = shown;
      caret.style.opacity = cut < text.length ? '1' : '0';

      if (t < 1) { raf = requestAnimationFrame(step); return; }
      done = true;
      word.textContent = 'готово';
      caret.style.opacity = '0';
      deckReady.current = true;
      raf = window.setTimeout(() => setView('show'), 620) as unknown as number;
    };

    logo.style.setProperty('--fill', '0%');
    word.textContent = '';
    caret.style.opacity = '1';
    raf = requestAnimationFrame(step);

    return () => { if (done) clearTimeout(raf); else cancelAnimationFrame(raf); };
  }, [view]);

  /* ══════════ Режимы ══════════ */
  const startGen = useCallback(() => {
    setAccOpen(false);
    deckReady.current = false;
    setNavAt(1);
    setView('gen');
  }, []);

  const go = useCallback((target: View) => {
    if (target === 'edit') { setNavAt(0); setView('edit'); return; }
    setNavAt(1);
    if (deckReady.current) setView('show');
    else startGen();
  }, [startGen]);

  /* Линзу можно не только нажать, но и протащить: пока держат — она идёт за
     рукой, на отпускании притягивается к ближайшему режиму. */
  const onNavDown = (e: ReactPointerEvent<HTMLElement>) => {
    const nav = navRef.current;
    if (!nav) return;
    dragRef.current = {
      id: e.pointerId, left: nav.getBoundingClientRect().left, moved: false, x0: e.clientX,
    };
    lensRef.current?.classList.add('is-drag');
    try { nav.setPointerCapture(e.pointerId); } catch { /* захват не обязателен */ }
  };

  const onNavMove = (e: ReactPointerEvent<HTMLElement>) => {
    const d = dragRef.current;
    if (!d || e.pointerId !== d.id || Math.abs(e.clientX - d.x0) < 3) return;
    d.moved = true;
    const cx = (e.clientX - d.left) / geom.current.S;
    const x = clamp(cx - LENS_HALF, NAV_X[0], NAV_X[1]);
    lensRef.current!.style.transform = `translateX(${(x - NAV_X[0]).toFixed(1)}px) scaleX(1.05)`;
  };

  const onNavUp = (e: ReactPointerEvent<HTMLElement>) => {
    const d = dragRef.current;
    if (!d || e.pointerId !== d.id) return;
    dragRef.current = null;
    const lens = lensRef.current;
    lens?.classList.remove('is-drag');
    void lens?.offsetWidth;                            // иначе возврат не анимируется
    if (!d.moved) return;                              // это был клик, его обработает onClick
    const cx = (e.clientX - d.left) / geom.current.S;
    const i = cx < (NAV_X[0] + NAV_X[1]) / 2 + LENS_HALF ? 0 : 1;
    // ставим ровно то же значение, что отрисует React, — иначе при неизменном
    // navAt перерисовки не будет и линза останется там, где её отпустили
    if (lens) lens.style.transform = `translateX(${(NAV_X[i] - NAV_X[0]).toFixed(1)}px) scaleX(1)`;
    go(i === 0 ? 'edit' : 'show');
  };

  /* ══════════ Аккаунт ══════════ */
  useEffect(() => {
    if (!accOpen) return;
    const away = (e: Event) => {
      const t = e.target as Element | null;
      if (t?.closest?.('.acc-menu, .avatar')) return;
      setAccOpen(false);
    };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setAccOpen(false); };
    document.addEventListener('pointerdown', away);
    addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('pointerdown', away);
      removeEventListener('keydown', esc);
    };
  }, [accOpen]);

  /* ══════════ Вложения ══════════ */
  const take = (kind: Kind) => (e: ChangeEvent<HTMLInputElement>) => {
    const files = [...(e.target.files ?? [])];
    const set = kind === 'tpl' ? setTpl : setImg;
    set((list) => [
      ...list,
      ...files.slice(0, MAX - list.length).map((f) =>
        kind === 'img' ? { name: f.name, url: URL.createObjectURL(f) } : { name: f.name }),
    ]);
    e.target.value = '';                               // чтобы тот же файл можно было выбрать снова
  };

  const drop = (kind: Kind) => (at: number) => {
    const set = kind === 'tpl' ? setTpl : setImg;
    set((list) => {
      const gone = list[at];
      if (gone?.url) URL.revokeObjectURL(gone.url);
      return list.filter((_, i) => i !== at);
    });
  };

  /* Ссылки на картинки отпускаем только при размонтировании: держим список
     в ref, иначе очистка сработала бы на каждом добавлении и убила бы
     превью, которые ещё показываются. */
  const imgLive = useRef(img);
  imgLive.current = img;
  useEffect(() => () => {
    imgLive.current.forEach((i) => { if (i.url) URL.revokeObjectURL(i.url); });
  }, []);

  const lensStyle: CSSProperties = {
    transform: `translateX(${(NAV_X[navAt] - NAV_X[0]).toFixed(1)}px) scaleX(1)`,
  };

  return (
    <>
      <div className="stage">
        {/* живая сетка — фон страницы, как на главной */}
        <canvas className="gridc" ref={vortexRef} aria-hidden="true" />

        <div className={accOpen ? 'scene is-acc' : 'scene'} ref={sceneRef}>

          <header className="top">
            <a className="logo" href="index.html" aria-label="На главную">ЦДС</a>

            <nav
              className="nav glass"
              ref={navRef}
              aria-label="Режимы"
              onPointerDown={onNavDown}
              onPointerMove={onNavMove}
              onPointerUp={onNavUp}
            >
              <i className="nav__lens" ref={lensRef} style={lensStyle} aria-hidden="true" />
              <button
                type="button"
                className={navAt === 0 ? 'nav__item is-active' : 'nav__item'}
                onClick={() => go('edit')}
              >Редактор</button>
              <button
                type="button"
                className={navAt === 1 ? 'nav__item is-active' : 'nav__item'}
                onClick={() => go('show')}
              >Демонстрация</button>
            </nav>

            <button
              className="avatar"
              type="button"
              aria-label="Аккаунт"
              aria-expanded={accOpen}
              aria-controls="accMenu"
              onClick={() => setAccOpen((v) => !v)}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round">
                <circle cx="12" cy="8.6" r="3.6" />
                <path d="M4.6 20c1.3-3.6 4-5.4 7.4-5.4s6.1 1.8 7.4 5.4" />
              </svg>
            </button>
          </header>

          {/* ══ Аккаунт ══
              Кружок иконки и тело панели лежат в одном слое под фильтром
              #gooGlass и слипаются в одну каплю; тело растёт из центра
              кружка. Когда форма собралась, её подменяет настоящее стекло. */}
          <div className="acc-goo" aria-hidden="true">
            <i className="acc-ball" />
            <i className="acc-body" />
          </div>
          <i className="acc-glass" aria-hidden="true" />
          <div className="acc-menu" id="accMenu" role="dialog" aria-label="Аккаунт">
            <p className="acc-name">Гость</p>
            <p className="acc-mail">войдите, чтобы сохранять колоды</p>
            <ul className="acc-list">
              <li><span>Мои презентации</span><b>12</b></li>
              <li><span>Мои шаблоны</span><b>3</b></li>
              <li><span>Формат по умолчанию</span><b>pptx</b></li>
            </ul>
            <button className="btn acc-cta" type="button">Войти</button>
          </div>

          {/* ══ Окно ══ */}
          <section className="win">
            <div className="win__bar">
              <i className="win__dot win__dot--r" />
              <i className="win__dot win__dot--y" />
              <i className="win__dot win__dot--g" />
            </div>

            <div className="win__body">

              {/* ── Состояние 1: редактор ── */}
              <div className={`view view--edit${view === 'edit' ? ' is-on' : ''}`}>
                <div className="pane pane--tpl">
                  <Slots
                    kind="tpl"
                    list={tpl}
                    onAdd={() => tplInputRef.current?.click()}
                    onDrop={drop('tpl')}
                  />
                </div>

                <div className="pane pane--img">
                  <Slots
                    kind="img"
                    list={img}
                    onAdd={() => imgInputRef.current?.click()}
                    onDrop={drop('img')}
                  />
                </div>

                <div className="pane pane--prompt">
                  <textarea
                    className="prompt"
                    spellCheck={false}
                    placeholder="Опиши задачу: тема и аудитория, тон, сколько слайдов, что обязательно должно быть внутри. Чем конкретнее — тем ближе результат."
                    onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') startGen(); }}
                  />
                  <button className="btn go" type="button" onClick={startGen}>Сгенерировать</button>
                </div>
              </div>

              {/* ── Состояние 2: генерация ── */}
              <div className={`view view--gen${view === 'gen' ? ' is-on' : ''}`}>
                <div className="pane pane--gen">
                  <div className="gen">
                    <div className="gen__logo" ref={genLogoRef}>ЦДС</div>
                    <div className="gen__word">
                      <span ref={genWordRef} />
                      <i className="gen__caret" ref={genCaretRef} />
                      <i className="gen__dots"><b /><b /><b /></i>
                    </div>
                  </div>
                </div>
              </div>

              {/* ── Состояние 3: демонстрация ── */}
              <div className={`view view--show${view === 'show' ? ' is-on' : ''}`}>
                <div className="rail">
                  {LOOKS.map((look, i) => (
                    <button
                      key={look}
                      type="button"
                      className={i === slide ? 'thumb is-on' : 'thumb'}
                      style={{ '--k': 0.205 } as CSSProperties}
                      aria-label={`Слайд ${i + 1}`}
                      onClick={() => setSlide(i)}
                    >
                      <DeckSlide look={look} />
                    </button>
                  ))}
                </div>
                <div className="canvas" style={{ '--k': 1 } as CSSProperties}>
                  <DeckSlide look={LOOKS[slide]} />
                </div>
              </div>

            </div>
          </section>
        </div>
      </div>

      {/* выбор файлов — скрытые поля, их открывают ячейки «добавить» */}
      <input ref={tplInputRef} type="file" accept=".pptx,.potx,.pdf,.key" multiple
             hidden onChange={take('tpl')} />
      <input ref={imgInputRef} type="file" accept="image/*" multiple
             hidden onChange={take('img')} />

      {/* свой курсор: кольцо с отставанием и точка ровно под указателем */}
      <div className="cur" ref={curRef} aria-hidden="true" />
      <div className="cur__dot" ref={curDotRef} aria-hidden="true" />

      {/* ══ Преломление стекла ══
          Карта смещения: красный канал — профиль по X, зелёный — по Y, 128
          значит «не трогать». Синий держим на 128 и подставляем в ту ось,
          которую проход двигать не должен, — так у горизонтали и вертикали
          независимые амплитуды. */}
      <svg className="fx" width="0" height="0" aria-hidden="true" focusable="false">
        <filter id="lensGlass" filterUnits="objectBoundingBox" x="0" y="0" width="1" height="1"
                primitiveUnits="userSpaceOnUse" colorInterpolationFilters="sRGB">
          <feImage href={MAP_X} preserveAspectRatio="none" result="mx" />
          <feImage href={MAP_Y} preserveAspectRatio="none" result="my" />
          <feBlend in="mx" in2="my" mode="screen" result="mxy" />
          <feColorMatrix in="mxy" result="map"
                         values="1 0 0 0 0  0 1 0 0 0  0 0 0 0 .5  0 0 0 0 1" />

          {/* горизонталь: три амплитуды дают хроматическую кайму по краю */}
          <feDisplacementMap in="SourceGraphic" in2="map" scale="25" xChannelSelector="R" yChannelSelector="B" result="hr" />
          <feDisplacementMap in="SourceGraphic" in2="map" scale="22" xChannelSelector="R" yChannelSelector="B" result="hg" />
          <feDisplacementMap in="SourceGraphic" in2="map" scale="19.4" xChannelSelector="R" yChannelSelector="B" result="hb" />
          <feColorMatrix in="hr" result="cr" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" />
          <feColorMatrix in="hg" result="cg" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" />
          <feColorMatrix in="hb" result="cb" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" />
          <feBlend in="cr" in2="cg" mode="screen" result="crg" />
          <feBlend in="crg" in2="cb" mode="screen" result="rgb" />

          {/* вертикаль: своя амплитуда */}
          <feDisplacementMap in="rgb" in2="map" scale="9" xChannelSelector="B" yChannelSelector="G" />
        </filter>

        {/* слипание фигур: размываем слой и режем по порогу — на стыке
            получается перетяжка, как у капли; второй порог даёт кромку */}
        <filter id="gooGlass" x="-30%" y="-30%" width="160%" height="160%" colorInterpolationFilters="sRGB">
          <feGaussianBlur in="SourceGraphic" stdDeviation="6" result="b" />
          <feColorMatrix in="b" result="solid"
                         values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 24 -11" />
          <feColorMatrix in="b" result="core"
                         values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 24 -12.7" />
          <feComposite in="solid" in2="core" operator="out" result="edge" />
          <feFlood floodColor="#ffffff" floodOpacity=".7" result="lite" />
          <feComposite in="lite" in2="edge" operator="in" result="rim" />
          {/* заливку приглушаем до полупрозрачной: панель обязана выглядеть
              стеклом с первого кадра. Кромку не трогаем */}
          <feColorMatrix in="solid" result="soft"
                         values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 .45 0" />
          <feMerge><feMergeNode in="soft" /><feMergeNode in="rim" /></feMerge>
        </filter>
      </svg>
    </>
  );
}
