"""
Document generation service.

Flow: user brief → structured *spec* (JSON) → deterministic renderer → file
in object storage → a row in ``documents``/``files``.

The spec is the contract. It is produced by the language layer, then validated
and repaired here, and there is a deterministic fallback spec for every kind so
a document is always produced — even with inference unavailable.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..core.config import settings
from ..core.db import new_id
from . import documents_render as render
from .ai_router import ai
from .storage_service import storage

log = logging.getLogger("docs")

KINDS = {
    "pptx": {"label": "Презентация", "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "ext": "pptx"},
    "xlsx": {"label": "Таблица Excel", "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "ext": "xlsx"},
    "docx": {"label": "Документ Word", "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "ext": "docx"},
    "md": {"label": "Markdown", "mime": "text/markdown", "ext": "md"},
    "html": {"label": "HTML-страница", "mime": "text/html", "ext": "html"},
}

MAX_SLIDES = 18
MAX_ROWS = 400


class DocumentError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip_list(value: Any, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    return value[:limit]


def _text_list(value: Any, limit: int, item_limit: int = 300) -> list[str]:
    out = []
    for item in _clip_list(value, limit):
        text = str(item or "").strip()
        if text:
            out.append(text[:item_limit])
    return out


# ---------------------------------------------------------------------------
# Spec validation / repair
# ---------------------------------------------------------------------------
def validate_spec(kind: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise DocumentError("spec must be an object")
    clean: dict[str, Any] = {
        "title": str(spec.get("title") or "Документ")[:140],
        "subtitle": str(spec.get("subtitle") or "")[:220],
        "author": str(spec.get("author") or settings.brand)[:80],
        "language": str(spec.get("language") or "ru")[:8],
        "summary": str(spec.get("summary") or "")[:900],
    }

    if kind == "pptx":
        slides: list[dict[str, Any]] = []
        for raw in _clip_list(spec.get("slides"), MAX_SLIDES):
            if not isinstance(raw, dict):
                continue
            layout = str(raw.get("layout") or "content").lower()
            if layout not in {"content", "section", "two_column", "stats", "kpi", "quote", "table", "chart"}:
                layout = "content"
            slide: dict[str, Any] = {
                "layout": layout,
                "title": str(raw.get("title") or raw.get("heading") or "")[:120],
                "kicker": str(raw.get("kicker") or "")[:60],
                "lead": str(raw.get("lead") or "")[:240],
                "points": _text_list(raw.get("points"), 6),
                "note": str(raw.get("note") or "")[:170],
            }
            stats = []
            for stat in _clip_list(raw.get("stats"), 4):
                if isinstance(stat, dict) and (stat.get("value") or stat.get("label")):
                    stats.append(
                        {
                            "value": str(stat.get("value") or "")[:14],
                            "label": str(stat.get("label") or "")[:70],
                        }
                    )
                elif isinstance(stat, (str, int, float)):
                    stats.append({"value": str(stat)[:14], "label": ""})
            slide["stats"] = stats
            if layout == "quote":
                slide["quote"] = str(raw.get("quote") or "")[:300]
                slide["author"] = str(raw.get("author") or "")[:80]
            if layout == "two_column":
                columns = []
                for column in _clip_list(raw.get("columns"), 2):
                    if isinstance(column, dict):
                        columns.append(
                            {
                                "title": str(column.get("title") or "")[:70],
                                "points": _text_list(column.get("points"), 6, 220),
                            }
                        )
                slide["columns"] = columns or [{"title": "", "points": slide["points"]}]
            if layout == "table":
                slide["headers"] = _text_list(raw.get("headers"), 5, 40)
                slide["rows"] = [
                    [str(cell)[:60] for cell in _clip_list(row, 5)]
                    for row in _clip_list(raw.get("rows"), 7)
                    if isinstance(row, (list, tuple))
                ]
            chart = raw.get("chart") or spec.get("chart")
            if isinstance(chart, dict) and chart.get("categories"):
                series = []
                for item in _clip_list(chart.get("series"), 4):
                    if isinstance(item, dict):
                        series.append(
                            {
                                "name": str(item.get("name") or "Ряд")[:40],
                                "values": [_number(v) for v in _clip_list(item.get("values"), 12)],
                            }
                        )
                slide["chart"] = {
                    "type": str(chart.get("type") or "bar").lower()[:8],
                    "categories": _text_list(chart.get("categories"), 12, 40),
                    "series": series,
                }
                slide["chart_title"] = str(chart.get("title") or slide["title"])[:80]
            if slide["title"] or slide["points"] or slide["stats"] or layout in {"section", "quote", "table"}:
                slides.append(slide)
        if not slides:
            raise DocumentError("presentation needs at least one slide")
        clean["slides"] = slides
        clean["closing_title"] = str(spec.get("closing_title") or "")[:90]
        clean["closing_text"] = str(spec.get("closing_text") or "")[:200]
        clean["contacts"] = _text_list(spec.get("contacts"), 4, 60)

    elif kind == "xlsx":
        sheets: list[dict[str, Any]] = []
        for raw in _clip_list(spec.get("sheets"), 8):
            if not isinstance(raw, dict):
                continue
            headers = _text_list(raw.get("headers"), 14, 48)
            rows: list[list[Any]] = []
            for row in _clip_list(raw.get("rows"), MAX_ROWS):
                if isinstance(row, dict):
                    row = [row.get(h) for h in headers] if headers else list(row.values())
                if isinstance(row, (list, tuple)):
                    rows.append([_number(cell) if _looks_numeric(cell) else str(cell)[:120] for cell in _clip_list(row, 14)])
            if not headers:
                continue
            charts = []
            for chart in _clip_list(raw.get("charts"), 2):
                if isinstance(chart, dict):
                    charts.append(
                        {
                            "type": str(chart.get("type") or "bar").lower()[:8],
                            "title": str(chart.get("title") or "Динамика")[:80],
                            "label_column": int(chart.get("label_column") or 1),
                            "columns": [int(c) for c in _clip_list(chart.get("columns"), 3) if str(c).isdigit()],
                        }
                    )
            sheets.append(
                {
                    "name": str(raw.get("name") or f"Лист {len(sheets) + 1}")[:28],
                    "title": str(raw.get("title") or raw.get("name") or "Данные")[:120],
                    "subtitle": str(raw.get("subtitle") or "")[:180],
                    "headers": headers,
                    "rows": rows,
                    "number_format": str(raw.get("number_format") or "#,##0.00")[:20],
                    "charts": charts,
                    "widths": [int(w) for w in _clip_list(raw.get("widths"), 14) if str(w).isdigit()],
                }
            )
        if not sheets:
            raise DocumentError("workbook needs at least one sheet with headers")
        clean["sheets"] = sheets
        clean["kpis"] = [
            {"label": str(k.get("label") or "")[:80], "value": _number(k.get("value")), "format": str(k.get("format") or "#,##0.00")[:20]}
            for k in _clip_list(spec.get("kpis"), 6)
            if isinstance(k, dict)
        ]
        clean["notes"] = _text_list(spec.get("notes"), 10)

    else:  # docx / md / html
        sections: list[dict[str, Any]] = []
        for raw in _clip_list(spec.get("sections"), 30):
            if not isinstance(raw, dict):
                continue
            blocks: list[dict[str, Any]] = []
            for block in _clip_list(raw.get("blocks"), 24):
                if isinstance(block, str):
                    blocks.append({"type": "paragraph", "text": block[:3000]})
                    continue
                if not isinstance(block, dict):
                    continue
                kind_block = str(block.get("type") or "paragraph").lower()
                if kind_block in ("bullet", "bullets", "numbered", "steps"):
                    items = _text_list(block.get("items"), 20, 600)
                    if items:
                        blocks.append({"type": kind_block, "items": items})
                elif kind_block == "table":
                    headers = _text_list(block.get("headers"), 8, 60)
                    rows = [
                        [str(c)[:200] for c in _clip_list(r, 8)]
                        for r in _clip_list(block.get("rows"), 60)
                        if isinstance(r, (list, tuple))
                    ]
                    if headers:
                        blocks.append({"type": "table", "headers": headers, "rows": rows})
                elif kind_block == "quote":
                    if block.get("text"):
                        blocks.append({"type": "quote", "text": str(block["text"])[:800]})
                elif kind_block == "callout":
                    if block.get("text"):
                        blocks.append({"type": "callout", "text": str(block["text"])[:600], "bold": bool(block.get("bold", True))})
                elif block.get("text"):
                    blocks.append({"type": "paragraph", "text": str(block["text"])[:3000]})
            if blocks or raw.get("heading"):
                sections.append(
                    {
                        "heading": str(raw.get("heading") or "")[:160],
                        "level": min(int(raw.get("level") or 1), 3),
                        "blocks": blocks,
                        "page_break": bool(raw.get("page_break")),
                    }
                )
        if not sections:
            raise DocumentError("document needs at least one section")
        clean["sections"] = sections
        clean["toc"] = bool(spec.get("toc", True))
        clean["doc_type"] = str(spec.get("doc_type") or KINDS.get(kind, {}).get("label") or "")[:40]

    return clean


def _looks_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    text = str(value or "").strip().replace(" ", "").replace("\u00a0", "")
    if not text:
        return False
    try:
        float(text.replace(",", "."))
        return True
    except ValueError:
        return False


def _number(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value or "").strip().replace(" ", "").replace("\u00a0", "")
    try:
        number = float(text.replace(",", "."))
        return int(number) if number.is_integer() else round(number, 4)
    except ValueError:
        return text[:120]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
def _deck_prompt(brief: str, slides: int, language: str, extra: str) -> str:
    lang = "русском" if language.startswith("ru") else "English"
    return f"""Собери структуру презентации на {language} языке. Тема и вводные:
{brief}
{extra}

