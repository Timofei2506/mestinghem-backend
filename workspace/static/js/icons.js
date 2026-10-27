/* Mestinghem AI — inline SVG icon system (no emoji, no network, no icon font).
   Usage: ico('shield')  ->  <svg class="ico-svg" ...>...</svg>
   All icons are 24x24, stroke=currentColor by default; solid ones override fill. */
(function (global) {
  'use strict';

  var ICONS = {
    /* --- modules / nav --- */
    spark: '<path fill="currentColor" stroke="none" d="M12 2.2l2.1 6.1 6.1 2.1-6.1 2.1L12 18.6l-2.1-6.1L3.8 10.4l6.1-2.1z"/><path fill="currentColor" stroke="none" d="M18.5 15.5l.9 2.4 2.4.9-2.4.9-.9 2.4-.9-2.4-2.4-.9 2.4-.9z"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h3.6l2 2.4H19a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    terminal: '<rect x="2.5" y="4" width="19" height="16" rx="2.5"/><path d="M7 10l2.6 2.4L7 15M12.6 15.2H17"/>',
    document: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
    presentation: '<rect x="2.5" y="4" width="19" height="12" rx="2"/><path d="M12 16v4M8.5 20.5h7M7 12l2.6-3 2.2 2L15 7.5"/>',
    spreadsheet: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9.5h18M3 15h18M9.5 4v16M15 4v16"/>',
    image: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="8.6" cy="9.4" r="1.5"/><path d="M21 16l-4.6-4.3L7 20"/>',
    audio: '<path d="M9 18V6l11-2v12"/><circle cx="6.4" cy="18" r="2.6"/><circle cx="17.4" cy="16" r="2.6"/>',
    video: '<rect x="2.5" y="5" width="14" height="14" rx="2.5"/><path d="M16.5 10.5l5-3v9l-5-3z"/>',
    archive: '<rect x="2.5" y="4" width="19" height="4.5" rx="1.5"/><path d="M4.5 8.5V19a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5V8.5M10 12.5h4"/>',
    code: '<path d="M16 18l6-6-6-6M8 6l-6 6 6 6"/>',
    file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    chess: '<path fill="currentColor" stroke="none" d="M12 3.2a3 3 0 0 1 1.9 5.3c-.3.2-.5.6-.5 1V11h1.3a1 1 0 0 1 1 .9l.6 4.3a1 1 0 0 1-1 1.1H8.7a1 1 0 0 1-1-1.1l.6-4.3a1 1 0 0 1 1-.9H10.6V9.5c0-.4-.2-.8-.5-1A3 3 0 0 1 12 3.2zM6.6 19h10.8l.9 2.2a.6.6 0 0 1-.6.8H6.3a.6.6 0 0 1-.6-.8z"/>',
    monitor: '<rect x="2.5" y="4" width="19" height="12.5" rx="2"/><path d="M8.5 20.5h7M12 16.5v4"/>',
    shield: '<path d="M12 21.5s7.5-3.6 7.5-9.4V5.6L12 2.8 4.5 5.6v6.5c0 5.8 7.5 9.4 7.5 9.4z"/><path d="M9.2 11.8l1.9 1.9 3.7-3.8"/>',
    settings: '<path d="M4 20v-6M4 10.5V4M12 20v-8M12 8.5V4M20 20v-4M20 12.5V4M1.5 13.5h5M9.5 8.5h5M17.5 15.5h5"/>',
    windows: '<path d="M3 5.5l8-1.1v7.1H3zM12.2 4.2L21 3v8.5h-8.8zM3 12.5h8v7.1l-8-1.1zM12.2 12.5H21V21l-8.8-1.2z"/>',

    /* --- status / feedback --- */
    check: '<path d="M4.5 12.5l5 5 10-11"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
    alert: '<path d="M12 3.6L1.9 20.2h20.2z"/><path d="M12 9.6v4.6M12 17.4h.01"/>',
    info: '<circle cx="12" cy="12" r="9.2"/><path d="M12 11v5.4M12 7.7h.01"/>',
    bell: '<path d="M18 8.6A6 6 0 0 0 6 8.6c0 6.4-2.4 8.2-2.4 8.2h16.8S18 15 18 8.6"/><path d="M13.7 20.4a2 2 0 0 1-3.4 0"/>',
    ban: '<circle cx="12" cy="12" r="9.2"/><path d="M5.6 5.6l12.8 12.8"/>',

    /* --- actions --- */
    send: '<path d="M21.5 2.5L10.8 13.2M21.5 2.5l-6.8 19-3.9-8.3-8.3-3.9z"/>',
    attach: '<path d="M20.4 11.2l-8.1 8.1a5 5 0 0 1-7.1-7.1l8.1-8.1a3.4 3.4 0 0 1 4.8 4.8l-8 8.1a1.8 1.8 0 0 1-2.5-2.5l7.4-7.4"/>',
    bolt: '<path fill="currentColor" stroke="none" d="M13.2 2L4 13.4h6L9.4 22 20 10.2h-6.6z"/>',
    download: '<path d="M20.5 15.5v3a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-3M7.5 10.5l4.5 4.5 4.5-4.5M12 15V3.5"/>',
    upload: '<path d="M20.5 15.5v3a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-3M7.5 8l4.5-4.5L16.5 8M12 3.5V15"/>',
    trash: '<path d="M3.5 6.5h17M9 6.5V4.6a1.6 1.6 0 0 1 1.6-1.6h2.8A1.6 1.6 0 0 1 15 4.6v1.9M6.5 6.5l.9 13a1.6 1.6 0 0 0 1.6 1.5h6a1.6 1.6 0 0 0 1.6-1.5l.9-13"/>',
    star: '<path fill="currentColor" stroke="none" d="M12 2.6l2.9 5.9 6.5.9-4.7 4.6 1.1 6.5-5.8-3-5.8 3 1.1-6.5L2.6 9.4l6.5-.9z"/>',
    edit: '<path d="M11.5 4.5H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6.5"/><path d="M18.4 2.6a2.1 2.1 0 0 1 3 3L12.6 14.4l-4 1 1-4z"/>',
    refresh: '<path d="M20.5 4.5v5.6h-5.6M3.5 19.5v-5.6h5.6"/><path d="M5.3 9.6a8 8 0 0 1 13.2-3.1l2 2M3.5 13.9l2 2a8 8 0 0 0 13.2-3.1"/>',
    play: '<path fill="currentColor" stroke="none" d="M7 4.6l12 7.4-12 7.4z"/>',
    logout: '<path d="M9.5 21H5.6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9M15.8 16.5l4.7-4.5-4.7-4.5M20.5 12H9.8"/>',
    dice: '<rect x="3.5" y="3.5" width="17" height="17" rx="3.5"/><circle cx="8.8" cy="8.8" r="1.35" fill="currentColor" stroke="none"/><circle cx="15.2" cy="15.2" r="1.35" fill="currentColor" stroke="none"/><circle cx="15.2" cy="8.8" r="1.35" fill="currentColor" stroke="none"/><circle cx="8.8" cy="15.2" r="1.35" fill="currentColor" stroke="none"/>',
    flag: '<path d="M5 21V4.2s1.4-1 4-1 4.4 1.7 7 1.7c1.6 0 3-.6 3-.6v9.3s-1.4.8-3 .8c-2.6 0-4.4-1.7-7-1.7-2.6 0-4 .9-4 .9"/>',
    search: '<circle cx="10.8" cy="10.8" r="6.8"/><path d="M20.5 20.5l-4.9-4.9"/>',
    filter: '<path d="M21.5 4.5H2.5l7.6 9v6.3l3.8 1.9V13.5z"/>',
    copy: '<rect x="8.5" y="8.5" width="12" height="12" rx="2"/><path d="M5 15.5H4a1.5 1.5 0 0 1-1.5-1.5V4.5A2 2 0 0 1 4.5 2.5H14A1.5 1.5 0 0 1 15.5 4v1"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    external: '<path d="M18 13.5V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5.5M15 3h6v6M10.5 13.5L21 3"/>',

    /* --- security cards --- */
    lock: '<rect x="4" y="10.5" width="16" height="10.5" rx="2.2"/><path d="M7.8 10.5V7.2a4.2 4.2 0 0 1 8.4 0v3.3"/>',
    key: '<circle cx="7.6" cy="16.4" r="3.9"/><path d="M10.5 13.6L20 4.1M17.2 6.9l2.4 2.4M14.6 9.5l2 2"/>',
    clock: '<circle cx="12" cy="12" r="9.2"/><path d="M12 6.6V12l3.6 2.2"/>',
    ticket: '<path d="M3 8.6V7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v1.6a2.6 2.6 0 0 0 0 5.2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3.2a2.6 2.6 0 0 0 0-5.2z"/><path d="M13.5 5v2.2M13.5 10.9v2.2M13.5 16.8V19"/>',
    pause: '<rect x="6.5" y="4.5" width="4" height="15" rx="1.4" fill="currentColor" stroke="none"/><rect x="13.5" y="4.5" width="4" height="15" rx="1.4" fill="currentColor" stroke="none"/>',
    receipt: '<path d="M5 2.8v18.4l2.3-1.4 2.4 1.4 2.3-1.4 2.4 1.4 2.3-1.4 2.3 1.4V2.8l-2.3 1.4-2.3-1.4-2.4 1.4L12 2.8 9.7 4.2 7.3 2.8z"/><path d="M8.6 8.4h6.8M8.6 12.2h6.8M8.6 16h4.4"/>',
    box: '<path d="M20.5 16.2V8a2 2 0 0 0-1-1.7l-6.6-3.8a2 2 0 0 0-2 0L4.3 6.3A2 2 0 0 0 3.3 8v8.2a2 2 0 0 0 1 1.7l6.6 3.8a2 2 0 0 0 2 0l6.6-3.8a2 2 0 0 0 1-1.7z"/><path d="M3.6 7l8.4 4.8L20.4 7M12 21.6V11.8"/>',
    globe: '<circle cx="12" cy="12" r="9.2"/><path d="M2.8 12h18.4M12 2.8a15 15 0 0 1 3.9 9.2A15 15 0 0 1 12 21.2a15 15 0 0 1-3.9-9.2A15 15 0 0 1 12 2.8z"/>',
    eye: '<path d="M1.8 12S5.6 5.2 12 5.2 22.2 12 22.2 12 18.4 18.8 12 18.8 1.8 12 1.8 12z"/><circle cx="12" cy="12" r="3"/>',
    home: '<path d="M3.5 10.2L12 3l8.5 7.2V20a1.5 1.5 0 0 1-1.5 1.5h-4v-6h-6v6H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
    cpu: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/><rect x="9.8" y="9.8" width="4.4" height="4.4" rx="1"/><path d="M9.5 2.8v3.7M14.5 2.8v3.7M9.5 17.5v3.7M14.5 17.5v3.7M2.8 9.5h3.7M2.8 14.5h3.7M17.5 9.5h3.7M17.5 14.5h3.7"/>',
    layers: '<path d="M12 2.8l9 4.7-9 4.7-9-4.7z"/><path d="M3 12.5l9 4.7 9-4.7M3 17l9 4.7 9-4.7"/>',
    user: '<circle cx="12" cy="8" r="3.8"/><path d="M4.8 20.5a7.2 7.2 0 0 1 14.4 0"/>',
    contrast: '<circle cx="12" cy="12" r="9"/><path fill="currentColor" stroke="none" d="M12 3a9 9 0 0 0 0 18z"/>',
    database: '<ellipse cx="12" cy="5.8" rx="8" ry="3"/><path d="M4 5.8v12.4c0 1.7 3.6 3 8 3s8-1.3 8-3V5.8M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  };

  function ico(name, opts) {
    opts = opts || {};
    var inner = ICONS[name] || ICONS.spark;
    var size = opts.size || 20;
    var cls = 'ico-svg' + (opts.cls ? ' ' + opts.cls : '');
    return '<svg class="' + cls + '" width="' + size + '" height="' + size +
      '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + inner + '</svg>';
  }

  /* Map any legacy emoji/glyph token to an icon name (defensive: ico() also
     accepts the original character so nothing breaks mid-migration). */
  var ALIAS = {
    '\u2726': 'spark', '\u{1F5C2}': 'folder', '\u2318': 'terminal', '\u25A4': 'presentation',
    '\u25C8': 'image', '\u265E': 'chess', '\u{1F5A5}': 'monitor', '\u{1F6E1}': 'shield',
    '\u2699': 'settings', '\u2713': 'check', '\u2715': 'x', '\u26A0': 'alert', '\u{1F514}': 'bell',
    '\u{1F310}': 'globe', '\u{1F441}': 'eye', '\u27A4': 'send', '\u{1F4CE}': 'attach',
    '\u26A1': 'bolt', '\u{1F3E0}': 'home', '\u{1F5D1}': 'trash', '\u2B07': 'download',
    '\u2B06': 'upload', '\u2605': 'star', '\u270E': 'edit', '\u21BA': 'refresh',
    '\u25B6': 'play', '\u266B': 'audio', '\u{1F5DC}': 'archive', '\u{1F4E6}': 'box',
    '\u2691': 'flag', '\u2684': 'dice', '\u238B': 'logout', '\u{1F50D}': 'search',
    '\u{1F510}': 'lock', '\u{1F512}': 'lock', '\u{1F5DD}': 'key', '\u23F1': 'clock',
    '\u{1F3AB}': 'ticket', '\u{1F6D1}': 'pause', '\u{1F9FE}': 'receipt', '\u270D': 'edit',
    '\u{1F6AB}': 'ban', '\u{1F575}': 'filter', '\u{1F30D}': 'globe', '\u2716': 'x',
    '\u25A6': 'spreadsheet', '\u{1F5CE}': 'document', '\u{1F5CB}': 'file', '\u{1F5C0}': 'folder',
    '\u229E': 'windows', '\u{1F427}': 'monitor', '\u2139': 'info',
  };

  function smart(token, opts) {
    if (token && ICONS[token]) return ico(token, opts);
    if (token && ALIAS[token]) return ico(ALIAS[token], opts);
    return ico('spark', opts);
  }

  global.ico = ico;
  global.icoSmart = smart;
  global.ICONS = ICONS;

  /* Auto-fill static placeholders: <span data-ico="bell"></span> -> SVG.
     Runs once the DOM is parsed (icons.js is loaded at the end of <body>). */
  function fillPlaceholders(root) {
    var scope = root || document;
    if (!scope.querySelectorAll) return;
    scope.querySelectorAll('[data-ico]').forEach(function (el) {
      if (el.dataset.icoDone) return;
      el.dataset.icoDone = '1';
      el.innerHTML = ico(el.getAttribute('data-ico'), { size: el.dataset.size ? +el.dataset.size : 20 });
    });
  }
  global.fillIcons = fillPlaceholders;
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { fillPlaceholders(document); });
    } else {
      fillPlaceholders(document);
    }
  }
})(typeof window !== 'undefined' ? window : globalThis);
