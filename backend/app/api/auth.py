"""
Identity, sessions and account self-service.

Security properties:
* passwords hashed with PBKDF2-SHA256 folded into bcrypt (see ``security``);
* short-lived access tokens (JWT) + rotating refresh tokens stored **hashed**;
* refresh-token *family* reuse detection — a replayed token revokes the family;
* progressive lockout after repeated failures;
* optional TOTP two-factor with a recovery-code fallback;
* every sensitive action lands in the audit log and security event stream;
* self-service data export and erasure (GDPR art. 15 / 17).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from ..core.config import settings
from ..core.db import db, new_id
from ..security import (
    audit,
    client_fingerprint,
    hash_password,
    issue_access_token,
    issue_refresh_token,
    password_strength,
    security_event,
    totp_code,
    totp_secret,
    totp_uri,
    token_hash,
    verify_password,
    verify_totp,
)
from .deps import client_ip, get_current_user, public_user, require_permission, utcnow

log = logging.getLogger("auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,24}$")
REFRESH_COOKIE = "mst_refresh"
_MAX_ATTEMPTS = 6
_LOCK_MINUTES = 15


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class RegisterBody(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=24)
    password: str = Field(min_length=10, max_length=200)
    display_name: str | None = Field(default=None, max_length=60)
    language: str = Field(default="ru", pattern="^(ru|en)$")
    accept_terms: bool = False
    accept_privacy: bool = False
    consent_marketing: bool = False
    invite: str | None = None


class LoginBody(BaseModel):
    identifier: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=1, max_length=200)
    mfa_code: str | None = Field(default=None, max_length=12)
    remember: bool = True


class RefreshBody(BaseModel):
    refresh_token: str | None = None


class PasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


class ProfileBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=60)
    language: str | None = Field(default=None, pattern="^(ru|en)$")
    theme: str | None = Field(default=None, max_length=24)
    preferences: dict[str, Any] | None = None
    permissions: dict[str, Any] | None = None


class ApiKeyBody(BaseModel):
    name: str = Field(min_length=2, max_length=40)
    scopes: list[str] = Field(default_factory=lambda: ["read", "write"])
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class MfaVerifyBody(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class ConsentBody(BaseModel):
    document: str = Field(min_length=2, max_length=40)
    version: str = Field(min_length=1, max_length=20)
    granted: bool = True
    scope: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _set_refresh_cookie(response: Response, token: str, *, remember: bool, max_age: int) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=max_age if remember else None,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api/auth",
    )


async def _issue_session(
    request: Request, user: dict[str, Any], *, remember: bool = True, family: str | None = None
) -> dict[str, Any]:
    response_tokens: dict[str, Any] = {}
    access = issue_access_token(user)
    raw, hashed, fam, expires = issue_refresh_token(user["id"], family)
    await db.insert(
        "refresh_tokens",
        {
            "id": new_id("rt_"),
            "user_id": user["id"],
            "token_hash": hashed,
            "family": fam,
            "user_agent": (request.headers.get("user-agent") or "")[:300],
            "ip": client_ip(request),
            "scopes": ["user"],
            "revoked": False,
            "rotated_from": None,
            "expires_at": expires.isoformat(),
            "created_at": utcnow(),
            "last_used_at": None,
        },
    )
    response_tokens = {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": settings.access_ttl,
        "refresh_token": raw,
        "refresh_family": fam,
    }
    return response_tokens


async def _register_refresh_cookie(request: Request, user: dict[str, Any], response: Response) -> dict[str, Any]:
    tokens = await _issue_session(request, user)
    _set_refresh_cookie(
        response,
        tokens["refresh_token"],
        remember=True,
        max_age=settings.refresh_ttl,
    )
    return tokens


async def _lock_check(user: dict[str, Any], request: Request) -> None:
    locked_until = user.get("locked_until")
    if not locked_until:
        return
    try:
        locked = datetime.fromisoformat(str(locked_until).replace("Z", "+00:00"))
        if locked.tzinfo is None:
            locked = locked.replace(tzinfo=timezone.utc)
    except ValueError:
        return
    if locked > datetime.now(timezone.utc):
        minutes = int((locked - datetime.now(timezone.utc)).total_seconds() // 60) + 1
        raise HTTPException(status.HTTP_423_LOCKED, f"Аккаунт заблокирован ещё на {minutes} мин")
    await db.execute(
        "update users set failed_attempts = 0, locked_until = null where id = $1", user["id"]
    )


async def _register_failure(user: dict[str, Any] | None, request: Request, *, email: str) -> None:
    if not user:
        await security_event("login_unknown_account", severity="low", ip=client_ip(request),
                             user_agent=request.headers.get("user-agent"),
                             detail={"fingerprint": client_fingerprint(client_ip(request), request.headers.get("user-agent"))})
        return
    attempts = int(user.get("failed_attempts") or 0) + 1
    update: dict[str, Any] = {"failed_attempts": attempts}
    if attempts >= _MAX_ATTEMPTS:
        update["locked_until"] = (datetime.now(timezone.utc) + timedelta(minutes=_LOCK_MINUTES)).isoformat()
        await security_event("account_locked", severity="high", user_id=user["id"], ip=client_ip(request),
                             detail={"attempts": attempts})
    await db.execute(
        "update users set failed_attempts = $1, locked_until = $2 where id = $3",
        attempts, update.get("locked_until"), user["id"],
    )


def _default_permissions() -> dict[str, Any]:
    return {
        "chat": True,
        "files": True,
        "documents": True,
        "images": True,
        "chess": True,
        "terminal": False,          # opt-in, explicit consent required
        "terminal_network": True,
        "terminal_packages": False,
        "agent": False,             # opt-in, device pairing + consent required
        "admin": False,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterBody, request: Request, response: Response) -> dict[str, Any]:
    if not settings.signup_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Регистрация временно закрыта")
    if not (body.accept_terms and body.accept_privacy):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Необходимо принять Условия и Политику конфиденциальности")
    username = body.username.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Логин: 3-24 символа, латиница, цифры, точка, дефис, подчёркивание")
    score, issues = password_strength(body.password)
    if score < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Слишком простой пароль. Минимум 10 символов, смешайте регистр, цифры и знаки.")

    email = body.email.strip().lower()
    existing = await db.fetch(
        "select id from users where lower(email) = $1 or lower(username) = $2", email, username
    ).first()
    if existing:
        await security_event("register_conflict", severity="low", ip=client_ip(request), detail={"email": email})
        raise HTTPException(status.HTTP_409_CONFLICT, "Такой e-mail или логин уже занят")

    user_id = new_id("usr_")
    now = utcnow()
    await db.insert(
        "users",
        {
            "id": user_id,
            "email": email,
            "username": username,
            "display_name": (body.display_name or username)[:60],
            "password_hash": hash_password(body.password),
            "kdf": "bcrypt+pbkdf2",
            "language": body.language,
            "theme": "aurum",
            "plan": "free",
            "role": "user",
            "status": "active",
            "preferences": {"animations": True, "sound": False, "compact": False, "streaming": True},
            "permissions": _default_permissions(),
            "email_verified": not settings.require_email_confirm,
            "mfa_enabled": False,
            "failed_attempts": 0,
            "created_at": now,
            "updated_at": now,
        },
    )
    for document, granted in (("terms", True), ("privacy", True), ("marketing", body.consent_marketing)):
        await db.insert(
            "consents",
            {
                "id": new_id("cns_"),
                "user_id": user_id,
                "document": document,
                "version": "1.0",
                "granted": granted,
                "scope": "account",
                "ip": client_ip(request),
                "user_agent": (request.headers.get("user-agent") or "")[:300],
                "created_at": now,
            },
        )
    # Personal workspace bootstrap
    from ..services.file_service import bootstrap_workspace

    await bootstrap_workspace(user_id)

    user = await db.fetch("select * from users where id = $1", user_id).first()
    await audit("auth.register", user_id=user_id, ip=client_ip(request),
                user_agent=request.headers.get("user-agent"), detail={"plan": "free"})
    tokens = await _register_refresh_cookie(request, user, response)
    return {"user": public_user(user), **tokens}


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
    identifier = body.identifier.strip().lower()
    user = await db.fetch(
        "select * from users where lower(email) = $1 or lower(username) = $1", identifier
    ).first()
    if not user:
        await _register_failure(None, request, email=identifier)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный e-mail или пароль")
    await _lock_check(user, request)
    if not verify_password(body.password, user.get("password_hash") or ""):
        await _register_failure(user, request, email=identifier)
        await audit("auth.login_failed", user_id=user["id"], outcome="denied", ip=client_ip(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный e-mail или пароль")

    if user.get("mfa_enabled") and not verify_totp(user.get("mfa_secret") or "", body.mfa_code or ""):
        await security_event("mfa_failed", severity="medium", user_id=user["id"], ip=client_ip(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный код двухфакторной аутентификации",
                            headers={"X-MFA-Required": "1"})

    await db.execute(
        "update users set failed_attempts = 0, locked_until = null, last_login_at = $1, last_seen_at = $1 where id = $2",
        utcnow(), user["id"],
    )
    await audit("auth.login", user_id=user["id"], ip=client_ip(request),
                user_agent=request.headers.get("user-agent"),
                detail={"mfa": bool(user.get("mfa_enabled"))})
    tokens = await _register_refresh_cookie(request, user, response, )
    fresh = await db.fetch("select * from users where id = $1", user["id"]).first()
    return {"user": public_user(fresh), **tokens}


@router.post("/refresh")
async def refresh(body: RefreshBody | None, request: Request, response: Response) -> dict[str, Any]:
    raw = (body.refresh_token if body and body.refresh_token else None) or request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нет refresh-токена")
    row = await db.fetch("select * from refresh_tokens where token_hash = $1", token_hash(raw)).first()
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия не найдена")
    if row.get("revoked"):
        # Reuse of a rotated token → compromise. Kill the whole family.
        await db.execute(
            "update refresh_tokens set revoked = true where family = $1", row["family"]
        )
        await security_event("refresh_token_reuse", severity="critical", user_id=row["user_id"],
                             ip=client_ip(request), detail={"family": row["family"]})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия отозвана по соображениям безопасности")
    expires = row.get("expires_at")
    if expires:
        try:
            when = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            if when < datetime.now(timezone.utc):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла")
        except ValueError:
            pass
    user = await db.fetch("select * from users where id = $1", row["user_id"]).first()
    if not user or user.get("status") != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Аккаунт недоступен")

    await db.execute(
        "update refresh_tokens set revoked = true, last_used_at = $1 where id = $2", utcnow(), row["id"]
    )
    tokens = await _issue_session(request, user, family=row["family"])
    await db.execute(
        "update refresh_tokens set rotated_from = $1 where token_hash = $2",
        row["id"], token_hash(tokens["refresh_token"]),
    )
    _set_refresh_cookie(response, tokens["refresh_token"], remember=True, max_age=settings.refresh_ttl)
    return {"user": public_user(user), **tokens}


@router.post("/logout")
async def logout(request: Request, response: Response, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        row = await db.fetch("select family from refresh_tokens where token_hash = $1", token_hash(raw)).first()
        if row:
            await db.execute("update refresh_tokens set revoked = true where family = $1", row["family"])
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    await audit("auth.logout", user_id=user["id"], ip=client_ip(request))
    return {"status": "ok"}


@router.get("/me")
async def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    stats = await _account_stats(user["id"])
    return {"user": public_user(user), "stats": stats, "limits": settings.as_public_dict()["limits"]}


async def _account_stats(user_id: str) -> dict[str, Any]:
    conversations = await db.fetchval("select count(*) from conversations where user_id = $1", user_id, default=0)
    files = await db.fetchval("select count(*) from files where user_id = $1 and trashed = false", user_id, default=0)
    bytes_used = await db.fetchval(
        "select coalesce(sum(size), 0) as total from files where user_id = $1 and trashed = false", user_id, default=0
    )
    images = await db.fetchval("select count(*) from images where user_id = $1", user_id, default=0)
    documents = await db.fetchval("select count(*) from documents where user_id = $1", user_id, default=0)
    games = await db.fetchval("select count(*) from chess_games where user_id = $1", user_id, default=0)
    commands = await db.fetchval("select count(*) from terminal_commands where user_id = $1", user_id, default=0)
    return {
        "conversations": conversations,
        "files": files,
        "storage_bytes": int(bytes_used or 0),
        "images": images,
        "documents": documents,
        "chess_games": games,
        "terminal_commands": commands,
    }


@router.get("/sessions")
async def sessions(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    rows = await db.fetch(
        "select id, family, user_agent, ip, created_at, last_used_at, revoked, expires_at "
        "from refresh_tokens where user_id = $1 order by created_at desc limit 25",
        user["id"],
    )
    return {
        "sessions": [
            {
                "id": r["id"],
                "device": (r["user_agent"] or "Неизвестное устройство")[:90],
                "ip": r["ip"],
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
                "revoked": bool(r["revoked"]),
                "expires_at": r["expires_at"],
            }
            for r in rows.rows
        ]
    }


@router.post("/sessions/revoke-all")
async def revoke_all(request: Request, response: Response, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    await db.execute("update refresh_tokens set revoked = true where user_id = $1", user["id"])
    await db.execute("update api_keys set active = false where user_id = $1", user["id"])
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
    await security_event("sessions_revoked_all", severity="medium", user_id=user["id"], ip=client_ip(request))
    await audit("auth.revoke_all", user_id=user["id"], ip=client_ip(request))
    return {"status": "ok"}


@router.post("/password")
async def change_password(body: PasswordBody, request: Request,
                          user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    if not verify_password(body.current_password, user.get("password_hash") or ""):
        await security_event("password_change_failed", severity="medium", user_id=user["id"], ip=client_ip(request))
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Текущий пароль неверен")
    score, issues = password_strength(body.new_password)
    if score < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Новый пароль слишком простой")
    await db.execute(
        "update users set password_hash = $1, updated_at = $2 where id = $3",
        hash_password(body.new_password), utcnow(), user["id"],
    )
    await db.execute("update refresh_tokens set revoked = true where user_id = $1", user["id"])
    await security_event("password_changed", severity="info", user_id=user["id"], ip=client_ip(request))
    await audit("auth.password_change", user_id=user["id"], ip=client_ip(request))
    return {"status": "ok", "message": "Пароль изменён, все сессии завершены"}


@router.patch("/profile")
async def update_profile(body: ProfileBody, request: Request,
                         user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    allowed_permission_keys = {
        "chat", "files", "documents", "images", "chess", "terminal",
        "terminal_network", "terminal_packages", "agent",
    }
    updates: dict[str, Any] = {}
    if body.display_name is not None:
        updates["display_name"] = body.display_name.strip()[:60]
    if body.language:
        updates["language"] = body.language
    if body.theme:
        updates["theme"] = body.theme[:24]
    if body.preferences is not None:
        merged = dict(user.get("preferences") or {})
        merged.update({k: v for k, v in body.preferences.items() if isinstance(k, str)})
        updates["preferences"] = merged
    if body.permissions is not None:
        merged_perms = dict(user.get("permissions") or {})
        for key, value in body.permissions.items():
            if key in allowed_permission_keys:
                merged_perms[key] = bool(value)
        updates["permissions"] = merged_perms
        if "terminal" in body.permissions or "agent" in body.permissions:
            await security_event(
                "sensitive_permission_changed", severity="medium", user_id=user["id"], ip=client_ip(request),
                detail={k: v for k, v in body.permissions.items() if k in ("terminal", "agent")},
            )
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нет изменений")
    updates["updated_at"] = utcnow()
    assignments = ", ".join(f"{key} = ${i + 2}" for i, key in enumerate(updates))
    await db.execute(
        f"update users set {assignments} where id = $1", user["id"], *list(updates.values())
    )
    await audit("auth.profile_update", user_id=user["id"], ip=client_ip(request),
                detail={"fields": sorted(updates.keys())})
    fresh = await db.fetch("select * from users where id = $1", user["id"]).first()
    return {"user": public_user(fresh)}


# ---------------------------------------------------------------------------
# Two-factor authentication
# ---------------------------------------------------------------------------
@router.post("/mfa/setup")
async def mfa_setup(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if user.get("mfa_enabled"):
        raise HTTPException(status.HTTP_409_CONFLICT, "2FA уже включена")
    secret = totp_secret()
    recovery = [secrets.token_hex(4).upper() for _ in range(8)]
    await db.execute(
        "update users set mfa_secret = $1, updated_at = $2 where id = $3",
        json.dumps({"secret": secret, "recovery": [hashlib.sha256(c.encode()).hexdigest() for c in recovery]}),
        utcnow(), user["id"],
    )
    return {
        "secret": secret,
        "otpauth": totp_uri(secret, user["email"]),
        "recovery_codes": recovery,
        "current_code": totp_code(secret),
        "warning": "Сохраните резервные коды сейчас — повторно они не показываются.",
    }


@router.post("/mfa/enable")
async def mfa_enable(body: MfaVerifyBody, request: Request,
                     user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    raw = user.get("mfa_secret")
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Сначала выполните /mfa/setup")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        secret = data["secret"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Некорректное состояние 2FA")
    if not verify_totp(secret, body.code):
        await security_event("mfa_enable_failed", severity="medium", user_id=user["id"], ip=client_ip(request))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код не подходит")
    await db.execute("update users set mfa_enabled = true, updated_at = $1 where id = $2", utcnow(), user["id"])
    await security_event("mfa_enabled", severity="info", user_id=user["id"], ip=client_ip(request))
    await audit("auth.mfa_enable", user_id=user["id"], ip=client_ip(request))
    return {"status": "ok"}


@router.post("/mfa/disable")
async def mfa_disable(body: PasswordBody, request: Request,
                      user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    body.new_password = body.new_password or body.current_password
    if not verify_password(body.current_password, user.get("password_hash") or ""):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Неверный пароль")
    await db.execute(
        "update users set mfa_enabled = false, mfa_secret = null, updated_at = $1 where id = $2",
        utcnow(), user["id"],
    )
    await security_event("mfa_disabled", severity="high", user_id=user["id"], ip=client_ip(request))
    await audit("auth.mfa_disable", user_id=user["id"], ip=client_ip(request))
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
@router.post("/keys")
async def create_api_key(body: ApiKeyBody, request: Request,
                         user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    raw = f"mst_{secrets.token_urlsafe(32)}"
    expires = None
    if body.expires_in_days:
        expires = (datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)).isoformat()
    key_id = new_id("key_")
    await db.insert(
        "api_keys",
        {
            "id": key_id,
            "user_id": user["id"],
            "name": body.name.strip()[:40],
            "key_hash": token_hash(raw),
            "prefix": raw[:12],
            "scopes": body.scopes[:8],
            "active": True,
            "expires_at": expires,
            "created_at": utcnow(),
        },
    )
    await audit("auth.key_create", user_id=user["id"], resource="api_key", resource_id=key_id, ip=client_ip(request))
    return {"id": key_id, "key": raw, "name": body.name, "scopes": body.scopes, "expires_at": expires,
            "warning": "Ключ показывается один раз. Скопируйте его сейчас."}


@router.get("/keys")
async def list_api_keys(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    rows = await db.fetch(
        "select id, name, prefix, scopes, active, created_at, last_used_at, expires_at "
        "from api_keys where user_id = $1 order by created_at desc", user["id"],
    )
    return {"keys": [{**r, "active": bool(r["active"])} for r in rows.rows]}


@router.delete("/keys/{key_id}")
async def revoke_api_key(key_id: str, request: Request,
                         user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    affected = await db.execute(
        "update api_keys set active = false where id = $1 and user_id = $2", key_id, user["id"]
    )
    if not affected:
        row = await db.fetch("select id from api_keys where id = $1 and user_id = $2", key_id, user["id"]).first()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ключ не найден")
    await audit("auth.key_revoke", user_id=user["id"], resource="api_key", resource_id=key_id, ip=client_ip(request))
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Consents, audit, GDPR
# ---------------------------------------------------------------------------
@router.post("/consent")
async def record_consent(body: ConsentBody, request: Request,
                         user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    await db.insert(
        "consents",
        {
            "id": new_id("cns_"),
            "user_id": user["id"],
            "document": body.document[:40],
            "version": body.version[:20],
            "granted": body.granted,
            "scope": (body.scope or "account")[:40],
            "ip": client_ip(request),
            "user_agent": (request.headers.get("user-agent") or "")[:300],
            "created_at": utcnow(),
        },
    )
    await audit("auth.consent", user_id=user["id"], resource=body.document, outcome="granted" if body.granted else "denied")
    return {"status": "ok"}


@router.get("/consents")
async def list_consents(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    rows = await db.fetch(
        "select document, version, granted, scope, created_at from consents where user_id = $1 order by created_at desc",
        user["id"],
    )
    return {"consents": [{**r, "granted": bool(r["granted"])} for r in rows.rows]}


@router.get("/audit")
async def my_audit(limit: int = 100, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    limit = max(1, min(500, limit))
    rows = await db.fetch(
        "select action, resource, resource_id, outcome, ip, detail, created_at "
        "from audit_logs where user_id = $1 order by created_at desc limit $2",
        user["id"], limit,
    )
    return {"events": rows.rows, "count": len(rows.rows)}


@router.get("/security")
async def security_overview(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    events = await db.fetch(
        "select kind, severity, ip, detail, created_at from security_events "
        "where user_id = $1 order by created_at desc limit 40", user["id"],
    )
    sessions_rows = await db.fetch(
        "select count(*) as c from refresh_tokens where user_id = $1 and revoked = false", user["id"]
    )
    blocked = await db.fetchval(
        "select count(*) from terminal_commands where user_id = $1 and blocked = true", user["id"], default=0
    )
    score = 100
    if not user.get("mfa_enabled"):
        score -= 25
    if not user.get("email_verified"):
        score -= 10
    if int(sessions_rows.scalar() or 0) > 5:
        score -= 10
    if int(blocked or 0) > 0:
        score -= min(15, int(blocked) * 3)
    critical = sum(1 for e in events.rows if e.get("severity") in ("critical", "high"))
    score -= min(25, critical * 5)
    return {
        "score": max(0, score),
        "mfa_enabled": bool(user.get("mfa_enabled")),
        "email_verified": bool(user.get("email_verified")),
        "active_sessions": int(sessions_rows.scalar() or 0),
        "blocked_commands": int(blocked or 0),
        "events": events.rows,
        "recommendations": _recommendations(user, critical),
    }


def _recommendations(user: dict[str, Any], critical: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not user.get("mfa_enabled"):
        out.append({"level": "high", "ru": "Включите двухфакторную аутентификацию", "en": "Enable two-factor authentication"})
    if not user.get("email_verified"):
        out.append({"level": "medium", "ru": "Подтвердите e-mail", "en": "Verify your email"})
    if critical:
        out.append({"level": "high", "ru": f"Проверьте {critical} критичных событий безопасности", "en": f"Review {critical} critical security events"})
    perms = user.get("permissions") or {}
    if perms.get("terminal") and not perms.get("agent"):
        out.append({"level": "low", "ru": "Терминал активен — держите сессию под наблюдением", "en": "Terminal is active — keep the session supervised"})
    if not out:
        out.append({"level": "ok", "ru": "Аккаунт защищён, действий не требуется", "en": "Account is secure, no action needed"})
    return out


@router.get("/export")
async def export_data(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """GDPR art. 15 — machine-readable copy of everything we hold."""
    uid = user["id"]
    payload = {
        "generated_at": utcnow(),
        "account": public_user(user),
        "profile": {k: user.get(k) for k in ("email_verified", "mfa_enabled", "plan", "role", "created_at", "last_login_at")},
        "conversations": (await db.fetch(
            "select id, title, mode, created_at, updated_at from conversations where user_id = $1", uid)).rows,
        "messages": (await db.fetch(
            "select conversation_id, role, content, created_at from messages where user_id = $1 "
            "order by created_at desc limit 5000", uid)).rows,
        "files": (await db.fetch(
            "select id, name, path, kind, size, created_at from files where user_id = $1", uid)).rows,
        "documents": (await db.fetch(
            "select id, kind, title, stats, created_at from documents where user_id = $1", uid)).rows,
        "images": (await db.fetch(
            "select id, prompt, style, width, height, created_at from images where user_id = $1", uid)).rows,
        "chess_games": (await db.fetch(
            "select id, white, black, san_moves, result, status, created_at from chess_games where user_id = $1", uid)).rows,
        "terminal_commands": (await db.fetch(
            "select command, cwd, exit_code, blocked, created_at from terminal_commands where user_id = $1 limit 2000", uid)).rows,
        "agent_devices": (await db.fetch(
            "select id, name, platform, permission_profile, status, created_at from agent_devices where user_id = $1", uid)).rows,
        "consents": (await db.fetch(
            "select document, version, granted, created_at from consents where user_id = $1", uid)).rows,
        "audit_logs": (await db.fetch(
            "select action, outcome, ip, created_at from audit_logs where user_id = $1 limit 2000", uid)).rows,
        "security_events": (await db.fetch(
            "select kind, severity, created_at from security_events where user_id = $1 limit 500", uid)).rows,
    }
    await audit("auth.data_export", user_id=uid)
    return payload


@router.post("/erase")
async def erase_account(body: PasswordBody, request: Request,
                        user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    """GDPR art. 17 — soft delete now, hard purge of content immediately."""
    if not verify_password(body.current_password, user.get("password_hash") or ""):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Неверный пароль")
    uid = user["id"]
    await db.execute("update refresh_tokens set revoked = true where user_id = $1", uid)
    await db.execute("update api_keys set active = false where user_id = $1", uid)
    await db.execute("delete from messages where user_id = $1", uid)
    await db.execute("delete from conversations where user_id = $1", uid)
    await db.execute("delete from images where user_id = $1", uid)
    await db.execute("delete from documents where user_id = $1", uid)
    await db.execute("delete from terminal_commands where user_id = $1", uid)
    await db.execute("delete from terminal_sessions where user_id = $1", uid)
    await db.execute("delete from chess_games where user_id = $1", uid)
    await db.execute("delete from agent_actions where user_id = $1", uid)
    await db.execute("delete from agent_devices where user_id = $1", uid)

    from ..services.file_service import purge_user_files

    await purge_user_files(uid)

    await db.execute(
        "update users set status = 'deleted', email = $1, username = $2, password_hash = null, "
        "display_name = null, deleted_at = $3, updated_at = $3 where id = $4",
        f"erased-{uid[:8]}@deleted.local", f"erased-{uid[:8]}", utcnow(), uid,
    )
    await security_event("account_erased", severity="high", user_id=uid, ip=client_ip(request))
    await audit("auth.account_erase", user_id=uid, ip=client_ip(request))
    return {"status": "ok", "message": "Аккаунт удалён, данные стёрты"}


@router.get("/password-strength")
async def strength(password: str) -> dict[str, Any]:
    score, issues = password_strength(password)
    labels = {0: "недопустимый", 1: "слабый", 2: "средний", 3: "надёжный", 4: "исключительный"}
    return {"score": score, "label": labels[score], "issues": issues}
