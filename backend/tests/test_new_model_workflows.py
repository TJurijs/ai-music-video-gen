"""Offline integration checks from scene preflight to the submitted JSON.

Only HTTP transport, polling and local media decoding are replaced. The real
scene routing, reference selection, pricing, request builder and job/asset
persistence run together; no paid request leaves these tests.
"""

import base64
import json
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

from app.config import settings
from app.models import Character, GenerationJob, Project, Scene, SceneAsset
from app.services import generation_service, openrouter


NEW_MODELS = [
    ("wan-3.0", "alibaba/wan-3.0", "1080p", 1.0),
    ("seedance-2.5", "bytedance/seedance-2.5", "720p", 1.1556),
    ("seedance-2.0-mini", "bytedance/seedance-2.0-mini", "720p", 0.378),
    ("hailuo-3-max", "minimax/hailuo-3-max", "768p", 0.4),
    ("runway-gen-4.5", "runway/gen-4.5", "720p", 0.6),
]


@pytest.fixture
def submitted_payloads(monkeypatch, tmp_path):
    """Capture the real adapter's only permitted request: video submission."""
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "openrouter_api_key", "offline-test-key")
    monkeypatch.setattr(settings, "fal_api_key", "")
    payloads = []

    def transport(request):
        assert request.method == "POST"
        assert request.url.host == "openrouter.ai"
        assert request.url.path == "/api/v1/videos"
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "offline-render"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        openrouter.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(transport), **kwargs),
    )

    async def poll(job_id, **kwargs):
        assert job_id == "offline-render"
        return "https://provider.invalid/offline-video.mp4"

    async def download(url, destination):
        assert url == "https://provider.invalid/offline-video.mp4"
        Path(destination).write_bytes(b"offline-video")

    async def prepare(scene, destination):
        assert Path(destination).read_bytes() == b"offline-video"

    async def unexpected_image(*args, **kwargs):
        pytest.fail("An existing frame or character-only render must not generate a new image")

    monkeypatch.setattr(openrouter, "poll_video_job", poll)
    monkeypatch.setattr(openrouter, "download_file", download)
    monkeypatch.setattr(generation_service, "_prepare_generated_video", prepare)
    monkeypatch.setattr(generation_service, "_generate_image", unexpected_image)
    return payloads


def _scene_with_portraits(db, tmp_path, model, resolution, *, chained=False, mode="frame"):
    project = Project(name="New model workflow", aspect_ratio="16:9")
    db.add(project)
    db.commit()
    db.refresh(project)
    still = tmp_path / "scene.jpg"
    still.write_bytes(b"planned-scene-first-frame")
    portrait = tmp_path / "alex.jpg"
    portrait.write_bytes(b"alex-portrait")
    unused = tmp_path / "other.jpg"
    unused.write_bytes(b"unmentioned-portrait")
    db.add_all([
        Character(project_id=project.id, name="Alex", description="Lead performer", reference_image_path=str(portrait)),
        Character(project_id=project.id, name="Morgan", description="Off-camera performer", reference_image_path=str(unused)),
    ])
    if chained:
        previous_frame = tmp_path / "previous-last.jpg"
        previous_frame.write_bytes(b"previous-video-final-pixels")
        db.add(Scene(
            project_id=project.id, order=1, audio_start=0, audio_end=5,
            status="done", extracted_last_frame_path=str(previous_frame),
        ))
    scene = Scene(
        project_id=project.id, order=2 if chained else 1,
        audio_start=5 if chained else 0, audio_end=10 if chained else 5,
        video_model=model, resolution=resolution,
        description="Alex walks toward the camera",
        video_prompt="Alex walks toward the camera",
        image_prompt="Alex in a wide shot",
        reference_image_path=str(still),
        video_reference_mode=mode,
        chain_from_prev=chained,
    )
    db.add(scene)
    db.commit()
    db.refresh(scene)
    return scene


def _image_bytes(part):
    return base64.b64decode(part["image_url"]["url"].split(",", 1)[1])


