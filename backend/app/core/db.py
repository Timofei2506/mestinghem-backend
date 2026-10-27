"""
Persistence layer.

One small async API, two interchangeable backends:

* ``postgres`` — Neon serverless Postgres (production).
* ``sqlite``   — zero-setup local fallback so the platform always boots,
                 even with no network or no credentials.

SQL is written once, in Postgres dialect (``$1`` placeholders). A lightweight
translator rewrites it for SQLite and normalises the few type differences
(``JSONB`` → ``JSON``, ``gen_random_uuid()`` → client-side UUID, …).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Sequence

from .config import settings

log = logging.getLogger("db")

_PLACEHOLDER = re.compile(r"\$(\d+)")

# SQLite cannot swallow every Postgres type verbatim; these are the only
# rewrites the runtime path needs (DDL is generated per-dialect in migrations).
_SQLITE_TYPE_MAP = (
    (re.compile(r"\bjsonb\b", re.I), "json"),
    (re.compile(r"\btimestamptz\b", re.I), "text"),
    (re.compile(r"\buuid\b", re.I), "text"),
    (re.compile(r"\bgen_random_uuid\(\)", re.I), "?"),
    (re.compile(r"\bnow\(\)", re.I), "CURRENT_TIMESTAMP"),
)


def _translate(sql: str, dialect: str) -> str:
    """Rewrite Postgres-flavoured SQL into something SQLite accepts."""
    if dialect == "postgres":
        return sql
    for pattern, replacement in _SQLITE_TYPE_MAP:
        sql = pattern.sub(replacement, sql)
    return sql


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _as_datetime(value: str) -> datetime | None:
    """Parse the ISO timestamps this app writes; None when it is not one."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _adapt_params(sql: str, params: Sequence[Any], dialect: str) -> tuple[str, list[Any]]:
    """Flatten Postgres-isms into values each backend understands."""
    adapted: list[Any] = []
    for value in params:
        if isinstance(value, (dict, list, tuple)):
            if dialect == "postgres":
                # A JSONB codec is registered on connect, so pass the object through.
                adapted.append(list(value) if isinstance(value, tuple) else value)
            else:
                adapted.append(json.dumps(value, ensure_ascii=False, default=str))
        elif isinstance(value, uuid.UUID):
            adapted.append(str(value))
        elif dialect == "sqlite":
            if isinstance(value, bool):
                adapted.append(int(value))
            elif isinstance(value, datetime):
                adapted.append(value.isoformat())
            else:
                adapted.append(value)
        elif isinstance(value, str) and _ISO_RE.match(value):
            # Postgres TIMESTAMPTZ wants a real datetime, not an ISO string.
            parsed = _as_datetime(value)
            adapted.append(parsed if parsed is not None else value)
        else:
            adapted.append(value)

    sql = _translate(sql, dialect)
    if dialect == "postgres":
        return sql, adapted

    # SQLite placeholders are positional "?" — reorder values to match $n usage.
    order = [int(m.group(1)) for m in _PLACEHOLDER.finditer(sql)]
    sql = _PLACEHOLDER.sub("?", sql)
    if order:
        adapted = [adapted[i - 1] for i in order if 1 <= i <= len(adapted)]
    return sql, adapted


async def _register_codecs(conn: Any) -> None:
    """Let dicts/lists flow straight into JSONB columns and back out again."""
    for typename in ("jsonb", "json"):
        try:
            await conn.set_type_codec(
                typename,
                encoder=lambda v: json.dumps(v, ensure_ascii=False, default=str),
                decoder=lambda v: json.loads(v) if isinstance(v, str) else v,
                schema="pg_catalog",
                format="text",
            )
        except Exception as exc:  # pragma: no cover
            log.debug("codec %s: %s", typename, exc)


