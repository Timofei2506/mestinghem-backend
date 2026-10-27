/* Mestinghem AI — workspace views =========================================
   Each module renders its own markup and wires its own behaviour.
   ========================================================================= */
(function (global) {
  'use strict';
  const { t, fmtBytes, fmtDate, relative } = global.I18N;
  const API = global.API;
  const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  /* authed media: fetch with bearer → object URL (plain <img src> would 401) */
  async function downloadBlob(path, name) {
    try {
      const u = await API.blobUrl(path);
      const a = document.createElement('a');
      a.href = u; a.download = name || '';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(u), 60000);
    } catch (e) { global.App.toast('err', t('common.error'), e.message); }
  }
  document.addEventListener('click', (ev) => {
    const tgt = ev.target;
    if (!tgt || !tgt.closest) return;
    const im = tgt.closest('img[data-img]');
    if (im) {
      const cur = im.currentSrc || im.src || '';
      global.App.lightbox(cur.indexOf('blob:') === 0 ? cur : API.images.url(im.dataset.img));
      return;
    }
    const el = tgt.closest('[data-dl],[data-file],[data-doc]');
    if (!el) return;
    ev.preventDefault();
    if (el.dataset.dl) return downloadBlob(el.dataset.dl, el.dataset.name || '');
    if (el.dataset.file) return downloadBlob(API.files.url(el.dataset.file), el.dataset.name || '');
    if (el.dataset.doc) return downloadBlob(`/api/documents/${el.dataset.doc}/download`, el.dataset.name || '');
  });

  const S = global.Store = {
    conversations: [],
    conversationId: null,
    messages: [],
    modes: [],
    mode: 'assistant',
    attachments: [],
    streaming: false,
    abort: null,
    files: { folders: [], files: [], folderId: null, view: 'grid', trashed: false, search: '' },
    terminal: { session: null, socket: null, history: [], policy: null },
    chess: { game: null, flipped: false, analysis: null, selected: null, legal: [], busy: false },
    images: { style: 'none', width: 1024, height: 1024, count: 1, list: [], styles: [] },
    docs: { kind: 'pptx', size: 'medium', list: [] },
    agent: { caps: null, devices: [], actions: [] },
    security: null,
    settingsTab: 'profile',
  };

  /* ------------------------------------------------------------ markdown */
  function md(text) {
    let src = String(text || '');
    const blocks = [];
    src = src.replace(/```([\w+-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      blocks.push(`<pre><code class="lang-${esc(lang)}">${esc(code.replace(/\n$/, ''))}</code></pre>`);
      return `\u0000B${blocks.length - 1}\u0000`;
    });
    src = src.replace(/`([^`\n]+)`/g, (_, code) => `\u0000C${blocks.push(`<code>${esc(code)}</code>`) - 1}\u0000`);
    src = esc(src);
    src = src.replace(/^\s{0,3}#{1,6}\s+(.+)$/gm, (_, h) => `<h3>${h}</h3>`);
    src = src.replace(/^\s{0,3}>\s?(.*)$/gm, '<blockquote>$1</blockquote>');
    src = src.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    src = src.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    src = src.replace(/~~([^~]+)~~/g, '<del>$1</del>');
    src = src.replace(/^\s*[-*+]\s+(.+)$/gm, '<li>$1</li>');
    src = src.replace(/^\s*(\d+)[.)]\s+(.+)$/gm, '<li>$2</li>');
    src = src.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, '<ul>$1</ul>');
    src = src.replace(/<\/ul>\s*<ul>/g, '');
    // tables
    src = src.replace(/^(\|.+\|)\n(\|[\s:|-]+\|)\n((?:\|.+\|\n?)+)/gm, (_, head, _sep, body) => {
      const th = head.split('|').slice(1, -1).map((c) => `<th>${c.trim()}</th>`).join('');
      const rows = body.trim().split('\n').map((line) =>
        `<tr>${line.split('|').slice(1, -1).map((c) => `<td>${c.trim()}</td>`).join('')}</tr>`).join('');
      return `<table><thead><tr>${th}</tr></thead><tbody>${rows}</tbody></table>`;
    });
    src = src.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    src = src.split(/\n{2,}/).map((chunk) => {
      const trimmed = chunk.trim();
      if (!trimmed) return '';
      if (/^<(h\d|ul|ol|pre|table|blockquote|li)/.test(trimmed)) return trimmed;
      if (/^\u0000B\d+\u0000$/.test(trimmed)) return trimmed;
      return `<p>${trimmed.replace(/\n/g, '<br>')}</p>`;
    }).join('\n');
    src = src.replace(/\u0000B(\d+)\u0000/g, (_, i) => blocks[Number(i)] || '');
    src = src.replace(/\u0000C(\d+)\u0000/g, (_, i) => blocks[Number(i)] || '');
    return src;
  }

  /* --------------------------------------------------------------- helpers */
  const iconFor = (kind) => ico(({
    presentation: 'presentation', spreadsheet: 'spreadsheet', document: 'document', image: 'image', audio: 'audio',
    video: 'video', archive: 'archive', code: 'code', file: 'file',
  }[kind] || 'file'));

  function spinner(label) {
    return `<div class="empty"><div class="typing"><i></i><i></i><i></i></div><div class="muted small">${esc(label || t('common.loading'))}</div></div>`;
  }

  function emptyState(icon, title, sub) {
    return `<div class="empty"><div class="ico">${ico(icon)}</div><div><b>${esc(title)}</b></div><div class="muted small">${esc(sub || '')}</div></div>`;
  }

  /* ===================================================================== */
  /* CHAT                                                                   */
  /* ===================================================================== */
  const CHAT_IDEAS = {
    ru: [
      'Собери презентацию для совета директоров по выходу на новый рынок',
      'Построй финансовую модель на 12 месяцев в Excel с графиком',
      'Проанализируй риски проекта и оформи отчёт в Word',
      'Сгенерируй 4 люксовых визуала для лендинга',
      'Сыграй со мной партию в шахматы и разбери мои ошибки',
      'Покажи состояние рабочей области через терминал',
    ],
    en: [
      'Build a board deck for entering a new market',
      'Create a 12-month financial model in Excel with a chart',
      'Analyse project risks and produce a Word report',
      'Generate four luxury visuals for a landing page',
      'Play a chess game with me and review my mistakes',
      'Show the workspace state through the terminal',
    ],
  };

  function renderChat(host) {
    host.innerHTML = `
      <div class="view-head">
        <div><h1>${t('chat.title')}</h1><p>${t('chat.sub')}</p></div>
        <div class="actions">
          <button class="btn btn-outline btn-sm" id="chatNew">＋ ${t('chat.new')}</button>
        </div>
      </div>
      <div class="chat-layout">
        <aside class="conv-list">
          <div class="conv-head"><b class="small">${t('chat.new')}</b>
            <span class="spacer" style="margin-left:auto"></span>
            <button class="mini-btn" id="convRefresh" title="${t('common.refresh')}">⟳</button></div>
          <div class="conv-items" id="convItems">${spinner()}</div>
        </aside>
        <section class="chat-main">
          <div class="chat-stream" id="chatStream"></div>
          <div class="composer">
            <div class="attach-row" id="attachRow"></div>
            <div class="composer-row">
              <textarea id="chatInput" rows="1" placeholder="${esc(t('chat.placeholder'))}"></textarea>
              <button class="send-btn" id="chatSend" title="Enter">${ico('send')}</button>
            </div>
            <div class="composer-tools">
              <button class="tool-btn" id="btnAttach">${ico('attach')} ${t('chat.attach')}</button>
              <button class="tool-btn on" id="btnStream">${ico('bolt')} ${t('chat.stream')}</button>
              <span class="muted small" id="chatMeta" style="margin-left:auto"></span>
            </div>
            <input type="file" id="filePicker" multiple hidden>
          </div>
        </section>
      </div>`;

    const stream = document.getElementById('chatStream');
    const input = document.getElementById('chatInput');
    const sendBtn = document.getElementById('chatSend');

    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = `${Math.min(240, input.scrollHeight)}px`;
    });
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); send(); }
    });
    sendBtn.addEventListener('click', send);
    document.getElementById('chatNew').addEventListener('click', () => { S.conversationId = null; S.messages = []; paint(); loadConversations(); });
    document.getElementById('convRefresh').addEventListener('click', loadConversations);

    const picker = document.getElementById('filePicker');
    document.getElementById('btnAttach').addEventListener('click', () => picker.click());
    picker.addEventListener('change', () => {
      [...picker.files].slice(0, 6).forEach((file) => S.attachments.push({ id: null, name: file.name, size: file.size, file }));
      paintAttachments();
      picker.value = '';
    });
    document.getElementById('btnStream').addEventListener('click', (ev) => {
      ev.currentTarget.classList.toggle('on');
    });

    paintAttachments();
    loadConversations();
    if (S.conversationId) loadConversation(S.conversationId); else paint();

    function paintAttachments() {
      document.getElementById('attachRow').innerHTML = S.attachments.map((a, i) =>
        `<span class="attach-pill">${ico('attach')} ${esc(a.name)} <button data-drop="${i}">×</button></span>`).join('');
      document.querySelectorAll('[data-drop]').forEach((b) => b.addEventListener('click', () => {
        S.attachments.splice(Number(b.dataset.drop), 1); paintAttachments();
      }));
    }

    function paint() {
      if (!S.messages.length) {
        stream.innerHTML = `
          <div class="empty">
            <div class="ico">${ico('spark')}</div>
            <div><b>${t('chat.empty.title')}</b></div>
            <div class="muted small" style="margin-bottom:18px">${t('chat.empty.sub')}</div>
            <div class="chips" style="justify-content:center">
              ${CHAT_IDEAS[global.I18N.lang].map((idea, i) => `<button class="chip" data-idea="${i}">${esc(idea.slice(0, 58))}…</button>`).join('')}
            </div>
          </div>`;
        stream.querySelectorAll('[data-idea]').forEach((btn) => btn.addEventListener('click', () => {
          input.value = CHAT_IDEAS[global.I18N.lang][Number(btn.dataset.idea)];
          input.dispatchEvent(new Event('input'));
          input.focus();
        }));
        return;
      }
      stream.innerHTML = S.messages.map(messageHtml).join('');
      API.hydrateImages(stream);
      stream.scrollTop = stream.scrollHeight;
    }

    function messageHtml(message) {
      const mine = message.role === 'user';
      const engine = message.meta && message.meta.engine;
      return `<div class="msg ${mine ? 'user' : 'ai'}">
        <div class="av">${mine ? (API.user.display_name || 'U').slice(0, 1).toUpperCase() : 'M'}</div>
        <div class="body">
          <div class="who">${mine ? t('chat.you') : t('chat.ai')}
            ${engine ? `<span class="eng">${esc(engine)}</span>` : ''}
            <span class="muted small" style="font-weight:400">${relative(message.created_at)}</span></div>
          <div class="bubble">${mine ? `<p>${esc(message.content).replace(/\n/g, '<br>')}</p>` : md(message.content) + actionCards((message.meta && message.meta.actions) || [])}</div>
          ${(message.attachments || []).length ? `<div class="chips" style="margin-top:8px">${message.attachments.map((a) => `<span class="tag">${ico('attach')} ${esc(a.name)}</span>`).join('')}</div>` : ''}
        </div></div>`;
    }

    async function loadConversations() {
      const box = document.getElementById('convItems');
      try {
        const data = await API.chat.conversations();
        S.conversations = data.conversations || [];
        S.modes = data.modes || [];
        if (!S.conversations.length) {
          box.innerHTML = `<div class="muted small" style="padding:14px">${t('chess.noGames').replace('Партий', 'Диалогов')}</div>`;
          return;
        }
        box.innerHTML = S.conversations.map((c) => `
          <div class="conv-item ${c.id === S.conversationId ? 'active' : ''}" data-id="${c.id}">
            <div class="t">${esc(c.title || '—')}</div>
            <div class="m"><span>${c.message_count || 0} msg</span><span>${relative(c.updated_at)}</span></div>
          </div>`).join('');
        box.querySelectorAll('[data-id]').forEach((el) => el.addEventListener('click', () => loadConversation(el.dataset.id)));
      } catch (e) {
        box.innerHTML = `<div class="muted small" style="padding:14px">${esc(e.message)}</div>`;
      }
    }

    async function loadConversation(id) {
      S.conversationId = id;
      stream.innerHTML = spinner();
      try {
        const data = await API.chat.conversation(id);
        S.messages = data.messages || [];
        S.mode = data.conversation.mode || S.mode;
        paint();
        loadConversations();
      } catch (e) {
        stream.innerHTML = emptyState('alert', t('common.error'), e.message);
      }
    }

    async function send() {
      const content = input.value.trim();
      if (!content || S.streaming) return;

      // Upload attachments first so the model receives them as context.
      const uploaded = [];
      for (const item of S.attachments) {
        if (item.id) { uploaded.push(item); continue; }
        try {
          const res = await API.files.upload(item.file);
          uploaded.push({ id: res.id, name: res.name, size: res.size });
        } catch (e) {
          global.App.toast('err', t('common.error'), e.message);
        }
      }
      S.attachments = [];
      paintAttachments();

      S.messages.push({ role: 'user', content, created_at: new Date().toISOString(), attachments: uploaded });
      input.value = ''; input.style.height = 'auto';
      paint();

      const aiMsg = { role: 'assistant', content: '', created_at: new Date().toISOString(), meta: {}, streaming: true };
      S.messages.push(aiMsg);
      stream.insertAdjacentHTML('beforeend', messageHtml(aiMsg));
      const live = stream.lastElementChild.querySelector('.bubble');
      live.innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
      stream.scrollTop = stream.scrollHeight;

      S.streaming = true;
      sendBtn.disabled = true;
      const useStream = document.getElementById('btnStream').classList.contains('on');
      const controller = new AbortController();
      S.abort = controller;
      let buffer = '';
      const actions = [];
      const seenActions = new Set();
      const pushAction = (a) => {
        if (!a) return;
        const key = a.document_id || a.id
          || (a.type === 'terminal' ? `term:${a.command || ''}|${a.duration_ms || 0}` : '')
          || JSON.stringify(a).slice(0, 140);
        if (seenActions.has(key)) return;
        seenActions.add(key);
        actions.push(a);
      };

      try {
        await API.chat.send({
          content,
          conversation_id: S.conversationId,
          language: global.I18N.lang,
          stream: useStream,
          attachments: uploaded.map((u) => u.id).filter(Boolean),
        }, {
          signal: controller.signal,
          onDelta(piece, full) {
            buffer = full;
            live.innerHTML = md(full) + '<span class="caret" style="display:inline-block;width:7px;height:14px;background:var(--gold);vertical-align:middle;animation:caret 1s steps(2) infinite"></span>';
            stream.scrollTop = stream.scrollHeight;
          },
          onEvent(event) {
            if (event.event === 'meta') {
              S.conversationId = event.conversation_id;
              const label = document.getElementById('engineLabel');
              if (label && event.engine) label.textContent = event.engine;
              aiMsg.meta.engine = event.engine;
            } else if (event.event === 'done') {
              buffer = event.text || buffer;
              aiMsg.meta = Object.assign(aiMsg.meta, { engine: event.engine, tokens: event.tokens, latency: event.latency_ms });
              (event.actions || []).forEach((a) => pushAction(a.result));
            } else if (event.event === 'action' || event.event === 'action_start') {
              if (event.type) pushAction(event);
            } else if (event.event === 'error') {
              global.App.toast('err', t('common.error'), event.message || '');
            } else if (event.event === 'notice') {
              global.App.toast('warn', 'Guardrail', event.reason);
            }
          },
          onEnd() { /* handled in done */ },
        });
      } catch (e) {
        if (e.name !== 'AbortError') global.App.toast('err', t('common.error'), e.message);
        buffer = buffer || `_${e.message}_`;
      } finally {
        S.streaming = false;
        sendBtn.disabled = false;
        S.abort = null;
      }

      aiMsg.content = buffer;
      aiMsg.streaming = false;
      const node = stream.lastElementChild;
      node.querySelector('.bubble').innerHTML = md(buffer) + actionCards(actions);
      API.hydrateImages(node);
      const who = node.querySelector('.who');
      if (aiMsg.meta.tokens && aiMsg.meta.tokens.total) {
        who.insertAdjacentHTML('beforeend', `<span class="muted small" style="font-weight:400">${aiMsg.meta.tokens.total} ${t('chat.tokens')} · ${aiMsg.meta.latency || 0} ${t('chat.ms')}</span>`);
      }
      stream.scrollTop = stream.scrollHeight;

      node.querySelectorAll('[data-img]').forEach((img) => img.addEventListener('click', () => {
        const s = img.currentSrc || img.src || '';
        global.App.lightbox(s.indexOf('blob:') === 0 ? s : API.images.url(img.dataset.img));
      }));
      loadConversations();
    }
  }

  function actionCards(actions) {
    if (!actions || !actions.length) return '';
    return actions.map((a) => {
      if (!a) return '';
      if (a.type === 'terminal') {
        if (a.error === 'no_permission' || a.error === 'empty') {
          return `<div class="action-card"><div class="ic">${ico('terminal')}</div><div class="txt"><b>${t('chat.termOff')}</b><span>${esc(a.notice || '')}</span></div></div>`;
        }
        const out = (a.output || '').replace(/\s+$/, '');
        const okc = a.exit_code === 0;
        const inner = out ? esc(out) : esc(a.allowed ? t('chat.termEmpty') : (a.reason || a.notice || t('chat.termEmpty')));
        const bc = okc ? 'rgba(120,200,140,.35)' : 'rgba(220,120,120,.4)';
        const code = (a.exit_code === null || a.exit_code === undefined) ? '—' : a.exit_code;
        return `<div class="action-card term"><div class="ic">${ico('terminal')}</div><div class="txt" style="flex:1;min-width:0"><b>${t('chat.termRan')}: <code style="opacity:.85">${esc(a.command || '')}</code></b><pre style="margin:6px 0 4px;padding:8px 10px;background:rgba(0,0,0,.4);border:1px solid ${bc};border-radius:8px;font:12px/1.55 ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;word-break:break-word;max-height:260px;overflow:auto;color:#dcdcdc">${inner}</pre><span class="muted small">${t('chat.exit')}: ${code} · ${a.duration_ms || 0} ${t('chat.ms')}</span></div></div>`;
      }
      if (a.error) return '';
      if (a.type === 'document' || a.document_id) {
        return `<div class="action-card"><div class="ic">${ico('presentation')}</div><div class="txt"><b>${t('chat.docMade')}: ${esc(a.title || a.name || '')}</b>
          <span>${esc(a.name || '')} · ${fmtBytes(a.size)}</span></div>
          <button class="btn btn-sm btn-outline" data-doc="${esc(a.document_id)}">${t('chat.download')}</button></div>`;
      }
      if (a.type === 'image' || (a.file_id && a.url)) {
        const src = a.url || `/api/images/${a.id}/file`;
        return `<img class="chat-inline-img" data-authsrc="${esc(src)}" data-img="${esc(a.id)}" alt="" loading="lazy" style="display:block;max-width:min(460px,100%);width:auto;height:auto;border-radius:14px;border:1px solid rgba(255,255,255,.09);cursor:zoom-in;box-shadow:0 10px 30px rgba(0,0,0,.45);background:rgba(255,255,255,.04);margin:8px 0">`;
      }
      if (a.type === 'file' || a.id) {
        return `<div class="action-card"><div class="ic">${ico('document')}</div><div class="txt"><b>${t('chat.fileMade')}: ${esc(a.name || '')}</b>
          <span>${fmtBytes(a.size)}</span></div>
          <button class="btn btn-sm btn-outline" data-file="${esc(a.id)}">${t('chat.download')}</button></div>`;
      }
      return '';
    }).join('');
  }

  /* ===================================================================== */
  /* DIRECTORY                                                              */
  /* ===================================================================== */
  function renderDirectory(host) {
    const F = S.files;
    host.innerHTML = `
      <div class="view-head">
        <div><h1>${t('dir.title')}</h1><p>${t('dir.sub')}</p></div>
        <div class="actions">
          <button class="btn btn-ghost btn-sm" id="dirView">${F.view === 'grid' ? t('dir.list') : t('dir.grid')}</button>
          <button class="btn btn-ghost btn-sm" id="dirTrash">${t('dir.trash')}</button>
          <button class="btn btn-outline btn-sm" id="dirNewFolder">＋ ${t('dir.newFolder')}</button>
          <button class="btn btn-gold btn-sm" id="dirUpload">${ico('upload')} ${t('dir.upload')}</button>
          <input type="file" id="dirPicker" multiple hidden>
        </div>
      </div>
      <div class="files-layout">
        <aside class="tree" id="dirTree">${spinner()}</aside>
        <section>
          <div class="panel" style="padding:14px 16px;margin-bottom:16px">
            <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
              <input class="inp" id="dirSearch" placeholder="${esc(t('dir.search'))}" style="flex:1;min-width:200px">
              <div class="chips" id="dirKinds"></div>
              <span class="muted small" id="dirStats"></span>
            </div>
          </div>
          <div class="dropzone" id="dropzone" style="margin-bottom:16px" hidden>${t('dir.drop')}</div>
          <div id="dirContent">${spinner()}</div>
        </section>
      </div>`;

    const content = document.getElementById('dirContent');
    const tree = document.getElementById('dirTree');
    const picker = document.getElementById('dirPicker');
    const search = document.getElementById('dirSearch');
    let kindFilter = '';

    document.getElementById('dirUpload').addEventListener('click', () => picker.click());
    picker.addEventListener('change', async () => {
      for (const file of [...picker.files]) {
        try {
          await API.files.upload(file, F.folderId);
          global.App.toast('ok', t('dir.upload'), file.name);
        } catch (e) { global.App.toast('err', file.name, e.message); }
      }
      picker.value = ''; load();
    });
    document.getElementById('dirView').addEventListener('click', (ev) => {
      F.view = F.view === 'grid' ? 'list' : 'grid';
      ev.currentTarget.textContent = F.view === 'grid' ? t('dir.list') : t('dir.grid');
      paintFiles();
    });
    document.getElementById('dirTrash').addEventListener('click', () => { F.trashed = !F.trashed; load(); });
    document.getElementById('dirNewFolder').addEventListener('click', async () => {
      const name = prompt(global.I18N.lang === 'ru' ? 'Имя папки' : 'Folder name');
      if (!name) return;
      try { await API.files.createFolder({ name, parent_id: F.folderId }); load(); }
      catch (e) { global.App.toast('err', t('common.error'), e.message); }
    });
    search.addEventListener('input', debounce(() => { F.search = search.value.trim(); load(); }, 320));

    const drop = document.getElementById('dropzone');
    ['dragenter', 'dragover'].forEach((ev) => document.addEventListener(ev, (e) => {
      if (!e.dataTransfer || ![...e.dataTransfer.types].includes('Files')) return;
      e.preventDefault(); drop.hidden = false; drop.classList.add('over');
    }));
    ['dragleave', 'drop'].forEach((ev) => document.addEventListener(ev, (e) => {
      if (ev === 'drop' && e.dataTransfer && e.dataTransfer.files.length) {
        e.preventDefault();
        [...e.dataTransfer.files].forEach(async (file) => {
          try { await API.files.upload(file, F.folderId); global.App.toast('ok', t('dir.upload'), file.name); }
          catch (err) { global.App.toast('err', file.name, err.message); }
        });
        load();
      }
      drop.classList.remove('over');
      setTimeout(() => { drop.hidden = true; }, 300);
    }));

    async function load() {
      try {
        const params = { trashed: String(F.trashed), limit: '300' };
        if (F.folderId) params.folder_id = F.folderId;
        if (kindFilter) params.kind = kindFilter;
        if (F.search) params.search = F.search;
        const data = await API.files.list(params);
        F.folders = F.trashed ? [] : (data.folders || []);
        F.files = data.files || [];
        const stats = await API.files.stats();
        document.getElementById('dirStats').textContent =
          `${stats.files} ${t('dir.files')} · ${fmtBytes(stats.total_bytes)}`;
        paintTree();
        paintKinds(stats.by_kind || []);
        paintFiles();
      } catch (e) {
        content.innerHTML = emptyState('alert', t('common.error'), e.message);
      }
    }

    function paintTree() {
      tree.innerHTML = `
        <div class="tree-item ${!F.folderId ? 'active' : ''}" data-folder="">
          <span>${ico('home')}</span><span>${t('dir.all')}</span><span class="cnt">${F.files.length}</span></div>
        ${F.folders.map((f) => `
          <div class="tree-item ${F.folderId === f.id ? 'active' : ''}" data-folder="${f.id}">
            <span>${ico('folder')}</span><span>${esc(f.name)}</span></div>`).join('')}
        <div class="divider"></div>
        <div class="tree-item ${F.trashed ? 'active' : ''}" data-trash="1"><span>${ico('trash')}</span><span>${t('dir.trash')}</span></div>`;
      tree.querySelectorAll('[data-folder]').forEach((el) => el.addEventListener('click', () => {
        F.folderId = el.dataset.folder || null; F.trashed = false; load();
      }));
      tree.querySelector('[data-trash]').addEventListener('click', () => { F.trashed = true; load(); });
    }

    function paintKinds(byKind) {
      const box = document.getElementById('dirKinds');
      box.innerHTML = byKind.slice(0, 6).map((k) =>
        `<button class="chip ${kindFilter === k.kind ? 'active' : ''}" data-kind="${k.kind}">${iconFor(k.kind)} ${esc(k.kind)} · ${k.count}</button>`).join('');
      box.querySelectorAll('[data-kind]').forEach((btn) => btn.addEventListener('click', () => {
        kindFilter = kindFilter === btn.dataset.kind ? '' : btn.dataset.kind;
        load();
      }));
    }

    function paintFiles() {
      if (!F.files.length && !F.folders.length) {
        content.innerHTML = emptyState('folder', t('dir.empty'), F.trashed ? t('dir.trash') : '');
        return;
      }
      if (F.view === 'grid') {
        content.innerHTML = `<div class="file-grid">${F.files.map(cardHtml).join('')}</div>`;
        API.hydrateImages(content);
      } else {
        content.innerHTML = `<div class="panel" style="padding:0;overflow:hidden">
          <table class="file-table"><thead><tr>
            <th>${t('dir.name')}</th><th>${t('dir.kind')}</th><th>${t('dir.size')}</th><th>${t('dir.modified')}</th><th></th>
          </tr></thead><tbody>${F.files.map(rowHtml).join('')}</tbody></table></div>`;
      }
      wireFileActions(content);
    }

    function cardHtml(file) {
      const isImage = file.kind === 'image';
      return `<div class="file-card" data-kind="${esc(file.kind)}" data-id="${file.id}">
        ${isImage ? `<img class="thumb" loading="lazy" data-authsrc="/api/files/${file.id}/download?inline=true" alt="">` : ''}
        ${file.pinned ? `<div class="pin">${ico('star')}</div>` : ''}
        <div class="fico" ${isImage ? 'style="position:relative;z-index:2;background:rgba(10,11,16,.72)"' : ''}>${iconFor(file.kind)}</div>
        <div class="fname" ${isImage ? 'style="position:relative;z-index:2"' : ''} title="${esc(file.name)}">${esc(file.name)}</div>
        <div class="fmeta" ${isImage ? 'style="position:relative;z-index:2"' : ''}>${fmtBytes(file.size)} · ${relative(file.updated_at)}</div>
        <div class="acts">
          <button class="mini-btn" data-act="download" title="${t('dir.download')}">${ico('download')}</button>
          <button class="mini-btn" data-act="pin" title="${t('dir.pin')}">${ico('star')}</button>
          <button class="mini-btn" data-act="rename" title="${t('dir.rename')}">${ico('edit')}</button>
          <button class="mini-btn" data-act="delete" title="${t('dir.delete')}">${ico('trash')}</button>
        </div></div>`;
    }

    function rowHtml(file) {
      return `<tr data-id="${file.id}">
        <td>${iconFor(file.kind)} ${esc(file.name)} ${file.pinned ? ico('star') : ''}</td>
        <td class="muted">${esc(file.kind)}</td>
        <td class="muted">${fmtBytes(file.size)}</td>
        <td class="muted">${fmtDate(file.updated_at)}</td>
        <td style="text-align:right">
          <button class="mini-btn" data-act="download" title="${t('dir.download')}">${ico('download')}</button>
          <button class="mini-btn" data-act="rename" title="${t('dir.rename')}">${ico('edit')}</button>
          <button class="mini-btn" data-act="delete" title="${t('dir.delete')}">${ico('trash')}</button></td></tr>`;
    }

    function wireFileActions(scope) {
      scope.querySelectorAll('[data-id]').forEach((el) => {
        const id = el.dataset.id;
        el.addEventListener('click', (ev) => {
          const act = ev.target.dataset && ev.target.dataset.act;
          if (!act) {
            if (ev.currentTarget.classList.contains('file-card') && el.dataset.kind === 'image') {
              global.App.lightbox(`/api/files/${id}/download?inline=true`);
            } else previewFile(id);
            return;
          }
          ev.stopPropagation();
          handle(act, id);
        });
      });
    }

    async function handle(act, id) {
      const file = F.files.find((f) => f.id === id);
      try {
        if (act === 'download') { global.location.href = API.files.url(id); return; }
        if (act === 'pin') { await API.files.pin(id, !(file && file.pinned)); load(); return; }
        if (act === 'rename') {
          const name = prompt(global.I18N.lang === 'ru' ? 'Новое имя' : 'New name', file ? file.name : '');
          if (!name) return;
          await API.files.rename(id, name); load(); return;
        }
        if (act === 'delete') {
          if (!confirm(global.I18N.lang === 'ru' ? 'Удалить файл безвозвратно?' : 'Delete permanently?')) return;
          await API.files.remove(id); load(); return;
        }
      } catch (e) { global.App.toast('err', t('common.error'), e.message); }
    }

    async function previewFile(id) {
      try {
        const meta = await API.files.get(id);
        const url = `/api/files/${id}/download?inline=true`;
        let body = '';
        if (meta.kind === 'image') body = `<img data-authsrc="${url}" style="border-radius:14px;width:100%">`;
        else if (meta.mime && meta.mime.startsWith('text/') || ['md', 'code', 'document'].includes(meta.kind)) {
          const res = await fetch(url, { headers: API.headers() });
          const text = await res.text();
          body = `<pre style="max-height:52vh;overflow:auto;background:#080a10;padding:16px;border-radius:12px;border:1px solid var(--line)"><code>${esc(text.slice(0, 60000))}</code></pre>`;
        } else if (meta.mime === 'application/pdf') body = `<iframe id="pvPdf" style="width:100%;height:60vh;border:none;border-radius:12px"></iframe>`;
        else body = `<div class="empty"><div class="ico">${iconFor(meta.kind)}</div>${esc(meta.name)}<div class="muted small">${fmtBytes(meta.size)} · ${esc(meta.mime || '')}</div></div>`;
        global.App.openDrawer(`
          <h2>${esc(meta.name)}</h2>
          <dl class="kv">
            <dt>${t('dir.kind')}</dt><dd>${esc(meta.kind)}</dd>
            <dt>${t('dir.size')}</dt><dd>${fmtBytes(meta.size)}</dd>
            <dt>Path</dt><dd>${esc(meta.path)}</dd>
            <dt>${t('dir.modified')}</dt><dd>${fmtDate(meta.updated_at)}</dd>
            <dt>SHA-256</dt><dd>${esc((meta.checksum || '').slice(0, 24))}…</dd>
          </dl>
          <div class="divider"></div>${body}
          <div style="display:flex;gap:9px;margin-top:18px">
            <a class="btn btn-gold btn-sm" href="#" data-dl="${API.files.url(id)}" data-name="${esc(meta.name)}">${t('common.download')}</a>
            <button class="btn btn-outline btn-sm" id="drPin">${t('dir.pin')}</button>
            <button class="btn btn-danger btn-sm" id="drDel">${t('dir.delete')}</button>
          </div>`);
        API.hydrateImages(document.getElementById('drawerBody'));
        const pvPdf = document.getElementById('pvPdf');
        if (pvPdf) API.blobUrl(url).then((u) => { pvPdf.src = u; }).catch(() => {});
        const pin = document.getElementById('drPin');
        if (pin) pin.addEventListener('click', async () => { await API.files.pin(id, !meta.pinned); global.App.closeDrawer(); load(); });
        const del = document.getElementById('drDel');
        if (del) del.addEventListener('click', async () => { await API.files.remove(id); global.App.closeDrawer(); load(); });
      } catch (e) { global.App.toast('err', t('common.error'), e.message); }
    }

    load();
  }

  /* ===================================================================== */
  /* TERMINAL                                                               */
  /* ===================================================================== */
  const QUICK = [
    { cmd: 'pwd', ru: 'Текущая директория', en: 'Current directory' },
    { cmd: 'ls -la', ru: 'Список файлов', en: 'List files' },
    { cmd: 'uname -a && nproc && free -m', ru: 'Сведения о среде', en: 'Environment info' },
    { cmd: 'python3 -c "print(\'Mestinghem AI\')"', ru: 'Запустить Python', en: 'Run Python' },
    { cmd: 'du -sh * 2>/dev/null | sort -h', ru: 'Размеры файлов', en: 'File sizes' },
    { cmd: 'find . -maxdepth 2 -type f | head -30', ru: 'Найти файлы', en: 'Find files' },
    { cmd: 'ps aux --sort=-%mem | head -10', ru: 'Процессы по памяти', en: 'Top processes' },
    { cmd: 'df -h .', ru: 'Свободное место', en: 'Disk space' },
  ];

  function renderTerminal(host) {
    const perms = (API.user && API.user.permissions) || {};
    if (!perms.terminal && (API.user.role !== 'owner' && API.user.role !== 'admin')) {
      host.innerHTML = `<div class="view-head"><div><h1>${t('term.title')}</h1><p>${t('term.sub')}</p></div></div>
        <div class="panel" style="max-width:620px">
          ${emptyState('terminal', t('term.off'), '')}
          <button class="btn btn-gold" id="termEnable">${t('term.enabled')}</button>
        </div>`;
      document.getElementById('termEnable').addEventListener('click', async () => {
        try {
          await API.auth.profile({ permissions: { terminal: true } });
          global.App.toast('ok', t('term.enabled'), '');
          global.App.reloadUser();
          renderTerminal(host);
        } catch (e) { global.App.toast('err', t('common.error'), e.message); }
      });
      return;
    }

    host.innerHTML = `
      <div class="view-head">
        <div><h1>${t('term.title')}</h1><p>${t('term.sub')}</p></div>
        <div class="actions">
          <button class="btn btn-ghost btn-sm" id="termPolicy">${ico('shield')} ${t('term.policy')}</button>
          <button class="btn btn-ghost btn-sm" id="termHistory">${ico('refresh')} ${t('term.history')}</button>
          <button class="btn btn-outline btn-sm" id="termNew">＋ ${t('term.new')}</button>
        </div>
      </div>
      <div class="term-wrap">
        <section class="term-panel">
          <div class="term-head">
            <span class="gc-dot r"></span><span class="gc-dot y"></span><span class="gc-dot g"></span>
            <span class="cwd" id="termCwd">connecting…</span>
            <span class="spacer" style="margin-left:auto"></span>
            <span class="state online" id="termState">offline</span>
          </div>
          <div class="term-body" id="termBody"></div>
          <div class="term-input-row">
            <span class="ps1">mestinghem:~$</span>
            <input id="termInput" autocomplete="off" spellcheck="false" placeholder="${esc(t('term.placeholder'))}">
            <button class="btn btn-gold btn-sm" id="termRun">${t('term.run')}</button>
          </div>
        </section>
        <aside class="term-side">
          <div class="panel">
            <div class="panel-head"><h3>${t('term.quick')}</h3></div>
            ${QUICK.map((q, i) => `<button class="quick-cmd" data-q="${i}"><code>${esc(q.cmd)}</code></button>`).join('')}
          </div>
          <div class="panel" id="termPolicyBox"></div>
        </aside>
      </div>`;

    const body = document.getElementById('termBody');
    const input = document.getElementById('termInput');
    const stateEl = document.getElementById('termState');
    const cwdEl = document.getElementById('termCwd');
    const T = S.terminal;
    const localHistory = [];
    let historyIndex = -1;
    let socket = null;
    let useWs = true;

    function write(text, cls) {
      const span = document.createElement('div');
      if (cls) span.className = cls;
      span.textContent = text;
      body.appendChild(span);
      body.scrollTop = body.scrollHeight;
    }
    function writeRaw(html) {
      body.insertAdjacentHTML('beforeend', html);
      body.scrollTop = body.scrollHeight;
    }

    async function connect() {
      stateEl.textContent = 'connecting';
      stateEl.className = 'state pending';
      try {
        const session = await API.terminal.open({});
        T.session = session.id;
        cwdEl.textContent = session.cwd;
        stateEl.textContent = 'live';
        stateEl.className = 'state online';
        write(session.banner || '', 'dim');
        write(`sandbox · ${session.id}`, 'dim');
        write('', 'dim');
        if (useWs) openSocket(session.id);
      } catch (e) {
        stateEl.textContent = 'error';
        stateEl.className = 'state revoked';
        write(e.message, 'err');
        useWs = false;
      }
    }

    function openSocket(sessionId) {
      try {
        socket = API.ws('/api/terminal/ws', { session_id: sessionId }, {
          onJson(event) {
            if (event.event === 'error') { write(event.message, 'err'); socket.close(); useWs = false; }
            if (event.event === 'blocked') write(`${ico('x')} ${event.reason}`, 'blocked');
          },
          onBinary(bytes) {
            const text = new TextDecoder('utf-8', { fatal: false }).decode(bytes)
              .replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, '').replace(/\r/g, '');
            if (text) { body.insertAdjacentHTML('beforeend', esc(text)); body.scrollTop = body.scrollHeight; }
          },
          onText(text) {
            const clean = text.replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, '').replace(/\r/g, '');
            if (clean) { body.insertAdjacentHTML('beforeend', esc(clean)); body.scrollTop = body.scrollHeight; }
          },
          onClose() { stateEl.textContent = 'closed'; stateEl.className = 'state offline'; },
        });
        socket.addEventListener('open', () => {
          stateEl.textContent = 'pty';
          stateEl.className = 'state online';
        });
      } catch (e) {
        useWs = false;
      }
    }

    async function run(command) {
      if (!command.trim()) return;
      localHistory.unshift(command); historyIndex = -1;
      if (socket && socket.readyState === 1 && useWs) {
        socket.send(JSON.stringify({ type: 'command', data: command }));
        return;
      }
      writeRaw(`<div><span class="cmd">mestinghem:~$ ${esc(command)}</span></div>`);
      try {
        const res = await API.terminal.run({ command, session_id: T.session, timeout: 60 });
        if (res.output) writeRaw(`<div>${esc(cleanAnsi(res.output))}</div>`);
        writeRaw(`<div class="${res.exit_code ? 'err' : 'ok'}">↳ exit ${res.exit_code ?? '—'} · ${res.duration_ms} ms${res.timed_out ? ' · timeout' : ''}</div>`);
      } catch (e) {
        writeRaw(`<div class="blocked">${ico('x')} ${esc(e.message)}</div>`);
      }
    }

    function cleanAnsi(text) {
      return String(text).replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, '').replace(/\x1b\][^\u0007]*\u0007/g, '').replace(/\r/g, '');
    }

    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { run(input.value); input.value = ''; }
      else if (ev.key === 'ArrowUp') {
        ev.preventDefault();
        if (localHistory.length) { historyIndex = Math.min(historyIndex + 1, localHistory.length - 1); input.value = localHistory[historyIndex]; }
      } else if (ev.key === 'ArrowDown') {
        ev.preventDefault();
        historyIndex = Math.max(historyIndex - 1, -1);
        input.value = historyIndex === -1 ? '' : localHistory[historyIndex];
      } else if (ev.key === 'l' && ev.ctrlKey) { ev.preventDefault(); body.innerHTML = ''; }
    });
    document.getElementById('termRun').addEventListener('click', () => { run(input.value); input.value = ''; });
    host.querySelectorAll('[data-q]').forEach((btn) => btn.addEventListener('click', () => {
      input.value = QUICK[Number(btn.dataset.q)].cmd; input.focus();
    }));
    document.getElementById('termNew').addEventListener('click', async () => {
      if (socket) { try { socket.close(); } catch (e) {} }
      body.innerHTML = ''; await connect();
    });
    document.getElementById('termPolicy').addEventListener('click', async () => {
      const policy = await API.terminal.policy();
      global.App.openDrawer(`
        <h2>${t('term.policy')}</h2>
        <dl class="kv">
          <dt>${t('term.sandbox')}</dt><dd>${esc(policy.root)}</dd>
          <dt>${t('term.timeout')}</dt><dd>${policy.timeout_seconds}s</dd>
          <dt>network</dt><dd>${policy.permissions.network_tools ? ico('check') : ico('x')}</dd>
          <dt>packages</dt><dd>${policy.permissions.package_managers ? ico('check') : ico('x')}</dd>
          <dt>sudo</dt><dd>${policy.permissions.privilege_escalation ? ico('check') : ico('x') + ' (always)'}</dd>
        </dl>
        <div class="divider"></div>
        <h3 style="font-size:15px;margin-bottom:10px">${global.I18N.lang === 'ru' ? 'Блокируется всегда' : 'Always blocked'}</h3>
        <ul style="padding-left:20px;color:var(--muted);font-size:13.5px">${policy.blocked_categories.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>`);
    });
    document.getElementById('termHistory').addEventListener('click', async () => {
      const data = await API.terminal.history(60);
      global.App.openDrawer(`
        <h2>${t('term.history')}</h2>
        ${data.commands.length ? data.commands.map((c) => `
          <div class="log-row">
            <span class="tm">${fmtDate(c.created_at)}</span>
            <span class="ac" style="flex:1"><code>${esc(c.command.slice(0, 120))}</code></span>
            <span class="${c.blocked ? 'risk high' : (c.exit_code ? 'risk medium' : 'risk low')}">${c.blocked ? 'blocked' : `exit ${c.exit_code ?? '—'}`}</span>
          </div>`).join('') : emptyState('refresh', global.I18N.lang === 'ru' ? 'История пуста' : 'History is empty', '')}`);
    });

    (async () => {
      try {
        const policy = await API.terminal.policy();
        document.getElementById('termPolicyBox').innerHTML = `
          <div class="panel-head"><h3>${ico('shield')} ${t('term.policy')}</h3></div>
          <div class="muted small">${esc(policy.root)}</div>
          <div class="chips" style="margin-top:10px">
            <span class="tag">timeout ${policy.timeout_seconds}s</span>
            <span class="tag">net ${policy.permissions.network_tools ? 'on' : 'off'}</span>
            <span class="tag">pkg ${policy.permissions.package_managers ? 'on' : 'off'}</span>
            <span class="tag">sudo off</span>
          </div>`;
      } catch (e) { /* terminal disabled */ }
    })();

    connect();
    global.addEventListener('beforeunload', () => { if (socket) socket.close(); });
  }

  /* ===================================================================== */
  /* DOCUMENTS                                                              */
  /* ===================================================================== */
  function renderDocuments(host) {
    host.innerHTML = `
      <div class="view-head">
        <div><h1>${t('docs.title')}</h1><p>${t('docs.sub')}</p></div>
      </div>
      <div class="doc-builder">
        <section class="panel">
          <div class="form-grid">
            <div>
              <div class="muted small" style="margin-bottom:7px">${t('docs.kind')}</div>
              <div class="seg" id="docKinds"></div>
            </div>
            <label class="field"><span>${t('docs.brief')}</span>
              <textarea id="docBrief" class="inp" rows="8" placeholder="${esc(t('docs.briefPh'))}" style="width:100%;resize:vertical"></textarea></label>
            <div class="grid-2">
              <label class="field"><span>${t('docs.size')}</span>
                <select id="docSize" class="inp">
                  <option value="short">${t('docs.short')}</option>
                  <option value="medium" selected>${t('docs.medium')}</option>
                  <option value="long">${t('docs.long')}</option>
                </select></label>
              <label class="field"><span>${t('docs.audience')}</span>
                <input id="docAudience" class="inp" placeholder="CEO, инвесторы…"></label>
            </div>
            <label class="field"><span>${t('docs.tone')}</span>
              <input id="docTone" class="inp" placeholder="${global.I18N.lang === 'ru' ? 'деловой, уверенный, лаконичный' : 'business, confident, concise'}"></label>
            <label class="field"><span>${t('docs.title')}</span>
              <input id="docTitle" class="inp" placeholder="${global.I18N.lang === 'ru' ? 'Необязательно' : 'Optional'}"></label>
            <button class="btn btn-gold btn-block" id="docBuild">${ico('spark')} ${t('docs.build')}</button>
            <div class="progress" id="docProgress" hidden><span></span></div>
            <div class="muted small" id="docHint"></div>
          </div>
        </section>
        <section>
          <div class="panel-head"><h3>${t('docs.mine')}</h3>
            <span class="spacer" style="margin-left:auto"></span>
            <button class="mini-btn" id="docRefresh">⟳</button></div>
          <div id="docList">${spinner()}</div>
        </section>
      </div>`;

    const KINDS = [
      { id: 'pptx', label: 'PPTX' }, { id: 'xlsx', label: 'XLSX' }, { id: 'docx', label: 'DOCX' },
      { id: 'md', label: 'MD' }, { id: 'html', label: 'HTML' },
    ];
    const kindsBox = document.getElementById('docKinds');
    kindsBox.innerHTML = KINDS.map((k) =>
      `<button class="${k.id === S.docs.kind ? 'active' : ''}" data-kind="${k.id}">${k.label}</button>`).join('');
    kindsBox.querySelectorAll('[data-kind]').forEach((btn) => btn.addEventListener('click', () => {
      S.docs.kind = btn.dataset.kind;
      kindsBox.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b === btn));
    }));

    document.getElementById('docRefresh').addEventListener('click', loadDocs);
    document.getElementById('docBuild').addEventListener('click', build);

    async function build() {
      const brief = document.getElementById('docBrief').value.trim();
      if (brief.length < 10) {
        global.App.toast('warn', t('docs.brief'), global.I18N.lang === 'ru' ? 'Опишите задачу подробнее' : 'Describe the task in more detail');
        return;
      }
      const button = document.getElementById('docBuild');
      const progress = document.getElementById('docProgress');
      const bar = progress.firstElementChild;
      button.classList.add('loading');
      button.disabled = true;
      progress.hidden = false;
      bar.style.width = '18%';
      const hint = document.getElementById('docHint');
      const steps = global.I18N.lang === 'ru'
        ? ['Собираю структуру…', 'Готовлю содержание…', 'Верстаю в фирменном стиле…', 'Сохраняю в директорию…']
        : ['Building the structure…', 'Preparing content…', 'Laying out in house style…', 'Saving to your directory…'];
      let step = 0;
      hint.textContent = steps[0];
      const timer = setInterval(() => {
        step = Math.min(step + 1, steps.length - 1);
        hint.textContent = steps[step];
        bar.style.width = `${Math.min(88, 18 + step * 22)}%`;
      }, 2200);

      try {
        const result = await API.documents.build({
          kind: S.docs.kind, brief,
          title: document.getElementById('docTitle').value.trim() || null,
          language: global.I18N.lang,
          size: document.getElementById('docSize').value,
          audience: document.getElementById('docAudience').value.trim(),
          tone: document.getElementById('docTone').value.trim(),
        });
        bar.style.width = '100%';
        global.App.toast('ok', t('docs.ready'), `${result.name} · ${fmtBytes(result.size)}`);
        global.App.openDrawer(docDrawerHtml(result));
        wireDocDrawer(result);
        loadDocs();
      } catch (e) {
        global.App.toast('err', t('common.error'), e.message);
      } finally {
        clearInterval(timer);
        button.classList.remove('loading');
        button.disabled = false;
        setTimeout(() => { progress.hidden = true; bar.style.width = '0'; hint.textContent = ''; }, 900);
      }
    }

    function docDrawerHtml(doc) {
      const stats = doc.stats || {};
      const bits = Object.entries(stats).map(([k, v]) => `<span class="tag">${esc(k)}: ${esc(v)}</span>`).join('');
      return `<h2>${esc(doc.title || doc.name)}</h2>
        <div class="chips" style="margin-bottom:14px"><span class="tag">${esc(doc.kind.toUpperCase())}</span>${bits}</div>
        <iframe src="/api/documents/${doc.document_id || doc.id}/preview" style="width:100%;height:56vh;border:1px solid var(--line);border-radius:14px;background:#fff"></iframe>
        <div style="display:flex;gap:9px;margin-top:16px">
          <a class="btn btn-gold btn-sm" href="#" data-dl="/api/documents/${doc.document_id || doc.id}/download" data-name="${esc(doc.title || 'document')}">${t('common.download')}</a>
          <button class="btn btn-outline btn-sm" data-rebuild>${t('docs.rebuild')}</button>
        </div>`;
    }

    function wireDocDrawer(doc) {
      const btn = document.querySelector('[data-rebuild]');
      if (btn) btn.addEventListener('click', async () => {
        btn.classList.add('loading');
        try {
          await API.documents.rebuild(doc.document_id || doc.id, null);
          global.App.toast('ok', t('docs.ready'), '');
          global.App.closeDrawer();
          loadDocs();
        } catch (e) { global.App.toast('err', t('common.error'), e.message); }
        finally { btn.classList.remove('loading'); }
      });
    }

    async function loadDocs() {
      const box = document.getElementById('docList');
      try {
        const data = await API.documents.list();
        S.docs.list = data.documents || [];
        if (!S.docs.list.length) { box.innerHTML = emptyState('presentation', t('docs.none'), ''); return; }
        box.innerHTML = `<div class="file-grid">${S.docs.list.map((doc) => `
          <div class="file-card" data-kind="${doc.kind === 'pptx' ? 'presentation' : doc.kind === 'xlsx' ? 'spreadsheet' : 'document'}" data-doc="${doc.id}">
            <div class="fico">${doc.kind === 'pptx' ? ico('presentation') : doc.kind === 'xlsx' ? ico('spreadsheet') : ico('document')}</div>
            <div class="fname" title="${esc(doc.title)}">${esc(doc.title || doc.filename)}</div>
            <div class="fmeta">${esc(doc.kind.toUpperCase())} · ${fmtBytes(doc.size)} · ${relative(doc.created_at)}</div>
          </div>`).join('')}</div>`;
        box.querySelectorAll('[data-doc]').forEach((el) => el.addEventListener('click', async () => {
          const doc = await API.documents.get(el.dataset.doc);
          global.App.openDrawer(docDrawerHtml(doc));
          wireDocDrawer(doc);
        }));
      } catch (e) {
        box.innerHTML = emptyState('alert', t('common.error'), e.message);
      }
    }

    loadDocs();
  }

  /* ===================================================================== */
  /* IMAGES                                                                 */
  /* ===================================================================== */
  const RATIOS = [
    { id: '1024x1024', label: '1:1' }, { id: '1280x720', label: '16:9' },
    { id: '720x1280', label: '9:16' }, { id: '1152x864', label: '4:3' },
    { id: '864x1152', label: '3:4' },
  ];

  function renderImages(host) {
    host.innerHTML = `
      <div class="view-head">
        <div><h1>${t('img.title')}</h1><p>${t('img.sub')}</p></div>
      </div>
      <div class="doc-builder">
        <section class="panel">
          <div class="form-grid">
            <label class="field"><span>${t('img.prompt')}</span>
              <textarea id="imgPrompt" class="inp" rows="5" placeholder="${esc(t('img.promptPh'))}" style="width:100%;resize:vertical"></textarea></label>
            <label class="field"><span>${t('img.negative')}</span>
              <input id="imgNegative" class="inp" placeholder="${global.I18N.lang === 'ru' ? 'текст, watermark, размытость…' : 'text, watermark, blur…'}"></label>
            <div><div class="muted small" style="margin-bottom:7px">${t('img.style')}</div>
              <div class="chips" id="imgStyles">${spinner()}</div></div>
            <div><div class="muted small" style="margin-bottom:7px">${t('img.size')}</div>
              <div class="ratio-chips" id="imgRatios">
                ${RATIOS.map((r) => `<button class="chip ${r.id === '1024x1024' ? 'active' : ''}" data-ratio="${r.id}">${r.label}</button>`).join('')}
              </div></div>
            <div><div class="muted small" style="margin-bottom:7px">${t('img.count')}</div>
              <div class="chips" id="imgCounts">
                ${[1, 2, 3, 4].map((n) => `<button class="chip ${n === 1 ? 'active' : ''}" data-count="${n}">${n}</button>`).join('')}
              </div></div>
            <button class="btn btn-gold btn-block" id="imgGenerate">${ico('image')} ${t('img.generate')}</button>
            <div class="muted small" id="imgHint"></div>
          </div>
        </section>
        <section>
          <div class="panel-head"><h3>${t('img.gallery')}</h3>
            <span class="spacer" style="margin-left:auto"></span>
            <button class="mini-btn" id="imgRefresh">⟳</button></div>
          <div class="img-grid" id="imgGrid">${spinner()}</div>
        </section>
      </div>`;

    document.getElementById('imgRatios').querySelectorAll('[data-ratio]').forEach((btn) => btn.addEventListener('click', () => {
      document.querySelectorAll('[data-ratio]').forEach((b) => b.classList.toggle('active', b === btn));
      const [w, h] = btn.dataset.ratio.split('x').map(Number);
      S.images.width = w; S.images.height = h;
    }));
    document.getElementById('imgCounts').querySelectorAll('[data-count]').forEach((btn) => btn.addEventListener('click', () => {
      document.querySelectorAll('[data-count]').forEach((b) => b.classList.toggle('active', b === btn));
      S.images.count = Number(btn.dataset.count);
    }));
    document.getElementById('imgRefresh').addEventListener('click', loadGallery);
    document.getElementById('imgGenerate').addEventListener('click', generate);

    (async () => {
      const box = document.getElementById('imgStyles');
      try {
        const data = await API.images.styles();
        S.images.styles = data.styles || [];
        box.innerHTML = S.images.styles.map((s) =>
          `<button class="chip ${s.id === S.images.style ? 'active' : ''}" data-style="${s.id}">${esc(s.label)}</button>`).join('');
        box.querySelectorAll('[data-style]').forEach((btn) => btn.addEventListener('click', () => {
          S.images.style = btn.dataset.style;
          box.querySelectorAll('[data-style]').forEach((b) => b.classList.toggle('active', b === btn));
        }));
      } catch (e) { box.innerHTML = `<span class="muted small">${esc(e.message)}</span>`; }
    })();

    async function generate() {
      const prompt = document.getElementById('imgPrompt').value.trim();
      if (prompt.length < 3) { global.App.toast('warn', t('img.prompt'), ''); return; }
      const button = document.getElementById('imgGenerate');
      const grid = document.getElementById('imgGrid');
      const hint = document.getElementById('imgHint');
      button.classList.add('loading'); button.disabled = true;
      hint.textContent = t('img.working');
      const skeletons = Array.from({ length: S.images.count }, () => '<div class="img-card"><div class="img-skeleton"></div></div>').join('');
      grid.insertAdjacentHTML('afterbegin', skeletons);
      try {
        const data = await API.images.generate({
          prompt,
          negative: document.getElementById('imgNegative').value.trim(),
          style: S.images.style,
          width: S.images.width,
          height: S.images.height,
          variations: S.images.count,
        });
        grid.querySelectorAll('.img-skeleton').forEach((el) => el.parentElement.remove());
        (data.images || []).forEach((img) => grid.insertAdjacentHTML('afterbegin', imgCard(img)));
        API.hydrateImages(grid);
        wireGrid(grid);
        global.App.toast('ok', t('img.done'), `${(data.images || []).length} × ${S.images.width}×${S.images.height}`);
        if (data.errors && data.errors.length) global.App.toast('warn', 'partial', data.errors[0]);
      } catch (e) {
        grid.querySelectorAll('.img-skeleton').forEach((el) => el.parentElement.remove());
        global.App.toast('err', t('common.error'), e.message);
      } finally {
        button.classList.remove('loading'); button.disabled = false; hint.textContent = '';
      }
    }

    function imgCard(img) {
      return `<div class="img-card" data-img="${img.id}" data-file="${img.file_id}">
        <img loading="lazy" data-authsrc="${API.images.url(img.id)}" alt="">
        <div class="ov"><b>${esc((img.prompt || '').slice(0, 70))}</b><br>${img.width}×${img.height} · ${esc(img.style || '')}</div>
      </div>`;
    }

    function wireGrid(scope) {
      scope.querySelectorAll('[data-img]').forEach((el) => {
        el.onclick = (ev) => {
          if (ev.target.dataset && ev.target.dataset.del) return;
          const im = el.tagName === 'IMG' ? el : el.querySelector('img');
          const cur = im ? (im.currentSrc || im.src || '') : '';
          global.App.lightbox(cur.indexOf('blob:') === 0 ? cur : API.images.url(el.dataset.img));
        };
      });
    }

    async function loadGallery() {
      const grid = document.getElementById('imgGrid');
      try {
        const data = await API.images.list(48);
        S.images.list = data.images || [];
        if (!S.images.list.length) { grid.innerHTML = emptyState('image', t('img.empty'), ''); return; }
        grid.innerHTML = S.images.list.map(imgCard).join('');
        API.hydrateImages(grid);
        wireGrid(grid);
      } catch (e) { grid.innerHTML = emptyState('alert', t('common.error'), e.message); }
    }

    loadGallery();
  }

  /* ===================================================================== */
  /* WORKSTATION AGENT                                                      */
  /* ===================================================================== */
  function renderAgent(host) {
    const perms = (API.user && API.user.permissions) || {};
    if (!perms.agent && API.user.role !== 'owner' && API.user.role !== 'admin') {
      host.innerHTML = `<div class="view-head"><div><h1>${t('agent.title')}</h1><p>${t('agent.sub')}</p></div></div>
        <div class="panel" style="max-width:660px">
          ${emptyState('monitor', t('agent.off'), '')}
          <label class="check" style="margin:14px 0"><input type="checkbox" id="agentConsent"><span>${t('agent.consent')}</span></label>
          <button class="btn btn-gold" id="agentEnable" disabled>${t('agent.enable')}</button>
        </div>`;
      const consent = document.getElementById('agentConsent');
      const btn = document.getElementById('agentEnable');
      consent.addEventListener('change', () => { btn.disabled = !consent.checked; });
      btn.addEventListener('click', async () => {
        try {
          await API.auth.consent({ document: 'agent-policy', version: '1.0', granted: true, scope: 'device-control' });
          await API.auth.profile({ permissions: { agent: true } });
          global.App.reloadUser();
          global.App.toast('ok', t('agent.enable'), '');
          renderAgent(host);
        } catch (e) { global.App.toast('err', t('common.error'), e.message); }
      });
      return;
    }

    host.innerHTML = `
      <div class="view-head">
        <div><h1>${t('agent.title')}</h1><p>${t('agent.sub')}</p></div>
        <div class="actions">
          <button class="btn btn-outline btn-sm" id="agentCaps">${ico('shield')} ${t('agent.guarantees')}</button>
          <button class="btn btn-gold btn-sm" id="agentPair">＋ ${t('agent.pair')}</button>
        </div>
      </div>
      <div class="grid-3" id="agentStats"></div>
      <div class="grid-2" style="margin-top:18px;align-items:start">
        <section class="panel">
          <div class="panel-head"><h3>${t('agent.devices')}</h3><span class="spacer" style="margin-left:auto"></span>
            <button class="mini-btn" id="agentRefresh">⟳</button></div>
          <div id="agentDevices">${spinner()}</div>
        </section>
        <section class="panel">
          <div class="panel-head"><h3>${t('agent.approvals')}</h3>
            <span class="spacer" style="margin-left:auto"></span>
            <button class="mini-btn" id="agentAsk">${ico('spark')} ${t('agent.ask')}</button></div>
          <div id="agentApprovals">${spinner()}</div>
          <div class="divider"></div>
          <div class="panel-head"><h3>${t('agent.history')}</h3></div>
          <div id="agentHistory">${spinner()}</div>
        </section>
      </div>
      <section class="panel" style="margin-top:18px">
        <div class="panel-head"><h3>${t('agent.download')}</h3></div>
        <p class="muted small" style="max-width:760px">${global.I18N.lang === 'ru'
          ? 'Агент — это небольшая программа, которая работает на вашем компьютере, слушает подписанные команды и выполняет только те действия, которые разрешены выбранным профилем и доверенными папками. В архиве — готовый к запуску агент на Python (Windows, macOS, Linux, без зависимостей), инструкция по сопряжению и пример конфига. Нативная сборка .exe/.app будет доступна в следующем релизе.'
          : 'The agent is a small program that runs on your computer, listens for signed commands and performs only the actions permitted by the chosen profile and trusted folders. The archive contains a ready-to-run Python agent (Windows, macOS, Linux, no dependencies), pairing instructions and a config template. A native .exe/.app build arrives in the next release.'}</p>
        <div style="display:flex;gap:9px;flex-wrap:wrap">
          <a class="btn btn-outline btn-sm" href="#" data-dl="/api/agent/download" data-name="mestinghem-agent.zip">${ico('download')} mestinghem-agent.zip</a>
          <a class="btn btn-ghost btn-sm" href="#/legal/agent">${t('footer.agent')}</a>
        </div>
      </section>`;

    document.getElementById('agentRefresh').addEventListener('click', loadAll);
    document.getElementById('agentPair').addEventListener('click', openPairing);
    document.getElementById('agentAsk').addEventListener('click', openAsk);
    document.getElementById('agentCaps').addEventListener('click', async () => {
      const caps = S.agent.caps || await API.agent.capabilities();
      global.App.openDrawer(`
        <h2>${t('agent.guarantees')}</h2>
        <ul style="padding-left:20px;color:var(--text-2);font-size:14px;line-height:1.8">${(caps.guarantees || []).map((g) => `<li>${esc(g)}</li>`).join('')}</ul>
        <div class="divider"></div>
        <h3 style="font-size:15px;margin-bottom:10px;color:var(--err)">${t('agent.forbidden')}</h3>
        <div class="chips">${(caps.forbidden || []).map((f) => `<span class="tag" style="color:#f0b3bc">${esc(f)}</span>`).join('')}</div>
        <div class="divider"></div>
        <h3 style="font-size:15px;margin-bottom:10px">${t('agent.profile')}</h3>
        ${(caps.profiles || []).map((p) => `<div class="panel" style="margin-bottom:10px;background:var(--surface-2)">
          <b>${esc(p.label)}</b> <span class="muted small">${esc(p.description)}</span>
          <div class="chips" style="margin-top:8px">${p.scopes.map((s) => `<span class="tag">${esc(s)}</span>`).join('')}</div></div>`).join('')}`);
    });

    function openPairing() {
      const caps = S.agent.caps;
      global.App.openDrawer(`
        <h2>${t('agent.pair')}</h2>
        <div class="form-grid">
          <label class="field"><span>${global.I18N.lang === 'ru' ? 'Имя устройства' : 'Device name'}</span>
            <input class="inp" id="pairName" value="${esc((API.user.display_name || 'work') + '-pc')}"></label>
          <label class="field"><span>OS</span>
            <select class="inp" id="pairPlatform"><option value="windows">Windows</option><option value="macos">macOS</option><option value="linux">Linux</option></select></label>
          <div><div class="muted small" style="margin-bottom:7px">${t('agent.profile')}</div>
            <div class="seg" id="pairProfile">
              ${(caps.profiles || []).map((p, i) => `<button class="${i === 0 ? 'active' : ''}" data-p="${p.id}">${esc(p.label)}</button>`).join('')}
            </div>
            <div class="muted small" id="pairProfileDesc" style="margin-top:8px"></div></div>
          <label class="field"><span>${t('agent.trusted')}</span>
            <input class="inp" id="pairPaths" placeholder="C:\\Users\\me\\Documents, ~/Projects"></label>
          <div class="perm-row"><div class="t"><b>${t('agent.requireApproval')}</b>
            <span>${global.I18N.lang === 'ru' ? 'Каждое действие ждёт вашего подтверждения' : 'Every action waits for your approval'}</span></div>
            <div class="switch on" id="pairApproval"></div></div>
          <label class="check"><input type="checkbox" id="pairConsent"><span>${t('agent.consent')}</span></label>
          <button class="btn btn-gold btn-block" id="pairSubmit">${t('agent.pair')}</button>
        </div>
        <div id="pairResult"></div>`);

      let profile = (caps.profiles && caps.profiles[0] && caps.profiles[0].id) || 'observer';
      const desc = document.getElementById('pairProfileDesc');
      function paintDesc() {
        const found = (caps.profiles || []).find((p) => p.id === profile);
        desc.textContent = found ? found.description : '';
      }
      paintDesc();
      document.querySelectorAll('#pairProfile [data-p]').forEach((btn) => btn.addEventListener('click', () => {
        profile = btn.dataset.p;
        document.querySelectorAll('#pairProfile button').forEach((b) => b.classList.toggle('active', b === btn));
        paintDesc();
      }));
      const sw = document.getElementById('pairApproval');
      sw.addEventListener('click', () => sw.classList.toggle('on'));

      document.getElementById('pairSubmit').addEventListener('click', async () => {
        const consent = document.getElementById('pairConsent');
        if (!consent.checked) { global.App.toast('warn', 'Consent', t('agent.consent')); return; }
        const btn = document.getElementById('pairSubmit');
        btn.classList.add('loading');
        try {
          const res = await API.agent.pair({
            device_name: document.getElementById('pairName').value.trim(),
            platform: document.getElementById('pairPlatform').value,
            permission_profile: profile,
            require_approval: sw.classList.contains('on'),
            trusted_paths: document.getElementById('pairPaths').value.split(',').map((p) => p.trim()).filter(Boolean),
            expires_in_minutes: 15,
            consent_given: true,
          });
          document.getElementById('pairResult').innerHTML = `
            <div class="pair-box" style="margin-top:18px">
              <div class="muted small">${t('agent.code')}</div>
              <div class="pair-code">${esc(res.pairing_code)}</div>
              <div class="muted small">${global.I18N.lang === 'ru' ? 'действует до' : 'valid until'} ${fmtDate(res.expires_at)}</div>
              <div class="pair-key">${esc(res.device_key)}</div>
              <button class="btn btn-sm btn-outline" style="margin-top:12px" id="copyPair">${t('common.copy')}</button>
            </div>`;
          document.getElementById('copyPair').addEventListener('click', () => {
            navigator.clipboard.writeText(`${res.pairing_code}\n${res.device_key}`);
            global.App.toast('ok', t('common.copied'), '');
          });
          loadAll();
        } catch (e) { global.App.toast('err', t('common.error'), e.message); }
        finally { btn.classList.remove('loading'); }
      });
    }

    function openAsk() {
      const caps = S.agent.caps;
      if (!S.agent.devices.length) { global.App.toast('warn', t('agent.noDevices'), ''); return; }
      global.App.openDrawer(`
        <h2>${t('agent.ask')}</h2>
        <div class="form-grid">
          <label class="field"><span>${global.I18N.lang === 'ru' ? 'Устройство' : 'Device'}</span>
            <select class="inp" id="askDevice">${S.agent.devices.map((d) => `<option value="${d.id}">${esc(d.name)} · ${esc(d.permission_profile)}</option>`).join('')}</select></label>
          <div><div class="muted small" style="margin-bottom:7px">${t('agent.action')}</div>
            <div class="chips" id="askActions">${(caps.actions || []).map((a) => `<button class="chip" data-a="${a.id}">${esc(a.label)} <span class="risk ${a.risk}" style="margin-left:6px">${a.risk}</span></button>`).join('')}</div></div>
          <label class="field"><span>Payload (JSON)</span>
            <textarea class="inp" id="askPayload" rows="4" style="width:100%;font-family:var(--mono);font-size:12.5px">{}</textarea></label>
          <button class="btn btn-gold btn-block" id="askSubmit">${t('agent.ask')}</button>
        </div>`);
      let action = 'system.info';
      document.querySelectorAll('#askActions [data-a]').forEach((btn) => btn.addEventListener('click', () => {
        action = btn.dataset.a;
        document.querySelectorAll('#askActions .chip').forEach((b) => b.classList.toggle('active', b === btn));
      }));
      document.querySelector('#askActions .chip').classList.add('active');
      document.getElementById('askSubmit').addEventListener('click', async () => {
        let payload = {};
        try { payload = JSON.parse(document.getElementById('askPayload').value || '{}'); }
        catch (e) { global.App.toast('err', 'JSON', e.message); return; }
        try {
          const res = await API.agent.propose({
            device_id: document.getElementById('askDevice').value, action, payload, requested_by: 'user',
          });
          global.App.toast(res.approval_state === 'approved' ? 'ok' : 'warn', res.label, res.message);
          global.App.closeDrawer();
          loadAll();
        } catch (e) { global.App.toast('err', t('common.error'), e.message); }
      });
    }

    async function loadAll() {
      try {
        S.agent.caps = await API.agent.capabilities();
        const devices = await API.agent.devices();
        S.agent.devices = devices.devices || [];
        paintStats();
        paintDevices();
        const actions = await API.agent.actions({ limit: 40 });
        S.agent.actions = actions.actions || [];
        paintActions();
      } catch (e) {
        document.getElementById('agentDevices').innerHTML = emptyState('alert', t('common.error'), e.message);
      }
    }

    function paintStats() {
      const online = S.agent.devices.filter((d) => d.status === 'online').length;
      const pending = S.agent.actions.filter((a) => a.approval_state === 'pending').length;
      const done = S.agent.actions.filter((a) => a.approval_state === 'done').length;
      document.getElementById('agentStats').innerHTML = [
        { lbl: global.I18N.lang === 'ru' ? 'Устройств' : 'Devices', val: S.agent.devices.length, sub: `${online} online` },
        { lbl: t('agent.approvals'), val: pending, sub: global.I18N.lang === 'ru' ? 'требуют решения' : 'need a decision' },
        { lbl: global.I18N.lang === 'ru' ? 'Выполнено' : 'Executed', val: done, sub: global.I18N.lang === 'ru' ? 'за всё время' : 'all time' },
      ].map((s) => `<div class="stat-card"><div class="lbl">${esc(s.lbl)}</div><div class="val">${s.val}</div><div class="sub">${esc(s.sub)}</div></div>`).join('');
      const badge = document.getElementById('approvalBadge');
      if (badge) { badge.textContent = pending; badge.hidden = pending === 0; }
    }

    function paintDevices() {
      const box = document.getElementById('agentDevices');
      if (!S.agent.devices.length) { box.innerHTML = emptyState('monitor', t('agent.noDevices'), ''); return; }
      box.innerHTML = S.agent.devices.map((d) => `
        <div class="device-card" style="margin-bottom:12px">
          <div class="head">
            <div class="ico">${ico({ windows: 'windows', macos: 'monitor', linux: 'monitor' }[d.platform] || 'monitor')}</div>
            <div style="min-width:0"><b style="display:block;font-size:14.5px">${esc(d.name)}</b>
              <span class="muted small">${esc(d.hostname || d.key_prefix || '')}</span></div>
            <span class="state ${esc(d.status)}" style="margin-left:auto">${esc(d.status)}</span>
          </div>
          <div class="chips" style="margin-bottom:12px">
            <span class="tag">${esc(d.profile_label || d.permission_profile)}</span>
            <span class="tag">${d.require_approval ? 'approval' : 'auto'}</span>
            ${(d.trusted_paths || []).slice(0, 2).map((p) => `<span class="tag">${esc(p)}</span>`).join('')}
          </div>
          <div class="muted small" style="margin-bottom:12px">last seen: ${relative(d.last_seen_at)}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn btn-sm btn-outline" data-perm="${d.id}">${t('agent.profile')}</button>
            <button class="btn btn-sm btn-danger" data-revoke="${d.id}">${t('agent.revoke')}</button>
          </div>
        </div>`).join('');
      box.querySelectorAll('[data-revoke]').forEach((btn) => btn.addEventListener('click', async () => {
        if (!confirm(global.I18N.lang === 'ru' ? 'Отозвать доступ устройства?' : 'Revoke device access?')) return;
        await API.agent.revoke(btn.dataset.revoke);
        global.App.toast('ok', t('agent.revoke'), '');
        loadAll();
      }));
      box.querySelectorAll('[data-perm]').forEach((btn) => btn.addEventListener('click', () => {
        const device = S.agent.devices.find((d) => d.id === btn.dataset.perm);
        const caps = S.agent.caps;
        global.App.openDrawer(`
          <h2>${esc(device.name)}</h2>
          <div class="form-grid">
            <div><div class="muted small" style="margin-bottom:7px">${t('agent.profile')}</div>
              <div class="seg" id="permProfile">${(caps.profiles || []).map((p) => `<button class="${p.id === device.permission_profile ? 'active' : ''}" data-p="${p.id}">${esc(p.label)}</button>`).join('')}</div></div>
            <label class="field"><span>${t('agent.trusted')}</span>
              <input class="inp" id="permPaths" value="${esc((device.trusted_paths || []).join(', '))}"></label>
            <div class="perm-row"><div class="t"><b>${t('agent.requireApproval')}</b></div>
              <div class="switch ${device.require_approval ? 'on' : ''}" id="permApproval"></div></div>
            <button class="btn btn-gold btn-block" id="permSave">${t('common.save')}</button>
          </div>`);
        let profile = device.permission_profile;
        document.querySelectorAll('#permProfile [data-p]').forEach((b) => b.addEventListener('click', () => {
          profile = b.dataset.p;
          document.querySelectorAll('#permProfile button').forEach((x) => x.classList.toggle('active', x === b));
        }));
        const sw = document.getElementById('permApproval');
        sw.addEventListener('click', () => sw.classList.toggle('on'));
        document.getElementById('permSave').addEventListener('click', async () => {
          try {
            await API.agent.update(device.id, {
              permission_profile: profile,
              require_approval: sw.classList.contains('on'),
              trusted_paths: document.getElementById('permPaths').value.split(',').map((p) => p.trim()).filter(Boolean),
            });
            global.App.toast('ok', t('set.saved'), '');
            global.App.closeDrawer();
            loadAll();
          } catch (e) { global.App.toast('err', t('common.error'), e.message); }
        });
      }));
    }

    function paintActions() {
      const approvals = document.getElementById('agentApprovals');
      const history = document.getElementById('agentHistory');
      const pending = S.agent.actions.filter((a) => a.approval_state === 'pending');
      approvals.innerHTML = pending.length ? pending.map((a) => `
        <div class="approval-row">
          <div style="flex:1;min-width:0">
            <b style="font-size:13.5px">${esc(a.label || a.action)}</b>
            <div class="muted small" style="font-family:var(--mono);font-size:11.5px;margin-top:3px">${esc(JSON.stringify(a.payload || {}).slice(0, 120))}</div>
            <div class="chips" style="margin-top:7px"><span class="risk ${esc(a.risk)}">${esc(a.risk)}</span>
              <span class="tag">${esc(a.requested_by)}</span><span class="tag">${esc(a.device_name || '')}</span></div>
          </div>
          <div style="display:flex;gap:6px">
            <button class="btn btn-sm btn-gold" data-ok="${a.id}">${t('agent.approve')}</button>
            <button class="btn btn-sm btn-danger" data-no="${a.id}">${t('agent.deny')}</button>
          </div>
        </div>`).join('') : emptyState('check', global.I18N.lang === 'ru' ? 'Ожидания нет' : 'Nothing pending', '');

      approvals.querySelectorAll('[data-ok]').forEach((b) => b.addEventListener('click', async () => {
        await API.agent.approve(b.dataset.ok); global.App.toast('ok', t('agent.approve'), ''); loadAll();
      }));
      approvals.querySelectorAll('[data-no]').forEach((b) => b.addEventListener('click', async () => {
        await API.agent.deny(b.dataset.no); global.App.toast('info', t('agent.deny'), ''); loadAll();
      }));

      const resolved = S.agent.actions.filter((a) => a.approval_state !== 'pending').slice(0, 14);
      history.innerHTML = resolved.length ? resolved.map((a) => `
        <div class="log-row"><span class="tm">${fmtDate(a.created_at)}</span>
          <span class="ac" style="flex:1">${esc(a.label || a.action)}</span>
          <span class="risk ${a.approval_state === 'done' ? 'low' : a.approval_state === 'failed' ? 'high' : 'medium'}">${esc(a.approval_state)}</span></div>`).join('')
        : `<div class="muted small">—</div>`;
    }

    loadAll();
    const timer = setInterval(() => { if (document.getElementById('agentApprovals')) loadAll(); else clearInterval(timer); }, 12000);
  }

  /* ===================================================================== */
  /* SECURITY                                                               */
  /* ===================================================================== */
  function renderSecurity(host) {
    host.innerHTML = `
      <div class="view-head">
        <div><h1>${t('sec.title')}</h1><p>${t('sec.sub')}</p></div>
        <div class="actions">
          <button class="btn btn-outline btn-sm" id="secExport">${ico('download')} ${t('sec.export')}</button>
          <button class="btn btn-danger btn-sm" id="secRevoke">${t('sec.revokeAll')}</button>
        </div>
      </div>
      <div class="grid-2" style="align-items:start">
        <section class="panel" id="secScorePanel">${spinner()}</section>
        <section class="panel">
          <div class="panel-head"><h3>${t('sec.mfa')}</h3></div>
          <div id="secMfa">${spinner()}</div>
        </section>
      </div>
      <div class="grid-2" style="margin-top:18px;align-items:start">
        <section class="panel">
          <div class="panel-head"><h3>${t('sec.sessions')}</h3></div>
          <div id="secSessions">${spinner()}</div>
        </section>
        <section class="panel">
          <div class="panel-head"><h3>${t('sec.events')}</h3></div>
          <div id="secEvents" style="max-height:320px;overflow:auto">${spinner()}</div>
        </section>
      </div>
      <section class="panel" style="margin-top:18px">
        <div class="panel-head"><h3>${t('sec.audit')}</h3></div>
        <div id="secAudit" style="max-height:340px;overflow:auto">${spinner()}</div>
      </section>
      <section class="panel" style="margin-top:18px;border-color:rgba(217,106,122,.35)">
        <div class="panel-head"><h3 style="color:#f0b3bc">${t('sec.erase')}</h3></div>
        <p class="muted small">${global.I18N.lang === 'ru'
          ? 'Удаление необратимо: диалоги, файлы, изображения, документы и журнал действий будут стёрты немедленно, аккаунт помечается как удалённый.'
          : 'Erasure is irreversible: chats, files, images, documents and the activity log are wiped immediately and the account is marked deleted.'}</p>
        <button class="btn btn-danger btn-sm" id="secErase">${t('sec.erase')}</button>
      </section>`;

    document.getElementById('secRevoke').addEventListener('click', async () => {
      if (!confirm(global.I18N.lang === 'ru' ? 'Завершить все сессии и отозвать API-ключи?' : 'Sign out everywhere and revoke API keys?')) return;
      await API.auth.revokeAll();
      global.App.toast('ok', t('sec.revokeAll'), '');
      global.App.leaveWorkspace();
    });
    document.getElementById('secExport').addEventListener('click', async () => {
      const data = await API.auth.export();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `mestinghem-export-${Date.now()}.json`; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      global.App.toast('ok', t('sec.export'), `${fmtBytes(blob.size)}`);
    });
    document.getElementById('secErase').addEventListener('click', () => {
      const password = prompt(global.I18N.lang === 'ru' ? 'Введите пароль для подтверждения удаления' : 'Enter your password to confirm erasure');
      if (!password) return;
      API.auth.erase({ current_password: password, new_password: password })
        .then(() => { global.App.toast('ok', t('sec.erase'), ''); global.App.leaveWorkspace(); })
        .catch((e) => global.App.toast('err', t('common.error'), e.message));
    });

    (async () => {
      try {
        const data = await API.auth.security();
        S.security = data;
        paintScore(data);
        paintMfa(API.user);
        const sessions = await API.auth.sessions();
        paintSessions(sessions.sessions || []);
        paintEvents(data.events || []);
        const auditData = await API.auth.audit(120);
        paintAudit(auditData.events || []);
      } catch (e) {
        document.getElementById('secScorePanel').innerHTML = emptyState('alert', t('common.error'), e.message);
      }
    })();

    function paintScore(data) {
      const score = Math.max(0, Math.min(100, data.score || 0));
      const colour = score >= 80 ? '#4fc3a1' : score >= 55 ? '#e0b252' : '#d96a7a';
      const circumference = 2 * Math.PI * 56;
      document.getElementById('secScorePanel').innerHTML = `
        <div class="panel-head"><h3>${t('sec.score')}</h3></div>
        <div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap">
          <div class="score-ring">
            <svg width="132" height="132" viewBox="0 0 132 132">
              <circle cx="66" cy="66" r="56" fill="none" stroke="var(--surface-3)" stroke-width="10"/>
              <circle cx="66" cy="66" r="56" fill="none" stroke="${colour}" stroke-width="10" stroke-linecap="round"
                stroke-dasharray="${circumference}" stroke-dashoffset="${circumference}" id="scoreArc"/>
            </svg><div class="val">${score}</div>
          </div>
          <div style="flex:1;min-width:220px">
            ${(data.recommendations || []).map((r) => `<div class="perm-row" style="padding:9px 0">
              <span class="risk ${r.level === 'high' ? 'high' : r.level === 'medium' ? 'medium' : 'low'}">${esc(r.level)}</span>
              <div class="t"><b>${esc(global.I18N.lang === 'ru' ? r.ru : r.en)}</b></div></div>`).join('')}
          </div>
        </div>
        <div class="divider"></div>
        <dl class="kv">
          <dt>${t('sec.mfa')}</dt><dd>${data.mfa_enabled ? ico('check') + ' on' : ico('x') + ' off'}</dd>
          <dt>${global.I18N.lang === 'ru' ? 'Подтверждённый e-mail' : 'Verified e-mail'}</dt><dd>${data.email_verified ? ico('check') : ico('x')}</dd>
          <dt>${t('sec.sessions')}</dt><dd>${data.active_sessions}</dd>
          <dt>${global.I18N.lang === 'ru' ? 'Заблокировано команд' : 'Blocked commands'}</dt><dd>${data.blocked_commands}</dd>
        </dl>`;
      const arc = document.getElementById('scoreArc');
      requestAnimationFrame(() => {
        arc.style.transition = 'stroke-dashoffset 1.2s cubic-bezier(.16,1,.3,1)';
        arc.style.strokeDashoffset = String(circumference * (1 - score / 100));
      });
    }

    function paintMfa(user) {
      const box = document.getElementById('secMfa');
      if (user.mfa_enabled) {
        box.innerHTML = `<div class="perm-row"><div class="t"><b>${ico('check')} ${t('sec.mfa')}</b>
          <span>${global.I18N.lang === 'ru' ? 'TOTP активирован' : 'TOTP active'}</span></div>
          <button class="btn btn-sm btn-danger" id="mfaOff">${global.I18N.lang === 'ru' ? 'Отключить' : 'Disable'}</button></div>`;
        document.getElementById('mfaOff').addEventListener('click', async () => {
          const password = prompt(global.I18N.lang === 'ru' ? 'Пароль для отключения 2FA' : 'Password to disable 2FA');
          if (!password) return;
          try { await API.auth.mfaDisable(password); global.App.reloadUser(); global.App.toast('ok', '2FA', 'off'); renderSecurity(host); }
          catch (e) { global.App.toast('err', t('common.error'), e.message); }
        });
        return;
      }
      box.innerHTML = `<p class="muted small">${global.I18N.lang === 'ru'
        ? 'Второй фактор защищает аккаунт, даже если пароль скомпрометирован. Подходит любое TOTP-приложение.'
        : 'A second factor protects the account even if the password leaks. Any TOTP app works.'}</p>
        <button class="btn btn-gold btn-sm" id="mfaSetup">${global.I18N.lang === 'ru' ? 'Настроить 2FA' : 'Set up 2FA'}</button>
        <div id="mfaBox"></div>`;
      document.getElementById('mfaSetup').addEventListener('click', async () => {
        try {
          const setup = await API.auth.mfaSetup();
          document.getElementById('mfaBox').innerHTML = `
            <div class="pair-box" style="margin-top:16px">
              <div class="muted small">TOTP secret</div>
              <div class="pair-key" style="font-size:14px;letter-spacing:.12em">${esc(setup.secret)}</div>
              <div class="muted small" style="margin-top:12px">${global.I18N.lang === 'ru' ? 'Резервные коды' : 'Recovery codes'}</div>
              <div class="pair-key">${setup.recovery_codes.join(' · ')}</div>
              <div style="display:flex;gap:8px;margin-top:14px;justify-content:center">
                <input class="inp" id="mfaCode" placeholder="000000" maxlength="6" style="width:130px;text-align:center">
                <button class="btn btn-gold btn-sm" id="mfaConfirm">${global.I18N.lang === 'ru' ? 'Включить' : 'Enable'}</button>
              </div>
              <div class="muted small" style="margin-top:10px">${esc(setup.warning)}</div>
            </div>`;
          document.getElementById('mfaConfirm').addEventListener('click', async () => {
            try {
              await API.auth.mfaEnable(document.getElementById('mfaCode').value.trim());
              global.App.reloadUser();
              global.App.toast('ok', t('sec.mfa'), '');
              renderSecurity(host);
            } catch (e) { global.App.toast('err', t('common.error'), e.message); }
          });
        } catch (e) { global.App.toast('err', t('common.error'), e.message); }
      });
    }

    function paintSessions(sessions) {
      document.getElementById('secSessions').innerHTML = sessions.length ? sessions.map((s) => `
        <div class="perm-row"><div class="t"><b>${esc(s.device)}</b>
          <span>${esc(s.ip || '')} · ${fmtDate(s.created_at)}${s.revoked ? ' · revoked' : ''}</span></div>
          <span class="state ${s.revoked ? 'revoked' : 'online'}">${s.revoked ? 'revoked' : 'active'}</span></div>`).join('')
        : `<div class="muted small">—</div>`;
    }

    function paintEvents(events) {
      document.getElementById('secEvents').innerHTML = events.length ? events.map((e) => `
        <div class="log-row"><span class="tm">${fmtDate(e.created_at)}</span>
          <span class="ac" style="flex:1">${esc(e.kind)}</span>
          <span class="risk ${e.severity === 'critical' || e.severity === 'high' ? 'high' : e.severity === 'medium' ? 'medium' : 'low'}">${esc(e.severity)}</span></div>`).join('')
        : `<div class="muted small">—</div>`;
    }

    function paintAudit(events) {
      document.getElementById('secAudit').innerHTML = events.length ? events.map((e) => `
        <div class="log-row"><span class="tm">${fmtDate(e.created_at)}</span>
          <span class="ac" style="flex:1">${esc(e.action)}</span>
          <span class="muted small">${esc(e.ip || '')}</span>
          <span class="risk ${e.outcome === 'success' ? 'low' : e.outcome === 'denied' ? 'high' : 'medium'}">${esc(e.outcome)}</span></div>`).join('')
        : `<div class="muted small">—</div>`;
    }
  }

  /* ===================================================================== */
  /* SETTINGS                                                               */
  /* ===================================================================== */
  const CAPS = [
    { key: 'chat', icon: 'spark' }, { key: 'files', icon: 'folder' }, { key: 'documents', icon: 'presentation' },
    { key: 'images', icon: 'image' }, { key: 'chess', icon: 'chess' }, { key: 'terminal', icon: 'terminal' },
    { key: 'terminal_packages', icon: 'box' }, { key: 'agent', icon: 'monitor' },
  ];

  function renderSettings(host) {
    const user = API.user;
    host.innerHTML = `
      <div class="view-head"><div><h1>${t('set.title')}</h1></div></div>
      <div class="set-layout">
        <nav class="set-nav" id="setNav">
          ${['profile', 'appearance', 'capabilities', 'keys', 'about'].map((tab) =>
            `<button data-tab="${tab}" class="${S.settingsTab === tab ? 'active' : ''}">${t('set.' + tab)}</button>`).join('')}
        </nav>
        <section id="setBody"></section>
      </div>`;

    document.querySelectorAll('#setNav [data-tab]').forEach((btn) => btn.addEventListener('click', () => {
      S.settingsTab = btn.dataset.tab;
      document.querySelectorAll('#setNav button').forEach((b) => b.classList.toggle('active', b === btn));
      paintTab();
    }));
    paintTab();

    function paintTab() {
      const body = document.getElementById('setBody');
      if (S.settingsTab === 'profile') {
        body.innerHTML = `<div class="panel">
          <div class="panel-head"><h3>${t('set.profile')}</h3></div>
          <div class="form-grid" style="max-width:520px">
            <label class="field"><span>${t('set.displayName')}</span><input class="inp" id="setName" value="${esc(user.display_name || '')}"></label>
            <label class="field"><span>E-mail</span><input class="inp" value="${esc(user.email)}" disabled></label>
            <label class="field"><span>${t('auth.username')}</span><input class="inp" value="${esc(user.username)}" disabled></label>
            <label class="field"><span>${t('set.language')}</span>
              <select class="inp" id="setLang"><option value="ru" ${user.language === 'ru' ? 'selected' : ''}>Русский</option>
              <option value="en" ${user.language === 'en' ? 'selected' : ''}>English</option></select></label>
            <button class="btn btn-gold" id="saveProfile">${t('set.save')}</button>
          </div>
          <div class="divider"></div>
          <div class="panel-head"><h3>${t('auth.password')}</h3></div>
          <div class="form-grid" style="max-width:520px">
            <label class="field"><span>${global.I18N.lang === 'ru' ? 'Текущий пароль' : 'Current password'}</span><input class="inp" type="password" id="pwOld"></label>
            <label class="field"><span>${global.I18N.lang === 'ru' ? 'Новый пароль' : 'New password'}</span><input class="inp" type="password" id="pwNew"></label>
            <button class="btn btn-outline" id="savePw">${global.I18N.lang === 'ru' ? 'Сменить пароль' : 'Change password'}</button>
          </div>
        </div>`;
        document.getElementById('saveProfile').addEventListener('click', async () => {
          try {
            await API.auth.profile({
              display_name: document.getElementById('setName').value.trim(),
              language: document.getElementById('setLang').value,
            });
            global.I18N.setLang(document.getElementById('setLang').value);
            global.App.toast('ok', t('set.saved'), '');
            global.App.reloadUser();
          } catch (e) { global.App.toast('err', t('common.error'), e.message); }
        });
        document.getElementById('savePw').addEventListener('click', async () => {
          try {
            await API.auth.password({
              current_password: document.getElementById('pwOld').value,
              new_password: document.getElementById('pwNew').value,
            });
            global.App.toast('ok', t('auth.password'), global.I18N.lang === 'ru' ? 'Пароль изменён, войдите снова' : 'Password changed, sign in again');
            global.App.leaveWorkspace();
          } catch (e) { global.App.toast('err', t('common.error'), e.message); }
        });
        return;
      }

      if (S.settingsTab === 'appearance') {
        const prefs = user.preferences || {};
        body.innerHTML = `<div class="panel">
          <div class="panel-head"><h3>${t('set.appearance')}</h3></div>
          <div class="perm-row"><div class="t"><b>${t('set.theme')}</b><span>Aurum Dark / Ivory Light</span></div>
            <div class="seg" style="width:220px" id="themeSeg">
              <button class="${user.theme !== 'ivory' ? 'active' : ''}" data-theme="aurum">Dark</button>
              <button class="${user.theme === 'ivory' ? 'active' : ''}" data-theme="ivory">Light</button></div></div>
          <div class="perm-row"><div class="t"><b>${t('set.animations')}</b>
            <span>${global.I18N.lang === 'ru' ? 'Плавные переходы, частицы, параллакс' : 'Transitions, particles, parallax'}</span></div>
            <div class="switch ${prefs.animations !== false ? 'on' : ''}" id="swAnim"></div></div>
          <div class="perm-row"><div class="t"><b>${global.I18N.lang === 'ru' ? 'Компактный интерфейс' : 'Compact UI'}</b></div>
            <div class="switch ${prefs.compact ? 'on' : ''}" id="swCompact"></div></div>
          <div class="perm-row"><div class="t"><b>${t('chat.stream')}</b>
            <span>${global.I18N.lang === 'ru' ? 'Ответ печатается по мере генерации' : 'Answers stream as they are produced'}</span></div>
            <div class="switch ${prefs.streaming !== false ? 'on' : ''}" id="swStream"></div></div>
        </div>`;
        const collect = () => ({
          theme: document.querySelector('#themeSeg .active').dataset.theme,
          preferences: Object.assign({}, prefs, {
            animations: document.getElementById('swAnim').classList.contains('on'),
            compact: document.getElementById('swCompact').classList.contains('on'),
            streaming: document.getElementById('swStream').classList.contains('on'),
          }),
        });
        document.querySelectorAll('#themeSeg button').forEach((b) => b.addEventListener('click', () => {
          document.querySelectorAll('#themeSeg button').forEach((x) => x.classList.toggle('active', x === b));
          save();
        }));
        ['swAnim', 'swCompact', 'swStream'].forEach((id) =>
          document.getElementById(id).addEventListener('click', (ev) => { ev.currentTarget.classList.toggle('on'); save(); }));
        async function save() {
          const payload = collect();
          document.documentElement.dataset.theme = payload.theme;
          try { await API.auth.profile(payload); global.App.reloadUser(); global.App.toast('ok', t('set.saved'), ''); }
          catch (e) { global.App.toast('err', t('common.error'), e.message); }
        }
        return;
      }

      if (S.settingsTab === 'capabilities') {
        const perms = user.permissions || {};
        body.innerHTML = `<div class="panel">
          <div class="panel-head"><h3>${t('set.capabilities')}</h3></div>
          <p class="muted small">${global.I18N.lang === 'ru'
            ? 'Модули «Терминал» и «Агент» по умолчанию выключены и включаются только вашим явным действием. Каждое изменение фиксируется в журнале безопасности.'
            : 'Terminal and Agent are off by default and only enabled by your explicit action. Every change is written to the security log.'}</p>
          ${CAPS.map((cap) => `
            <div class="perm-row">
              <span style="font-size:19px;width:26px;text-align:center">${ico(cap.icon)}</span>
              <div class="t"><b>${t('set.cap.' + cap.key)}</b>
                <span>${capHint(cap.key)}</span></div>
              <div class="switch ${perms[cap.key] ? 'on' : ''}" data-cap="${cap.key}"></div>
            </div>`).join('')}
        </div>`;
        body.querySelectorAll('[data-cap]').forEach((sw) => sw.addEventListener('click', async () => {
          const key = sw.dataset.cap;
          const next = !sw.classList.contains('on');
          if ((key === 'terminal' || key === 'agent') && next) {
            const ok = confirm(global.I18N.lang === 'ru'
              ? `Включить «${t('set.cap.' + key)}»? Действие будет записано в журнал безопасности.`
              : `Enable "${t('set.cap.' + key)}"? This is written to the security log.`);
            if (!ok) return;
          }
          sw.classList.toggle('on', next);
          try {
            await API.auth.profile({ permissions: { [key]: next } });
            global.App.reloadUser();
            global.App.toast(next ? 'ok' : 'info', t('set.cap.' + key), next ? 'on' : 'off');
            global.App.buildRail();
          } catch (e) { global.App.toast('err', t('common.error'), e.message); }
        }));
        return;
      }

      if (S.settingsTab === 'keys') {
        body.innerHTML = `<div class="panel">
          <div class="panel-head"><h3>${t('set.keys')}</h3>
            <span class="spacer" style="margin-left:auto"></span>
            <button class="btn btn-sm btn-gold" id="newKey">＋ ${t('set.newKey')}</button></div>
          <p class="muted small">${global.I18N.lang === 'ru'
            ? 'Ключи дают доступ к API платформы из ваших скриптов. Передавайте их в заголовке X-API-Key. Ключ показывается один раз.'
            : 'Keys grant API access from your scripts. Send them in the X-API-Key header. A key is shown once.'}</p>
          <div id="keyList">${spinner()}</div>
        </div>`;
        const listBox = document.getElementById('keyList');
        const load = async () => {
          const data = await API.auth.keys();
          listBox.innerHTML = (data.keys || []).length ? data.keys.map((k) => `
            <div class="perm-row"><div class="t"><b>${esc(k.name)}</b>
              <span><code>${esc(k.prefix)}…</code> · ${(k.scopes || []).join(', ')} · ${fmtDate(k.created_at)}${k.last_used_at ? ` · used ${relative(k.last_used_at)}` : ''}</span></div>
              <span class="state ${k.active ? 'online' : 'revoked'}">${k.active ? 'active' : 'revoked'}</span>
              ${k.active ? `<button class="btn btn-sm btn-danger" data-rev="${k.id}">${ico('x')}</button>` : ''}</div>`).join('')
            : `<div class="muted small">—</div>`;
          listBox.querySelectorAll('[data-rev]').forEach((b) => b.addEventListener('click', async () => {
            await API.auth.revokeKey(b.dataset.rev); load();
          }));
        };
        document.getElementById('newKey').addEventListener('click', async () => {
          const name = prompt(t('set.keyName'), 'script');
          if (!name) return;
          try {
            const res = await API.auth.createKey({ name, scopes: ['read', 'write'] });
            global.App.openDrawer(`<h2>${t('set.newKey')}</h2>
              <p class="muted small">${t('set.keyWarn')}</p>
              <div class="pair-key" style="font-size:14px">${esc(res.key)}</div>
              <button class="btn btn-gold btn-sm" style="margin-top:14px" id="copyKey">${t('common.copy')}</button>`);
            document.getElementById('copyKey').addEventListener('click', () => {
              navigator.clipboard.writeText(res.key);
              global.App.toast('ok', t('common.copied'), '');
            });
            load();
          } catch (e) { global.App.toast('err', t('common.error'), e.message); }
        });
        load();
        return;
      }

      body.innerHTML = `<div class="panel">
        <div class="panel-head"><h3>${t('set.about')}</h3></div>
        <div id="aboutBox">${spinner()}</div>
        <div class="divider"></div>
        <h3 style="font-size:15px;margin-bottom:10px">${t('footer.legal')}</h3>
        <div class="chips">
          ${['privacy', 'terms', 'security', 'cookies', 'aup', 'dpa', 'agent', 'disclosure'].map((d) =>
            `<a class="chip" href="#/legal/${d}">${t('footer.' + (d === 'aup' ? 'aup' : d === 'disclosure' ? 'disclosure' : d))}</a>`).join('')}
        </div>
      </div>`;
      (async () => {
        try {
          const [cfg, health, status] = await Promise.all([API.config(), API.system.health(), API.system.status()]);
          document.getElementById('aboutBox').innerHTML = `
            <dl class="kv">
              <dt>Platform</dt><dd>${esc(cfg.brand)} ${esc(cfg.version)} (${esc(cfg.build)})</dd>
              <dt>Engine</dt><dd>mst-core · mst-lite · mst-analyst</dd>
              <dt>Database</dt><dd>${esc(health.database.engine)} · ${health.database.latency_ms} ms</dd>
              <dt>Storage</dt><dd>${esc(health.storage.backend)}</dd>
              <dt>Uptime</dt><dd>${Math.round(health.uptime_seconds / 60)} min</dd>
              <dt>Modules</dt><dd>${Object.keys(cfg.features).filter((k) => cfg.features[k]).length}/${Object.keys(cfg.features).length}</dd>
              <dt>Languages</dt><dd>${(cfg.languages || []).join(', ')}</dd>
              <dt>Support</dt><dd>${esc(cfg.contact.support)}</dd>
              <dt>DPO</dt><dd>${esc(cfg.contact.dpo)}</dd>
            </dl>
            <div class="divider"></div>
            <dl class="kv">
              <dt>Users</dt><dd>${status.counts.users}</dd>
              <dt>Messages</dt><dd>${status.counts.messages}</dd>
              <dt>Files</dt><dd>${status.counts.files}</dd>
              <dt>Documents</dt><dd>${status.counts.documents}</dd>
              <dt>Images</dt><dd>${status.counts.images}</dd>
              <dt>Chess games</dt><dd>${status.counts.chess_games}</dd>
            </dl>`;
        } catch (e) {
          document.getElementById('aboutBox').innerHTML = `<div class="muted small">${esc(e.message)}</div>`;
        }
      })();
    }

    function capHint(key) {
      const ru = global.I18N.lang === 'ru';
      return {
        chat: ru ? 'Диалоги, режимы мышления, работа с контекстом файлов' : 'Chats, thinking modes, file context',
        files: ru ? 'Директория, загрузка, версии, поиск' : 'Directory, uploads, versions, search',
        documents: ru ? 'Презентации, таблицы, документы по ТЗ' : 'Decks, sheets and documents from a brief',
        images: ru ? 'Генерация визуалов и галерея' : 'Visual generation and gallery',
        chess: ru ? 'Партия, анализ, разбор, PGN' : 'Play, analyse, review, PGN',
        terminal: ru ? 'PTY-сессия в изолированной песочнице (требует согласия)' : 'PTY session in an isolated sandbox (consent required)',
        terminal_packages: ru ? 'Установка пакетов внутри песочницы' : 'Package installation inside the sandbox',
        agent: ru ? 'Управление подключённым компьютером (требует согласия и сопряжения)' : 'Control of a paired computer (consent and pairing required)',
      }[key] || '';
    }
  }

  /* ===================================================================== */
  function debounce(fn, ms) {
    let timer;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  global.Views = {
    md, esc, spinner, emptyState, iconFor, debounce, actionCards,
    chat: renderChat, directory: renderDirectory, terminal: renderTerminal,
    documents: renderDocuments, images: renderImages, agent: renderAgent,
    security: renderSecurity, settings: renderSettings,
  };
})(window);
