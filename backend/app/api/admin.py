"""Platform status, administration and public configuration."""
from __future__ import annotations

import logging
import platform
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..core.config import settings
from ..core.db import db
from ..security import audit
from ..services.storage_service import storage
from ..services.terminal_service import terminal_service
from .deps import client_ip, get_current_user, public_user, require_role, utcnow

log = logging.getLogger("admin")
router = APIRouter(tags=["system"])

_started = time.time()


@router.get("/api/config")
async def public_config() -> dict[str, Any]:
    """Everything the browser is allowed to know. No vendors, no keys, no paths."""
    return settings.as_public_dict()


@router.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - _started),
        "database": db.health(),
        "storage": storage.health(),
        "terminal": {"enabled": settings.terminal_enabled, "sessions": len(terminal_service.sessions)},
        "inference": {"ready": bool(settings.inference_key)},
    }


@router.get("/api/status")
async def status_page(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    counts = {
        "users": await db.fetchval("select count(*) from users", default=0),
        "conversations": await db.fetchval("select count(*) from conversations", default=0),
        "messages": await db.fetchval("select count(*) from messages", default=0),
        "files": await db.fetchval("select count(*) from files", default=0),
        "documents": await db.fetchval("select count(*) from documents", default=0),
        "images": await db.fetchval("select count(*) from images", default=0),
        "chess_games": await db.fetchval("select count(*) from chess_games", default=0),
        "terminal_commands": await db.fetchval("select count(*) from terminal_commands", default=0),
        "agent_devices": await db.fetchval("select count(*) from agent_devices", default=0),
    }
    return {
        "counts": {k: int(v or 0) for k, v in counts.items()},
        "uptime_seconds": int(time.time() - _started),
        "database": db.health(),
        "storage": storage.health(),
        "features": settings.as_public_dict()["features"],
    }


# ---------------------------------------------------------------------------
# Administration
# ---------------------------------------------------------------------------
admin = APIRouter(prefix="/api/admin", tags=["admin"])


@admin.get("/overview")
async def overview(user: dict[str, Any] = Depends(require_role("owner", "admin"))) -> dict[str, Any]:
    recent_users = await db.fetch(
        "select id, email, username, plan, role, status, created_at, last_login_at from users "
        "order by created_at desc limit 25"
    )
    events = await db.fetch(
        "select id, user_id, kind, severity, ip, detail, created_at from security_events "
        "order by created_at desc limit 40"
    )
    audits = await db.fetch(
        "select action, actor, outcome, ip, created_at from audit_logs order by created_at desc limit 40"
    )
    usage = await db.fetch(
        "select coalesce(sum(size),0) as bytes, count(*) as files from files"
    ).first()
    return {
        "users": [{**u, "email": _mask_email(u["email"])} for u in recent_users.rows],
        "security_events": events.rows,
        "audit": audits.rows,
        "storage_bytes": int((usage or {}).get("bytes") or 0),
        "files": int((usage or {}).get("files") or 0),
        "terminal_sessions": terminal_service.for_user(user["id"]),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "database": db.health(),
            "storage": storage.health(),
        },
    }


@admin.get("/users")
async def users(search: str | None = None, limit: int = 50,
                user: dict[str, Any] = Depends(require_role("owner", "admin"))) -> dict[str, Any]:
    limit = max(1, min(200, limit))
    if search:
        rows = await db.fetch(
            "select id, email, username, display_name, plan, role, status, created_at, last_login_at "
            "from users where email ilike $1 or username ilike $1 order by created_at desc limit $2",
            f"%{search[:60]}%", limit,
        )
    else:
        rows = await db.fetch(
            "select id, email, username, display_name, plan, role, status, created_at, last_login_at "
            "from users order by created_at desc limit $1", limit,
        )
    return {"users": rows.rows, "count": len(rows.rows)}


