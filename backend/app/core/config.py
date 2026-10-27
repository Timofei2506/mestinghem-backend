"""
Mestinghem AI — runtime configuration.

Every secret is read from the environment. Nothing is hard-coded here.
A local `.env` (repo root) is loaded when present so the same code runs on a
laptop, in CI and in production.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv() -> None:
    """Minimal .env loader — avoids a hard dependency at import time."""
    for candidate in (ROOT / ".env", Path.cwd() / ".env"):
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
        break


_load_dotenv()


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _bool(key: str, default: bool = False) -> bool:
    raw = _env(key).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _int(key: str, default: int) -> int:
    try:
        return int(_env(key) or default)
    except ValueError:
        return default


@dataclass(slots=True)
class Settings:
    # --- identity -----------------------------------------------------
    brand: str = _env("BRAND", "Mestinghem AI")
    brand_short: str = _env("BRAND_SHORT", "Mestinghem")
    version: str = _env("APP_VERSION", "1.0.0")
    build: str = _env("BUILD_ID", "aurum")
    languages: tuple[str, ...] = ("ru", "en")
    default_language: str = _env("DEFAULT_LANG", "ru")

    # --- server -------------------------------------------------------
    host: str = _env("HOST", "0.0.0.0")
    port: int = _int("PORT", 8000)
    public_origin: str = _env("PUBLIC_ORIGIN", "")
    debug: bool = _bool("DEBUG", False)
    cors_origins: list[str] = field(
        default_factory=lambda: [
            o.strip()
            for o in _env(
                "CORS_ORIGINS",
                "*",
            ).split(",")
            if o.strip()
        ]
    )

    # --- storage backends --------------------------------------------
    storage_dir: Path = Path(_env("STORAGE_DIR", str(ROOT / "workspace" / "storage")))
    workspace_dir: Path = Path(_env("WORKSPACE_DIR", str(ROOT / "workspace")))

    db_mode: str = _env("DB_MODE", "auto")  # auto | postgres | sqlite
    database_url: str = _env("DATABASE_URL", "")
    sqlite_path: Path = Path(_env("SQLITE_PATH", str(ROOT / "workspace" / "storage" / "local.db")))

    storage_backend: str = _env("STORAGE_BACKEND", "auto")  # auto | local | s3 | postgres
    s3_endpoint: str = _env("R2_ENDPOINT", "")
    s3_bucket: str = _env("R2_BUCKET", "assets")
    s3_access_key: str = _env("R2_ACCESS_KEY_ID", "")
    s3_secret_key: str = _env("R2_SECRET_ACCESS_KEY", "")
    s3_region: str = _env("R2_REGION", "auto")

    # --- inference ----------------------------------------------------
    inference_provider: str = _env("INFERENCE_PROVIDER", "groq")
    inference_key: str = _env("GROQ_API_KEY", "")
    inference_base: str = _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    model_primary: str = _env("MODEL_PRIMARY", "openai/gpt-oss-120b")
    model_fast: str = _env("MODEL_FAST", "openai/gpt-oss-20b")
    model_vision: str = _env("MODEL_VISION", "qwen/qwen3.8-27b")
    inference_timeout: int = _int("INFERENCE_TIMEOUT", 90)
    max_context_messages: int = _int("MAX_CONTEXT_MESSAGES", 40)

    # --- vision / creative -------------------------------------------
    image_provider: str = _env("IMAGE_PROVIDER", "cloudflare")
    image_model: str = _env("IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
    image_timeout: int = _int("IMAGE_TIMEOUT", 150)
    image_fallback_model: str = _env("IMAGE_FALLBACK_MODEL", "@cf/stabilityai/stable-diffusion-xl-base-1.0")
    image_steps: int = _int("IMAGE_STEPS", 6)
    image_ai_account: str = _env("CF_AI_ACCOUNT_ID", _env("CF_ACCOUNT_ID", ""))
    image_ai_token: str = _env("CF_AI_TOKEN", _env("CF_API_TOKEN", ""))
    image_horde_key: str = _env("IMAGE_HORDE_KEY", "")

    # --- engines ------------------------------------------------------
    chess_engine_path: str = _env(
        "CHESS_ENGINE_PATH",
        next(
            (p for p in ("/usr/games/stockfish", "/usr/bin/stockfish", "/usr/local/bin/stockfish")
             if Path(p).exists()),
            "stockfish",
        ),
    )
    chess_engine_skill: int = _int("CHESS_ENGINE_SKILL", 20)  # 0..20
    chess_engine_nodes: int = _int("CHESS_ENGINE_NODES", 400_000)

    # --- auth ---------------------------------------------------------
    secret_key: str = _env("APP_SECRET_KEY") or secrets.token_urlsafe(48)
    jwt_alg: str = _env("JWT_ALG", "HS256")
    access_ttl: int = _int("ACCESS_TOKEN_TTL", 900)
    refresh_ttl: int = _int("REFRESH_TOKEN_TTL", 60 * 60 * 24 * 30)
    bcrypt_rounds: int = _int("BCRYPT_ROUNDS", 12)
    require_email_confirm: bool = _bool("REQUIRE_EMAIL_CONFIRM", False)
    signup_enabled: bool = _bool("SIGNUP_ENABLED", True)

    # --- guardrails ---------------------------------------------------
    rate_limit_per_minute: int = _int("RATE_LIMIT_PER_MINUTE", 90)
    chat_chars_per_minute: int = _int("CHAT_CHARS_PER_MINUTE", 24_000)
    upload_max_mb: int = _int("UPLOAD_MAX_MB", 64)
    terminal_enabled: bool = _bool("TERMINAL_ENABLED", True)
    terminal_timeout: int = _int("TERMINAL_TIMEOUT", 120)
    agent_enabled: bool = _bool("AGENT_ENABLED", True)
    agent_require_consent: bool = _bool("AGENT_REQUIRE_CONSENT", True)

    # --- misc ---------------------------------------------------------
    sentry_dsn: str = _env("SENTRY_DSN", "")
    contact_email: str = _env("CONTACT_EMAIL", "privacy@mestinghem.ai")
    support_email: str = _env("SUPPORT_EMAIL", "support@mestinghem.ai")
    dpo_email: str = _env("DPO_EMAIL", "dpo@mestinghem.ai")
    legal_entity: str = _env("LEGAL_ENTITY", "Mestinghem AI UAB")
    legal_address: str = _env("LEGAL_ADDRESS", "Vilnius, Lithuania")

    def as_public_dict(self) -> dict[str, Any]:
        """Non-sensitive configuration the browser is allowed to see.

        Deliberately excludes credentials, provider hostnames, model names and
        internal paths: the client learns *capabilities*, never *plumbing*.
        """
        return {
            "brand": self.brand,
            "brand_short": self.brand_short,
            "version": self.version,
            "build": self.build,
            "languages": list(self.languages),
            "default_language": self.default_language,
            "signup_enabled": self.signup_enabled,
            "features": {
                "chat": True,
                "streaming": True,
                "terminal": self.terminal_enabled,
                "chess": True,
                "documents": ["pptx", "xlsx", "docx", "md", "html"],
                "image_generation": True,
                "file_manager": True,
                "remote_agent": self.agent_enabled,
                "two_factor": True,
                "audit_log": True,
            },
            "limits": {
                "upload_max_mb": self.upload_max_mb,
                "rate_limit_per_minute": self.rate_limit_per_minute,
            },
            "contact": {
                "support": self.support_email,
                "privacy": self.contact_email,
                "dpo": self.dpo_email,
            },
        }


settings = Settings()
settings.storage_dir.mkdir(parents=True, exist_ok=True)
settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
