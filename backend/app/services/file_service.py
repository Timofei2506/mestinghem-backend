"""
Virtual file system ("Directory").

Metadata lives in the database, bytes live in object storage. Users see a
tree of folders and files with paths, kinds, previews and versioning — the
underlying storage key is never exposed.
"""
from __future__ import annotations

import logging
import mimetypes
import re
from datetime import datetime, timezone
from typing import Any

from ..core.config import settings
from ..core.db import Database, db, new_id
from .storage_service import storage

log = logging.getLogger("files")

_KIND_BY_EXT = {
    "pptx": "presentation", "ppt": "presentation", "key": "presentation",
    "xlsx": "spreadsheet", "xls": "spreadsheet", "csv": "spreadsheet", "tsv": "spreadsheet",
    "docx": "document", "doc": "document", "pdf": "document", "txt": "document", "md": "document",
    "rtf": "document", "odt": "document",
    "png": "image", "jpg": "image", "jpeg": "image", "webp": "image", "gif": "image",
    "bmp": "image", "svg": "image", "avif": "image",
    "mp3": "audio", "wav": "audio", "ogg": "audio", "flac": "audio", "m4a": "audio",
    "mp4": "video", "mov": "video", "webm": "video", "mkv": "video", "avi": "video",
    "zip": "archive", "tar": "archive", "gz": "archive", "7z": "archive", "rar": "archive",
    "py": "code", "js": "code", "ts": "code", "tsx": "code", "jsx": "code", "json": "code",
    "html": "code", "css": "code", "sql": "code", "sh": "code", "yaml": "code", "yml": "code",
    "go": "code", "rs": "code", "c": "code", "cpp": "code", "java": "code", "rb": "code",
}

_ICON = {
    "presentation": "slides", "spreadsheet": "sheet", "document": "doc", "image": "image",
    "audio": "audio", "video": "video", "archive": "archive", "code": "code", "file": "file",
}

