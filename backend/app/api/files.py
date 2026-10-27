"""Virtual file system API ("Directory")."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.db import db
from ..security import audit
from ..services.file_service import FileError, file_service, icon_of, content_disposition
from .deps import Throttler, client_ip, get_current_user

log = logging.getLogger("files")
router = APIRouter(prefix="/api/files", tags=["files"])


class FolderBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    parent_id: str | None = None


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MoveBody(BaseModel):
    folder_id: str | None = None


class TextBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(max_length=4_000_000)
    folder_id: str | None = None


def _guard(exc: FileError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.get("/")
async def browse(
    folder_id: str | None = None,
    kind: str | None = None,
    search: str | None = None,
    trashed: bool = False,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        return await file_service.list(
            user["id"], folder_id=folder_id, kind=kind, search=search,
            trashed=trashed, limit=limit, offset=offset,
        )
    except FileError as exc:
        raise _guard(exc)


@router.get("/stats")
async def stats(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return await file_service.quota(user["id"])


@router.get("/search")
async def search(q: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"results": await file_service.search(user["id"], q)}


@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(body: FolderBody, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        result = await file_service.create_folder(user["id"], body.name, body.parent_id)
    except FileError as exc:
        raise _guard(exc)
    await audit("file.folder_create", user_id=user["id"], resource="folder", resource_id=result["id"])
    return result


@router.patch("/folders/{folder_id}")
async def rename_folder(folder_id: str, body: RenameBody, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await file_service.rename_folder(user["id"], folder_id, body.name)
    except FileError as exc:
        raise _guard(exc)


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str, permanent: bool = False, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        result = await file_service.delete_folder(user["id"], folder_id, permanent=permanent)
    except FileError as exc:
        raise _guard(exc)
    await audit("file.folder_delete", user_id=user["id"], resource="folder", resource_id=folder_id,
                detail={"permanent": permanent})
    return result


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    folder_id: str | None = None,
    user: dict[str, Any] = Depends(Throttler("upload", limit=30)),
) -> dict[str, Any]:
    payload = await file.read()
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пустой файл")
    if len(payload) > settings.upload_max_mb * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"Файл больше {settings.upload_max_mb} МБ")
    try:
        result = await file_service.save(
            user["id"], name=file.filename or "upload.bin", payload=payload,
            folder_id=folder_id, origin="upload",
            meta={"uploaded_from": client_ip(request)},
        )
    except FileError as exc:
        raise _guard(exc)
    await audit("file.upload", user_id=user["id"], resource="file", resource_id=result["id"],
                detail={"size": result["size"], "kind": result["kind"]})
    return result


@router.post("/text", status_code=status.HTTP_201_CREATED)
async def create_text(body: TextBody, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        result = await file_service.save(
            user["id"], name=body.name, payload=body.content.encode("utf-8"),
            folder_id=body.folder_id, origin="editor", meta={"created_by": "user"},
        )
    except FileError as exc:
        raise _guard(exc)
    await audit("file.create_text", user_id=user["id"], resource="file", resource_id=result["id"])
    return result


@router.get("/{file_id}")
async def get_file(file_id: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        row = await file_service.get(user["id"], file_id)
    except FileError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    row.pop("storage_key", None)
    return {
        **row,
        "download_url": f"/api/files/{file_id}/download",
        "icon": icon_of(row.get("kind") or "file"),
    }


@router.get("/{file_id}/download")
async def download(file_id: str, inline: bool = False, user: dict[str, Any] = Depends(get_current_user)) -> Response:
    try:
        data, row = await file_service.read_bytes(user["id"], file_id)
    except FileError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    except FileNotFoundError:
        raise HTTPException(status.HTTP_410_GONE, "Файл недоступен в хранилище")
    disposition = "inline" if inline or (row.get("kind") in ("image", "document") and row.get("mime", "").startswith(("image/", "text/"))) else "attachment"
    await audit("file.download", user_id=user["id"], resource="file", resource_id=file_id)
    return Response(
        content=data,
        media_type=row.get("mime") or "application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(row["name"], inline=disposition == "inline"),
            "Cache-Control": "private, max-age=60",
        },
    )


@router.patch("/{file_id}")
async def patch_file(file_id: str, body: dict[str, Any], user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        if "name" in body:
            return await file_service.rename(user["id"], file_id, str(body["name"]))
        if "folder_id" in body:
            return await file_service.move(user["id"], file_id, body["folder_id"])
        if "pinned" in body:
            return await file_service.set_pinned(user["id"], file_id, bool(body["pinned"]))
        if "trashed" in body:
            return await file_service.set_trashed(user["id"], file_id, bool(body["trashed"]))
    except FileError as exc:
        raise _guard(exc)
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нет изменений")


@router.delete("/{file_id}")
async def delete_file(file_id: str, permanent: bool = True, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        result = await file_service.delete(user["id"], file_id, permanent=permanent)
    except FileError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    await audit("file.delete", user_id=user["id"], resource="file", resource_id=file_id,
                detail={"permanent": permanent})
    return result


@router.get("/tree/full")
async def full_tree(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    folders = await db.fetch(
        "select id, name, path, parent_id, icon from folders where user_id = $1 and trashed = false order by path",
        user["id"],
    )
    counts = await db.fetch(
        "select folder_id, count(*) as c, coalesce(sum(size),0) as bytes from files "
        "where user_id = $1 and trashed = false group by folder_id", user["id"],
    )
    lookup = {c["folder_id"]: c for c in counts.rows}
    return {
        "folders": [
            {
                **f,
                "files": int((lookup.get(f["id"]) or {}).get("c") or 0),
                "bytes": int((lookup.get(f["id"]) or {}).get("bytes") or 0),
            }
            for f in folders.rows
        ]
    }
