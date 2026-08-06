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
# Video models — OpenRouter video models spanning price tiers.
# IDs verified against GET /api/v1/videos/models on 2026-05-09.
# Pricing comes from OpenRouter's pricing_skus and depends on resolution.
# We never set `generate_audio` (the song's audio is muxed verbatim at
# assembly), so the `with_audio=False` branch of pricing.video_cost()
# is always used.
# ---------------------------------------------------------------------------
VIDEO_MODELS = {
    "seedance-1.5-pro": {
        "name": "Seedance 1.5 Pro (Debug)",
        "model_id": "bytedance/seedance-1-5-pro",
        # No fal R2V endpoint exists for Seedance 1.5 — fal only publishes
        # the 2.0 family (plus its fast variant). The OpenRouter I2V path
        # for 1.5 still works as a draft model.
        "tier": "debug",
        "tagline": "Pipeline testing — pennies per clip",
        "durations": list(range(4, 13)),  # 4 through 12
        "resolutions": ["480p", "720p", "1080p"],
        "aspects": ["1:1", "3:4", "9:16", "9:21", "4:3", "16:9", "21:9"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        # Token-priced at $0.0000024/token (audio included in token count).
        # Formula: (height × width × duration × 24) / 1024 tokens.
        # 480p(854×480)=9607.5 t/s, 720p(1280×720)=21600 t/s, 1080p(1920×1080)=48600 t/s.
        "pricing": {
            "480p":  {"with_audio": 0.023, "without_audio": 0.012},
            "720p":  {"with_audio": 0.052, "without_audio": 0.026},
            "1080p": {"with_audio": 0.117, "without_audio": 0.058},
        },
        "max_duration": 12,
        "audio_reference_support": "none",
        "audio_note": "Generates synchronized dialogue/audio, but cannot follow an uploaded song segment.",
        "face_guardrail": "high",
        "face_guardrail_note": "Photoreal portrait inputs are frequently rejected in this app.",
        "reference_note": "Character refs are supported, but frame mode and character-reference mode cannot be combined.",
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
        # Veo enforces a hard choice: `first_frame` OR `input_references`,
        # not both. Our pipeline always sends `first_frame` (the scene's
        # planned still or chained last frame), so refs get silently
        # dropped. Identity must come from `first_frame` alone.
        "supports_reference_images": False,
        # Resolution × audio price matrix ($/s)
        "pricing": {
            "720p":  {"with_audio": 0.05, "without_audio": 0.03},
            "1080p": {"with_audio": 0.08, "without_audio": 0.05},
        },
        "max_duration": 8,
        "audio_reference_support": "none",
        "audio_note": "Can generate native audio, but does not accept uploaded reference audio.",
        "face_guardrail": "high",
        "face_guardrail_note": "Explicit adult-only person generation; children and some recognizable people are restricted.",
        "reference_note": "Stable route uses exact frame conditioning; separate character references are unavailable.",
        "note": "Veo quality at draft pricing. Use to iterate before paying for the full model.",
    },
    "seedance-2.0": {
        "name": "Seedance 2.0",
        "model_id": "bytedance/seedance-2.0",
        # fal app slug — note the path uses `/fast/` and `/reference-to-video`
        # as SEPARATE segments after the app id. Don't collapse to
        # `seedance-2.0-fast/...` (that 404s with "Application not found").
        "fal_r2v_model_id": "bytedance/seedance-2.0/reference-to-video",
        # When True, the user can flip audio_sync_enabled on this scene to
        # route video gen through fal's reference-to-video endpoint (with
        # the scene's audio slice + character refs, no first_frame).
        "supports_audio_input": True,
        "tier": "cheap",
        "tagline": "Strong character consistency, all 7 aspect ratios",
        "durations": list(range(4, 16)),  # 4 through 15
        "resolutions": ["480p", "720p", "1080p", "4K"],
        # The fal reference-audio route is a different SKU from the normal
        # OpenRouter route and does not expose every OpenRouter resolution.
        "audio_resolutions": ["480p", "720p", "1080p"],
        "audio_pricing": {
            "480p": 0.18,
            "720p": 0.30,
            "1080p": 0.62,
        },
        "aspects": ["1:1", "3:4", "9:16", "4:3", "16:9", "21:9", "9:21"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        # Token-priced at $0.000007/token (audio included in token count).
        # Formula: (height × width × duration × 24) / 1024 tokens.
        # 480p(854×480)=9607.5 t/s → $0.067, 720p(1280×720)=21600 → $0.151, 1080p(1920×1080)=48600 → $0.340.
        "pricing": {
            "480p":  {"with_audio": 0.067, "without_audio": 0.067},
            "720p":  {"with_audio": 0.151, "without_audio": 0.151},
            "1080p": {"with_audio": 0.340, "without_audio": 0.340},
            "4K":    {"with_audio": 1.361, "without_audio": 1.361},
        },
        "max_duration": 15,
        "audio_reference_support": "app",
        "audio_note": "App can route through fal with the scene audio plus character references; exact frame anchors are skipped.",
        "face_guardrail": "high",
        "face_guardrail_note": "Strict with recognizable photoreal portrait references.",
        "reference_note": "Choose frame continuity or character references. OpenRouter gives frame images priority when both are sent.",
        "note": "ByteDance's character-consistency specialist. NOTE: stricter image-input filter than Veo/Kling — refuses photoreal portraits via input_references. Use Veo/Kling for scenes that need photoreal character anchors.",
    },
    "seedance-2.0-fast": {
        "name": "Seedance 2.0 Fast",
        "model_id": "bytedance/seedance-2.0-fast",
        # fal slug: the model lives under the 2.0 app namespace with /fast/
        # as a sub-segment, NOT as `seedance-2.0-fast` (that 404s).
        "fal_r2v_model_id": "bytedance/seedance-2.0/fast/reference-to-video",
        "supports_audio_input": True,
        "tier": "cheap",
        "tagline": "Same look as Seedance 2.0, half the price — for drafting",
        "durations": list(range(4, 16)),  # 4 through 15
        "resolutions": ["480p", "720p"],
        "audio_resolutions": ["480p", "720p"],
        "audio_pricing": {
            "480p": 0.09,
            "720p": 0.15,
        },
        "aspects": ["1:1", "3:4", "9:16", "4:3", "16:9", "21:9", "9:21"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        # Token-priced at ~$0.0000056/token (audio included).
        # Formula: (height × width × duration × 24) / 1024 tokens.
        # 480p=9607.5 t/s → $0.054, 720p=21600 t/s → $0.121.
        "pricing": {
            "480p": {"with_audio": 0.054, "without_audio": 0.054},
            "720p": {"with_audio": 0.121, "without_audio": 0.121},
        },
        "max_duration": 15,
        "audio_reference_support": "app",
        "audio_note": "App can route through fal with the scene audio plus character references; exact frame anchors are skipped.",
        "face_guardrail": "high",
        "face_guardrail_note": "Strict with recognizable photoreal portrait references.",
        "reference_note": "Choose frame continuity or character references. OpenRouter gives frame images priority when both are sent.",
        "note": "Faster, cheaper Seedance 2.0 variant. Capped at 720p / 10s. Use for iteration; promote to full Seedance 2.0 for finals.",
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
        # Kling has a "Bind Subject" feature in its native UI, but OpenRouter
        # does NOT forward `input_references` to Kling — they're silently
        # dropped. So on the OpenRouter route, character identity must come
        # ENTIRELY from `first_frame`. If face isn't visible there, Kling
        # improvises.
        "supports_reference_images": False,
        "pricing": {
            "720p": {"with_audio": 0.168, "without_audio": 0.112},
        },
        "max_duration": 15,
        "audio_reference_support": "none",
        "audio_note": "Can generate native audio, but cannot follow an uploaded song segment through OpenRouter.",
        "face_guardrail": "low",
        "face_guardrail_note": "Comparatively permissive with adult photoreal faces in this app; normal safety policy still applies.",
        "reference_note": "OpenRouter supports exact first/last frames but does not forward Kling's separate subject binding.",
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
        # Same Veo constraint as the Lite variant: `first_frame` xor
        # `input_references`. We always send first_frame so refs get
        # dropped on this route. Identity = first_frame alone.
        "supports_reference_images": False,
        "pricing": {
            "720p":  {"with_audio": 0.40, "without_audio": 0.20},
            "1080p": {"with_audio": 0.40, "without_audio": 0.20},
            "4K":    {"with_audio": 0.60, "without_audio": 0.40},
        },
        "max_duration": 8,
        "audio_reference_support": "none",
        "audio_note": "Can generate native audio, but does not accept uploaded reference audio.",
        "face_guardrail": "high",
        "face_guardrail_note": "Explicit adult-only person generation; children and some recognizable people are restricted.",
        "reference_note": "Stable route uses exact frame conditioning; separate character references are unavailable.",
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
        # Same as Kling Pro: OpenRouter doesn't forward `input_references`
        # to the Kling API. Identity comes from `first_frame` only.
        "supports_reference_images": False,
        # OpenRouter verified 2026-06-19: $0.084/s without audio, $0.126/s with audio.
        "pricing": {
            "720p": {"with_audio": 0.126, "without_audio": 0.084},
        },
        "max_duration": 15,
        "audio_reference_support": "none",
        "audio_note": "Can generate native audio, but cannot follow an uploaded song segment through OpenRouter.",
        "face_guardrail": "low",
        "face_guardrail_note": "Comparatively permissive with adult photoreal faces in this app; normal safety policy still applies.",
        "reference_note": "OpenRouter supports exact first/last frames but does not forward Kling's separate subject binding.",
        "note": "Cheaper Kling — same lenient face filter as the Pro variant. Reliable identity anchor for photoreal portraits.",
    },
    "grok-imagine-video-1.5": {
        "name": "Grok Imagine Video 1.5",
        "model_id": "x-ai/grok-imagine-video-1.5",
        "tier": "mid",
        "tagline": "Flexible 1-15s image animation with synchronized sound",
        "durations": list(range(1, 16)),
        "resolutions": ["480p", "720p", "1080p"],
        "aspects": ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"],
        "supports_first_frame": True,
        "supports_last_frame": False,
        "supports_reference_images": False,
        "pricing": {
            "480p":  {"with_audio": 0.080, "without_audio": 0.080},
            "720p":  {"with_audio": 0.140, "without_audio": 0.140},
            "1080p": {"with_audio": 0.250, "without_audio": 0.250},
        },
        "max_duration": 15,
        "audio_reference_support": "none",
        "audio_note": "Can create synchronized sound, but 1.5 does not accept uploaded reference audio.",
        "face_guardrail": "medium",
        "face_guardrail_note": "Realistic people are supported with active safety moderation and mandatory provenance watermarking.",
        "reference_note": "First-frame animation only; Grok 1.5 does not support separate reference-to-video character images.",
        "note": "Useful when a single scene still is already a strong identity anchor. Each input image adds about $0.01.",
    },
    "wan-2.7": {
        "name": "Wan 2.7",
        "model_id": "alibaba/wan-2.7",
        # fal's Wan I2V endpoint accepts an exact first frame plus a driving
        # audio_url. This is the app's audio-sync route for Wan; ordinary
        # frame/character generation still uses OpenRouter.
        "fal_audio_model_id": "fal-ai/wan/v2.7/image-to-video",
        "audio_input_mode": "wan_i2v",
        "supports_audio_input": True,
        "tier": "mid",
        "tagline": "Multimodal all-rounder with character and audio guidance",
        "durations": list(range(2, 11)),
        "resolutions": ["720p", "1080p"],
        "audio_resolutions": ["720p", "1080p"],
        "audio_pricing": {
            "720p": 0.10,
            "1080p": 0.15,
        },
        "aspects": ["16:9", "9:16", "1:1", "4:3", "3:4"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        "pricing": {
            "720p":  {"with_audio": 0.100, "without_audio": 0.100},
            "1080p": {"with_audio": 0.100, "without_audio": 0.100},
        },
        "max_duration": 10,
        "audio_reference_support": "app",
        "audio_note": "Works in app through fal: the scene song slice drives timing while the scene still or chained frame anchors the visuals. Audio-route rate: about $6/min at 720p or $9/min at 1080p.",
        "face_guardrail": "medium",
        "face_guardrail_note": "Realistic people are supported; identity/deepfake moderation should still be expected.",
        "reference_note": "Supports multiple character references or exact first/last frames, but OpenRouter treats them as mutually exclusive modes.",
        "note": "Audio mode uses fal Wan I2V. Character-reference mode remains a separate OpenRouter route and cannot be combined with driving audio.",
    },
    "veo-3.1-fast": {
        "name": "Veo 3.1 Fast",
        "model_id": "google/veo-3.1-fast",
        "tier": "mid",
        "tagline": "Veo quality at a practical production price",
        "durations": [4, 6, 8],
        "resolutions": ["720p", "1080p", "4K"],
        "aspects": ["16:9", "9:16"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": False,
        "pricing": {
            "720p":  {"with_audio": 0.100, "without_audio": 0.080},
            "1080p": {"with_audio": 0.120, "without_audio": 0.100},
            "4K":    {"with_audio": 0.300, "without_audio": 0.250},
        },
        "max_duration": 8,
        "audio_reference_support": "none",
        "audio_note": "Can generate native audio, but does not accept uploaded reference audio.",
        "face_guardrail": "high",
        "face_guardrail_note": "Explicit adult-only person generation; children and some recognizable people are restricted.",
        "reference_note": "Supports exact first/last frames; separate character-reference mode is unavailable on the stable route.",
        "note": "Strong default for polished shots when Veo Lite is not enough and full Veo is too expensive.",
    },
    "hailuo-2.3": {
        "name": "Hailuo 2.3",
        "model_id": "minimax/hailuo-2.3",
        "tier": "cheap",
        "tagline": "Affordable 1080p motion and facial expression specialist",
        "durations": [6, 10],
        "resolutions": ["1080p"],
        "aspects": ["16:9"],
        "supports_first_frame": True,
        "supports_last_frame": False,
        "supports_reference_images": False,
        "pricing": {
            "1080p": {"with_audio": 0.0817, "without_audio": 0.0817},
        },
        "max_duration": 10,
        "audio_reference_support": "none",
        "audio_note": "This OpenRouter model produces silent video and accepts no reference audio.",
        "face_guardrail": "medium",
        "face_guardrail_note": "People are supported, but MiniMax does not publish a detailed real-face acceptance matrix.",
        "reference_note": "OpenRouter exposes first-frame animation only for this model; no last-frame or separate character refs.",
        "note": "OpenRouter advertises 6s and 10s at 1080p; validate 10s before a large batch.",
    },
}

# Image models — used for scene reference images
IMAGE_MODELS = {
    "gemini-3-pro-image": {
        "name": "Gemini 3 Pro Image (Preview)",
        "model_id": "google/gemini-3-pro-image-preview",
        # OpenRouter currently bills this preview through image-output tokens.
        # Recent real requests cost ~$0.137-$0.138; use a conservative estimate
        # for preflight and replace it with response.usage.cost after completion.
        "price_per_image": 0.14,
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


# Lipsync models were removed when the audio-sync / post-hoc lipsync paths
# were retired. The pipeline produces purely visual scenes; the song is
# muxed in verbatim at assembly time.


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
