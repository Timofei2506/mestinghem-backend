"""
Terminal API.

Two modes:

* ``POST /api/terminal/run`` — one-shot execution with captured output
  (used by the AI and by quick commands).
* ``WS  /api/terminal/ws`` — an interactive PTY stream for the web console.

Both require the explicit ``terminal`` permission, which the user grants in
Settings; it is off by default.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.db import db
from ..security import audit, decode_token, screen_input
from ..services.terminal_service import screen_command, terminal_service
from .deps import Throttler, get_current_user, require_permission, utcnow

log = logging.getLogger("terminal")
router = APIRouter(prefix="/api/terminal", tags=["terminal"])


class RunBody(BaseModel):
    command: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    cwd: str | None = Field(default=None, max_length=400)
    timeout: int = Field(default=30, ge=2, le=300)


class SessionBody(BaseModel):
    cwd: str | None = Field(default=None, max_length=400)
    shell: str = Field(default="/bin/bash", max_length=60)


class ResizeBody(BaseModel):
    rows: int = Field(ge=4, le=200)
    cols: int = Field(ge=20, le=400)


async def _ensure_session(user: dict[str, Any], session_id: str | None, cwd: str | None = None,
                          shell: str = "/bin/bash") -> Any:
    if session_id:
        session = terminal_service.sessions.get(session_id)
        if session and session.user_id == user["id"] and session.alive:
            return session
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сессия терминала не найдена или закрыта")
    return await terminal_service.open(
        user["id"], shell=shell if shell in ("/bin/bash", "/bin/sh", "/usr/bin/bash") else "/bin/bash",
        cwd=cwd, permissions=user.get("permissions") or {},
    )


@router.get("/policy")
async def policy(user: dict[str, Any] = Depends(require_permission("terminal"))) -> dict[str, Any]:
    permissions = user.get("permissions") or {}
    return {
        "enabled": settings.terminal_enabled,
        "sandboxed": True,
        "root": str(terminal_service.sandbox(user["id"])),
        "timeout_seconds": settings.terminal_timeout,
        "permissions": {
            "network_tools": bool(permissions.get("terminal_network", True)),
            "package_managers": bool(permissions.get("terminal_packages", False)),
            "privilege_escalation": False,
            "host_filesystem": False,
        },
        "blocked_categories": [
            "Удаление корневой файловой системы",
            "Форматирование и прямая запись на устройства",
            "Повышение привилегий (sudo/su/doas)",
            "Управление питанием, cron, systemd, фаерволом, учётными записями хоста",
            "Выполнение скриптов из сети через pipe",
            "Reverse-shell и инструменты подбора паролей",
            "Выход за пределы рабочей области (включая «..»)",
        ],
        "max_sessions": 4,
    }


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def open_session(body: SessionBody | None, user: dict[str, Any] = Depends(require_permission("terminal"))) -> dict[str, Any]:
    session = await terminal_service.open(
        user["id"],
        shell=(body.shell if body and body.shell in ("/bin/bash", "/bin/sh") else "/bin/bash"),
        cwd=body.cwd if body else None,
        permissions=user.get("permissions") or {},
    )
    return {
        "id": session.id,
        "cwd": str(session.cwd),
        "shell": session.shell,
        "banner": (
            f"{settings.brand} terminal · sandbox {session.cwd.name}\n"
            "Команды выполняются в изолированной рабочей области.\n"
        ),
    }


@router.get("/sessions")
async def list_sessions(user: dict[str, Any] = Depends(require_permission("terminal"))) -> dict[str, Any]:
    return {"sessions": terminal_service.for_user(user["id"])}


@router.post("/sessions/{session_id}/resize")
async def resize(session_id: str, body: ResizeBody, user: dict[str, Any] = Depends(require_permission("terminal"))) -> dict[str, str]:
    if session_id not in terminal_service.sessions or terminal_service.sessions[session_id].user_id != user["id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сессия не найдена")
    terminal_service.resize(session_id, body.rows, body.cols)
    return {"status": "ok"}


@router.delete("/sessions/{session_id}")
async def close_session(session_id: str, user: dict[str, Any] = Depends(require_permission("terminal"))) -> dict[str, str]:
    try:
        closed = await terminal_service.close(session_id, user_id=user["id"])
    except PermissionError:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Не ваша сессия")
    if not closed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сессия не найдена")
    return {"status": "ok"}


@router.post("/run")
async def run(body: RunBody, user: dict[str, Any] = Depends(Throttler("terminal", limit=60))) -> dict[str, Any]:
    verdict = screen_input(body.command, strict=False)
    if not verdict.allowed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Команда отклонена политикой безопасности")
    session = await _ensure_session(user, body.session_id, body.cwd)
    result = await terminal_service.run(
        session, body.command, permissions=user.get("permissions") or {}, timeout=body.timeout
    )
    if not result["allowed"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, result["reason"])
    return {"session_id": session.id, **result}


@router.get("/history")
async def history(limit: int = 60, user: dict[str, Any] = Depends(require_permission("terminal"))) -> dict[str, Any]:
    return {"commands": await terminal_service.history(user["id"], max(1, min(300, limit)))}


@router.post("/check")
async def check_command(body: RunBody, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """Dry-run policy check — used by the UI before executing."""
    verdict = screen_command(body.command, user.get("permissions") or {},
                             cwd=terminal_service.sandbox(user["id"]))
    return {
        "allowed": verdict.allowed,
        "reason": verdict.reason,
        "code": verdict.code,
        "risk": verdict.risk,
    }


# ---------------------------------------------------------------------------
# WebSocket PTY
# ---------------------------------------------------------------------------
@router.websocket("/ws")
async def ws_terminal(websocket: WebSocket, token: str = Query(default=""), session_id: str = Query(default="")) -> None:
    await websocket.accept()
    try:
        payload = decode_token(token)
    except Exception:
        await websocket.send_text(json.dumps({"event": "error", "message": "Требуется авторизация"}))
        await websocket.close(code=4401)
        return

    user = await db.fetch("select * from users where id = $1", payload.get("sub", "")).first()
    if not user or user.get("status") != "active":
        await websocket.send_text(json.dumps({"event": "error", "message": "Пользователь не найден"}))
        await websocket.close(code=4401)
        return
    permissions = user.get("permissions") or {}
    if not permissions.get("terminal") and user.get("role") not in ("owner", "admin"):
        await websocket.send_text(json.dumps({"event": "error", "message": "Терминал отключён в настройках аккаунта"}))
        await websocket.close(code=4403)
        return
    if not settings.terminal_enabled:
        await websocket.send_text(json.dumps({"event": "error", "message": "Терминал отключён администратором"}))
        await websocket.close(code=4403)
        return

    try:
        session = await _ensure_session(user, session_id or None)
    except HTTPException as exc:
        await websocket.send_text(json.dumps({"event": "error", "message": exc.detail}))
        await websocket.close(code=4404)
        return

    await websocket.send_text(json.dumps({
        "event": "ready", "session_id": session.id, "cwd": str(session.cwd), "shell": session.shell,
        "banner": f"{settings.brand} · sandbox {session.cwd.name}\r\n",
    }))
    queue = terminal_service.subscribe(session)

    async def pump() -> None:
        try:
            while session.alive:
                data = await queue.get()
                if not data:
                    break
                await websocket.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as exc:  # pragma: no cover
            log.debug("ws pump stopped: %s", exc)

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if (text := message.get("text")) is not None:
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    await terminal_service.write(session, text)
                    continue
                kind = event.get("type")
                if kind == "input":
                    await terminal_service.write(session, str(event.get("data") or ""))
                elif kind == "resize":
                    terminal_service.resize(session.id, int(event.get("rows") or 24), int(event.get("cols") or 80))
                elif kind == "command":
                    command = str(event.get("data") or "").strip()
                    verdict = screen_command(command, permissions, cwd=session.cwd)
                    if verdict.allowed:
                        await terminal_service.write(session, command + "\n")
                    else:
                        await websocket.send_text(json.dumps({
                            "event": "blocked", "reason": verdict.reason, "code": verdict.code,
                        }))
                elif kind == "ping":
                    await websocket.send_text(json.dumps({"event": "pong"}))
            elif (raw := message.get("bytes")) is not None:
                await terminal_service.write(session, raw)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover
        log.warning("terminal ws error: %s", exc)
    finally:
        pump_task.cancel()
        terminal_service.unsubscribe(session, queue)
        try:
            await websocket.close()
        except Exception:
            pass
