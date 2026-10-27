"""
Trust & safety primitives.

Password hashing is layered on purpose: a fast memory-hard KDF (PBKDF2-SHA256,
210 000 rounds) is folded *into* bcrypt, so an attacker who steals the database
must break both — and bcrypt alone is still available for legacy records.

Also here: JWT issuing/verification, TOTP two-factor, audit logging, sliding
window rate limiting and the input/output guardrails that sit in front of the
inference layer.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import bcrypt
import jwt as pyjwt

from .core.config import settings
from .core.db import Database, db, new_id

log = logging.getLogger("security")

# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
_PBKDF2_ROUNDS = 210_000
_FORMAT = "mst$1"


def _pbkdf2(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS, dklen=32)


def hash_password(password: str) -> str:
    """Return a self-describing hash string: mst$1$<salt>$<digest>."""
    salt = secrets.token_bytes(16)
    derived = base64.b64encode(_pbkdf2(password, salt)).decode()
    digest = bcrypt.hashpw(derived.encode(), bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode()
    return f"{_FORMAT}${base64.b64encode(salt).decode()}${digest}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    try:
        if stored.startswith(_FORMAT + "$"):
            _, _, salt_b64, digest = stored.split("$", 3)
            salt = base64.b64decode(salt_b64)
            derived = base64.b64encode(_pbkdf2(password, salt)).decode()
            return bcrypt.checkpw(derived.encode(), digest.encode())
        return bcrypt.checkpw(password.encode(), stored.encode())
    except Exception:
        return False


def password_strength(password: str) -> tuple[int, list[str]]:
    """Score 0..4 plus human-readable advice (used by the signup meter)."""
    issues: list[str] = []
    score = 0
    if len(password) >= 10:
        score += 1
    else:
        issues.append("min10")
    if len(password) >= 16:
        score += 1
    classes = sum(
        bool(re.search(p, password))
        for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w\s]")
    )
    score += 1 if classes >= 3 else 0
    score += 1 if classes == 4 and len(password) >= 16 else 0
    if re.search(r"(.)\1{3,}", password):
        score = max(0, score - 1)
        issues.append("repeat")
    lowered = password.lower()
    if any(w in lowered for w in ("password", "qwerty", "123456", "admin", "mestinghem")):
        score = max(0, score - 2)
        issues.append("common")
    return min(score, 4), issues


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def issue_access_token(user: dict[str, Any], scopes: Iterable[str] = ("user",)) -> str:
    payload = {
        "sub": user["id"],
        "email": user.get("email"),
        "role": user.get("role", "user"),
        "plan": user.get("plan", "free"),
        "scopes": list(scopes),
        "typ": "access",
        "iat": int(_now().timestamp()),
        "exp": int((_now() + timedelta(seconds=settings.access_ttl)).timestamp()),
        "jti": new_id("jti_"),
    }
    return pyjwt.encode(payload, settings.secret_key, algorithm=settings.jwt_alg)


def issue_refresh_token(user_id: str, family: str | None = None) -> tuple[str, str, str, datetime]:
    """Returns (raw_token, token_hash, family, expires_at)."""
    raw = secrets.token_urlsafe(48)
    expires = _now() + timedelta(seconds=settings.refresh_ttl)
    return raw, token_hash(raw), family or new_id("fam_"), expires


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    payload = pyjwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_alg],
        options={"require": ["exp", "sub"]},
    )
    if payload.get("typ") != expected_type:
        raise pyjwt.InvalidTokenError("wrong token type")
    return payload


# ---------------------------------------------------------------------------
# TOTP two-factor (RFC 6238) — no third-party dependency
# ---------------------------------------------------------------------------
def totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_uri(secret: str, account: str, issuer: str = "Mestinghem AI") -> str:
    return f"otpauth://totp/{issuer}:{account}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"


def totp_code(secret: str, at: float | None = None, digits: int = 6, period: int = 30) -> str:
    counter = int((at if at is not None else time.time()) // period)
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    code = re.sub(r"\D", "", code or "")
    if len(code) != 6 or not secret:
        return False
    now = time.time()
    return any(totp_code(secret, now + drift * 30) == code for drift in range(-window, window + 1))


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
async def audit(
    action: str,
    *,
    user_id: str | None = None,
    actor: str = "user",
    resource: str | None = None,
    resource_id: str | None = None,
    outcome: str = "success",
    ip: str | None = None,
    user_agent: str | None = None,
    detail: dict[str, Any] | None = None,
    database: Database | None = None,
) -> None:
    """Append an immutable audit record. Never raises into the request path."""
    store = database or db
    try:
        await store.insert(
            "audit_logs",
            {
                "id": new_id("aud_"),
                "user_id": user_id,
                "actor": actor,
                "action": action,
                "resource": resource,
                "resource_id": resource_id,
                "outcome": outcome,
                "ip": ip,
                "user_agent": (user_agent or "")[:300],
                "detail": detail or {},
                "created_at": _now().isoformat(),
            },
        )
    except Exception as exc:  # pragma: no cover
        log.warning("audit write failed: %s", exc)


async def security_event(
    kind: str,
    *,
    severity: str = "info",
    user_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    detail: dict[str, Any] | None = None,
    database: Database | None = None,
) -> None:
    store = database or db
    try:
        await store.insert(
            "security_events",
            {
                "id": new_id("sec_"),
                "user_id": user_id,
                "kind": kind,
                "severity": severity,
                "ip": ip,
                "user_agent": (user_agent or "")[:300],
                "detail": detail or {},
                "resolved": False,
                "created_at": _now().isoformat(),
            },
        )
    except Exception as exc:  # pragma: no cover
        log.warning("security event write failed: %s", exc)


# ---------------------------------------------------------------------------
# Rate limiting — sliding window per bucket
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, window: int = 60) -> None:
        self.window = window
        self._memory: dict[str, list[float]] = {}

    async def hit(
        self,
        bucket: str,
        *,
        limit: int | None = None,
        cost: int = 1,
        database: Database | None = None,
    ) -> tuple[bool, int, float]:
        """Returns (allowed, remaining, retry_after_seconds)."""
        limit = limit if limit is not None else settings.rate_limit_per_minute
        store = database or db
        now = time.time()
        window_start = int(now // self.window) * self.window
        try:
            row = await store.fetch(
                "select id, count, window_start from rate_counters where bucket = $1", bucket
            ).first()
            if row and int(row["window_start"]) == window_start:
                count = int(row["count"]) + cost
                if count > limit:
                    return False, 0, float(self.window - (now - window_start))
                await store.execute(
                    "update rate_counters set count = $1, updated_at = $2 where id = $3",
                    count,
                    _now().isoformat(),
                    row["id"],
                )
                return True, max(limit - count, 0), 0.0
            await store.execute(
                "delete from rate_counters where bucket = $1", bucket
            )
            await store.insert(
                "rate_counters",
                {
                    "id": new_id("rl_"),
                    "bucket": bucket,
                    "window_start": window_start,
                    "count": cost,
                    "chars": 0,
                    "updated_at": _now().isoformat(),
                },
            )
            return True, max(limit - cost, 0), 0.0
        except Exception as exc:
            # Fail-open on infrastructure trouble, fail-safe on abuse:
            # keep a short in-memory counter so a DB outage cannot disable limits.
            hits = [t for t in self._memory.setdefault(bucket, []) if now - t < self.window]
            hits.append(now)
            self._memory[bucket] = hits
            log.debug("rate limiter degraded to memory: %s", exc)
            if len(hits) > limit:
                return False, 0, float(self.window - (now - hits[0]))
            return True, max(limit - len(hits), 0), 0.0


rate_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class GuardVerdict:
    allowed: bool
    reason: str = ""
    severity: str = "none"
    rewritten: str = ""


_INJECTION_PATTERNS = (
    r"ignore (all |any )?(previous|prior|above) (instructions|messages)",
    r"disregard (the )?(system|safety) prompt",
    r"reveal|print|repeat (your )?(system prompt|hidden instructions)",
    r"you are now (in )?(developer|dan|jailbreak) mode",
    r"\bdo anything now\b",
    r"pretend (you have|you are) (no|without) (rules|restrictions)",
    r"override (your|the) (safety|content) (policy|rules)",
)
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.I)

_PII_RE = {
    "card": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"),
    "phone": re.compile(r"\+?\d[\d\s().-]{9,18}\d"),
    "secret": re.compile(
        r"\b(?:sk|pk|gsk|napi|cfat|xox[baprs]|gh[pousr]|AKIA)[-_A-Za-z0-9]{8,}\b"
    ),
}

_BLOCKED_TOPICS = (
    "child sexual abuse",
    "csam",
    "weapon of mass destruction construction",
)


def screen_input(text: str, *, strict: bool = False) -> GuardVerdict:
    """Pre-flight check on user-supplied text."""
    if not text:
        return GuardVerdict(True, reason="empty")
    lowered = text.lower()
    for topic in _BLOCKED_TOPICS:
        if topic in lowered:
            return GuardVerdict(False, reason="prohibited_topic", severity="critical")

    secret_hits = _PII_RE["secret"].findall(text)
    if secret_hits and strict:
        return GuardVerdict(False, reason="credentials_detected", severity="high")

    redacted = text
    for key in ("secret", "card"):
        redacted = _PII_RE[key].sub(lambda m: f"[redacted-{key}]", redacted)

    injection = bool(_INJECTION_RE.search(text))
    if injection:
        return GuardVerdict(
            allowed=not strict,
            reason="prompt_injection_pattern",
            severity="medium" if not strict else "high",
            rewritten=redacted,
        )
    return GuardVerdict(True, reason="ok", rewritten=redacted)


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SCRIPT_RE = re.compile(r"<\s*(script|iframe|object|embed|link|meta)\b.*?>.*?<\s*/\s*\1\s*>", re.I | re.S)
_TAG_OPEN_RE = re.compile(r"<\s*/?\s*(script|iframe|object|embed|style|svg|link|meta|form)\b[^>]*>", re.I)
_EVENT_RE = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_URL_RE = re.compile(r"\bjavascript\s*:", re.I)


def sanitize_output(text: str) -> str:
    """Neutralise anything executable before model output reaches the DOM."""
    if not text:
        return ""
    cleaned = _CONTROL_RE.sub("", text)
    cleaned = _SCRIPT_RE.sub("", cleaned)
    cleaned = _TAG_OPEN_RE.sub("", cleaned)
    cleaned = _EVENT_RE.sub("", cleaned)
    cleaned = _URL_RE.sub("", cleaned)
    return cleaned


def mask_secret(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "•" * len(value)
    return f"{value[:visible]}{'•' * 8}{value[-visible:]}"


def client_ip(request: Any) -> str:
    """Best-effort client address, honouring proxy headers."""
    try:
        headers = request.headers
    except AttributeError:
        return ""
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    real_ip = headers.get("cf-connecting-ip") or headers.get("x-real-ip")
    if real_ip:
        return real_ip[:64]
    client = getattr(request, "client", None)
    return (client.host if client else "")[:64]


def client_fingerprint(ip: str | None, user_agent: str | None) -> str:
    raw = f"{ip or '-'}|{(user_agent or '-')[:120]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def csrf_token(session_id: str) -> str:
    return hmac.new(
        settings.secret_key.encode(), session_id.encode(), hashlib.sha256
    ).hexdigest()[:32]


def pairing_code() -> str:
    """Human-readable device pairing code (Crockford-ish, no ambiguous glyphs)."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def signing_key(device_key: str) -> bytes:
    return hashlib.sha256(f"{settings.secret_key}:{device_key}".encode()).digest()


def sign_command(device_key: str, payload: str, nonce: str) -> str:
    """HMAC over the canonical command envelope (agent-side verification)."""
    message = f"{nonce}\n{payload}".encode("utf-8")
    return hmac.new(signing_key(device_key), message, hashlib.sha256).hexdigest()
