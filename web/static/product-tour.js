/**
 * product-tour.js -- the product tour (tip dots + walkthrough), as a drop-in for any web app.
 *
 * Ported from the Facilities Ticket System (src/Tips.html). Two modes, because
 * "leave it on as long as you like" and "dim everything and spotlight one thing"
 * are incompatible if you only build one:
 *
 *   markers -- small numbered dots beside each key control. Non-blocking, so the
 *              app stays fully usable with tips left on indefinitely. Hovering
 *              either a dot or the control itself shows the explanation; clicking
 *              pins it open, which is also the path on a touch screen.
 *   tour    -- dim the screen, ring one element at a time, Next/Back. Entered on
 *              request from the tips bar ("Walk me through").
 *
 * The people this is for do not use software all day, and a one-time tour is no
 * help three weeks later when they have forgotten where a button lives. So this is
 * a switch they own, not an onboarding flow: it stays on until they turn it off,
 * and the state is remembered per browser.
 *
 *   ProductTour.init({ storageKey: 'myapp-tips-on', tips: [ ... ] });
 *   ProductTour.refresh('screen-key');   // at the end of every screen render
 *
 * No dependencies. Styles are injected on init and pick up the house design tokens
 * (--accent, --surface, --ink, ...) when the page defines them. See SKILL.md.
 */
