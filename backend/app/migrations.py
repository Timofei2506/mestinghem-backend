"""
Schema migrations — idempotent, dialect aware, applied on boot.

Each table is declared once as a neutral column list; the renderer emits the
right DDL for Postgres (UUID / JSONB / TIMESTAMPTZ) or SQLite (TEXT / JSON).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from .core.db import Database

log = logging.getLogger("migrations")

# ---------------------------------------------------------------------------
# Neutral type vocabulary
# ---------------------------------------------------------------------------
PK = "pk"
UUID = "uuid"
TEXT = "text"
LONGTEXT = "longtext"
JSON = "json"
BOOL = "bool"
INT = "int"
BIGINT = "bigint"
FLOAT = "float"
TS = "ts"
NUMERIC = "numeric"

_TYPE_MAP = {
    "postgres": {
        PK: "text primary key",
        UUID: "text",
        TEXT: "text",
        LONGTEXT: "text",
        JSON: "jsonb",
        BOOL: "boolean not null default false",
        INT: "integer",
        BIGINT: "bigint",
        FLOAT: "double precision",
        TS: "timestamptz",
        NUMERIC: "numeric",
    },
    "sqlite": {
        PK: "text primary key",
        UUID: "text",
        TEXT: "text",
        LONGTEXT: "text",
        JSON: "json",
        BOOL: "integer not null default 0",
        INT: "integer",
        BIGINT: "integer",
        FLOAT: "real",
        TS: "text",
        NUMERIC: "numeric",
    },
}


def _render(table: str, columns: dict[str, str], dialect: str, indexes: Iterable[tuple[str, list[str]]]) -> list[str]:
    types = _TYPE_MAP[dialect]
    parts = []
    auto_now = {"created_at": "now()", "updated_at": "now()"}
    auto_now_sqlite = {"created_at": "CURRENT_TIMESTAMP", "updated_at": "CURRENT_TIMESTAMP"}
    for name, kind in columns.items():
        if kind.startswith("!"):  # explicit override, e.g. "!text not null unique"
            parts.append(f"  {name} {kind[1:]}")
        elif kind == TS and name in auto_now:
            default = auto_now[name] if dialect == "postgres" else auto_now_sqlite[name]
            parts.append(f"  {name} {types[TS]} default {default}")
        else:
            parts.append(f"  {name} {types.get(kind, 'text')}")
    ddl = [f"create table if not exists {table} (\n" + ",\n".join(parts) + "\n)"]
    for index_name, cols in indexes:
        ddl.append(
            f"create index if not exists {index_name} on {table} ({', '.join(cols)})"
        )
    return ddl


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
TABLES: dict[str, tuple[dict[str, str], list[tuple[str, list[str]]]]] = {
    # ---- identity & access -------------------------------------------------
    "users": (
        {
            "id": PK,
            "email": "!text not null unique",
            "username": "!text not null unique",
            "display_name": TEXT,
            "password_hash": TEXT,
            "kdf": TEXT,
            "language": "!text not null default 'ru'",
            "theme": "!text not null default 'aurum'",
            "plan": "!text not null default 'free'",
            "role": "!text not null default 'user'",
            "status": "!text not null default 'active'",
            "avatar_key": TEXT,
            "preferences": JSON,
            "permissions": JSON,
            "email_verified": BOOL,
            "mfa_enabled": BOOL,
            "mfa_secret": TEXT,
            "failed_attempts": "!integer not null default 0",
            "locked_until": TS,
            "last_login_at": TS,
            "last_seen_at": TS,
            "created_at": TS,
            "updated_at": TS,
            "deleted_at": TS,
        },
        [("idx_users_status", ["status"]), ("idx_users_plan", ["plan"])],
    ),
    "refresh_tokens": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "token_hash": "!text not null unique",
            "family": TEXT,
            "user_agent": TEXT,
            "ip": TEXT,
            "scopes": JSON,
            "revoked": BOOL,
            "rotated_from": TEXT,
            "expires_at": TS,
            "created_at": TS,
            "last_used_at": TS,
        },
        [("idx_rt_user", ["user_id"]), ("idx_rt_family", ["family"])],
    ),
    "verification_codes": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "purpose": "!text not null",
            "code_hash": "!text not null",
            "channel": TEXT,
            "attempts": "!integer not null default 0",
            "consumed": BOOL,
            "expires_at": TS,
            "created_at": TS,
        },
        [("idx_vc_user", ["user_id", "purpose"])],
    ),
    "api_keys": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "name": TEXT,
            "key_hash": "!text not null unique",
            "prefix": TEXT,
            "scopes": JSON,
            "active": BOOL,
            "last_used_at": TS,
            "expires_at": TS,
            "created_at": TS,
        },
        [("idx_keys_user", ["user_id"])],
    ),
    "consents": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "document": "!text not null",
            "version": "!text not null",
            "granted": BOOL,
            "scope": TEXT,
            "ip": TEXT,
            "user_agent": TEXT,
            "created_at": TS,
        },
        [("idx_consent_user", ["user_id", "document"])],
    ),
    # ---- virtual file system ----------------------------------------------
    "folders": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "parent_id": TEXT,
            "name": "!text not null",
            "path": "!text not null",
            "color": TEXT,
            "icon": TEXT,
            "shared": BOOL,
            "trashed": BOOL,
            "created_at": TS,
            "updated_at": TS,
        },
        [("idx_folders_user", ["user_id", "parent_id"]), ("idx_folders_path", ["user_id", "path"])],
    ),
    "files": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "folder_id": TEXT,
            "name": "!text not null",
            "path": "!text not null",
            "kind": "!text not null default 'document'",
            "mime": TEXT,
            "size": "!bigint not null default 0",
            "backend": "!text not null default 'local'",
            "storage_key": TEXT,
            "checksum": TEXT,
            "preview": JSON,
            "meta": JSON,
            "origin": TEXT,
            "pinned": BOOL,
            "trashed": BOOL,
            "version": "!integer not null default 1",
            "created_at": TS,
            "updated_at": TS,
        },
        [
            ("idx_files_user", ["user_id", "folder_id"]),
            ("idx_files_kind", ["user_id", "kind"]),
            ("idx_files_trash", ["user_id", "trashed"]),
        ],
    ),
    # ---- conversations -----------------------------------------------------
    "conversations": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "title": TEXT,
            "mode": "!text not null default 'assistant'",
            "persona": TEXT,
            "language": TEXT,
            "context": JSON,
            "pinned": BOOL,
            "archived": BOOL,
            "message_count": "!integer not null default 0",
            "token_usage": JSON,
            "created_at": TS,
            "updated_at": TS,
        },
        [("idx_conv_user", ["user_id", "archived", "updated_at"])],
    ),
    "messages": (
        {
            "id": PK,
            "conversation_id": "!text not null references conversations(id) on delete cascade",
            "user_id": "!text not null references users(id) on delete cascade",
            "role": "!text not null",
            "content": LONGTEXT,
            "attachments": JSON,
            "meta": JSON,
            "tokens": INT,
            "latency_ms": INT,
            "flagged": BOOL,
            "created_at": TS,
        },
        [("idx_msg_conv", ["conversation_id", "created_at"])],
    ),
    # ---- chess -------------------------------------------------------------
    "chess_games": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "white": TEXT,
            "black": TEXT,
            "event": TEXT,
            "side": "!text not null default 'white'",
            "mode": "!text not null default 'engine'",
            "start_fen": TEXT,
            "current_fen": TEXT,
            "san_moves": JSON,
            "uci_moves": JSON,
            "clock": JSON,
            "annotations": JSON,
            "evaluations": JSON,
            "result": TEXT,
            "status": "!text not null default 'active'",
            "engine_level": INT,
            "created_at": TS,
            "updated_at": TS,
        },
        [("idx_games_user", ["user_id", "status", "updated_at"])],
    ),
    # ---- terminal ----------------------------------------------------------
    "terminal_sessions": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "title": TEXT,
            "cwd": TEXT,
            "shell": TEXT,
            "sandboxed": BOOL,
            "status": "!text not null default 'active'",
            "created_at": TS,
            "closed_at": TS,
        },
        [("idx_term_user", ["user_id", "status"])],
    ),
    "terminal_commands": (
        {
            "id": PK,
            "session_id": "!text not null references terminal_sessions(id) on delete cascade",
            "user_id": "!text not null references users(id) on delete cascade",
            "command": LONGTEXT,
            "cwd": TEXT,
            "exit_code": INT,
            "duration_ms": INT,
            "output_bytes": INT,
            "blocked": BOOL,
            "reason": TEXT,
            "created_at": TS,
        },
        [("idx_cmd_session", ["session_id", "created_at"])],
    ),
    # ---- generated artefacts ----------------------------------------------
    "documents": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "file_id": TEXT,
            "kind": "!text not null",
            "title": TEXT,
            "template": TEXT,
            "language": TEXT,
            "spec": JSON,
            "stats": JSON,
            "status": "!text not null default 'ready'",
            "created_at": TS,
        },
        [("idx_docs_user", ["user_id", "kind", "created_at"])],
    ),
    "images": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "file_id": TEXT,
            "prompt": LONGTEXT,
            "negative_prompt": TEXT,
            "style": TEXT,
            "width": INT,
            "height": INT,
            "seed": BIGINT,
            "provider": "!text not null default 'internal'",
            "model": TEXT,
            "status": "!text not null default 'ready'",
            "duration_ms": INT,
            "created_at": TS,
        },
        [("idx_img_user", ["user_id", "created_at"])],
    ),
    # ---- remote workstation agent -----------------------------------------
    "agent_devices": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "name": TEXT,
            "platform": TEXT,
            "hostname": TEXT,
            "agent_version": TEXT,
            "device_key": "!text not null unique",
            "pairing_code_hash": TEXT,
            "scopes": JSON,
            "permission_profile": "!text not null default 'readonly'",
            "require_approval": BOOL,
            "trusted_paths": JSON,
            "status": "!text not null default 'pending'",
            "last_seen_at": TS,
            "revoked_at": TS,
            "created_at": TS,
        },
        [("idx_dev_user", ["user_id", "status"])],
    ),
    "agent_actions": (
        {
            "id": PK,
            "device_id": "!text not null references agent_devices(id) on delete cascade",
            "user_id": "!text not null references users(id) on delete cascade",
            "action": "!text not null",
            "payload": JSON,
            "requested_by": "!text not null default 'ai'",
            "approval_state": "!text not null default 'pending'",
            "approved_by": TEXT,
            "result": JSON,
            "risk": "!text not null default 'low'",
            "created_at": TS,
            "resolved_at": TS,
        },
        [("idx_act_device", ["device_id", "created_at"]), ("idx_act_approval", ["approval_state"])],
    ),
    # ---- trust & safety ----------------------------------------------------
    "audit_logs": (
        {
            "id": PK,
            "user_id": TEXT,
            "actor": "!text not null default 'user'",
            "action": "!text not null",
            "resource": TEXT,
            "resource_id": TEXT,
            "outcome": "!text not null default 'success'",
            "ip": TEXT,
            "user_agent": TEXT,
            "detail": JSON,
            "created_at": TS,
        },
        [("idx_audit_user", ["user_id", "created_at"]), ("idx_audit_action", ["action"])],
    ),
    "security_events": (
        {
            "id": PK,
            "user_id": TEXT,
            "kind": "!text not null",
            "severity": "!text not null default 'info'",
            "ip": TEXT,
            "user_agent": TEXT,
            "detail": JSON,
            "resolved": BOOL,
            "created_at": TS,
        },
        [("idx_sec_user", ["user_id", "created_at"]), ("idx_sec_kind", ["kind"])],
    ),
    "rate_counters": (
        {
            "id": PK,
            "bucket": "!text not null",
            "window_start": "!bigint not null default 0",
            "count": "!integer not null default 0",
            "chars": "!integer not null default 0",
            "updated_at": TS,
        },
        [("idx_rate_bucket", ["bucket"])],
    ),
    "jobs": (
        {
            "id": PK,
            "user_id": TEXT,
            "kind": "!text not null",
            "payload": JSON,
            "status": "!text not null default 'queued'",
            "progress": "!integer not null default 0",
            "result": JSON,
            "error": TEXT,
            "created_at": TS,
            "finished_at": TS,
        },
        [("idx_jobs_user", ["user_id", "status"])],
    ),
    "notifications": (
        {
            "id": PK,
            "user_id": "!text not null references users(id) on delete cascade",
            "title": TEXT,
            "body": TEXT,
            "level": "!text not null default 'info'",
            "read": BOOL,
            "link": TEXT,
            "created_at": TS,
        },
        [("idx_notif_user", ["user_id", "read", "created_at"])],
    ),
}


async def apply_migrations(database: Database) -> dict[str, Any]:
    """Create missing tables/indexes. Safe to call on every boot."""
    dialect = database.dialect
    executed = 0
    for table, (columns, indexes) in TABLES.items():
        for statement in _render(table, columns, dialect, indexes):
            try:
                await database.execute(statement)
                executed += 1
            except Exception as exc:  # pragma: no cover
                log.warning("migration %s: %s", table, exc)
    # Column drift repair: add columns that exist in the model but not the DB.
    added = await _repair_columns(database, dialect)
    relaxed = await _relax_timestamps(database, dialect)
    await _seed(database)
    if relaxed:
        log.info("relaxed not-null on %s timestamp column(s)", relaxed)
    log.info("migrations applied (%s): %s statements, %s columns added", dialect, executed, added)
    return {"statements": executed, "columns_added": added, "tables": len(TABLES)}


async def _repair_columns(database: Database, dialect: str) -> int:
    added = 0
    for table, (columns, _) in TABLES.items():
        try:
            if dialect == "postgres":
                rows = await database.fetch(
                    "select column_name from information_schema.columns where table_name = $1",
                    table,
                )
            else:
                rows = await database.fetch(f"pragma table_info({table})")
                existing = {r["name"] for r in rows.rows}
                rows = None
        except Exception:
            continue
        if rows is not None:
            existing = {r["column_name"] for r in rows.rows}
        for name, kind in columns.items():
            if name in existing:
                continue
            rendered = kind[1:] if kind.startswith("!") else _TYPE_MAP[dialect].get(kind, "text")
            rendered = rendered.replace("primary key", "").replace("not null", "").replace("unique", "").strip()
            rendered = re.sub(r"\s*default\s+.*$", "", rendered, flags=re.I).strip() or "text"
            try:
                await database.execute(f"alter table {table} add column {name} {rendered}")
                added += 1
            except Exception as exc:
                log.debug("add column %s.%s: %s", table, name, exc)
    return added


async def _relax_timestamps(database: Database, dialect: str) -> int:
    """Repair timestamp columns on tables created by earlier builds.

    Two defects are corrected idempotently on Postgres:
      * event timestamps (locked_until, deleted_at, expires_at, …) were rendered
        NOT NULL DEFAULT now(), so every INSERT silently stamped them and, for
        deleted_at, made fresh accounts look soft-deleted. They must be nullable
        with no default.
      * created_at / updated_at should keep DEFAULT now() but may stay nullable.
    """
    if dialect != "postgres":
        return 0
    auto = {"created_at", "updated_at"}
    touched = 0
    for table, (columns, _) in TABLES.items():
        for name, kind in columns.items():
            if kind != TS:
                continue
            try:
                await database.execute(f"alter table {table} alter column {name} drop not null")
                if name in auto:
                    await database.execute(f"alter table {table} alter column {name} set default now()")
                else:
                    await database.execute(f"alter table {table} alter column {name} drop default")
                touched += 1
            except Exception as exc:
                log.debug("fix ts %s.%s: %s", table, name, exc)
    return touched


async def _seed(database: Database) -> None:
    """Bootstrap records every deployment needs."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    existing = await database.fetchval("select count(*) from users")
    if not existing:
        from .security import hash_password

        await database.insert(
            "users",
            {
                "id": "usr_owner0000000000001",
                "email": "owner@mestinghem.ai",
                "username": "owner",
                "display_name": "Owner",
                "password_hash": hash_password("Mestinghem-Owner-2026"),
                "kdf": "bcrypt+pbkdf2",
                "language": "ru",
                "theme": "aurum",
                "plan": "enterprise",
                "role": "owner",
                "status": "active",
                "preferences": {"animations": True, "sound": False},
                "permissions": {
                    "terminal": True,
                    "files": True,
                    "chess": True,
                    "documents": True,
                    "images": True,
                    "agent": True,
                    "admin": True,
                },
                "email_verified": True,
                "created_at": now,
                "updated_at": now,
            },
        )
        log.info("seeded owner account")

