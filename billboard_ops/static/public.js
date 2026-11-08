/* The public pages' own motion, on top of motion.js: the power-on intro, depth as you scroll, the billboard that
   follows the pointer, shooting stars, magnetic buttons, an LED cursor, headings that rise word by word, labels that
   decode like an LED panel. Decoration only: the pages work without it. */
(function () {
  'use strict';
  var doc = document.documentElement;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var intro = $('.m-intro');
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { if (intro) intro.remove(); return; }
  var fine = window.matchMedia('(pointer: fine)').matches, rtl = doc.dir === 'rtl', en = (doc.lang || 'en') === 'en';

  // The power-on intro: once a visit, on the front page.
  if (intro) {
    if (doc.classList.contains('m-intro-on')) {
      try { sessionStorage.setItem('intro', '1'); } catch (e) {}
      setTimeout(function () { doc.classList.add('m-intro-out'); }, 950);
      setTimeout(function () { intro.remove(); doc.classList.remove('m-intro-on', 'm-intro-out'); }, 1800);
    } else intro.remove();
  }

  // Headings rise word by word as they come into view.
  function words(el) {
    if (el.dataset.split) return;
    el.dataset.split = '1';
    var parts = el.textContent.trim().split(/\s+/), html = '';
    parts.forEach(function (w, i) {
      html += '<span class="w"><span style="--w:' + i + '">' + w.replace(/[&<>"]/g, function (c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }) + '</span></span>' + (i < parts.length - 1 ? ' ' : '');
    });
    el.setAttribute('aria-label', el.textContent.trim());
    el.innerHTML = html;
    $$('.w', el).forEach(function (w) { w.setAttribute('aria-hidden', 'true'); });
  }
  $$('.h2, .final h2').forEach(words);

  // Labels decode like an LED panel warming up (English only: Arabic letters join, so they just fade in).
  var GLYPHS = '█▓▒░#*+=-01';
  function decode(el) {
    var target = el.textContent, n = target.length, t0 = performance.now(), dur = 650;
    (function f(t) {
      var p = Math.min(1, (t - t0) / dur), done = Math.floor(p * n), out = '';
      for (var i = 0; i < n; i++) out += i < done || target[i] === ' ' ? target[i] : GLYPHS[(Math.random() * GLYPHS.length) | 0];
      el.textContent = out;
      if (p < 1) requestAnimationFrame(f); else el.textContent = target;
    })(t0);
  }
  if (en && 'IntersectionObserver' in window) {
    var dio = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { dio.unobserve(e.target); decode(e.target); } });
    }, {threshold: .6});
    $$('.kicker, .playbar .np').forEach(function (el) { if (!el.children.length) dio.observe(el); });
  }

  // Depth as you scroll: the stars drift, the city sinks, the words lift away.
  var hero = $('.hero');
  if (hero) {
    var stars = $('.stars', hero), sky = $('.hero > .skyline'), copy = $('.grid > div:first-child', hero), stage = $('.board-stage', hero);
    var track = $('.marquee .track'), anim = track && track.getAnimations ? track.getAnimations()[0] : null;
    var lastY = scrollY, vel = 0, ticking = false;
    var paint = function () {
      ticking = false;
      var y = scrollY, h = hero.offsetHeight;
      if (y < h * 1.2) {
        if (stars) stars.style.transform = 'translateY(' + (y * .35) + 'px)';
        if (sky) sky.style.transform = 'translateY(' + (y * .12) + 'px)';
        if (copy) { copy.style.transform = 'translateY(' + (y * -.08) + 'px)'; copy.style.opacity = Math.max(0, 1 - y / (h * .75)); }
        if (stage) stage.style.translate = '0 ' + (y * .06) + 'px';
      }
      vel = vel * .8 + (y - lastY) * .2; lastY = y;
      if (track) track.style.transform = 'skewX(' + Math.max(-12, Math.min(12, -vel * .6)) + 'deg)';
      if (anim) anim.playbackRate = 1 + Math.min(5, Math.abs(vel) * .25);
    };
    addEventListener('scroll', function () { if (!ticking) { ticking = true; requestAnimationFrame(paint); } }, {passive: true});
    // Let the marquee settle back to its own pace.
    setInterval(function () { if (Math.abs(vel) > .05) { vel *= .7; paint(); } }, 120);

    // The billboard turns to follow the pointer.
    if (fine && stage) {
      hero.addEventListener('pointermove', function (e) {
        var r = hero.getBoundingClientRect();
        stage.style.setProperty('--bx', ((e.clientX - r.left) / r.width - .5).toFixed(3));
        stage.style.setProperty('--by', ((e.clientY - r.top) / r.height - .5).toFixed(3));
      });
      hero.addEventListener('pointerleave', function () { stage.style.setProperty('--bx', 0); stage.style.setProperty('--by', 0); });
    }

    // Shooting stars, now and then.
    if (stars) (function shoot() {
      var s = document.createElement('b');
      s.className = 'shoot';
      s.style.top = (Math.random() * 40) + '%';
      s.style.left = (rtl ? 10 + Math.random() * 40 : 40 + Math.random() * 55) + '%';
      stars.appendChild(s);
      setTimeout(function () { s.remove(); }, 1500);
      setTimeout(shoot, 2600 + Math.random() * 4200);
    })();
  }

  // The side panels on sign-up and sign-in: the billboard leans towards the pointer too.
  $$('.split .side').forEach(function (side) {
    var stage = $('.board-stage', side);
    if (!fine || !stage) return;
    side.addEventListener('pointermove', function (e) {
      var r = side.getBoundingClientRect();
      stage.style.setProperty('--bx', ((e.clientX - r.left) / r.width - .5).toFixed(3));
      stage.style.setProperty('--by', ((e.clientY - r.top) / r.height - .5).toFixed(3));
    });
    side.addEventListener('pointerleave', function () { stage.style.setProperty('--bx', 0); stage.style.setProperty('--by', 0); });
  });

  // The billboard flickers as the sign-up form changes what it shows.
  $$('[data-src]').forEach(function (inp) {
    inp.addEventListener('input', function () {
      $$('.board .screen').forEach(function (s) { s.classList.remove('flick'); void s.offsetWidth; s.classList.add('flick'); });
    });
  });

  // The last call glows where the pointer is.
  var fin = $('.final');
  if (fin && fine) fin.addEventListener('pointermove', function (e) {
    var r = fin.getBoundingClientRect();
    fin.style.setProperty('--ax', ((e.clientX - r.left) / r.width * 100).toFixed(1) + '%');
    fin.style.setProperty('--ay', ((e.clientY - r.top) / r.height * 100).toFixed(1) + '%');
  });

  if (!fine) return;

  // Buttons lean towards the pointer.
  $$('.btn.glow, .btn.ghost, .btn.dark, .signin-link, .chip').forEach(function (b) {
    var pull = b.classList.contains('chip') ? .18 : .3;
    b.addEventListener('pointermove', function (e) {
      var r = b.getBoundingClientRect();
      b.style.translate = ((e.clientX - r.left - r.width / 2) * pull).toFixed(1) + 'px ' + ((e.clientY - r.top - r.height / 2) * pull).toFixed(1) + 'px';
    });
    b.addEventListener('pointerleave', function () { b.style.translate = ''; });
  });

  // An LED ring that trails the pointer, and grows over anything you can press.
  var cur = document.createElement('div');
  cur.className = 'm-cursor';
  document.body.appendChild(cur);
  var tx = -100, ty = -100, cx = -100, cy = -100;
  addEventListener('pointermove', function (e) {
    if (e.pointerType !== 'mouse') return;
    tx = e.clientX; ty = e.clientY; cur.classList.add('on');
    var over = e.target.closest && e.target.closest('a, button, summary, label, input, select, textarea, .scard, .tile');
    cur.classList.toggle('big', !!over && !e.target.closest('input, textarea, select'));
  });
  document.addEventListener('pointerleave', function () { cur.classList.remove('on'); });
  addEventListener('pointerdown', function () { cur.classList.add('down'); });
  addEventListener('pointerup', function () { cur.classList.remove('down'); });
  (function follow() {
    cx += (tx - cx) * .2; cy += (ty - cy) * .2;
    cur.style.transform = 'translate(' + cx.toFixed(1) + 'px,' + cy.toFixed(1) + 'px)';
    requestAnimationFrame(follow);
  })();
})();