@dataclass
class Result:
    rows: list[dict[str, Any]]

    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def all(self) -> list[dict[str, Any]]:
        return self.rows

    def one_or_none(self) -> dict[str, Any] | None:
        return self.rows[0] if len(self.rows) == 1 else None

    def scalar(self, default: Any = None) -> Any:
        row = self.first()
        if not row:
            return default
        return next(iter(row.values()), default)

    def __iter__(self) -> Iterable[dict[str, Any]]:
        return iter(self.rows)

    def __len__(self) -> int:
        return len(self.rows)


class _ResultProxy:
    """Lets `await db.fetch(sql, ...).first()` work.

    Much of the codebase chains a result helper straight onto the query call,
    and awaiting a coroutine there would explode. So the query is a plain
    callable, the chain is recorded lazily and only runs once awaited.
    """

    __slots__ = ("_fn", "_args", "_kwargs", "_chain")

    def __init__(self, fn: Any, args: tuple, kwargs: dict) -> None:
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._chain: tuple = ()

    def _extend(self, step: Any) -> "_ResultProxy":
        proxy = _ResultProxy(self._fn, self._args, self._kwargs)
        proxy._chain = self._chain + (step,)
        return proxy

    def __getattr__(self, name: str) -> "_ResultProxy":
        if name.startswith("__"):
            raise AttributeError(name)
        return self._extend(name)

    def __call__(self, *args: Any) -> "_ResultProxy":
        return self._extend(args)

    def __await__(self):
        return self._resolve().__await__()

    async def _resolve(self) -> Any:
        value: Any = await self._fn(*self._args, **self._kwargs)
        for step in self._chain:
            value = value(*step) if isinstance(step, tuple) else getattr(value, step)
        return value

    def result(self) -> Any:
        """Await-free access for code that already holds the Result."""
        return None


