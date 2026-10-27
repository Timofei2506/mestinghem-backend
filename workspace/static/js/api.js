/* Mestinghem AI — API client =============================================
   Handles bearer tokens, silent refresh, NDJSON streaming and uploads.
   Nothing about the backend infrastructure is exposed here.
   ========================================================================= */
(function (global) {
  'use strict';

  const KEY_ACCESS = 'mst.access';
  const KEY_REFRESH = 'mst.refresh';
  const KEY_USER = 'mst.user';

  const state = {
    access: localStorage.getItem(KEY_ACCESS) || '',
    refresh: localStorage.getItem(KEY_REFRESH) || '',
    user: null,
    config: null,
    refreshing: null,
    listeners: new Set(),
  };

  try { state.user = JSON.parse(localStorage.getItem(KEY_USER) || 'null'); } catch (e) { state.user = null; }

  function save(session) {
    if (!session) return;
    if (session.access_token) { state.access = session.access_token; localStorage.setItem(KEY_ACCESS, session.access_token); }
    if (session.refresh_token) { state.refresh = session.refresh_token; localStorage.setItem(KEY_REFRESH, session.refresh_token); }
    if (session.user) { state.user = session.user; localStorage.setItem(KEY_USER, JSON.stringify(session.user)); }
    emit('auth', state.user);
  }

  function clear() {
    state.access = ''; state.refresh = ''; state.user = null;
    [KEY_ACCESS, KEY_REFRESH, KEY_USER].forEach((k) => localStorage.removeItem(k));
    emit('auth', null);
  }

  function emit(event, detail) {
    state.listeners.forEach((fn) => { try { fn(event, detail); } catch (e) { console.warn(e); } });
  }

  function on(fn) { state.listeners.add(fn); return () => state.listeners.delete(fn); }

  function headers(extra) {
    const h = Object.assign({ 'Accept': 'application/json' }, extra || {});
    if (state.access) h['Authorization'] = `Bearer ${state.access}`;
    const lang = global.I18N ? global.I18N.lang : 'ru';
    h['Accept-Language'] = lang;
    return h;
  }

  async function refresh() {
    if (!state.refresh) return false;
    if (state.refreshing) return state.refreshing;
    state.refreshing = (async () => {
      try {
        const res = await fetch('/api/auth/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ refresh_token: state.refresh }),
        });
        if (!res.ok) { clear(); return false; }
        save(await res.json());
        return true;
      } catch (e) {
        clear();
        return false;
      } finally {
        state.refreshing = null;
      }
    })();
    return state.refreshing;
  }

  async function request(path, options) {
    const opts = Object.assign({ method: 'GET' }, options || {});
    opts.headers = headers(opts.headers);
    if (opts.body && !(opts.body instanceof FormData) && typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
      opts.headers['Content-Type'] = 'application/json';
    }
    if (opts.body instanceof FormData) delete opts.headers['Content-Type'];

    let res;
    try {
      res = await fetch(path, opts);
    } catch (e) {
      throw new ApiError(global.I18N ? global.I18N.t('common.networkError') : 'Network error', 0);
    }

    if (res.status === 401 && state.refresh && !opts._retried) {
      const ok = await refresh();
      if (ok) {
        opts._retried = true;
        opts.headers = headers(opts.headers);
        return request(path, opts);
      }
    }
    if (res.status === 401) { clear(); throw new ApiError(global.I18N ? global.I18N.t('common.sessionExpired') : 'Unauthorized', 401); }

    if (!res.ok) {
      let detail = `${res.status}`;
      try {
        const data = await res.json();
        detail = data.detail || data.message || data.error || detail;
        if (Array.isArray(detail)) detail = detail.map((d) => d.msg || d).join('; ');
      } catch (e) { /* keep status */ }
      throw new ApiError(detail, res.status);
    }
    if (res.status === 204) return null;
    const type = res.headers.get('content-type') || '';
    if (type.includes('application/json')) return res.json();
    return res.text();
  }

  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; this.name = 'ApiError'; }
  }

  /* ---------------------------------------------------------------- stream */
  async function stream(path, body, handlers) {
    const res = await fetch(path, {
      method: 'POST',
      headers: headers({ 'Content-Type': 'application/json', 'Accept': 'application/x-ndjson' }),
      body: JSON.stringify(body),
      signal: handlers.signal,
    });
    if (!res.ok) {
      if (res.status === 401 && state.refresh) {
        const ok = await refresh();
        if (ok) return stream(path, body, handlers);
      }
      let detail = `${res.status}`;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
      throw new ApiError(detail, res.status);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let full = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        let event;
        try { event = JSON.parse(line); } catch (e) { continue; }
        if (event.event === 'delta') {
          full += event.text || '';
          if (handlers.onDelta) handlers.onDelta(event.text, full);
        } else if (handlers.onEvent) {
          handlers.onEvent(event);
        }
      }
    }
    if (handlers.onEnd) handlers.onEnd(full);
    return full;
  }

  /* ------------------------------------------------------------ websocket */
  function ws(path, params, handlers) {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const query = new URLSearchParams(Object.assign({ token: state.access }, params || {}));
    const socket = new WebSocket(`${proto}://${location.host}${path}?${query}`);
    socket.binaryType = 'arraybuffer';
    if (handlers.onOpen) socket.addEventListener('open', handlers.onOpen);
    socket.addEventListener('message', (ev) => {
      if (typeof ev.data === 'string') {
        if (handlers.onText) handlers.onText(ev.data);
        if (handlers.onJson) {
          try { handlers.onJson(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
        }
      } else if (handlers.onBinary) {
        handlers.onBinary(new Uint8Array(ev.data));
      }
    });
    socket.addEventListener('close', (ev) => { if (handlers.onClose) handlers.onClose(ev); });
    socket.addEventListener('error', (ev) => { if (handlers.onError) handlers.onError(ev); });
    return socket;
  }

  /* ------------------------------------------------------------------ api */
  const api = {
    ApiError,
    get user() { return state.user; },
    get access() { return state.access; },
    get isAuthenticated() { return Boolean(state.access && state.user); },
    on, save, clear, refresh, request, stream, ws, headers,

    config: async () => {
      if (state.config) return state.config;
      state.config = await request('/api/config');
      return state.config;
    },

    auth: {
      register: (payload) => request('/api/auth/register', { method: 'POST', body: payload }).then((r) => { save(r); return r; }),
      login: (payload) => request('/api/auth/login', { method: 'POST', body: payload }).then((r) => { save(r); return r; }),
      logout: () => request('/api/auth/logout', { method: 'POST' }).finally(clear),
      me: () => request('/api/auth/me'),
      profile: (payload) => request('/api/auth/profile', { method: 'PATCH', body: payload }).then((r) => { if (r.user) save({ user: r.user }); return r; }),
      password: (payload) => request('/api/auth/password', { method: 'POST', body: payload }),
      sessions: () => request('/api/auth/sessions'),
      revokeAll: () => request('/api/auth/sessions/revoke-all', { method: 'POST' }).finally(clear),
      security: () => request('/api/auth/security'),
      audit: (limit) => request(`/api/auth/audit?limit=${limit || 100}`),
      export: () => request('/api/auth/export'),
      erase: (payload) => request('/api/auth/erase', { method: 'POST', body: payload }).finally(clear),
      mfaSetup: () => request('/api/auth/mfa/setup', { method: 'POST' }),
      mfaEnable: (code) => request('/api/auth/mfa/enable', { method: 'POST', body: { code } }),
      mfaDisable: (current_password) => request('/api/auth/mfa/disable', { method: 'POST', body: { current_password, new_password: current_password } }),
      keys: () => request('/api/auth/keys'),
      createKey: (payload) => request('/api/auth/keys', { method: 'POST', body: payload }),
      revokeKey: (id) => request(`/api/auth/keys/${id}`, { method: 'DELETE' }),
      consents: () => request('/api/auth/consents'),
      consent: (payload) => request('/api/auth/consent', { method: 'POST', body: payload }),
    },

    chat: {
      conversations: () => request('/api/chat/conversations'),
      conversation: (id, limit) => request(`/api/chat/conversations/${id}?limit=${limit || 200}`),
      create: (payload) => request('/api/chat/conversations', { method: 'POST', body: payload || {} }),
      patch: (id, payload) => request(`/api/chat/conversations/${id}`, { method: 'PATCH', body: payload }),
      remove: (id) => request(`/api/chat/conversations/${id}`, { method: 'DELETE' }),
      send: (payload, handlers) => stream('/api/chat/send', payload, handlers),
      retitle: (id) => request(`/api/chat/title?conversation_id=${id}`, { method: 'POST' }),
      translate: (text, target) => request('/api/chat/translate', { method: 'POST', body: { text, target } }),
      stats: () => request('/api/chat/stats'),
    },

    files: {
      list: (params) => request('/api/files/?' + new URLSearchParams(params || {})),
      tree: () => request('/api/files/tree/full'),
      stats: () => request('/api/files/stats'),
      search: (q) => request(`/api/files/search?q=${encodeURIComponent(q)}`),
      get: (id) => request(`/api/files/${id}`),
      upload: (file, folderId) => {
        const form = new FormData();
        form.append('file', file);
        if (folderId) form.append('folder_id', folderId);
        return request('/api/files/upload', { method: 'POST', body: form });
      },
      createText: (payload) => request('/api/files/text', { method: 'POST', body: payload }),
      rename: (id, name) => request(`/api/files/${id}`, { method: 'PATCH', body: { name } }),
      move: (id, folder_id) => request(`/api/files/${id}`, { method: 'PATCH', body: { folder_id } }),
      pin: (id, pinned) => request(`/api/files/${id}`, { method: 'PATCH', body: { pinned } }),
      trash: (id, trashed) => request(`/api/files/${id}`, { method: 'PATCH', body: { trashed } }),
      remove: (id) => request(`/api/files/${id}?permanent=true`, { method: 'DELETE' }),
      createFolder: (payload) => request('/api/files/folders', { method: 'POST', body: payload }),
      removeFolder: (id, permanent) => request(`/api/files/folders/${id}?permanent=${!!permanent}`, { method: 'DELETE' }),
      url: (id) => `/api/files/${id}/download`,
    },

    documents: {
      kinds: () => request('/api/documents/kinds'),
      build: (payload) => request('/api/documents/build', { method: 'POST', body: payload }),
      list: () => request('/api/documents/'),
      get: (id) => request(`/api/documents/${id}`),
      rebuild: (id, brief) => request(`/api/documents/${id}/rebuild`, { method: 'POST', body: { brief } }),
      remove: (id) => request(`/api/documents/${id}`, { method: 'DELETE' }),
      url: (id) => `/api/documents/${id}/download`,
      preview: (id) => `/api/documents/${id}/preview`,
    },

    images: {
      styles: () => request('/api/images/styles'),
      generate: (payload) => request('/api/images/generate', { method: 'POST', body: payload }),
      list: (limit) => request(`/api/images/?limit=${limit || 48}`),
      remove: (id) => request(`/api/images/${id}`, { method: 'DELETE' }),
      url: (id) => `/api/images/${id}/file`,
    },

    chess: {
      games: () => request('/api/chess/games'),
      game: (id) => request(`/api/chess/games/${id}`),
      create: (payload) => request('/api/chess/games', { method: 'POST', body: payload }),
      move: (id, move) => request(`/api/chess/games/${id}/moves`, { method: 'POST', body: { move } }),
      hint: (id) => request(`/api/chess/games/${id}/hint`, { method: 'POST' }),
      analyse: (id, language, multipv) => request(`/api/chess/games/${id}/analyse`, { method: 'POST', body: { language, multipv: multipv || 3 } }),
      review: (id, language) => request(`/api/chess/games/${id}/review`, { method: 'POST', body: { language } }),
      undo: (id) => request(`/api/chess/games/${id}/undo`, { method: 'POST' }),
      resign: (id) => request(`/api/chess/games/${id}/resign`, { method: 'POST' }),
      pgn: (id) => `/api/chess/games/${id}/pgn`,
      openings: () => request('/api/chess/openings'),
      status: () => request('/api/chess/engine/status'),
      analysePosition: (fen, language) => request('/api/chess/position/analyse', { method: 'POST', body: { fen, language } }),
    },

    terminal: {
      policy: () => request('/api/terminal/policy'),
      sessions: () => request('/api/terminal/sessions'),
      open: (payload) => request('/api/terminal/sessions', { method: 'POST', body: payload || {} }),
      close: (id) => request(`/api/terminal/sessions/${id}`, { method: 'DELETE' }),
      run: (payload) => request('/api/terminal/run', { method: 'POST', body: payload }),
      history: (limit) => request(`/api/terminal/history?limit=${limit || 60}`),
      check: (command) => request('/api/terminal/check', { method: 'POST', body: { command } }),
    },

    agent: {
      capabilities: () => request('/api/agent/capabilities'),
      devices: () => request('/api/agent/devices'),
      pair: (payload) => request('/api/agent/pair', { method: 'POST', body: payload }),
      update: (id, payload) => request(`/api/agent/devices/${id}`, { method: 'PATCH', body: payload }),
      revoke: (id) => request(`/api/agent/devices/${id}/revoke`, { method: 'POST' }),
      propose: (payload) => request('/api/agent/actions', { method: 'POST', body: payload }),
      actions: (params) => request('/api/agent/actions?' + new URLSearchParams(params || {})),
      approve: (id) => request(`/api/agent/actions/${id}/approve`, { method: 'POST' }),
      deny: (id) => request(`/api/agent/actions/${id}/deny`, { method: 'POST' }),
    },

    system: {
      health: () => request('/api/health'),
      status: () => request('/api/status'),
      legal: () => request('/api/legal/'),
    },
  };

  global.API = api;
})(window);
