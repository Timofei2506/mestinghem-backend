"""Chess API — board, engine play and grandmaster commentary."""
from __future__ import annotations

import logging
from typing import Any

import chess as pychess
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.db import db, new_id
from ..security import audit
from ..services.chess_engine import ChessError, chess_service, detect_opening
from .deps import Throttler, get_current_user, require_permission, utcnow

log = logging.getLogger("chess")
router = APIRouter(prefix="/api/chess", tags=["chess"])


class NewGameBody(BaseModel):
    side: str = Field(default="white", pattern="^(white|black|random)$")
    mode: str = Field(default="engine", pattern="^(engine|analysis|hotseat)$")
    level: int = Field(default=60, ge=1, le=100)
    fen: str | None = Field(default=None, max_length=140)
    white: str | None = Field(default=None, max_length=40)
    black: str | None = Field(default=None, max_length=40)
    clock: dict[str, Any] | None = None


class MoveBody(BaseModel):
    move: str = Field(min_length=2, max_length=12)


class AnalyseBody(BaseModel):
    language: str = Field(default="ru", pattern="^(ru|en)$")
    multipv: int = Field(default=3, ge=1, le=3)


def _payload(state: Any, **extra: Any) -> dict[str, Any]:
    data = state.to_client()
    data["opening"] = detect_opening(state.san_moves)
    data["engine"] = {
        "name": f"{settings.brand_short} Grandmaster",
        "version": "gm-1",
        "level": state.engine_level,
        "level_label": _level_label(state.engine_level),
    }
    data.update(extra)
    return data


def _level_label(level: int) -> str:
    if level <= 15:
        return "Новичок"
    if level <= 35:
        return "Любитель"
    if level <= 55:
        return "Клубный игрок"
    if level <= 75:
        return "Кандидат в мастера"
    if level <= 90:
        return "Мастер"
    return "Гроссмейстер"