_UNSAFE = re.compile(r"[^\w\-. \u0400-\u04FF()]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """Build a latin-1-safe Content-Disposition value (RFC 5987/6266).

    HTTP headers cannot carry raw non-ASCII, so a Cyrillic/Unicode filename is
    percent-encoded into ``filename*`` with a transliterated ASCII fallback.
    """
    from urllib.parse import quote

    name = (filename or "download").strip() or "download"
    ascii_fallback = name.encode("ascii", "ignore").decode("ascii").strip() or "download"
    ascii_fallback = ascii_fallback.replace('"', "").replace("\\", "_")
    disposition = "inline" if inline else "attachment"
    encoded = quote(name, safe="")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


def safe_name(name: str, *, max_len: int = 120) -> str:
    name = (name or "без имени").strip().replace("\x00", "")
    name = _UNSAFE.sub("_", name)
    name = re.sub(r"\s{2,}", " ", name).strip(" ._")
    return (name or "без имени")[:max_len]


def kind_of(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _KIND_BY_EXT.get(ext, "file")


def icon_of(kind: str) -> str:
    return _ICON.get(kind, "file")


class FileError(ValueError):
    pass


class FileService:
    # ------------------------------------------------------------- bootstrap
    async def bootstrap_workspace(self, user_id: str) -> dict[str, Any]:
        """Create the default folder tree + a welcome document for a new user."""
        existing = await db.fetchval("select count(*) from folders where user_id = $1", user_id, default=0)
        if existing:
            return {"created": 0}
        created = 0
        for name, icon in (
            ("Документы", "doc"), ("Презентации", "slides"), ("Таблицы", "sheet"),
            ("Изображения", "image"), ("Проекты", "folder"), ("Архив", "archive"),
        ):
            await db.insert(
                "folders",
                {
                    "id": new_id("fld_"),
                    "user_id": user_id,
                    "parent_id": None,
                    "name": name,
                    "path": f"/{name}",
                    "color": "gold",
                    "icon": icon,
                    "shared": False,
                    "trashed": False,
                    "created_at": _now(),
                    "updated_at": _now(),
                },
            )
            created += 1
        return {"created": created}

    # --------------------------------------------------------------- folders
    async def create_folder(self, user_id: str, name: str, parent_id: str | None = None) -> dict[str, Any]:
        clean = safe_name(name, max_len=60)
        parent = None
        if parent_id:
            parent = await db.fetch(
                "select * from folders where id = $1 and user_id = $2", parent_id, user_id
            ).first()
            if not parent:
                raise FileError("parent folder not found")
        path = f"{parent['path']}/{clean}" if parent else f"/{clean}"
        duplicate = await db.fetch(
            "select id from folders where user_id = $1 and path = $2 and trashed = false", user_id, path
        ).first()
        if duplicate:
            raise FileError("folder already exists")
        folder_id = new_id("fld_")
        await db.insert(
            "folders",
            {
                "id": folder_id, "user_id": user_id, "parent_id": parent_id, "name": clean,
                "path": path, "color": "gold", "icon": "folder", "shared": False, "trashed": False,
                "created_at": _now(), "updated_at": _now(),
            },
        )
        return {"id": folder_id, "name": clean, "path": path, "parent_id": parent_id}

    async def rename_folder(self, user_id: str, folder_id: str, name: str) -> dict[str, Any]:
        row = await db.fetch("select * from folders where id = $1 and user_id = $2", folder_id, user_id).first()
        if not row:
            raise FileError("folder not found")
        clean = safe_name(name, max_len=60)
        prefix = row["path"].rsplit("/", 1)[0]
        new_path = f"{prefix}/{clean}" if prefix else f"/{clean}"
        await db.execute(
            "update folders set name = $1, path = $2, updated_at = $3 where id = $4",
            clean, new_path, _now(), folder_id,
        )
        return {"id": folder_id, "name": clean, "path": new_path}

    async def delete_folder(self, user_id: str, folder_id: str, *, permanent: bool = False) -> dict[str, Any]:
        row = await db.fetch("select * from folders where id = $1 and user_id = $2", folder_id, user_id).first()
        if not row:
            raise FileError("folder not found")
        if permanent:
            await db.execute("delete from folders where id = $1 or (user_id = $2 and path like $3)",
                             folder_id, user_id, row["path"] + "/%")
            return {"deleted": True}
        await db.execute("update folders set trashed = true, updated_at = $1 where id = $2", _now(), folder_id)
        await db.execute(
            "update files set trashed = true, updated_at = $1 where user_id = $2 and (folder_id = $3 or path like $4)",
            _now(), user_id, folder_id, row["path"] + "/%",
        )
        return {"trashed": True}

    # ----------------------------------------------------------------- files
    async def save(
        self,
        user_id: str,
        *,
        name: str,
        payload: bytes,
        folder_id: str | None = None,
        kind: str | None = None,
        mime: str | None = None,
        origin: str = "upload",
        meta: dict[str, Any] | None = None,
        preview: dict[str, Any] | None = None,
        namespace: str | None = None,
    ) -> dict[str, Any]:
        clean = safe_name(name)
        if len(payload) > settings.upload_max_mb * 1024 * 1024:
            raise FileError(f"file exceeds {settings.upload_max_mb} MB limit")
        folder = None
        if folder_id:
            folder = await db.fetch(
                "select * from folders where id = $1 and user_id = $2", folder_id, user_id
            ).first()
        path = f"{folder['path']}/{clean}" if folder else f"/{clean}"
        resolved_kind = kind or kind_of(clean)
        content_type = mime or mimetypes.guess_type(clean)[0] or "application/octet-stream"
        stored = await storage.put(
            payload, clean, namespace=namespace or f"files/{user_id[:12]}", mime=content_type
        )
        file_id = new_id("fil_")
        await db.insert(
            "files",
            {
                "id": file_id, "user_id": user_id, "folder_id": folder_id, "name": clean, "path": path,
                "kind": resolved_kind, "mime": content_type, "size": stored.size, "backend": stored.backend,
                "storage_key": stored.key, "checksum": stored.checksum,
                "preview": preview or {}, "meta": meta or {}, "origin": origin, "pinned": False,
                "trashed": False, "version": 1, "created_at": _now(), "updated_at": _now(),
            },
        )
        return {
            "id": file_id, "name": clean, "path": path, "kind": resolved_kind, "icon": icon_of(resolved_kind),
            "size": stored.size, "mime": content_type, "checksum": stored.checksum,
            "created_at": _now(), "origin": origin,
        }

    async def list(
        self,
        user_id: str,
        *,
        folder_id: str | None = None,
        kind: str | None = None,
        search: str | None = None,
        trashed: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        clauses = ["user_id = $1"]
        params: list[Any] = [user_id]
        if trashed:
            clauses.append("trashed = true")
        else:
            clauses.append("trashed = false")
        if folder_id:
            params.append(folder_id)
            clauses.append(f"folder_id = ${len(params)}")
        if kind:
            params.append(kind)
            clauses.append(f"kind = ${len(params)}")
        if search:
            params.append(f"%{search.strip()[:60]}%")
            clauses.append(f"(name ilike ${len(params)} or path ilike ${len(params)})")
        params.append(limit)
        limit_sql = f"${len(params)}"
        params.append(offset)
        offset_sql = f"${len(params)}"
        where = " and ".join(clauses)
        rows = await db.fetch(
            f"select id, name, path, kind, mime, size, preview, meta, origin, pinned, folder_id, "
            f"version, created_at, updated_at from files where {where} "
            f"order by pinned desc, updated_at desc limit {limit_sql} offset {offset_sql}",
            *params,
        )
        total = await db.fetchval(f"select count(*) from files where {where}", *params[:-2], default=0)
        folders_rows = await db.fetch(
            "select id, name, path, icon, color, parent_id, created_at, updated_at from folders "
            "where user_id = $1 and trashed = $2 and ($3::text is null or parent_id = $3) order by name",
            user_id, bool(trashed), folder_id,
        ) if db.dialect == "postgres" else await db.fetch(
            "select id, name, path, icon, color, parent_id, created_at, updated_at from folders "
            "where user_id = $1 and trashed = $2 order by name",
            user_id, bool(trashed),
        )
        files = [
            {**row, "icon": icon_of(row.get("kind") or "file"), "pinned": bool(row.get("pinned"))}
            for row in rows.rows
        ]
        return {
            "folders": folders_rows.rows,
            "files": files,
            "total": int(total or 0),
            "limit": limit,
            "offset": offset,
        }

    async def get(self, user_id: str, file_id: str) -> dict[str, Any]:
        row = await db.fetch("select * from files where id = $1 and user_id = $2", file_id, user_id).first()
        if not row:
            raise FileError("file not found")
        return row

    async def read_bytes(self, user_id: str, file_id: str) -> tuple[bytes, dict[str, Any]]:
        row = await self.get(user_id, file_id)
        data = await storage.get(row["storage_key"])
        return data, row

    async def rename(self, user_id: str, file_id: str, name: str) -> dict[str, Any]:
        row = await self.get(user_id, file_id)
        clean = safe_name(name)
        prefix = row["path"].rsplit("/", 1)[0]
        await db.execute(
            "update files set name = $1, path = $2, updated_at = $3 where id = $4",
            clean, f"{prefix}/{clean}", _now(), file_id,
        )
        return {"id": file_id, "name": clean, "path": f"{prefix}/{clean}"}

    async def move(self, user_id: str, file_id: str, folder_id: str | None) -> dict[str, Any]:
        row = await self.get(user_id, file_id)
        folder = None
        if folder_id:
            folder = await db.fetch(
                "select * from folders where id = $1 and user_id = $2", folder_id, user_id
            ).first()
            if not folder:
                raise FileError("folder not found")
        path = f"{folder['path']}/{row['name']}" if folder else f"/{row['name']}"
        await db.execute(
            "update files set folder_id = $1, path = $2, updated_at = $3 where id = $4",
            folder_id, path, _now(), file_id,
        )
        return {"id": file_id, "folder_id": folder_id, "path": path}

    async def set_trashed(self, user_id: str, file_id: str, trashed: bool) -> dict[str, Any]:
        await self.get(user_id, file_id)
        await db.execute(
            "update files set trashed = $1, updated_at = $2 where id = $3", bool(trashed), _now(), file_id
        )
        return {"id": file_id, "trashed": bool(trashed)}

    async def delete(self, user_id: str, file_id: str, *, permanent: bool = True) -> dict[str, Any]:
        row = await self.get(user_id, file_id)
        if permanent and row.get("storage_key"):
            await storage.delete(row["storage_key"])
        await db.execute("delete from files where id = $1 and user_id = $2", file_id, user_id)
        return {"id": file_id, "deleted": True}

    async def set_pinned(self, user_id: str, file_id: str, pinned: bool) -> dict[str, Any]:
        await self.get(user_id, file_id)
        await db.execute("update files set pinned = $1, updated_at = $2 where id = $3", bool(pinned), _now(), file_id)
        return {"id": file_id, "pinned": bool(pinned)}

    async def stats(self, user_id: str) -> dict[str, Any]:
        rows = await db.fetch(
            "select kind, count(*) as count, coalesce(sum(size), 0) as bytes from files "
            "where user_id = $1 and trashed = false group by kind order by bytes desc", user_id,
        )
        total = sum(int(r["bytes"] or 0) for r in rows.rows)
        quota = {"free": 500, "pro": 25_000, "enterprise": 250_000}
        return {
            "total_bytes": total,
            "total_mb": round(total / (1024 * 1024), 2),
            "by_kind": [{"kind": r["kind"], "count": int(r["count"]), "bytes": int(r["bytes"] or 0)} for r in rows.rows],
            "files": int(sum(r["count"] for r in rows.rows)),
        }

    async def purge_user_files(self, user_id: str) -> int:
        rows = await db.fetch("select id, storage_key from files where user_id = $1", user_id)
        removed = 0
        for row in rows.rows:
            if row.get("storage_key"):
                try:
                    await storage.delete(row["storage_key"])
                    removed += 1
                except Exception:
                    pass
        await db.execute("delete from files where user_id = $1", user_id)
        await db.execute("delete from folders where user_id = $1", user_id)
        return removed

    async def search(self, user_id: str, query: str, limit: int = 40) -> list[dict[str, Any]]:
        pattern = f"%{query.strip()[:60]}%"
        rows = await db.fetch(
            "select id, name, path, kind, size, updated_at from files "
            "where user_id = $1 and trashed = false and (name ilike $2 or path ilike $2) "
            "order by updated_at desc limit $3",
            user_id, pattern, limit,
        )
        return [{**r, "icon": icon_of(r.get("kind") or "file")} for r in rows.rows]

    async def quota(self, user_id: str) -> dict[str, Any]:
        stats = await self.stats(user_id)
        return {**stats, "quota_mb": settings.upload_max_mb * 100, "backend": storage.health()["backend"]}


file_service = FileService()
bootstrap_workspace = file_service.bootstrap_workspace
purge_user_files = file_service.purge_user_files
