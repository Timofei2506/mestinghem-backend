"""
Object storage.

Two interchangeable backends behind one interface:

* ``local`` — files under ``workspace/storage`` (always available).
* ``s3``    — any S3-compatible object store (Cloudflare R2, MinIO, …).

The backend is chosen automatically at boot: if credentials *and* a reachable
bucket are present, objects go to the remote store; otherwise the platform
degrades to local disk without losing functionality. Clients only ever see an
opaque ``file_id`` and a signed download route — never a bucket name or key.
"""
from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from ..core.config import settings

log = logging.getLogger("storage")

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(name: str, max_len: int = 60) -> str:
    name = (name or "file").strip()
    name = _SAFE.sub("-", name).strip("-.")
    return (name or "file")[:max_len]


def guess_mime(name: str) -> str:
    mime, _ = mimetypes.guess_type(name)
    return mime or "application/octet-stream"


@dataclass(slots=True)
class StoredObject:
    key: str
    backend: str
    size: int
    checksum: str
    mime: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "backend": self.backend,
            "size": self.size,
            "checksum": self.checksum,
            "mime": self.mime,
        }


class ObjectStorage:
    def __init__(self) -> None:
        self.backend: str = "local"
        self._s3: Any = None
        self._bucket: str = settings.s3_bucket
        self.root: Path = settings.storage_dir / "objects"
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_base: str = ""

    # ------------------------------------------------------------------ boot
    async def connect(self) -> None:
        if settings.storage_backend == "local":
            log.info("storage: local disk")
            return
        if settings.storage_backend == "postgres":
            self.backend = "postgres"
            await self._ensure_table()
            log.info("storage: managed database (postgres)")
            return
        if settings.s3_access_key and settings.s3_endpoint:
            try:
                await self._probe_s3()
                self.backend = "s3"
                log.info("storage: remote object store")
                return
            except Exception as exc:
                log.warning("remote object store unavailable (%s) — falling back", exc)
        # auto: prefer the managed database when it is Postgres (durable, no
        # separate object store / billing required), else local disk.
        from ..core.db import db

        if db.dialect == "postgres":
            self.backend = "postgres"
            await self._ensure_table()
            log.info("storage: managed database (postgres)")
            return
        self.backend = "local"
        log.info("storage: local disk")

    async def _ensure_table(self) -> None:
        from ..core.db import db

        if db.dialect == "postgres":
            await db.execute(
                "create table if not exists storage_objects ("
                "object_key text primary key, data text, mime text, "
                "size bigint not null default 0, checksum text, "
                "created_at timestamptz default now())"
            )
        else:
            await db.execute(
                "create table if not exists storage_objects ("
                "object_key text primary key, data text, mime text, "
                "size integer not null default 0, checksum text, created_at text)"
            )

    async def _probe_s3(self) -> None:
        import boto3
        from botocore.client import Config

        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region or "auto",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        try:
            client.head_bucket(Bucket=self._bucket)
        except Exception:
            client.create_bucket(Bucket=self._bucket)
            log.info("object store bucket provisioned")
        self._s3 = client

    # ------------------------------------------------------------------- ops
    @staticmethod
    def _key(namespace: str, filename: str, *, unique: str = "") -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y/%m")
        token = unique or hashlib.sha1(f"{filename}{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:12]
        return f"{namespace}/{stamp}/{token}-{slugify(filename)}"

    async def put(
        self,
        data: bytes | BinaryIO,
        filename: str,
        *,
        namespace: str = "files",
        mime: str | None = None,
        key: str | None = None,
    ) -> StoredObject:
        payload = data.read() if hasattr(data, "read") else bytes(data)
        checksum = hashlib.sha256(payload).hexdigest()
        object_key = key or self._key(namespace, filename)
        content_type = mime or guess_mime(filename)
        if self.backend == "s3" and self._s3 is not None:
            self._s3.put_object(
                Bucket=self._bucket,
                Key=object_key,
                Body=payload,
                ContentType=content_type,
                Metadata={"checksum": checksum},
            )
        elif self.backend == "postgres":
            import base64

            from ..core.db import db

            blob = base64.b64encode(payload).decode("ascii")
            if db.dialect == "postgres":
                await db.execute(
                    "insert into storage_objects (object_key, data, mime, size, checksum, created_at) "
                    "values ($1, $2, $3, $4, $5, now()) "
                    "on conflict (object_key) do update set data = excluded.data, "
                    "mime = excluded.mime, size = excluded.size, checksum = excluded.checksum",
                    object_key, blob, content_type, len(payload), checksum,
                )
            else:
                await db.execute(
                    "insert or replace into storage_objects "
                    "(object_key, data, mime, size, checksum, created_at) "
                    "values ($1, $2, $3, $4, $5, $6)",
                    object_key, blob, content_type, len(payload), checksum,
                    datetime.now(timezone.utc).isoformat(),
                )
        else:
            path = self.root / object_key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        return StoredObject(object_key, self.backend, len(payload), checksum, content_type)

    async def get(self, key: str) -> bytes:
        if self.backend == "s3" and self._s3 is not None:
            return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        if self.backend == "postgres":
            import base64

            from ..core.db import db

            row = await db.fetch(
                "select data from storage_objects where object_key = $1", key
            ).first()
            if not row or not row.get("data"):
                raise FileNotFoundError(key)
            return base64.b64decode(row["data"])
        path = self.root / key
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    async def stream(self, key: str) -> io.BytesIO:
        return io.BytesIO(await self.get(key))

    async def delete(self, key: str) -> None:
        try:
            if self.backend == "s3" and self._s3 is not None:
                self._s3.delete_object(Bucket=self._bucket, Key=key)
            elif self.backend == "postgres":
                from ..core.db import db

                await db.execute("delete from storage_objects where object_key = $1", key)
            else:
                (self.root / key).unlink(missing_ok=True)
        except Exception as exc:  # pragma: no cover
            log.warning("delete %s failed: %s", key, exc)

    async def exists(self, key: str) -> bool:
        if self.backend == "s3" and self._s3 is not None:
            try:
                self._s3.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception:
                return False
        if self.backend == "postgres":
            from ..core.db import db

            row = await db.fetch(
                "select 1 as present from storage_objects where object_key = $1", key
            ).first()
            return bool(row)
        return (self.root / key).is_file()

    def presigned_url(self, key: str, expires: int = 900) -> str | None:
        """Remote-only convenience; local objects are served by the API."""
        if self.backend != "s3" or self._s3 is None:
            return None
        try:
            return self._s3.generate_presigned_url(
                "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires
            )
        except Exception:  # pragma: no cover
            return None

    def health(self) -> dict[str, Any]:
        label = {
            "s3": "managed-object-store",
            "postgres": "managed-database",
        }.get(self.backend, "local")
        return {"backend": label, "ready": True}


storage = ObjectStorage()