@admin.patch("/users/{user_id}")
async def patch_user(user_id: str, payload: dict[str, Any], request: Request,
                     actor: dict[str, Any] = Depends(require_role("owner", "admin"))) -> dict[str, Any]:
    row = await db.fetch("select * from users where id = $1", user_id).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    updates: dict[str, Any] = {}
    if "plan" in payload and str(payload["plan"]) in ("free", "pro", "enterprise"):
        updates["plan"] = str(payload["plan"])
    if "role" in payload and str(payload["role"]) in ("user", "admin", "owner") and actor["role"] == "owner":
        updates["role"] = str(payload["role"])
    if "status" in payload and str(payload["status"]) in ("active", "suspended", "banned"):
        updates["status"] = str(payload["status"])
    if "permissions" in payload and isinstance(payload["permissions"], dict):
        merged = dict(row.get("permissions") or {})
        merged.update({k: bool(v) for k, v in payload["permissions"].items()})
        updates["permissions"] = merged
    if "email_verified" in payload:
        updates["email_verified"] = bool(payload["email_verified"])
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нет изменений")
    updates["updated_at"] = utcnow()
    assignments = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(updates))
    await db.execute(f"update users set {assignments} where id = $1", user_id, *updates.values())
    if updates.get("status") in ("suspended", "banned"):
        await db.execute("update refresh_tokens set revoked = true where user_id = $1", user_id)
    await audit("admin.user_update", user_id=actor["id"], actor="admin", resource="user",
                resource_id=user_id, ip=client_ip(request), detail={"fields": sorted(updates)})
    fresh = await db.fetch("select * from users where id = $1", user_id).first()
    return public_user(fresh)


@admin.post("/users/{user_id}/unlock")
async def unlock_user(user_id: str, request: Request,
                      actor: dict[str, Any] = Depends(require_role("owner", "admin"))) -> dict[str, str]:
    await db.execute(
        "update users set failed_attempts = 0, locked_until = null, status = 'active' where id = $1", user_id
    )
    await audit("admin.user_unlock", user_id=actor["id"], actor="admin", resource="user",
                resource_id=user_id, ip=client_ip(request))
    return {"status": "ok"}


@admin.get("/security/events")
async def security_events(limit: int = 100, severity: str | None = None,
                          actor: dict[str, Any] = Depends(require_role("owner", "admin"))) -> dict[str, Any]:
    limit = max(1, min(500, limit))
    if severity:
        rows = await db.fetch(
            "select id, user_id, kind, severity, ip, detail, resolved, created_at from security_events "
            "where severity = $1 order by created_at desc limit $2", severity, limit,
        )
    else:
        rows = await db.fetch(
            "select id, user_id, kind, severity, ip, detail, resolved, created_at from security_events "
            "order by created_at desc limit $1", limit,
        )
    return {"events": rows.rows}


@admin.post("/security/events/{event_id}/resolve")
async def resolve_event(event_id: str, request: Request,
                        actor: dict[str, Any] = Depends(require_role("owner", "admin"))) -> dict[str, str]:
    await db.execute("update security_events set resolved = true where id = $1", event_id)
    await audit("admin.security_resolve", user_id=actor["id"], actor="admin", resource="security_event",
                resource_id=event_id, ip=client_ip(request))
    return {"status": "ok"}


@admin.get("/audit")
async def audit_trail(limit: int = 200, action: str | None = None,
                      actor: dict[str, Any] = Depends(require_role("owner", "admin"))) -> dict[str, Any]:
    limit = max(1, min(1000, limit))
    if action:
        rows = await db.fetch(
            "select id, user_id, actor, action, resource, resource_id, outcome, ip, detail, created_at "
            "from audit_logs where action ilike $1 order by created_at desc limit $2",
            f"%{action[:60]}%", limit,
        )
    else:
        rows = await db.fetch(
            "select id, user_id, actor, action, resource, resource_id, outcome, ip, detail, created_at "
            "from audit_logs order by created_at desc limit $1", limit,
        )
    return {"events": rows.rows, "count": len(rows.rows)}


@admin.post("/maintenance/reap")
async def reap(actor: dict[str, Any] = Depends(require_role("owner", "admin"))) -> dict[str, Any]:
    closed = await terminal_service.reap_idle()
    await db.execute(
        "delete from rate_counters where window_start < $1",
        int(time.time() // 60) * 60 - 3600,
    )
    return {"terminal_sessions_closed": closed, "status": "ok"}


def _mask_email(email: str) -> str:
    if not email or "@" not in email:
        return email or ""
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        return f"{local[0]}***@{domain}"
    return f"{local[0]}{'*' * (len(local) - 2)}{local[-1]}@{domain}"
