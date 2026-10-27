"""
Conversation API.

Streaming is NDJSON over a single POST (works through any proxy, no
EventSource restrictions): each line is a small JSON event —
``meta``, ``delta``, ``action``, ``file``, ``done``, ``error``.

The assistant can act on the workspace: when the model emits a fenced
``mst-action`` block, the router executes it (create a document, save a file,
queue an image) and reports the result back into the same stream.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..core.config import settings
from ..core.db import db, new_id
from ..security import audit, sanitize_output, screen_input
from ..services.ai_router import PUBLIC_ALIAS, InferenceError, ai
from ..services.file_service import file_service
from .deps import Throttler, client_ip, get_current_user, public_user, utcnow

log = logging.getLogger("chat")
router = APIRouter(prefix="/api/chat", tags=["chat"])

ACTION_RE = re.compile(r"```mst-action\s*(\{.*?\})\s*```", re.S)
FENCE_LANGS = ("json", "python", "bash", "sql", "text")


class ConversationBody(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    mode: str = Field(default="assistant", max_length=24)
    persona: str | None = Field(default=None, max_length=40)
    language: str = Field(default="ru", pattern="^(ru|en)$")


class MessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=24_000)
    conversation_id: str | None = None
    language: str = Field(default="ru", pattern="^(ru|en)$")
    mode: str = Field(default="assistant", max_length=24)
    stream: bool = True
    attachments: list[str] = Field(default_factory=list, max_length=6)
    context: dict[str, Any] | None = None


MODE_TASK = {
    "assistant": "chat",
    "analyst": "analyst",
    "writer": "writer",
    "reason": "reason",
    "code": "chat",
    "fast": "fast",
    "vision": "vision",
}

MODE_LABEL = {
    "assistant": {"ru": "Ассистент", "en": "Assistant"},
    "analyst": {"ru": "Аналитик", "en": "Analyst"},
    "writer": {"ru": "Редактор", "en": "Editor"},
    "reason": {"ru": "Глубокий анализ", "en": "Deep reasoning"},
    "code": {"ru": "Инженер", "en": "Engineer"},
    "fast": {"ru": "Быстрый ответ", "en": "Fast"},
}


def _ndjson(event: str, **payload: Any) -> str:
    return json.dumps({"event": event, **payload}, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationBody, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    conversation_id = new_id("cnv_")
    now = utcnow()
    await db.insert(
        "conversations",
        {
            "id": conversation_id,
            "user_id": user["id"],
            "title": (body.title or "Новый диалог")[:120],
            "mode": body.mode[:24],
            "persona": body.persona,
            "language": body.language,
            "context": {},
            "pinned": False,
            "archived": False,
            "message_count": 0,
            "token_usage": {"prompt": 0, "completion": 0, "total": 0},
            "created_at": now,
            "updated_at": now,
        },
    )
    return {"id": conversation_id, "title": body.title or "Новый диалог", "mode": body.mode,
            "language": body.language, "created_at": now}


@router.get("/conversations")
async def list_conversations(
    include_archived: bool = False, limit: int = 60, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    limit = max(1, min(200, limit))
    rows = await db.fetch(
        "select id, title, mode, persona, language, pinned, archived, message_count, "
        "token_usage, created_at, updated_at from conversations where user_id = $1 and archived = $2 "
        "order by pinned desc, updated_at desc limit $3",
        user["id"], bool(include_archived), limit,
    )
    return {
        "conversations": [
            {**r, "pinned": bool(r["pinned"]), "archived": bool(r["archived"])} for r in rows.rows
        ],
        "modes": [],
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str, limit: int = 200, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    row = await db.fetch(
        "select * from conversations where id = $1 and user_id = $2", conversation_id, user["id"]
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Диалог не найден")
    messages = await db.fetch(
        "select id, role, content, attachments, meta, tokens, latency_ms, created_at from messages "
        "where conversation_id = $1 order by created_at asc limit $2",
        conversation_id, max(1, min(500, limit)),
    )
    return {
        "conversation": {
            "id": row["id"], "title": row["title"], "mode": row["mode"], "persona": row["persona"],
            "language": row["language"], "pinned": bool(row["pinned"]), "archived": bool(row["archived"]),
            "message_count": row["message_count"], "token_usage": row["token_usage"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        },
        "messages": messages.rows,
    }


@router.patch("/conversations/{conversation_id}")
async def patch_conversation(
    conversation_id: str, payload: dict[str, Any], user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    row = await db.fetch(
        "select id from conversations where id = $1 and user_id = $2", conversation_id, user["id"]
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Диалог не найден")
    updates: dict[str, Any] = {}
    for key in ("title", "mode", "persona", "language"):
        if key in payload and isinstance(payload[key], str):
            updates[key] = payload[key][:120]
    for key in ("pinned", "archived"):
        if key in payload:
            updates[key] = bool(payload[key])
    if not updates:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нет изменений")
    updates["updated_at"] = utcnow()
    assignments = ", ".join(f"{k} = ${i + 3}" for i, k in enumerate(updates))
    await db.execute(
        f"update conversations set {assignments} where id = $1", conversation_id, utcnow(), *updates.values()
    )
    return {"id": conversation_id, **updates}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, str]:
    affected = await db.execute(
        "delete from conversations where id = $1 and user_id = $2", conversation_id, user["id"]
    )
    await db.execute(
        "delete from messages where conversation_id = $1 and user_id = $2", conversation_id, user["id"]
    )
    await audit("chat.conversation_delete", user_id=user["id"], resource="conversation",
                resource_id=conversation_id)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------
async def _history(conversation_id: str | None, limit: int = 24) -> list[dict[str, Any]]:
    if not conversation_id:
        return []
    rows = await db.fetch(
        "select role, content from messages where conversation_id = $1 order by created_at desc limit $2",
        conversation_id, limit,
    )
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows.rows)]


async def _attachment_context(user_id: str, file_ids: list[str]) -> tuple[str, list[dict[str, Any]]]:
    if not file_ids:
        return "", []
    parts: list[str] = []
    resolved: list[dict[str, Any]] = []
    for file_id in file_ids[:6]:
        try:
            row = await file_service.get(user_id, file_id)
        except Exception:
            continue
        resolved.append({"id": row["id"], "name": row["name"], "kind": row["kind"], "size": row["size"]})
        if row["kind"] in ("document", "code") and int(row["size"] or 0) < 400_000:
            try:
                data = await file_service.read_bytes(user_id, row["id"])
                text = data[0].decode("utf-8", "replace")[:24_000]
                parts.append(f"--- Файл: {row['name']} ---\n{text}")
            except Exception:
                parts.append(f"--- Файл: {row['name']} (бинарный, {row['size']} байт) ---")
        else:
            parts.append(f"--- Файл: {row['name']} ({row['kind']}, {row['size']} байт) ---")
    return ("\n\n".join(parts), resolved) if parts else ("", resolved)


def _mode_instruction(mode: str, language: str, persona: str | None) -> str:
    ru = language.lower().startswith("ru")
    extra = {
        "code": (
            "Ты ведущий инженер. Давай рабочий код с пояснениями, указывай допущения, "
            "предупреждай о рисках. Код — в блоках с указанием языка."
            if ru else
            "You are a principal engineer. Provide working code with explanations, state assumptions, flag risks."
        ),
        "analyst": (
            "Структура ответа: вывод одной строкой → аргументы и цифры → риски → рекомендуемые действия."
            if ru else "Structure: one-line conclusion → evidence and numbers → risks → recommended actions."
        ),
        "writer": (
            "Пиши сильным деловым языком. Предлагай варианты формулировок, если уместно."
            if ru else "Write in strong business language. Offer wording variants where useful."
        ),
        "reason": (
            "Разбери задачу по шагам, проверь допущения, в конце дай итоговый ответ отдельной строкой."
            if ru else "Reason step by step, verify assumptions, end with a separate final answer line."
        ),
    }.get(mode, "")
    if persona and ru:
        extra += f"\nРоль: {persona[:60]}."
    return extra


def _action_footer(language: str) -> str:
    """Instructions for workspace actions — parsed and stripped server-side."""
    if language.lower().startswith("ru"):
        return (
            "\n\nЕсли пользователю нужен готовый файл, добавь в конец ответа один блок вида:\n"
            "```mst-action\n{\"action\":\"create_document\",\"kind\":\"pptx|xlsx|docx|md|html\","
            "\"title\":\"...\",\"brief\":\"подробное ТЗ на 2-5 предложений\"}\n```\n"
            "или\n"
            "```mst-action\n{\"action\":\"generate_image\",\"prompt\":\"...\",\"style\":\"luxury|photo|cinema|neon|3d|logo|none\","
            "\"width\":1024,\"height\":1024}\n```\n"
            "или\n"
            "```mst-action\n{\"action\":\"save_file\",\"name\":\"отчёт.md\",\"content\":\"...\"}\n```\n"
            "Блок выполняется платформой автоматически — не описывай его словами и не добавляй больше одного блока."
        )
    return (
        "\n\nIf the user needs a finished file, append exactly one block:\n"
        "```mst-action\n{\"action\":\"create_document\",\"kind\":\"pptx|xlsx|docx|md|html\","
        "\"title\":\"...\",\"brief\":\"detailed specification\"}\n```\n"
        "or {\"action\":\"generate_image\",\"prompt\":\"...\",\"style\":\"luxury\",\"width\":1024,\"height\":1024}\n"
        "or {\"action\":\"save_file\",\"name\":\"report.md\",\"content\":\"...\"}\n"
        "The platform executes it automatically — do not describe it, and never emit more than one block."
    )


def _agent_footer(language: str) -> str:
    """Agentic capabilities — the model acts via fenced mst-action blocks (parsed & stripped server-side)."""
    if language.lower().startswith("ru"):
        return (
            "\n\n## Действия в рабочей области\n"
            "Ты умеешь сам выполнять действия. Когда это нужно, добавь в ответ один или несколько блоков "
            "(не больше 4) строго в формате:\n"
            "```mst-action\n{ ...валидный JSON... }\n```\n"
            "Доступные действия:\n"
            "• Картинка: {\"action\":\"generate_image\",\"prompt\":\"подробное описание ВСЕГДА на английском\",\"style\":\"photo|cinema|neon|3d|logo|luxury|none\",\"width\":1024,\"height\":1024}\n"
            "• Терминал: {\"action\":\"run_terminal\",\"command\":\"одна команда bash\"}\n"
            "• Файл: {\"action\":\"save_file\",\"name\":\"имя_с_расширением\",\"content\":\"полное содержимое\",\"folder\":\"Проекты\"}\n"
            "• Документ: {\"action\":\"create_document\",\"kind\":\"docx|xlsx|pptx|md|html\",\"title\":\"название\",\"brief\":\"подробное ТЗ на 2-5 предложений\",\"folder\":\"Проекты\"}\n"
            "Правила:\n"
            "1. Пиши живым языком и естественно объявляй, что делаешь, но содержимое блоков словами не "
            "пересказывай и сами блоки не показывай — платформа выполнит их сама.\n"
            "2. Каждый блок — отдельный валидный JSON: строки в двойных кавычках, без trailing comma, без комментариев.\n"
            "3. Никогда не выдумывай результат: платформа вернёт реальный вывод терминала, файл или картинку.\n"
            "4. Терминал — одна команда, без sudo, без «..» и без выхода за пределы рабочей области; опасную "
            "команду платформа отклонит, а ты спокойно объяснишь почему. Не запускай бесконечные или фоновые процессы.\n"
            "5. Для картинок prompt всегда на английском и максимально описательный (объект, стиль, свет, детали).\n"
            "6. Если действий не нужно — просто ответь по-человечески, без блоков.\n"
        )
    return (
        "\n\n## Workspace actions\n"
        "You can act on your own. When needed, add one or more blocks (max 4) to your reply, strictly as:\n"
        "```mst-action\n{ ...valid JSON... }\n```\n"
        "Available actions:\n"
        "• Image: {\"action\":\"generate_image\",\"prompt\":\"detailed description ALWAYS in English\",\"style\":\"photo|cinema|neon|3d|logo|luxury|none\",\"width\":1024,\"height\":1024}\n"
        "• Terminal: {\"action\":\"run_terminal\",\"command\":\"a single bash command\"}\n"
        "• File: {\"action\":\"save_file\",\"name\":\"name_with_extension\",\"content\":\"full contents\",\"folder\":\"Projects\"}\n"
        "• Document: {\"action\":\"create_document\",\"kind\":\"docx|xlsx|pptx|md|html\",\"title\":\"title\",\"brief\":\"detailed 2-5 sentence spec\",\"folder\":\"Projects\"}\n"
        "Rules:\n"
        "1. Write naturally and announce what you're doing like a human, but never narrate the block contents "
        "or show the blocks — the platform runs them for you.\n"
        "2. Each block is separate, valid JSON: double-quoted strings, no trailing commas, no comments.\n"
        "3. Never fabricate results: the platform returns the real terminal output, file or image.\n"
        "4. Terminal — one command, no sudo, no \"..\", no leaving the workspace; dangerous commands are blocked "
        "and you calmly explain why. Don't start endless or background processes.\n"
        "5. Image prompts are always in English and as descriptive as possible (subject, style, light, details).\n"
        "6. If no action is needed — just reply like a human, with no blocks.\n"
    )


async def _resolve_folder(user_id: str, want: str | None, default_name: str) -> str | None:
    """Find-or-create a folder by name; return its id (None = workspace root)."""
    name = (want or default_name or "").strip().strip("/")
    if not name:
        return None
    name = (name.split("/")[-1][:60] or default_name)
    row = await db.fetch(
        "select id from folders where user_id = $1 and lower(name) = lower($2) and trashed = false limit 1",
        user_id, name,
    ).first()
    if row:
        return row["id"]
    try:
        created = await file_service.create_folder(user_id, name)
        return created.get("id")
    except Exception:
        return None


async def _execute_action(action: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    kind = str(action.get("action") or "").lower()
    language = user.get("language") or "ru"

    if kind == "run_terminal":
        from ..services.terminal_service import terminal_service

        perms = user.get("permissions") or {}
        staff = user.get("role") in ("owner", "admin")
        command = str(action.get("command") or "").strip()[:4000]
        if not staff and not perms.get("terminal"):
            return {
                "type": "terminal", "error": "no_permission", "command": command, "output": "",
                "exit_code": None, "allowed": False,
                "notice": ("Терминал выключен — включи его в настройках, и я смогу выполнять команды."
                           if str(language).lower().startswith("ru") else
                           "Terminal is off — enable it in Settings and I'll be able to run commands."),
            }
        if not command:
            return {"type": "terminal", "error": "empty", "output": "", "exit_code": None, "allowed": False}
        session = next(
            (s for s in terminal_service.sessions.values() if s.user_id == user["id"] and s.alive), None
        )
        if session is None:
            session = await terminal_service.open(user["id"], permissions=perms)
        result = await terminal_service.run(
            session, command, permissions=perms, timeout=int(action.get("timeout") or 30)
        )
        await audit("terminal.command", user_id=user["id"], resource="terminal",
                    resource_id=session.id, detail={"via": "chat", "command": command[:200]})
        return {
            "type": "terminal", "command": command, "allowed": bool(result.get("allowed")),
            "output": (result.get("output") or "")[-8000:], "exit_code": result.get("exit_code"),
            "reason": result.get("reason"), "duration_ms": result.get("duration_ms"),
            "session_id": session.id,
        }

    if kind == "create_document":
        from ..services.documents import document_service

        doc_kind = str(action.get("kind") or "docx").lower()
        if doc_kind not in ("pptx", "xlsx", "docx", "md", "html"):
            doc_kind = "docx"
        brief = str(action.get("brief") or action.get("title") or "")[:4000]
        if not brief:
            return {"error": "empty brief"}
        result = await document_service.create(
            db, user_id=user["id"], kind=doc_kind, brief=brief,
            title=str(action.get("title") or "")[:140] or None, language=language,
            size=str(action.get("size") or "medium"), audience=str(action.get("audience") or ""),
        )
        await audit("document.generated", user_id=user["id"], resource="document",
                    resource_id=result["document_id"], detail={"kind": doc_kind, "via": "chat"})
        return {"type": "document", **result}

    if kind == "generate_image":
        from ..services.image_service import image_service

        prompt = str(action.get("prompt") or "")[:900]
        if not prompt:
            return {"error": "empty prompt"}
        data, meta = await image_service.generate(
            prompt,
            width=int(action.get("width") or 1024),
            height=int(action.get("height") or 1024),
            style=str(action.get("style") or "none"),
            language=language,
            negative=str(action.get("negative") or ""),
        )
        saved = await image_service.save(data, user_id=user["id"], prompt=prompt, meta=meta)
        folder_id = await _resolve_folder(user["id"], action.get("folder"), "Изображения")
        record = await _register_image(user["id"], saved, prompt, meta, data, folder_id=folder_id)
        return {"type": "image", **record}

    if kind == "save_file":
        content = str(action.get("content") or "")
        if not content:
            return {"error": "empty content"}
        folder_id = await _resolve_folder(user["id"], action.get("folder"), "Проекты")
        record = await file_service.save(
            user["id"], name=str(action.get("name") or "note.md"), payload=content.encode("utf-8"),
            folder_id=folder_id, origin="assistant", meta={"created_by": "assistant"},
        )
        return {"type": "file", **record}

    return {"error": f"unknown action: {kind}"}


async def _register_image(
    user_id: str, saved: Any, prompt: str, meta: dict[str, Any], data: bytes,
    *, folder_id: str | None = None,
) -> dict[str, Any]:
    file_record = await file_service.save(
        user_id,
        name=f"{re.sub(r'[^a-z0-9а-яё]+', '-', prompt.lower())[:40] or 'image'}.jpg",
        payload=data,
        kind="image",
        mime="image/jpeg",
        origin="generated",
        folder_id=folder_id,
        meta={"prompt": prompt[:900], "style": meta.get("style"), "seed": meta.get("seed")},
        preview={"width": meta.get("width"), "height": meta.get("height")},
        namespace=f"images/{user_id[:12]}",
    )
    image_id = new_id("img_")
    await db.insert(
        "images",
        {
            "id": image_id, "user_id": user_id, "file_id": file_record["id"], "prompt": prompt[:2000],
            "negative_prompt": "", "style": meta.get("style") or "none", "width": meta.get("width") or 0,
            "height": meta.get("height") or 0, "seed": meta.get("seed") or 0,
            "provider": "internal", "model": "mst-diffusion-v1", "status": "ready",
            "duration_ms": saved.duration_ms, "created_at": utcnow(),
        },
    )
    return {"id": image_id, "file_id": file_record["id"], "url": f"/api/images/{image_id}/file",
            "width": meta.get("width"), "height": meta.get("height"), "style": meta.get("style")}


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
async def _persist(
    user: dict[str, Any],
    conversation_id: str,
    role: str,
    content: str,
    *,
    tokens: dict[str, int] | None = None,
    latency: int | None = None,
    meta: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> str:
    message_id = new_id("msg_")
    await db.insert(
        "messages",
        {
            "id": message_id, "conversation_id": conversation_id, "user_id": user["id"], "role": role,
            "content": content, "attachments": attachments or [], "meta": meta or {},
            "tokens": (tokens or {}).get("total") or 0, "latency_ms": latency or 0,
            "flagged": False, "created_at": utcnow(),
        },
    )
    await db.execute(
        "update conversations set message_count = message_count + 1, updated_at = $1 where id = $2",
        utcnow(), conversation_id,
    )
    if tokens:
        await db.execute(
            "update conversations set token_usage = $1 where id = $2",
            {
                "prompt": int((tokens.get("prompt") or 0)),
                "completion": int((tokens.get("completion") or 0)),
                "total": int((tokens.get("total") or 0)),
            },
            conversation_id,
        )
    return message_id


async def _auto_title(content: str, conversation_id: str, language: str) -> None:
    try:
        title = await ai.quick(
            "title", content[:1200], language=language, temperature=0.3, max_tokens=24
        )
        title = re.sub(r"[\n\r\"»«]+", " ", title).strip()[:70]
        if title:
            await db.execute(
                "update conversations set title = $1, updated_at = $2 where id = $3",
                title, utcnow(), conversation_id,
            )
    except Exception as exc:
        log.debug("auto title failed: %s", exc)


async def _stream_reply(
    request: Request,
    user: dict[str, Any],
    body: MessageBody,
    conversation_id: str,
) -> AsyncIterator[str]:
    language = body.language or user.get("language") or "ru"
    task = "chat"
    verdict = screen_input(body.content, strict=False)
    if not verdict.allowed:
        yield _ndjson("error", code="blocked", message="Запрос отклонён политикой безопасности")
        return
    if verdict.severity in ("medium", "high"):
        yield _ndjson("notice", severity=verdict.severity, reason=verdict.reason)

    attachments_block, attachment_meta = await _attachment_context(user["id"], body.attachments)
    history = await _history(conversation_id)
    messages: list[dict[str, Any]] = list(history)

    system_extra = _agent_footer(language)
    if body.context:
        system_extra += "\n\nДополнительный контекст от клиента: " + json.dumps(body.context, ensure_ascii=False)[:2000]
    if attachments_block:
        system_extra += "\n\nПриложенные материалы:\n" + attachments_block[:24_000]

    engine = PUBLIC_ALIAS.get(task, "mst-core")
    yield _ndjson("meta", conversation_id=conversation_id, engine=engine, mode=body.mode, language=language)
    await _persist(user, conversation_id, "user", body.content, meta={"mode": body.mode}, attachments=attachment_meta)
    if len(history) == 0:
        asyncio.create_task(_auto_title(body.content, conversation_id, language))

    started = datetime.now(timezone.utc)
    buffer: list[str] = []
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    try:
        if body.stream:
            MARK = "```mst-action"
            accum = ""
            sent = 0
            async for piece in ai.stream(
                task,
                messages + [{"role": "user", "content": body.content}],
                system=ai.persona("chat", language) + "\n\n" + system_extra,
                language=language,
            ):
                accum += piece
                cut = accum.find(MARK)
                # Stream only what precedes an action fence; hold back a tail that
                # could still turn into the marker split across chunks.
                visible_upto = cut if cut != -1 else max(sent, len(accum) - (len(MARK) - 1))
                if visible_upto > sent:
                    yield _ndjson("delta", text=accum[sent:visible_upto])
                    sent = visible_upto
                if await request.is_disconnected():
                    break
            if accum.find(MARK) == -1 and len(accum) > sent:
                yield _ndjson("delta", text=accum[sent:])
            text = accum
            tokens = {"completion": max(1, len(text) // 4), "prompt": len(body.content) // 4,
                      "total": max(1, (len(text) + len(body.content)) // 4)}
        else:
            reply = await ai.complete(
                task,
                messages + [{"role": "user", "content": body.content}],
                system=ai.persona("chat", language) + "\n\n" + system_extra,
                language=language,
            )
            text = reply.text
            tokens = reply.tokens
            yield _ndjson("delta", text=text)
    except InferenceError as exc:
        log.warning("inference error: %s", exc)
        yield _ndjson("error", code="inference", message="Модель недоступна, попробуйте ещё раз")
        return
    except asyncio.CancelledError:
        yield _ndjson("error", code="cancelled", message="Ответ прерван")
        raise
    except Exception as exc:  # pragma: no cover
        log.exception("chat stream failed")
        yield _ndjson("error", code="internal", message="Внутренняя ошибка")
        return

    # --- workspace actions ------------------------------------------------
    visible = text
    actions_found = ACTION_RE.findall(text)
    executed: list[dict[str, Any]] = []
    if actions_found:
        visible = ACTION_RE.sub("", text).strip()
        for raw in actions_found[:4]:
            try:
                action = json.loads(raw)
            except json.JSONDecodeError:
                continue
            yield _ndjson("action_start", action=action.get("action"))
            try:
                result = await _execute_action(action, user)
            except Exception as exc:
                log.warning("action failed: %s", exc)
                result = {"error": str(exc)[:200]}
            executed.append({"action": action.get("action"), "result": result})
            yield _ndjson("action", **result)

    latency = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    clean_visible = sanitize_output(visible)
    await _persist(
        user, conversation_id, "assistant", clean_visible,
        tokens=tokens, latency=latency,
        meta={"engine": engine, "mode": body.mode, "actions": [e["action"] for e in executed]},
    )
    await db.execute(
        "update users set last_seen_at = $1 where id = $2", utcnow(), user["id"]
    )
    yield _ndjson(
        "done",
        message_id=None,
        tokens=tokens,
        latency_ms=latency,
        engine=engine,
        text=clean_visible,
        actions=executed,
    )


@router.post("/send")
async def send_message(
    body: MessageBody,
    request: Request,
    user: dict[str, Any] = Depends(Throttler("chat", limit=settings.rate_limit_per_minute)),
) -> Any:
    conversation_id = body.conversation_id
    if not conversation_id:
        conversation_id = new_id("cnv_")
        await db.insert(
            "conversations",
            {
                "id": conversation_id, "user_id": user["id"], "title": "Новый диалог",
                "mode": body.mode, "persona": None, "language": body.language, "context": {},
                "pinned": False, "archived": False, "message_count": 0,
                "token_usage": {"prompt": 0, "completion": 0, "total": 0},
                "created_at": utcnow(), "updated_at": utcnow(),
            },
        )
    else:
        row = await db.fetch(
            "select id from conversations where id = $1 and user_id = $2", conversation_id, user["id"]
        ).first()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Диалог не найден")

    await audit("chat.message", user_id=user["id"], resource="conversation", resource_id=conversation_id,
                ip=client_ip(request), detail={"mode": body.mode, "chars": len(body.content)})

    if body.stream:
        return StreamingResponse(
            _stream_reply(request, user, body, conversation_id),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    collected: dict[str, Any] = {}
    async for line in _stream_reply(request, user, body, conversation_id):
        event = json.loads(line)
        if event["event"] == "done":
            collected = event
        elif event["event"] == "error":
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, event.get("message") or "Ошибка модели")
        elif event["event"] == "action":
            collected.setdefault("actions", []).append(event)
    return {"conversation_id": conversation_id, **collected}


@router.post("/title")
async def regenerate_title(
    conversation_id: str, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    row = await db.fetch(
        "select * from conversations where id = $1 and user_id = $2", conversation_id, user["id"]
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Диалог не найден")
    first = await db.fetch(
        "select content from messages where conversation_id = $1 and role = 'user' order by created_at asc limit 1",
        conversation_id,
    ).first()
    if not first:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нет сообщений")
    title = await ai.quick("title", first["content"][:1200], language=row.get("language") or "ru")
    title = re.sub(r"[\n\r\"»«]+", " ", title).strip()[:70]
    await db.execute("update conversations set title = $1, updated_at = $2 where id = $3",
                     title, utcnow(), conversation_id)
    return {"id": conversation_id, "title": title}


@router.post("/translate")
async def translate(
    payload: dict[str, Any], user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    text = str(payload.get("text") or "")[:8000]
    target = str(payload.get("target") or "en")
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нет текста")
    result = await ai.quick(
        "translate",
        f"Переведи на {target}:\n\n{text}" if target != "ru" else f"Переведи на русский:\n\n{text}",
        language=target,
    )
    return {"text": sanitize_output(result), "target": target}


@router.get("/stats")
async def usage_stats(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    rows = await db.fetch(
        "select count(*) as messages, coalesce(sum(tokens), 0) as tokens, "
        "coalesce(avg(latency_ms), 0) as avg_latency from messages where user_id = $1 and role = 'assistant'",
        user["id"],
    ).first()
    by_mode = await db.fetch(
        "select meta->>'mode' as mode, count(*) as c from messages where user_id = $1 and role = 'assistant' "
        "group by meta->>'mode' order by c desc limit 10", user["id"],
    ) if db.dialect == "postgres" else None
    return {
        "assistant_messages": int(rows["messages"] or 0) if rows else 0,
        "tokens": int(rows["tokens"] or 0) if rows else 0,
        "avg_latency_ms": round(float(rows["avg_latency"] or 0)) if rows else 0,
        "by_mode": [r for r in (by_mode.rows if by_mode else [])],
    }
