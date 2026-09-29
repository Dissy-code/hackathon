/* ─────────────────────────────────────────────────────────────
   Цифровой дизайнер слайдов — ознакомительный сайт
   ─────────────────────────────────────────────────────────────
   Всё рисуется в координатах композиции 750×422 (.scene), которая
   масштабируется под окно. Сайт листается по слайдам (колесо,
   свайп, стрелки, точки справа):

     p = 0   главный экран: вкладка конфигуратора и три блока
     p = 1   лента шаблонов
     p = 2   бриф → структура → колода слайдов
     p = 3   три варианта вёрстки
     p = 4   экспорт: «Затем импортируй…»
     p = 5   финал — бейдж «Перейти в конфигуратор»

   Анимации внутри слайдов проигрываются один раз за вход и остаются
   в конечном состоянии; отсчёт ведётся от момента смены слайда.

   Лента шаблонов идёт по прямой снизу вверх (вверх-вправо), не
   останавливается и стоит на одном месте всю дорогу; на первом
   слайде она скрыта и проявляется, когда панель уезжает вверх.

   Окно выбора на 4-м слайде ходит по карточкам без остановки.

   Для отладки: ?p=1.4 фиксирует прогресс, ?tl=6 — локальное время слайда,
   ?vh=0…1 — фазу перелёта окна выбора.
   ───────────────────────────────────────────────────────────── */
