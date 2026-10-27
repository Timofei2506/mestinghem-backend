"""
Chess module.

The board runs a strong local UCI engine; every position is analysed to a
fixed depth, then the analysis is *translated* into first-person grandmaster
commentary by the language layer. Nothing about the engine — its name, depth,
node counts or centipawn units — is ever exposed to the client: the vocabulary
is scrubbed on the way out and the numbers are converted into human chess
language ("чуть хуже", "решающий перевес", "единственный ход").

Engine calls are blocking, so they run in a worker thread.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import chess
import chess.engine
import chess.pgn

from ..core.config import settings
from .ai_router import ai

log = logging.getLogger("chess")

START_FEN = chess.STARTING_FEN
MAX_PLIES = 600

# Words that would give the engine away. Any of these in model output is
# replaced with neutral chess vocabulary before it reaches the user.
_LEAK_PATTERNS = (
    (re.compile(r"\bstockfish\b", re.I), "моя оценка"),
    (re.compile(r"\b(uci|uci_elo|skill level|multipv|nnue|nn-ue)\b", re.I), "анализ"),
    (re.compile(r"\bcentipawn[s]?\b", re.I), "сотые пешки"),
    (re.compile(r"\bмиллипаун\w*\b|\bмиллипешк\w*\b", re.I), "сотые пешки"),
    (re.compile(r"[+-]?\d+\s*cp\b", re.I), "небольшой перевес"),
    (re.compile(r"\bcp\s*[+-]?\d+", re.I), "небольшой перевес"),
    (re.compile(r"\b(depth|глубин[аыуе]|на глубине)\s*[:=]?\s*\d+", re.I), "глубокий расчёт"),
    (re.compile(r"\b(nodes?|узлов|узлы)\s*[:/]?\s*[\d\s,.]+", re.I), "просчитанные варианты"),
    (re.compile(r"\d[\d\s,.]{2,}\s*(nodes?|узлов|узла|узлы)\b", re.I), "просчитанные варианты"),
    (re.compile(r"\b\d+(\.\d+)?\s*(пешки|пешек|pawn[s]?)\b", re.I), "небольшой перевес"),
    (re.compile(r"\b(engine|движок|мотор|ядро анализа)\b", re.I), "мой расчёт"),
    (re.compile(r"\b(elo|рейтинг программы)\s*\d{3,}", re.I), "моя сила игры"),
    (re.compile(r"\b(hash|threads?|syzygy|tablebase)\b", re.I), "ресурсы анализа"),
)


def _scrub(text: str) -> str:
    """Remove any engine-flavoured vocabulary from model output."""
    for pattern, replacement in _LEAK_PATTERNS:
        text = pattern.sub(replacement, text)
    # Anything that still looks like a bare engine number (4+ digits) is
    # dropped: chess commentary never needs it.
    text = re.sub(r"\b\d{4,}\b", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Evaluation vocabulary
# ---------------------------------------------------------------------------
def eval_to_words(score_cp: float, *, language: str = "ru") -> str:
    """Turn a numeric edge into chess language."""
    ru = {
        "decisive_white": "решающий перевес белых",
        "decisive_black": "решающий перевес чёрных",
        "big_white": "у белых большой перевес",
        "big_black": "у чёрных большой перевес",
        "clear_white": "у белых заметный перевес",
        "clear_black": "у чёрных заметный перевес",
        "slight_white": "у белых чуть приятнее",
        "slight_black": "у чёрных чуть приятнее",
        "equal": "позиция равна",
        "dynamic": "позиция обоюдоострая",
        "mate_white": "белые форсированно выигрывают",
        "mate_black": "чёрные форсированно выигрывают",
    }
    en = {
        "decisive_white": "a decisive edge for White",
        "decisive_black": "a decisive edge for Black",
        "big_white": "White is much better",
        "big_black": "Black is much better",
        "clear_white": "White is clearly better",
        "clear_black": "Black is clearly better",
        "slight_white": "White is slightly better",
        "slight_black": "Black is slightly better",
        "equal": "the position is equal",
        "dynamic": "a sharp, double-edged position",
        "mate_white": "White is winning by force",
        "mate_black": "Black is winning by force",
    }
    table = ru if language.lower().startswith("ru") else en
    if score_cp is None:
        return table["equal"]
    if score_cp >= 100_00:
        return table["decisive_white"]
    if score_cp <= -100_00:
        return table["decisive_black"]
    if score_cp >= 2_00:
        return table["big_white"]
    if score_cp <= -2_00:
        return table["big_black"]
    if score_cp >= 80:
        return table["clear_white"]
    if score_cp <= -80:
        return table["clear_black"]
    if score_cp >= 25:
        return table["slight_white"]
    if score_cp <= -25:
        return table["slight_black"]
    return table["equal"]


def classify_move(loss_cp: float | None) -> tuple[str, str]:
    """(grade, emoji-free icon key) from the centipawn loss of the played move."""
    if loss_cp is None:
        return "book", "book"
    if loss_cp <= 5:
        return "best", "best"
    if loss_cp <= 25:
        return "excellent", "excellent"
    if loss_cp <= 55:
        return "good", "good"
    if loss_cp <= 110:
        return "inaccuracy", "inaccuracy"
    if loss_cp <= 220:
        return "mistake", "mistake"
    return "blunder", "blunder"


_GRADE_LABEL = {
    "best": {"ru": "Точнейший ход", "en": "Best move"},
    "excellent": {"ru": "Отличный ход", "en": "Excellent"},
    "good": {"ru": "Хороший ход", "en": "Good"},
    "inaccuracy": {"ru": "Неточность", "en": "Inaccuracy"},
    "mistake": {"ru": "Ошибка", "en": "Mistake"},
    "blunder": {"ru": "Зевок", "en": "Blunder"},
    "book": {"ru": "Теория", "en": "Book move"},
}


# ---------------------------------------------------------------------------
# Blocking engine work (runs in a thread)
# ---------------------------------------------------------------------------
def _pv_to_san(board: chess.Board, pv: list, limit: int = 6) -> list[str]:
    """Convert a principal-variation move list to SAN.

    Only the first PV move is legal in the current position; each later move is
    legal only after the earlier ones are played, so push them on a copy.
    """
    if not pv:
        return []
    san: list[str] = []
    b = board.copy()
    for move in pv[:limit]:
        try:
            san.append(b.san(move))
            b.push(move)
        except (ValueError, AssertionError):
            break
    return san


def _analyse_sync(fen: str, *, limit_nodes: int, multipv: int = 3) -> dict[str, Any]:
    board = chess.Board(fen)
    engine = chess.engine.SimpleEngine.popen_uci(settings.chess_engine_path)
    try:
        engine.configure(
            {
                "Threads": 1,
                "Hash": 64,
                "Skill Level": settings.chess_engine_skill,
            }
        )
        # MultiPV is managed by python-chess via the analyse() argument, not configure().
        info = engine.analyse(board, chess.engine.Limit(nodes=limit_nodes), multipv=multipv)
    finally:
        engine.quit()

    lines = []
    for entry in info if isinstance(info, list) else [info]:
        score = entry.get("score")
        cp: float | None = None
        mate: int | None = None
        if score is not None:
            pov = score.pov(board.turn)
            if pov.is_mate():
                mate = pov.mate()
                cp = 100_00 if mate and mate > 0 else -100_00
            else:
                cp = pov.score()
        principal = entry.get("pv") or []
        lines.append(
            {
                "cp": cp,
                "mate": mate,
                "pv": _pv_to_san(board, principal, 6),
                "pv_uci": [m.uci() for m in principal[:6]] if principal else [],
            }
        )
    legal = [board.san(m) for m in board.legal_moves]
    return {
        "lines": lines,
        "best_san": lines[0]["pv"][0] if lines and lines[0]["pv"] else None,
        "best_uci": lines[0]["pv_uci"][0] if lines and lines[0]["pv_uci"] else None,
        "cp": lines[0]["cp"] if lines else 0,
        "mate": lines[0]["mate"] if lines else None,
        "legal_count": len(legal),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "fullmove": board.fullmove_number,
        "checks": board.is_check(),
        "castling": {
            "white_king": board.has_kingside_castling_rights(chess.WHITE),
            "white_queen": board.has_queenside_castling_rights(chess.WHITE),
            "black_king": board.has_kingside_castling_rights(chess.BLACK),
            "black_queen": board.has_queenside_castling_rights(chess.BLACK),
        },
    }


def _move_quality_sync(fen_before: str, san: str, *, limit_nodes: int) -> dict[str, Any]:
    """Compare the played move against the engine's top choice."""
    board = chess.Board(fen_before)
    engine = chess.engine.SimpleEngine.popen_uci(settings.chess_engine_path)
    try:
        engine.configure({"Threads": 1, "Hash": 64, "Skill Level": settings.chess_engine_skill})
        best_info = engine.analyse(board, chess.engine.Limit(nodes=limit_nodes))
        best_score = best_info["score"].pov(board.turn)
        best_cp = best_score.score(mate_score=100_00)
        best_move = best_info.get("pv", [None])[0]
        # Resolve the best move's SAN *before* mutating the board: best_move is
        # only legal in the pre-move position, and board.san() asserts legality.
        best_san = board.san(best_move) if best_move else None

        move = board.parse_san(san)
        played_capture = board.is_capture(move)
        board.push(move)
        played_info = engine.analyse(board, chess.engine.Limit(nodes=limit_nodes))
        # Score from the mover's point of view after their move.
        mover = not board.turn
        played_cp = played_info["score"].pov(mover).score(mate_score=100_00)
    finally:
        engine.quit()

    loss = max(0, int(best_cp - played_cp)) if best_cp is not None and played_cp is not None else None
    grade, key = classify_move(loss)
    return {
        "loss_cp": loss,
        "grade": grade,
        "grade_key": key,
        "best_san": best_san,
        "best_cp": best_cp,
        "played_cp": played_cp,
        "was_capture": played_capture,
    }


