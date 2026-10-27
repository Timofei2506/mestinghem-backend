"""Legal documents API — served from versioned markdown sources."""
from __future__ import annotations

import html
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

from ..core.config import settings

log = logging.getLogger("legal")
router = APIRouter(prefix="/api/legal", tags=["legal"])

LEGAL_DIR = Path(__file__).resolve().parents[3] / "legal"

DOCUMENTS = {
    "privacy": {"file": "privacy-policy", "title": "Политика конфиденциальности", "version": "1.0"},
    "terms": {"file": "terms-of-service", "title": "Условия использования", "version": "1.0"},
    "security": {"file": "security", "title": "Безопасность и защита данных", "version": "1.0"},
    "cookies": {"file": "cookie-policy", "title": "Политика использования cookie", "version": "1.0"},
    "aup": {"file": "acceptable-use", "title": "Правила допустимого использования", "version": "1.0"},
    "dpa": {"file": "dpa", "title": "Соглашение об обработке данных (DPA)", "version": "1.0"},
    "agent": {"file": "agent-policy", "title": "Политика управления устройством", "version": "1.0"},
    "disclosure": {"file": "disclosure", "title": "Ответственное раскрытие уязвимостей", "version": "1.0"},
}


def _document_path(key: str, language: str) -> Path:
    meta = DOCUMENTS[key]
    suffix = "" if language == "ru" else f".{language}"
    candidate = LEGAL_DIR / f"{meta['file']}{suffix}.md"
    if candidate.is_file():
        return candidate
    return LEGAL_DIR / f"{meta['file']}.md"


def markdown_to_html(markdown: str) -> str:
    """Tiny, dependency-free Markdown renderer for legal prose."""
    lines = markdown.splitlines()
    out: list[str] = []
    in_list: str | None = None
    in_code = False
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            if in_list:
                out.append(f"</{in_list}>")
                in_list = None
            out.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line))
            continue
        if not line.strip():
            if in_list:
                out.append(f"</{in_list}>")
                in_list = None
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            if in_list:
                out.append(f"</{in_list}>")
                in_list = None
            level = len(heading.group(1))
            text = _inline(heading.group(2))
            slug = re.sub(r"[^a-zа-я0-9]+", "-", heading.group(2).lower()).strip("-")[:60]
            out.append(f'<h{level} id="{slug}">{text}</h{level}>')
            continue

        bullet = re.match(r"^\s*[-*+]\s+(.*)$", line)
        numbered = re.match(r"^\s*(\d+)[.)]\s+(.*)$", line)
        if bullet or numbered:
            tag = "ul" if bullet else "ol"
            if in_list != tag:
                if in_list:
                    out.append(f"</{in_list}>")
                out.append(f"<{tag}>")
                in_list = tag
            content = _inline(bullet.group(1) if bullet else numbered.group(2))
            out.append(f"<li>{content}</li>")
            continue

        if in_list:
            out.append(f"</{in_list}>")
            in_list = None
        if line.startswith("> "):
            out.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif line.strip() in ("---", "***", "___"):
            out.append("<hr>")
        else:
            out.append(f"<p>{_inline(line)}</p>")
    if in_list:
        out.append(f"</{in_list}>")
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" rel="noopener">\1</a>', escaped)
    return escaped