@pytest.mark.parametrize("model,provider_id,resolution,expected_cost", NEW_MODELS)
@pytest.mark.parametrize("chained", [False, True], ids=["scene-still", "previous-final-frame"])
@pytest.mark.asyncio
async def test_new_models_submit_selected_frame_and_match_preflight_pricing(
    test_engine, tmp_path, submitted_payloads, model, provider_id,
    resolution, expected_cost, chained,
):
    with Session(test_engine) as db:
        scene = _scene_with_portraits(db, tmp_path, model, resolution, chained=chained)
        # Wan 3 now has a separate fal audio-reference route. This regression
        # deliberately selects its standard OpenRouter route. Unsupported
        # models must still warn about stale audio-sync preferences.
        scene.audio_sync_enabled = model != "wan-3.0"
        db.add(scene)
        db.commit()
        report = generation_service.scene_generation_preflight(scene, db, phase="video")
        assert report["ready"], report["errors"]
        assert report["provider"] == "openrouter"
        assert report["route"] == "openrouter-video"
        assert not report["will_generate_image"]
        stale_audio_warning = any(
            "Audio sync" in warning and "video-only" in warning
            for warning in report["warnings"]
        )
        assert stale_audio_warning is (model != "wan-3.0")
        assert report["estimated_video_cost"] == pytest.approx(expected_cost)
        assert report["estimated_cost"] == pytest.approx(expected_cost)

        await generation_service._run_pipeline(scene, db, test_engine, phase="video")

        assert len(submitted_payloads) == 1
        payload = submitted_payloads[0]
        assert payload["model"] == provider_id
        assert payload["duration"] == 5
        assert payload["resolution"] == resolution
        assert payload["aspect_ratio"] == "16:9"
        assert payload["generate_audio"] is False
        assert "input_references" not in payload
        assert "audio_url" not in payload
        assert len(payload["frame_images"]) == 1
        assert payload["frame_images"][0]["frame_type"] == "first_frame"
        assert _image_bytes(payload["frame_images"][0]) == (
            b"previous-video-final-pixels" if chained else b"planned-scene-first-frame"
        )
        job = db.exec(select(GenerationJob).where(GenerationJob.scene_id == scene.id)).one()
        asset = db.exec(select(SceneAsset).where(SceneAsset.scene_id == scene.id)).one()
        assert job.status == "completed"
        assert job.cost_usd == pytest.approx(expected_cost)
        assert asset.cost_usd == pytest.approx(expected_cost)
        assert asset.model_used == model
        assert json.loads(asset.metadata_json)["resolution"] == resolution
        assert scene.status == "done"


@pytest.mark.parametrize("model,provider_id", [
    ("wan-3.0", "alibaba/wan-3.0"),
    ("seedance-2.5", "bytedance/seedance-2.5"),
    ("seedance-2.0-mini", "bytedance/seedance-2.0-mini"),
])
@pytest.mark.asyncio
async def test_new_character_reference_routes_send_only_named_portrait(
    test_engine, tmp_path, submitted_payloads, model, provider_id,
):
    with Session(test_engine) as db:
        scene = _scene_with_portraits(db, tmp_path, model, "720p", mode="character")
        report = generation_service.scene_generation_preflight(scene, db, phase="video")
        assert report["ready"], report["errors"]
        assert not report["will_generate_image"]
        await generation_service._run_pipeline(scene, db, test_engine, phase="video")
        assert len(submitted_payloads) == 1
        payload = submitted_payloads[0]
        assert payload["model"] == provider_id
        assert "frame_images" not in payload
        assert len(payload["input_references"]) == 1
        assert _image_bytes(payload["input_references"][0]) == b"alex-portrait"
        assert payload["generate_audio"] is False


@pytest.mark.parametrize("model,resolution", [("hailuo-3-max", "768p"), ("runway-gen-4.5", "720p")])
@pytest.mark.asyncio
async def test_models_without_character_route_block_before_submission(
    test_engine, tmp_path, submitted_payloads, model, resolution,
):
    with Session(test_engine) as db:
        scene = _scene_with_portraits(db, tmp_path, model, resolution, mode="character")
        report = generation_service.scene_generation_preflight(scene, db, phase="video")
        assert not report["ready"]
        assert any("character references" in error for error in report["errors"])
        with pytest.raises(RuntimeError, match="does not support"):
            await generation_service._run_pipeline(scene, db, test_engine, phase="video")
        assert submitted_payloads == []
        assert not db.exec(select(GenerationJob)).all()


@pytest.mark.asyncio
async def test_h3_invalid_720p_warns_and_uses_same_fallback_for_price_and_submit(
    test_engine, tmp_path, submitted_payloads,
):
    with Session(test_engine) as db:
        scene = _scene_with_portraits(db, tmp_path, "hailuo-3-max", "720p")
        report = generation_service.scene_generation_preflight(scene, db, phase="video")
        assert report["ready"], report["errors"]
        assert any("720p" in warning and "480p" in warning for warning in report["warnings"])
        assert report["estimated_video_cost"] == pytest.approx(0.25)
        await generation_service._run_pipeline(scene, db, test_engine, phase="video")
        assert submitted_payloads[0]["resolution"] == "480p"
        job = db.exec(select(GenerationJob)).one()
        assert job.cost_usd == pytest.approx(report["estimated_video_cost"])