Требования:
- {slides} содержательных слайдов (не считая титульного и финального — их добавит рендерер).
- Каждый слайд: одна мысль, заголовок до 90 символов, 3-5 пунктов до 160 символов каждый.
- Чередуй layout: "content", "two_column", "stats", "table", "chart", "section", "quote".
- Для "stats" дай 2-4 карточки {{"value": "короткая цифра", "label": "пояснение"}}.
- Для "chart" дай {{"type": "bar|line|pie", "categories": [...], "series": [{{"name": "...", "values": [числа]}}]}} — числа реалистичные, согласованные.
- Для "table" дай "headers" (до 5) и "rows" (до 7).
- Никакой воды, только конкретика: цифры, сроки, ответственные, риски.

Верни строго JSON:
{{"title": "...", "subtitle": "...", "summary": "...", "author": "...",
  "slides": [{{"layout": "content", "title": "...", "kicker": "...", "lead": "...", "points": ["..."], "stats": [], "note": "..."}}],
  "closing_title": "...", "closing_text": "...", "contacts": ["..."]}}"""


def _sheet_prompt(brief: str, sheets: int, language: str, extra: str) -> str:
    lang = "русском" if language.startswith("ru") else "English"
    return f"""Собери структуру рабочей книги Excel на {language} языке. Задача:
{brief}
{extra}

