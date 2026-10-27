"""
Shared API dependencies: auth, permissions, throttling, request metadata.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import jwt as pyjwt
from fastapi import Depends, Header, HTTPException, Request, status

from ..core.config import settings
from ..core.db import Database, db
from ..security import (
    audit,
    client_ip,
    decode_token,
    rate_limiter,
    security_event,
    token_hash,
)

log = logging.getLogger("api")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["client_ip", "get_current_user", "require_permission", "require_role",
           "Throttler", "public_user", "utcnow", "throttle", "load_user"]


PUBLIC_USER_FIELDS = (
    "id", "email", "username", "display_name", "language", "theme", "plan",
    "role", "status", "avatar_key", "preferences", "permissions",
    "email_verified", "mfa_enabled", "created_at", "last_login_at",
)


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    """Strip everything sensitive before a user record leaves the API."""
    data = {key: row.get(key) for key in PUBLIC_USER_FIELDS}
    data["permissions"] = row.get("permissions") or {}
    data["preferences"] = row.get("preferences") or {}
    data["plan"] = row.get("plan") or "free"
    data["role"] = row.get("role") or "user"
    return data


async def load_user(database: Database, user_id: str) -> dict[str, Any] | None:
    row = await database.fetch("select * from users where id = $1", user_id).first()
    if not row or row.get("status") in ("deleted", "banned"):
        return None
    if row.get("deleted_at"):
        return None
    return row


async def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    """Resolve the caller from a bearer token or a personal API key."""
    if x_api_key:
        user = await _user_from_api_key(x_api_key, request)
        if user:
            return user
    if not authorization:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется авторизация",
                            headers={"WWW-Authenticate": "Bearer"})
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный формат заголовка Authorization")
    try:
        payload = decode_token(token)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Недействительный токен")

    user = await load_user(db, payload["sub"])
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь не найден")
    if user.get("locked_until"):
        try:
            locked = datetime.fromisoformat(str(user["locked_until"]).replace("Z", "+00:00"))
            if locked.tzinfo is None:
                locked = locked.replace(tzinfo=timezone.utc)
            if locked > datetime.now(timezone.utc):
                raise HTTPException(status.HTTP_423_LOCKED, "Аккаунт временно заблокирован")
        except ValueError:
            pass
    request.state.user = user
    return user


async def _user_from_api_key(key: str, request: Request) -> dict[str, Any] | None:
    row = await db.fetch(
        "select * from api_keys where key_hash = $1 and active = true", token_hash(key)
    ).first()
    if not row:
        return None
    if row.get("expires_at"):
        try:
            expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires < datetime.now(timezone.utc):
                return None
        except ValueError:
            pass
    user = await load_user(db, row["user_id"])
    if not user:
        return None
    await db.execute("update api_keys set last_used_at = $1 where id = $2", utcnow(), row["id"])
    request.state.api_key = row
    return user


def require_permission(*names: str) -> Any:
    """Dependency factory: user must hold every listed permission (or be staff)."""

    async def dependency(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        permissions = user.get("permissions") or {}
        staff = user.get("role") in ("owner", "admin")
        missing = [n for n in names if not staff and not permissions.get(n, False)]
        if missing:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Недостаточно прав: {', '.join(missing)}",
            )
        return user

    return dependency


def require_role(*roles: str) -> Any:
    async def dependency(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        if user.get("role") not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
        return user

    return dependency


async def throttle(
    request: Request,
    user: dict[str, Any],
    *,
    action: str,
    limit: int | None = None,
    cost: int = 1,
) -> None:
    bucket = f"{action}:{user['id']}"
    allowed, remaining, retry = await rate_limiter.hit(bucket, limit=limit, cost=cost)
    request.state.rate_remaining = remaining
    if not allowed:
        await security_event("rate_limited", severity="low", user_id=user["id"],
                             ip=client_ip(request), detail={"action": action})
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много запросов. Повторите позже.",
            headers={"Retry-After": str(int(retry) or 1)},
        )


class Throttler:
    """Reusable dependency that enforces a per-user rate limit on a route."""

    def __init__(self, action: str, limit: int | None = None, cost: int = 1) -> None:
        self.action = action
        self.limit = limit
        self.cost = cost

    async def __call__(self, request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        await throttle(request, user, action=self.action, limit=self.limit, cost=self.cost)
        return user


def log_request(request: Request, user: dict[str, Any] | None, status_code: int) -> None:
    if status_code >= 400:
        log.warning(
            "%s %s -> %s (user=%s)",
            request.method,
            request.url.path,
            status_code,
            (user or {}).get("id", "anon"),
        )