(function (global) {
  'use strict';

  var opts = {
    tips: [],                    // [{ token, text, screen?, roles? }]
    storageKey: 'product-tour-on',
    attr: 'data-tip',            // the attribute that marks a control
    role: null,                  // function () -> current role; omit to ignore roles
    defaultOn: false,
    onChange: null               // function (on) -- e.g. show a toast
  };

  // ------------------------------------------------------------------ styles

  var UI_VARS =
    '--pt-accent:var(--accent,#0e5f66);' +
    '--pt-accent-ink:var(--accent-ink,var(--accent,#0e5f66));' +
    '--pt-on-accent:var(--on-accent,#fff);' +
    '--pt-surface:var(--surface,#fcfcfb);' +
    '--pt-surface-3:var(--surface-3,#efede6);' +
    '--pt-ink:var(--ink,#161511);' +
    '--pt-ink-2:var(--ink-2,#52514e);' +
    '--pt-muted:var(--muted,#8a8880);' +
    '--pt-hair:var(--hair,#e3e1d8);' +
    '--pt-hair-strong:var(--hair-strong,#cdcbc0);' +
    '--pt-warn:var(--warn,#b5890a);' +
    '--pt-radius:var(--radius,10px);' +
    '--pt-radius-sm:var(--radius-sm,7px);' +
    '--pt-shadow:var(--shadow-lg,0 2px 4px rgba(22,21,17,.06),0 18px 44px rgba(22,21,17,.13));' +
    '--pt-sans:var(--sans,"Public Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif);' +
    '--pt-mono:var(--mono,"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace);' +
    '--pt-ease:var(--ease,cubic-bezier(.22,.61,.36,1));' +
    '--pt-scrim:rgba(16,15,14,.62);';

  // Dark fallbacks only matter for a page with none of the house tokens; a page that
  // has them already swaps them for dark mode itself.
  var UI_VARS_DARK =
    '--pt-accent:var(--accent,#4fc4c9);' +
    '--pt-accent-ink:var(--accent-ink,var(--accent,#4fc4c9));' +
    '--pt-on-accent:var(--on-accent,#0b1220);' +
    '--pt-surface:var(--surface,#1a1a18);' +
    '--pt-surface-3:var(--surface-3,#2a2825);' +
    '--pt-ink:var(--ink,#f4f3ee);' +
    '--pt-ink-2:var(--ink-2,#c0beb5);' +
    '--pt-muted:var(--muted,#8d8b82);' +
    '--pt-hair:var(--hair,#302e2b);' +
    '--pt-hair-strong:var(--hair-strong,#42403b);' +
    '--pt-warn:var(--warn,#d9a521);' +
    '--pt-shadow:var(--shadow-lg,0 2px 4px rgba(0,0,0,.5),0 18px 44px rgba(0,0,0,.5));' +
    '--pt-scrim:rgba(0,0,0,.72);';

  var CSS = [
    '.pt-ui,.pt-switch{' + UI_VARS + '}',
    '@media (prefers-color-scheme: dark){:root:not([data-theme="light"]) .pt-ui,' +
      ':root:not([data-theme="light"]) .pt-switch{' + UI_VARS_DARK + '}}',
    ':root[data-theme="dark"] .pt-ui,:root[data-theme="dark"] .pt-switch{' + UI_VARS_DARK + '}',

    '.pt-layer{position:fixed;inset:0;z-index:9000;pointer-events:none}',

    /* ---- markers ---- */
    /* padding:0 matters: the UA stylesheet gives buttons horizontal padding, which
       squeezes the digit off-centre inside a circle this small. */
    '.pt-dot{position:fixed;pointer-events:auto;cursor:pointer;width:22px;height:22px;' +
      'border-radius:50%;background:var(--pt-accent);color:var(--pt-on-accent);' +
      'border:2px solid var(--pt-surface);padding:0;margin:0;text-align:center;text-indent:0;' +
      'display:flex;align-items:center;justify-content:center;' +
      'font:700 11px/1 var(--pt-sans);font-variant-numeric:tabular-nums;letter-spacing:0;' +
      'box-shadow:0 2px 7px color-mix(in srgb,var(--pt-accent) 42%,transparent);' +
      'transition:transform .15s var(--pt-ease),box-shadow .15s var(--pt-ease);' +
      'animation:pt-pop .3s var(--pt-ease) both}',
    '.pt-dot:hover,.pt-dot:focus-visible{transform:scale(1.18);' +
      'box-shadow:0 3px 12px color-mix(in srgb,var(--pt-accent) 60%,transparent)}',
    '.pt-dot.is-open{transform:scale(1.18)}',
    '@keyframes pt-pop{from{opacity:0;transform:scale(.4)}}',

    /* A single ring drawn around whatever a marker belongs to, on hover. */
    '.pt-outline{position:fixed;pointer-events:none;border-radius:var(--pt-radius-sm);' +
      'border:1.5px dashed var(--pt-accent);opacity:0;transition:opacity .15s var(--pt-ease)}',
    '.pt-outline.is-on{opacity:.85}',

    /* ---- callout ---- */
    '.pt-callout{position:fixed;pointer-events:auto;z-index:9002;max-width:290px;' +
      'padding:12px 14px;background:var(--pt-surface);color:var(--pt-ink);' +
      'border:1px solid var(--pt-hair);border-radius:var(--pt-radius);box-shadow:var(--pt-shadow);' +
      'font:400 13.5px/1.55 var(--pt-sans);text-align:left;animation:pt-fade .2s var(--pt-ease) both}',
    '@keyframes pt-fade{from{opacity:0;transform:translateY(-4px)}}',
    '.pt-callout .pt-label{font:600 10px/1.3 var(--pt-mono);letter-spacing:.13em;' +
      'text-transform:uppercase;color:var(--pt-accent-ink);margin-bottom:4px}',
    '.pt-callout .pt-close{position:absolute;top:6px;right:6px;background:none;border:0;' +
      'color:var(--pt-muted);cursor:pointer;padding:2px;line-height:0}',
    '.pt-callout .pt-nav{display:flex;align-items:center;gap:7px;margin-top:11px;' +
      'padding-top:10px;border-top:1px solid var(--pt-hair)}',
    '.pt-callout .pt-step{font-size:11.5px;color:var(--pt-muted);' +
      'font-variant-numeric:tabular-nums;margin-right:auto}',

    /* ---- tour spotlight ---- */
    /* One element carries both the light border and the dim, via an enormous spread
       shadow. Cheaper and crisper than four positioned scrims, and it animates. */
    '.pt-ring{position:fixed;z-index:9001;pointer-events:none;border-radius:var(--pt-radius-sm);' +
      'border:2px solid var(--pt-accent);' +
      'box-shadow:0 0 0 4px color-mix(in srgb,var(--pt-accent) 18%,transparent),' +
        '0 6px 22px color-mix(in srgb,var(--pt-accent) 30%,transparent),' +
        '0 0 0 9999px var(--pt-scrim);' +
      'transition:top .32s var(--pt-ease),left .32s var(--pt-ease),' +
        'width .32s var(--pt-ease),height .32s var(--pt-ease);' +
      'animation:pt-fade .22s var(--pt-ease) both}',

    /* ---- the persistent bar ---- */
    '.pt-bar{position:fixed;z-index:9003;left:50%;bottom:18px;transform:translateX(-50%);' +
      'display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:9px 12px 9px 15px;' +
      'background:var(--pt-surface);color:var(--pt-ink);border:1px solid var(--pt-hair);' +
      'border-radius:999px;box-shadow:var(--pt-shadow);font:400 13px/1.4 var(--pt-sans);' +
      'pointer-events:auto;max-width:calc(100vw - 28px);animation:pt-rise .26s var(--pt-ease) both}',
    '@keyframes pt-rise{from{opacity:0;transform:translate(-50%,10px)}}',
    '.pt-bar .pt-count{display:inline-flex;align-items:center;gap:6px;color:var(--pt-ink-2)}',
    '.pt-bar .pt-pip{width:8px;height:8px;border-radius:50%;background:var(--pt-accent)}',
    '.pt-bar .pt-stale{display:inline-flex;align-items:center;gap:4px;color:var(--pt-warn);' +
      'font-size:12px;font-weight:600}',

    /* Rebuilt after a resize: skip the entrance animation. */
    '.pt-dot.pt-still,.pt-bar.pt-still{animation:none}',

    /* ---- buttons ---- */
    '.pt-btn{display:inline-flex;align-items:center;gap:5px;padding:5px 11px;margin:0;' +
      'border:1px solid var(--pt-hair-strong);border-radius:999px;background:var(--pt-surface);' +
      'color:var(--pt-ink);font:600 12.5px/1.2 var(--pt-sans);cursor:pointer;' +
      'transition:background .13s var(--pt-ease),filter .13s var(--pt-ease)}',
    '.pt-btn:hover{background:var(--pt-surface-3)}',
    '.pt-btn:focus-visible{outline:2px solid var(--pt-accent);outline-offset:2px}',
    '.pt-btn-primary{background:var(--pt-accent);border-color:var(--pt-accent);color:var(--pt-on-accent)}',
    '.pt-btn-primary:hover{background:var(--pt-accent);filter:brightness(1.08)}',
    '.pt-btn-ghost{background:none;border-color:transparent;color:var(--pt-ink-2)}',
    '.pt-btn svg,.pt-stale svg,.pt-close svg{flex:0 0 auto}',

    /* ---- the optional toggle switch (markup in SKILL.md) ---- */
    '.pt-switch{position:relative;display:flex;align-items:center;gap:9px;padding:7px 6px;' +
      'border-radius:var(--pt-radius-sm);cursor:pointer;user-select:none;' +
      'font:600 13px/1.3 var(--pt-sans);color:var(--pt-ink-2);' +
      'transition:background .13s var(--pt-ease),color .13s var(--pt-ease)}',
    '.pt-switch:hover{background:var(--pt-surface-3);color:var(--pt-ink)}',
    '.pt-switch input{position:absolute;opacity:0;width:0;height:0}',
    '.pt-track{position:relative;flex:0 0 auto;width:34px;height:19px;' +
      'background:var(--pt-hair-strong);border-radius:999px;transition:background .18s var(--pt-ease)}',
    '.pt-knob{position:absolute;top:2px;left:2px;width:15px;height:15px;' +
      'background:var(--pt-surface);border-radius:50%;box-shadow:0 1px 3px rgba(0,0,0,.3);' +
      'transition:transform .18s var(--pt-ease)}',
    '.pt-switch input:checked+.pt-track{background:var(--pt-accent)}',
    '.pt-switch input:checked+.pt-track .pt-knob{transform:translateX(15px)}',
    '.pt-switch input:focus-visible+.pt-track{outline:2px solid var(--pt-accent);outline-offset:2px}',
    '.pt-switch.is-on{color:var(--pt-accent-ink)}',

    '@media (prefers-reduced-motion: reduce){.pt-dot,.pt-callout,.pt-ring,.pt-bar{animation:none}' +
      '.pt-ring{transition:none}}',
    /* ---- journey: a scripted tour across screens ---- */
    '.pt-scrim{position:fixed;inset:0;z-index:9001;background:var(--pt-scrim);animation:pt-fade .2s var(--pt-ease) both}',
    '.pt-callout.pt-journey{max-width:360px;font-size:14.5px}',
    '.pt-callout .pt-title{margin:0 0 5px;font-size:16px;font-weight:700;line-height:1.3;padding-right:22px}',
    '.pt-callout.pt-card{top:50%;left:50%;transform:translate(-50%,-50%);max-width:min(480px,calc(100vw - 28px));' +
      'padding:20px 22px 16px;font-size:15.5px;animation:pt-pop .24s var(--pt-ease) both}',
    '.pt-callout.pt-card .pt-title{font-size:21px;margin-bottom:8px}',
    '@keyframes pt-pop{from{opacity:0;transform:translate(-50%,-50%) scale(.96)}}',
    '@media (prefers-reduced-motion: reduce){.pt-scrim,.pt-card{animation:none}}',
    '@media print{.pt-layer,.pt-bar,.pt-callout,.pt-ring,.pt-scrim{display:none!important}}'
  ].join('\n');

  function injectStyles() {
    if (document.getElementById('pt-styles')) return;
    var s = document.createElement('style');
    s.id = 'pt-styles';
    s.textContent = CSS;
    (document.head || document.documentElement).appendChild(s);
  }

  // ------------------------------------------------------------------- icons

  var ICONS = {
    close: '<path d="M18 6 6 18M6 6l12 12"/>',
    play: '<path d="M7 4.5v15l12-7.5z"/>',
    alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>' +
           '<path d="M12 9v4M12 17h.01"/>',
    left: '<path d="m15 18-6-6 6-6"/>',
    right: '<path d="m9 18 6-6-6-6"/>',
    check: '<path d="M20 6 9 17l-5-5"/>'
  };

  function icon(name, size) {
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" ' +
           'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
           'stroke-linejoin="round" aria-hidden="true">' + ICONS[name] + '</svg>';
  }

  // ----------------------------------------------------------------- helpers

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }

  function reducedMotion() {
    try { return global.matchMedia('(prefers-reduced-motion: reduce)').matches; }
    catch (e) { return false; }
  }

  // ------------------------------------------------------------------- state

  var screen = null;
  var memOn = false;
  var layer = null;
  var bar = null;
  var callout = null;
  var outline = null;
  var outlineEl = null;        // the control the hover outline is drawn around
  var ring = null;
  var tourIndex = -1;
  var tourItems = [];
  var openToken = null;
  var openItem = null;
  var pinned = false;          // a clicked tip stays open; a hovered one does not
  var closeTimer = null;
  var currentItems = [];       // last resolved set, so hover does not re-query
  var staleTokens = [];
  var ticking = false;
  var needRebuild = false;
  var bound = false;

  function enabled() {
    try {
      var v = localStorage.getItem(opts.storageKey);
      if (v !== null) memOn = v === '1';
      return memOn;
    } catch (e) { return memOn; }
  }

  function setEnabled(v) {
    memOn = !!v;
    try { localStorage.setItem(opts.storageKey, memOn ? '1' : '0'); } catch (e) { /* memory only */ }
    syncSwitches();
    refresh(screen);
    if (typeof opts.onChange === 'function') opts.onChange(memOn);
    return memOn;
  }

  function toggle() { return setEnabled(!enabled()); }

  /** Keep every entry point (a switch, a header button) showing the same state. */
  function syncSwitches() {
    var on = enabled();
    var nodes = document.querySelectorAll('[data-tips-toggle]');
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.tagName === 'INPUT') {
        n.checked = on;
        var wrap = n.closest('.pt-switch');
        if (wrap) wrap.classList.toggle('is-on', on);
      } else {
        n.classList.toggle('is-on', on);
        n.setAttribute('aria-pressed', on ? 'true' : 'false');
        n.title = on ? 'Hide tips' : 'Show tips';
      }
    }
  }

  // --------------------------------------------------------------- selection

  function applicable() {
    var r = typeof opts.role === 'function' ? opts.role() : null;
    return opts.tips.filter(function (t) {
      if (t.screen != null && t.screen !== screen) return false;
      if (!t.roles || t.roles === '*' || typeof opts.role !== 'function') return true;
      return t.roles.indexOf(r) > -1;
    });
  }

  function visible(el) { return el.getClientRects().length > 0; }

  /** Resolve each tip to a live element, recording the ones that resolve to nothing. */
  function resolved() {
    staleTokens = [];
    var out = [];
    applicable().forEach(function (t) {
      var all = document.querySelectorAll('[' + opts.attr + '="' + String(t.token).replace(/"/g, '\\"') + '"]');
      var el = null;
      for (var i = 0; i < all.length; i++) { if (visible(all[i])) { el = all[i]; break; } }
      if (el) out.push({ tip: t, el: el });
      else staleTokens.push(t.token);
    });
    if (staleTokens.length) {
      console.warn('[product-tour] no visible target on "' + screen + '" for: ' +
                   staleTokens.join(', ') + ' -- update the tips registry');
    }
    return out;
  }

  // ----------------------------------------------------------------- drawing

  function ensureLayer() {
    if (layer && layer.parentNode) return layer;
    layer = document.createElement('div');
    layer.className = 'pt-ui pt-layer';
    document.body.appendChild(layer);
    return layer;
  }

  function teardown() {
    closeCallout();
    endTour();
    if (layer && layer.parentNode) layer.remove();
    layer = null;
    outline = null;
    outlineEl = null;
    if (bar && bar.parentNode) bar.remove();
    bar = null;
    currentItems = [];
  }

  function box(el) { return el.getBoundingClientRect(); }

  /** Build the markers and bar for the current screen. */
  function build(animate) {
    var keep = pinned ? openToken : null;
    var items = resolved();
    ensureLayer();
    layer.innerHTML = '';
    outline = null;
    outlineEl = null;
    currentItems = items;

    if (items.length) {
      outline = document.createElement('div');
      outline.className = 'pt-outline';
      layer.appendChild(outline);
    }

    items.forEach(function (item, i) {
      var dot = document.createElement('button');
      dot.className = 'pt-dot' + (animate ? '' : ' pt-still');
      dot.type = 'button';
      dot.textContent = String(i + 1);
      dot.setAttribute('aria-label', 'Tip ' + (i + 1) + ': ' + item.tip.text);
      dot.dataset.token = item.tip.token;
      if (animate) dot.style.animationDelay = Math.min(i * 35, 300) + 'ms';

      dot.addEventListener('mouseenter', function () { hoverIn(item, i); });
      dot.addEventListener('mouseleave', function () { hoverOut(); });
      dot.addEventListener('focus', function () { hoverIn(item, i); });
      dot.addEventListener('blur', function () { hoverOut(); });
      dot.addEventListener('click', function (e) {
        e.stopPropagation();
        togglePin(item, i);
      });
      item.dot = dot;
      layer.appendChild(dot);
    });

    renderBar(items.length, animate);
    place();

    // A pinned callout survives a rebuild if its control is still on screen.
    var still = keep ? items.filter(function (x) { return x.tip.token === keep; })[0] : null;
    if (still && callout) {
      openItem = still;
      still.dot.classList.add('is-open');
      position(callout, still.el);
    } else if (tourIndex < 0) {
      closeCallout();
    }
  }

  /**
   * Move the markers to follow their controls. Runs on every scroll frame, so it
   * repositions what is there rather than rebuilding it -- a rebuild would replay
   * the entrance animation each frame.
   */
  function place() {
    var vh = global.innerHeight, vw = global.innerWidth;
    for (var i = 0; i < currentItems.length; i++) {
      var item = currentItems[i];
      if (!item.el.isConnected) { scheduleReflow(true); return; }
      var b = box(item.el);
      // Controls scrolled out of view lose their dot, or the dots pile up at the edge.
      // visibility rather than display: re-showing a display:none dot replays its
      // entrance animation.
      var off = b.bottom < 0 || b.top > vh || b.right < 0 || b.left > vw;
      item.dot.style.visibility = off ? 'hidden' : '';
      if (off) continue;

      /**
       * Small controls get the dot on their top-right corner. Wide blocks -- a card
       * header, a table head -- get it on the top-LEFT instead, next to where their
       * text begins; on a full-width card a dot floating at the far right edge reads
       * as belonging to nothing.
       */
      var cx = b.width > 320 ? b.left - 9 : b.right - 11;
      item.dot.style.top = Math.max(4, Math.round(b.top - 9)) + 'px';
      item.dot.style.left = Math.max(4, Math.min(vw - 26, Math.round(cx))) + 'px';
    }
    if (outlineEl) drawOutline(outlineEl);
    if (callout && openItem) position(callout, openItem.el);
  }

  /* ---------------------------------------------------------------- hovering */

  function cancelClose() {
    if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
  }

  /** Delay the close so the pointer can travel from the marker to the callout. */
  function scheduleClose() {
    if (pinned || tourIndex >= 0) return;
    cancelClose();
    closeTimer = setTimeout(function () {
      closeTimer = null;
      closeCallout();
    }, 240);
  }

  function hoverIn(item, index) {
    if (tourIndex >= 0) return;
    cancelClose();
    showOutline(item.el);
    if (openToken === item.tip.token) return;
    pinned = false;
    openCallout(item, index, currentItems.length, false);
  }

  function hoverOut() {
    if (tourIndex >= 0) return;
    hideOutline();
    scheduleClose();
  }

  /** Click pins a tip open, so it survives the pointer leaving. Click again closes. */
  function togglePin(item, index) {
    if (tourIndex >= 0) return;
    if (openToken === item.tip.token && pinned) {
      closeCallout();
      return;
    }
    cancelClose();
    if (openToken !== item.tip.token) openCallout(item, index, currentItems.length, false);
    pinned = true;
  }

  /** Index of a tip by its target element, or -1. */
  function indexOfElement(el) {
    for (var i = 0; i < currentItems.length; i++) {
      if (currentItems[i].el === el) return i;
    }
    return -1;
  }

  function drawOutline(el) {
    if (!outline) return;
    var b = box(el);
    outline.style.top = (b.top - 4) + 'px';
    outline.style.left = (b.left - 4) + 'px';
    outline.style.width = (b.width + 8) + 'px';
    outline.style.height = (b.height + 8) + 'px';
  }

  function showOutline(el) {
    if (!outline) return;
    outlineEl = el;
    drawOutline(el);
    outline.classList.add('is-on');
  }

  function hideOutline() {
    outlineEl = null;
    if (outline) outline.classList.remove('is-on');
  }

  /** The persistent bar: state, the walkthrough entry point, and a drift warning. */
  function renderBar(count, animate) {
    if (bar && bar.parentNode) bar.remove();
    if (!enabled()) { bar = null; return; }

    bar = document.createElement('div');
    bar.className = 'pt-ui pt-bar' + (animate ? '' : ' pt-still');
    bar.setAttribute('role', 'region');
    bar.setAttribute('aria-label', 'Tips');
    bar.innerHTML =
      '<span class="pt-count"><span class="pt-pip"></span>' +
        (count
          ? plural(count, 'tip') + ' on this screen — tap or hover a number'
          : 'No tips for this screen yet') +
      '</span>' +
      (count
        ? '<button type="button" class="pt-btn pt-btn-primary" data-pt-tour>' +
          icon('play', 13) + ' Walk me through</button>'
        : '') +
      (staleTokens.length
        ? '<span class="pt-stale" title="' + esc(staleTokens.join(', ')) + '">' +
          icon('alert', 13) + ' ' + plural(staleTokens.length, 'tip') +
          (staleTokens.length === 1 ? ' needs' : ' need') + ' updating</span>'
        : '') +
      '<button type="button" class="pt-btn pt-btn-ghost" data-pt-off>' +
        icon('close', 13) + ' Turn off</button>';

    document.body.appendChild(bar);
    bar.querySelector('[data-pt-off]').addEventListener('click', function () {
      setEnabled(false);
    });
    var tourBtn = bar.querySelector('[data-pt-tour]');
    if (tourBtn) tourBtn.addEventListener('click', function () { startTour(); });
  }

  // ----------------------------------------------------------------- callout

  function closeCallout() {
    cancelClose();
    pinned = false;
    if (callout && callout.parentNode) callout.remove();
    callout = null;
    openToken = null;
    openItem = null;
    if (layer) {
      layer.querySelectorAll('.pt-dot.is-open').forEach(function (d) {
        d.classList.remove('is-open');
      });
    }
  }

  function openCallout(item, index, total, isTour) {
    closeCallout();
    openToken = item.tip.token;
    openItem = item;
    if (item.dot) item.dot.classList.add('is-open');

    callout = document.createElement('div');
    callout.className = 'pt-ui pt-callout';
    callout.setAttribute('role', 'dialog');
    callout.setAttribute('aria-label', 'Tip ' + (index + 1) + ' of ' + total);
    callout.innerHTML =
      '<div class="pt-label">Tip ' + (index + 1) + ' of ' + total + '</div>' +
      '<button type="button" class="pt-close" aria-label="Close">' + icon('close', 14) + '</button>' +
      '<div>' + esc(item.tip.text) + '</div>' +
      (isTour
        ? '<div class="pt-nav">' +
            '<span class="pt-step">' + (index + 1) + ' / ' + total + '</span>' +
            (index > 0
              ? '<button type="button" class="pt-btn" data-pt-prev>' + icon('left', 13) + ' Back</button>'
              : '') +
            (index < total - 1
              ? '<button type="button" class="pt-btn pt-btn-primary" data-pt-next>Next ' +
                icon('right', 13) + '</button>'
              : '<button type="button" class="pt-btn pt-btn-primary" data-pt-done>' +
                icon('check', 13) + ' Done</button>') +
          '</div>'
        : '');

    document.body.appendChild(callout);
    position(callout, item.el);

    // Moving onto the callout must not dismiss it.
    callout.addEventListener('mouseenter', cancelClose);
    callout.addEventListener('mouseleave', function () { scheduleClose(); });

    callout.querySelector('.pt-close').addEventListener('click', function () {
      if (isTour) endTour(); else closeCallout();
    });
    var prev = callout.querySelector('[data-pt-prev]');
    if (prev) prev.addEventListener('click', function () { step(-1); });
    var next = callout.querySelector('[data-pt-next]');
    if (next) next.addEventListener('click', function () { step(1); });
    var done = callout.querySelector('[data-pt-done]');
    if (done) done.addEventListener('click', function () { endTour(); });
  }

  /**
   * Place the callout beside its element, flipping above/below and clamping
   * horizontally so it never lands off screen.
   */
  function position(node, el) {
    var b = box(el);
    var w = node.offsetWidth, h = node.offsetHeight;
    var gap = 14;

    var top = b.bottom + gap;
    if (top + h > global.innerHeight - 10) {
      top = b.top - h - gap;                       // flip above
      if (top < 10) top = Math.max(10, global.innerHeight - h - 10);
    }
    var left = b.left + b.width / 2 - w / 2;       // centred on the element
    left = Math.max(10, Math.min(left, global.innerWidth - w - 10));

    node.style.top = Math.round(top) + 'px';
    node.style.left = Math.round(left) + 'px';
  }

  // -------------------------------------------------------------------- tour

  function startTour() {
    // Reuse the drawn markers when there are some, so the current one shows as open.
    var items = currentItems.filter(function (x) { return x.el.isConnected; });
    if (!items.length) items = resolved();
    if (!items.length) return;
    closeCallout();
    hideOutline();
    // The callout carries its own Back/Next/Done, and the bar would cover both it
    // and the control for anything near the bottom of the page.
    if (bar) bar.style.display = 'none';
    tourItems = items;
    tourIndex = 0;
    showTourStep();
  }

  function step(delta) {
    if (tourIndex < 0) return;
    var next = tourIndex + delta;
    if (next < 0 || next >= tourItems.length) return endTour();
    tourIndex = next;
    showTourStep();
  }

  function drawRing(el) {
    var b = box(el);
    ring.style.top = (b.top - 5) + 'px';
    ring.style.left = (b.left - 5) + 'px';
    ring.style.width = (b.width + 10) + 'px';
    ring.style.height = (b.height + 10) + 'px';
  }

  function showTourStep() {
    var item = tourItems[tourIndex];
    if (!item || !item.el.isConnected) return endTour();

    // Bring it into view before measuring, or the ring lands off screen.
    try { item.el.scrollIntoView({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' }); }
    catch (e) { item.el.scrollIntoView(); }

    setTimeout(function () {
      if (tourIndex < 0 || tourItems[tourIndex] !== item) return;   // moved on meanwhile
      if (!ring) {
        ring = document.createElement('div');
        ring.className = 'pt-ui pt-ring';
        document.body.appendChild(ring);
      }
      drawRing(item.el);
      openCallout(item, tourIndex, tourItems.length, true);
      var primary = callout.querySelector('.pt-btn-primary');
      if (primary) primary.focus({ preventScroll: true });
    }, 260);
  }

  function endTour() {
    if (tourIndex < 0 && !ring) return;
    tourIndex = -1;
    tourItems = [];
    if (ring && ring.parentNode) ring.remove();
    ring = null;
    if (bar) bar.style.display = '';
    closeCallout();
  }

  // ----------------------------------------------------------------- journey
  // A scripted tour: an ordered list of steps that can span screens, each a card in
  // the middle of a dimmed page (no token) or a spotlight on one control (token).
  // The app supplies `before(step)`, which navigates to the step's screen and may
  // return a promise; the journey waits for it. Steps whose control is not on the
  // screen are skipped, so one script covers every state of a page.
  //
  //   ProductTour.journey([
  //     { title: 'Welcome', text: 'What this site is for...' },
  //     { hash: 'roles', token: 'find-role-submit', title: 'Forgot your role?', text: '...' }
  //   ], { before: function (step) { return goTo(step.hash); }, onEnd: function () {} });

  var journey = null;        // { steps, index, opts, seq }
  var jRing = null, jScrim = null, jCallout = null, jEl = null;

  function resolveToken(token) {
    var all = document.querySelectorAll('[' + opts.attr + '="' + String(token).replace(/"/g, '\"') + '"]');
    for (var i = 0; i < all.length; i++) if (visible(all[i])) return all[i];
    return null;
  }

  function startJourney(steps, o) {
    if (!steps || !steps.length) return false;
    endJourney(true);
    endTour();
    closeCallout();
    teardown();                       // no dots or bar underneath the scrim
    journey = { steps: steps.slice(), index: -1, opts: o || {}, seq: 0 };
    document.documentElement.classList.add('pt-journey-on');
    journeyStep(1);
    return true;
  }

  function clearJourneyUi() {
    if (jRing && jRing.parentNode) jRing.remove();
    if (jScrim && jScrim.parentNode) jScrim.remove();
    if (jCallout && jCallout.parentNode) jCallout.remove();
    jRing = null; jScrim = null; jCallout = null; jEl = null;
  }

  function endJourney(silent) {
    if (!journey) return;
    var o = journey.opts;
    journey = null;
    clearJourneyUi();
    document.documentElement.classList.remove('pt-journey-on');
    if (silent) return;
    refresh(screen);                  // dots and bar come back if tips are on
    if (typeof o.onEnd === 'function') o.onEnd();
  }

  /** Move `delta` steps (1 or -1), skipping steps whose control is not on screen. */
  function journeyStep(delta) {
    if (!journey) return;
    var next = journey.index + delta;
    if (next < 0) next = 0;
    if (next >= journey.steps.length) return endJourney();
    var j = journey, seq = ++j.seq, step = j.steps[next];
    j.index = next;
    var ready = typeof j.opts.before === 'function' ? j.opts.before(step, next) : null;
    Promise.resolve(ready).then(function () {
      if (journey !== j || j.seq !== seq) return;              // ended or moved on meanwhile
      if (step.token) {
        var el = resolveToken(step.token);
        if (!el) {                                             // not on this screen: skip it
          if (next + delta < 0 || next + delta >= j.steps.length) return endJourney();
          return journeyStep(delta);
        }
        showSpotlightStep(step, el, next);
      } else {
        showCardStep(step, next);
      }
    }, function () { if (journey === j && j.seq === seq) journeyStep(delta); });
  }

  function journeyNav(index) {
    var total = journey.steps.length;
    var last = index >= total - 1;
    return '<div class="pt-nav">' +
      '<span class="pt-step">' + (index + 1) + ' / ' + total + '</span>' +
      (index > 0 ? '<button type="button" class="pt-btn" data-pt-prev>' + icon('left', 13) + ' Back</button>' : '') +
      (last
        ? '<button type="button" class="pt-btn pt-btn-primary" data-pt-done>' + icon('check', 13) + ' Done</button>'
        : '<button type="button" class="pt-btn pt-btn-primary" data-pt-next>Next ' + icon('right', 13) + '</button>') +
      '</div>';
  }

  function buildJourneyCallout(step, index, extraClass) {
    if (jCallout && jCallout.parentNode) jCallout.remove();
    jCallout = document.createElement('div');
    jCallout.className = 'pt-ui pt-callout pt-journey' + (extraClass ? ' ' + extraClass : '');
    jCallout.setAttribute('role', 'dialog');
    jCallout.setAttribute('aria-modal', 'true');
    jCallout.setAttribute('aria-label', (step.title ? step.title + '. ' : '') + 'Step ' + (index + 1) + ' of ' + journey.steps.length);
    jCallout.innerHTML =
      '<button type="button" class="pt-close" aria-label="Skip the tour" title="Skip the tour">' + icon('close', 14) + '</button>' +
      (step.title ? '<div class="pt-title">' + esc(step.title) + '</div>' : '') +
      '<div>' + esc(step.text) + '</div>' +
      journeyNav(index);
    document.body.appendChild(jCallout);
    jCallout.querySelector('.pt-close').addEventListener('click', function () { endJourney(); });
    var prev = jCallout.querySelector('[data-pt-prev]');
    if (prev) prev.addEventListener('click', function () { journeyStep(-1); });
    var next = jCallout.querySelector('[data-pt-next]');
    if (next) next.addEventListener('click', function () { journeyStep(1); });
    var done = jCallout.querySelector('[data-pt-done]');
    if (done) done.addEventListener('click', function () { endJourney(); });
    var primary = jCallout.querySelector('.pt-btn-primary');
    if (primary) primary.focus({ preventScroll: true });
    return jCallout;
  }

  function showCardStep(step, index) {
    jEl = null;
    if (jRing && jRing.parentNode) jRing.remove();
    jRing = null;
    if (!jScrim) {
      jScrim = document.createElement('div');
      jScrim.className = 'pt-ui pt-scrim';
      document.body.appendChild(jScrim);
    }
    buildJourneyCallout(step, index, 'pt-card');
  }

  function showSpotlightStep(step, el, index) {
    var j = journey, seq = j.seq;
    jEl = el;
    try { el.scrollIntoView({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' }); }
    catch (e) { el.scrollIntoView(); }
    setTimeout(function () {
      if (journey !== j || j.seq !== seq) return;
      if (jScrim && jScrim.parentNode) jScrim.remove();
      jScrim = null;
      if (!jRing) {
        jRing = document.createElement('div');
        jRing.className = 'pt-ui pt-ring';
        document.body.appendChild(jRing);
      }
      var b = box(el);
      jRing.style.top = (b.top - 5) + 'px';
      jRing.style.left = (b.left - 5) + 'px';
      jRing.style.width = (b.width + 10) + 'px';
      jRing.style.height = (b.height + 10) + 'px';
      buildJourneyCallout(step, index, '');
      position(jCallout, el);
    }, 280);
  }

  function reflowJourney() {
    if (!journey || !jEl) return;
    if (!jEl.isConnected) { jEl = null; return; }
    if (jRing) {
      var b = box(jEl);
      jRing.style.top = (b.top - 5) + 'px';
      jRing.style.left = (b.left - 5) + 'px';
      jRing.style.width = (b.width + 10) + 'px';
      jRing.style.height = (b.height + 10) + 'px';
    }
    if (jCallout) position(jCallout, jEl);
  }

  // --------------------------------------------------------------- lifecycle

  /** Call at the end of every screen render, with that screen's key. */
  function refresh(screenKey) {
    if (screenKey != null) screen = screenKey;
    if (journey) { teardown(); return; }      // the journey draws its own UI; dots return when it ends
    endTour();
    closeCallout();
    if (!enabled()) { teardown(); syncSwitches(); return; }
    build(true);
    syncSwitches();
  }

  /** Markers are fixed-position, so they have to follow scroll and resize. */
  function scheduleReflow(rebuild) {
    if (rebuild) needRebuild = true;
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () {
      ticking = false;
      var rb = needRebuild;
      needRebuild = false;
      if (journey) { reflowJourney(); return; }
      if (!enabled() || !layer) return;
      if (tourIndex >= 0) {
        var item = tourItems[tourIndex];
        place();                       // the dots, and the callout (openItem is the step)
        if (ring && item) drawRing(item.el);
        return;
      }
      if (rb) build(false); else place();
    });
  }

  function bindOnce() {
    if (bound) return;
    bound = true;

    global.addEventListener('scroll', function () { scheduleReflow(false); }, true);
    global.addEventListener('resize', function () { scheduleReflow(true); });

    /**
     * Hovering the actual control explains it, not just hovering its marker -- with
     * tips switched on, that is the behaviour someone expects.
     *
     * Delegated from the document rather than bound per element, so listeners never
     * pile up on controls that survive a rebuild.
     */
    document.addEventListener('mouseover', function (e) {
      if (!enabled() || tourIndex >= 0 || !currentItems.length) return;
      if (!e.target || !e.target.closest) return;
      if (e.target.closest('.pt-callout') || e.target.closest('.pt-dot')) return;
      var host = e.target.closest('[' + opts.attr + ']');
      if (!host) return;
      var idx = indexOfElement(host);
      if (idx > -1) hoverIn(currentItems[idx], idx);
    });

    document.addEventListener('mouseout', function (e) {
      if (!enabled() || tourIndex >= 0) return;
      if (!e.target || !e.target.closest) return;
      if (e.target.closest('.pt-callout') || e.target.closest('.pt-dot')) return;
      var host = e.target.closest('[' + opts.attr + ']');
      if (host && !(e.relatedTarget && host.contains(e.relatedTarget))) hoverOut();
    });

    document.addEventListener('keydown', function (e) {
      if (journey) {
        if (e.key === 'Escape') endJourney();
        else if (e.key === 'ArrowRight') journeyStep(1);
        else if (e.key === 'ArrowLeft') journeyStep(-1);
        return;
      }
      if (!enabled()) return;
      if (e.key === 'Escape') { if (tourIndex >= 0) endTour(); else closeCallout(); }
      else if (tourIndex >= 0 && e.key === 'ArrowRight') step(1);
      else if (tourIndex >= 0 && e.key === 'ArrowLeft') step(-1);
    });

    // Entry points: a checkbox switch fires change; anything else (a button) fires click.
    document.addEventListener('change', function (e) {
      var t = e.target;
      if (t && t.matches && t.matches('input[data-tips-toggle]')) setEnabled(t.checked);
    });
    document.addEventListener('click', function (e) {
      var t = e.target && e.target.closest && e.target.closest('[data-tips-toggle]');
      if (!t || t.tagName === 'INPUT') return;
      e.preventDefault();
      toggle();
    });
  }

  function init(o) {
    o = o || {};
    for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) opts[k] = o[k];
    memOn = !!opts.defaultOn;
    injectStyles();
    bindOnce();
    syncSwitches();
    return api;
  }

  var api = {
    init: init,
    refresh: refresh,
    enabled: enabled,
    setEnabled: setEnabled,
    toggle: toggle,
    startTour: startTour,
    /** Reposition the markers after a layout change that is not a scroll or resize (a menu opening, a panel expanding). */
    reflow: function () { scheduleReflow(true); },
    /** A scripted tour across screens; see the journey section. */
    journey: startJourney,
    endJourney: function () { endJourney(); },
    journeyActive: function () { return !!journey; },
    /** For tests and admin screens. */
    registry: function () { return opts.tips.slice(); },
    stale: function () { return staleTokens.slice(); }
  };

  global.ProductTour = api;
})(window);