(() => {
  'use strict';

  /* ── Лента шаблонов: карточки клонируются из макетов #cardTpls по кругу,
     столько, чтобы цепочка покрывала видимый участок без разрывов ── */
  const CARD_MAX = 24;

  const CW = 750, CH = 422;                 // размер композиции

  /* ── Ось ленты: диагональ снизу вверх (вверх-вправо), dx/dy = -0.75 ── */
  const AXIS = [-0.6, 0.8];                 // единичный вектор оси (вниз-влево)
  const CARD_ROT = 36.87;                   // поворот карточки вдоль оси
  const LINE_PT = [393, 211];               // опорная точка ленты (y = 211 — центр композиции)

  const SPEED_PX    = 150;    // скорость ленты, px композиции в секунду
  const CARD_GAP    = 160;    // шаг между карточками вдоль оси: высота 152 + зазор ≈ 5 мм
  const VIS_MARGIN  = 260;    // запас за краем окна, чтобы карточка входила/выходила целиком
  const SLAB_IN     = 620;    // с какого расстояния вдоль оси въезжают блоки
  const GRID_CELL   = 24;     // шаг сетки в px композиции (мельче — больше клеток)
  const SLIDES      = 6;
  const SLIDE_MS    = 1600;   // длительность перехода между слайдами

  /* ── Качество ──
     Сайт сам подстраивается под машину: если кадры начинают опаздывать,
     опускается на уровень ниже (реже сэмплы сетки, меньше разрешение
     подсветки и размытие стекла), на быстрой — возвращается наверх.
     Принудительно: ?q=0…3. */
  const QUAL = [
    { glow: 0.6,  blur: 16, bg: 40 },
    { glow: 0.5,  blur: 12, bg: 55 },
    { glow: 0.4,  blur: 9,  bg: 70 },
    { glow: 0.35, blur: 6,  bg: 90 },
  ];
  let qi = Math.min(QUAL.length - 1, Math.max(0, +new URLSearchParams(location.search).get('q') || 0));
  const qLock = new URLSearchParams(location.search).has('q');
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const lerp  = (a, b, t) => a + (b - a) * t;
  const ease  = (t) => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };   // easy ease AE (33 %)

  /* ── Прямая лента ──
     Положение карточки задаётся расстоянием d вдоль оси DIR от центра линии.
     Видимый диапазон d считаем по углам окна, чтобы карточек было ровно
     столько, сколько нужно. */
  function linePoint(cx, cy, d) {
    return [cx + AXIS[0] * d, cy + AXIS[1] * d];
  }
  function visibleRange(cx, cy) {
    const x0 = -OX / S - VIS_MARGIN, x1 = (W - OX) / S + VIS_MARGIN;
    const y0 = -OY / S - VIS_MARGIN, y1 = (H - OY) / S + VIS_MARGIN;
    let lo = Infinity, hi = -Infinity;
    for (const [x, y] of [[x0, y0], [x1, y0], [x0, y1], [x1, y1]]) {
      const d = (x - cx) * AXIS[0] + (y - cy) * AXIS[1];           // проекция угла на ось
      lo = Math.min(lo, d); hi = Math.max(hi, d);
    }
    return [lo, hi];
  }

  /* ── DOM ── */
  const $ = (id) => document.getElementById(id);
  const sceneIntro = $('sceneIntro'), sceneUi = $('sceneUi');
  const stream = $('stream'), finalBlock = $('final'), dots = $('dots'), hint = $('hint');
  const vortex = $('vortex'), badge = $('badge');
  const sc1 = $('sc1'), sc2 = $('sc2'), sc3 = $('sc3');
  const tab = $('tab'), tabBar = $('tabBar'), tabLive = $('tabLive');
  const slabL = $('slabL'), slabR = $('slabR');
  const fmts = [...document.querySelectorAll('.fmt')];   // карточки форматов на слайде экспорта
  const s4 = $('s4'), briefText = $('briefText'), briefCaret = $('briefCaret');
  const win = $('win'), brief = $('brief');
  const winBlocks = [...document.querySelectorAll('.win__block')];
  const s5 = $('s5'), variants = [...document.querySelectorAll('.variant')], vframe = $('vframe');
  const cur = $('cur'), curDot = $('curDot');
  const navLens = $('navLens'), navItems = [...document.querySelectorAll('.nav__item')];
  const avatar = $('avatar'), accMenu = $('accMenu'), shell = document.querySelector('.shell');

  const cards = [];
  const cardTpls = [...document.querySelectorAll('#cardTpls template')];
  function addCard() {
    const el = document.createElement('div');
    el.className = 'card';
    el.appendChild(cardTpls[cards.length % cardTpls.length].content.cloneNode(true));
    stream.appendChild(el);
    cards.push(el);
    return el;
  }

  /* ── Состояние ── */
  let W = innerWidth, H = innerHeight, S = 1, OX = 0, OY = 0;
  /* Разрешение канваса — ровно как у экрана (до 2×). Дробный масштаб вроде
     1.25× заставлял браузер растягивать холст, и тонкие линии сетки начинали
     мерцать при движении. Меняться по ходу он не должен: пересоздание холста
     даёт вспышку и скачок резкости. */
  const DPR = Math.min(2, devicePixelRatio || 1);
  const mouse = { x: -1e4, y: -1e4 };
  addEventListener('mousemove', (e) => { mouse.x = e.clientX; mouse.y = e.clientY; mouseTs = performance.now(); });
  document.addEventListener('mouseleave', () => { mouse.x = -1e4; mouse.y = -1e4; });

  /* ── Свой курсор ──
     Точка идёт ровно под указателем (пишем её прямо в обработчике),
     кольцо догоняет в общем кадре. Только для мыши: на тач-устройствах
     системного курсора нет и подменять нечего. */
  const fine = matchMedia('(hover: hover) and (pointer: fine)').matches;
  let curX = 0, curY = 0, curScale = 1, curShown = false;
  if (fine) {
    document.documentElement.classList.add('has-cur');
    addEventListener('mousemove', (e) => {
      curDot.style.transform = `translate3d(${e.clientX}px, ${e.clientY}px, 0)`;
      if (!curShown) {                               // первое движение — показываем и ставим кольцо на место
        curShown = true; curX = e.clientX; curY = e.clientY;
        cur.classList.add('is-vis'); curDot.classList.add('is-vis');
      }
    }, { passive: true });
    document.addEventListener('mouseleave', () => {
      curShown = false; cur.classList.remove('is-vis'); curDot.classList.remove('is-vis');
    });
    document.addEventListener('mouseover', (e) => {  // реагируем на смену элемента, а не на каждое движение
      const hot = !!(e.target.closest && e.target.closest('a, button, .tab, .card, .vpick'));
      cur.classList.toggle('is-on', hot);
    }, { passive: true });
  }
  /* ── Окно выбора на 4-м слайде ──
     Само переезжает между вариантами, но его можно взять и перетащить:
     тогда автопоказ останавливается и окно слушается руки, а на отпускании
     притягивается к ближайшей карточке. */
  const V_STEP = 236, V_MAX = V_STEP * 2;              // шаг между карточками и предел хода
  const V_HOP = 620, V_HOLD = 820;                     // мс на перелёт и на паузу между ними
  let vX = 0, vScaleX = 1, vFrom = 0, vTo = 0, vHopAt = 0, vDir = 1;
  let vDrag = null, vHold = 0;                         // vHold — до какого времени стоим после руки

  let badgeHot = 0;                                   // 0…1 — курсор над бейджем
  let enteredAt = 0, curSlide = -1;                   // локальное время активного слайда
  let p = 0, slide = 0, activeDot = -1;
  let animFrom = 0, animStart = 0;                    // переход между слайдами по времени
  let dists = null;
  const order = [], cardZ = [];
  const debugP = new URLSearchParams(location.search).get('p');
  const debugTl = new URLSearchParams(location.search).get('tl');   // ?tl=6 фиксирует локальное время слайда
  const debugVh = new URLSearchParams(location.search).get('vh');   // ?vh=0…1 фиксирует фазу перелёта окна выбора
  let tabHot = 0;                                     // 0…1 — курсор на вкладке
  let tabBox = { x: 0, y: 0, w: 1, h: 1 };            // геометрия вкладки в координатах композиции
  let fpsEma = 16.7, prevTs = 0, slowFrames = 0, fastFrames = 0;   // средний интервал между кадрами, мс
  let bgTs = 0, mouseTs = -1e4;                       // когда фон рисовался и когда двигали курсор
  let dtSmooth = 1 / 60;                              // сглаженная длительность кадра для хода ленты
  const showFps = new URLSearchParams(location.search).has('fps');
  const fpsBox = showFps ? document.body.appendChild(Object.assign(document.createElement('div'), {
    style: 'position:fixed;left:8px;top:8px;z-index:99;padding:4px 8px;font:12px monospace;' +
           'color:#0f0;background:rgba(0,0,0,.7);border-radius:4px;pointer-events:none',
  })) : null;
  let shellY = 0;                                     // сдвиг панели по Y (нужен для рамки вкладки)

  function layout() {
    W = innerWidth; H = innerHeight;
    S = Math.min(W / CW, H / CH);                     // вписываем композицию целиком
    OX = (W - CW * S) / 2; OY = (H - CH * S) / 2;
    const tr = `translate(${OX}px, ${OY}px) scale(${S})`;
    sceneIntro.style.transform = tr; sceneUi.style.transform = tr;
    const cw = Math.round(W * DPR), ch = Math.round(H * DPR);
    if (vortex.width !== cw || vortex.height !== ch) { vortex.width = cw; vortex.height = ch; }
    const sh = tab.offsetParent;                      // .shell — вкладка лежит в ней
    tabBox = { x: sh.offsetLeft + tab.offsetLeft, y: sh.offsetTop + tab.offsetTop,
               w: tab.offsetWidth, h: tab.offsetHeight };
  }
  function applyQuality() {
    document.documentElement.style.setProperty('--shell-blur', QUAL[qi].blur + 'px');
  }
  applyQuality();
  layout();                                           // первый расчёт масштаба сцены
  addEventListener('resize', layout);

  /* ── Листание по слайдам ── */
  const dotEls = [...dots.querySelectorAll('button')];
  function goTo(n) {
    n = clamp(n, 0, SLIDES - 1);
    if (n === slide) return;
    if (n !== 0) acc(false);                          // на других слайдах шапки уже нет
    slide = n; animFrom = p; animStart = performance.now();
  }
  dotEls.forEach((d) => d.addEventListener('click', () => goTo(+d.dataset.slide)));
  hint.addEventListener('click', () => goTo(slide + 1));

  /* ── Разделы в шапке ──
     Пункт ведёт на свой слайд, а по пилюле ездит стеклянная линза. Сама она
     не бегает за курсором: её нужно взять и протащить. Пока держат — линза
     идёт за указателем кадр в кадр и растягивается по ходу движения; когда
     отпустили — притягивается к ближайшему разделу и уводит на его слайд.
     Короткий тычок без движения работает как обычный клик. */
  const navEl = document.querySelector('.nav');
  const navFor = (n) => (n < 1 ? 0 : n < 2 ? 1 : n <= 3 ? 2 : 3);   // какой раздел отвечает за слайд n
  let navAt = -1, navDrag = null, navSettle = 0;
  let navGeom = [], navSets = [[], []];              // геометрия пунктов: обычная и сжатая

  /* Пилюля сжимается, когда открыт аккаунт, поэтому у пунктов два набора
     координат. Меряем оба сразу, с выключенными переходами: тогда при
     открытии линзе достаточно поставить новую цель — дальше её везёт тот же
     CSS-переход, что и пилюлю, и догонять покадрово ничего не нужно. */
  function navSnapshot() {
    return navItems.map((el) => ({
      x: el.offsetLeft - 6, w: el.offsetWidth + 12, c: el.offsetLeft + el.offsetWidth / 2,
    }));
  }
  function navMeasure() {
    const root = document.documentElement;
    const open = shell.classList.contains('is-acc');
    root.classList.add('no-anim');
    shell.classList.remove('is-acc');
    navSets[0] = navSnapshot();
    shell.classList.add('is-acc');
    navSets[1] = navSnapshot();
    shell.classList.toggle('is-acc', open);
    void navEl.offsetWidth;                          // фиксируем состояние до включения переходов
    root.classList.remove('no-anim');
    navGeom = navSets[open ? 1 : 0];
  }
  function navPut(x, w, sx) {
    navLens.style.width = w.toFixed(1) + 'px';
    navLens.style.transform = `translateX(${x.toFixed(1)}px) scaleX(${sx.toFixed(3)})`;
  }
  function navMove(i, jump) {                          // перелёт к разделу i
    if (!navLens || !navGeom[i] || (i === navAt && !jump)) return;
    navAt = i;
    const g = navGeom[i];
    navPut(g.x, g.w, jump ? 1 : 1.12);
    clearTimeout(navSettle);
    if (!jump) navSettle = setTimeout(() => navPut(g.x, g.w, 1), 170);
  }
  function navNearest(cx) {                            // ближайший раздел к точке cx
    let best = 0;
    for (let i = 1; i < navGeom.length; i++) {
      if (Math.abs(navGeom[i].c - cx) < Math.abs(navGeom[best].c - cx)) best = i;
    }
    return best;
  }
  function navWidthAt(cx) {                            // ширина линзы между разделами
    if (cx <= navGeom[0].c) return navGeom[0].w;
    const last = navGeom.length - 1;
    if (cx >= navGeom[last].c) return navGeom[last].w;
    for (let i = 0; i < last; i++) {
      if (cx <= navGeom[i + 1].c) {
        const t = (cx - navGeom[i].c) / (navGeom[i + 1].c - navGeom[i].c);
        return lerp(navGeom[i].w, navGeom[i + 1].w, t);
      }
    }
    return navGeom[last].w;
  }

  if (navEl) {
    navEl.addEventListener('pointerdown', (e) => {
      if (p > 0.5) return;                             // шапка живёт только на первом слайде
      e.preventDefault();                              // иначе браузер начнёт выделять надписи
      navMeasure();
      navDrag = { id: e.pointerId, rect: navEl.getBoundingClientRect(), moved: false,
                  x0: e.clientX, px: 0, t: performance.now() };
      navDrag.px = (e.clientX - navDrag.rect.left) / S;
      navLens.classList.add('is-drag');
      clearTimeout(navSettle);
      try { navEl.setPointerCapture(e.pointerId); } catch (_) {}
    });
    navEl.addEventListener('pointermove', (e) => {
      if (!navDrag || e.pointerId !== navDrag.id) return;
      const d = navDrag;
      if (Math.abs(e.clientX - d.x0) > 3) d.moved = true;
      const cx = clamp((e.clientX - d.rect.left) / S, 0, navEl.offsetWidth);
      const ms = performance.now();
      const vx = (cx - d.px) / Math.max(8, ms - d.t) * 1000;   // px композиции в секунду
      d.px = cx; d.t = ms;
      const w = navWidthAt(cx);
      const x = clamp(cx - w / 2, 0, navEl.offsetWidth - w);
      navPut(x, w, 1 + clamp(Math.abs(vx) / 1500, 0, 0.18));   // на ходу каплю вытягивает
      navAt = -1;
    });
    const navDrop = (e) => {
      if (!navDrag || e.pointerId !== navDrag.id) return;
      const d = navDrag; navDrag = null;
      navLens.classList.remove('is-drag');
      void navLens.offsetWidth;                      // пересчёт стилей, иначе возврат к разделу не анимируется
      const cx = clamp((e.clientX - d.rect.left) / S, 0, navEl.offsetWidth);
      const i = navNearest(cx);
      navMove(i, true);                                // притягиваем к разделу — уже с переходом
      goTo(+navItems[i].dataset.slide);
    };
    navEl.addEventListener('pointerup', navDrop);
    navEl.addEventListener('pointercancel', () => {
      if (!navDrag) return;
      navDrag = null; navLens.classList.remove('is-drag');
      navMove(navFor(Math.round(p)), true);
    });
  }
  navItems.forEach((el) => {
    // мышь и палец обрабатывает перетаскивание; здесь остаётся клавиатура
    el.addEventListener('click', (e) => { e.preventDefault(); if (!e.detail) goTo(+el.dataset.slide); });
  });
  navMeasure();
  navMove(0, true);                                    // линза сразу стоит на месте
  if (document.fonts) document.fonts.ready.then(() => { navMeasure(); navMove(navAt >= 0 ? navAt : 0, true); });
  addEventListener('resize', navMeasure);

  /* ── Аккаунт ──
     Панель вытекает из иконки профиля: силуэт и кружок слипаются под
     общим фильтром (см. .acc-goo в стилях), здесь только состояние. */
  let accOpen = false;
  function acc(on) {
    if (on === accOpen || !shell) return;
    accOpen = on;
    shell.classList.toggle('is-acc', on);
    avatar.setAttribute('aria-expanded', on ? 'true' : 'false');
    navGeom = navSets[on ? 1 : 0];                    // пилюля поджимается — у пунктов другой набор
    const i = navAt >= 0 ? navAt : navFor(Math.round(p));
    navAt = -1; navMove(i, true);                     // линза едет тем же переходом, что и пилюля
  }
  if (avatar) {
    avatar.addEventListener('click', (e) => { e.preventDefault(); acc(!accOpen); });
    // клик мимо панели закрывает её; внутри — нет
    document.addEventListener('pointerdown', (e) => {
      if (!accOpen) return;
      if (e.target.closest && e.target.closest('#accMenu, #avatar')) return;
      acc(false);
    });
    addEventListener('keydown', (e) => { if (e.key === 'Escape') acc(false); });
  }

  if (vframe) {
    vframe.addEventListener('pointerdown', (e) => {
      if (Math.round(p) !== 3) return;                 // только на своём слайде
      e.preventDefault();
      vDrag = { id: e.pointerId, dx: e.clientX / S - vX, px: vX, t: performance.now() };
      vScaleX = 1;
      vframe.classList.add('is-drag');
      try { vframe.setPointerCapture(e.pointerId); } catch (_) {}
    });
    vframe.addEventListener('pointermove', (e) => {
      if (!vDrag || e.pointerId !== vDrag.id) return;
      const nx = clamp(e.clientX / S - vDrag.dx, 0, V_MAX);
      const ms = performance.now();
      const v = (nx - vDrag.px) / Math.max(8, ms - vDrag.t) * 1000;   // px композиции в секунду
      vDrag.px = nx; vDrag.t = ms;
      vScaleX = 1 + clamp(Math.abs(v) / 2600, 0, 0.14);               // на ходу стекло тянется
      vX = nx;
    });
    const vDrop = () => {
      if (!vDrag) return;
      vDrag = null; vScaleX = 1;
      vframe.classList.remove('is-drag');
      vTo = vFrom = clamp(Math.round(vX / V_STEP), 0, 2);   // притягиваем к ближайшей карточке
      vX = vTo * V_STEP;
      vDir = vTo === 2 ? -1 : 1;
      vHold = performance.now() + 2200;                     // даём паузу, потом ход возобновляется
    };
    vframe.addEventListener('pointerup', vDrop);
    vframe.addEventListener('pointercancel', vDrop);
  }

  let lockUntil = 0, wheelAcc = 0;
  function step(dir) {
    const now = performance.now();
    if (now < lockUntil) return;
    lockUntil = now + SLIDE_MS;
    goTo(slide + dir);
  }
  addEventListener('wheel', (e) => {
    e.preventDefault();
    if (performance.now() < lockUntil) { wheelAcc = 0; return; }
    wheelAcc += e.deltaY;
    if (Math.abs(wheelAcc) > 40) { step(wheelAcc > 0 ? 1 : -1); wheelAcc = 0; }
  }, { passive: false });
  addEventListener('keydown', (e) => {
    if (e.target !== document.body) return;
    if (['ArrowDown', 'PageDown', ' '].includes(e.key)) { e.preventDefault(); step(1); }
    else if (['ArrowUp', 'PageUp'].includes(e.key)) { e.preventDefault(); step(-1); }
    else if (e.key === 'Home') goTo(0);
    else if (e.key === 'End') goTo(SLIDES - 1);
  });
  let touchY = null;
  addEventListener('touchstart', (e) => { touchY = e.touches[0].clientY; }, { passive: true });
  addEventListener('touchend', (e) => {
    if (touchY === null || navDrag) return;
    const dy = touchY - e.changedTouches[0].clientY;
    if (Math.abs(dy) > 50) step(dy > 0 ? 1 : -1);
    touchY = null;
  });

  const show = (el, o) => { el.style.opacity = o; el.style.visibility = o > 0.005 ? 'visible' : 'hidden'; };

  const BRIEF = 'Питч новой фичи для инвесторов. Двенадцать слайдов, деловой тон. ' +
                'Начни с проблемы, покажи решение и экономику. Добавь цифры рынка, ' +
                'план на год и состав команды. Финал — запрос на раунд. Шрифты и ' +
                'палитру возьми из загруженного шаблона.';

  /* ── Сетка (фон всех слайдов) ──
     Ровная статичная сетка мелкой клеткой: геометрия не меняется, поэтому
     она рисуется один раз в offscreen-холст и каждый кадр только копируется.
     Живое здесь одно — оранжевое свечение линий вокруг курсора; оно рисуется
     тем же контуром, но только в пределах своего пятна. */
  const gctx = vortex.getContext('2d');
  const base = document.createElement('canvas');      // готовая белая сетка
  const bctx = base.getContext('2d');
  const glow = document.createElement('canvas');      // оранжевый слой, маскируется градиентом
  const glctx = glow.getContext('2d');
  const spot = { x: -1e4, y: -1e4, k: 0 };            // центр свечения (сглаженный) и его сила 0…1
  let gridPath = new Path2D();
  let gridW = -1, gridH = -1;                         // размер, под который собрана сетка

  /* Контур и готовая сетка — пересобираются только при смене размера окна. */
  function buildGrid() {
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

  function drawGrid(time, finalV) {
    if (gridW !== W || gridH !== H) { gridW = W; gridH = H; buildGrid(); }
    const cx = W / 2, cy = H / 2;
    // цель свечения: курсор, а на финальном слайде при наведении на бейдж — его центр
    let tx = mouse.x, ty = mouse.y, want = mouse.x > -1e3 ? 1 : 0;
    if (mouse.x < -1e3) {                             // без курсора пятно медленно плывёт само
      tx = cx + Math.sin(time * 0.5) * W * 0.32; ty = cy + Math.cos(time * 0.67) * H * 0.28; want = 0.7;
    }
    if (badgeHot > 0.01) { tx = lerp(tx, cx, badgeHot); ty = lerp(ty, cy, badgeHot); want = Math.max(want, 1); }
    if (spot.x < -1e3) { spot.x = tx; spot.y = ty; }
    spot.x += (tx - spot.x) * 0.2; spot.y += (ty - spot.y) * 0.2;
    spot.k += (want - spot.k) * 0.15;

    const R = Math.min(W, H) * (0.24 + 0.1 * badgeHot); // радиус свечения
    const bx = spot.x, by = spot.y;

    // готовая сетка — одно копирование вместо тысяч отрезков
    const ctx = gctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, vortex.width, vortex.height);
    ctx.drawImage(base, 0, 0);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);

    /* Свечение: те же линии оранжевым, маска — радиальный градиент. Все
       операции ограничены рамкой пятна, а не всем экраном. */
    if (spot.k > 0.02) {
      const gs = QUAL[qi].glow, gw = Math.round(W * DPR * gs), gh = Math.round(H * DPR * gs);
      if (glow.width !== gw || glow.height !== gh) { glow.width = gw; glow.height = gh; }
      const rad = R * 1.15;
      const bl = Math.max(0, bx - rad), bt = Math.max(0, by - rad);
      const br = Math.min(W, bx + rad), bb = Math.min(H, by + rad);
      const bw = br - bl, bh2 = bb - bt;
      if (bw > 1 && bh2 > 1) {
        const g = glctx;
        g.setTransform(DPR * gs, 0, 0, DPR * gs, 0, 0);
        g.globalCompositeOperation = 'source-over';
        g.clearRect(bl, bt, bw, bh2);
        g.save();
        g.beginPath(); g.rect(bl, bt, bw, bh2); g.clip();
        g.lineWidth = 6; g.strokeStyle = 'rgba(190,97,18,.5)';       // мягкий ореол
        g.stroke(gridPath);
        g.lineWidth = 1.8; g.strokeStyle = '#ffa04a';                // яркая сердцевина линии
        g.stroke(gridPath);
        const mask = g.createRadialGradient(bx, by, 0, bx, by, rad);
        mask.addColorStop(0, `rgba(0,0,0,${spot.k})`);
        mask.addColorStop(0.5, `rgba(0,0,0,${0.62 * spot.k})`);
        mask.addColorStop(1, 'rgba(0,0,0,0)');
        g.globalCompositeOperation = 'destination-in';
        g.fillStyle = mask; g.fillRect(bl, bt, bw, bh2);
        g.restore();
        const k = DPR * gs;
        ctx.drawImage(glow, bl * k, bt * k, bw * k, bh2 * k, bl, bt, bw, bh2);
      }
    }

    // тёмная сердцевина под бейджем на финальном слайде, чтобы он читался
    if (finalV > 0) {
      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.min(W, H) * 0.24);
      grad.addColorStop(0, `rgba(0,0,0,${0.85 * finalV})`); grad.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = grad; ctx.fillRect(0, 0, W, H);
    }
  }

  /* ── Кадр ── */
  let last = performance.now();
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;

    if (debugP !== null) p = +debugP;
    else p = lerp(animFrom, slide, ease((now - animStart) / SLIDE_MS));
    hint.style.opacity = 1 - clamp(p * 3, 0, 1);
    hint.style.visibility = p < 0.33 ? 'visible' : 'hidden';
    const active = Math.round(p);
    if (active !== activeDot) {                       // классы трогаем только при смене слайда
      activeDot = active;
      dotEls.forEach((d, i) => d.classList.toggle('is-active', i === active));
      const na = navFor(active);
      navItems.forEach((el, i) => el.classList.toggle('is-active', i === na));
      if (!navDrag) navMove(na);
    }

    // видимость слайда n: 1 на месте, плавно в 0 при уходе на соседний
    const vis = (n) => 1 - ease(Math.abs(p - n) / 0.6);

    /* ══════════ Слайд 1: главный экран ══════════
       Панель уезжает вверх, открывая ленту, — как в композиции. */
    {
      show(sc1, p < 1.08 ? 1 : 0);                    // панель уезжает целиком — без подмешивания прозрачности
      shellY = -clamp(p, 0, 1) * CH * 1.06;
      sc1.style.transform = `translateY(${shellY}px)`;

      // вкладка живёт: наклоняется к курсору и чуть покачивается.
      // Рамку считаем сами из кэша — getBoundingClientRect каждый кадр
      // заставлял бы браузер пересчитывать лейаут.
      const rw = tabBox.w * S, rh = tabBox.h * S;
      const rl = OX + tabBox.x * S, rt = OY + (tabBox.y + shellY) * S;
      const inside = p < 1 && mouse.x > rl && mouse.x < rl + rw && mouse.y > rt && mouse.y < rt + rh;
      tabHot += ((inside ? 1 : 0) - tabHot) * 0.12;
      const nx = clamp((mouse.x - (rl + rw / 2)) / (rw / 2), -1, 1);
      const ny = clamp((mouse.y - (rt + rh / 2)) / (rh / 2), -1, 1);
      const T = now / 1000;
      const wob = tabHot * 1.2;                       // покачивание только под курсором
      const rz = 5 + Math.sin(T * 2.1) * 0.35 * (0.3 + tabHot) + wob * Math.sin(T * 3.3) * 0.5;
      const ry = -17 + nx * 7 * tabHot + Math.sin(T * 1.5) * 0.8;
      const rx = 5 - ny * 6 * tabHot;
      tab.style.transform =
        `perspective(560px) rotateZ(${rz.toFixed(2)}deg) rotateY(${ry.toFixed(2)}deg) rotateX(${rx.toFixed(2)}deg) scale(${(1 + tabHot * 0.025).toFixed(3)})`;

      // интерфейс внутри вкладки: индикатор генерации бежит по кругу
      const gen = (T * 0.18) % 1;
      tabBar.style.width = (gen * 100).toFixed(1) + '%';
      const n12 = 1 + Math.floor(gen * 12);
      if (tabLive.dataset.n !== String(n12)) { tabLive.dataset.n = n12; tabLive.textContent = `генерация ${n12} / 12`; }
    }

    /* ══════════ Слайды 2–3: косые блоки вдоль ленты ══════════
       Блоки въезжают по оси ленты: левый — снизу вверх (против ленты),
       правый и блок 3-го слайда — сверху вниз (по ленте). */
    const along = (el, dist) =>                       // сдвиг вдоль оси ленты, dist в px композиции
      el.style.transform = `translate(${(AXIS[0] * dist).toFixed(1)}px, ${(AXIS[1] * dist).toFixed(1)}px)`;
    {
      show(sc2, p < 2.9 ? 1 : 0);
      show(sc3, p < 5.9 ? 1 : 0);

      // 1 → 2: блоки въезжают по оси ленты — левый снизу (против ленты),
      // правый сверху (по ленте). Дальше по оси они не двигаются.
      const q = clamp(p - 1, -1.2, 0);
      along(slabL, -q * SLAB_IN);
      along(slabR,  q * SLAB_IN);

      // второй слайд уходит вверх; экспорт (слайд 6) приходит снизу
      sc2.style.transform = `translateY(${-Math.max(0, p - 1) * CH * 1.06}px)`;
      sc3.style.transform = `translateY(${(clamp(p, 3, 5) - 4) * -CH * 1.06}px)`;
    }

    /* ══════════ Лента шаблонов ══════════
       Карточки едут снизу вверх. Лента — элемент второго слайда: проявляется,
       когда уезжает панель первого, и уходит на переходе к третьему. */
    const streamVis = ease((p - 0.12) / 0.45) * (1 - ease((p - 1.25) / 0.55));
    show(stream, streamVis);

    const [lineX, lineY] = LINE_PT;

    const [d0, d1] = visibleRange(lineX, lineY);
    if (!dists) {                                     // старт: цепочка уже заполняет видимый участок
      dists = [];
      for (let d = d1; d > d0 - CARD_GAP && dists.length < CARD_MAX; d -= CARD_GAP) { dists.push(d); addCard(); }
    }
    const need = Math.min(CARD_MAX, Math.ceil((d1 - d0) / CARD_GAP) + 2);
    while (cards.length < need) {
      let lo = Infinity;
      for (let i = 0; i < dists.length; i++) if (dists[i] < lo) lo = dists[i];
      dists.push(lo - CARD_GAP); addCard();
    }
    const N = cards.length;
    // шаг ленты берём по сглаженному dt: если кадры приходят неровно
    // (16-33-16 мс), точный dt даёт подрагивание, сглаженный — ровный ход
    dtSmooth += (dt - dtSmooth) * 0.15;
    const adv = reduceMotion ? 0 : dtSmooth * SPEED_PX;
    let hi = -Infinity;
    for (let i = 0; i < N; i++) { dists[i] -= adv; if (dists[i] > hi) hi = dists[i]; }
    for (let i = 0; i < N; i++) {                     // ушедшая вверх встаёт в хвост очереди
      if (dists[i] < d0) { hi += CARD_GAP; dists[i] = hi; }
    }
    if (streamVis > 0.004) {                          // невидимую ленту не двигаем в DOM
      order.length = N;
      for (let i = 0; i < N; i++) order[i] = i;
      order.sort((a, b) => dists[a] - dists[b]);
      for (let r = 0; r < N; r++) {
        const i = order[r];
        const [x, y] = linePoint(lineX, lineY, dists[i]);
        cards[i].style.transform = `translate3d(${x.toFixed(2)}px, ${y.toFixed(2)}px, 0) rotate(${CARD_ROT}deg)`;
        if (cardZ[i] !== r) { cardZ[i] = r; cards[i].style.zIndex = r + 1; }
      }
    }

    /* ══════════ Свои слайды ══════════ */
    const near = Math.round(p);
    if (near !== curSlide) { curSlide = near; enteredAt = now; }
    const tl = debugTl !== null ? +debugTl : (now - enteredAt) / 1000;   // секунды с момента, как слайд стал текущим
    const place = (el, n) => {                        // вход снизу / уход вверх
      // без filter/will-change на секции: иначе она становится backdrop root,
      // и стеклянные панели внутри перестают размывать сетку позади
      const q = p - n, v = vis(n);
      show(el, v);
      el.style.transform = `translateY(${-q * 90}px)`;
    };

    /* слайд 3 — опиши задачу: бриф печатается справа, окно слева наполняется */
    place(s4, 2);
    if (vis(2) > 0) {
      const L = Math.min(tl, 6);                          // проигрывается один раз за вход
      const k0 = ease(L / 0.45);                          // панели приезжают
      show(brief, k0); show(win, k0);
      brief.style.transform = `translateY(${((1 - k0) * 22).toFixed(1)}px)`;
      win.style.transform = `translateY(${((1 - k0) * 26).toFixed(1)}px)`;

      const typed = clamp((L - 0.5) * 62, 0, BRIEF.length);
      briefText.textContent = BRIEF.slice(0, Math.floor(typed));
      briefCaret.style.opacity = typed < BRIEF.length ? 1 : 0;

      const done = 0.5 + BRIEF.length / 62;               // когда бриф дописан
      winBlocks.forEach((el, i) => {                      // и только потом появляются блоки
        const k = ease((L - done - 0.25 - i * 0.22) / 0.4);
        show(el, k);
        el.style.transform = `scale(${(0.9 + 0.1 * k).toFixed(3)})`;
      });
    }

    /* слайд 4 — три варианта вёрстки.
       Карточки стоят по координатам из макета (CSS), здесь только вход:
       приподнимаются и проявляются лесенкой. Стеклянная подложка выбора
       переезжает между ними — шаг 236 px, как между карточками. */
    place(s5, 3);
    if (vis(3) > 0) {
      const L = Math.min(tl, 4);                          // проигрывается один раз за вход
      variants.forEach((el, i) => {
        const k = ease((L - 0.12 - i * 0.14) / 0.34);
        show(el, k);
        el.style.transform = `translateY(${((1 - k) * 26).toFixed(1)}px) scale(${(0.94 + 0.06 * k).toFixed(3)})`;
      });
      if (L < 0.1 && !vDrag) {                           // вернулись на слайд — начинаем слева
        vX = 0; vFrom = vTo = 0; vDir = 1; vScaleX = 1;
        vHopAt = now + 700; vHold = 0;
      }
      if (!vDrag && now >= vHold) {
        if (debugVh !== null) { vFrom = 0; vTo = 1; }
        const t = debugVh !== null ? +debugVh : (now - vHopAt) / V_HOP;
        if (t >= 1) {                                    // долетели — стоим паузу и уходим дальше
          vX = vTo * V_STEP; vScaleX = 1; vFrom = vTo;
          if (now - vHopAt >= V_HOP + V_HOLD) {
            vTo += vDir;
            if (vTo > 2) { vTo = 1; vDir = -1; }
            else if (vTo < 0) { vTo = 1; vDir = 1; }
            vHopAt = now;
          }
        } else if (t >= 0) {
          vX = (vFrom + (vTo - vFrom) * ease(t)) * V_STEP;
          vScaleX = 1;
        }
      }
      show(vframe, ease((L - 0.55) / 0.25));
      vframe.style.transform = `translateX(${vX.toFixed(1)}px) scaleX(${vScaleX.toFixed(3)})`;
    }

    /* слайд 5 — экспорт: карточки форматов приходят лесенкой */
    if (vis(4) > 0) {
      const L = Math.min(tl, 3);                          // проигрывается один раз за вход
      fmts.forEach((el, i) => {
        const k = ease((L - 0.15 - i * 0.16) / 0.4);
        show(el, k);
        el.style.transform = `translateY(${((1 - k) * 28).toFixed(1)}px) scale(${(0.94 + 0.06 * k).toFixed(3)})`;
      });
    }

    /* слайд 6 — воронка и бейдж */
    {
      const v = vis(5);
      show(finalBlock, v);
      finalBlock.style.transform = `translateY(${-(p - 5) * 60}px)`;
      badgeHot += ((badge.matches(':hover') ? 1 : 0) - badgeHot) * 0.08;
      /* Фон идёт полным темпом — он главный живой элемент страницы. Экономим
         только на первом слайде и только в покое: там сетка почти скрыта под
         стеклом, а каждая её перерисовка заставляет браузер заново размывать
         полноэкранный backdrop-filter. Как только курсор двигают, пузырь
         должен успевать за ним — возвращаем полный темп. */
      const idle = now - mouseTs > 700;
      const bgGap = p < 0.9 && idle ? QUAL[qi].bg : 0;
      if (now - bgTs >= bgGap) { bgTs = now; drawGrid(now / 1000, v); }
    }

    /* ── Автоподстройка качества ──
       Меряем ИНТЕРВАЛ между кадрами, а не время работы JS: основная нагрузка
       (размытие фона, заливка слоёв) ложится на GPU уже после колбэка, и по
       длительности самого колбэка её не видно. 16.7 мс — это 60 кадров/с. */
    if (!qLock) {
      const gap = now - prevTs; prevTs = now;
      if (gap > 4 && gap < 200) fpsEma += (gap - fpsEma) * 0.05;
      if (fpsEma > 21 && qi < QUAL.length - 1) {
        if (++slowFrames > 40) { qi++; slowFrames = fastFrames = 0; applyQuality(); }
      } else slowFrames = 0;
      if (fpsEma < 14 && qi > 0) {
        if (++fastFrames > 360) { qi--; slowFrames = fastFrames = 0; applyQuality(); }
      } else fastFrames = 0;
      if (fpsBox) fpsBox.textContent = `${fpsEma.toFixed(1)} мс · ${(1000 / fpsEma).toFixed(0)} fps · качество ${qi}`;
    }

    /* кольцо курсора догоняет указатель с лёгким отставанием */
    if (fine && curShown) {
      curX += (mouse.x - curX) * 0.2;
      curY += (mouse.y - curY) * 0.2;
      const want = cur.classList.contains('is-on') ? 1.5 : 1;
      curScale += (want - curScale) * 0.15;
      cur.style.transform = `translate3d(${curX}px, ${curY}px, 0) scale(${curScale})`;
    }

    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