Требования:
- {sheets} лист(а/ов) данных с реальными, согласованными числами (не «данные1», «данные2»).
- На каждом листе: "name" (до 28 символов), "headers" (4-8 колонок), "rows" (12-40 строк).
- Числа пиши числами, без единиц измерения в значении (единицы — в названии колонки).
- Добавь "charts": [{{"type": "bar", "title": "...", "label_column": 1, "columns": [2]}}] там, где это уместно.
- В "kpis" вынеси 3-5 главных показателей книги: {{"label": "...", "value": число}}.
- В "notes" — 3-6 выводов по данным.

Верни строго JSON:
{{"title": "...", "subtitle": "...", "kpis": [{{"label": "...", "value": 0}}],
  "sheets": [{{"name": "...", "title": "...", "headers": ["..."], "rows": [["...", 0]], "charts": []}}],
  "notes": ["..."]}}"""


def _doc_prompt(brief: str, sections: int, language: str, extra: str) -> str:
    return f"""Собери структуру документа на {"русском" if language.startswith("ru") else "English"} языке. Задача:
{brief}
{extra}

Требования:
- {sections} разделов, в каждом 2-4 блока.
- Типы блоков: "paragraph" (связный текст 2-5 предложений), "bullets", "numbered", "table"
  ({{"headers": [...], "rows": [[...]]}}), "quote", "callout" (важный вывод).