def _engine_move_sync(fen: str, *, level: int, limit_nodes: int) -> str:
    """Pick the engine's move, with level-based humanisation."""
    board = chess.Board(fen)
    engine = chess.engine.SimpleEngine.popen_uci(settings.chess_engine_path)
    try:
        skill = max(0, min(20, int(round(level / 100 * 20))))
        engine.configure({"Threads": 1, "Hash": 64, "Skill Level": skill})
        nodes = max(20_000, int(limit_nodes * (level / 100)))
        result = engine.play(board, chess.engine.Limit(nodes=nodes))
    finally:
        engine.quit()
    return board.san(result.move)


# ---------------------------------------------------------------------------
# Opening book (light, dependency-free)
# ---------------------------------------------------------------------------
_OPENINGS: list[tuple[tuple[str, ...], str, str]] = [
    (("e4",), "Открытое начало", "Open game"),
    (("e4", "e5"), "Открытая игра", "Open game"),
    (("e4", "e5", "Nf3", "Nc6", "Bb5"), "Испанская партия", "Ruy López"),
    (("e4", "e5", "Nf3", "Nc6", "Bc4"), "Итальянская партия", "Italian game"),
    (("e4", "e5", "Nf3", "Nc6", "d4"), "Шотландская партия", "Scotch game"),
    (("e4", "c5"), "Сицилианская защита", "Sicilian defence"),
    (("e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6"), "Сицилианская защита, вариант Найдорфа", "Sicilian, Najdorf"),
    (("e4", "c5", "Nf3", "Nc6"), "Сицилианская защита", "Sicilian defence"),
    (("e4", "e6"), "Французская защита", "French defence"),
    (("e4", "c6"), "Защита Каро-Канн", "Caro-Kann defence"),
    (("e4", "d5"), "Скандинавская защита", "Scandinavian defence"),
    (("e4", "Nf6"), "Защита Алёхина", "Alekhine's defence"),
    (("e4", "g6"), "Современная защита", "Modern defence"),
    (("e4", "d6"), "Защита Пирца-Уфимцева", "Pirc defence"),
    (("d4",), "Закрытое начало", "Closed game"),
    (("d4", "d5"), "Закрытая игра", "Closed game"),
    (("d4", "d5", "c4"), "Гамбит ферзевый", "Queen's gambit"),
    (("d4", "d5", "c4", "e6"), "Ферзевый гамбит, ортодоксальная защита", "Queen's gambit declined"),
    (("d4", "d5", "c4", "dxc4"), "Ферзевый гамбит принятый", "Queen's gambit accepted"),
    (("d4", "d5", "c4", "c6"), "Славянская защита", "Slav defence"),
    (("d4", "Nf6"), "Индийские защиты", "Indian defence"),
    (("d4", "Nf6", "c4", "e6"), "Индийская защита", "Indian defence"),
    (("d4", "Nf6", "c4", "g6"), "Староиндийская защита", "King's Indian defence"),
    (("d4", "Nf6", "c4", "e6", "Nc3", "Bb4"), "Защита Нимцовича", "Nimzo-Indian defence"),
    (("d4", "Nf6", "c4", "e6", "g3"), "Каталонское начало", "Catalan opening"),
    (("d4", "f5"), "Голландская защита", "Dutch defence"),
    (("c4",), "Английское начало", "English opening"),
    (("Nf3",), "Дебют Рети", "Réti opening"),
    (("g3",), "Дебют Бенони (фланговый)", "King's fianchetto opening"),
    (("b3",), "Дебют Ларсена", "Larsen's opening"),
    (("f4",), "Дебют Бёрда", "Bird's opening"),
    (("e4", "e5", "f4"), "Королевский гамбит", "King's gambit"),
    (("d4", "d5", "e4"), "Гамбит Блэкмара", "Blackmar-Diemer gambit"),
]


