/* ─────────────────────────────────────────────────────────────
   Prism — колода карточек, управляемая прокруткой
   ─────────────────────────────────────────────────────────────
   Страница не двигается. Под сценой лежит невидимый «трек»
   из N экранов (по одному на карточку) с scroll-snap. Позиция
   прокрутки превращается в прогресс p ∈ [0, N-1]; для каждой
   карточки считаем d = i − p и по ключевым кадрам (ниже)
   интерполируем её положение в 3D.

   d = 0   — карточка впереди, ровно
   d < 0   — уже пролистана: откинулась назад и ушла вверх
   d > 0   — ещё впереди: лежит ниже, слегка наклонена

   Для отладки: ?p=0.35 фиксирует прогресс.
   ───────────────────────────────────────────────────────────── */
(() => {
  'use strict';

  /* ── Ключевые кадры ──
     d — смещение от текущей карточки
     y — сдвиг по вертикали, % высоты карточки
     z — глубина, px (отрицательная — вглубь)
     rx — наклон, градусы (вокруг нижнего ребра карточки)
     s — масштаб
     dim — затемнение 0..1
     o — прозрачность                                            */
  const KEYS = [
    //  d      y     z    rx     s    dim   o
    [-2.0,  -52, -420,  20,  0.86, 0.72, 0],
    [-1.0,  -40, -240,  16,  0.92, 0.50, 1],
    [-0.55, -14, -300,  17,  0.96, 0.22, 1],
    [-0.30,  -2,  -70,  12,  0.985,0.06, 1],
    [ 0.0,    0,    0,   0,  1.00, 0.00, 1],
    [ 0.30,   9,  -70,   9,  0.985,0.06, 1],
    [ 0.55,  18, -150,  15,  0.97, 0.18, 1],
    [ 1.0,   26, -130,  14,  0.96, 0.42, 1],
    [ 2.0,   38, -260,  14,  0.90, 0.65, 0],
  ];

  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const lerp = (a, b, t) => a + (b - a) * t;

  function sample(d) {
    d = clamp(d, KEYS[0][0], KEYS[KEYS.length - 1][0]);
    let i = 0;
    while (i < KEYS.length - 2 && d > KEYS[i + 1][0]) i++;
    const a = KEYS[i], b = KEYS[i + 1];
    const t = (d - a[0]) / (b[0] - a[0]);
    const out = [];
    for (let k = 1; k < a.length; k++) out.push(lerp(a[k], b[k], t));
    return out; // [y, z, rx, s, dim, o]
  }

  /* ── DOM ── */
  const deck = document.getElementById('deck');
  const cards = Array.from(deck.querySelectorAll('.card'));
  const N = cards.length;
  const titlesEl = document.getElementById('titles');
  const rail = document.getElementById('rail');
  const track = document.getElementById('track');
  const hint = document.getElementById('scrollHint');
  const navLinks = Array.from(document.querySelectorAll('[data-goto]'));
  const root = document.documentElement;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const titles = [];
  const dots = [];

  cards.forEach((card, i) => {
    const section = document.createElement('div');
    section.className = 'track__section';
    track.appendChild(section);

    const title = document.createElement('h2');
    title.className = 'title';
    title.textContent = card.dataset.title || '';
    // Длинные заголовки ужимаем, чтобы влезали в ширину экрана
    const len = Math.max(6, title.textContent.length);
    title.style.fontSize = `min(clamp(34px, 7.4vw, 108px), ${(92 / (len * 0.95)).toFixed(2)}vw)`;
    titlesEl.appendChild(title);
    titles.push(title);

    const dot = document.createElement('button');
    dot.className = 'rail__dot';
    dot.type = 'button';
    dot.dataset.label = card.dataset.title || `Карточка ${i + 1}`;
    dot.setAttribute('aria-label', dot.dataset.label);
    dot.addEventListener('click', () => goTo(i));
    rail.appendChild(dot);
    dots.push(dot);

    const sheen = document.createElement('i');
    sheen.className = 'sheen';
    card.appendChild(sheen);
  });

  /* ── Прокрутка → прогресс ── */
  const debugP = new URLSearchParams(location.search).get('p');
  const fixedP = debugP !== null ? clamp(parseFloat(debugP) || 0, 0, N - 1) : null;

  let sectionH = window.innerHeight;
  let target = 0;   // куда стремимся
  let current = 0;  // что показываем (сглаженное)
  let activeIndex = -1;

  function readScroll() {
    if (fixedP !== null) { target = fixedP; return; }
    target = clamp(window.scrollY / sectionH, 0, N - 1);
  }

  function goTo(i) {
    i = clamp(i, 0, N - 1);
    window.scrollTo({ top: i * sectionH, behavior: reduced ? 'auto' : 'smooth' });
  }

  navLinks.forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      goTo(parseInt(a.dataset.goto, 10));
    });
  });

  /* ── Курсор → лёгкий наклон всей колоды ── */
  let tiltTX = 0, tiltTY = 0, tiltX = 0, tiltY = 0;
  if (!reduced && matchMedia('(pointer: fine)').matches) {
    window.addEventListener('pointermove', (e) => {
      const nx = e.clientX / window.innerWidth - 0.5;
      const ny = e.clientY / window.innerHeight - 0.5;
      tiltTY = nx * 5;    // rotateY, град
      tiltTX = -ny * 3;   // rotateX, град
    }, { passive: true });
    window.addEventListener('pointerleave', () => { tiltTX = 0; tiltTY = 0; });
  }

  /* ── Рендер ── */
  function render() {
    const p = current;

    for (let i = 0; i < N; i++) {
      const d = i - p;
      const card = cards[i];

      // Заголовок: держится рядом с карточкой, размывается при уходе
      const t = titles[i];
      const k = clamp(Math.abs(d) * 1.6, 0, 1);
      t.style.opacity = (1 - k).toFixed(3);
      t.style.transform = `translateY(${(-d * 28).toFixed(1)}px) scale(${(1 - k * 0.08).toFixed(3)})`;
      t.style.filter = `blur(${(k * 10).toFixed(1)}px)`;
      t.style.visibility = k >= 1 ? 'hidden' : 'visible';

      // Карточки дальше двух шагов не рисуем вовсе
      if (Math.abs(d) >= 2) {
        card.classList.add('is-hidden');
        continue;
      }
      card.classList.remove('is-hidden');

      const [y, z, rx, s, dim, o] = sample(d);
      card.style.transform =
        `translate3d(0, ${y.toFixed(2)}%, ${z.toFixed(1)}px) rotateX(${rx.toFixed(2)}deg) scale(${s.toFixed(4)})`;
      card.style.opacity = o.toFixed(3);
      card.style.setProperty('--dim', dim.toFixed(3));
      card.classList.toggle('is-front', Math.abs(d) < 0.5);
      card.setAttribute('aria-hidden', Math.abs(d) >= 0.5 ? 'true' : 'false');
    }

    const idx = Math.round(p);
    if (idx !== activeIndex) {
      activeIndex = idx;
      dots.forEach((dot, i) => dot.classList.toggle('is-active', i === idx));
      navLinks.forEach((a) => a.classList.toggle('is-active', parseInt(a.dataset.goto, 10) === idx));
    }
    hint.classList.toggle('is-gone', p > 0.15);

    root.style.setProperty('--tilt-x', `${tiltX.toFixed(2)}deg`);
    root.style.setProperty('--tilt-y', `${tiltY.toFixed(2)}deg`);
  }

  let last = performance.now();
  function frame(now) {
    const dt = Math.min(64, now - last) / 1000;
    last = now;

    if (reduced) {
      current = target;
      tiltX = tiltY = 0;
    } else {
      // экспоненциальное сглаживание, не зависящее от частоты кадров
      const k = 1 - Math.exp(-dt * 9);
      current += (target - current) * k;
      if (Math.abs(target - current) < 0.0005) current = target;
      const kt = 1 - Math.exp(-dt * 5);
      tiltX += (tiltTX - tiltX) * kt;
      tiltY += (tiltTY - tiltY) * kt;
    }

    render();
    requestAnimationFrame(frame);
  }

  function onResize() {
    const first = track.firstElementChild;
    sectionH = first ? first.getBoundingClientRect().height || window.innerHeight : window.innerHeight;
    readScroll();
  }

  window.addEventListener('scroll', readScroll, { passive: true });
  window.addEventListener('resize', onResize);

  // Восстанавливаем позицию после перезагрузки без «прыжка»
  if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
  onResize();
  current = target;
  requestAnimationFrame(frame);
})();
