/* Mestinghem AI — application shell, router, overlays ==================== */
(function (global) {
  'use strict';
  const { t, esc } = { t: global.I18N.t, esc: global.Views.esc };
  const API = global.API;

  const MODULES = [
    { id: 'chat', icon: 'spark', key: 'rail.chat' },
    { id: 'directory', icon: 'folder', key: 'rail.directory' },
    { id: 'terminal', icon: 'terminal', key: 'rail.terminal' },
    { id: 'documents', icon: 'presentation', key: 'rail.documents' },
    { id: 'images', icon: 'image', key: 'rail.images' },
    { id: 'chess', icon: 'chess', key: 'rail.chess' },
    { id: 'agent', icon: 'monitor', key: 'rail.agent' },
    { id: 'security', icon: 'shield', key: 'rail.security' },
    { id: 'settings', icon: 'settings', key: 'rail.settings' },
  ];

  let activeModule = 'chat';
  let eventsSocket = null;
  let notifications = [];

  /* ---------------------------------------------------------------- toasts */
  function toast(kind, title, text) {
    const host = document.getElementById('toasts');
    if (!host) return;
    const icons = { ok: 'check', err: 'x', warn: 'alert', info: 'spark' };
    const node = document.createElement('div');
    node.className = `toast ${kind}`;
    node.innerHTML = `<span class="ic">${ico(icons[kind] || 'spark')}</span>
      <span class="tx"><b>${esc(title)}</b>${text ? `<span class="muted small">${esc(text)}</span>` : ''}</span>`;
    host.appendChild(node);
    setTimeout(() => { node.classList.add('out'); setTimeout(() => node.remove(), 320); }, kind === 'err' ? 6200 : 3800);
  }

  /* --------------------------------------------------------------- drawer */
  function openDrawer(html) {
    const drawer = document.getElementById('drawer');
    document.getElementById('drawerBody').innerHTML = html;
    drawer.hidden = false;
    document.body.classList.add('no-scroll');
    drawer.querySelectorAll('[data-close]').forEach((el) => (el.onclick = closeDrawer));
  }
  function closeDrawer() {
    document.getElementById('drawer').hidden = true;
    document.body.classList.remove('no-scroll');
  }

  /* ------------------------------------------------------------- lightbox */
  function lightbox(src) {
    const box = document.getElementById('lightbox');
    const img = box.querySelector('img');
    box.hidden = false;
    if (typeof src === 'string' && src.indexOf('blob:') !== 0 && src.indexOf('/api/') === 0) {
      img.removeAttribute('src');
      API.blobUrl(src).then((u) => { img.src = u; }).catch(() => { img.alt = '!'; });
    } else {
      img.src = src;
    }
    box.querySelectorAll('[data-close]').forEach((el) => (el.onclick = () => { box.hidden = true; }));
    box.onclick = (ev) => { if (ev.target === box) box.hidden = true; };
  }

  /* ----------------------------------------------------------------- auth */
  function openAuth(tab) {
    const modal = document.getElementById('authModal');
    modal.hidden = false;
    document.body.classList.add('no-scroll');
    switchAuthTab(tab || 'login');
    setTimeout(() => {
      const field = modal.querySelector(`#${tab === 'register' ? 'register' : 'login'}Form input:not([type=checkbox])`);
      if (field) field.focus();
    }, 120);
  }
  function closeAuth() {
    document.getElementById('authModal').hidden = true;
    document.body.classList.remove('no-scroll');
  }
  function switchAuthTab(tab) {
    document.querySelectorAll('#authModal .auth-tab').forEach((el) => el.classList.toggle('active', el.dataset.tab === tab));
    document.getElementById('loginForm').hidden = tab !== 'login';
    document.getElementById('registerForm').hidden = tab !== 'register';
  }

  /* ------------------------------------------------------------- palette */
  function paletteItems() {
    const items = MODULES.map((m) => ({ icon: m.icon, title: t(m.key), sub: `#/app/${m.id}`, run: () => go(`#/app/${m.id}`) }));
    if (API.isAuthenticated) {
      items.push({ icon: 'shield', title: t('sec.title'), sub: 'security', run: () => go('#/app/security') });
      items.push({ icon: 'logout', title: t('rail.logout'), sub: 'sign out', run: () => leaveWorkspace() });
      items.push({ icon: '◐', title: t('set.theme'), sub: 'aurum / ivory', run: toggleTheme });
      items.push({ icon: 'globe', title: `${t('set.language')} · RU/EN`, sub: 'language', run: () => global.I18N.setLang(global.I18N.lang === 'ru' ? 'en' : 'ru') });
    } else {
      items.push({ icon: '→', title: t('nav.login'), sub: 'auth', run: () => openAuth('login') });
      items.push({ icon: '＋', title: t('nav.start'), sub: 'auth', run: () => openAuth('register') });
    }
    ['privacy', 'terms', 'security', 'cookies', 'aup', 'dpa', 'agent', 'disclosure'].forEach((doc) => {
      items.push({ icon: '§', title: t('footer.' + (doc === 'aup' ? 'aup' : doc === 'disclosure' ? 'disclosure' : doc)), sub: `#/legal/${doc}`, run: () => go(`#/legal/${doc}`) });
    });
    return items;
  }

  let paletteIndex = 0;
  function openPalette() {
    const box = document.getElementById('palette');
    box.hidden = false;
    const input = document.getElementById('paletteInput');
    input.value = '';
    paletteIndex = 0;
    paintPalette('');
    setTimeout(() => input.focus(), 60);
    box.querySelectorAll('[data-close]').forEach((el) => (el.onclick = closePalette));
    input.oninput = () => { paletteIndex = 0; paintPalette(input.value); };
    input.onkeydown = (ev) => {
      const nodes = [...document.querySelectorAll('#paletteList .palette-item')];
      if (ev.key === 'ArrowDown') { ev.preventDefault(); paletteIndex = Math.min(paletteIndex + 1, nodes.length - 1); paintSelection(nodes); }
      else if (ev.key === 'ArrowUp') { ev.preventDefault(); paletteIndex = Math.max(paletteIndex - 1, 0); paintSelection(nodes); }
      else if (ev.key === 'Enter') { ev.preventDefault(); const node = nodes[paletteIndex]; if (node) node.click(); }
      else if (ev.key === 'Escape') closePalette();
    };
  }
  function paintSelection(nodes) {
    nodes.forEach((n, i) => n.classList.toggle('sel', i === paletteIndex));
    if (nodes[paletteIndex]) nodes[paletteIndex].scrollIntoView({ block: 'nearest' });
  }
  function paintPalette(query) {
    const list = document.getElementById('paletteList');
    const q = query.trim().toLowerCase();
    const items = paletteItems().filter((item) => !q || `${item.title} ${item.sub}`.toLowerCase().includes(q));
    list.innerHTML = items.length ? items.map((item, i) => `
      <div class="palette-item ${i === paletteIndex ? 'sel' : ''}" data-i="${i}">
        <div class="ic">${ico(item.icon)}</div>
        <div class="t"><b>${esc(item.title)}</b><span>${esc(item.sub)}</span></div>
        <span class="muted small">↵</span>
      </div>`).join('') : `<div class="empty small">—</div>`;
    [...list.querySelectorAll('[data-i]')].forEach((el) => {
      el.onclick = () => { items[Number(el.dataset.i)].run(); closePalette(); };
    });
  }
  function closePalette() { document.getElementById('palette').hidden = true; }

  /* ---------------------------------------------------------------- theme */
  function toggleTheme() {
    const current = document.documentElement.dataset.theme === 'ivory' ? 'aurum' : 'ivory';
    document.documentElement.dataset.theme = current;
    localStorage.setItem('mst.theme', current);
    if (API.isAuthenticated) API.auth.profile({ theme: current }).catch(() => {});
  }

  /* ----------------------------------------------------------------- rail */
  function buildRail() {
    const nav = document.getElementById('railNav');
    if (!nav) return;
    const perms = (API.user && API.user.permissions) || {};
    const staff = API.user && ['owner', 'admin'].includes(API.user.role);
    nav.innerHTML = MODULES.filter((m) => {
      if (['terminal', 'agent'].includes(m.id)) return Boolean(perms[m.id]) || staff;
      return true;
    }).map((m) => `
      <button class="rail-link ${m.id === activeModule ? 'active' : ''}" data-mod="${m.id}">
        ${ico(m.icon)}<span class="tip">${t(m.key)}</span></button>`).join('');
    nav.querySelectorAll('[data-mod]').forEach((btn) => btn.addEventListener('click', () => go(`#/app/${btn.dataset.mod}`)));

    const userBox = document.getElementById('railUser');
    if (userBox && API.user) {
      const initial = (API.user.display_name || API.user.username || 'U').slice(0, 1).toUpperCase();
      userBox.innerHTML = `<div class="rail-user" title="${esc(API.user.display_name || '')}">${esc(initial)}</div>`;
      userBox.querySelector('.rail-user').addEventListener('click', () => go('#/app/settings'));
      const chip = document.getElementById('userChip');
      if (chip) {
        chip.innerHTML = `<span class="av">${esc(initial)}</span>
          <span><span class="nm">${esc(API.user.display_name || API.user.username)}</span><br>
          <span class="pl">${esc(API.user.plan)}</span></span>`;
        chip.onclick = () => go('#/app/settings');
      }
    }
  }

  function setCrumbs(moduleId) {
    const found = MODULES.find((m) => m.id === moduleId);
    const crumbs = document.getElementById('crumbs');
    if (!crumbs) return;
    crumbs.innerHTML = found
      ? `<span>${t('nav.platform')}</span><span class="sep">/</span><b>${t(found.key)}</b>`
      : `<span>${t('nav.platform')}</span>`;
  }

  /* --------------------------------------------------------------- router */
  const VIEWS = {
    chat: global.Views.chat,
    directory: global.Views.directory,
    terminal: global.Views.terminal,
    documents: global.Views.documents,
    images: global.Views.images,
    chess: global.Views.chess,
    agent: global.Views.agent,
    security: global.Views.security,
    settings: global.Views.settings,
  };

  function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }

  async function route() {
    const hash = location.hash || '#/';
    const boot = document.getElementById('boot');
    const landing = document.getElementById('landing');
    const workspace = document.getElementById('workspace');

    if (hash.startsWith('#/legal/')) {
      landing.hidden = false; workspace.hidden = true;
      boot.classList.add('done');
      return showLegal(hash.split('/')[2], landing);
    }
    if (hash.startsWith('#/app')) {
      if (!API.isAuthenticated) { openAuth('login'); landing.hidden = false; workspace.hidden = true; boot.classList.add('done'); return; }
      landing.hidden = true; workspace.hidden = false;
      boot.classList.add('done');
      const moduleId = hash.split('/')[2] || 'chat';
      await mountModule(moduleId);
      return;
    }
    landing.hidden = false; workspace.hidden = true;
    boot.classList.add('done');
    global.Landing.render();
    window.scrollTo({ top: 0 });
    if (['#/platform', '#/security', '#/pricing', '#/chess'].includes(hash)) {
      const target = document.querySelector(hash.slice(1));
      if (target) setTimeout(() => target.scrollIntoView({ behavior: 'smooth' }), 80);
    }
  }

  async function mountModule(moduleId) {
    if (!VIEWS[moduleId]) moduleId = 'chat';
    activeModule = moduleId;
    buildRail();
    setCrumbs(moduleId);
    const host = document.getElementById('views');
    host.style.opacity = '0';
    host.innerHTML = `<div class="view-head"><div><h1>${t('common.loading')}</h1></div></div>${global.Views.spinner()}`;
    requestAnimationFrame(() => { host.style.transition = 'opacity .3s'; host.style.opacity = '1'; });
    try {
      await VIEWS[moduleId](host);
    } catch (e) {
      host.innerHTML = global.Views.emptyState('alert', t('common.error'), e.message);
    }
    history.replaceState(null, '', `#/app/${moduleId}`);
  }

  /* ---------------------------------------------------------- legal reader */
  async function showLegal(slug, landing) {
    const main = landing;
    let host = document.getElementById('legalHost');
    if (!host) {
      host = document.createElement('div');
      host.id = 'legalHost';
      host.className = 'section';
      const footer = main.querySelector('.footer');
      main.insertBefore(host, footer);
    }
    const hero = main.querySelector('.hero');
    const sections = [...main.querySelectorAll('.section')].filter((s) => s.id !== 'legalHost');
    hero.hidden = true;
    sections.forEach((s) => { s.hidden = true; });
    main.querySelectorAll('.nav-links a').forEach((a) => a.classList.remove('active'));
    host.hidden = false;
    host.innerHTML = global.Views.spinner();
    try {
      const response = await fetch(`/api/legal/${slug}?format=md&language=${global.I18N.lang}`);
      if (!response.ok) throw new Error(`${response.status}`);
      const markdown = await response.text();
      host.innerHTML = `<div style="max-width:880px;margin:0 auto">
        <a href="#/" class="btn btn-ghost btn-sm" style="margin-bottom:18px">← ${t('common.back')}</a>
        <article class="legal-body">${global.Views.md(markdown)}</article></div>`;
      window.scrollTo({ top: 0 });
    } catch (e) {
      host.innerHTML = global.Views.emptyState('§', t('common.error'), e.message);
    }
    main.dataset.legalOpen = '1';
    const observer = () => {
      if (!(location.hash || '').startsWith('#/legal/')) {
        hero.hidden = false;
        sections.forEach((s) => { s.hidden = false; });
        host.hidden = true;
        main.dataset.legalOpen = '';
        window.removeEventListener('hashchange', observer);
      }
    };
    window.addEventListener('hashchange', observer);
  }

  /* ------------------------------------------------------------ workspace */
  function enterWorkspace() {
    document.getElementById('landing').hidden = true;
    document.getElementById('workspace').hidden = false;
    document.getElementById('boot').classList.add('done');
    buildRail();
    if (!location.hash.startsWith('#/app')) location.hash = '#/app/chat';
    else route();
    connectEvents();
  }

  function leaveWorkspace() {
    if (eventsSocket) { try { eventsSocket.close(); } catch (e) {} eventsSocket = null; }
    API.clear();
    document.getElementById('workspace').hidden = true;
    document.getElementById('landing').hidden = false;
    location.hash = '#/';
    route();
  }

  async function reloadUser() {
    try {
      const data = await API.auth.me();
      API.save({ user: data.user });
      buildRail();
      document.documentElement.dataset.theme = (data.user.theme === 'ivory' ? 'ivory' : 'aurum');
    } catch (e) { /* ignore */ }
  }

  function connectEvents() {
    if (eventsSocket) { try { eventsSocket.close(); } catch (e) {} }
    try {
      eventsSocket = API.ws('/api/ws/events', {}, {
        onJson(event) {
          if (event.event === 'notification') {
            notifications.unshift(event);
            notifications = notifications.slice(0, 40);
            paintBell();
            toast('info', event.title || '', event.body || '');
          } else if (event.event === 'approvals_pending') {
            const badge = document.getElementById('approvalBadge');
            if (badge) { badge.textContent = event.count; badge.hidden = !event.count; }
          }
        },
        onClose() { setTimeout(() => { if (API.isAuthenticated) connectEvents(); }, 15000); },
      });
      setInterval(() => {
        if (eventsSocket && eventsSocket.readyState === 1) eventsSocket.send('ping');
      }, 25000);
    } catch (e) { /* websocket unavailable */ }
  }

  function paintBell() {
    const badge = document.getElementById('bellBadge');
    if (badge) { badge.textContent = notifications.length; badge.hidden = notifications.length === 0; }
  }

  /* ----------------------------------------------------------------- boot */
  async function boot() {
    const storedTheme = localStorage.getItem('mst.theme');
    if (storedTheme) document.documentElement.dataset.theme = storedTheme;

    global.I18N.apply(document);
    document.addEventListener('mst:lang', () => {
      global.I18N.apply(document);
      global.Landing.render();
      if (API.isAuthenticated && !document.getElementById('workspace').hidden) {
        buildRail();
        setCrumbs(activeModule);
        mountModule(activeModule);
      }
    });

    document.getElementById('btnLang2').addEventListener('click', () =>
      global.I18N.setLang(global.I18N.lang === 'ru' ? 'en' : 'ru'));
    document.getElementById('btnPalette').addEventListener('click', openPalette);
    document.getElementById('btnTheme').addEventListener('click', toggleTheme);
    document.getElementById('btnBell').addEventListener('click', () => {
      openDrawer(`<h2>${global.I18N.lang === 'ru' ? 'Уведомления' : 'Notifications'}</h2>
        ${notifications.length ? notifications.map((n) => `
          <div class="perm-row"><div class="t"><b>${esc(n.title || '')}</b>
            <span>${esc(n.body || '')}</span>
            <span class="muted small">${global.I18N.relative(n.created_at)}</span></div></div>`).join('')
          : global.Views.emptyState('bell', global.I18N.lang === 'ru' ? 'Уведомлений нет' : 'No notifications', '')}`);
    });
    document.getElementById('btnApprovals').addEventListener('click', () => go('#/app/agent'));

    document.addEventListener('keydown', (ev) => {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'k') { ev.preventDefault(); openPalette(); }
      if (ev.key === 'Escape') { closePalette(); closeDrawer(); document.getElementById('lightbox').hidden = true; }
    });

    window.addEventListener('hashchange', route);

    try { await API.config(); } catch (e) { /* offline preview */ }

    if (API.isAuthenticated) {
      try {
        const data = await API.auth.me();
        API.save({ user: data.user });
        document.documentElement.dataset.theme = data.user.theme === 'ivory' ? 'ivory' : 'aurum';
        if (data.user.language && data.user.language !== global.I18N.lang) global.I18N.setLang(data.user.language);
        enterWorkspace();
        return;
      } catch (e) {
        const ok = await API.refresh();
        if (ok) { enterWorkspace(); return; }
      }
    }
    global.Landing.init();
    setTimeout(() => document.getElementById('boot').classList.add('done'), 620);
    route();
  }

  global.App = {
    boot, route, go, toast, openDrawer, closeDrawer, lightbox, openAuth, closeAuth, switchAuthTab,
    enterWorkspace, leaveWorkspace, reloadUser, buildRail, openPalette, toggleTheme,
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})(window);
