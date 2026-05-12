"""Cost estimates per operation, in USD.

Pulled from VIDEO_MODELS / IMAGE_MODELS configs where possible; fixed defaults
for other operations. These are *estimates* applied at job-creation time —
actual provider invoices may vary slightly.
"""

from app.config import VIDEO_MODELS, IMAGE_MODELS


# Flat-rate operations
LIPSYNC_PRICE_USD = 0.20            # fal LatentSync per clip
MUSIC_LYRIA_USD = 0.08              # Lyria 3 Pro per song
MUSIC_SUNO_USD = 0.118              # Suno V4 per clip
WHISPER_USD_PER_MIN = 0.006         # fal-ai/whisper word-level (preferred), or OpenRouter chat fallback
LLM_PLAN_FLAT_USD = 0.06            # Claude scene plan, ~5K in + 3K out
LLM_EXPAND_FLAT_USD = 0.005         # Per-scene prompt expansion


def image_cost(model_key: str) -> tuple[float, str]:
    cfg = IMAGE_MODELS.get(model_key, {})
    price = cfg.get("price_per_image", 0.04)
    return price, f"{cfg.get('name', model_key)} · 1 image"


def video_cost(
    model_key: str,
    duration_seconds: int,
    resolution: str = "720p",
    with_audio: bool = False,
) -> tuple[float, str]:
    cfg = VIDEO_MODELS.get(model_key, {})
    pricing = cfg.get("pricing") or {}
    res_pricing = pricing.get(resolution) or pricing.get("720p") or {}
    audio_key = "with_audio" if with_audio else "without_audio"
    rate = res_pricing.get(audio_key) or next(iter(res_pricing.values()), 0.05)
    total = round(rate * duration_seconds, 4)
    name = cfg.get("name", model_key)
    suffix = " +audio" if with_audio else ""
    return total, f"{name} · {duration_seconds}s × ${rate}/s @ {resolution}{suffix}"


def lipsync_cost(model_key: str = "fal-latentsync") -> tuple[float, str]:
    from app.config import LIPSYNC_MODELS
    cfg = LIPSYNC_MODELS.get(model_key, {})
    price = cfg.get("price_per_clip", LIPSYNC_PRICE_USD)
    name = cfg.get("name", model_key)
    return price, f"{name} · per clip"


def music_cost(source: str) -> tuple[float, str]:
    if source == "suno":
        return MUSIC_SUNO_USD, "Suno V4 · 1 song"
    return MUSIC_LYRIA_USD, "Lyria 3 Pro · 1 song"


def transcription_cost(duration_seconds: float) -> tuple[float, str]:
    minutes = duration_seconds / 60.0
    total = round(WHISPER_USD_PER_MIN * minutes, 4)
    return total, f"Whisper Large V3 Turbo · {minutes:.1f}min × ${WHISPER_USD_PER_MIN}/min"


def llm_plan_cost() -> tuple[float, str]:
    return LLM_PLAN_FLAT_USD, "Claude Sonnet · scene plan (estimate)"


def llm_expand_cost() -> tuple[float, str]:
    return LLM_EXPAND_FLAT_USD, "Claude Sonnet · prompt expansion (estimate)"


LLM_THEME_FLAT_USD = 0.02       # Theme analysis ~3K total tokens
LLM_CHAR_SUGGEST_FLAT_USD = 0.04  # Character suggestion, count-dependent estimate


def theme_analysis_cost() -> tuple[float, str]:
    return LLM_THEME_FLAT_USD, "Claude Sonnet · song theme analysis"


def character_suggest_cost(count: int = 3) -> tuple[float, str]:
    cost = round(LLM_CHAR_SUGGEST_FLAT_USD + 0.01 * count, 4)
    return cost, f"Claude Sonnet · {count} character suggestions"
