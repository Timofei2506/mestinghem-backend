"""Image generation API."""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..core.db import db, new_id
from ..security import audit, screen_input
from ..services.file_service import FileError, file_service
from ..services.image_service import ImageError, image_service
from .deps import Throttler, get_current_user, utcnow

log = logging.getLogger("images")
router = APIRouter(prefix="/api/images", tags=["images"])


class GenerateBody(BaseModel):
    prompt: str = Field(min_length=3, max_length=900)
    negative: str = Field(default="", max_length=300)
    style: str = Field(default="none", max_length=24)
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    seed: int | None = None
    variations: int = Field(default=1, ge=1, le=4)
    folder_id: str | None = None
    engine: str | None = Field(default=None, max_length=48)


@router.get("/styles")
async def styles(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"styles": image_service.styles(user.get("language") or "ru")}


@router.get("/engines")
async def engines(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"engines": image_service.engines(user.get("language") or "ru")}


async def _generate(body: GenerateBody, user: dict[str, Any]) -> dict[str, Any]:
    verdict = screen_input(body.prompt, strict=True)
    if not verdict.allowed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Промпт отклонён политикой контента")
    language = user.get("language") or "ru"
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for index in range(body.variations):
        seed = (body.seed + index * 7919) if body.seed is not None else None
        try:
            data, meta = await image_service.generate(
                body.prompt,
                width=body.width,
                height=body.height,
                style=body.style,
                seed=seed,
                language=language,
                negative=body.negative,
                model=body.engine,
            )
        except ImageError as exc:
            errors.append(str(exc)[:160])
            continue
        slug = re.sub(r"[^a-z0-9а-яё]+", "-", body.prompt.lower()).strip("-")[:40] or "image"
        try:
            file_record = await file_service.save(
                user["id"],
                name=f"{slug}-{index + 1}.jpg",
                payload=data,
                folder_id=body.folder_id,
                kind="image",
                mime="image/jpeg",
                origin="generated",
                meta={"prompt": body.prompt[:900], "style": body.style, "seed": meta.get("seed")},
                preview={"width": meta.get("width"), "height": meta.get("height")},
                namespace=f"images/{user['id'][:12]}",
            )
        except FileError as exc:
            errors.append(str(exc)[:160])
            continue
        image_id = new_id("img_")
        await db.insert(
            "images",
            {
                "id": image_id, "user_id": user["id"], "file_id": file_record["id"],
                "prompt": body.prompt[:2000], "negative_prompt": body.negative[:300],
                "style": body.style, "width": meta.get("width") or body.width,
                "height": meta.get("height") or body.height, "seed": meta.get("seed") or 0,
                "provider": "internal", "model": meta.get("engine") or "mst-diffusion-v1", "status": "ready",
                "duration_ms": 0, "created_at": utcnow(),
            },
        )
        results.append(
            {
                "id": image_id,
                "file_id": file_record["id"],
                "url": f"/api/images/{image_id}/file",
                "download_url": f"/api/files/{file_record['id']}/download",
                "prompt": body.prompt,
                "style": body.style,
                "width": meta.get("width"),
                "height": meta.get("height"),
                "seed": meta.get("seed"),
                "size": file_record["size"],
                "engine": meta.get("engine") or "mst-diffusion-v1",
            }
        )

    if not results:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            errors[0] if errors else "Генерация недоступна, попробуйте позже")
    await audit("image.generate", user_id=user["id"], resource="image",
                resource_id=results[0]["id"], detail={"style": body.style, "count": len(results)})
    engine_label = (results[0].get("engine") if results else None) or "mst-diffusion-v1"
    return {"images": results, "errors": errors, "engine": engine_label}


@router.post("/generate", status_code=status.HTTP_201_CREATED)
async def generate(
    body: GenerateBody,
    user: dict[str, Any] = Depends(Throttler("image", limit=20)),
) -> dict[str, Any]:
    return await _generate(body, user)


@router.get("/")
async def list_images(limit: int = 48, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    rows = await db.fetch(
        "select id, file_id, prompt, style, width, height, seed, status, created_at "
        "from images where user_id = $1 order by created_at desc limit $2",
        user["id"], max(1, min(200, limit)),
    )
    return {
        "images": [{**r, "url": f"/api/images/{r['id']}/file"} for r in rows.rows],
    }


@router.get("/{image_id}")
async def get_image(image_id: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    row = await db.fetch(
        "select * from images where id = $1 and user_id = $2", image_id, user["id"]
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Изображение не найдено")
    row["url"] = f"/api/images/{image_id}/file"
    return row


@router.get("/{image_id}/file")
async def image_bytes(image_id: str, user: dict[str, Any] = Depends(get_current_user)) -> Response:
    row = await db.fetch(
        "select file_id from images where id = $1 and user_id = $2", image_id, user["id"]
    ).first()
    if not row or not row.get("file_id"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Изображение не найдено")
    try:
        data, meta = await file_service.read_bytes(user["id"], row["file_id"])
    except (FileError, FileNotFoundError):
        raise HTTPException(status.HTTP_410_GONE, "Файл недоступен")
    return Response(
        content=data,
        media_type=meta.get("mime") or "image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete("/{image_id}")
async def delete_image(image_id: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    row = await db.fetch(
        "select file_id from images where id = $1 and user_id = $2", image_id, user["id"]
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Изображение не найдено")
    if row.get("file_id"):
        try:
            await file_service.delete(user["id"], row["file_id"], permanent=True)
        except FileError:
            pass
    await db.execute("delete from images where id = $1 and user_id = $2", image_id, user["id"])
    await audit("image.delete", user_id=user["id"], resource="image", resource_id=image_id)
    return {"status": "ok"}


@router.post("/{image_id}/variate")
async def variate(
    image_id: str, body: dict[str, Any], user: dict[str, Any] = Depends(Throttler("image", limit=20))
) -> dict[str, Any]:
    row = await db.fetch(
        "select prompt, style, width, height from images where id = $1 and user_id = $2", image_id, user["id"]
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Изображение не найдено")
    payload = GenerateBody(
        prompt=str(body.get("prompt") or row["prompt"]),
        style=str(body.get("style") or row["style"] or "none"),
        width=int(body.get("width") or row["width"] or 1024),
        height=int(body.get("height") or row["height"] or 1024),
        variations=min(int(body.get("variations") or 2), 4),
    )
    return await _generate(payload, user)
