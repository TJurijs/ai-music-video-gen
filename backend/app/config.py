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

MODEL_CATALOG_VERIFIED_AT = "2026-09-24"

# ---------------------------------------------------------------------------
# Video models — OpenRouter video models spanning price tiers.
# IDs and capabilities verified against GET /api/v1/videos/models on 2026-09-17.
# Sources and adapter limitations are recorded in docs/MODEL_CATALOG.md.
# Pricing comes from OpenRouter's pricing_skus and depends on resolution.
# The OpenRouter adapter sets `generate_audio=False` because the original
# song is muxed at assembly, so pricing.video_cost() uses without_audio.
# ---------------------------------------------------------------------------
VIDEO_MODELS = {
    "ltx-2.5-fast": {
        "name": "LTX 2.5 Fast",
        "provider": "fal",
        "model_id": "lightricks/ltx-2.5/audio-to-video/fast",
        "fal_audio_model_id": "lightricks/ltx-2.5/audio-to-video/fast",
        "audio_input_mode": "ltx_a2v",
        "requires_audio_input": True,
        "supports_audio_input": True,
        "tier": "mid",
        "tagline": "Song-driven scenes with first-frame continuity, up to 20 seconds",
        "durations": list(range(2, 21)),
        "audio_durations": list(range(2, 21)),
        # The fal A2V schema has no resolution selector: this endpoint is 1080p.
        "resolutions": ["1080p"],
        "audio_resolutions": ["1080p"],
        "aspects": ["16:9", "9:16"],
        "supports_first_frame": True,
        "supports_last_frame": False,
        "supports_reference_images": False,
        "pricing": {"1080p": {"with_audio": 0.13, "without_audio": 0.13}},
        "audio_pricing": {"1080p": 0.13},
        "max_duration": 20,
        "audio_reference_support": "app",
        "audio_note": "Always uses your scene's song segment through fal. Audio length sets video length; $0.13 per input-audio second at 1080p.",
        "reference_note": "Uses the scene still or previous clip's final frame as its first frame. Character identity comes from that image; separate portraits and target end frames are not accepted by this fal audio endpoint.",
        "note": "Audio is required. Supports landscape and portrait output. The app pads the final song segment with silence to preserve your chosen scene length.",
    },
    "wan-3.0": {
        "name": "Wan 3.0",
        # Explicitly restored by the user despite no retained generation history.
        "show_in_catalog": True,
        "model_id": "alibaba/wan-3.0",
        "supports_audio_input": True,
        "audio_input_mode": "wan_r2v",
        "fal_r2v_model_id": "alibaba/wan-3.0/reference-to-video",
        # The app sends the complete scene song slice; fal caps all reference
        # audio combined at 15s even though standard output can reach 30s.
        "audio_durations": list(range(2, 16)),
        "audio_resolutions": ["480p", "720p", "1080p"],
        "audio_aspects": ["16:9", "4:3", "1:1", "3:4", "9:16"],
        "audio_pricing": {"480p": 0.05, "720p": 0.10, "1080p": 0.20},
        "tier": "mid",
        "tagline": "Longer scenes up to 30 seconds, with 1080p output",
        "durations": list(range(2, 31)),
        "resolutions": ["480p", "720p", "1080p"],
        "aspects": ["16:9", "4:3", "1:1", "3:4", "9:16"],
        "supports_first_frame": True,
        "supports_last_frame": False,
        "supports_reference_images": True,
        "pricing": {
            "480p": {"with_audio": 0.05, "without_audio": 0.05},
            "720p": {"with_audio": 0.10, "without_audio": 0.10},
            "1080p": {"with_audio": 0.20, "without_audio": 0.20},
        },
        "max_duration": 30,
        "audio_reference_support": "app",
        "audio_note": "Audio reference uses your scene's song segment through fal, with scene and character images as references. Supports 2–15s scenes in this mode. Precise singing/lip sync has not been tested live. Standard generation uses OpenRouter and supports 2–30s.",
        "reference_note": "Choose a first frame or separate character references.",
        "note": "Released August 2026. Use when a scene needs more than 15 seconds. Song audio is added during assembly.",
    },
    "seedance-2.5": {
        "name": "Seedance 2.5",
        "model_id": "bytedance/seedance-2.5",
        "tier": "premium",
        "tagline": "Longer 4–30 second scenes with first-frame or character guidance",
        "durations": list(range(4, 31)),
        "resolutions": ["480p", "720p"],
        "aspects": ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        # $0.0000107/video token; estimated per second at 24 fps, 16:9.
        "pricing": {
            "480p": {"with_audio": 0.10280025, "without_audio": 0.10280025},
            "720p": {"with_audio": 0.23112, "without_audio": 0.23112},
        },
        "max_duration": 30,
        "audio_reference_support": "provider",
        "audio_note": "The provider accepts audio references, but this app has not connected that route for Seedance 2.5.",
        "reference_note": "Choose frame continuity or separate character references. Frame inputs take priority when both are present.",
        "note": "Released August 2026. Longer clips through OpenRouter; the audio-sync toggle remains limited to the existing verified fal routes.",
    },
    "seedance-2.0-mini": {
        "name": "Seedance 2.0 Mini",
        "model_id": "bytedance/seedance-2.0-mini",
        "tier": "cheap",
        "tagline": "Lower-cost 480p and 720p Seedance drafts",
        "durations": list(range(4, 16)),
        "resolutions": ["480p", "720p"],
        "aspects": ["1:1", "3:4", "9:16", "4:3", "16:9", "21:9", "9:21"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": True,
        # $0.0000035/video token; estimated per second at 24 fps, 16:9.
        "pricing": {
            "480p": {"with_audio": 0.03362625, "without_audio": 0.03362625},
            "720p": {"with_audio": 0.0756, "without_audio": 0.0756},
        },
        "max_duration": 15,
        "audio_reference_support": "provider",
        "audio_note": "The provider accepts audio references, but this app has not connected that route for Mini.",
        "reference_note": "Choose frame continuity or separate character references. Frame inputs take priority when both are present.",
        "note": "Released August 2026. A lower-cost draft route than Seedance 2.0 Fast, with up to 15-second clips.",
    },
    "hailuo-3-max": {
        "name": "MiniMax H3 Max",
        "model_id": "minimax/hailuo-3-max",
        "tier": "cheap",
        "tagline": "Silent 5–15 second scenes at 480p or 768p",
        "durations": list(range(5, 16)),
        "resolutions": ["480p", "768p"],
        "aspects": ["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
        "supports_first_frame": True,
        "supports_last_frame": True,
        "supports_reference_images": False,
        "pricing": {
            "480p": {"with_audio": 0.05, "without_audio": 0.05},
            "768p": {"with_audio": 0.08, "without_audio": 0.08},
        },
        "max_duration": 15,
        "audio_reference_support": "none",
        "audio_note": "Silent video; the original song is added during assembly.",
        "reference_note": "First-frame guidance in this app. Character identity comes from the scene still; provider end-frame targeting is not connected.",
        "note": "Released September 2026. This route uses 768p, not 720p, for its higher-resolution output.",
    },
    "runway-gen-4.5": {
        "name": "Runway Gen-4.5",
        "model_id": "runway/gen-4.5",
        "tier": "mid",
        "tagline": "Short cinematic shots with a first-frame anchor",
        "durations": list(range(2, 11)),
        "resolutions": ["720p"],
        "aspects": ["16:9", "9:16"],
        "supports_first_frame": True,
        "supports_last_frame": False,
        "supports_reference_images": False,
        "pricing": {
            "720p": {"with_audio": 0.12, "without_audio": 0.12},
        },
        "max_duration": 10,
        "audio_reference_support": "none",
        "audio_note": "Silent video; the original song is added during assembly.",
        "reference_note": "First-frame animation only. Last-frame targets and separate character references are unavailable.",
        "note": "Added to OpenRouter July 2026. Landscape or portrait clips from 2 to 10 seconds.",
    },
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
        "note": "Budget draft option. Use 480p × 4s for pipeline tests (~$0.05/clip). Portrait references remain subject to provider moderation.",
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
        # Resolution-specific token rates verified 2026-09-17; estimates use 16:9.
        # Formula: (height × width × duration × 24) / 1024 tokens.
        # 1080p now costs $0.0000077/token; 4K costs $0.000004/token.
        "pricing": {
            "480p":  {"with_audio": 0.067, "without_audio": 0.067},
            "720p":  {"with_audio": 0.151, "without_audio": 0.151},
            "1080p": {"with_audio": 0.37422, "without_audio": 0.37422},
            "4K":    {"with_audio": 0.7776, "without_audio": 0.7776},
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
        "tagline": "Lower-cost Seedance drafts up to 15 seconds",
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
        # Token-priced at $0.0000042/token (verified 2026-09-17).
        # Formula: (height × width × duration × 24) / 1024 tokens.
        "pricing": {
            "480p": {"with_audio": 0.0403515, "without_audio": 0.0403515},
            "720p": {"with_audio": 0.09072, "without_audio": 0.09072},
        },
        "max_duration": 15,
        "audio_reference_support": "app",
        "audio_note": "App can route through fal with the scene audio plus character references; exact frame anchors are skipped.",
        "face_guardrail": "high",
        "face_guardrail_note": "Strict with recognizable photoreal portrait references.",
        "reference_note": "Choose frame continuity or character references. OpenRouter gives frame images priority when both are sent.",
        "note": "Faster, cheaper Seedance 2.0 variant. Up to 720p / 15s. Use for iteration; choose full Seedance 2.0 for higher-resolution finals.",
    },
    "kling-v3.0-pro": {
        "name": "Kling 3.0 Pro",
        "model_id": "kwaivgi/kling-v3.0-pro",
        "tier": "mid",
        "tagline": "Flexible 3–15 second scenes with frame continuity",
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
        "note": "Flexible 3–15s scenes with exact first/last frame guidance. Character identity comes from the scene still; portrait inputs remain subject to provider moderation.",
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
        "tagline": "Lower-cost Kling scenes with frame continuity",
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
        "note": "Lower-cost Kling with exact first/last frame guidance. Character identity comes from the scene still; portrait inputs remain subject to provider moderation.",
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
        "audio_durations": list(range(2, 16)),
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
        "audio_note": "Song sync through fal supports 2–15 seconds: the scene song slice drives timing with an exact first frame. $0.10/s at 720p or $0.15/s at 1080p. The standard OpenRouter route supports 2–10 seconds.",
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


def video_uses_audio_input(model_cfg: dict, audio_sync_enabled: bool = False) -> bool:
    """Whether this selection uses a connected song-driven provider route."""
    return bool(
        (audio_sync_enabled or model_cfg.get("requires_audio_input"))
        and model_cfg.get("supports_audio_input")
        and (model_cfg.get("fal_audio_model_id") or model_cfg.get("fal_r2v_model_id"))
    )


def video_durations(model_cfg: dict, audio_sync_enabled: bool = False) -> list[int]:
    """Return duration limits for the actual route, not another provider's model."""
    if video_uses_audio_input(model_cfg, audio_sync_enabled):
        return model_cfg.get("audio_durations") or model_cfg.get("durations") or []
    return model_cfg.get("durations") or []


# Image models — used for scene reference images
IMAGE_MODELS = {
    "gemini-3.1-flash-lite-image": {
        "name": "Gemini 3.1 Flash Lite Image",
        "model_id": "google/gemini-3.1-flash-lite-image",
        "price_per_image": 0.04,
        "supports_reference_images": True,
        "note": "Fast 1K drafts with character references. Token-based cost is estimated and replaced by provider usage after completion.",
    },
    "gemini-3-pro-image": {
        "name": "Gemini 3 Pro Image",
        "model_id": "google/gemini-3-pro-image",
        # OpenRouter bills this model through image-output tokens.
        # Recent real requests cost ~$0.137-$0.138; use a conservative estimate
        # for preflight and replace it with response.usage.cost after completion.
        "price_per_image": 0.14,
        "supports_reference_images": True,
        "note": "Stable Pro route for detailed scene stills and character references. Higher cost than Flash.",
    },
    "gemini-3.1-flash-image": {
        "name": "Gemini 3.1 Flash Image",
        "model_id": "google/gemini-3.1-flash-image",
        "price_per_image": 0.08,
        "supports_reference_images": True,
        "note": "Stable Flash route for scene stills and character references. Token-based 1K estimate; actual cost depends on output size and references.",
    },
    "gemini-flash-image": {
        "name": "Gemini 2.5 Flash Image",
        "model_id": "google/gemini-2.5-flash-image",
        "price_per_image": 0.04,
        "supports_reference_images": True,
        "note": "Stable Gemini image model. Reliable for character consistency.",
    },
    "gpt-image-1": {
        "name": "GPT Image 1 (unavailable)",
        "model_id": "openai/gpt-image-1",
        "price_per_image": 0.04,
        "supports_reference_images": True,
        "available": False,
        "unavailable_reason": "GPT Image 1 requires the dedicated Images API, which is not connected in this app. Select Gemini 3.1 Flash Image or Gemini 3 Pro Image.",
        "note": "Kept for existing scene settings. Choose an available Gemini image model before generating.",
    },
    "seedream-4.5": {
        "name": "Seedream 4.5 (unavailable)",
        "model_id": "bytedance/seedream-4.5",
        "price_per_image": 0.04,
        "supports_reference_images": True,
        "available": False,
        "unavailable_reason": "The saved Seedream route is not a supported chat-image model. Its dedicated Images API route is not connected. Select Gemini 3.1 Flash Image or Gemini 3 Pro Image.",
        "note": "Kept for existing scene settings. Choose an available Gemini image model before generating.",
    },
}

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


# Lipsync models were removed when the audio-sync / post-hoc lipsync paths
# were retired. The pipeline produces purely visual scenes; the song is
# muxed in verbatim at assembly time.


# ---------------------------------------------------------------------------
# LLM models for scene planning and prompt expansion. Existing keys remain valid.
# ---------------------------------------------------------------------------
LLM_MODELS = {
    "gemini-3.8-flash": {
        "name": "Gemini 3.8 Flash",
        "model_id": "google/gemini-3.8-flash",
        "tier": "mid",
        "note": "Released September 2026. Current stable Flash option with image input and structured output for scene planning.",
    },
    "gemini-3.5-flash-lite": {
        "name": "Gemini 3.5 Flash Lite",
        "model_id": "google/gemini-3.5-flash-lite",
        "tier": "cheap",
        "note": "Released July 2026. Newer Flash Lite option for low-cost plans and prompt expansion.",
    },
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
