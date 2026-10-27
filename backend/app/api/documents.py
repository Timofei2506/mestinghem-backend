"""Document generation API — presentations, spreadsheets, documents."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..core.db import db
from ..security import audit
from ..services.documents import DocumentError, KINDS, document_service
from ..services.file_service import FileError, content_disposition, file_service
from .deps import Throttler, get_current_user, utcnow

log = logging.getLogger("docs")
router = APIRouter(prefix="/api/documents", tags=["documents"])


class BuildBody(BaseModel):
    kind: str = Field(pattern="^(pptx|xlsx|docx|md|html)$")
    brief: str = Field(min_length=10, max_length=6000)
    title: str | None = Field(default=None, max_length=140)
    language: str = Field(default="ru", pattern="^(ru|en)$")
    size: str = Field(default="medium", pattern="^(short|medium|long)$")
    audience: str = Field(default="", max_length=160)
    tone: str = Field(default="", max_length=160)
    extra: str = Field(default="", max_length=1200)
    folder_id: str | None = None
    use_ai: bool = True


class SpecBody(BaseModel):
    kind: str = Field(pattern="^(pptx|xlsx|docx|md|html)$")
    spec: dict[str, Any]
    title: str | None = Field(default=None, max_length=140)
    folder_id: str | None = None


class RebuildBody(BaseModel):
    brief: str | None = Field(default=None, max_length=6000)


@router.get("/kinds")
async def kinds(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    language = (user.get("language") or "ru").lower()
    ru = language.startswith("ru")
    return {
        "kinds": [
            {
                "id": key,
                "label": value["label"] if ru else {"pptx": "Presentation", "xlsx": "Excel workbook",
                                                    "docx": "Word document", "md": "Markdown",
                                                    "html": "HTML page"}[key],
                "ext": value["ext"],
            }
            for key, value in KINDS.items()
        ],
        "sizes": [
            {"id": "short", "label": "Кратко" if ru else "Short"},
            {"id": "medium", "label": "Стандарт" if ru else "Standard"},
            {"id": "long", "label": "Подробно" if ru else "Detailed"},
        ],
        "templates": [
            {"id": "aurum-dark", "label": "Aurum Dark" if not ru else "Aurum Dark (тёмный золотой)"},
            {"id": "obsidian", "label": "Obsidian" if not ru else "Obsidian (графит)"},
        ],
    }


@router.post("/build", status_code=status.HTTP_201_CREATED)
async def build(
    body: BuildBody, user: dict[str, Any] = Depends(Throttler("document", limit=25))
) -> dict[str, Any]:
    try:
        result = await document_service.create(
            db,
            user_id=user["id"],
            kind=body.kind,
            brief=body.brief,
            title=body.title,
            folder_id=body.folder_id,
            language=body.language,
            size=body.size,
            audience=body.audience,
            tone=body.tone,
            extra=body.extra,
            use_ai=body.use_ai,
        )
    except DocumentError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    except Exception as exc:  # pragma: no cover
        log.exception("document build failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Не удалось собрать документ")
    await audit("document.build", user_id=user["id"], resource="document", resource_id=result["document_id"],
                detail={"kind": body.kind, "fallback": result.get("fallback", False)})
    result["download_url"] = f"/api/documents/{result['document_id']}/download"
    result["preview_url"] = f"/api/documents/{result['document_id']}/preview"
    return result


@router.post("/from-spec", status_code=status.HTTP_201_CREATED)
async def from_spec(body: SpecBody, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    try:
        result = await document_service.create(
            db, user_id=user["id"], kind=body.kind, brief=body.title or "spec",
            title=body.title, folder_id=body.folder_id, spec_override=body.spec,
            language=body.spec.get("language") or user.get("language") or "ru",
        )
    except DocumentError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    await audit("document.from_spec", user_id=user["id"], resource="document", resource_id=result["document_id"])
    result["download_url"] = f"/api/documents/{result['document_id']}/download"
    return result


@router.get("/")
async def list_documents(
    kind: str | None = None, limit: int = 50, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    rows = await db.fetch(
        "select d.id, d.kind, d.title, d.language, d.stats, d.status, d.created_at, d.file_id, "
        "f.size as size, f.name as filename "
        "from documents d left join files f on f.id = d.file_id "
        "where d.user_id = $1 order by d.created_at desc limit $2",
        user["id"], max(1, min(200, limit)),
    )
    items = []
    for row in rows.rows:
        if kind and row.get("kind") != kind:
            continue
        items.append(
            {
                **row,
                "download_url": f"/api/documents/{row['id']}/download",
                "preview_url": f"/api/documents/{row['id']}/preview",
            }
        )
    return {"documents": items}


async def _load(document_id: str, user_id: str) -> dict[str, Any]:
    row = await db.fetch(
        "select * from documents where id = $1 and user_id = $2", document_id, user_id
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Документ не найден")
    return row


@router.get("/{document_id}")
async def get_document(document_id: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    row = await _load(document_id, user["id"])
    return {
        "id": row["id"], "kind": row["kind"], "title": row["title"], "language": row["language"],
        "template": row["template"], "stats": row["stats"], "status": row["status"],
        "spec": row["spec"], "file_id": row["file_id"], "created_at": row["created_at"],
        "download_url": f"/api/documents/{document_id}/download",
    }


@router.post("/{document_id}/rebuild")
async def rebuild(
    document_id: str, body: RebuildBody, user: dict[str, Any] = Depends(Throttler("document", limit=25))
) -> dict[str, Any]:
    await _load(document_id, user["id"])
    try:
        result = await document_service.regenerate(
            db, document_id=document_id, user_id=user["id"], brief=body.brief
        )
    except DocumentError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    await audit("document.rebuild", user_id=user["id"], resource="document", resource_id=document_id)
    result["download_url"] = f"/api/documents/{document_id}/download"
    return result


@router.get("/{document_id}/download")
async def download(document_id: str, user: dict[str, Any] = Depends(get_current_user)) -> Response:
    row = await _load(document_id, user["id"])
    if not row.get("file_id"):
        raise HTTPException(status.HTTP_410_GONE, "Файл не найден")
    try:
        data, meta = await file_service.read_bytes(user["id"], row["file_id"])
    except (FileError, FileNotFoundError):
        # Re-render on demand if the object is missing (e.g. storage migrated).
        payload, mime, ext = await document_service.render(row["kind"], row["spec"] or {})
        data, meta = payload, {"mime": mime, "name": f"{row['title']}.{ext}"}
    await audit("document.download", user_id=user["id"], resource="document", resource_id=document_id)
    return Response(
        content=data,
        media_type=meta.get("mime") or "application/octet-stream",
        headers={"Content-Disposition": content_disposition(meta.get("name") or f'{row["title"]}.{row["kind"]}')},
    )


@router.get("/{document_id}/preview")
async def preview(document_id: str, user: dict[str, Any] = Depends(get_current_user)) -> Response:
    """HTML preview for any kind — office files are summarised structurally."""
    from ..services import documents_render as render

    row = await _load(document_id, user["id"])
    spec = row["spec"] or {}
    if row["kind"] in ("md", "html", "docx"):
        html = render.render_html(spec, language=row.get("language") or "ru")
    else:
        html = _office_preview_html(row, spec)
    return Response(content=html, media_type="text/html; charset=utf-8")


def _office_preview_html(row: dict[str, Any], spec: dict[str, Any]) -> str:
    import html as html_lib

    items: list[str] = []
    if row["kind"] == "pptx":
        for index, slide in enumerate(spec.get("slides") or [], start=1):
            points = "".join(f"<li>{html_lib.escape(str(p))}</li>" for p in slide.get("points") or [])
            items.append(
                f'<section class="slide"><div class="num">{index:02d}</div>'
                f'<h3>{html_lib.escape(str(slide.get("title") or ""))}</h3><ul>{points}</ul></section>'
            )
    else:
        for sheet in spec.get("sheets") or []:
            headers = "".join(f"<th>{html_lib.escape(str(h))}</th>" for h in sheet.get("headers") or [])
            rows = "".join(
                "<tr>" + "".join(f"<td>{html_lib.escape(str(c))}</td>" for c in list(r)[:8]) + "</tr>"
                for r in (sheet.get("rows") or [])[:15]
            )
            items.append(
                f'<section class="slide"><h3>{html_lib.escape(str(sheet.get("name") or ""))}</h3>'
                f'<table><thead><tr>{headers}</tr></thead><tbody>{rows}</tbody></table></section>'
            )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html_lib.escape(str(row.get('title')))}</title>
<style>body{{margin:0;background:#0a0b10;color:#f3f1ec;font:15px/1.6 "Segoe UI",system-ui,sans-serif;padding:32px}}
h1{{font-size:26px;margin:0 0 4px}}.sub{{color:#9aa0b0;margin:0 0 24px}}
.slide{{background:#14161f;border:1px solid #232838;border-top:3px solid #c8a45c;border-radius:14px;
padding:20px 22px;margin-bottom:16px}}.num{{color:#c8a45c;font-weight:700;font-size:12px;letter-spacing:.18em}}
h3{{margin:6px 0 10px;font-size:18px}}ul{{margin:0;padding-left:20px;color:#cfd3de}}li{{margin:4px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th{{background:#1e222e;color:#e8c87e;text-align:left;padding:8px}}
td{{padding:7px 8px;border-bottom:1px solid #232838}}</style></head><body>
<h1>{html_lib.escape(str(row.get('title') or ''))}</h1>
<p class="sub">{html_lib.escape(str(spec.get('subtitle') or ''))}</p>{''.join(items)}</body></html>"""


@router.delete("/{document_id}")
async def delete_document(document_id: str, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, str]:
    row = await _load(document_id, user["id"])
    if row.get("file_id"):
        try:
            await file_service.delete(user["id"], row["file_id"], permanent=True)
        except FileError:
            pass
    await db.execute("delete from documents where id = $1 and user_id = $2", document_id, user["id"])
    await audit("document.delete", user_id=user["id"], resource="document", resource_id=document_id)
    return {"status": "ok"}
