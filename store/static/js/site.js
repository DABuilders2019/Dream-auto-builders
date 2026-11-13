/* Dream Auto Builders storefront. Pages work without this file; it adds search suggestions,
   the Year/Make/Model menus, the slideshows, filters that apply on click, and add-to-cart without a reload. */
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var money = function (n) { return n == null ? '' : '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
  var reduceMotion = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var SVG = function (path, s) { return '<svg width="' + (s || 16) + '" height="' + (s || 16) + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + path + '</svg>'; };
  var IC = {
    filter: '<path d="M3 5h18l-7 8v6l-4 2v-8z"/>', shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>', hash: '<path d="M5 9h14M4 15h14M10 3L8 21M16 3l-2 18"/>',
    car: '<path d="M3 16v-3l2-5h11l3 4h2v4h-2"/><path d="M3 16h2m4 0h6"/><circle cx="7" cy="16.5" r="2"/><circle cx="17" cy="16.5" r="2"/><path d="M6 12h13"/>',
    check: '<path d="M4 12.5l5 5L20 6.5"/>', checkc: '<circle cx="12" cy="12" r="9.5"/><path d="M7.5 12.5l3 3 6-6.5"/>'
  };

  /* ---------- toast ---------- */
  var toastTimer;
  function toast(html) {
    var t = $('#toast'); if (!t) return;
    t.innerHTML = html; t.classList.add('show');
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.classList.remove('show'); }, 2800);
  }

  /* ---------- Year / Make / Model ---------- */
  function getJSON(url, signal) {
    return fetch(url, { headers: { Accept: 'application/json' }, signal: signal, credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
  }
  $$('form[data-ymm]').forEach(function (f) {
    var y = $('[data-y]', f), m = $('[data-m]', f), d = $('[data-d]', f), go = $('[data-go]', f);
    var reset = function (sel, label) { sel.innerHTML = '<option value="">' + label + '</option>'; sel.disabled = true; };
    y.addEventListener('change', function () {
      reset(m, 'Make'); reset(d, 'Model'); go.disabled = true;
      if (!y.value) return;
      m.innerHTML = '<option value="">Loading…</option>';
      getJSON('/api/vehicles/makes?year=' + encodeURIComponent(y.value)).then(function (list) {
        m.innerHTML = '<option value="">Make</option>' + list.map(function (x) { return '<option>' + esc(x) + '</option>'; }).join('');
        m.disabled = false; m.focus();
      }).catch(function () { reset(m, 'Make'); toast('Could not load makes. Please try again.'); });
    });
    m.addEventListener('change', function () {
      reset(d, 'Model'); go.disabled = true;
      if (!m.value) return;
      d.innerHTML = '<option value="">Loading…</option>';
      getJSON('/api/vehicles/models?year=' + encodeURIComponent(y.value) + '&make=' + encodeURIComponent(m.value)).then(function (list) {
        var opt = function (x) { return '<option value="' + x.id + '">' + esc(x.model) + '</option>'; };
        var groups = [];
        list.forEach(function (x) { var g = groups[groups.length - 1]; if (!g || g.key !== x.group) groups.push(g = { key: x.group, label: x.group_label, items: [] }); g.items.push(x); });
        d.innerHTML = '<option value="">Model</option>' + (groups.length < 2 ? list.map(opt).join('') :
          groups.map(function (g) { return '<optgroup label="' + esc(g.label) + '">' + g.items.map(opt).join('') + '</optgroup>'; }).join(''));
        d.disabled = false; d.focus();
      }).catch(function () { reset(d, 'Model'); toast('Could not load models. Please try again.'); });
    });
    d.addEventListener('change', function () { go.disabled = !d.value; if (d.value) go.focus(); });
  });

  /* ---------- Year / Make / Model menus drawn by the page (computers only) ----------
     A browser's own dropdown follows the computer's theme (dark gray on a Mac in dark mode) and can't style
     group headings, so on computers these menus open a white list drawn by the page, with red group headings
     in the Model menu. Phones keep their built-in picker. The real <select> stays in place for the form. */
  var finePointer = window.matchMedia && matchMedia('(hover: hover) and (pointer: fine)').matches;
  function groupedMenu(sel) {
    var wrap = document.createElement('span'); wrap.className = 'ddwrap';
    sel.parentNode.insertBefore(wrap, sel); wrap.appendChild(sel);
    var panel = document.createElement('div'); panel.className = 'ddpanel'; panel.setAttribute('role', 'listbox'); panel.hidden = true;
    panel.id = (sel.id || 'dd') + 'List'; wrap.appendChild(panel);
    var opts = [], at = -1;
    var typed = '', typedAt = 0;
    function mark(i) {
      opts.forEach(function (o, j) { o.classList.toggle('on', j === i); });
      at = i; if (opts[i]) opts[i].scrollIntoView({ block: 'nearest' });
    }
    function build() {
      panel.innerHTML = ''; opts = [];
      var add = function (o) {
        if (!o.value) return;
        var d = document.createElement('div'); d.className = 'ddopt'; d.setAttribute('role', 'option');
        d.textContent = o.text; d.dataset.value = o.value; d.setAttribute('aria-selected', String(o.selected));
        d.addEventListener('mousedown', function (e) { e.preventDefault(); choose(o.value); });
        d.addEventListener('mousemove', function () { mark(opts.indexOf(d)); });
        panel.appendChild(d); opts.push(d);
      };
      Array.prototype.forEach.call(sel.children, function (ch) {
        if (ch.tagName === 'OPTGROUP') {
          var h = document.createElement('div'); h.className = 'ddhead'; h.textContent = ch.label; h.setAttribute('role', 'presentation');
          panel.appendChild(h); Array.prototype.forEach.call(ch.children, add);
        } else add(ch);
      });
    }
    function open() {
      if (sel.disabled || !Array.prototype.some.call(sel.options, function (o) { return o.value; })) return false;
      build(); panel.hidden = false; wrap.classList.add('open'); sel.setAttribute('aria-expanded', 'true');
      var cur = opts.findIndex(function (o) { return o.dataset.value === sel.value; });
      mark(cur < 0 ? 0 : cur); return true;
    }
    function close() { panel.hidden = true; wrap.classList.remove('open'); sel.setAttribute('aria-expanded', 'false'); }
    function choose(v) { sel.value = v; close(); sel.dispatchEvent(new Event('change', { bubbles: true })); }
    sel.setAttribute('aria-controls', panel.id);
    sel.addEventListener('mousedown', function (e) {
      if (sel.disabled) return;
      e.preventDefault(); sel.focus();
      if (panel.hidden) open(); else close();
    });
    sel.addEventListener('keydown', function (e) {
      if (panel.hidden) {
        if ((e.key === ' ' || e.key === 'Enter' || (e.altKey && e.key === 'ArrowDown')) && open()) e.preventDefault();
        return;
      }
      if (e.key === 'ArrowDown') { mark(Math.min(at + 1, opts.length - 1)); e.preventDefault(); }
      else if (e.key === 'ArrowUp') { mark(Math.max(at - 1, 0)); e.preventDefault(); }
      else if (e.key === 'Enter' || e.key === ' ') { if (opts[at]) choose(opts[at].dataset.value); e.preventDefault(); }
      else if (e.key === 'Escape' || e.key === 'Tab') close();
      else if (e.key.length === 1 && /\S/.test(e.key)) {          /* type to jump: "20" -> 2026..., "fo" -> Ford */
        var now = Date.now(); typed = (now - typedAt < 800 ? typed : '') + e.key.toLowerCase(); typedAt = now;
        var i = opts.findIndex(function (o) { return o.textContent.toLowerCase().indexOf(typed) === 0; });
        if (i >= 0) mark(i);
        e.preventDefault();
      }
    });
    sel.addEventListener('blur', function () { setTimeout(close, 120); });
  }
  /* ---------- click tracking: anything marked data-track="..." (see store/tracking.py) ---------- */
  var noTrack = navigator.doNotTrack === '1' || navigator.globalPrivacyControl === true;
  document.addEventListener('click', function (e) {
    var el = e.target.closest && e.target.closest('[data-track]');
    if (!el || noTrack) return;
    var body = JSON.stringify({ t: el.getAttribute('data-track').slice(0, 120), p: location.pathname });
    try {
      if (navigator.sendBeacon) navigator.sendBeacon('/api/t', new Blob([body], { type: 'application/json' }));
      else fetch('/api/t', { method: 'POST', body: body, headers: { 'Content-Type': 'application/json' }, keepalive: true });
    } catch (err) { /* never block the click */ }
  }, true);

  /* ---------- header Brands menu: opens on hover (computers); a tap goes to the brands page ---------- */
  var bm = $('#brandMenu');
  if (bm && finePointer) {
    var bl = bm.querySelector('.hbtn'), bt = null;
    var show = function (on) { clearTimeout(bt); bm.classList.toggle('open', on); bl.setAttribute('aria-expanded', String(on)); };
    bm.addEventListener('mouseenter', function () { show(true); });
    bm.addEventListener('mouseleave', function () { bt = setTimeout(function () { show(false); }, 150); });
    bm.addEventListener('focusin', function () { show(true); });
    bm.addEventListener('focusout', function (e) { if (!bm.contains(e.relatedTarget)) show(false); });
    bm.addEventListener('keydown', function (e) { if (e.key === 'Escape') { show(false); bl.focus(); } });
  }

  if (finePointer) $$('form[data-ymm] select[data-y], form[data-ymm] select[data-m], form[data-ymm] select[data-d]').forEach(groupedMenu);

  /* ---------- search suggestions ---------- */
  var q = $('#q'), ac = $('#ac'), box = $('#searchBox');
  if (q && ac) {
    var ctrl = null, timer = null, lastQ = '';
    var vehicle = document.body.getAttribute('data-vehicle') || '';
    var close = function () { ac.classList.remove('open'); };
    var hl = function (text, term) {
      text = String(text || ''); var t = term.trim().toLowerCase(); var i = t ? text.toLowerCase().indexOf(t) : -1;
      return i < 0 ? esc(text) : esc(text.slice(0, i)) + '<mark>' + esc(text.slice(i, i + t.length)) + '</mark>' + esc(text.slice(i + t.length));
    };
    var row = function (href, ic, label, small) { return '<a href="' + href + '" role="option">' + SVG(IC[ic]) + '<span>' + label + '</span>' + (small ? '<small>' + small + '</small>' : '') + '</a>'; };
    var draw = function (res, term) {
      var h = '';
      if (res.vehicle) h += '<h4>Vehicle</h4>' + row('/search?q=' + encodeURIComponent(term), 'car', 'Parts for <b>' + esc(res.vehicle.label) + '</b>', '');
      if (res.part_numbers.length) h += '<h4>Part numbers</h4>' + res.part_numbers.map(function (p) { return row('/p/' + encodeURIComponent(p.slug), 'hash', hl(p.part_number, term) + ' <span style="color:var(--muted)">· ' + esc(p.name) + '</span>', esc(p.brand)); }).join('');
      if (res.categories.length) h += '<h4>Categories</h4>' + res.categories.map(function (c) { return row('/c/' + encodeURIComponent(c.id), 'filter', hl(c.name, term), esc(c.parent || 'Category')); }).join('');
      if (res.brands.length) h += '<h4>Brands</h4>' + res.brands.map(function (b) { return row('/brand/' + encodeURIComponent(b.id), 'shield', hl(b.name, term), 'Brand'); }).join('');
      if (res.products.length) h += '<h4>Parts' + (vehicle ? ' · fits first for your vehicle' : '') + '</h4>' + res.products.map(function (p) {
        return row('/p/' + encodeURIComponent(p.slug), p.fit === 'fits' ? 'checkc' : 'search', esc(p.short_name || p.title) + ' <span style="color:var(--muted)">· ' + esc(p.brand_name) + '</span>', money(p.price));
      }).join('');
      h += '<h4>All results</h4>' + row('/search?q=' + encodeURIComponent(term), 'search', 'See all results for “' + esc(term) + '”', '');
      ac.innerHTML = h; ac.classList.add('open');
    };
    var suggest = function () {
      var term = q.value.trim();
      if (term.length < 2) { close(); return; }
      if (term === lastQ && ac.innerHTML) { ac.classList.add('open'); return; }
      lastQ = term;
      if (ctrl) ctrl.abort();
      ctrl = window.AbortController ? new AbortController() : null;
      getJSON('/api/suggest?q=' + encodeURIComponent(term) + (vehicle ? '&vehicle=' + vehicle : ''), ctrl && ctrl.signal)
        .then(function (res) { if (q.value.trim() === term) draw(res, term); })
        .catch(function () { /* aborted or offline: keep the plain search box working */ });
    };
    q.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(suggest, 140); });
    q.addEventListener('focus', function () { if (q.value.trim().length >= 2) suggest(); });
    q.addEventListener('keydown', function (e) {
      var items = $$('a', ac); if (!items.length || !ac.classList.contains('open')) return;
      var i = items.findIndex(function (a) { return a.classList.contains('hi'); });
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); items.forEach(function (a) { a.classList.remove('hi'); });
        var n = e.key === 'ArrowDown' ? (i + 1) % items.length : (i <= 0 ? items.length - 1 : i - 1);
        items[n].classList.add('hi'); items[n].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter' && i >= 0) { e.preventDefault(); location.href = items[i].getAttribute('href'); }
      else if (e.key === 'Escape') close();
    });
    document.addEventListener('click', function (e) { if (!box.contains(e.target)) close(); });
  }

  /* ---------- add to cart without leaving the page ---------- */
  $$('form[data-cart]').forEach(function (f) {
    f.addEventListener('submit', function (e) {
      if (!window.fetch || !window.FormData) return;
      e.preventDefault();
      var btn = $('button[type=submit]', f); if (btn) btn.disabled = true;
      fetch(f.action, { method: 'POST', body: new FormData(f), headers: { Accept: 'application/json' }, credentials: 'same-origin' })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (!res.ok) { toast(esc(res.j.error || 'Could not add that part.')); return; }
          var c = $('#cartCount'); if (c) { c.hidden = !res.j.count; c.textContent = res.j.count; }
          var s = $('#cartSmall'); if (s) s.textContent = res.j.subtotal_text;
          toast(SVG(IC.checkc, 18) + ' Added to cart <a href="/cart">View cart</a>');
        })
        .catch(function () { f.submit(); })
        .then(function () { if (btn) btn.disabled = false; });
    });
  });

  /* quantity buttons on the product page */
  $$('[data-qty]').forEach(function (b) {
    b.addEventListener('click', function () {
      var input = $('input[name=qty]', b.closest('form'));
      var n = (parseInt(input.value, 10) || 1) + Number(b.getAttribute('data-qty'));
      input.value = Math.max(1, Math.min(99, n));
    });
  });

  /* ---------- results page filters apply on click ---------- */
  var ff = $('form[data-autosubmit]');
  if (ff) {
    var submit = function () {
      var params = new URLSearchParams(new FormData(ff));
      var clean = new URLSearchParams();
      params.forEach(function (v, k) { if (v !== '' && !(k === 'sort' && v === 'relevance')) clean.append(k, v); });
      var all = clean.getAll('all'); clean.delete('all'); if (all.length && all[all.length - 1] === '1') clean.set('all', '1');
      var s = clean.toString();
      location.href = location.pathname + (s ? '?' + s : '');
    };
    ff.addEventListener('change', submit);
    var so = $('#sort'); if (so) so.addEventListener('change', submit);
    ff.addEventListener('submit', function (e) { e.preventDefault(); submit(); });
    var tog = $('#ftog');
    if (tog) tog.addEventListener('click', function () { var open = ff.classList.toggle('open'); tog.setAttribute('aria-expanded', open ? 'true' : 'false'); });
  }

  /* ---------- product page: photos and tabs ---------- */
  var main = $('#mainImg img');
  $$('[data-img]').forEach(function (b) {
    b.addEventListener('click', function () {
      main.src = b.getAttribute('data-img');
      $$('[data-img]').forEach(function (x) { x.classList.toggle('on', x === b); });
    });
  });
  var tabs = $$('.tabs [role=tab]');
  if (tabs.length) {
    var show = function (tab) {
      tabs.forEach(function (t) {
        var on = t === tab; t.classList.toggle('on', on); t.setAttribute('aria-selected', on ? 'true' : 'false');
        var panel = document.getElementById(t.getAttribute('aria-controls')); if (panel) panel.hidden = !on;
      });
    };
    tabs.forEach(function (t) { t.addEventListener('click', function () { show(t); }); });
    show(tabs[0]);
  }

  /* ---------- home: full-screen video with the header see-through over it ---------- */
  var hero = $('.vhero'), hdr = $('header.top');
  if (hero && hdr) {
    var setH = function () { document.documentElement.style.setProperty('--hdrH', hdr.offsetHeight + 'px'); };
    var solid = function () {
      var searching = document.activeElement && document.activeElement.id === 'q';
      hdr.classList.toggle('solid', searching || window.scrollY > Math.min(120, hero.offsetHeight * 0.25));
    };
    setH(); solid();
    window.addEventListener('resize', setH);
    window.addEventListener('scroll', solid, { passive: true });
    if (window.ResizeObserver) new ResizeObserver(setH).observe(hdr);
    /* a search box with suggestions open is easier to read on the solid header */
    hdr.addEventListener('focusin', solid);
    hdr.addEventListener('focusout', function () { setTimeout(solid, 150); });

    var vid = $('#heroVideo'), hp = $('#heroPause'), vPaused = reduceMotion;
    if (vid) {
      vid.muted = true;
      var small = window.matchMedia('(max-width: 900px)').matches || (navigator.connection && navigator.connection.saveData);
      vid.src = small ? vid.getAttribute('data-src-small') : vid.getAttribute('data-src');
      var showBtn = function () {
        if (!hp) return;
        hp.innerHTML = hp.getAttribute(vPaused ? 'data-play' : 'data-pause');
        hp.setAttribute('aria-label', vPaused ? 'Play background video' : 'Pause background video');
      };
      var tryPlay = function () { if (vPaused) return; var p = vid.play(); if (p && p.catch) p.catch(function () { vPaused = true; showBtn(); }); };
      if (!vPaused) { vid.preload = 'auto'; tryPlay(); }
      showBtn();
      if (hp) hp.addEventListener('click', function () { vPaused = !vPaused; if (vPaused) vid.pause(); else tryPlay(); showBtn(); });
      /* stop decoding while the video is scrolled out of view */
      if (window.IntersectionObserver) new IntersectionObserver(function (es) {
        es.forEach(function (e) { if (e.isIntersecting) tryPlay(); else vid.pause(); });
      }, { threshold: 0.05 }).observe(hero);
    }
  }

  /* ---------- home: promo slideshow ---------- */
  var view = $('#pView');
  if (view) {
    var slides = $$('.slide', view), dots = $$('#pDots button'), i = 0, paused = reduceMotion, pTimer = null;
    var pb = $('#pPause');
    var showSlide = function (n) {
      i = (n + slides.length) % slides.length;
      slides.forEach(function (s, k) { var on = k === i; s.classList.toggle('on', on); s.setAttribute('aria-hidden', on ? 'false' : 'true'); s.tabIndex = on ? 0 : -1; });
      dots.forEach(function (d, k) { d.classList.toggle('on', k === i); });
    };
    var start = function () { clearInterval(pTimer); if (!paused && slides.length > 1) pTimer = setInterval(function () { if (!document.hidden) showSlide(i + 1); }, 6500); };
    var setPause = function (v) {
      paused = v;
      if (pb) { pb.innerHTML = pb.getAttribute(v ? 'data-play' : 'data-pause'); pb.setAttribute('aria-label', v ? 'Play slideshow' : 'Pause slideshow'); }
      start();
    };
    if ($('#pNext')) $('#pNext').addEventListener('click', function () { showSlide(i + 1); start(); });
    if ($('#pPrev')) $('#pPrev').addEventListener('click', function () { showSlide(i - 1); start(); });
    dots.forEach(function (d, k) { d.addEventListener('click', function () { showSlide(k); start(); }); });
    if (pb) pb.addEventListener('click', function () { setPause(!paused); });
    view.addEventListener('mouseenter', function () { clearInterval(pTimer); });
    view.addEventListener('mouseleave', start);
    setPause(paused);
  }

  /* ---------- home: brand carousel (only when there are more brands than fit) ---------- */
  var track = $('#bTrack');
  if (track && !track.classList.contains('static')) {
    var bView = $('#bView'), busy = false, bTimer = null;
    var step = function () { return track.children[0].getBoundingClientRect().width + 10; };
    var next = function () {
      if (busy) return; busy = true;
      track.style.transition = reduceMotion ? 'none' : 'transform .45s ease'; track.style.transform = 'translateX(' + (-step()) + 'px)';
      var done = function () { track.style.transition = 'none'; track.appendChild(track.children[0]); track.style.transform = 'translateX(0)'; busy = false; };
      reduceMotion ? done() : setTimeout(done, 460);
    };
    var prev = function () {
      if (busy) return; busy = true;
      track.style.transition = 'none'; track.insertBefore(track.lastElementChild, track.children[0]); track.style.transform = 'translateX(' + (-step()) + 'px)';
      void track.offsetWidth;
      track.style.transition = reduceMotion ? 'none' : 'transform .45s ease'; track.style.transform = 'translateX(0)';
      setTimeout(function () { busy = false; }, reduceMotion ? 0 : 460);
    };
    var restart = function () { clearInterval(bTimer); if (!reduceMotion) bTimer = setInterval(function () { if (!document.hidden) next(); }, 3000); };
    $('#bNext').addEventListener('click', function () { next(); restart(); });
    $('#bPrev').addEventListener('click', function () { prev(); restart(); });
    bView.addEventListener('mouseenter', function () { clearInterval(bTimer); });
    bView.addEventListener('mouseleave', restart);
    bView.addEventListener('focusin', function () { clearInterval(bTimer); });
    bView.addEventListener('focusout', restart);
    /* phones: swipe left or right (there are no arrows on small screens) */
    var tx = null, ty = null;
    bView.addEventListener('touchstart', function (e) { tx = e.touches[0].clientX; ty = e.touches[0].clientY; clearInterval(bTimer); }, { passive: true });
    bView.addEventListener('touchend', function (e) {
      if (tx === null) return;
      var dx = e.changedTouches[0].clientX - tx, dy = e.changedTouches[0].clientY - ty; tx = null;
      if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) { if (dx < 0) next(); else prev(); }
      restart();
    });
    restart();
  }
})();