@router.post("/games", status_code=status.HTTP_201_CREATED)
async def new_game(body: NewGameBody, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    import secrets as _secrets

    side = body.side
    if side == "random":
        side = _secrets.choice(["white", "black"])
    if body.fen:
        try:
            pychess.Board(body.fen)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Некорректная позиция: {exc}")
    game_id = new_id("chs_")
    try:
        state = await chess_service.create(
            db,
            user_id=user["id"],
            game_id=game_id,
            side=side,
            mode=body.mode,
            fen=body.fen,
            level=body.level,
            clock=body.clock,
            white=body.white,
            black=body.black,
        )
    except ChessError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Некорректная позиция: {exc}")
    await audit("chess.game_create", user_id=user["id"], resource="chess_game", resource_id=game_id,
                detail={"side": side, "mode": body.mode, "level": body.level})
    return _payload(state, id=game_id)


@router.get("/games")
async def list_games(limit: int = 40, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"games": await chess_service.list(db, user["id"], limit=max(1, min(100, limit)))}


@router.get("/games/{game_id}")
async def get_game(game_id: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        state = await chess_service.load(db, game_id, user["id"])
    except ChessError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return _payload(state)


@router.post("/games/{game_id}/moves")
async def play_move(
    game_id: str,
    body: MoveBody,
    user: dict[str, Any] = Depends(Throttler("chess", limit=120)),
) -> dict[str, Any]:
    try:
        state = await chess_service.load(db, game_id, user["id"])
        if state.status != "active":
            raise HTTPException(status.HTTP_409_CONFLICT, f"Партия завершена: {state.result or ''}")
        if state.mode == "engine" and state.turn != state.side:
            raise HTTPException(status.HTTP_409_CONFLICT, "Сейчас ход платформы")
        updated = await chess_service.play_move(db, state, body.move)
    except ChessError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    annotation = getattr(updated, "last_annotation", None)
    return _payload(updated, annotation=annotation)


@router.post("/games/{game_id}/hint")
async def hint(game_id: str, user: dict[str, Any] = Depends(Throttler("chess_hint", limit=40))) -> dict[str, Any]:
    try:
        state = await chess_service.load(db, game_id, user["id"])
        return await chess_service.hint(db, state)
    except ChessError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.post("/games/{game_id}/analyse")
async def analyse(
    game_id: str, body: AnalyseBody | None = None,
    user: dict[str, Any] = Depends(Throttler("chess_analyse", limit=40)),
) -> dict[str, Any]:
    try:
        state = await chess_service.load(db, game_id, user["id"])
        language = (body.language if body else None) or user.get("language") or "ru"
        result = await chess_service.analyse(db, state, language=language,
                                             multipv=(body.multipv if body else 3))
    except ChessError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    await audit("chess.analyse", user_id=user["id"], resource="chess_game", resource_id=game_id)
    return result


@router.post("/games/{game_id}/review")
async def review(
    game_id: str, body: AnalyseBody | None = None,
    user: dict[str, Any] = Depends(Throttler("chess_review", limit=15)),
) -> dict[str, Any]:
    try:
        state = await chess_service.load(db, game_id, user["id"])
        language = (body.language if body else None) or user.get("language") or "ru"
        return await chess_service.review_game(db, state, language=language)
    except ChessError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.post("/games/{game_id}/undo")
async def undo(game_id: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        state = await chess_service.load(db, game_id, user["id"])
        return _payload(await chess_service.undo(db, state))
    except ChessError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.post("/games/{game_id}/resign")
async def resign(game_id: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        state = await chess_service.load(db, game_id, user["id"])
        return _payload(await chess_service.resign(db, state))
    except ChessError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/games/{game_id}/pgn")
async def pgn(game_id: str, user: dict[str, Any] = Depends(get_current_user)) -> PlainTextResponse:
    try:
        state = await chess_service.load(db, game_id, user["id"])
    except ChessError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    return PlainTextResponse(chess_service.pgn(state), media_type="application/x-chess-pgn")


@router.post("/position/analyse")
async def analyse_position(
    body: dict[str, Any], user: dict[str, Any] = Depends(Throttler("chess_analyse", limit=40))
) -> dict[str, Any]:
    """Analyse an arbitrary position — used by the analysis board."""
    fen = str(body.get("fen") or pychess.STARTING_FEN)
    try:
        pychess.Board(fen)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Некорректная позиция: {exc}")
    language = str(body.get("language") or user.get("language") or "ru")
    state = await chess_service.create(
        db, user_id=user["id"], game_id=new_id("chs_"), side="white", mode="analysis",
        fen=fen, level=80,
    )
    try:
        return await chess_service.analyse(db, state, language=language)
    except ChessError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/openings")
async def openings(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    from ..services.chess_engine import _OPENINGS

    language = (user.get("language") or "ru").lower()
    key = "ru" if language.startswith("ru") else "en"
    return {
        "openings": [
            {"moves": " ".join(line), "name": names[0 if key == "ru" else 1]}
            for line, *names in _OPENINGS
        ]
    }


@router.get("/engine/status")
async def engine_status(user: dict[str, Any] = Depends(require_permission("chess"))) -> dict[str, Any]:
    """Engine health without disclosing implementation details."""
    from pathlib import Path

    available = Path(settings.chess_engine_path).exists()
    return {
        "available": available,
        "engine": f"{settings.brand_short} Grandmaster",
        "version": "gm-1",
        "levels": [
            {"value": 10, "label": "Новичок"},
            {"value": 30, "label": "Любитель"},
            {"value": 55, "label": "Клубный игрок"},
            {"value": 75, "label": "Кандидат в мастера"},
            {"value": 90, "label": "Мастер"},
            {"value": 100, "label": "Гроссмейстер"},
        ],
        "features": ["analysis", "hint", "review", "pgn", "openings", "engine_play"],
    }