class Database:
    def __init__(self) -> None:
        self.dialect: str = "sqlite"
        self._pg: Any = None
        self._pg_pool: Any = None
        self._sqlite_lock = asyncio.Lock()
        self._conn: Any = None
        self.ready = False
        self.latency_ms: float = 0.0

    # ------------------------------------------------------------------ boot
    async def connect(self) -> None:
        mode = settings.db_mode.lower()
        if mode in ("auto", "postgres") and settings.database_url:
            try:
                await self._connect_postgres()
                return
            except Exception as exc:  # pragma: no cover - network dependent
                log.warning("Postgres unavailable (%s) — falling back to SQLite", exc)
                if mode == "postgres":
                    raise
        await self._connect_sqlite()

    async def _connect_postgres(self) -> None:
        import asyncpg

        url = settings.database_url
        conn = await asyncpg.connect(dsn=url, ssl="require", timeout=30, command_timeout=60)
        await _register_codecs(conn)
        await conn.fetchval("select 1")
        self._pg = asyncpg
        self._conn = conn
        self.dialect = "postgres"
        self.ready = True
        log.info("database: neon postgres connected")

    async def _connect_sqlite(self) -> None:
        import aiosqlite

        settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(settings.sqlite_path), isolation_level=None)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=8000")
        conn.row_factory = aiosqlite.Row
        self._conn = conn
        self.dialect = "sqlite"
        self.ready = True
        log.info("database: local sqlite at %s", settings.sqlite_path)

    async def close(self) -> None:
        try:
            if self._conn is not None:
                await self._conn.close()
        finally:
            self._conn = None
            self.ready = False

    async def reconnect(self) -> None:
        await self.close()
        await self.connect()

    # --------------------------------------------------------------- queries
    def fetch(self, sql: str, *params: Any) -> _ResultProxy:
        """Await it directly, or chain `.first()` / `.scalar()` / `.rows` first."""
        return _ResultProxy(self._fetch_rows, (sql, params), {})

    async def _fetch_rows(self, sql: str, params: tuple) -> Result:
        started = time.perf_counter()
        query, values = _adapt_params(sql, params, self.dialect)
        if self.dialect == "postgres":
            import asyncpg

            try:
                raw = await self._conn.fetch(query, *values)
            except (asyncpg.exceptions.InterfaceError, OSError, ConnectionError):
                await self.reconnect()
                raw = await self._conn.fetch(query, *values)
            rows = [dict(r) for r in raw]
        else:
            async with self._sqlite_lock:
                cur = await self._conn.execute(query, values)
                raw = await cur.fetchall()
                rows = [dict(r) for r in raw]
                await cur.close()
        self.latency_ms = (time.perf_counter() - started) * 1000
        return Result(rows=[self._decode(r) for r in rows])

    async def fetchval(self, sql: str, *params: Any, default: Any = None) -> Any:
        res = await self._fetch_rows(sql, params)
        return res.scalar(default)

    async def _result_of(self, sql: str, params: tuple) -> Result:
        started = time.perf_counter()
        query, values = _adapt_params(sql, params, self.dialect)
        if self.dialect == "postgres":
            import asyncpg

            try:
                raw = await self._conn.fetch(query, *values)
            except (asyncpg.exceptions.InterfaceError, OSError, ConnectionError):
                await self.reconnect()
                raw = await self._conn.fetch(query, *values)
            rows = [dict(r) for r in raw]
        else:
            async with self._sqlite_lock:
                cur = await self._conn.execute(query, values)
                raw = await cur.fetchall()
                rows = [dict(r) for r in raw]
                await cur.close()
        self.latency_ms = (time.perf_counter() - started) * 1000
        return Result(rows=[self._decode(r) for r in rows])

    async def execute(self, sql: str, *params: Any) -> int:
        """Run a statement. Returns rows affected (sqlite) or 0 (postgres)."""
        started = time.perf_counter()
        query, values = _adapt_params(sql, params, self.dialect)
        if self.dialect == "postgres":
            import asyncpg

            try:
                status = await self._conn.execute(query, *values)
            except (asyncpg.exceptions.InterfaceError, OSError, ConnectionError):
                await self.reconnect()
                status = await self._conn.execute(query, *values)
            self.latency_ms = (time.perf_counter() - started) * 1000
            try:
                return int(status.split()[-1])
            except (ValueError, IndexError):
                return 0
        async with self._sqlite_lock:
            cur = await self._conn.execute(query, values)
            await self._conn.commit()
            affected = cur.rowcount
            await cur.close()
        self.latency_ms = (time.perf_counter() - started) * 1000
        return max(affected, 0)

    async def insert(self, table: str, data: dict[str, Any], returning: str = "id") -> Any:
        cols = list(data)
        values = [data[c] for c in cols]
        holders = ", ".join(f"${i+1}" for i in range(len(cols)))
        col_sql = ", ".join(cols)
        sql = f"insert into {table} ({col_sql}) values ({holders})"
        if self.dialect == "postgres":
            sql += f" returning {returning}"
            res = await self.fetch(sql, *values).scalar()
            return res
        await self.execute(sql, *values)
        if returning == "id":
            return data.get("id")
        return await self.fetch(
            f"select {returning} from {table} where rowid = last_insert_rowid()"
        ).scalar()

    async def script(self, statements: Iterable[str]) -> None:
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            await self.execute(stmt)

    # ----------------------------------------------------------------- utils
    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        for key, value in row.items():
            if isinstance(value, (bytes, bytearray)):
                try:
                    row[key] = json.loads(value.decode("utf-8"))
                except Exception:
                    row[key] = value.decode("utf-8", "replace")
            elif isinstance(value, str) and value[:1] in "[{" and len(value) > 1:
                try:
                    row[key] = json.loads(value)
                except Exception:
                    pass
        return row

    def health(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "engine": "managed-cloud" if self.dialect == "postgres" else "embedded",
            "latency_ms": round(self.latency_ms, 2),
        }


db = Database()


@asynccontextmanager
async def database_lifespan() -> AsyncIterator[None]:
    await db.connect()
    from ..migrations import apply_migrations

    await apply_migrations(db)
    try:
        yield
    finally:
        await db.close()


def new_id(prefix: str = "") -> str:
    token = uuid.uuid4().hex[:20]
    return f"{prefix}{token}" if prefix else token
