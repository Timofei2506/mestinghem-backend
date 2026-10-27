/* Mestinghem AI — chess module ============================================
   Board rendering, drag & drop, move legality from the server's legal list,
   evaluation bar, move list with per-move grades and the commentary panel.
   ========================================================================= */
(function (global) {
  'use strict';
  const { t, fmtDate, relative } = global.I18N;
  const API = global.API;
  const esc = global.Views.esc;

  const GLYPH = {
    wK: '♔', wQ: '♕', wR: '♖', wB: '♗', wN: '♘', wP: '♙',
    bK: '♚', bQ: '♛', bR: '♜', bB: '♝', bN: '♞', bP: '♟',
  };
  const FILES = 'abcdefgh';
  const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

  const C = global.Store.chess;

  function parseFen(fen) {
    const [placement, turn, castling, ep, half, full] = String(fen || START_FEN).split(/\s+/);
    const board = Array.from({ length: 8 }, () => Array(8).fill(null));
    let rank = 0, file = 0;
    for (const ch of placement || '') {
      if (ch === '/') { rank++; file = 0; continue; }
      if (/\d/.test(ch)) { file += Number(ch); continue; }
      board[rank][file] = (ch === ch.toUpperCase() ? 'w' : 'b') + ch.toUpperCase();
      file++;
    }
    return {
      board, turn: turn === 'b' ? 'black' : 'white', castling: castling || '-', ep: ep || '-',
      half: Number(half || 0), full: Number(full || 1),
    };
  }

  function squareName(file, rank) { return FILES[file] + (8 - rank); }

  function findKing(position, colour) {
    for (let r = 0; r < 8; r++) {
      for (let f = 0; f < 8; f++) {
        if (position.board[r][f] === colour + 'K') return { r, f };
      }
    }
    return null;
  }

  /* ------------------------------------------------------------------- UI */
  function render(host) {
    host.innerHTML = `
      <div class="view-head">
        <div><h1>${t('chess.title')}</h1><p>${t('chess.sub')}</p></div>
        <div class="actions">
          <button class="btn btn-ghost btn-sm" id="chFlip">⇅ ${t('chess.flip')}</button>
          <button class="btn btn-ghost btn-sm" id="chGames">${ico('refresh')} ${global.I18N.lang === 'ru' ? 'Мои партии' : 'My games'}</button>
          <button class="btn btn-gold btn-sm" id="chNew">＋ ${t('chess.new')}</button>
        </div>
      </div>
      <div id="chBody">${global.Views.spinner()}</div>`;

    document.getElementById('chNew').addEventListener('click', newGameDialog);
    document.getElementById('chFlip').addEventListener('click', () => { C.flipped = !C.flipped; if (C.game) paint(); });
    document.getElementById('chGames').addEventListener('click', gamesDialog);

    if (C.game) { paint(); return; }
    const saved = localStorage.getItem('mst.chess.game');
    if (saved) {
      API.chess.game(saved).then((game) => { C.game = game; paint(); }).catch(() => newGameDialog());
    } else {
      newGameDialog();
    }
  }

  function newGameDialog() {
    const levels = [
      { v: 10, ru: 'Новичок', en: 'Beginner' }, { v: 30, ru: 'Любитель', en: 'Casual' },
      { v: 55, ru: 'Клубный игрок', en: 'Club player' }, { v: 75, ru: 'Кандидат в мастера', en: 'Candidate master' },
      { v: 90, ru: 'Мастер', en: 'Master' }, { v: 100, ru: 'Гроссмейстер', en: 'Grandmaster' },
    ];
    global.App.openDrawer(`
      <h2>${t('chess.new')}</h2>
      <div class="form-grid">
        <div><div class="muted small" style="margin-bottom:7px">${t('chess.side')}</div>
          <div class="seg" id="ngSide">
            <button class="active" data-v="white">♔ ${t('chess.white')}</button>
            <button data-v="black">♚ ${t('chess.black')}</button>
            <button data-v="random">${ico('dice')} ${t('chess.random')}</button>
          </div></div>
        <div><div class="muted small" style="margin-bottom:7px">${t('chess.mode')}</div>
          <div class="seg" id="ngMode">
            <button class="active" data-v="engine">${t('chess.modeEngine')}</button>
            <button data-v="analysis">${t('chess.modeAnalysis')}</button>
            <button data-v="hotseat">${t('chess.modeHotseat')}</button>
          </div></div>
        <div><div class="muted small" style="margin-bottom:7px">${t('chess.level')}</div>
          <input type="range" id="ngLevel" min="5" max="100" value="60" style="width:100%">
          <div class="chips" style="margin-top:9px">${levels.map((l) => `<button class="chip" data-l="${l.v}">${global.I18N.lang === 'ru' ? l.ru : l.en}</button>`).join('')}</div>
          <div class="muted small" id="ngLevelLabel" style="margin-top:8px"></div></div>
        <label class="field"><span>FEN <span class="muted small">(${global.I18N.lang === 'ru' ? 'необязательно' : 'optional'})</span></span>
          <input class="inp" id="ngFen" placeholder="${esc(START_FEN)}" style="font-family:var(--mono);font-size:12px"></label>
        <button class="btn btn-gold btn-block" id="ngStart">${t('chess.start')}</button>
      </div>`);

    let side = 'white', mode = 'engine';
    const levelInput = document.getElementById('ngLevel');
    const label = document.getElementById('ngLevelLabel');
    function paintLabel() {
      const v = Number(levelInput.value);
      const found = levels.reduce((best, l) => Math.abs(l.v - v) < Math.abs(best.v - v) ? l : best, levels[0]);
      label.textContent = `${global.I18N.lang === 'ru' ? found.ru : found.en} · ${v}`;
    }
    paintLabel();
    levelInput.addEventListener('input', paintLabel);
    document.querySelectorAll('[data-l]').forEach((b) => b.addEventListener('click', () => { levelInput.value = b.dataset.l; paintLabel(); }));
    document.querySelectorAll('#ngSide [data-v]').forEach((b) => b.addEventListener('click', () => {
      side = b.dataset.v;
      document.querySelectorAll('#ngSide button').forEach((x) => x.classList.toggle('active', x === b));
    }));
    document.querySelectorAll('#ngMode [data-v]').forEach((b) => b.addEventListener('click', () => {
      mode = b.dataset.v;
      document.querySelectorAll('#ngMode button').forEach((x) => x.classList.toggle('active', x === b));
    }));

    document.getElementById('ngStart').addEventListener('click', async (ev) => {
      const btn = ev.currentTarget;
      btn.classList.add('loading');
      try {
        const game = await API.chess.create({
          side, mode, level: Number(levelInput.value),
          fen: document.getElementById('ngFen').value.trim() || null,
        });
        C.game = game;
        C.flipped = game.side === 'black';
        C.analysis = null;
        localStorage.setItem('mst.chess.game', game.id);
        global.App.closeDrawer();
        paint();
        if (game.status === 'active' && game.turn !== game.side && mode === 'engine') {
          global.App.toast('info', t('chess.thinking'), '');
        }
      } catch (e) { global.App.toast('err', t('common.error'), e.message); }
      finally { btn.classList.remove('loading'); }
    });
  }

  async function gamesDialog() {
    global.App.openDrawer(`<h2>${global.I18N.lang === 'ru' ? 'Мои партии' : 'My games'}</h2><div id="gamesBox">${global.Views.spinner()}</div>`);
    try {
      const data = await API.chess.games();
      const games = data.games || [];
      document.getElementById('gamesBox').innerHTML = games.length ? games.map((g) => `
        <div class="perm-row" data-g="${g.id}" style="cursor:pointer">
          <div class="t"><b>${esc(g.white)} — ${esc(g.black)}</b>
            <span>${g.plies} ${global.I18N.lang === 'ru' ? 'ходов' : 'plies'} · ${esc(g.result || g.status)} · ${relative(g.updated_at)}</span></div>
          <span class="state ${g.status === 'active' ? 'online' : 'offline'}">${esc(g.status)}</span>
        </div>`).join('') : global.Views.emptyState('chess', t('chess.noGames'), '');
      document.querySelectorAll('[data-g]').forEach((el) => el.addEventListener('click', async () => {
        try {
          C.game = await API.chess.game(el.dataset.g);
          C.analysis = null;
          localStorage.setItem('mst.chess.game', C.game.id);
          global.App.closeDrawer();
          paint();
        } catch (e) { global.App.toast('err', t('common.error'), e.message); }
      }));
    } catch (e) {
      document.getElementById('gamesBox').innerHTML = global.Views.emptyState('alert', t('common.error'), e.message);
    }
  }

  /* -------------------------------------------------------------- painting */
  function paint() {
    const game = C.game;
    const body = document.getElementById('chBody');
    if (!body || !game) return;
    const position = parseFen(game.fen);
    const king = game.checks ? findKing(position, position.turn) : null;
    const lastUci = (game.uci || [])[game.uci.length - 1] || '';

    body.innerHTML = `
      <div class="chess-layout">
        <section>
          <div class="board-card">
            ${playerBar(game, position, 'top')}
            <div class="board-frame">
              <div class="eval-bar" id="evalBar"><span></span></div>
              <div class="board" id="board"></div>
            </div>
            ${playerBar(game, position, 'bottom')}
          </div>
          <div class="panel" style="margin-top:16px">
            <div class="panel-head"><h3>${t('chess.moves')}</h3>
              <span class="spacer" style="margin-left:auto"></span>
              <span class="muted small">${game.opening ? esc(game.opening[global.I18N.lang] || game.opening.ru) : ''}</span></div>
            <div class="move-list" id="moveList"></div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
              <button class="btn btn-sm btn-outline" id="chHint">${ico('spark')} ${t('chess.hint')}</button>
              <button class="btn btn-sm btn-outline" id="chAnalyse">${ico('image')} ${t('chess.analyse')}</button>
              <button class="btn btn-sm btn-outline" id="chReview">${ico('presentation')} ${t('chess.review')}</button>
              <button class="btn btn-sm btn-ghost" id="chUndo">${ico('refresh')} ${t('chess.undo')}</button>
              <button class="btn btn-sm btn-ghost" id="chPgn">${ico('download')} ${t('chess.pgn')}</button>
              <button class="btn btn-sm btn-danger" id="chResign">${ico('flag')} ${t('chess.resign')}</button>
            </div>
          </div>
        </section>
        <aside>
          <div class="analysis-box" id="analysisBox">
            <div class="panel-head"><h3>${t('chess.commentary')}</h3></div>
            <div class="txt" id="analysisText">${C.analysis ? '' : `<span class="muted">${global.I18N.lang === 'ru'
              ? 'Сделайте ход или запросите анализ — я расскажу, что вижу в позиции.'
              : 'Make a move or request analysis — I will tell you what I see.'}</span>`}</div>
            <div id="analysisExtra"></div>
          </div>
          <div class="panel" style="margin-top:16px">
            <div class="panel-head"><h3>${t('chess.material')}</h3></div>
            <dl class="kv">
              <dt>${t('chess.opening')}</dt><dd>${esc((game.opening && (game.opening[global.I18N.lang] || game.opening.ru)) || '—')}</dd>
              <dt>${global.I18N.lang === 'ru' ? 'Ход' : 'Move'}</dt><dd>${game.fullmove_number} · ${game.turn === 'white' ? '♔' : '♚'}</dd>
              <dt>${t('chess.material')}</dt><dd>${game.material ? `${game.material.white} : ${game.material.black}` : '—'}</dd>
              <dt>${global.I18N.lang === 'ru' ? 'Уровень' : 'Level'}</dt><dd>${esc((game.engine && game.engine.level_label) || game.engine_level)}</dd>
              <dt>${t('chess.mode')}</dt><dd>${esc(game.mode)}</dd>
              <dt>Status</dt><dd>${esc(game.status)}${game.result ? ` · ${esc(game.result)}` : ''}</dd>
            </dl>
          </div>
        </aside>
      </div>`;

    drawBoard(position, king, lastUci);
    drawMoves(game);
    wireActions(game);
    if (C.analysis) paintAnalysis(C.analysis);
    if (game.status !== 'active') {
      const result = game.result;
      const message = result === '1/2-1/2' ? t('chess.draw')
        : (game.side === 'white' && result === '1-0') || (game.side === 'black' && result === '0-1') ? t('chess.win') : t('chess.lose');
      setCommentary(`**${t('chess.over')}**: ${message} ${result || ''}${game.draw_reason ? ` — ${game.draw_reason}` : ''}`);
    }
  }

  function playerBar(game, position, which) {
    const topIsBlack = !C.flipped;
    const isTop = which === 'top';
    const colour = isTop ? (topIsBlack ? 'black' : 'white') : (topIsBlack ? 'white' : 'black');
    const name = colour === 'white' ? game.white : game.black;
    const captured = (game.captured || {})[colour === 'white' ? 'black' : 'white'] || [];
    const glyphs = captured.map((p) => GLYPH[(colour === 'white' ? 'b' : 'w') + p.toUpperCase()] || '').join('');
    const active = position.turn === colour && game.status === 'active';
    return `<div class="player-bar ${active ? 'active' : ''}">
      <span style="font-size:20px">${colour === 'white' ? '♔' : '♚'}</span>
      <div><div class="nm">${esc(name)}</div>
        <div class="muted small">${active ? t('chess.yourTurn') : ''}</div></div>
      <span class="cap">${glyphs}</span></div>`;
  }

  function drawBoard(position, kingInCheck, lastUci) {
    const board = document.getElementById('board');
    if (!board) return;
    const lastFrom = lastUci ? lastUci.slice(0, 2) : '';
    const lastTo = lastUci ? lastUci.slice(2, 4) : '';
    const cells = [];
    for (let i = 0; i < 64; i++) {
      const visualRank = Math.floor(i / 8);
      const visualFile = i % 8;
      const rank = C.flipped ? 7 - visualRank : visualRank;
      const file = C.flipped ? 7 - visualFile : visualFile;
      const piece = position.board[rank][file];
      const isLight = (rank + file) % 2 === 0;
      const name = squareName(file, rank);
      const isLast = name === lastFrom || name === lastTo;
      const isCheck = kingInCheck && kingInCheck.r === rank && kingInCheck.f === file;
      cells.push(`<div class="sq ${isLight ? 'light' : 'dark'} ${isLast ? 'last' : ''} ${isCheck ? 'check' : ''}"
        data-sq="${name}" data-r="${rank}" data-f="${file}">
        ${visualFile === 0 ? `<span class="coord rank">${8 - rank}</span>` : ''}
        ${visualRank === 7 ? `<span class="coord file">${FILES[file]}</span>` : ''}
        ${piece ? `<span class="pc ${piece[0]}">${GLYPH[piece]}</span>` : ''}</div>`);
    }
    board.innerHTML = cells.join('');
    wireBoard(board, position);
  }

  function wireBoard(board, position) {
    let selected = null;
    let dragging = null;
    let ghost = null;
    const targets = new Map();

    const legal = (C.game && C.game.legal_moves) || [];

    function clearMarks() {
      board.querySelectorAll('.sq').forEach((sq) => {
        sq.classList.remove('sel');
        const dot = sq.querySelector('.dot, .ring');
        if (dot) dot.remove();
      });
      targets.clear();
    }

    function markMoves(from) {
      const matching = legal.filter((san) => sanMatches(san, from, position));
      matching.forEach((san) => {
        const to = sanTarget(san);
        if (!to) return;
        targets.set(to, san);
        const sq = board.querySelector(`[data-sq="${to}"]`);
        if (!sq) return;
        const capture = san.includes('x');
        sq.insertAdjacentHTML('beforeend', capture ? '<span class="ring"></span>' : '<span class="dot"></span>');
      });
    }

    board.querySelectorAll('.sq').forEach((sq) => {
      sq.addEventListener('click', () => {
        const name = sq.dataset.sq;
        if (selected && targets.has(name)) {
          submit(targets.get(name));
          return;
        }
        clearMarks();
        const piece = position.board[Number(sq.dataset.r)][Number(sq.dataset.f)];
        if (!piece) { selected = null; return; }
        const myColour = C.game.side === 'random' ? position.turn : C.game.side;
        if (C.game.mode === 'engine' && piece[0] !== (myColour === 'white' ? 'w' : 'b')) return;
        if (C.game.status !== 'active') return;
        selected = name;
        sq.classList.add('sel');
        markMoves(name);
      });

      sq.addEventListener('pointerdown', (ev) => {
        const piece = position.board[Number(sq.dataset.r)][Number(sq.dataset.f)];
        if (!piece || C.game.status !== 'active') return;
        const myColour = C.game.mode === 'engine' ? C.game.side : position.turn;
        if (C.game.mode === 'engine' && piece[0] !== (myColour === 'white' ? 'w' : 'b')) return;
        if (ev.pointerType === 'mouse' && ev.button !== 0) return;
        clearMarks();
        selected = sq.dataset.sq;
        sq.classList.add('sel');
        markMoves(sq.dataset.sq);
        dragging = { from: sq.dataset.sq, piece };
        ghost = document.createElement('div');
        ghost.className = 'ghost-piece ' + piece[0];
        ghost.textContent = GLYPH[piece];
        ghost.style.left = `${ev.clientX}px`;
        ghost.style.top = `${ev.clientY}px`;
        document.body.appendChild(ghost);
        board.classList.add('dragging');
        sq.querySelector('.pc').style.opacity = '.25';
        ev.preventDefault();
      });
    });

    document.addEventListener('pointermove', (ev) => {
      if (!ghost) return;
      ghost.style.left = `${ev.clientX}px`;
      ghost.style.top = `${ev.clientY}px`;
    });

    document.addEventListener('pointerup', (ev) => {
      if (!dragging) return;
      const element = document.elementFromPoint(ev.clientX, ev.clientY);
      const sq = element && element.closest ? element.closest('.sq') : null;
      const target = sq && sq.dataset.sq;
      if (target && targets.has(target) && target !== dragging.from) {
        submit(targets.get(target));
      } else if (dragging) {
        const origin = board.querySelector(`[data-sq="${dragging.from}"] .pc`);
        if (origin) origin.style.opacity = '1';
      }
      if (ghost) { ghost.remove(); ghost = null; }
      board.classList.remove('dragging');
      dragging = null;
    });

    function submit(san) {
      clearMarks();
      selected = null;
      if (C.busy) return;
      C.busy = true;
      setCommentary(`<span class="typing"><i></i><i></i><i></i></span> ${t('chess.thinking')}`);
      API.chess.move(C.game.id, san)
        .then((game) => {
          C.game = game;
          C.analysis = null;
          paint();
          const annotation = game.annotation;
          if (annotation && annotation.grade_label) {
            setCommentary(`**${esc(annotation.san)}** — ${esc(annotation.grade_label)}` +
              (annotation.best && annotation.grade !== 'best' ? ` · ${global.I18N.lang === 'ru' ? 'сильнее было' : 'stronger was'} \`${esc(annotation.best)}\`` : ''));
          }
          if (game.status !== 'active') {
            API.chess.review(game.id, global.I18N.lang).then((review) => {
              paintReview(review);
            }).catch(() => {});
          }
        })
        .catch((e) => {
          global.App.toast('err', t('common.error'), e.message);
          setCommentary(`<span class="muted">${esc(e.message)}</span>`);
        })
        .finally(() => { C.busy = false; });
    }
  }

  function sanTarget(san) {
    const cleaned = san.replace(/[+#]?[!?]*$/, '');
    const match = cleaned.match(/([a-h][1-8])$/);
    if (match) return match[1];
    const castle = cleaned.match(/^O-O(-O)?$/);
    return castle ? null : null;
  }

  function sanMatches(san, from, position) {
    const cleaned = san.replace(/[+#]+$/, '').replace(/[!?]+$/, '');
    if (/^O-O(-O)?$/.test(cleaned)) {
      const rank = position.turn === 'white' ? '1' : '8';
      const kingFile = FILES.indexOf('e');
      const kingSq = 'e' + rank;
      return from === kingSq;
    }
    const target = sanTarget(cleaned);
    if (!target) return false;
    const pieceLetter = /^[KQRBN]/.test(cleaned) ? cleaned[0] : 'P';
    const disambiguation = cleaned.slice(1, cleaned.length - target.length - (cleaned.includes('x') ? 1 : 0));
    const fileHint = disambiguation.match(/[a-h]/);
    const rankHint = disambiguation.match(/[1-8]/);
    const fromFile = from[0];
    const fromRank = from[1];
    if (fileHint && fileHint[0] !== fromFile) return false;
    if (rankHint && rankHint[0] !== fromRank) return false;
    const piece = position.board[8 - Number(fromRank)][FILES.indexOf(fromFile)];
    if (!piece) return false;
    return piece[1] === pieceLetter;
  }

  function drawMoves(game) {
    const box = document.getElementById('moveList');
    if (!box) return;
    const moves = game.moves || [];
    const annotations = game.annotations || [];
    if (!moves.length) {
      box.innerHTML = `<div class="muted small" style="padding:14px">${global.I18N.lang === 'ru' ? 'Ходов пока нет' : 'No moves yet'}</div>`;
      return;
    }
    const rows = [];
    for (let i = 0; i < moves.length; i += 2) {
      const white = moves[i];
      const black = moves[i + 1];
      const wGrade = (annotations[i] || {}).grade || '';
      const bGrade = (annotations[i + 1] || {}).grade || '';
      rows.push(`<div class="move-row">
        <div class="n">${Math.floor(i / 2) + 1}.</div>
        <div data-i="${i}">${esc(white)}${wGrade ? `<span class="grade ${wGrade}" title="${esc(wGrade)}"></span>` : ''}</div>
        <div data-i="${i + 1}">${black ? esc(black) + (bGrade ? `<span class="grade ${bGrade}" title="${esc(bGrade)}"></span>` : '') : ''}</div>
      </div>`);
    }
    box.innerHTML = rows.join('');
    box.scrollTop = box.scrollHeight;
  }

  function setCommentary(html) {
    const node = document.getElementById('analysisText');
    if (node) node.innerHTML = global.Views.md(html);
  }

  function paintAnalysis(analysis) {
    setCommentary(analysis.commentary || '');
    const extra = document.getElementById('analysisExtra');
    if (!extra) return;
    const evaluation = analysis.evaluation || {};
    const lines = (evaluation.lines || []).map((line) => `
      <div class="eval-line"><span class="sc">${esc(line.assessment || '')}</span>
        <span class="pv">${esc((line.moves || []).join(' '))}</span></div>`).join('');
    const plans = (analysis.plans || []).map((plan, i) =>
      `<div class="plan-item"><span class="n">${i + 1}</span><span>${esc(plan)}</span></div>`).join('');
    extra.innerHTML = `
      <div class="divider"></div>
      <div class="muted small" style="letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px">${t('chess.eval')}</div>
      ${lines}
      ${plans ? `<div class="divider"></div>
        <div class="muted small" style="letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px">${t('chess.plans')}</div>${plans}` : ''}
      ${analysis.structure ? `<div class="divider"></div>
        <dl class="kv">
          <dt>${global.I18N.lang === 'ru' ? 'Стадия' : 'Phase'}</dt><dd>${esc(analysis.structure.phase)}</dd>
          <dt>${global.I18N.lang === 'ru' ? 'Открытые линии' : 'Open files'}</dt><dd>${esc((analysis.structure.open_files || []).join(' ') || '—')}</dd>
          <dt>${global.I18N.lang === 'ru' ? 'Пара слонов' : 'Bishop pair'}</dt><dd>${analysis.structure.bishop_pair.white ? '♔︎ ' + ico('check') : '♔︎ ' + ico('x')} / ${analysis.structure.bishop_pair.black ? '♚︎ ' + ico('check') : '♚︎ ' + ico('x')}</dd>
        </dl>` : ''}`;
    updateEvalBar(evaluation);
  }

  function updateEvalBar(evaluation) {
    const bar = document.querySelector('#evalBar span');
    if (!bar) return;
    let share = 50;
    if (evaluation && evaluation.mate) {
      share = evaluation.mate > 0 ? (C.flipped ? 4 : 96) : (C.flipped ? 96 : 4);
    } else if (evaluation && typeof evaluation.cp === 'number') {
      share = 50 + 50 * (2 / (1 + Math.exp(-evaluation.cp / 320)) - 1);
      share = Math.max(3, Math.min(97, share));
      if (C.flipped) share = 100 - share;
    }
    bar.style.height = `${share}%`;
  }

  function paintReview(review) {
    const box = document.getElementById('analysisExtra');
    if (!box) return;
    const counts = review.counts || {};
    const labels = { blunder: 'Зевок', mistake: 'Ошибка', inaccuracy: 'Неточность', good: 'Хорошо', excellent: 'Отлично', best: 'Точно' };
    box.innerHTML = `
      <div class="divider"></div>
      <div class="muted small" style="letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px">${t('chess.review')}</div>
      <div class="stat-card" style="margin-bottom:12px"><div class="lbl">${t('chess.accuracy')}</div>
        <div class="val">${review.accuracy}%</div></div>
      <div class="chips">${Object.entries(counts).filter(([, v]) => v).map(([k, v]) =>
        `<span class="tag">${labels[k] || k} · ${v}</span>`).join('')}</div>
      <div class="divider"></div>
      <div class="txt">${global.Views.md(review.summary || '')}</div>`;
    setCommentary(review.summary || '');
  }

  function wireActions(game) {
    const busyGuard = (fn) => async (ev) => {
      if (C.busy) return;
      const btn = ev.currentTarget;
      btn.classList.add('loading'); btn.disabled = true;
      try { await fn(); } catch (e) { global.App.toast('err', t('common.error'), e.message); }
      finally { btn.classList.remove('loading'); btn.disabled = false; }
    };

    const hint = document.getElementById('chHint');
    if (hint) hint.addEventListener('click', busyGuard(async () => {
      setCommentary(`<span class="typing"><i></i><i></i><i></i></span>`);
      const data = await API.chess.hint(game.id);
      const board = document.getElementById('board');
      if (board && data.move) {
        const target = sanTarget(data.move.replace(/[+#]/g, ''));
        if (target) {
          const sq = board.querySelector(`[data-sq="${target}"]`);
          if (sq) { sq.classList.add('hint'); setTimeout(() => sq.classList.remove('hint'), 2400); }
        }
      }
      setCommentary(`**${data.move}** — ${data.commentary || data.assessment}`);
      document.getElementById('analysisExtra').innerHTML = `
        <div class="divider"></div><div class="eval-line"><span class="sc">${esc(data.assessment)}</span>
        <span class="pv">${esc((data.line || []).join(' '))}</span></div>`;
    }));

    const analyse = document.getElementById('chAnalyse');
    if (analyse) analyse.addEventListener('click', busyGuard(async () => {
      setCommentary(`<span class="typing"><i></i><i></i><i></i></span>`);
      const data = await API.chess.analyse(game.id, global.I18N.lang, 3);
      C.analysis = data;
      paintAnalysis(data);
    }));

    const review = document.getElementById('chReview');
    if (review) review.addEventListener('click', busyGuard(async () => {
      const data = await API.chess.review(game.id, global.I18N.lang);
      paintReview(data);
    }));

    const undo = document.getElementById('chUndo');
    if (undo) undo.addEventListener('click', busyGuard(async () => {
      C.game = await API.chess.undo(game.id);
      C.analysis = null;
      paint();
    }));

    const pgn = document.getElementById('chPgn');
    if (pgn) pgn.addEventListener('click', async () => {
      const res = await fetch(API.chess.pgn(game.id), { headers: API.headers() });
      const text = await res.text();
      const blob = new Blob([text], { type: 'application/x-chess-pgn' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `${game.id}.pgn`; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 3000);
    });

    const resign = document.getElementById('chResign');
    if (resign) resign.addEventListener('click', busyGuard(async () => {
      if (!confirm(global.I18N.lang === 'ru' ? 'Сдаться в этой партии?' : 'Resign this game?')) return;
      C.game = await API.chess.resign(game.id);
      paint();
    }));
  }

  global.Chess = { render, parseFen, GLYPH };
})(window);
