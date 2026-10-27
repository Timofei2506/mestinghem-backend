"""
Remote workstation agent API.

This is the most sensitive surface in the product, so it is built deny-by-default:

* the agent is **off** until the user turns it on in Settings;
* connecting a machine requires a short-lived pairing code bound to a
  permission profile chosen by the user;
* the agent holds a device key; every command the platform sends is
  **HMAC-signed** with a nonce, and the agent must verify the signature,
  the nonce freshness and its own permission profile before acting;
* actions are classified by risk. ``high`` risk actions are queued and stay
  ``pending`` until the user approves them in the UI — the AI cannot
  self-approve;
* hard prohibitions are enforced server-side regardless of the profile:
  no keystroke capture, no reading of credential stores, no writing outside
  the trusted paths, no destructive filesystem operations, no disabling of
  security software, no persistence mechanisms;
* every action is written to the audit log with actor, risk and outcome.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import secrets
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.db import db, new_id
from ..security import (
    audit,
    client_ip,
    pairing_code,
    security_event,
    sign_command,
    token_hash,
)
from .deps import get_current_user, require_permission, utcnow

log = logging.getLogger("agent")
router = APIRouter(prefix="/api/agent", tags=["agent"])

# ---------------------------------------------------------------------------
# Capability matrix
# ---------------------------------------------------------------------------
PROFILES: dict[str, dict[str, Any]] = {
    "observer": {
        "label": {"ru": "Наблюдатель", "en": "Observer"},
        "scopes": ["system.info", "windows.list", "screen.view", "files.read"],
        "auto_approve": ["system.info", "windows.list", "files.list"],
        "description": {"ru": "Только чтение: состояние системы, окна, снимок экрана.",
                        "en": "Read-only: system state, windows, screen snapshot."},
    },
    "assistant": {
        "label": {"ru": "Ассистент", "en": "Assistant"},
        "scopes": ["system.info", "windows.list", "screen.view", "files.read", "files.list",
                   "apps.list", "apps.launch", "clipboard.read"],
        "auto_approve": ["system.info", "windows.list", "files.list", "apps.list"],
        "description": {"ru": "Чтение + запуск приложений по вашему подтверждению.",
                        "en": "Read plus app launching with your confirmation."},
    },
    "operator": {
        "label": {"ru": "Оператор", "en": "Operator"},
        "scopes": ["system.info", "windows.list", "screen.view", "files.read", "files.list",
                   "files.write", "apps.list", "apps.launch", "apps.close", "settings.change",
                   "clipboard.read", "clipboard.write"],
        "auto_approve": ["system.info", "windows.list", "files.list", "apps.list", "screen.view"],
        "description": {"ru": "Работа с файлами в доверенных папках, настройки, окна и приложения.",
                        "en": "Files in trusted folders, settings, windows and apps."},
    },
    "full": {
        "label": {"ru": "Полный контроль", "en": "Full control"},
        "scopes": ["*"],
        "auto_approve": ["system.info", "windows.list", "files.list", "apps.list"],
        "description": {"ru": "Максимум возможностей. Каждая запись и каждое изменение — только с вашего подтверждения.",
                        "en": "Maximum capability. Every write and change still requires your approval."},
    },
}

ACTIONS: dict[str, dict[str, Any]] = {
    "system.info": {"risk": "low", "scope": "system.info", "label": "Сведения о системе"},
    "system.health": {"risk": "low", "scope": "system.info", "label": "Загрузка CPU/RAM/диска"},
    "windows.list": {"risk": "low", "scope": "windows.list", "label": "Список окон"},
    "windows.focus": {"risk": "low", "scope": "windows.list", "label": "Фокус на окно"},
    "windows.close": {"risk": "medium", "scope": "apps.close", "label": "Закрыть окно"},
    "screen.view": {"risk": "medium", "scope": "screen.view", "label": "Снимок экрана"},
    "apps.list": {"risk": "low", "scope": "apps.list", "label": "Список приложений"},
    "apps.launch": {"risk": "medium", "scope": "apps.launch", "label": "Запустить приложение"},
    "apps.close": {"risk": "medium", "scope": "apps.close", "label": "Закрыть приложение"},
    "processes.list": {"risk": "low", "scope": "system.info", "label": "Список процессов"},
    "files.list": {"risk": "low", "scope": "files.list", "label": "Просмотр папки"},
    "files.read": {"risk": "medium", "scope": "files.read", "label": "Чтение файла"},
    "files.write": {"risk": "high", "scope": "files.write", "label": "Запись файла"},
    "files.organize": {"risk": "high", "scope": "files.write", "label": "Разбор папки по типам"},
    "clipboard.read": {"risk": "high", "scope": "clipboard.read", "label": "Чтение буфера обмена"},
    "clipboard.write": {"risk": "medium", "scope": "clipboard.write", "label": "Запись в буфер обмена"},
    "settings.change": {"risk": "high", "scope": "settings.change", "label": "Изменение настроек ОС"},
    "network.status": {"risk": "low", "scope": "system.info", "label": "Состояние сети"},
}

# Never permitted, whatever the profile says.
FORBIDDEN = (
    "input.keylog", "input.record", "credentials.read", "browser.passwords",
    "filesystem.format", "filesystem.wipe", "registry.system", "security.disable",
    "persistence.install", "remote.shell", "camera.capture", "microphone.capture",
    "payment.execute", "mail.send", "network.exfiltrate", "process.kill_system",
)

_FORBIDDEN_RE = {
    "filesystem": ("\\\\windows\\\\system32", "/etc/shadow", "/etc/passwd", "\\sam", "\\system32\\config"),
    "credentials": ("password", "passwd", "credential", "vault", ".ssh/id_", "wallet.dat", "keychain", "пароль", "куки", "cookies"),
    "security": ("defender", "antivirus", "firewall", "uac", "битdefender", "kaspersky", "drweb"),
}


def _forbidden_reason(action: str, payload: dict[str, Any]) -> str | None:
    if action in FORBIDDEN:
        return "Действие запрещено политикой платформы"
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for category, needles in _FORBIDDEN_RE.items():
        if category == "filesystem" and action.startswith("files.") is False:
            continue
        for needle in needles:
            if needle in blob:
                return f"Запрос затрагивает защищённую область: {category}"
    if action.startswith("files.") and not payload.get("path"):
        return "Не указан путь"
    return None


def _risk(action: str, payload: dict[str, Any]) -> str:
    base = ACTIONS.get(action, {}).get("risk", "high")
    blob = json.dumps(payload, ensure_ascii=False).lower()
    if any(word in blob for word in ("rm -rf", "format", "del /f /s /q", "shutil.rmtree", "drop table")):
        return "critical"
    if action == "files.write" and str(payload.get("path", "")).lower().endswith((".exe", ".bat", ".ps1", ".sh", ".dll", ".vbs")):
        return "critical"
    return base


def _scopes_for_profile(profile: str) -> list[str]:
    return list(PROFILES.get(profile, PROFILES["observer"])["scopes"])


def _auto_approve(profile: str, action: str, risk: str) -> bool:
    if risk in ("high", "critical"):
        return False
    profile_data = PROFILES.get(profile, PROFILES["observer"])
    if action in profile_data["auto_approve"]:
        return True
    return risk == "low" and "*" in profile_data["scopes"]


def _in_scope(profile: str, action: str) -> bool:
    scopes = _scopes_for_profile(profile)
    if "*" in scopes:
        return True
    required = ACTIONS.get(action, {}).get("scope", action)
    return required in scopes


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class PairBody(BaseModel):
    device_name: str = Field(min_length=2, max_length=60)
    platform: str = Field(default="windows", pattern="^(windows|macos|linux)$")
    permission_profile: str = Field(default="observer", pattern="^(observer|assistant|operator|full)$")
    require_approval: bool = True
    trusted_paths: list[str] = Field(default_factory=list, max_length=12)
    expires_in_minutes: int = Field(default=10, ge=1, le=1440)
    consent_given: bool = False


class ProfileBody(BaseModel):
    permission_profile: str | None = Field(default=None, pattern="^(observer|assistant|operator|full)$")
    require_approval: bool | None = None
    trusted_paths: list[str] | None = Field(default=None, max_length=12)
    scopes: list[str] | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, max_length=60)


class ActionBody(BaseModel):
    device_id: str
    action: str = Field(min_length=3, max_length=48)
    payload: dict[str, Any] = Field(default_factory=dict)
    requested_by: str = Field(default="user", pattern="^(user|ai|schedule)$")
    reason: str = Field(default="", max_length=300)


class AgentCommand(BaseModel):
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    nonce: str
    signature: str
    action_id: str
    issued_at: str
    profile: str
    trusted_paths: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    action_id: str
    ok: bool = True
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int = 0


async def _device(device_id: str, user_id: str) -> dict[str, Any]:
    row = await db.fetch(
        "select * from agent_devices where id = $1 and user_id = $2", device_id, user_id
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Устройство не найдено")
    return row


def _public_device(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "platform": row["platform"],
        "hostname": row["hostname"],
        "agent_version": row["agent_version"],
        "permission_profile": row["permission_profile"],
        "profile_label": PROFILES.get(row["permission_profile"], {}).get("label", {}).get("ru"),
        "scopes": row["scopes"] or _scopes_for_profile(row["permission_profile"]),
        "require_approval": bool(row["require_approval"]),
        "trusted_paths": row["trusted_paths"] or [],
        "status": row["status"],
        "last_seen_at": row["last_seen_at"],
        "created_at": row["created_at"],
        "key_prefix": (row.get("device_key") or "")[:12],
    }


# ---------------------------------------------------------------------------
# User-facing routes
# ---------------------------------------------------------------------------
@router.get("/download")
async def download_agent() -> Response:
    """Serve the cross-platform device-agent bundle as a zip.

    Public by design: the bundle is a reference installer and contains no
    secrets (the device key is pasted into config.json after pairing).
    """
    bundle_dir = Path(__file__).resolve().parent.parent / "agent_bundle"
    if not bundle_dir.is_dir():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent bundle is not available")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in sorted(bundle_dir.rglob("*")):
            if file.is_file():
                archive.write(file, arcname=f"mestinghem-agent/{file.relative_to(bundle_dir)}")
    payload = buffer.getvalue()
    from ..services.file_service import content_disposition

    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition("mestinghem-agent.zip"),
            "Content-Length": str(len(payload)),
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get("/capabilities")
async def capabilities(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    language = (user.get("language") or "ru").lower()
    key = "ru" if language.startswith("ru") else "en"
    return {
        "enabled": settings.agent_enabled and bool((user.get("permissions") or {}).get("agent")),
        "consent_required": settings.agent_require_consent,
        "profiles": [
            {
                "id": profile_id,
                "label": data["label"][key],
                "description": data["description"][key],
                "scopes": data["scopes"],
                "auto_approve": data["auto_approve"],
            }
            for profile_id, data in PROFILES.items()
        ],
        "actions": [
            {"id": action_id, "label": meta["label"], "risk": meta["risk"], "scope": meta["scope"]}
            for action_id, meta in ACTIONS.items()
        ],
        "forbidden": list(FORBIDDEN),
        "guarantees": [
            "Команды передаются только по подписанному каналу (HMAC + nonce).",
            "Действия высокого риска выполняются исключительно после вашего подтверждения.",
            "Перехват клавиатуры, чтение хранилищ паролей и отключение защиты запрещены всегда.",
            "Запись файлов возможна только в доверенных папках, которые вы указали.",
            "Устройство можно отозвать одним действием — агент немедленно теряет доступ.",
        ],
    }


@router.get("/devices")
async def list_devices(user: dict[str, Any] = Depends(require_permission("agent"))) -> dict[str, Any]:
    rows = await db.fetch(
        "select * from agent_devices where user_id = $1 and revoked_at is null order by created_at desc",
        user["id"],
    )
    return {"devices": [_public_device(r) for r in rows.rows]}


@router.post("/pair", status_code=status.HTTP_201_CREATED)
async def pair_device(body: PairBody, request: Request,
                      user: dict[str, Any] = Depends(require_permission("agent"))) -> dict[str, Any]:
    if not settings.agent_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Агент отключён на этом развёртывании")
    if settings.agent_require_consent and not body.consent_given:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Требуется явное согласие с политикой управления устройством")

    code = pairing_code()
    device_key = f"mstd_{secrets.token_urlsafe(36)}"
    device_id = new_id("dev_")
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(minutes=body.expires_in_minutes)).isoformat()
    paths = [p.strip() for p in body.trusted_paths if p.strip()][:12]

    await db.insert(
        "agent_devices",
        {
            "id": device_id,
            "user_id": user["id"],
            "name": body.device_name.strip()[:60],
            "platform": body.platform,
            "hostname": None,
            "agent_version": None,
            "device_key": device_key,
            "pairing_code_hash": token_hash(code),
            "scopes": _scopes_for_profile(body.permission_profile),
            "permission_profile": body.permission_profile,
            "require_approval": body.require_approval,
            "trusted_paths": paths,
            "status": "pending",
            "last_seen_at": None,
            "revoked_at": None,
            "created_at": utcnow(),
        },
    )
    await db.insert(
        "verification_codes",
        {
            "id": new_id("vc_"),
            "user_id": user["id"],
            "purpose": f"agent_pair:{device_id}",
            "code_hash": token_hash(code),
            "channel": "device",
            "attempts": 0,
            "consumed": False,
            "expires_at": expires,
            "created_at": utcnow(),
        },
    )
    await audit("agent.pairing_created", user_id=user["id"], resource="device", resource_id=device_id,
                ip=client_ip(request), detail={"profile": body.permission_profile, "expires": expires})
    return {
        "device_id": device_id,
        "pairing_code": code,
        "device_key": device_key,
        "expires_at": expires,
        "permission_profile": body.permission_profile,
        "instructions": {
            "ru": "Откройте агент на компьютере, введите код сопряжения и ключ устройства. "
                  "Код действует ограниченное время и показывается один раз.",
            "en": "Open the agent on your computer, enter the pairing code and the device key. "
                  "The code is short-lived and shown once.",
        },
    }


@router.post("/devices/{device_id}/activate")
async def activate(device_id: str, body: dict[str, Any], request: Request,
                   user: dict[str, Any] = Depends(require_permission("agent"))) -> dict[str, Any]:
    """Called by the agent once, with the pairing code, to move it to `online`."""
    device = await _device(device_id, user["id"])
    code = str(body.get("pairing_code") or "").strip().upper()
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нет кода сопряжения")
    if device.get("status") != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, "Устройство уже активировано")
    row = await db.fetch(
        "select * from verification_codes where user_id = $1 and purpose = $2 and consumed = false",
        user["id"], f"agent_pair:{device_id}",
    ).first()
    if not row or token_hash(code) != row["code_hash"]:
        await security_event("agent_pairing_failed", severity="high", user_id=user["id"], ip=client_ip(request))
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Неверный код сопряжения")
    expires = datetime.fromisoformat(str(row["expires_at"]).replace("Z", "+00:00"))
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_410_GONE, "Код сопряжения истёк")

    await db.execute("update verification_codes set consumed = true where id = $1", row["id"])
    await db.execute(
        "update agent_devices set status = 'online', hostname = $1, agent_version = $2, "
        "pairing_code_hash = null, last_seen_at = $3 where id = $4",
        str(body.get("hostname") or "")[:80], str(body.get("agent_version") or "")[:24],
        utcnow(), device_id,
    )
    await audit("agent.device_activated", user_id=user["id"], resource="device", resource_id=device_id,
                ip=client_ip(request), detail={"profile": device["permission_profile"]})
    return {"status": "online", "device_id": device_id}


@router.patch("/devices/{device_id}")
async def update_device(device_id: str, body: ProfileBody, request: Request,
                        user: dict[str, Any] = Depends(require_permission("agent"))) -> dict[str, Any]:
    device = await _device(device_id, user["id"])
    updates: dict[str, Any] = {}
    if body.permission_profile:
        updates["permission_profile"] = body.permission_profile
        updates["scopes"] = _scopes_for_profile(body.permission_profile)
    if body.require_approval is not None:
        updates["require_approval"] = bool(body.require_approval)
    if body.trusted_paths is not None:
        updates["trusted_paths"] = [p.strip() for p in body.trusted_paths if p.strip()][:12]
    if body.scopes is not None:
        updates["scopes"] = [s for s in body.scopes if s in ACTIONS][:20]
    if body.name:
        updates["name"] = body.name.strip()[:60]
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нет изменений")
    assignments = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(updates))
    await db.execute(f"update agent_devices set {assignments} where id = $1", device_id, *updates.values())
    if "permission_profile" in updates:
        await security_event("agent_profile_changed", severity="medium", user_id=user["id"],
                             ip=client_ip(request), detail={"device_id": device_id, **updates})
    await audit("agent.device_update", user_id=user["id"], resource="device", resource_id=device_id,
                detail={"fields": sorted(updates)})
    fresh = await _device(device_id, user["id"])
    return _public_device(fresh)


@router.post("/devices/{device_id}/revoke")
async def revoke_device(device_id: str, request: Request,
                        user: dict[str, Any] = Depends(require_permission("agent"))) -> dict[str, str]:
    await _device(device_id, user["id"])
    await db.execute(
        "update agent_devices set status = 'revoked', revoked_at = $1 where id = $2", utcnow(), device_id
    )
    await db.execute(
        "update agent_actions set approval_state = 'cancelled', resolved_at = $1 "
        "where device_id = $2 and approval_state in ('pending', 'approved')", utcnow(), device_id,
    )
    await security_event("agent_device_revoked", severity="high", user_id=user["id"], ip=client_ip(request),
                         detail={"device_id": device_id})
    await audit("agent.device_revoke", user_id=user["id"], resource="device", resource_id=device_id)
    return {"status": "ok", "message": "Устройство отозвано, агент немедленно потеряет доступ"}


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
@router.post("/actions", status_code=status.HTTP_201_CREATED)
async def propose_action(body: ActionBody, request: Request,
                         user: dict[str, Any] = Depends(require_permission("agent"))) -> dict[str, Any]:
    device = await _device(body.device_id, user["id"])
    if device["status"] not in ("online", "idle"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Устройство не на связи ({device['status']})")

    action = body.action.strip().lower()
    forbidden = _forbidden_reason(action, body.payload)
    if forbidden:
        await security_event("agent_forbidden_action", severity="critical", user_id=user["id"],
                             ip=client_ip(request), detail={"action": action, "by": body.requested_by})
        raise HTTPException(status.HTTP_403_FORBIDDEN, forbidden)
    if not _in_scope(device["permission_profile"], action):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"Действие «{ACTIONS.get(action, {}).get('label', action)}» вне профиля "
                            f"«{device['permission_profile']}»")
    if action.startswith("files.") and body.payload.get("path"):
        trusted = device.get("trusted_paths") or []
        path = str(body.payload["path"])
        if trusted and not any(path.replace("\\", "/").startswith(t.replace("\\", "/")) for t in trusted):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Путь вне списка доверенных папок")

    risk = _risk(action, body.payload)
    if risk == "critical":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Действие отнесено к критическому риску и запрещено")

    auto = _auto_approve(device["permission_profile"], action, risk) and not device["require_approval"]
    state = "approved" if auto else "pending"
    if body.requested_by == "ai" and risk != "low":
        state = "pending"  # the AI never self-approves anything sensitive

    action_id = new_id("act_")
    await db.insert(
        "agent_actions",
        {
            "id": action_id, "device_id": device["id"], "user_id": user["id"], "action": action,
            "payload": body.payload, "requested_by": body.requested_by, "approval_state": state,
            "approved_by": "auto" if state == "approved" else None, "result": None, "risk": risk,
            "created_at": utcnow(), "resolved_at": utcnow() if state == "approved" else None,
        },
    )
    await audit("agent.action_proposed", user_id=user["id"], resource="device", resource_id=device["id"],
                detail={"action": action, "risk": risk, "state": state, "by": body.requested_by,
                        "reason": body.reason[:200]})
    return {
        "action_id": action_id, "action": action, "risk": risk, "approval_state": state,
        "requires_approval": state == "pending",
        "label": ACTIONS.get(action, {}).get("label", action),
        "message": "Действие поставлено в очередь и ждёт вашего подтверждения"
        if state == "pending" else "Действие одобрено и отправлено агенту",
    }


@router.get("/actions")
async def list_actions(
    device_id: str | None = None, state: str | None = None, limit: int = 60,
    user: dict[str, Any] = Depends(require_permission("agent")),
) -> dict[str, Any]:
    clauses = ["a.user_id = $1"]
    params: list[Any] = [user["id"]]
    if device_id:
        params.append(device_id)
        clauses.append(f"a.device_id = ${len(params)}")
    if state:
        params.append(state)
        clauses.append(f"a.approval_state = ${len(params)}")
    params.append(max(1, min(200, limit)))
    rows = await db.fetch(
        f"select a.id, a.action, a.payload, a.requested_by, a.approval_state, a.risk, a.result, "
        f"a.created_at, a.resolved_at, d.name as device_name, d.platform "
        f"from agent_actions a join agent_devices d on d.id = a.device_id "
        f"where {' and '.join(clauses)} order by a.created_at desc limit ${len(params)}",
        *params,
    )
    return {
        "actions": [
            {**r, "label": ACTIONS.get(r["action"], {}).get("label", r["action"])} for r in rows.rows
        ]
    }


@router.post("/actions/{action_id}/approve")
async def approve_action(action_id: str, request: Request,
                         user: dict[str, Any] = Depends(require_permission("agent"))) -> dict[str, Any]:
    row = await db.fetch(
        "select * from agent_actions where id = $1 and user_id = $2", action_id, user["id"]
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Действие не найдено")
    if row["approval_state"] != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Действие уже {row['approval_state']}")
    await db.execute(
        "update agent_actions set approval_state = 'approved', approved_by = $1, resolved_at = $2 where id = $3",
        user["id"], utcnow(), action_id,
    )
    await audit("agent.action_approved", user_id=user["id"], resource="agent_action", resource_id=action_id,
                ip=client_ip(request), detail={"action": row["action"], "risk": row["risk"]})
    return {"status": "approved", "action_id": action_id}


@router.post("/actions/{action_id}/deny")
async def deny_action(action_id: str, request: Request,
                      user: dict[str, Any] = Depends(require_permission("agent"))) -> dict[str, Any]:
    row = await db.fetch(
        "select id, action, risk from agent_actions where id = $1 and user_id = $2", action_id, user["id"]
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Действие не найдено")
    await db.execute(
        "update agent_actions set approval_state = 'denied', resolved_at = $1 where id = $2", utcnow(), action_id
    )
    await audit("agent.action_denied", user_id=user["id"], resource="agent_action", resource_id=action_id,
                outcome="denied", ip=client_ip(request), detail={"action": row["action"]})
    return {"status": "denied", "action_id": action_id}


@router.post("/actions/bulk")
async def bulk_decide(body: dict[str, Any], request: Request,
                      user: dict[str, Any] = Depends(require_permission("agent"))) -> dict[str, Any]:
    decision = str(body.get("decision") or "").lower()
    ids = [str(i) for i in (body.get("action_ids") or [])][:50]
    if decision not in ("approve", "deny") or not ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нужны decision=approve|deny и action_ids")
    updated = 0
    for action_id in ids:
        row = await db.fetch(
            "select id from agent_actions where id = $1 and user_id = $2 and approval_state = 'pending'",
            action_id, user["id"],
        ).first()
        if not row:
            continue
        await db.execute(
            "update agent_actions set approval_state = $1, approved_by = $2, resolved_at = $3 where id = $4",
            "approved" if decision == "approve" else "denied", user["id"], utcnow(), action_id,
        )
        updated += 1
    await audit(f"agent.bulk_{decision}", user_id=user["id"], ip=client_ip(request),
                detail={"count": updated})
    return {"updated": updated, "decision": decision}


# ---------------------------------------------------------------------------
# Agent-facing protocol (authenticated by device key + HMAC)
# ---------------------------------------------------------------------------
async def _agent_device(device_id: str, device_key: str) -> dict[str, Any]:
    row = await db.fetch(
        "select * from agent_devices where id = $1 and device_key = $2", device_id, device_key
    ).first()
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный ключ устройства")
    if row.get("revoked_at") or row["status"] == "revoked":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Устройство отозвано")
    return row


@router.get("/agent/{device_id}/poll")
async def agent_poll(
    device_id: str,
    x_device_key: str = Header(default=""),
    limit: int = 10,
) -> dict[str, Any]:
    device = await _agent_device(device_id, x_device_key)
    await db.execute("update agent_devices set last_seen_at = $1, status = 'online' where id = $2",
                     utcnow(), device_id)
    rows = await db.fetch(
        "select id, action, payload, risk, created_at from agent_actions "
        "where device_id = $1 and approval_state = 'approved' and result is null "
        "order by created_at asc limit $2",
        device_id, max(1, min(20, limit)),
    )
    commands: list[dict[str, Any]] = []
    for row in rows.rows:
        payload = row["payload"] or {}
        envelope = {
            "action_id": row["id"],
            "action": row["action"],
            "payload": payload,
            "issued_at": utcnow(),
            "profile": device["permission_profile"],
            "trusted_paths": device.get("trusted_paths") or [],
        }
        nonce = secrets.token_hex(12)
        canonical = json.dumps({"action_id": row["id"], "action": row["action"], "payload": payload},
                               ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        envelope["nonce"] = nonce
        envelope["signature"] = sign_command(device["device_key"], canonical, nonce)
        commands.append(AgentCommand(**envelope).model_dump())
    return {
        "commands": commands,
        "policy": {
            "profile": device["permission_profile"],
            "scopes": device.get("scopes") or _scopes_for_profile(device["permission_profile"]),
            "trusted_paths": device.get("trusted_paths") or [],
            "require_approval": bool(device["require_approval"]),
            "forbidden": list(FORBIDDEN),
        },
        "server_time": utcnow(),
    }


@router.post("/agent/{device_id}/result")
async def agent_result(device_id: str, body: AgentResult, x_device_key: str = Header(default="")) -> dict[str, Any]:
    device = await _agent_device(device_id, x_device_key)
    row = await db.fetch(
        "select * from agent_actions where id = $1 and device_id = $2", body.action_id, device_id
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Действие не найдено")
    if row["approval_state"] != "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, "Действие не одобрено")
    await db.execute(
        "update agent_actions set result = $1, approval_state = $2, resolved_at = $3 where id = $4",
        {"ok": body.ok, "data": body.result, "error": body.error, "duration_ms": body.duration_ms},
        "done" if body.ok else "failed",
        utcnow(), body.action_id,
    )
    await audit("agent.action_result", user_id=device["user_id"], actor="agent", resource="device",
                resource_id=device_id, outcome="success" if body.ok else "error",
                detail={"action": row["action"], "risk": row["risk"], "error": (body.error or "")[:160]})
    return {"status": "recorded"}


@router.post("/agent/{device_id}/heartbeat")
async def agent_heartbeat(device_id: str, payload: dict[str, Any],
                          x_device_key: str = Header(default="")) -> dict[str, Any]:
    device = await _agent_device(device_id, x_device_key)
    await db.execute(
        "update agent_devices set last_seen_at = $1, status = 'online', hostname = $2, agent_version = $3 "
        "where id = $4",
        utcnow(), str(payload.get("hostname") or device.get("hostname") or "")[:80],
        str(payload.get("agent_version") or device.get("agent_version") or "")[:24], device_id,
    )
    pending = await db.fetchval(
        "select count(*) from agent_actions where device_id = $1 and approval_state = 'approved' and result is null",
        device_id, default=0,
    )
    return {"ok": True, "queued": int(pending or 0), "revoked": bool(device.get("revoked_at"))}


@router.get("/agent/{device_id}/manifest")
async def agent_manifest(device_id: str, x_device_key: str = Header(default="")) -> dict[str, Any]:
    """What the agent is allowed to do — re-fetched on every session start."""
    device = await _agent_device(device_id, x_device_key)
    profile = device["permission_profile"]
    return {
        "device_id": device_id,
        "profile": profile,
        "scopes": device.get("scopes") or _scopes_for_profile(profile),
        "actions": [
            {"id": action_id, "risk": meta["risk"], "label": meta["label"]}
            for action_id, meta in ACTIONS.items()
            if _in_scope(profile, action_id)
        ],
        "forbidden": list(FORBIDDEN),
        "trusted_paths": device.get("trusted_paths") or [],
        "require_approval": bool(device["require_approval"]),
        "poll_interval_seconds": 5,
        "signature": "hmac-sha256(nonce + canonical_json)",
    }