- Стиль деловой, конкретный: факты, цифры, сроки, риски, рекомендации. Без общих фраз.
- Первый блок документа — "summary" (3-5 предложений, суть).

Верни строго JSON:
{{"title": "...", "subtitle": "...", "summary": "...", "author": "...", "doc_type": "...",
  "sections": [{{"heading": "...", "level": 1, "blocks": [{{"type": "paragraph", "text": "..."}}]}}]}}"""


# ---------------------------------------------------------------------------
# Deterministic fallbacks (used when inference is unavailable or invalid)
# ---------------------------------------------------------------------------
def fallback_spec(kind: str, brief: str, *, language: str = "ru", slides: int = 8) -> dict[str, Any]:
    title = (brief.strip().splitlines() or ["Документ"])[0][:120] or "Документ"
    ru = language.lower().startswith("ru")
    if kind == "pptx":
        blocks = [
            ("Контекст и постановка задачи", ["Исходная ситуация и триггеры изменений", "Ограничения: сроки, бюджет, ресурсы", "Критерии успеха"]),
            ("Цели", ["Измеримая цель №1", "Измеримая цель №2", "Что не входит в периметр"]),
            ("Решение", ["Ключевая идея", "Архитектура решения", "Почему это работает именно сейчас"]),
            ("План внедрения", ["Этап 1 — подготовка", "Этап 2 — пилот", "Этап 3 — масштабирование"]),
            ("Экономика", ["Затраты", "Ожидаемый эффект", "Срок окупаемости"]),
            ("Риски и меры", ["Риск 1 и митигация", "Риск 2 и митигация", "Риск 3 и митигация"]),
            ("Следующие шаги", ["Решение, которое нужно принять", "Ответственные и даты", "Контрольные точки"]),
        ]
        slides_out = []
        for index, (heading, points) in enumerate(blocks[: max(3, slides)], start=1):
            layout = "stats" if index == 5 else ("two_column" if index == 3 else "content")
            slide: dict[str, Any] = {"layout": layout, "title": heading, "kicker": f"Блок {index}", "points": points}
            if layout == "stats":
                slide["stats"] = [
                    {"value": "—", "label": "Затраты"},
                    {"value": "—", "label": "Эффект"},
                    {"value": "—", "label": "Окупаемость"},
                ]
            if layout == "two_column":
                slide["columns"] = [
                    {"title": "Что делаем", "points": points[:3]},
                    {"title": "Что получаем", "points": ["Прозрачный процесс", "Измеримый результат"]},
                ]
            slides_out.append(slide)
        return {
            "title": title,
            "subtitle": "Каркас презентации — заполните данные по блокам" if ru else "Presentation skeleton — fill in the data",
            "slides": slides_out,
            "closing_title": "Переходим к решениям" if ru else "Moving to decisions",
            "closing_text": "Обсудим следующие шаги и закреплённые сроки." if ru else "Next steps and committed dates.",
        }
    if kind == "xlsx":
        months = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
        rows = [[m, 0, 0, 0] for m in months]
        return {
            "title": title,
            "subtitle": "Шаблон рабочей книги — подставьте свои данные" if ru else "Workbook template — insert your data",
            "kpis": [{"label": "Итого" if ru else "Total", "value": 0}],
            "sheets": [
                {
                    "name": "Данные" if ru else "Data",
                    "title": title,
                    "headers": ["Период" if ru else "Period", "План" if ru else "Plan", "Факт" if ru else "Actual", "Отклонение" if ru else "Variance"],
                    "rows": rows,
                    "charts": [{"type": "bar", "title": "План/факт" if ru else "Plan vs actual", "label_column": 1, "columns": [2, 3]}],
                }
            ],
            "notes": ["Заполните значения — итоги, графики и условное форматирование пересчитаются автоматически."],
        }
    return {
        "title": title,
        "subtitle": "Структура документа" if ru else "Document outline",
        "summary": "Каркас документа по заявленной теме. Заполните разделы фактурой: цифры, сроки, ответственные."
        if ru
        else "Document skeleton for the stated topic. Fill each section with facts, numbers and owners.",
        "sections": [
            {"heading": "1. Контекст" if ru else "1. Context", "level": 1,
             "blocks": [{"type": "paragraph", "text": title}, {"type": "bullets", "items": ["Исходная ситуация", "Ограничения", "Критерии успеха"]}]},
            {"heading": "2. Цели и задачи" if ru else "2. Objectives", "level": 1,
             "blocks": [{"type": "numbered", "items": ["Цель 1", "Цель 2", "Цель 3"]}]},
            {"heading": "3. Решение" if ru else "3. Approach", "level": 1,
             "blocks": [{"type": "paragraph", "text": "Описание подхода."}, {"type": "callout", "text": "Ключевой вывод."}]},
            {"heading": "4. План" if ru else "4. Plan", "level": 1,
             "blocks": [{"type": "table", "headers": ["Этап", "Срок", "Ответственный"], "rows": [["Подготовка", "—", "—"], ["Пилот", "—", "—"], ["Масштабирование", "—", "—"]]}]},
            {"heading": "5. Риски" if ru else "5. Risks", "level": 1,
             "blocks": [{"type": "bullets", "items": ["Риск и мера снижения", "Риск и мера снижения"]}]},
        ],
    }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
class DocumentService:
    async def build_spec(
        self,
        kind: str,
        brief: str,
        *,
        language: str = "ru",
        size: str = "medium",
        extra: str = "",
        audience: str = "",
        tone: str = "",
        use_ai: bool = True,
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise DocumentError(f"unsupported kind: {kind}")
        brief = (brief or "").strip()
        if not brief:
            raise DocumentError("empty brief")
        if len(brief) > 6000:
            brief = brief[:6000]

        scale = {"short": (5, 2, 4), "medium": (9, 3, 7), "long": (15, 5, 12)}.get(size, (9, 3, 7))
        hints = []
        if audience:
            hints.append(f"Аудитория: {audience[:120]}.")
        if tone:
            hints.append(f"Тон: {tone[:120]}.")
        hints.append(extra[:600])
        extra_block = "\n".join(h for h in hints if h)

        doc_kind = "pptx" if kind == "pptx" else ("xlsx" if kind == "xlsx" else "docx")
        prompt_fn = {"pptx": _deck_prompt, "xlsx": _sheet_prompt, "docx": _doc_prompt}[doc_kind]
        primary = scale[0] if doc_kind == "pptx" else (scale[1] if doc_kind == "xlsx" else scale[2])

        if not use_ai:
            return validate_spec(kind, fallback_spec(kind, brief, language=language, slides=primary))

        prompt = prompt_fn(brief, primary, language, extra_block)
        try:
            data = await ai.json_task(
                "document",
                prompt,
                language=language,
                schema_hint="Только JSON, без markdown-обёрток и комментариев.",
            )
            spec = validate_spec(kind, data)
            spec["language"] = language
            return spec
        except Exception as exc:
            log.warning("spec generation failed (%s) — using deterministic template", exc)
            spec = validate_spec(kind, fallback_spec(kind, brief, language=language, slides=primary))
            spec["language"] = language
            spec["_fallback"] = True
            return spec

    async def render(self, kind: str, spec: dict[str, Any]) -> tuple[bytes, str, str]:
        brand = settings.brand
        if kind == "pptx":
            return render.render_pptx(spec, brand=brand), KINDS[kind]["mime"], "pptx"
        if kind == "xlsx":
            return render.render_xlsx(spec, brand=brand), KINDS[kind]["mime"], "xlsx"
        if kind == "docx":
            return render.render_docx(spec, brand=brand), KINDS[kind]["mime"], "docx"
        if kind == "md":
            return render.render_markdown(spec).encode("utf-8"), "text/markdown", "md"
        if kind == "html":
            return (
                render.render_html(spec, brand=brand, lang=spec.get("language", "ru")).encode("utf-8"),
                "text/html",
                "html",
            )
        raise DocumentError(f"unsupported kind: {kind}")

    async def create(
        self,
        store: Any,
        *,
        user_id: str,
        kind: str,
        brief: str,
        title: str | None = None,
        folder_id: str | None = None,
        language: str = "ru",
        size: str = "medium",
        audience: str = "",
        tone: str = "",
        extra: str = "",
        use_ai: bool = True,
        spec_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        spec = (
            validate_spec(kind, spec_override)
            if spec_override
            else await self.build_spec(kind, brief, language=language, size=size, extra=extra,
                                       audience=audience, tone=tone, use_ai=use_ai)
        )
        if title:
            spec["title"] = title[:140]
        payload, mime, ext = await self.render(kind, spec)
        safe_title = "".join(ch for ch in spec["title"] if ch.isalnum() or ch in " -_.").strip()[:80] or "document"
        filename = f"{safe_title}.{ext}"
        stored = await storage.put(payload, filename, namespace=f"documents/{user_id[:12]}", mime=mime)

        file_id = new_id("fil_")
        doc_id = new_id("doc_")
        now = _now()
        stats = _spec_stats(kind, spec)
        await store.insert(
            "files",
            {
                "id": file_id,
                "user_id": user_id,
                "folder_id": folder_id,
                "name": filename,
                "path": f"/Документы/{filename}",
                "kind": "document",
                "mime": mime,
                "size": stored.size,
                "backend": stored.backend,
                "storage_key": stored.key,
                "checksum": stored.checksum,
                "preview": {"type": ext, "stats": stats},
                "meta": {"document_id": doc_id, "spec_kind": kind},
                "origin": "generated",
                "pinned": False,
                "trashed": False,
                "version": 1,
                "created_at": now,
                "updated_at": now,
            },
        )
        await store.insert(
            "documents",
            {
                "id": doc_id,
                "user_id": user_id,
                "file_id": file_id,
                "kind": kind,
                "title": spec["title"],
                "template": "aurum-dark",
                "language": language,
                "spec": spec,
                "stats": stats,
                "status": "ready",
                "created_at": now,
            },
        )
        return {
            "document_id": doc_id,
            "file_id": file_id,
            "name": filename,
            "kind": kind,
            "size": stored.size,
            "mime": mime,
            "stats": stats,
            "title": spec["title"],
            "fallback": bool(spec.get("_fallback")),
            "created_at": now,
        }

    async def regenerate(self, store: Any, *, document_id: str, user_id: str, brief: str | None = None) -> dict[str, Any]:
        row = await store.fetch(
            "select * from documents where id = $1 and user_id = $2", document_id, user_id
        ).first()
        if not row:
            raise DocumentError("document not found")
        spec = row.get("spec") or {}
        if brief:
            spec = await self.build_spec(row["kind"], brief, language=row.get("language") or "ru")
        else:
            spec = validate_spec(row["kind"], spec)
        payload, mime, ext = await self.render(row["kind"], spec)
        stored = await storage.put(payload, f"{document_id}.{ext}", namespace=f"documents/{user_id[:12]}", mime=mime)
        stats = _spec_stats(row["kind"], spec)
        await store.execute(
            "update documents set spec = $1, stats = $2 where id = $3", spec, stats, document_id
        )
        if row.get("file_id"):
            await store.execute(
                "update files set size = $1, checksum = $2, storage_key = $3, backend = $4, "
                "version = version + 1, updated_at = $5 where id = $6",
                stored.size, stored.checksum, stored.key, stored.backend, _now(), row["file_id"],
            )
        return {"document_id": document_id, "file_id": row.get("file_id"), "size": stored.size, "stats": stats}


def _spec_stats(kind: str, spec: dict[str, Any]) -> dict[str, Any]:
    if kind == "pptx":
        return {"slides": len(spec.get("slides") or []), "words": _word_count(json.dumps(spec, ensure_ascii=False))}
    if kind == "xlsx":
        sheets = spec.get("sheets") or []
        return {
            "sheets": len(sheets),
            "rows": sum(len(s.get("rows") or []) for s in sheets),
            "columns": max((len(s.get("headers") or []) for s in sheets), default=0),
            "charts": sum(len(s.get("charts") or []) for s in sheets),
        }
    return {"sections": len(spec.get("sections") or []), "words": _word_count(json.dumps(spec, ensure_ascii=False))}


def _word_count(text: str) -> int:
    return len([w for w in text.replace("\\n", " ").split() if any(ch.isalpha() for ch in w)])


document_service = DocumentService()