PAGE = """<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · {brand}</title>
<style>
:root{{--gold:#c8a45c;--ink:#14161d;--muted:#6b7280;--line:#e6e1d6}}
*{{box-sizing:border-box}}
body{{margin:0;background:#f6f4ef;color:var(--ink);
font:16px/1.75 "Segoe UI",system-ui,-apple-system,"Helvetica Neue",Arial,sans-serif}}
.wrap{{max-width:880px;margin:0 auto;padding:56px 28px 96px}}
.crumb{{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--gold);font-weight:700}}
h1{{font-size:38px;line-height:1.15;margin:12px 0 6px;font-weight:600;letter-spacing:-.01em}}
.meta{{color:var(--muted);font-size:13px;margin-bottom:28px}}
h2{{font-size:23px;margin:40px 0 10px;padding-bottom:8px;border-bottom:1px solid var(--line);font-weight:600}}
h3{{font-size:18px;margin:26px 0 8px;font-weight:600}}
h4{{font-size:15px;margin:20px 0 6px;color:#3a3f4b;text-transform:uppercase;letter-spacing:.08em}}
p{{margin:0 0 14px}} ul,ol{{margin:0 0 16px;padding-left:22px}} li{{margin:6px 0}}
blockquote{{margin:18px 0;padding:14px 20px;border-left:3px solid var(--gold);background:#fbf7ee;border-radius:0 10px 10px 0}}
code{{background:#eeeae0;padding:2px 6px;border-radius:5px;font-family:Consolas,monospace;font-size:14px}}
pre{{background:var(--ink);color:#f3f1ec;padding:18px;border-radius:12px;overflow:auto}}
pre code{{background:none;color:inherit;padding:0}}
hr{{border:none;border-top:1px solid var(--line);margin:32px 0}}
table{{width:100%;border-collapse:collapse;margin:18px 0;font-size:14px}}
th{{background:#1e222e;color:#e8c87e;text-align:left;padding:10px 12px}}
td{{padding:9px 12px;border-bottom:1px solid var(--line)}}
footer{{margin-top:56px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}}
a{{color:#8a6d2f}} .toc{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px 22px;margin:24px 0}}
.toc h2{{border:none;margin:0 0 10px;font-size:15px;text-transform:uppercase;letter-spacing:.1em;color:var(--gold)}}
.toc ol{{margin:0;padding-left:20px}} .toc li{{margin:4px 0}}
@media print{{body{{background:#fff}}.wrap{{padding:0}}}}
</style></head><body><div class="wrap">
<div class="crumb">{brand}</div>
<h1>{title}</h1>
<div class="meta">Версия {version} · действующая редакция · {entity}, {address}</div>
{toc}
{body}
<footer>{brand} · {entity} · {address} · <a href="mailto:{contact}">{contact}</a></footer>
</div></body></html>
"""


def _toc(body_html: str) -> str:
    headings = re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', body_html)
    if len(headings) < 3:
        return ""
    items = "".join(
        f'<li><a href="#{slug}">{re.sub(r"<[^>]+>", "", text)}</a></li>' for slug, text in headings[:40]
    )
    return f'<nav class="toc"><h2>Содержание</h2><ol>{items}</ol></nav>'


@router.get("/")
async def index(language: str = Query(default="ru", pattern="^(ru|en)$")) -> dict[str, Any]:
    return {
        "documents": [
            {
                "id": key,
                "title": meta["title"],
                "version": meta["version"],
                "url": f"/api/legal/{key}",
                "available": _document_path(key, language).is_file(),
            }
            for key, meta in DOCUMENTS.items()
        ],
        "entity": settings.legal_entity,
        "address": settings.legal_address,
        "contact": settings.contact_email,
        "dpo": settings.dpo_email,
    }


@router.get("/{key}")
async def get_document(key: str, format: str = Query(default="html", pattern="^(html|md)$"),
                       language: str = Query(default="ru", pattern="^(ru|en)$")) -> Any:
    if key not in DOCUMENTS:
        raise HTTPException(404, "Документ не найден")
    path = _document_path(key, language)
    if not path.is_file():
        raise HTTPException(404, "Документ ещё не опубликован")
    markdown = path.read_text(encoding="utf-8")
    if format == "md":
        return PlainTextResponse(markdown, media_type="text/markdown; charset=utf-8")
    body = markdown_to_html(markdown)
    return HTMLResponse(
        PAGE.format(
            lang=language,
            brand=html.escape(settings.brand),
            title=html.escape(DOCUMENTS[key]["title"]),
            version=DOCUMENTS[key]["version"],
            entity=html.escape(settings.legal_entity),
            address=html.escape(settings.legal_address),
            contact=html.escape(settings.contact_email),
            toc=_toc(body),
            body=body,
        )
    )
