/* Motion for every page (see motion.css). Nothing here is needed for the page to work: if this file doesn't load,
   or the visitor asks for less motion, the pages are simply still. */
(function () {
  'use strict';
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var fine = window.matchMedia('(pointer: fine)').matches;
  var doc = document.documentElement, rtl = doc.dir === 'rtl', lang = doc.lang || 'en';
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var Motion = window.Motion = {};
  var quiet = document.body.classList.contains('quiet');   // the public pages: no ripples, no confetti

  // Order: each child of a grid of tiles, each table row and each menu item knows its place, for the stagger.
  function order() {
    $$('.bento, .grid, .qcards, .cols-2, .cols-3, .cols-4').forEach(function (g) {
      Array.prototype.forEach.call(g.children, function (c, i) { c.style.setProperty('--i', Math.min(i, 14)); });
    });
    $$('tbody, table').forEach(function (t) {
      var rows = $$(':scope > tr', t);
      rows.forEach(function (r, i) { if (i < 30) r.style.setProperty('--i', i); else r.style.animation = 'none'; });
    });
    $$('.ticks').forEach(function (t) { Array.prototype.forEach.call(t.children, function (c, i) { c.style.setProperty('--i', i); }); });
    $$('.dropdown').forEach(function (d) { Array.prototype.forEach.call(d.children, function (c, i) { c.style.setProperty('--i', i); }); });
    // Once something has arrived, let go of its entrance so hover effects can take over.
    document.addEventListener('animationend', function (e) {
      if (e.animationName === 'm-rise' && e.target.style) e.target.style.animation = 'none';
    });
  }
  order();
  if (reduce) return;

  // The loading bar: a click on a link or a form sent shows the next page is on its way.
  var load = document.createElement('div');
  load.className = 'm-load';
  document.body.appendChild(load);
  var loadTimer = null;
  function startLoad() {
    load.classList.add('on'); load.style.width = '0';
    requestAnimationFrame(function () { load.style.width = '35%'; });
    clearTimeout(loadTimer);
    loadTimer = setTimeout(function () { load.style.width = '72%'; }, 450);
    setTimeout(stopLoad, 8000);       // a download doesn't leave the page: don't spin for ever
  }
  function stopLoad() { load.style.width = '100%'; setTimeout(function () { load.classList.remove('on'); load.style.width = '0'; }, 260); }
  window.addEventListener('pageshow', stopLoad);
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    var href = a.getAttribute('href');
    if (!href || href[0] === '#' || a.target === '_blank' || a.hasAttribute('download') || /^(mailto|tel|javascript):/.test(href)) return;
    if (/[?&]format=|\/pdf\b|\/file\b|\.(pdf|csv|xlsx|zip)\b/.test(href)) return;
    if (a.origin && a.origin !== location.origin) return;
    startLoad();
  });

  // Forms: the button you pressed shows a spinner until the next page arrives.
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (e.defaultPrevented || f.target === '_blank') return;
    var b = e.submitter;
    if (b && b.matches('button') && !b.classList.contains('linkish') && !/format|export|pdf/.test((f.action || '') + (b.name || ''))) {
      setTimeout(function () { b.classList.add('m-busy'); }, 0);
      setTimeout(function () { b.classList.remove('m-busy'); }, 8000);
    }
    if ((f.method || 'get').toLowerCase() === 'post' || f.action) startLoad();
  });
  window.addEventListener('pageshow', function () { $$('.m-busy').forEach(function (b) { b.classList.remove('m-busy'); }); });

  // A ripple where you press.
  document.addEventListener('pointerdown', function (e) {
    var b = e.target.closest && e.target.closest('button:not(.linkish), .btn, .chip[type=button], .doors button, .navpill > a');
    if (!b || b.disabled || quiet) return;
    var r = b.getBoundingClientRect(), d = Math.max(r.width, r.height) * 2.2, s = document.createElement('span');
    s.className = 'm-ripple';
    s.style.cssText = 'width:' + d + 'px;height:' + d + 'px;left:' + (e.clientX - r.left - d / 2) + 'px;top:' + (e.clientY - r.top - d / 2) + 'px';
    b.appendChild(s);
    setTimeout(function () { s.remove(); }, 700);
  });

  // The scroll bar along the top.
  if (document.body.scrollHeight > innerHeight * 1.6) {
    var bar = document.createElement('div');
    bar.className = 'm-scroll';
    document.body.appendChild(bar);
    var sc = function () { var m = document.body.scrollHeight - innerHeight; bar.style.setProperty('--p', m > 0 ? Math.min(1, scrollY / m) : 0); };
    addEventListener('scroll', sc, {passive: true}); sc();
  }

  // Numbers count up the first time you see them.
  var NUM = /^(\D{0,6}?)(\d[\d,]*)(\.\d+)?(\D{0,8})$/;
  function countUp(el, to, dur) {
    var m = (el.dataset.countText || el.textContent).trim().match(NUM);
    if (to === undefined) {
      if (!m) return;
      to = parseFloat(m[2].replace(/,/g, '') + (m[3] || ''));
    }
    var pre = m ? m[1] : '', post = m ? m[4] : '', dec = m && m[3] ? m[3].length - 1 : 0;
    var useLocale = !!(m && m[2].indexOf(',') >= 0) || to >= 1000;
    var t0 = performance.now(); dur = dur || Math.min(1600, 700 + to * 8);
    (function f(t) {
      var p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 4), v = to * e;
      var txt = dec ? v.toFixed(dec) : Math.round(v);
      el.textContent = pre + (useLocale ? Number(txt).toLocaleString('en', {minimumFractionDigits: dec, maximumFractionDigits: dec}) : txt) + post;
      if (p < 1) requestAnimationFrame(f);
    })(t0);
  }
  Motion.countUp = countUp;
  var counters = $$('.stat .num, .tile .big, .kpi, [data-count-up]').filter(function (el) {
    return el.children.length === 0 && NUM.test(el.textContent.trim()) && !el.closest('[data-count]');
  });
  if ('IntersectionObserver' in window && counters.length) {
    var cio = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { cio.unobserve(e.target); countUp(e.target); } });
    }, {threshold: .4});
    counters.forEach(function (el) { el.dataset.countText = el.textContent.trim(); cio.observe(el); });
  }

  // Tiles tilt towards the pointer and catch the light.
  function tilt(el, max) {
    el.classList.add('m-tilt', 'm-glare');
    var raf = 0;
    el.addEventListener('pointermove', function (e) {
      var r = el.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(function () {
        el.classList.add('m-hot');
        el.style.setProperty('--ry', ((x - .5) * max).toFixed(2) + 'deg');
        el.style.setProperty('--rx', ((.5 - y) * max).toFixed(2) + 'deg');
        el.style.setProperty('--gx', (x * 100).toFixed(1) + '%');
        el.style.setProperty('--gy', (y * 100).toFixed(1) + '%');
      });
    });
    el.addEventListener('pointerleave', function () {
      cancelAnimationFrame(raf);
      el.classList.remove('m-hot');
      el.style.setProperty('--rx', '0deg'); el.style.setProperty('--ry', '0deg');
    });
  }
  Motion.tilt = tilt;
  if (fine) {
    $$('.bento > .tile, .bento > a.card, .grid > .card.stat, .stat.mini, .qcards > *, .tile.s5, .tile.s7, .scard, .stats .stat, .po').forEach(function (el) {
      var big = el.getBoundingClientRect().width > 520;
      tilt(el, big ? 3 : 7);
    });
    $$('.bento > .card').forEach(function (el) { el.classList.add('m-glare'); });
  }

  // The overview chart draws itself.
  $$('svg path[stroke][fill=none]').forEach(function (p) {
    if (p.closest('.ring, .logo, button, a, .iconbtn, summary, .skyline') || !p.getAttribute('d') || p.getAttribute('d').length < 40) return;
    p.setAttribute('pathLength', '1'); p.classList.add('m-draw');
    var svg = p.ownerSVGElement;
    $$('path[fill]:not([fill=none])', svg).forEach(function (a) { a.classList.add('m-area'); });
    $$('circle', svg).forEach(function (c, i) { c.classList.add('m-dot'); c.style.setProperty('--i', i); });
  });

  // Celebrations: confetti in the brand colours for the big moments.
  var COLORS = ['#8F80FF', '#5A49E6', '#FFB394', '#FF7A52', '#7FE0BF', '#FFD166'];
  function confetti(x, y, n) {
    var box = document.createElement('div');
    box.className = 'm-confetti';
    document.body.appendChild(box);
    x = x === undefined ? innerWidth / 2 : x; y = y === undefined ? innerHeight / 3 : y; n = n || 90;
    var bits = [];
    for (var i = 0; i < n; i++) {
      var b = document.createElement('i'), a = Math.random() * Math.PI * 2, v = 6 + Math.random() * 10;
      b.style.background = COLORS[i % COLORS.length];
      if (Math.random() < .3) b.style.borderRadius = '50%';
      box.appendChild(b);
      bits.push({el: b, x: x, y: y, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 7, r: Math.random() * 360, vr: (Math.random() - .5) * 24});
    }
    var t0 = performance.now();
    (function f(t) {
      var age = (t - t0) / 1000;
      bits.forEach(function (b) {
        b.vy += .42; b.vx *= .985; b.x += b.vx; b.y += b.vy; b.r += b.vr;
        b.el.style.transform = 'translate(' + b.x + 'px,' + b.y + 'px) rotate(' + b.r + 'deg)';
        b.el.style.opacity = Math.max(0, 1 - age / 2.2);
      });
      if (age < 2.3) requestAnimationFrame(f); else box.remove();
    })(t0);
  }
  Motion.confetti = confetti;
  var BIG = /confirm|accepted|approved|is live|went live|placed|booked|sent|ready in the outbox|purchase order|thank/i;
  $$('.flash.ok, .alert.ok').forEach(function (f) {
    if (quiet || !BIG.test(f.textContent)) return;
    var r = f.getBoundingClientRect();
    setTimeout(function () { confetti(r.left + r.width / 2, r.top + r.height / 2, 110); }, 350);
  });
  $$('[data-confetti]').forEach(function (el) {
    var r = el.getBoundingClientRect();
    setTimeout(function () { confetti(r.left + r.width / 2, r.top + 40, 140); }, 500);
  });

  // Make the next page arrive faster: fetch it when the pointer rests on its link.
  if (HTMLScriptElement.supports && HTMLScriptElement.supports('speculationrules')) {
    var rules = document.createElement('script');
    rules.type = 'speculationrules';
    // Never pages that change something just by opening them (the notices page marks them read), or downloads.
    rules.textContent = JSON.stringify({prefetch: [{source: 'document', eagerness: 'moderate', where: {and: [
      {href_matches: '/*'},
      {not: {selector_matches: '[download], [target=_blank], [rel~=nofollow], [href*="format="], [href*="/notices"], ' +
        '[href*="/portal/link/"], [href*="/upload/"], [href*="/pdf"], [href*="/file"], [href*="/export"], [href*="/logout"]'}}]}}]});
    document.head.appendChild(rules);
  }
})();
