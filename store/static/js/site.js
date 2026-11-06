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
    restart();
  }
})();
