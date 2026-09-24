"""Cost estimates per operation, in USD.

Pulled from VIDEO_MODELS / IMAGE_MODELS configs where possible; fixed defaults
for other operations. These are *estimates* applied at job-creation time —
actual provider invoices may vary slightly.
"""

from app.config import VIDEO_MODELS, IMAGE_MODELS


# Flat-rate operations
MUSIC_SUNO_USD = 0.118              # Suno route estimate per generated song
WHISPER_USD_PER_MIN = 0.006         # fal-ai/whisper word-level (preferred), or OpenRouter chat fallback
LLM_PLAN_FLAT_USD = 0.06            # Planning fallback; actual usage replaces this
LLM_EXPAND_FLAT_USD = 0.005         # Per-scene prompt expansion


def image_cost(model_key: str) -> tuple[float, str]:
    cfg = IMAGE_MODELS.get(model_key, {})
    price = cfg.get("price_per_image", 0.04)
    return price, f"{cfg.get('name', model_key)} · 1 image (estimate)"


def video_cost(
    model_key: str,
    duration_seconds: int,
    resolution: str = "720p",
    with_audio: bool = False,
) -> tuple[float, str]:
    cfg = VIDEO_MODELS.get(model_key, {})
    if cfg.get("requires_audio_input"):
        return video_cost_fal_frame_audio(model_key, duration_seconds, resolution)
    pricing = cfg.get("pricing") or {}
    res_pricing = pricing.get(resolution) or pricing.get("720p") or {}
    audio_key = "with_audio" if with_audio else "without_audio"
    rate = res_pricing.get(audio_key) or next(iter(res_pricing.values()), 0.05)
    total = round(rate * duration_seconds, 4)
    name = cfg.get("name", model_key)
    suffix = " +audio" if with_audio else ""
    return total, f"{name} · {duration_seconds}s × ${rate}/s @ {resolution}{suffix} (estimate)"


def video_cost_fal_seedance_r2v(
    model_key: str,
    duration_seconds: int,
    resolution: str = "720p",
) -> tuple[float, str]:
    """Cost for Seedance reference-to-video via fal (the audio-sync path).

    Distinct from `video_cost()` because the same model on OpenRouter (I2V)
    vs fal (R2V) bills very differently. The user opted into this path by
    setting `audio_sync_enabled=True` on the scene; estimate the fal route
    separately from the OpenRouter I2V route.
    """
    cfg = VIDEO_MODELS.get(model_key, {})
    table = cfg.get("audio_pricing") or {"720p": 0.30}
    rate = table.get(resolution) or next(iter(table.values()))
    total = round(rate * duration_seconds, 4)
    name = cfg.get("name", model_key)
    return total, f"{name} R2V (fal) · {duration_seconds}s × ${rate}/s @ {resolution} +audio (estimate)"


def video_cost_fal_frame_audio(
    model_key: str,
    duration_seconds: int,
    resolution: str = "720p",
) -> tuple[float, str]:
    """Cost for first-frame generation driven by a supplied song segment."""
    cfg = VIDEO_MODELS.get(model_key, {})
    table = cfg.get("audio_pricing") or {"720p": 0.10, "1080p": 0.15}
    rate = table.get(resolution) or next(iter(table.values()))
    total = round(rate * duration_seconds, 4)
    name = cfg.get("name", model_key)
    return total, f"{name} song sync (fal) · {duration_seconds}s × ${rate}/s @ {resolution} (estimate)"


# Retained for callers of the previously Wan-only rate helper.
video_cost_fal_wan_audio = video_cost_fal_frame_audio


def music_cost(source: str) -> tuple[float, str]:
    if source != "suno":
        raise ValueError(f"Unknown music-generation source: {source}")
    return MUSIC_SUNO_USD, "Suno V5.5 · 1 song"


def transcription_cost(duration_seconds: float) -> tuple[float, str]:
    minutes = duration_seconds / 60.0
    total = round(WHISPER_USD_PER_MIN * minutes, 4)
    return total, f"Whisper Large V3 Turbo · {minutes:.1f}min × ${WHISPER_USD_PER_MIN}/min"


def llm_plan_cost() -> tuple[float, str]:
    return LLM_PLAN_FLAT_USD, "Scene plan (estimate; actual cost depends on selected model)"


def llm_expand_cost() -> tuple[float, str]:
    return LLM_EXPAND_FLAT_USD, "Prompt expansion (estimate; actual cost depends on selected model)"


LLM_THEME_FLAT_USD = 0.02       # Theme analysis ~3K total tokens
LLM_CHAR_SUGGEST_FLAT_USD = 0.04  # Character suggestion, count-dependent estimate


def theme_analysis_cost() -> tuple[float, str]:
    return LLM_THEME_FLAT_USD, "Song theme analysis (estimate)"


def character_suggest_cost(count: int = 3) -> tuple[float, str]:
    cost = round(LLM_CHAR_SUGGEST_FLAT_USD + 0.01 * count, 4)
    return cost, f"{count} character suggestions (estimate)"
