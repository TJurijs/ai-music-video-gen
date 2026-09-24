"""Validate app routes against a captured, read-only upstream catalog.

These tests are offline: they never submit a paid generation request.
Refresh the fixture only after checking the documented adapter contract.
"""

import json
from pathlib import Path

import pytest

from app.config import IMAGE_MODELS, LLM_MODELS, VIDEO_MODELS
from app.services import pricing
from app.services.generation_service import _closest_supported


CATALOG = json.loads(
    (Path(__file__).parent / "fixtures/model_catalog_2026_09_17.json").read_text()
)


@pytest.mark.parametrize(
    "config",
    [cfg for cfg in VIDEO_MODELS.values() if cfg.get("provider", "openrouter") == "openrouter"],
    ids=[key for key, cfg in VIDEO_MODELS.items() if cfg.get("provider", "openrouter") == "openrouter"],
)
def test_video_options_are_supported_by_provider_catalog(config):
    provider = CATALOG["video"][config["model_id"]]
    assert set(config["durations"]) <= set(provider["supported_durations"])
    assert set(config["resolutions"]) <= set(provider["supported_resolutions"])
    assert set(config["aspects"]) <= set(provider["supported_aspect_ratios"])
    assert config["max_duration"] == max(config["durations"])
    for frame in ("first_frame", "last_frame"):
        if config[f"supports_{frame}"]:
            assert frame in provider["supported_frame_images"]
    assert set(config["pricing"]) == set(config["resolutions"])


@pytest.mark.parametrize("config", IMAGE_MODELS.values(), ids=IMAGE_MODELS.keys())
def test_enabled_image_models_support_the_chat_image_adapter(config):
    if config.get("available") is False:
        assert config.get("unavailable_reason")
        return
    provider = CATALOG["chat"][config["model_id"]]
    assert "image" in provider["architecture"]["output_modalities"]
    if config.get("supports_reference_images"):
        assert "image" in provider["architecture"]["input_modalities"]


@pytest.mark.parametrize(
    "model",
    ["gemini-3-pro-image", "gemini-3.1-flash-image", "gemini-3.1-flash-lite-image"],
)
def test_stable_gemini_images_advertise_standard_project_aspects(model):
    provider = CATALOG["image"][IMAGE_MODELS[model]["model_id"]]
    assert "image" in provider["architecture"]["output_modalities"]
    assert {"16:9", "9:16", "1:1"} <= set(
        provider["supported_parameters"]["aspect_ratio"]["values"]
    )
    assert provider["supported_parameters"]["input_references"]["max"] == 14


@pytest.mark.parametrize("config", LLM_MODELS.values(), ids=LLM_MODELS.keys())
def test_planning_models_support_json_and_visual_continuation(config):
    provider = CATALOG["chat"][config["model_id"]]
    assert "response_format" in provider["supported_parameters"]
    assert "image" in provider["architecture"]["input_modalities"]
    assert "text" in provider["architecture"]["output_modalities"]


@pytest.mark.parametrize("model", ["wan-3.0", "seedance-2.5"])
def test_new_long_scene_models_preserve_a_28_second_scene(model):
    assert _closest_supported(28, VIDEO_MODELS[model]["durations"]) == 28


@pytest.mark.parametrize(
    ("model", "resolution", "expected"),
    [
        ("seedance-2.0-fast", "720p", 0.4536),
        ("seedance-2.0", "1080p", 1.8711),
        ("seedance-2.0", "4K", 3.888),
        ("seedance-2.0-mini", "720p", 0.378),
        ("seedance-2.5", "720p", 1.1556),
        ("wan-3.0", "1080p", 1.0),
        ("hailuo-3-max", "768p", 0.4),
        ("runway-gen-4.5", "720p", 0.6),
    ],
)
def test_five_second_cost_uses_current_sku(model, resolution, expected):
    cost, _ = pricing.video_cost(model, 5, resolution)
    assert cost == pytest.approx(expected)


def test_audio_sync_is_only_advertised_for_connected_routes():
    for config in VIDEO_MODELS.values():
        if config.get("supports_audio_input"):
            assert config.get("fal_audio_model_id") or config.get("fal_r2v_model_id")
            assert config["audio_reference_support"] == "app"
            assert config["audio_pricing"]
        elif config.get("audio_reference_support") == "provider":
            assert not config.get("fal_audio_model_id")
            assert not config.get("fal_r2v_model_id")


def test_legacy_incompatible_image_routes_are_not_silently_replaced():
    assert IMAGE_MODELS["gpt-image-1"]["available"] is False
    assert IMAGE_MODELS["seedream-4.5"]["available"] is False
    assert IMAGE_MODELS["gpt-image-1"]["model_id"] == "openai/gpt-image-1"
    assert IMAGE_MODELS["seedream-4.5"]["model_id"] == "bytedance/seedream-4.5"
