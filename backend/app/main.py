"""
Mestinghem AI — application entrypoint.

FastAPI app: JSON API under /api, the SPA and static assets under /,
WebSocket endpoints for the terminal and live notifications.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .api import ROUTERS
from .core.config import settings
from .core.db import database_lifespan, db
from .security import audit, decode_token
from .services.storage_service import storage
from .services.terminal_service import terminal_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)-12s %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
log = logging.getLogger("app")

WORKSPACE = settings.workspace_dir
STATIC = WORKSPACE / "static"
_maintenance_task: asyncio.Task | None = None


async def _maintenance_loop() -> None:
    while True:
        try:
            await asyncio.sleep(300)
            closed = await terminal_service.reap_idle()
            if closed:
                log.info("reaped %s idle terminal sessions", closed)
        except asyncio.CancelledError:
            break
        except Exception as exc:  # pragma: no cover
            log.warning("maintenance loop: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _maintenance_task
    async with database_lifespan():
        await storage.connect()
        _maintenance_task = asyncio.create_task(_maintenance_loop())
        log.info(
            "%s v%s ready — db=%s storage=%s",
            settings.brand, settings.version, db.dialect, storage.backend,
        )
        try:
            yield
        finally:
            if _maintenance_task:
                _maintenance_task.cancel()
            await terminal_service.close_all()


app = FastAPI(
    title=f"{settings.brand} Platform API",
    version=settings.version,
    description="Enterprise AI workspace: assistant, terminal, directory, documents, imagery, chess, remote agent.",
    docs_url=None if not settings.debug else "/api/docs",
    redoc_url=None,
    openapi_url=None if not settings.debug else "/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-RateLimit-Remaining", "Content-Disposition"],
    max_age=600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    started = time.perf_counter()
    response: Response = await call_next(request)
    elapsed = int((time.perf_counter() - started) * 1000)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=(), interest-cohort=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("X-Runtime-ms", str(elapsed))
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/files/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


for router in ROUTERS:
    app.include_router(router)


@app.get("/api/ping")
async def ping() -> dict[str, Any]:
    return {"pong": True, "brand": settings.brand, "time": time.time()}


@app.websocket("/api/ws/events")
async def events(websocket: WebSocket, token: str = "") -> None:
    """Lightweight live channel: notifications, agent approvals, system notices."""
    await websocket.accept()
    try:
        payload = decode_token(token)
    except Exception:
        await websocket.send_text(json.dumps({"event": "error", "message": "unauthorized"}))
        await websocket.close(code=4401)
        return
    user_id = payload.get("sub")
    await websocket.send_text(json.dumps({"event": "connected", "user_id": user_id}))
    seen: set[str] = set()
    try:
        while True:
            rows = await db.fetch(
                "select id, title, body, level, link, created_at from notifications "
                "where user_id = $1 and read = false order by created_at desc limit 20",
                user_id,
            )
            for row in rows.rows:
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                await websocket.send_text(json.dumps({"event": "notification", **row}, ensure_ascii=False))
            pending = await db.fetchval(
                "select count(*) from agent_actions where user_id = $1 and approval_state = 'pending'",
                user_id, default=0,
            )
            if pending:
                await websocket.send_text(json.dumps({"event": "approvals_pending", "count": int(pending)}))
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=15)
                if message == "ping":
                    await websocket.send_text(json.dumps({"event": "pong"}))
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"event": "heartbeat"}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as exc:  # pragma: no cover
        log.debug("events ws: %s", exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Static SPA
# ---------------------------------------------------------------------------
INDEX = STATIC / "index.html"


@app.get("/", include_in_schema=False)
async def index() -> Any:
    if INDEX.is_file():
        return FileResponse(INDEX)
    return JSONResponse({"brand": settings.brand, "status": "static assets missing"}, status_code=503)


@app.get("/app", include_in_schema=False)
@app.get("/app/{path:path}", include_in_schema=False)
async def spa(path: str = "") -> Any:
    if INDEX.is_file():
        return FileResponse(INDEX)
    return JSONResponse({"status": "not found"}, status_code=404)


@app.get("/legal/{slug}", include_in_schema=False)
async def legal_page(slug: str) -> Any:
    candidate = WORKSPACE / "legal" / f"{slug}.html"
    if candidate.is_file():
        return FileResponse(candidate)
    return Response(status_code=307, headers={"Location": f"/api/legal/{slug}?format=html"})


if STATIC.is_dir():
    for sub in ("css", "js", "media", "vendor"):
        directory = STATIC / sub
        if directory.is_dir():
            app.mount(f"/static/{sub}", StaticFiles(directory=str(directory)), name=sub)


@app.get("/robots.txt", include_in_schema=False)
async def robots() -> Response:
    return Response("User-agent: *\nAllow: /\nDisallow: /api/\n", media_type="text/plain")


@app.exception_handler(404)
async def not_found(request: Request, exc: Any) -> JSONResponse:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "not found", "path": request.url.path}, status_code=404)
    if INDEX.is_file():
        return FileResponse(INDEX)
    return JSONResponse({"error": "not found"}, status_code=404)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        {"error": "internal", "message": "Внутренняя ошибка сервера" if not settings.debug else str(exc)[:200]},
        status_code=500,
    )
