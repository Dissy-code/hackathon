/* ─────────────────────────────────────────────────────────────
   Конфигуратор ЦДС
   ─────────────────────────────────────────────────────────────
   Три состояния одного окна:

     edit  — редактор: шаблоны, картинки и промт для нейросети
     gen   — генерация: надпись ЦДС закрашивается по диагонали,
             под ней меняются слова, точки бегут
     show  — демонстрация: лента миниатюр и холст

   Вся раскладка живёт в координатах композиции 750×422 (.scene),
   которая вписывается в окно, — как на главной странице.
   ───────────────────────────────────────────────────────────── */
(() => {
  'use strict';

  const CW = 750, CH = 422;                       // размер композиции
  const GRID_CELL = 24;                           // шаг сетки в px композиции

  /* ── Качество ──
     Как на главной: если кадры начинают опаздывать, опускаемся на уровень
     ниже (реже сэмплы подсветки и меньше размытие стекла окна). ?q=0…3
     фиксирует уровень. */
  const QUAL = [
    { glow: 0.6,  blur: 16, bg: 40 },
    { glow: 0.5,  blur: 12, bg: 55 },
    { glow: 0.4,  blur: 9,  bg: 70 },
    { glow: 0.35, blur: 6,  bg: 90 },
  ];
  let qi = Math.min(QUAL.length - 1, Math.max(0, +new URLSearchParams(location.search).get('q') || 0));
  const qLock = new URLSearchParams(location.search).has('q');
  /* Разрешение холста — как у экрана (до 2×) и меняться не должно:
     пересоздание холста даёт вспышку и скачок резкости. */
  const DPR = Math.min(2, devicePixelRatio || 1);
  const $ = (id) => document.getElementById(id);
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

  const stage = $('stage'), scene = $('scene'), vortex = $('vortex');
  const fine = matchMedia('(hover: hover) and (pointer: fine)').matches;
  let curX = 0, curY = 0, curScale = 1, curShown = false;
  const mouse = { x: 0, y: 0 };
  const nav = $('nav'), navLens = $('navLens');
  const navItems = [...nav.querySelectorAll('.nav__item')];
  const avatar = $('avatar'), accMenu = $('accMenu');
  const views = { edit: $('viewEdit'), gen: $('viewGen'), show: $('viewShow') };
  const tplSlots = $('tplSlots'), imgSlots = $('imgSlots');
  const promptBox = $('prompt'), goBtn = $('go');
  const genLogo = $('genLogo'), genWord = $('genWord'), genCaret = $('genCaret');
  const rail = $('rail'), canvas = $('canvas');
  const cur = $('cur'), curDot = $('curDot');

  /* ── Масштаб сцены ── */
  let S = 1, W = innerWidth, H = innerHeight;
  function layout() {
    W = innerWidth; H = innerHeight;
    S = Math.min(W / CW, H / CH);
    const ox = (W - CW * S) / 2, oy = (H - CH * S) / 2;
    scene.style.transform = `translate(${ox}px, ${oy}px) scale(${S})`;
    const cw = Math.round(W * DPR), ch = Math.round(H * DPR);
    if (vortex.width !== cw || vortex.height !== ch) { vortex.width = cw; vortex.height = ch; }
  }
  function applyQuality() {
    document.documentElement.style.setProperty('--win-blur', QUAL[qi].blur + 'px');
  }
  applyQuality();
  layout();
  addEventListener('resize', layout);

  /* ══════════ Фон: живая сетка ══════════
     Один в один с главной: статичная сетка лежит в отдельном холсте и
     копируется целиком, а оранжевая подсветка рисуется только в рамке
     пятна и маскируется радиальным градиентом. */
  const gctx = vortex.getContext('2d');
  const base = document.createElement('canvas');      // готовая белая сетка
  const bctx = base.getContext('2d');
  const glow = document.createElement('canvas');      // оранжевый слой под маску
  const glctx = glow.getContext('2d');
  const spot = { x: -1e4, y: -1e4, k: 0 };            // центр свечения и его сила 0…1
  let gridPath = new Path2D();
  let gridW = -1, gridH = -1;

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

  function drawGrid(time) {
    if (gridW !== W || gridH !== H) { gridW = W; gridH = H; buildGrid(); }
    let tx = mouse.x, ty = mouse.y, want = curShown ? 1 : 0;
    if (!curShown) {                                  // без курсора пятно плывёт само
      tx = W / 2 + Math.sin(time * 0.5) * W * 0.32;
      ty = H / 2 + Math.cos(time * 0.67) * H * 0.28;
      want = 0.7;
    }
    if (spot.x < -1e3) { spot.x = tx; spot.y = ty; }
    spot.x += (tx - spot.x) * 0.2; spot.y += (ty - spot.y) * 0.2;
    spot.k += (want - spot.k) * 0.15;

    const R = Math.min(W, H) * 0.24;
    const bx = spot.x, by = spot.y;

    gctx.setTransform(1, 0, 0, 1, 0, 0);
    gctx.clearRect(0, 0, vortex.width, vortex.height);
    gctx.drawImage(base, 0, 0);
    gctx.setTransform(DPR, 0, 0, DPR, 0, 0);

    if (spot.k > 0.02) {
      const gs = QUAL[qi].glow;
      const gw = Math.round(W * DPR * gs), gh = Math.round(H * DPR * gs);
      if (glow.width !== gw || glow.height !== gh) { glow.width = gw; glow.height = gh; }
      const rad = R * 1.15;
      const bl = Math.max(0, bx - rad), bt = Math.max(0, by - rad);
      const br = Math.min(W, bx + rad), bb = Math.min(H, by + rad);
      const bw = br - bl, bh = bb - bt;
      if (bw > 1 && bh > 1) {
        const g = glctx;
        g.setTransform(DPR * gs, 0, 0, DPR * gs, 0, 0);
        g.globalCompositeOperation = 'source-over';
        g.clearRect(bl, bt, bw, bh);
        g.save();
        g.beginPath(); g.rect(bl, bt, bw, bh); g.clip();
        g.lineWidth = 6; g.strokeStyle = 'rgba(190,97,18,.5)';       // мягкий ореол
        g.stroke(gridPath);
        g.lineWidth = 1.8; g.strokeStyle = '#ffa04a';                // яркая сердцевина
        g.stroke(gridPath);
        const mask = g.createRadialGradient(bx, by, 0, bx, by, rad);
        mask.addColorStop(0, `rgba(0,0,0,${spot.k})`);
        mask.addColorStop(0.5, `rgba(0,0,0,${0.62 * spot.k})`);
        mask.addColorStop(1, 'rgba(0,0,0,0)');
        g.globalCompositeOperation = 'destination-in';
        g.fillStyle = mask; g.fillRect(bl, bt, bw, bh);
        g.restore();
        const k = DPR * gs;
        gctx.drawImage(glow, bl * k, bt * k, bw * k, bh * k, bl, bt, bw, bh);
      }
    }
  }

  /* ══════════ Свой курсор ══════════
     Точка идёт ровно под указателем, кольцо догоняет в кадре. */
  if (fine) {
    document.documentElement.classList.add('has-cur');
    addEventListener('mousemove', (e) => {
      mouse.x = e.clientX; mouse.y = e.clientY;
      curDot.style.transform = `translate3d(${e.clientX}px, ${e.clientY}px, 0)`;
      if (!curShown) {
        curShown = true; curX = e.clientX; curY = e.clientY;
        cur.classList.add('is-vis'); curDot.classList.add('is-vis');
      }
    }, { passive: true });
    document.addEventListener('mouseleave', () => {
      curShown = false; cur.classList.remove('is-vis'); curDot.classList.remove('is-vis');
    });
    document.addEventListener('mouseover', (e) => {   // реагируем на смену элемента
      const hot = !!(e.target.closest && e.target.closest('a, button, .slot, .thumb, textarea'));
      cur.classList.toggle('is-on', hot);
    }, { passive: true });
  }

  /* ══════════ Режимы в шапке ══════════
     Пункт ведёт в свой вид, а по пилюле ездит стеклянная линза. Её можно
     не только нажать, но и протащить: пока держат — линза идёт за рукой,
     на отпускании притягивается к ближайшему режиму. */
  const NAV_X = [4.6, 182.8];                      // левые края режимов в px композиции
  let navAt = -1, navDrag = null;

  function navMove(i, jump) {
    if (i === navAt && !jump) return;
    navAt = i;
    navLens.style.transform = `translateX(${(NAV_X[i] - NAV_X[0]).toFixed(1)}px) scaleX(1)`;
    navItems.forEach((el, k) => el.classList.toggle('is-active', k === i));
  }
  navItems.forEach((el, i) => {
    el.addEventListener('click', () => { navMove(i); go(el.dataset.view); });
  });
  nav.addEventListener('pointerdown', (e) => {
    // короткий тычок обрабатывает click на самом пункте, здесь — только протяжка
    const r = nav.getBoundingClientRect();
    navDrag = { id: e.pointerId, rect: r, moved: false, x0: e.clientX };
    navLens.classList.add('is-drag');
    try { nav.setPointerCapture(e.pointerId); } catch (_) {}
  });
  nav.addEventListener('pointermove', (e) => {
    if (!navDrag || e.pointerId !== navDrag.id) return;
    if (Math.abs(e.clientX - navDrag.x0) < 3) return;
    navDrag.moved = true;
    const cx = (e.clientX - navDrag.rect.left) / S;          // в координатах композиции
    const x = clamp(cx - 89.4, NAV_X[0], NAV_X[1]);          // 89.4 — половина ширины линзы
    navLens.style.transform = `translateX(${(x - NAV_X[0]).toFixed(1)}px) scaleX(1.05)`;
  });
  const navDrop = (e) => {
    if (!navDrag || e.pointerId !== navDrag.id) return;
    const d = navDrag; navDrag = null;
    navLens.classList.remove('is-drag');
    void navLens.offsetWidth;                                // иначе возврат не анимируется
    if (!d.moved) return;                                    // это был клик — его уже обработали
    const cx = (e.clientX - d.rect.left) / S;
    const i = cx < (NAV_X[0] + NAV_X[1]) / 2 + 89.4 ? 0 : 1;
    navAt = -1; navMove(i);
    go(navItems[i].dataset.view);
  };
  nav.addEventListener('pointerup', navDrop);
  nav.addEventListener('pointercancel', () => {
    if (!navDrag) return;
    navDrag = null; navLens.classList.remove('is-drag'); navMove(navAt, true);
  });
  navMove(0, true);

  /* ══════════ Аккаунт ══════════ */
  let accOpen = false;
  function acc(on) {
    if (on === accOpen) return;
    accOpen = on;
    scene.classList.toggle('is-acc', on);
    avatar.setAttribute('aria-expanded', on ? 'true' : 'false');
  }
  avatar.addEventListener('click', () => acc(!accOpen));
  document.addEventListener('pointerdown', (e) => {
    if (!accOpen) return;
    if (e.target.closest && e.target.closest('#accMenu, #avatar')) return;
    acc(false);
  });
  addEventListener('keydown', (e) => { if (e.key === 'Escape') acc(false); });
  accMenu.querySelector('.acc-cta').addEventListener('click', (e) => e.preventDefault());

  /* ══════════ Вложения ══════════
     До трёх шаблонов и до трёх картинок. Пока есть свободное место,
     крайняя левая ячейка — «добавить»; остальные свободные стоят пустыми,
     как в макете. */
  const MAX = 3;
  const tpl = [], img = [];
  const TPL_ACCEPT = '.pptx,.potx,.pdf,.html,.htm';

  /* Шаблон уходит на сервер сразу после выбора; ответ перекрашивает
     миниатюру в палитру шаблона и подписывает, сколько образцов нашлось.
     Та же логика, что в api.ts у React-версии. */
  function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
  }
  async function uploadTemplate(file) {
    const body = new FormData();
    body.append('file', file);
    let r;
    try { r = await fetch('/api/templates', { method: 'POST', body }); }
    catch { throw new Error('сервер недоступен'); }
    const data = await r.json().catch(() => null);
    if (!r.ok) throw new Error((data && data.detail) || `сервер ответил ${r.status}`);
    return data;
  }

  function pick(accept, multiple, done) {
    const inp = Object.assign(document.createElement('input'),
      { type: 'file', accept, multiple, style: 'display:none' });
    inp.addEventListener('change', () => { done([...inp.files]); inp.remove(); });
    document.body.appendChild(inp);
    inp.click();
  }

  const TPL_LOOK = ['a', 'b', 'c'];                 // три вида миниатюры шаблона
  function tplMini(i) {
    const look = TPL_LOOK[i % TPL_LOOK.length];
    const extra = look === 'b' ? '<i class="t-line"></i>' : '';
    return `<span class="tpl-mini">
              <i class="t-bar"></i><i class="t-box"></i>
              <i class="t-line"></i><i class="t-line short"></i>${extra}
            </span>`;
  }

  function fill(host, list, kind) {
    host.textContent = '';
    const room = list.length < MAX;
    const cells = room ? [null, ...list] : [...list];
    while (cells.length < MAX) cells.push(undefined);    // пустые ячейки макета

    cells.forEach((item) => {
      if (item === null) {                               // ячейка «добавить»
        const b = document.createElement('button');
        b.className = 'slot slot--add';
        b.type = 'button';
        b.setAttribute('aria-label', kind === 'tpl' ? 'Добавить шаблон' : 'Добавить картинку');
        b.addEventListener('click', () => add(kind));
        host.appendChild(b);
        return;
      }
      const el = document.createElement('div');
      el.className = 'slot' + (item && item.state === 'busy' ? ' is-busy' : item && item.state === 'err' ? ' is-err' : '');
      if (item) {                                        // вложение
        el.innerHTML = (kind === 'img' && item.url)
          ? `<img class="slot__img" src="${item.url}" alt="">`
          : tplMini(list.indexOf(item));
        const mini = el.querySelector('.tpl-mini');
        if (mini && item.colors) {
          const c = item.colors;
          if (c.background) mini.style.setProperty('--tpl-bg', '#' + c.background);
          if (c.text) mini.style.setProperty('--tpl-ink', '#' + c.text);
          if (c.accent) mini.style.setProperty('--tpl-acc', '#' + c.accent);
        }
        const name = document.createElement('span');
        name.className = 'slot__name';
        if (item.note) {
          const meta = document.createElement('b');
          meta.className = 'slot__meta';
          meta.textContent = item.note;
          name.appendChild(meta);
          el.title = item.note;
        }
        name.append(item.name);                          // имя файла — только текстом
        const kill = document.createElement('button');
        kill.className = 'slot__kill';
        kill.type = 'button';
        kill.setAttribute('aria-label', `Убрать ${item.name}`);
        kill.addEventListener('click', () => {
          const at = list.indexOf(item);
          if (at >= 0) { if (item.url) URL.revokeObjectURL(item.url); list.splice(at, 1); }
          fill(host, list, kind);
        });
        el.append(name, kill);
      }
      host.appendChild(el);
    });
  }

  function add(kind) {
    const list = kind === 'tpl' ? tpl : img;
    const accept = kind === 'tpl' ? TPL_ACCEPT : 'image/*';
    pick(accept, true, (files) => {
      files.slice(0, MAX - list.length).forEach((f) => {
        if (kind === 'img') { list.push({ name: f.name, url: URL.createObjectURL(f) }); return; }
        const item = { name: f.name, state: 'busy', note: 'разбираем…' };
        list.push(item);
        uploadTemplate(f)
          .then((t) => Object.assign(item, {
            state: 'ok', id: t.id, colors: t.colors,
            note: `${t.patterns} ${plural(t.patterns, 'образец', 'образца', 'образцов')}${t.fonts[0] ? ' · ' + t.fonts[0] : ''}`,
          }))
          .catch((err) => Object.assign(item, { state: 'err', note: err.message }))
          .finally(() => fill(tplSlots, tpl, 'tpl'));
      });
      fill(kind === 'tpl' ? tplSlots : imgSlots, list, kind);
    });
  }

  fill(tplSlots, tpl, 'tpl');
  fill(imgSlots, img, 'img');

  /* ══════════ Переключение видов ══════════ */
  let view = 'edit', deckReady = false;
  function show(name) {
    view = name;
    Object.entries(views).forEach(([k, el]) => el.classList.toggle('is-on', k === name));
    navMove(name === 'edit' ? 0 : 1, true);
  }
  function go(target) {
    if (target === 'edit') { stopGen(); show('edit'); return; }
    if (deckReady) { show('show'); return; }
    startGen();
  }
  goBtn.addEventListener('click', startGen);
  promptBox.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') startGen();
  });

  /* ══════════ Генерация ══════════
     Прогресс закрашивает надпись ЦДС по диагонали, а под ней слова
     печатаются по букве и сменяют друг друга. */
  const WORDS = ['читаем задачу', 'подбираем макеты', 'пишем текст',
                 'раскладываем слайды', 'наводим красоту'];
  let genRaf = 0, genT0 = 0, progress = 0, wordAt = -1, wordT0 = 0;

  function setFill(pct) {
    progress = pct;
    genLogo.style.setProperty('--fill', pct.toFixed(1) + '%');
  }

  function stopGen() {
    cancelAnimationFrame(genRaf); genRaf = 0;
  }

  function startGen() {
    if (genRaf) return;
    acc(false);
    deckReady = false;
    setFill(0);
    wordAt = -1; wordT0 = 0;
    genWord.textContent = '';
    genCaret.style.opacity = 1;
    show('gen');
    genT0 = performance.now();
    genRaf = requestAnimationFrame(genStep);
  }

  const GEN_MS = 7200;                               // сколько идёт «генерация»
  function genStep(now) {
    const t = clamp((now - genT0) / GEN_MS, 0, 1);
    // лёгкая неравномерность — как у настоящей очереди задач
    const eased = t < 1 ? t + Math.sin(t * 7.3) * 0.035 : 1;
    setFill(clamp(eased, 0, 1) * 100);

    // слово меняется по ходу прогресса, каждое печатается по букве
    const next = Math.min(WORDS.length - 1, Math.floor(t * WORDS.length));
    if (next !== wordAt) { wordAt = next; wordT0 = now; }
    const typed = Math.floor((now - wordT0) / 34);
    const word = WORDS[wordAt];
    const cut = Math.min(word.length, typed);
    if (genWord.textContent !== word.slice(0, cut)) genWord.textContent = word.slice(0, cut);
    genCaret.style.opacity = cut < word.length ? 1 : 0;

    if (t < 1) { genRaf = requestAnimationFrame(genStep); return; }

    genRaf = 0;
    genWord.textContent = 'готово';
    genCaret.style.opacity = 0;
    buildDeck();
    setTimeout(() => { if (view === 'gen') { deckReady = true; show('show'); } }, 620);
  }

  /* ══════════ Демонстрация ══════════ */
  const LOOKS = ['a', 'b', 'c'];
  function slideMarkup(look) {
    const extra = look === 'b' ? '<b class="d-box2"></b>' : '';
    const num = look === 'c' ? '<b class="d-num">+38 %</b>' : '';
    return `<span class="deck-slide deck-slide--${look}">
              <i class="d-bar"></i><b class="d-box"></b>${extra}
              <i class="d-line"></i><i class="d-line short"></i>${num}
            </span>`;
  }

  function buildDeck() {
    rail.textContent = '';
    LOOKS.forEach((look, i) => {
      const b = document.createElement('button');
      b.className = 'thumb' + (i === 0 ? ' is-on' : '');
      b.type = 'button';
      b.style.setProperty('--k', '0.205');            // миниатюра — та же графика, мельче
      b.setAttribute('aria-label', `Слайд ${i + 1}`);
      b.innerHTML = slideMarkup(look);
      b.addEventListener('click', () => pickSlide(i));
      rail.appendChild(b);
    });
    pickSlide(0);
  }

  function pickSlide(i) {
    [...rail.children].forEach((el, k) => el.classList.toggle('is-on', k === i));
    canvas.style.setProperty('--k', '1');
    canvas.innerHTML = slideMarkup(LOOKS[i]);
  }

  /* ── Отладка ──
     ?view=edit|gen|show открывает нужное состояние, ?p=0…1 замораживает
     прогресс генерации: по статичному кадру видно, как закрашена надпись. */
  {
    const q = new URLSearchParams(location.search);
    const v = q.get('view'), pr = q.get('p');
    if (v || pr !== null) {
      buildDeck();
      if (pr !== null) {
        show('gen'); stopGen();
        setFill(clamp(+pr, 0, 1) * 100);
        const i = Math.min(WORDS.length - 1, Math.floor(+pr * WORDS.length));
        genWord.textContent = WORDS[i];
        genCaret.style.opacity = 0;
      } else if (views[v]) {
        show(v);
        if (v === 'show') deckReady = true;
      }
    }
  }

  /* ── Кадровый цикл: фон, кольцо курсора и автоподстройка качества ── */
  let bgTs = 0, mouseTs = -1e4, prevTs = 0, fpsEma = 16.7, slow = 0, fast = 0;
  addEventListener('mousemove', () => { mouseTs = performance.now(); }, { passive: true });

  (function frame(now) {
    if (fine && curShown) {
      curX += (mouse.x - curX) * 0.2;
      curY += (mouse.y - curY) * 0.2;
      const want = cur.classList.contains('is-on') ? 1.5 : 1;
      curScale += (want - curScale) * 0.15;
      cur.style.transform = `translate3d(${curX}px, ${curY}px, 0) scale(${curScale})`;
    }

    /* Окно — большое стекло: каждая перерисовка сетки заставляет браузер
       заново размывать его фон. Пока курсор стоит, сбавляем темп; как
       только его двигают, пузырь обязан успевать — идём полным. */
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

    requestAnimationFrame(frame);
  })(performance.now());
})();
