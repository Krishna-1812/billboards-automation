/* The public pages: the opening curtain, words rising out of a mask, the posters taking turns on the big screen,
   the Dubai clock, the loop dial, the index preview that follows the pointer, and the live preview on sign-up.
   Decoration only: every page reads and works without it. */
(function () {
  'use strict';
  var doc = document.documentElement;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var fine = window.matchMedia('(pointer: fine)').matches;
  var lang = doc.lang || 'en';
  var fmt = function (n) { return Math.round(n).toLocaleString('en'); };

  // The Dubai clock, wherever it appears.
  var tf = new Intl.DateTimeFormat('en-GB', {timeZone: 'Asia/Dubai', hour: '2-digit', minute: '2-digit', hour12: false});
  function tick() {
    var t = tf.format(new Date()).split(':');
    $$('[data-clock] [data-t]').forEach(function (el) { el.innerHTML = t[0] + '<span class="colon">:</span>' + t[1]; });
  }
  tick(); setInterval(tick, 15000);

  // Words rise out of a mask. Italic words stay italic.
  function split(el) {
    var i = 0, out = document.createDocumentFragment();
    el.setAttribute('aria-label', el.textContent.replace(/\s+/g, ' ').trim());
    function word(text, wrapTag) {
      var w = document.createElement('span'), s = document.createElement('span');
      w.className = 'w'; w.setAttribute('aria-hidden', 'true');
      s.style.setProperty('--w', i++);
      if (wrapTag) { var e = document.createElement(wrapTag); e.textContent = text; s.appendChild(e); } else s.textContent = text;
      w.appendChild(s);
      return w;
    }
    Array.prototype.slice.call(el.childNodes).forEach(function (node) {
      var tag = node.nodeType === 1 ? node.tagName.toLowerCase() : null;
      var parts = (node.textContent || '').split(/(\s+)/);
      parts.forEach(function (p) {
        if (!p) return;
        if (/^\s+$/.test(p)) out.appendChild(document.createTextNode(' '));
        else out.appendChild(word(p, tag === 'em' ? 'em' : null));
      });
    });
    el.textContent = '';
    el.appendChild(out);
  }
  if (!reduce) $$('[data-split]').forEach(split);

  // The curtain, once a visit, on the front page only.
  var curtain = $('.curtain');
  function show() { doc.classList.add('shown'); }
  if (doc.classList.contains('m-intro-on') && curtain && !reduce) {
    try { sessionStorage.setItem('intro', '1'); } catch (e) {}
    var count = $('.c-count', curtain), t0 = performance.now(), dur = 950;
    (function f(t) {
      var p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      if (count) count.textContent = String(Math.round(e * 100)).padStart(3, '0');
      curtain.style.setProperty('--p', e);
      if (p < 1) requestAnimationFrame(f);
    })(t0);
    setTimeout(function () { doc.classList.add('m-intro-out'); }, 1100);
    setTimeout(show, 1350);
    setTimeout(function () { curtain.remove(); doc.classList.remove('m-intro-on', 'm-intro-out'); }, 2400);
  } else {
    doc.classList.remove('m-intro-on');
    if (curtain) curtain.remove();
    requestAnimationFrame(function () { requestAnimationFrame(show); });
  }

  // Numbers count up when they come into view.
  function countTo(el, to, ms, suffix) {
    suffix = suffix || '';
    if (reduce) { el.textContent = fmt(to) + suffix; return; }
    var from = parseFloat((el.textContent || '0').replace(/[^\d.]/g, '')) || 0, t0 = performance.now();
    (function f(t) {
      var p = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - p, 4);
      el.textContent = fmt(from + (to - from) * e) + suffix;
      if (p < 1) requestAnimationFrame(f);
    })(t0);
  }

  // Things rise into view.
  var targets = $$('.reveal, .steps, .numbers, [data-split]').filter(function (el) { return !el.closest('[data-hero]'); });
  if ('IntersectionObserver' in window && !reduce) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        io.unobserve(e.target);
        e.target.classList.add('in');
        $$('[data-count]', e.target).forEach(function (n) { n.textContent = '0'; countTo(n, +n.dataset.count, 1600); });
      });
    }, {threshold: .15, rootMargin: '0px 0px -8% 0px'});
    targets.forEach(function (el) { io.observe(el); });
  } else targets.forEach(function (el) { el.classList.add('in'); });

  // The top bar: solid once you scroll, out of the way while you read down, back when you scroll up.
  var bar = $('.ptop'), lastY = scrollY;
  if (bar) {
    var onScroll = function () {
      var y = scrollY;
      bar.classList.toggle('scrolled', y > 24);
      var under = document.elementFromPoint(innerWidth / 2, bar.offsetHeight + 4);
      bar.classList.toggle('over-dark', !!(under && under.closest('.dark, .pfoot')));
      if (!reduce) bar.classList.toggle('away', y > 320 && y > lastY + 2 && !bar.contains(document.activeElement));
      if (y < lastY - 2) bar.classList.remove('away');
      lastY = y;
    };
    addEventListener('scroll', onScroll, {passive: true}); onScroll();
  }

  // The big screen: posters take turns, the way advertisers do in a loop.
  $$('[data-stage]').forEach(function (st) {
    var posters = $$('.poster', st), track = $('.track i', st), slot = $('[data-slot]', st);
    var total = +st.dataset.slots || 12, ms = +st.dataset.slotMs || 4200, i = 0;
    var where = slot && slot.textContent.indexOf(' · ') > 0 ? slot.textContent.slice(slot.textContent.indexOf(' · ')) : '';
    st.style.setProperty('--slot-ms', ms + 'ms');
    function run() { if (!track) return; track.classList.remove('run'); void track.offsetWidth; track.classList.add('run'); }
    function next() {
      var cur = posters[i % posters.length];
      i++;
      var nx = posters[i % posters.length];
      posters.forEach(function (p) { p.classList.remove('was'); });
      cur.classList.remove('on'); cur.classList.add('was');
      nx.classList.remove('was'); void nx.offsetWidth; nx.classList.add('on');
      if (slot) slot.textContent = slot.dataset.tpl.replace('{n}', String(i % total + 1).padStart(2, '0'))
        .replace('{total}', String(total).padStart(2, '0')) + where;
      run();
    }
    run();
    if (!reduce && posters.length > 1) setInterval(function () { if (!document.hidden) next(); }, ms);

    // On the front page the screen grows to the full width as you scroll to it.
    if (!reduce && st.closest('.hero')) {
      var screen = $('.stage', st), wrap = $('.wrap');
      var paint = function () {
        var g = wrap ? parseFloat(getComputedStyle(wrap).paddingLeft) : 40;
        var r = st.getBoundingClientRect(), p = Math.max(0, Math.min(1, (innerHeight - r.top) / (innerHeight * .95) - .15));
        screen.style.setProperty('--inset', (g * (1 - p)).toFixed(1) + 'px');
        screen.style.setProperty('--rad', (6 * (1 - p)).toFixed(1) + 'px');
      };
      addEventListener('scroll', function () { requestAnimationFrame(paint); }, {passive: true});
      addEventListener('resize', paint); paint();
    }
  });

  // The loop dial: one arc a slot; yours in orange. The tabs book more of the loop.
  $$('[data-dial]').forEach(function (d) {
    var n = +d.dataset.slots, g = $('[data-ticks]', d), R = 168, C = 200, gap = n > 30 ? .8 : 2.2;
    var perHour = +d.dataset.ph, perDay = +d.dataset.pd, arcs = [];
    for (var i = 0; i < n; i++) {
      var a0 = i / n * 360 + gap / 2, a1 = (i + 1) / n * 360 - gap / 2;
      var pt = function (a) { return [C + R * Math.sin(a * Math.PI / 180), C - R * Math.cos(a * Math.PI / 180)]; };
      var p0 = pt(a0), p1 = pt(a1), path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', 'M' + p0[0].toFixed(2) + ' ' + p0[1].toFixed(2) + ' A' + R + ' ' + R + ' 0 ' + (a1 - a0 > 180 ? 1 : 0) + ' 1 ' + p1[0].toFixed(2) + ' ' + p1[1].toFixed(2));
      path.setAttribute('class', 'tick');
      g.appendChild(path); arcs.push(path);
    }
    var hour = $('b[data-per-hour]', d), day = $('[data-n-day]', d.parentNode);
    function pick(k, animate) {
      var mine = {};
      for (var j = 0; j < k; j++) mine[Math.floor(j * n / k)] = 1;
      arcs.forEach(function (a, j) { a.classList.toggle('mine', !!mine[j]); });
      if (animate) { countTo(hour, perHour * k, 700); countTo(day, perDay * k, 700); }
      else { hour.textContent = fmt(perHour * k); day.textContent = fmt(perDay * k); }
    }
    var tabs = $$('[data-k]', d.closest('section'));
    tabs.forEach(function (b) {
      b.addEventListener('click', function () {
        tabs.forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
        pick(+b.dataset.k, true);
      });
    });
    pick(1, false);
  });

  // The index: a preview of the screen follows the pointer along the rows.
  if (fine && !reduce && $('[data-peek]')) {
    var THEMES = [['#11110F', '#F2EFE8'], ['#B84A2A', '#F7E9DA'], ['#16301F', '#E8F0E2'], ['#D8CBB0', '#11110F'], ['#1F2FB8', '#F2F0FF']];
    var pos = document.createElement('div'), peek = document.createElement('div');
    pos.className = 'peek-pos'; peek.className = 'peek';
    peek.innerHTML = '<span></span><b></b>';
    pos.appendChild(peek); document.body.appendChild(pos);
    var tx = 0, ty = 0, cx = 0, cy = 0, on = false;
    $$('[data-peek]').forEach(function (row) {
      row.addEventListener('pointerenter', function (e) {
        var th = THEMES[+row.dataset.theme % THEMES.length];
        peek.style.background = th[0]; peek.style.color = th[1];
        peek.firstChild.textContent = row.dataset.peek; peek.lastChild.textContent = row.dataset.peekName;
        if (!on) { cx = tx = e.clientX; cy = ty = e.clientY; }
        on = true; peek.classList.add('on');
      });
      row.addEventListener('pointerleave', function () { on = false; peek.classList.remove('on'); });
    });
    addEventListener('pointermove', function (e) { tx = e.clientX; ty = e.clientY; });
    (function follow() {
      cx += (tx - cx) * .14; cy += (ty - cy) * .14;
      pos.style.transform = 'translate(' + (cx + 28).toFixed(1) + 'px,' + (cy - 90).toFixed(1) + 'px)';
      requestAnimationFrame(follow);
    })();
  }

  // The footer's wordmark fills the width exactly.
  function fit() {
    $$('[data-fit]').forEach(function (el) {
      el.style.fontSize = '100px';
      var w = el.scrollWidth, avail = el.parentNode.clientWidth - parseFloat(getComputedStyle(el.parentNode).paddingLeft) * 2;
      if (w) el.style.fontSize = (100 * avail / w).toFixed(2) + 'px';
    });
  }
  fit(); addEventListener('resize', fit);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);

  // Sign-up: the preview screen shows the brand and the dates as they're typed.
  var co = $('[data-src=company]'), sd = $('[data-src=start]'), ed = $('[data-src=end]');
  if (co || sd) {
    var big = $('[data-live=company]'), when = $('[data-live=when]');
    var brand = big ? big.dataset.default : '', onAir = when ? when.dataset.default : '';
    var df = function (v) {
      try { return new Date(v + 'T00:00:00').toLocaleDateString(lang === 'ar' ? 'ar-AE' : 'en-GB', {day: 'numeric', month: 'short'}); }
      catch (e) { return v; }
    };
    var paintPreview = function (flick) {
      var name = (co && co.value.trim()) || brand;
      if (big && big.textContent !== name) {
        big.textContent = name;
        big.style.fontSize = name.length > 24 ? 'clamp(26px, 2.6vw, 40px)' : (name.length > 14 ? 'clamp(32px, 3.4vw, 56px)' : '');
        if (flick && !reduce) { big.classList.remove('flick'); void big.offsetWidth; big.classList.add('flick'); }
      }
      if (when) when.textContent = sd && sd.value ? df(sd.value) + (ed && ed.value ? ' – ' + df(ed.value) : '') : onAir;
    };
    [co, sd, ed].forEach(function (el) { if (el) el.addEventListener('input', function () { paintPreview(true); }); });
    paintPreview(false);
  }
})();
