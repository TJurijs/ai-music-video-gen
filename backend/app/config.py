from pydantic_settings import BaseSettings, SettingsConfigDict
import os

# Resolve project root: .../musicvideo/  (one level up from backend/)
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_THIS_DIR)
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)


class Settings(BaseSettings):
    openrouter_api_key: str = ""
    suno_source: str = "sunoapi.org"
    suno_api_key: str = ""
    suno_api_base: str = "https://api.sunoapi.org"
    fal_api_key: str = ""
    storage_dir: str = os.path.join(_BACKEND_DIR, "storage")
    frontend_url: str = "http://localhost:3000"
    # Public base URL where the backend (and therefore /storage/*) is reachable
    # by the frontend / browser. Used to construct the absolute URLs we hand
    # back in API responses. Set via env var when deploying behind a proxy.
    public_base_url: str = "http://localhost:8010"
    default_video_model: str = "kling-v3.0-pro"
    default_image_model: str = "gemini-3.1-flash-image"
    default_llm_model: str = "google/gemini-3-flash-preview"

    # Look for .env in: project root first, then backend/  (project root wins)
    model_config = SettingsConfigDict(
        env_file=(
            os.path.join(_PROJECT_ROOT, ".env"),
            os.path.join(_BACKEND_DIR, ".env"),
        ),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


# ---------------------------------------------------------------------------
# Video models — three real OpenRouter video models spanning price tiers.
# IDs verified against GET /api/v1/videos/models on 2026-05-09.
# Pricing comes from OpenRouter's pricing_skus and depends on resolution +
# whether `generate_audio` is on; see pricing.video_cost().
# ---------------------------------------------------------------------------
VIDEO_MODELS = {
    "seedance-1.5-pro": {
        "name": "Seedance 1.5 Pro (Debug)",
        "model_id": "bytedance/seedance-1-5-pro",
        "tier": "debug",
        "tagline": "Pipeline testing — pennies per clip",
        "durations": list(range(4, 13)),  # 4 through 12
        "resolutions": ["480p", "720p", "1080p"],
        "aspects": ["1:1", "3:4", "9:16", "9:21", "4:3", "16:9", "21:9"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        "supports_audio_input": False,
        "generates_audio": True,
        # Token-priced at $0.0000012 (no audio) / $0.0000024 (with audio) per token.
        # Approximate per-second rates assuming ~24fps and typical token density.
        "pricing": {
            "480p":  {"with_audio": 0.010, "without_audio": 0.005},
            "720p":  {"with_audio": 0.024, "without_audio": 0.012},
            "1080p": {"with_audio": 0.050, "without_audio": 0.025},
        },
        "max_duration": 12,
        "note": "Cheapest option. Use 480p × 4s for end-to-end pipeline tests (~$0.02/clip). Same Seedance image-input filter applies — may refuse photoreal portrait refs.",
    },
    "veo-3.1-lite": {
        "name": "Veo 3.1 Lite",
        "model_id": "google/veo-3.1-lite",
        "tier": "cheap",
        "tagline": "Best for drafting and iteration",
        "durations": [4, 6, 8],
        "resolutions": ["720p", "1080p"],
        "aspects": ["16:9", "9:16"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        "supports_audio_input": False,
        "generates_audio": True,
        # Resolution × audio price matrix ($/s)
        "pricing": {
            "720p":  {"with_audio": 0.05, "without_audio": 0.03},
            "1080p": {"with_audio": 0.08, "without_audio": 0.05},
        },
        "max_duration": 8,
        "note": "Veo quality at draft pricing. Use to iterate before paying for the full model.",
    },
    "seedance-2.0": {
        "name": "Seedance 2.0",
        "model_id": "bytedance/seedance-2.0",
        "tier": "cheap",
        "tagline": "Strong character consistency, all 7 aspect ratios",
        "durations": list(range(4, 16)),  # 4 through 15
        "resolutions": ["480p", "720p", "1080p"],
        "aspects": ["1:1", "3:4", "9:16", "4:3", "16:9", "21:9", "9:21"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        # OpenRouter's variant is text/image-to-video; the audio-input variant
        # lives on fal (fal-ai/bytedance/seedance/v2/audio-to-video).
        "supports_audio_input": False,
        "generates_audio": True,
        # Token-priced; approximate per-second rates based on typical 24fps render.
        # (OpenRouter charges $0.000007 per video token; tokens scale with res×duration.)
        "pricing": {
            "480p":  {"with_audio": 0.025, "without_audio": 0.025},
            "720p":  {"with_audio": 0.05,  "without_audio": 0.05},
            "1080p": {"with_audio": 0.10,  "without_audio": 0.10},
        },
        "max_duration": 15,
        "note": "ByteDance's character-consistency specialist. Cheap, flexible, supports all 7 aspect ratios. NOTE: stricter image-input filter than Veo/Kling — refuses photoreal portraits via input_references. Use Veo/Kling for scenes that need photoreal character anchors.",
    },
    "kling-v3.0-pro": {
        "name": "Kling 3.0 Pro",
        "model_id": "kwaivgi/kling-v3.0-pro",
        "tier": "mid",
        "tagline": "Workhorse — flexible duration + permissive face filter",
        "durations": list(range(3, 16)),  # 3 through 15
        "resolutions": ["720p"],
        "aspects": ["16:9", "9:16", "1:1"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        "supports_audio_input": False,
        "generates_audio": True,
        "pricing": {
            "720p": {"with_audio": 0.168, "without_audio": 0.112},
        },
        "max_duration": 15,
        "note": "Most flexible duration (3–15s). Solid quality, weak face filter — accepts photoreal portraits without trouble. Good default when scenes have named characters.",
    },
    "veo-3.1": {
        "name": "Veo 3.1 (Premium)",
        "model_id": "google/veo-3.1",
        "tier": "premium",
        "tagline": "Hero shots — 4K available",
        "durations": [4, 6, 8],
        "resolutions": ["720p", "1080p", "4K"],
        "aspects": ["16:9", "9:16"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        "supports_audio_input": False,
        "generates_audio": True,
        "pricing": {
            "720p":  {"with_audio": 0.40, "without_audio": 0.20},
            "1080p": {"with_audio": 0.40, "without_audio": 0.20},
            "4K":    {"with_audio": 0.60, "without_audio": 0.40},
        },
        "max_duration": 8,
        "note": "Top-tier Google video model with 4K support. Save for hero shots. Accepts photoreal portraits when generation_service sets personGeneration='allow_adult' (we do).",
    },
    "kling-v3.0-std": {
        "name": "Kling 3.0 Standard",
        "model_id": "kwaivgi/kling-v3.0-std",
        "tier": "cheap",
        "tagline": "Same weak face filter as Pro at lower cost",
        "durations": list(range(3, 16)),  # 3 through 15
        "resolutions": ["720p"],
        "aspects": ["16:9", "9:16", "1:1"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        "supports_audio_input": False,
        "generates_audio": False,
        # Standard is roughly 2/3 the price of Pro for comparable behaviour.
        "pricing": {
            "720p": {"with_audio": 0.075, "without_audio": 0.075},
        },
        "max_duration": 15,
        "note": "Cheaper Kling — same lenient face filter as the Pro variant. Reliable identity anchor for photoreal portraits.",
    },
    "hailuo-2.3": {
        "name": "MiniMax Hailuo 2.3",
        "model_id": "minimax/hailuo-2.3",
        "tier": "mid",
        "tagline": "Built for character animation — accepts photoreal refs",
        "durations": [6, 10],
        "resolutions": ["720p", "1080p"],
        "aspects": ["16:9", "9:16", "1:1"],
        "supports_first_frame": True,
        "supports_last_frame": False,
        "supports_reference_images": True,
        "supports_audio_input": False,
        "generates_audio": False,
        "pricing": {
            "720p":  {"with_audio": 0.045, "without_audio": 0.045},
            "1080p": {"with_audio": 0.08,  "without_audio": 0.08},
        },
        "max_duration": 10,
        "note": "MiniMax's character-animation specialist. Reportedly the most permissive on photoreal character references — strong choice when Seedance refuses your portraits.",
    },
    "wan-2.6": {
        "name": "Alibaba Wan 2.6",
        "model_id": "alibaba/wan-2.6",
        "tier": "mid",
        "tagline": "15s clips + audio + lipsync built-in",
        "durations": [5, 10, 15],
        "resolutions": ["720p", "1080p"],
        "aspects": ["16:9", "9:16", "1:1"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        "supports_audio_input": True,  # native audio-to-video for lipsync
        "generates_audio": True,
        "pricing": {
            "720p":  {"with_audio": 0.08,  "without_audio": 0.05},
            "1080p": {"with_audio": 0.12,  "without_audio": 0.08},
        },
        "max_duration": 15,
        "note": "Alibaba Wan — moderate filter, accepts photoreal portraits. Native lipsync (no separate step). Longest single-clip duration in our config at 15s.",
    },
}

# Image models — used for scene reference images
IMAGE_MODELS = {
    "gemini-3-pro-image": {
        "name": "Gemini 3 Pro Image (Preview)",
        "model_id": "google/gemini-3-pro-image-preview",
        "price_per_image": 0.06,
        "supports_reference_images": True,
        "note": "Highest quality Gemini image model. Slower, more expensive.",
    },
    "gemini-3.1-flash-image": {
        "name": "Gemini 3.1 Flash Image (Preview)",
        "model_id": "google/gemini-3.1-flash-image-preview",
        "price_per_image": 0.04,
        "supports_reference_images": True,
        "note": "Latest fast Gemini image model. Best balance of quality and cost.",
    },
    "gemini-flash-image": {
        "name": "Gemini 2.5 Flash Image",
        "model_id": "google/gemini-2.5-flash-image",
        "price_per_image": 0.04,
        "supports_reference_images": True,
        "note": "Stable Gemini image model. Reliable for character consistency.",
    },
    "gpt-image-1": {
        "name": "GPT Image 1.5",
        "model_id": "openai/gpt-image-1",
        "price_per_image": 0.04,
        "supports_reference_images": True,
    },
    "seedream-4.5": {
        "name": "Seedream 4.5",
        "model_id": "bytedance/seedream-4.5",
        "price_per_image": 0.04,
        "supports_reference_images": True,
    },
}

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# Lipsync models — fal-hosted post-hoc lipsync. None of our video models
# accept audio input on OpenRouter, so this is the path to lyrics-matched
# mouth movement. Run after video generation.
# ---------------------------------------------------------------------------
LIPSYNC_MODELS = {
    "fal-latentsync": {
        "name": "LatentSync",
        "model_id": "fal-ai/latentsync",
        "tier": "cheap",
        "price_per_clip": 0.20,
        "note": "Default. Fast, decent quality. Best general-purpose choice.",
    },
    "fal-sync-lipsync": {
        "name": "Sync.so v1.6",
        "model_id": "fal-ai/sync-lipsync",
        "tier": "premium",
        "price_per_clip": 0.50,
        "note": "Highest quality lipsync. Slower and more expensive.",
    },
    "fal-musetalk": {
        "name": "MuseTalk",
        "model_id": "fal-ai/musetalk",
        "tier": "mid",
        "price_per_clip": 0.30,
        "note": "Real-time lipsync. Good for talking-head shots.",
    },
    "fal-wav2lip": {
        "name": "Wav2Lip",
        "model_id": "fal-ai/wav2lip",
        "tier": "debug",
        "price_per_clip": 0.05,
        "note": "Cheapest. Older model, lower quality, good for quick tests.",
    },
}


# ---------------------------------------------------------------------------
# LLM models for Auto-Plan and AI Expand. Three options span cost and quality.
# ---------------------------------------------------------------------------
LLM_MODELS = {
    "gemini-3.1-flash-lite": {
        "name": "Gemini 3.1 Flash Lite",
        "model_id": "google/gemini-3.1-flash-lite",
        "tier": "cheap",
        "note": "Cheapest. Use for fast iteration on scene plans.",
    },
    "gemini-3-flash-preview": {
        "name": "Gemini 3 Flash (Preview)",
        "model_id": "google/gemini-3-flash-preview",
        "tier": "mid",
        "note": "Default. Balanced quality and cost.",
    },
    "gemini-3.1-pro-preview": {
        "name": "Gemini 3.1 Pro (Preview)",
        "model_id": "google/gemini-3.1-pro-preview",
        "tier": "premium",
        "note": "Highest-quality reasoning. Use when narrative coherence matters.",
    },
}
