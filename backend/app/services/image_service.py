"""
Image generation.

The platform talks to an open, keyless generation endpoint through a thin
adapter. Nothing about that endpoint is visible to the client: the browser
asks for "an image with these parameters" and receives a ``file_id`` served
from our own storage under our own URL. Prompts are screened, results are
persisted, and a second engine is tried automatically if the first fails.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from PIL import Image

from ..core.config import settings
from .storage_service import storage

log = logging.getLogger("images")

ENDPOINT = "https://image.pollinations.ai/prompt/{prompt}"
AI_GATEWAY = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
MAX_SIDE = 2048

# Public engine catalogue. Clients only ever see our own names — the underlying
# open models are an implementation detail of the platform.
ENGINES: dict[str, dict[str, Any]] = {
    "mst-diffusion-v1": {
        "model": "@cf/black-forest-labs/flux-1-schnell",
        "label": {"ru": "Стандарт", "en": "Standard"},
        "note": {"ru": "Универсальный движок — баланс скорости и качества", "en": "Balanced all-round engine"},
        "max_side": 2048,
    },
    "mst-diffusion-pro": {
        "model": "@cf/leonardo/phoenix-1.0",
        "label": {"ru": "Студия", "en": "Studio"},
        "note": {"ru": "Максимальная детализация и свет", "en": "Highest detail and lighting"},
        "max_side": 1024,
    },
    "mst-diffusion-fast": {
        "model": "@cf/bytedance/stable-diffusion-xl-lightning",
        "label": {"ru": "Быстрый", "en": "Fast"},
        "note": {"ru": "Мгновенный результат для набросков", "en": "Instant drafts"},
        "max_side": 1024,
    },
    "mst-diffusion-art": {
        "model": "@cf/lykon/dreamshaper-8-lcm",
        "label": {"ru": "Арт", "en": "Art"},
        "note": {"ru": "Живописный, иллюстративный стиль", "en": "Painterly illustration"},
        "max_side": 1024,
    },
    "mst-diffusion-portrait": {
        "model": "horde:Juggernaut XL",
        "label": {"ru": "Портрет", "en": "Portrait"},
        "note": {"ru": "Фотореализм и лица крупным планом", "en": "Photorealism and close-up faces"},
        "max_side": 1024,
    },
    "mst-diffusion-classic": {
        "model": "horde:Deliberate",
        "label": {"ru": "Классика", "en": "Classic"},
        "note": {"ru": "Классическая живопись и иллюстрация", "en": "Classic painting and illustration"},
        "max_side": 1024,
    },
}

# Distributed open-model pool used when the primary backend is saturated.
HORDE_API = "https://aihorde.net/api/v2"
HORDE_POOL = ("Juggernaut XL", "Deliberate", "AlbedoBase XL (SDXL)", "stable_diffusion")
DEFAULT_ENGINE = "mst-diffusion-v1"

# Styles map a friendly name onto prompt modifiers — the user never has to
# write prompt engineering to get a premium-looking result.
STYLES: dict[str, dict[str, str]] = {
    "none": {"en": "", "ru": ""},
    "photo": {
        "en": "ultra realistic photography, 85mm lens, soft natural light, shallow depth of field, high detail",
        "ru": "ультрареалистичная фотография, объектив 85 мм, мягкий естественный свет, малая глубина резкости, высокая детализация",
    },
    "cinema": {
        "en": "cinematic still, anamorphic lens flare, dramatic rim lighting, teal and orange grade, film grain, 35mm",
        "ru": "кинокадр, анаморфный блик, драматичный контровой свет, бирюзово-оранжевый грейд, зерно плёнки, 35 мм",
    },
    "luxury": {
        "en": "luxury editorial, black and champagne gold palette, obsidian background, studio lighting, premium minimal composition",
        "ru": "люксовый эдиториал, палитра чёрного и шампанского золота, обсидиановый фон, студийный свет, премиальная минималистичная композиция",
    },
    "neon": {
        "en": "neon noir, cyberpunk city, volumetric fog, glowing signage, reflective wet asphalt, high contrast",
        "ru": "неоновый нуар, киберпанк-город, объёмный туман, светящиеся вывески, мокрый отражающий асфальт, высокий контраст",
    },
    "anime": {
        "en": "anime key visual, crisp lineart, vibrant cel shading, dynamic composition",
        "ru": "аниме-кей-визуал, чёткий лайнарт, яркая cel-тень, динамичная композиция",
    },
    "oil": {
        "en": "oil painting, impasto brushwork, rich pigments, classical composition, canvas texture",
        "ru": "живопись маслом, импасто, насыщенные пигменты, классическая композиция, фактура холста",
    },
    "3d": {
        "en": "octane 3d render, subsurface scattering, studio hdri, ultra clean, product design",
        "ru": "3D-рендер, подповерхностное рассеивание, студийный HDRI, идеально чисто, продуктовый дизайн",
    },
    "sketch": {
        "en": "graphite sketch, cross hatching, monochrome, architectural precision",
        "ru": "графитовый скетч, штриховка, монохром, архитектурная точность",
    },
    "watercolor": {
        "en": "watercolor illustration, soft washes, paper texture, delicate palette",
        "ru": "акварельная иллюстрация, мягкие заливки, фактура бумаги, деликатная палитра",
    },
    "isometric": {
        "en": "isometric illustration, clean vector shapes, soft shadows, tech infographic style",
        "ru": "изометрическая иллюстрация, чистые векторные формы, мягкие тени, стиль техно-инфографики",
    },
    "logo": {
        "en": "minimal vector logo, monoline geometry, flat design, centered, negative space, no text",
        "ru": "минималистичный векторный логотип, монолинейная геометрия, плоский дизайн, по центру, негативное пространство, без текста",
    },
}

_BLOCKED = re.compile(
    r"\b(nud|nude|naked|nsfw|porn|sex(?!ual orientation)|gore|dismember|"
    r"child\s*(abuse|porn)|csam|terrorist\s*(manual|attack)|real\s*gun\s*violence)\w*",
    re.I,
)


class ImageError(RuntimeError):
    pass


@dataclass(slots=True)
class GeneratedImage:
    file_id: str
    key: str
    width: int
    height: int
    size: int
    seed: int
    prompt: str
    style: str
    mime: str
    duration_ms: int


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _looks_like_image(data: bytes) -> bool:
    """Cheap magic-byte check so we never store an HTML/error page as an image."""
    if len(data) < 2048:
        return False
    return (
        data[:3] == b"\xff\xd8\xff"          # JPEG
        or data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")  # WebP
        or data[:6] in (b"GIF87a", b"GIF89a")  # GIF
    )


def build_prompt(prompt: str, style: str = "none", *, language: str = "ru", enhance: bool = True) -> str:
    """Compose the final prompt: user text + style modifiers + quality tail."""
    base = (prompt or "").strip()
    if not base:
        raise ImageError("empty prompt")
    if _BLOCKED.search(base):
        raise ImageError("prompt violates the content policy")
    modifier = STYLES.get(style, STYLES["none"]).get("en", "")
    tail = "high quality, coherent composition, no watermark, no text artifacts" if enhance else ""
    return ", ".join(part for part in (base, modifier, tail) if part)


def _engine_key(model: str) -> str:
    """Map a real model id back to its public catalogue name."""
    for name, spec in ENGINES.items():
        if spec["model"] == model:
            return name
    return DEFAULT_ENGINE


class ImageService:
    timeout = settings.image_timeout

    async def generate(
        self,
        prompt: str,
        *,
        width: int = 1024,
        height: int = 1024,
        style: str = "none",
        seed: int | None = None,
        language: str = "ru",
        negative: str = "",
        enhance: bool = True,
        model: str | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        width = _clamp(width, 256, MAX_SIDE)
        height = _clamp(height, 256, MAX_SIDE)
        full_prompt = build_prompt(prompt, style, language=language, enhance=enhance)
        if negative.strip():
            full_prompt = f"{full_prompt}, avoid: {negative.strip()[:200]}"
        seed = seed if seed is not None else int(hashlib.sha256(full_prompt.encode()).hexdigest()[:8], 16) % 2**31
        # Resolve the requested engine (public name or raw model id) and build a
        # fallback chain from the rest of the catalogue.
        requested = (model or "").strip()
        chain: list[str] = []
        if requested in ENGINES:
            chain.append(ENGINES[requested]["model"])
        elif requested:
            chain.append(requested)
        else:
            chain.append(settings.image_model)
        for name, spec in ENGINES.items():
            if spec["model"] not in chain:
                chain.append(spec["model"])
        if settings.image_fallback_model and settings.image_fallback_model not in chain:
            chain.append(settings.image_fallback_model)
        engines = chain
        seen: set[str] = set()
        last_error: Exception | None = None

        for engine in engines:
            if engine in seen:
                continue
            seen.add(engine)
            try:
                payload = await self._fetch(full_prompt, width, height, seed, engine)
                return payload, {
                    "prompt": full_prompt,
                    "style": style,
                    "width": width,
                    "height": height,
                    "seed": seed,
                    "engine": _engine_key(engine),
                }
            except Exception as exc:
                last_error = exc
                log.warning("generation attempt failed (%s)", type(exc).__name__)
        raise ImageError(f"generation failed: {last_error}")

    async def _fetch(self, prompt: str, width: int, height: int, seed: int, model: str) -> bytes:
        """Dispatch to the configured generation backend."""
        if model.startswith("horde:"):
            return await self._fetch_horde(prompt, width, height, seed, model.split(":", 1)[1])
        if model.startswith("@cf/") or settings.image_provider.lower() == "neural":
            try:
                return await self._fetch_neural(prompt, width, height, seed, model)
            except ImageError as exc:
                # primary backend saturated or down — fall back to the pool
                log.warning("neural backend unavailable (%s) — using pool", exc)
                return await self._fetch_horde(prompt, width, height, seed, "")
        return await self._fetch_open(prompt, width, height, seed, model)

    async def _fetch_horde(self, prompt: str, width: int, height: int, seed: int, model: str) -> bytes:
        """Open-model pool: asynchronous job, then download the finished render."""
        names = [model] if model else list(HORDE_POOL)
        side = max(256, min(1024, max(width, height)))
        side = min(side, 1024)
        deadline = time.monotonic() + max(45, min(self.timeout, 180))
        headers = {
            "Content-Type": "application/json", "Accept": "application/json",
            "apikey": settings.image_horde_key or "0000000000",
            "Client-Agent": "mestinghem:1.0:image",
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=15), follow_redirects=True) as client:
            job_id = ""
            for name in names + [""]:
                body = {
                    "prompt": prompt[:900],
                    "params": {"width": side, "height": side, "steps": 24, "n": 1, "cfg_scale": 6.5},
                    "models": [name] if name else [],
                    "r2": True,
                }
                try:
                    resp = await client.post(f"{HORDE_API}/generate/async", json=body, headers=headers)
                    if resp.status_code in (200, 202):
                        job_id = (resp.json() or {}).get("id") or ""
                        if job_id:
                            break
                except Exception:
                    continue
            if not job_id:
                raise ImageError("pool rejected the request")
            while time.monotonic() < deadline:
                await asyncio.sleep(4)
                check = await client.get(f"{HORDE_API}/generate/check/{job_id}", headers=headers)
                state = check.json() if check.status_code == 200 else {}
                if state.get("faulted"):
                    raise ImageError("pool job failed")
                if not state.get("done"):
                    continue
                status_resp = await client.get(f"{HORDE_API}/generate/status/{job_id}", headers=headers)
                generations = (status_resp.json() or {}).get("generations") or []
                if not generations:
                    raise ImageError("pool returned no image")
                image_resp = await client.get(generations[0]["img"], headers={"User-Agent": "mestinghem/1.0"})
                data = image_resp.content
                if not _looks_like_image(data):
                    raise ImageError("pool returned no usable image")
                return self._normalise(data, width, height)
        raise ImageError("pool timed out")

    async def _fetch_neural(self, prompt: str, width: int, height: int, seed: int, model: str) -> bytes:
        """Managed neural image backend — clean renders, no third-party marks."""
        account = settings.image_ai_account.strip()
        token = settings.image_ai_token.strip()
        if not account or not token:
            raise ImageError("image backend is not configured")
        url = AI_GATEWAY.format(account=account, model=model)
        body: dict[str, Any] = {"prompt": prompt[:2000]}
        if "flux" in model:
            body["steps"] = max(1, min(8, settings.image_steps))
        else:
            body["num_steps"] = max(1, min(20, settings.image_steps))
        read_timeout = max(20, min(self.timeout, 90))
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout, connect=15), follow_redirects=True
        ) as client:
            response = await client.post(
                url, json=body,
                headers={"Authorization": f"Bearer {token}", "Accept": "image/*,application/json"},
            )
            ctype = (response.headers.get("content-type") or "").lower()
            if response.status_code >= 400:
                raise ImageError(f"upstream {response.status_code}")
            if "application/json" in ctype:
                payload = response.json()
                if not payload.get("success", True):
                    raise ImageError("upstream rejected the request")
                encoded = (payload.get("result") or {}).get("image") or ""
                if not encoded:
                    raise ImageError("upstream returned no image")
                import base64 as _b64
                data = _b64.b64decode(encoded)
            else:
                data = response.content
            if not _looks_like_image(data):
                raise ImageError("upstream returned no usable image")
            return self._normalise(data, width, height)

    async def _fetch_open(self, prompt: str, width: int, height: int, seed: int, model: str) -> bytes:
        from urllib.parse import quote

        url = ENDPOINT.format(prompt=quote(prompt[:900], safe=""))
        params = {
            "width": width,
            "height": height,
            "seed": seed,
            "model": model,
            "nologo": "true",
            "referrer": settings.brand_short.lower().replace(" ", "-"),
            # The open endpoint's `safe` flag makes large requests hang; prompts are
            # already screened by our own content policy before they reach here.
        }
        read_timeout = max(20, min(self.timeout, 60))
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(read_timeout, connect=15), follow_redirects=True
        ) as client:
            response = await client.get(url, params=params, headers={"Accept": "image/*"})
            data = response.content
            # The open endpoint sometimes serves a genuine image alongside a
            # soft-quota status (402/429). Accept the bytes whenever they decode
            # as a real image; only hard-fail on other errors or non-image bodies.
            hard_error = response.status_code >= 400 and response.status_code not in (402, 429)
            if hard_error or not _looks_like_image(data):
                raise ImageError(f"upstream {response.status_code} returned no usable image")
            return self._normalise(data, width, height)

    @staticmethod
    def _normalise(data: bytes, width: int, height: int) -> bytes:
        """Re-encode to a known-good JPEG/PNG and verify dimensions."""
        try:
            image = Image.open(__import__("io").BytesIO(data))
            image.load()
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            if image.size != (width, height):
                image = image.resize((width, height), Image.LANCZOS)
            buffer = __import__("io").BytesIO()
            fmt = "PNG" if image.mode == "RGBA" else "JPEG"
            image.save(buffer, format=fmt, quality=92, optimize=True)
            return buffer.getvalue()
        except Exception:
            return data  # trust the upstream bytes if PIL is unhappy

    async def save(
        self,
        data: bytes,
        *,
        user_id: str,
        prompt: str,
        meta: dict[str, Any],
        filename: str | None = None,
    ) -> GeneratedImage:
        started = time.perf_counter()
        stamp = hashlib.sha1(data).hexdigest()[:10]
        name = filename or f"mestinghem-{stamp}.jpg"
        stored = await storage.put(data, name, namespace=f"images/{user_id[:12]}", mime="image/jpeg")
        return GeneratedImage(
            file_id="",
            key=stored.key,
            width=meta.get("width", 0),
            height=meta.get("height", 0),
            size=stored.size,
            seed=meta.get("seed", 0),
            prompt=prompt,
            style=meta.get("style", "none"),
            mime=stored.mime,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def engines(self, language: str = "ru") -> list[dict[str, Any]]:
        key = "ru" if language.lower().startswith("ru") else "en"
        out = []
        for name, spec in ENGINES.items():
            out.append({
                "id": name,
                "label": spec["label"][key],
                "note": spec["note"][key],
                "default": name == DEFAULT_ENGINE,
                "max_side": spec["max_side"],
            })
        return out

    def styles(self, language: str = "ru") -> list[dict[str, str]]:
        key = "ru" if language.lower().startswith("ru") else "en"
        return [
            {
                "id": name,
                "label": {
                    "none": {"ru": "Без стиля", "en": "No style"},
                    "photo": {"ru": "Фотореализм", "en": "Photorealism"},
                    "cinema": {"ru": "Кино", "en": "Cinematic"},
                    "luxury": {"ru": "Люкс / VIP", "en": "Luxury"},
                    "neon": {"ru": "Неон-нуар", "en": "Neon noir"},
                    "anime": {"ru": "Аниме", "en": "Anime"},
                    "oil": {"ru": "Масло", "en": "Oil painting"},
                    "3d": {"ru": "3D-рендер", "en": "3D render"},
                    "sketch": {"ru": "Скетч", "en": "Sketch"},
                    "watercolor": {"ru": "Акварель", "en": "Watercolor"},
                    "isometric": {"ru": "Изометрия", "en": "Isometric"},
                    "logo": {"ru": "Логотип", "en": "Logo"},
                }[name][key],
            }
            for name in STYLES
        ]


image_service = ImageService()