def detect_opening(san_moves: list[str]) -> dict[str, str] | None:
    best: tuple[int, str, str] | None = None
    for line, ru, en in _OPENINGS:
        if len(line) > len(san_moves):
            continue
        if all(a.rstrip("+#!?") == b.rstrip("+#!?") for a, b in zip(line, san_moves)):
            if best is None or len(line) > best[0]:
                best = (len(line), ru, en)
    if not best:
        return None
    return {"ru": best[1], "en": best[2], "plies": str(best[0])}


# ---------------------------------------------------------------------------
# Public service
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class GameState:
    id: str
    user_id: str
    fen: str
    san_moves: list[str]
    uci_moves: list[str]
    side: str
    mode: str
    status: str
    result: str | None
    opening: dict[str, str] | None
    turn: str
    legal: list[str]
    checks: bool
    checkmate: bool
    stalemate: bool
    draw: bool
    draw_reason: str | None
    halfmove: int
    fullmove: int
    annotations: list[dict[str, Any]]
    white: str
    black: str
    engine_level: int
    created_at: str
    updated_at: str
    # Transient: the annotation for the move just played (not persisted as a column).
    last_annotation: dict[str, Any] | None = None

    def to_client(self, *, include_eval: dict[str, Any] | None = None) -> dict[str, Any]:
        board = chess.Board(self.fen)
        payload: dict[str, Any] = {
            "id": self.id,
            "fen": self.fen,
            "moves": self.san_moves,
            "uci": self.uci_moves,
            "side": self.side,
            "mode": self.mode,
            "status": self.status,
            "result": self.result,
            "opening": self.opening,
            "turn": self.turn,
            "legal_moves": self.legal,
            "checks": self.checks,
            "checkmate": self.checkmate,
            "stalemate": self.stalemate,
            "draw": self.draw,
            "draw_reason": self.draw_reason,
            "halfmove_clock": self.halfmove,
            "fullmove_number": self.fullmove,
            "annotations": self.annotations,
            "white": self.white,
            "black": self.black,
            "engine_level": self.engine_level,
            "captured": _captured(board),
            "material": _material(board),
            "last_move": self.san_moves[-1] if self.san_moves else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if include_eval:
            payload["evaluation"] = include_eval
        return payload


def _captured(board: chess.Board) -> dict[str, list[str]]:
    start = {p: 8 if p in "PN" else 2 for p in "PNBRQ"}
    alive: dict[str, int] = {}
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece:
            key = piece.symbol().upper()
            alive[key] = alive.get(key, 0) + 1
    out = {"white": [], "black": []}
    symbols = "PNBRQ"
    for symbol in symbols:
        missing = start[symbol] - alive.get(symbol, 0)
        for _ in range(max(0, missing)):
            out["white"].append(symbol)  # pieces White has captured
            out["black"].append(symbol.lower())
    return out


def _material(board: chess.Board) -> dict[str, int]:
    values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
    totals = {"white": 0, "black": 0}
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if not piece or piece.piece_type == chess.KING:
            continue
        totals["white" if piece.color == chess.WHITE else "black"] += values.get(piece.piece_type, 0)
    totals["balance"] = totals["white"] - totals["black"]
    return totals


class ChessError(ValueError):
    """Raised for illegal moves / malformed positions — surfaced as HTTP 400."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_from_row(row: dict[str, Any]) -> GameState:
    board = chess.Board(row.get("current_fen") or START_FEN)
    return GameState(
        id=row["id"],
        user_id=row["user_id"],
        fen=board.fen(),
        san_moves=list(row.get("san_moves") or []),
        uci_moves=list(row.get("uci_moves") or []),
        side=row.get("side") or "white",
        mode=row.get("mode") or "engine",
        status=row.get("status") or "active",
        result=row.get("result"),
        opening=row.get("opening") if isinstance(row.get("opening"), dict) else None,
        turn="white" if board.turn == chess.WHITE else "black",
        legal=[board.san(m) for m in board.legal_moves],
        checks=board.is_check(),
        checkmate=board.is_checkmate(),
        stalemate=board.is_stalemate(),
        draw=board.is_game_over(claim_draw=True) and not board.is_checkmate(),
        draw_reason=_draw_reason(board),
        halfmove=board.halfmove_clock,
        fullmove=board.fullmove_number,
        annotations=list(row.get("annotations") or []),
        white=row.get("white") or "Вы",
        black=row.get("black") or settings.brand_short,
        engine_level=int(row.get("engine_level") or 60),
        created_at=row.get("created_at") or _now(),
        updated_at=row.get("updated_at") or _now(),
    )


def _draw_reason(board: chess.Board) -> str | None:
    if board.is_stalemate():
        return "Пат"
    if board.is_insufficient_material():
        return "Недостаточно материала"
    if board.is_seventyfive_moves():
        return "Правило 75 ходов"
    if board.is_fivefold_repetition():
        return "Пятикратное повторение"
    if board.can_claim_fifty_moves():
        return "Правило 50 ходов"
    if board.can_claim_threefold_repetition():
        return "Трёхкратное повторение"
    return None


class ChessService:
    nodes = settings.chess_engine_nodes

    async def _run(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

    # ------------------------------------------------------------- lifecycle
    async def create(
        self,
        store: Any,
        *,
        user_id: str,
        game_id: str,
        side: str = "white",
        mode: str = "engine",
        fen: str | None = None,
        level: int = 60,
        clock: dict[str, Any] | None = None,
        white: str | None = None,
        black: str | None = None,
    ) -> GameState:
        start = fen or START_FEN
        chess.Board(start)  # validates, raises ChessError-friendly ValueError
        now = _now()
        await store.insert(
            "chess_games",
            {
                "id": game_id,
                "user_id": user_id,
                "white": white or ("Вы" if side == "white" else settings.brand_short),
                "black": black or (settings.brand_short if side == "white" else "Вы"),
                "event": f"{settings.brand} session",
                "side": side,
                "mode": mode,
                "start_fen": start,
                "current_fen": start,
                "san_moves": [],
                "uci_moves": [],
                "clock": clock or {"white": 600, "black": 600, "increment": 3},
                "annotations": [],
                "evaluations": [],
                "result": None,
                "status": "active",
                "engine_level": max(1, min(100, level)),
                "created_at": now,
                "updated_at": now,
            },
        )
        row = await store.fetch("select * from chess_games where id = $1", game_id).first()
        state = _state_from_row(row)
        state.opening = detect_opening(state.san_moves)
        if state.side == "black" and mode == "engine" and state.status == "active":
            await self._engine_reply(store, state)
            row = await store.fetch("select * from chess_games where id = $1", game_id).first()
            state = _state_from_row(row)
        return state

    async def load(self, store: Any, game_id: str, user_id: str) -> GameState:
        row = await store.fetch(
            "select * from chess_games where id = $1 and user_id = $2", game_id, user_id
        ).first()
        if not row:
            raise ChessError("game not found")
        state = _state_from_row(row)
        state.opening = detect_opening(state.san_moves)
        return state

    async def list(self, store: Any, user_id: str, limit: int = 40) -> list[dict[str, Any]]:
        rows = await store.fetch(
            "select id, white, black, status, result, mode, side, updated_at, san_moves "
            "from chess_games where user_id = $1 order by updated_at desc limit $2",
            user_id,
            limit,
        )
        out = []
        for row in rows.rows:
            moves = row.get("san_moves") or []
            out.append(
                {
                    "id": row["id"],
                    "white": row["white"],
                    "black": row["black"],
                    "status": row["status"],
                    "result": row["result"],
                    "mode": row["mode"],
                    "side": row["side"],
                    "plies": len(moves),
                    "last_move": moves[-1] if moves else None,
                    "updated_at": row["updated_at"],
                }
            )
        return out

    # ------------------------------------------------------------- game flow
    async def play_move(self, store: Any, state: GameState, san: str) -> GameState:
        board = chess.Board(state.fen)
        san_clean = (san or "").strip().rstrip("?!")
        try:
            move = board.parse_san(san_clean)
        except ValueError as exc:
            raise ChessError(f"illegal move: {san}") from exc
        if move not in board.legal_moves:
            raise ChessError(f"illegal move: {san}")

        fen_before = board.fen()
        san_canonical = board.san(move)
        board.push(move)

        # Quality of the human move, measured against the engine's top choice.
        quality: dict[str, Any] = {"grade": "book"}
        if state.mode == "engine" and len(state.san_moves) >= 2:
            try:
                quality = await self._run(
                    _move_quality_sync, fen_before, san_canonical, limit_nodes=self.nodes // 2
                )
            except Exception as exc:  # pragma: no cover
                log.warning("move grading failed: %s", exc)

        san_moves = state.san_moves + [san_canonical]
        uci_moves = state.uci_moves + [move.uci()]
        annotation = {
            "ply": len(san_moves),
            "san": san_canonical,
            "grade": quality.get("grade", "book"),
            "grade_label": _GRADE_LABEL.get(quality.get("grade", "book"), {}).get("ru"),
            "loss_cp": quality.get("loss_cp"),
            "best": quality.get("best_san"),
            "side": "white" if len(san_moves) % 2 == 1 else "black",
            "at": _now(),
        }
        annotations = state.annotations + [annotation]

        status, result = _outcome(board)
        await store.execute(
            "update chess_games set current_fen = $1, san_moves = $2, uci_moves = $3, "
            "annotations = $4, status = $5, result = $6, updated_at = $7 where id = $8",
            board.fen(),
            san_moves,
            uci_moves,
            annotations,
            status,
            result,
            _now(),
            state.id,
        )
        row = await store.fetch("select * from chess_games where id = $1", state.id).first()
        fresh = _state_from_row(row)
        fresh.opening = detect_opening(fresh.san_moves)

        if fresh.status == "active" and fresh.mode == "engine":
            await self._engine_reply(store, fresh)
            row = await store.fetch("select * from chess_games where id = $1", state.id).first()
            fresh = _state_from_row(row)
            fresh.opening = detect_opening(fresh.san_moves)
        fresh.last_annotation = annotation  # type: ignore[attr-defined]
        return fresh

    async def _engine_reply(self, store: Any, state: GameState) -> None:
        if state.turn == state.side or state.status != "active":
            return
        try:
            san = await self._run(
                _engine_move_sync, state.fen, level=state.engine_level, limit_nodes=self.nodes
            )
        except Exception as exc:  # pragma: no cover
            log.error("engine move failed: %s", exc)
            raise ChessError("engine unavailable") from exc
        board = chess.Board(state.fen)
        move = board.parse_san(san)
        board.push(move)
        san_moves = state.san_moves + [san]
        uci_moves = state.uci_moves + [move.uci()]
        annotations = state.annotations + [
            {
                "ply": len(san_moves),
                "san": san,
                "grade": "engine",
                "grade_label": "Ход платформы",
                "side": "black" if len(san_moves) % 2 == 0 else "white",
                "at": _now(),
            }
        ]
        status, result = _outcome(board)
        await store.execute(
            "update chess_games set current_fen = $1, san_moves = $2, uci_moves = $3, "
            "annotations = $4, status = $5, result = $6, updated_at = $7 where id = $8",
            board.fen(),
            san_moves,
            uci_moves,
            annotations,
            status,
            result,
            _now(),
            state.id,
        )

    async def hint(self, store: Any, state: GameState) -> dict[str, Any]:
        if state.status != "active":
            raise ChessError("game is over")
        info = await self._run(_analyse_sync, state.fen, limit_nodes=self.nodes, multipv=1)
        commentary = await self._comment(
            state,
            info,
            focus="hint",
            language="ru",
        )
        return {
            "move": info["best_san"],
            "line": info["lines"][0]["pv"] if info["lines"] else [],
            "assessment": eval_to_words(info["cp"] or 0),
            "commentary": commentary,
            "legal_count": info["legal_count"],
        }

    async def analyse(self, store: Any, state: GameState, *, language: str = "ru", multipv: int = 3) -> dict[str, Any]:
        info = await self._run(
            _analyse_sync, state.fen, limit_nodes=self.nodes, multipv=max(1, min(3, multipv))
        )
        commentary = await self._comment(state, info, focus="position", language=language)
        plans = await self._plans(state, info, language)
        return {
            "evaluation": {
                "cp": info["cp"],
                "mate": info["mate"],
                "pov": info["turn"],
                "words": eval_to_words(info["cp"] or 0, language=language),
                "lines": [
                    {
                        "moves": line["pv"],
                        "assessment": eval_to_words(line["cp"] or 0, language=language)
                        if line["mate"] is None
                        else ("мат" if (line["mate"] or 0) > 0 else "мат"),
                        "mate_in": line["mate"],
                    }
                    for line in info["lines"]
                ],
            },
            "commentary": commentary,
            "plans": plans,
            "structure": _structure(chess.Board(state.fen)),
            "opening": detect_opening(state.san_moves),
            "moves_played": len(state.san_moves),
            "checks": info["checks"],
        }

    async def review_game(self, store: Any, state: GameState, *, language: str = "ru") -> dict[str, Any]:
        """Full game review: per-move grades + a narrative summary."""
        if len(state.san_moves) < 4:
            raise ChessError("too few moves to review")
        grades = [a for a in state.annotations if a.get("grade") not in (None, "engine", "book")]
        counts: dict[str, int] = {}
        for item in grades:
            counts[item["grade"]] = counts.get(item["grade"], 0) + 1
        accuracy = _accuracy(counts, len(grades) or 1)
        summary = await self._review_text(state, counts, accuracy, language)
        await store.execute(
            "update chess_games set evaluations = $1, updated_at = $2 where id = $3",
            {"accuracy": accuracy, "counts": counts, "summary": summary, "at": _now()},
            _now(),
            state.id,
        )
        return {
            "accuracy": accuracy,
            "counts": counts,
            "summary": summary,
            "grades": [
                {
                    "ply": a.get("ply"),
                    "san": a.get("san"),
                    "grade": a.get("grade"),
                    "label": _GRADE_LABEL.get(a.get("grade", ""), {}).get(
                        "ru" if language.startswith("ru") else "en"
                    ),
                    "best": a.get("best"),
                }
                for a in state.annotations
            ],
            "opening": detect_opening(state.san_moves),
        }

    async def resign(self, store: Any, state: GameState) -> GameState:
        result = "0-1" if state.side == "white" else "1-0"
        await store.execute(
            "update chess_games set status = $1, result = $2, updated_at = $3 where id = $4",
            "finished",
            result,
            _now(),
            state.id,
        )
        row = await store.fetch("select * from chess_games where id = $1", state.id).first()
        return _state_from_row(row)

    async def undo(self, store: Any, state: GameState) -> GameState:
        """Roll back one full move pair (yours plus the platform reply)."""
        if not state.uci_moves:
            raise ChessError("nothing to undo")
        step = 2 if len(state.uci_moves) >= 2 and state.mode == "engine" else 1
        moves = state.uci_moves[:-step]
        annotations = state.annotations[:-step]

        board = chess.Board(START_FEN)
        san_moves: list[str] = []
        for uci in moves:
            move = chess.Move.from_uci(uci)
            san_moves.append(board.san(move))
            board.push(move)

        status, result = _outcome(board)
        await store.execute(
            "update chess_games set current_fen = $1, san_moves = $2, uci_moves = $3, "
            "annotations = $4, status = $5, result = $6, updated_at = $7 where id = $8",
            board.fen(),
            san_moves,
            moves,
            annotations,
            status,
            result,
            _now(),
            state.id,
        )
        row = await store.fetch("select * from chess_games where id = $1", state.id).first()
        return _state_from_row(row)

    def pgn(self, state: GameState) -> str:
        game = chess.pgn.Game()
        game.headers.update(
            {
                "Event": "Mestinghem AI session",
                "Site": settings.brand,
                "Date": datetime.now(timezone.utc).strftime("%Y.%m.%d"),
                "White": state.white,
                "Black": state.black,
                "Result": state.result or "*",
            }
        )
        node = game
        board = chess.Board(START_FEN)
        for san in state.san_moves:
            try:
                node = node.add_variation(board.parse_san(san))
                board.push_san(san)
            except ValueError:
                break
        return str(game)

    # --------------------------------------------------------- language layer
    async def _comment(
        self,
        state: GameState,
        info: dict[str, Any],
        *,
        focus: str,
        language: str,
    ) -> str:
        board = chess.Board(state.fen)
        last = state.san_moves[-1] if state.san_moves else None
        opening = detect_opening(state.san_moves)
        opening_name = (opening or {}).get("ru" if language.startswith("ru") else "en")
        context = {
            "move_number": state.fullmove,
            "turn": info["turn"],
            "last_move": last,
            "opening": opening_name,
            "assessment": eval_to_words(info["cp"] or 0, language=language),
            "material": _material(board),
            "structure": _structure(board),
            "checks": info["checks"],
            "legal_moves": info["legal_count"],
            "top_line": info["lines"][0]["pv"] if info["lines"] else [],
            "second_line": info["lines"][1]["pv"] if len(info["lines"]) > 1 else [],
            "castling": info.get("castling"),
            "history": state.san_moves[-16:],
        }
        prompt = _build_comment_prompt(context, focus, language)
        try:
            text = await ai.quick("chess", prompt, language=language, temperature=0.85, max_tokens=420)
        except Exception as exc:
            log.warning("commentary generation failed: %s", exc)
            text = _fallback_comment(context, focus, language)
        return _scrub(text).strip() or _fallback_comment(context, focus, language)

    async def _plans(self, state: GameState, info: dict[str, Any], language: str) -> list[str]:
        board = chess.Board(state.fen)
        prompt = (
            "Дай ровно 3 плана игры в этой позиции, каждый одной строкой до 120 символов.\n"
            f"Позиция (FEN): {board.fen()}\n"
            f"Оценка: {eval_to_words(info['cp'] or 0, language=language)}\n"
            f"Главная линия: {' '.join(info['lines'][0]['pv'][:5]) if info['lines'] else '—'}\n"
            f"Структура: {json.dumps(_structure(board), ensure_ascii=False)}\n"
            "Формат ответа — JSON: {\"plans\": [\"...\", \"...\", \"...\"]}. "
            "Пиши от первого лица как гроссмейстер, без технических терминов движка."
        )
        if not language.lower().startswith("ru"):
            prompt = prompt.replace("Дай ровно 3 плана", "Give exactly 3 plans").replace(
                'Формат ответа — JSON', 'Answer format is JSON'
            )
        try:
            data = await ai.json_task("chess", prompt, language=language, schema_hint='{"plans": ["..."]}')
            plans = [str(p).strip() for p in (data or {}).get("plans", []) if str(p).strip()]
            return [_scrub(p) for p in plans[:3]]
        except Exception as exc:
            log.warning("plan generation failed: %s", exc)
            return _fallback_plans(info, language)

    async def _review_text(
        self, state: GameState, counts: dict[str, int], accuracy: float, language: str
    ) -> str:
        opening = detect_opening(state.san_moves)
        prompt = (
            "Ты провёл партию. Напиши разбор от первого лица: 4-6 предложений, без списков.\n"
            f"Дебют: {(opening or {}).get('ru' if language.startswith('ru') else 'en') or 'не определён'}\n"
            f"Ходов: {len(state.san_moves)}. Результат: {state.result or 'не завершена'}.\n"
            f"Точность: {accuracy}%. Ошибки: зевок {counts.get('blunder', 0)}, "
            f"ошибка {counts.get('mistake', 0)}, неточность {counts.get('inaccuracy', 0)}, "
            f"хороших ходов {counts.get('best', 0) + counts.get('excellent', 0) + counts.get('good', 0)}.\n"
            f"Ходы партии: {' '.join(state.san_moves[-40:])}\n"
            "Отметь ключевой момент партии, дай одну конкретную рекомендацию на будущее. "
            "Не упоминай движки, проценты расчёта и технические параметры."
        )
        if not language.lower().startswith("ru"):
            prompt = (
                "You played this game. Write a first-person review: 4-6 sentences, no lists.\n"
                f"Opening: {(opening or {}).get('en') or 'unknown'}\n"
                f"Moves: {len(state.san_moves)}. Result: {state.result or 'unfinished'}.\n"
                f"Accuracy: {accuracy}%. Blunders {counts.get('blunder', 0)}, "
                f"mistakes {counts.get('mistake', 0)}, inaccuracies {counts.get('inaccuracy', 0)}.\n"
                f"Moves: {' '.join(state.san_moves[-40:])}\n"
                "Highlight the turning point and give one concrete recommendation. "
                "Do not mention engines or technical parameters."
            )
        try:
            text = await ai.quick("chess", prompt, language=language, temperature=0.8, max_tokens=500)
        except Exception as exc:
            log.warning("review text failed: %s", exc)
            text = f"Точность партии {accuracy}%."
        return _scrub(text).strip()


def _outcome(board: chess.Board) -> tuple[str, str | None]:
    if board.is_checkmate():
        return "finished", "1-0" if board.turn == chess.BLACK else "0-1"
    if board.is_stalemate() or board.is_insufficient_material():
        return "finished", "1/2-1/2"
    if board.is_seventyfive_moves() or board.is_fivefold_repetition():
        return "finished", "1/2-1/2"
    if board.is_game_over(claim_draw=True):
        return "finished", "1/2-1/2"
    return "active", None


def _square_color(square: int) -> int:
    """0 = dark, 1 = light. python-chess >=1.10 dropped chess.square_color."""
    return (chess.square_file(square) + chess.square_rank(square)) % 2


def _structure(board: chess.Board) -> dict[str, Any]:
    pawns = {"white": [], "black": []}
    for square in chess.SQUARES:
        piece = board.piece_at(square)
        if piece and piece.piece_type == chess.PAWN:
            pawns["white" if piece.color == chess.WHITE else "black"].append(chess.square_file(square))
    open_files = [f for f in range(8) if f not in pawns["white"] and f not in pawns["black"]]
    half_open_white = [f for f in range(8) if f not in pawns["white"] and f in pawns["black"]]
    return {
        "open_files": [chess.FILE_NAMES[f] for f in open_files],
        "half_open_for_white": [chess.FILE_NAMES[f] for f in half_open_white],
        "bishop_pair": {
            "white": len({_square_color(s) for s in chess.SQUARES
                          if (p := board.piece_at(s)) and p.piece_type == chess.BISHOP and p.color == chess.WHITE}) == 2,
            "black": len({_square_color(s) for s in chess.SQUARES
                          if (p := board.piece_at(s)) and p.piece_type == chess.BISHOP and p.color == chess.BLACK}) == 2,
        },
        "kings": {
            "white": chess.square_name(board.king(chess.WHITE)),
            "black": chess.square_name(board.king(chess.BLACK)),
        },
        "phase": _phase(board),
    }


def _phase(board: chess.Board) -> str:
    majors = sum(
        1
        for s in chess.SQUARES
        if (p := board.piece_at(s)) and p.piece_type in (chess.ROOK, chess.QUEEN)
    )
    minors = sum(
        1 for s in chess.SQUARES if (p := board.piece_at(s)) and p.piece_type in (chess.BISHOP, chess.KNIGHT)
    )
    pawns = sum(1 for s in chess.SQUARES if (p := board.piece_at(s)) and p.piece_type == chess.PAWN)
    if majors <= 4 and pawns <= 10:
        return "эндшпиль"
    if minors + majors >= 12:
        return "дебют/миттельшпиль"
    return "миттельшпиль"


def _accuracy(counts: dict[str, int], total: int) -> float:
    weights = {
        "best": 100,
        "excellent": 96,
        "good": 90,
        "inaccuracy": 78,
        "mistake": 55,
        "blunder": 20,
        "book": 100,
        "engine": 100,
    }
    scored = sum(weights.get(grade, 85) for grade, n in counts.items() for _ in range(n))
    return round(scored / max(total, 1), 1)


def _build_comment_prompt(context: dict[str, Any], focus: str, language: str) -> str:
    material = context["material"]
    structure = context["structure"]
    line = " ".join(context["top_line"][:4]) or "—"
    if focus == "hint":
        task = (
            "Подскажи лучший ход и коротко объясни идею (2-3 предложения). "
            "Начни с хода в формате «Сыграл бы …»."
        )
    else:
        task = (
            "Оцени позицию от первого лица: 3-5 предложений — что важно сейчас, "
            "какие фигуры работают, где слабые места, какой план выбираешь."
        )
    if not language.lower().startswith("ru"):
        task = (
            "Suggest the best move and explain the idea in 2-3 sentences."
            if focus == "hint"
            else "Assess the position in the first person: 3-5 sentences about what matters now, which pieces work, where the weaknesses are and which plan you choose."
        )
    return (
        f"{task}\n\n"
        f"Ход {context['move_number']}, очередь: {context['turn']}.\n"
        f"Последний ход: {context['last_move'] or '—'}.\n"
        f"Дебют: {context['opening'] or '—'}.\n"
        f"Оценка позиции: {context['assessment']}.\n"
        f"Материал: белые {material['white']}, чёрные {material['black']}.\n"
        f"Стадия: {structure['phase']}. Открытые линии: {', '.join(structure['open_files']) or 'нет'}.\n"
        f"Пара слонов: белые {structure['bishop_pair']['white']}, чёрные {structure['bishop_pair']['black']}.\n"
        f"Главная линия: {line}.\n"
        f"Последние ходы партии: {' '.join(context['history']) or '—'}.\n"
        "Пиши живым шахматным языком, уверенно, как гроссмейстер за доской. "
        "Без markdown, без списков, без технических терминов анализа."
    )


def _fallback_comment(context: dict[str, Any], focus: str, language: str) -> str:
    if language.lower().startswith("ru"):
        base = f"Позиция: {context['assessment']}. "
        if focus == "hint" and context["top_line"]:
            return _scrub(base + f"Сыграл бы {context['top_line'][0]} — это держит инициативу.")
        return _scrub(base + "Играю по позиции: улучшаю фигуры и контролирую центр.")
    base = f"Assessment: {context['assessment']}. "
    if focus == "hint" and context["top_line"]:
        return _scrub(base + f"I would play {context['top_line'][0]} — it keeps the initiative.")
    return _scrub(base + "I play positionally: improve the pieces and control the centre.")


def _fallback_plans(info: dict[str, Any], language: str) -> list[str]:
    line = info["lines"][0]["pv"][:3] if info["lines"] else []
    if language.lower().startswith("ru"):
        return [
            f"Провести {' '.join(line)}" if line else "Улучшить положение худшей фигуры",
            "Занять открытую линию тяжёлой фигурой",
            "Закрепить центр и ограничить контригру соперника",
        ]
    return [
        f"Play {' '.join(line)}" if line else "Improve the worst placed piece",
        "Occupy the open file with a heavy piece",
        "Consolidate the centre and limit counterplay",
    ]


chess_service = ChessService()
