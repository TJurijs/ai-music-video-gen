"""Wan 3 song-reference contract and recovery, using only mocked provider IO."""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.config import VIDEO_MODELS
from app.models import Character, GenerationJob, Project, SceneAsset
from app.services import fal_client, generation_service as generation
from test_audio_video_workflows import fal_transport, make_scene


@pytest.fixture(autouse=True)
def mock_reference_media_for_mocked_workflows(request, monkeypatch):
    if not request.node.name.startswith("test_real_wan_image"):
        monkeypatch.setattr(generation, "_validate_wan_reference_image", lambda path: None)


def wan_scene(db, tmp_path, *, chained=False, duration=15):
    scene = make_scene(db, tmp_path, "wan-3.0", chained=chained, duration=duration)
    scene.audio_sync_enabled = True
    db.add(scene)
    db.commit()
    return scene


@pytest.mark.parametrize("chained", [False, True])
@pytest.mark.parametrize("resolution,cost", [("480p", 0.75), ("720p", 1.5), ("1080p", 3.0)])
@pytest.mark.asyncio
async def test_wan3_submits_full_audio_still_cast_correct_schema_and_price(
    test_engine, tmp_path, fal_transport, chained, resolution, cost,
):
    with Session(test_engine) as db:
        scene = wan_scene(db, tmp_path, chained=chained)
        scene.resolution = resolution
        report = generation.scene_generation_preflight(scene, db, phase="video")
        assert report["ready"], report["errors"]
        assert report["route"] == "wan-r2v-audio"
        assert report["provider"] == "fal"
        assert report["estimated_video_cost"] == cost
        assert any("not yet verified" in warning for warning in report["warnings"])
        await generation._run_pipeline(scene, db, test_engine, phase="video")

        path, payload = fal_transport["payloads"][0]
        frame = "previous.jpg" if chained else "still.jpg"
        assert path == "/alibaba/wan-3.0/reference-to-video"
        assert payload == {
            "prompt": scene.video_prompt + "\n\nUse Audio 1 as the song reference for the visible performance and movement."
                      "\nImage 1: scene composition and visual style reference, not an exact first frame."
                      "\nImage 2: appearance reference for Alex.",
            "reference_image_urls": [f"https://media.invalid/{frame}", "https://media.invalid/portrait.jpg"],
            "reference_audio_urls": ["https://media.invalid/exact.wav"],
            "duration": 15, "resolution": resolution, "aspect_ratio": "16:9",
            "audio": True, "enable_prompt_expansion": True, "enable_safety_checker": True,
        }
        assert fal_transport["slices"] == [(15 if chained else 0, 15, {"lossless": True})]
        assert fal_transport["conform"] == [15]
        job = db.exec(select(GenerationJob)).one()
        assert job.cost_usd == cost and job.status == "completed"
        metadata = json.loads(db.exec(select(SceneAsset)).one().metadata_json)
        assert metadata["route"] == "wan-r2v-audio"
        assert metadata["reference_mode"] == "references"
        assert metadata["image_refs"] == 2 and metadata["char_refs"] == 1
        assert metadata["frame_ref_included"] is True
        assert metadata["audio_reference_used"] is True
        assert metadata["audio_duration"] == 15


@pytest.mark.parametrize("problem,reason", [
    ("duration", "2 to 15"), ("fractional", "whole-second"),
    ("resolution", "2160p"), ("aspect", "21:9"), ("prompt", "20,000"),
    ("large_image", "20 MB"), ("image_format", "JPG"), ("ref_count", "10 reference images"),
])
@pytest.mark.asyncio
async def test_wan3_rejects_bad_inputs_before_provider_or_image_work(
    test_engine, tmp_path, fal_transport, problem, reason,
):
    with Session(test_engine) as db:
        scene = wan_scene(db, tmp_path)
        if problem == "duration":
            scene.audio_end = 16
        elif problem == "fractional":
            scene.audio_end = 14.9
        elif problem == "resolution":
            scene.resolution = "2160p"
        elif problem == "aspect":
            project = db.get(Project, scene.project_id)
            project.aspect_ratio = "21:9"
            db.add(project)
        elif problem == "prompt":
            project = db.get(Project, scene.project_id)
            project.style = "x" * 20000
            db.add(project)
        elif problem == "large_image":
            with open(scene.reference_image_path, "wb") as stream:
                stream.truncate(20 * 1024 * 1024 + 1)
        elif problem == "image_format":
            path = tmp_path / "bad.gif"
            path.write_bytes(b"unsupported")
            scene.reference_image_path = str(path)
        elif problem == "ref_count":
            for index in range(9):
                portrait = tmp_path / f"cast{index}.jpg"
                portrait.write_bytes(b"portrait")
                name = f"Singer{index}"
                db.add(Character(project_id=scene.project_id, name=name,
                                 description="Singer", reference_image_path=str(portrait)))
                scene.video_prompt += " " + name
        db.add(scene)
        db.commit()
        report = generation.scene_generation_preflight(scene, db, phase="video")
        assert not report["ready"]
        assert any(reason in error for error in report["errors"]), report["errors"]
        with pytest.raises(RuntimeError, match=reason):
            await generation._run_pipeline(scene, db, test_engine, phase="video")
        assert not fal_transport["payloads"] and not fal_transport["uploads"]


@pytest.mark.parametrize("audio_duration", [None, 14.8, 15.02])
@pytest.mark.asyncio
async def test_wan3_checks_actual_audio_before_upload(
    test_engine, tmp_path, fal_transport, monkeypatch, audio_duration,
):
    monkeypatch.setattr(generation, "_probe_duration", lambda path: audio_duration)
    with Session(test_engine) as db:
        scene = wan_scene(db, tmp_path)
        with pytest.raises(RuntimeError, match="reference audio must match"):
            await generation._run_pipeline(scene, db, test_engine, phase="video")
        assert not fal_transport["payloads"] and not fal_transport["uploads"]


@pytest.mark.parametrize("failure", ["poll", "duration", "cancel"])
@pytest.mark.asyncio
async def test_wan3_resume_uses_saved_contract_without_new_upload_or_submission(
    test_engine, tmp_path, fal_transport, monkeypatch, failure,
):
    original_poll = fal_client.poll
    original_conform = generation._conform_audio_driven_video_duration
    if failure == "poll":
        fal_transport["fail_poll"] = True
    elif failure == "duration":
        def reject(*args):
            raise RuntimeError("provider video duration differs")
        monkeypatch.setattr(generation, "_conform_audio_driven_video_duration", reject)
    else:
        async def cancel(*args, **kwargs):
            raise asyncio.CancelledError()
        monkeypatch.setattr(fal_client, "poll", cancel)

    with Session(test_engine) as db:
        scene = wan_scene(db, tmp_path)
        with pytest.raises((RuntimeError, asyncio.CancelledError)):
            await generation._run_pipeline(scene, db, test_engine, phase="video")
        job = db.exec(select(GenerationJob)).one()
        assert job.status == "running" and job.external_id == "saved-render"
        assert json.loads(job.request_json)["route"] == "wan-r2v-audio"
        monkeypatch.setattr(fal_client, "poll", original_poll)
        monkeypatch.setattr(generation, "_conform_audio_driven_video_duration", original_conform)
        scene.video_model = "removed-model"
        scene.audio_end = 8
        scene.audio_sync_enabled = False
        db.add(scene)
        db.commit()
        report = generation.scene_generation_preflight(scene, db, phase="video")
        assert report["ready"] and report["resuming"] and report["estimated_cost"] == 0
        await generation._run_pipeline(scene, db, test_engine, phase="video", force=True)
        assert len(fal_transport["payloads"]) == 1
        assert len(fal_transport["uploads"]) == 3
        assert fal_transport["conform"] == [15]
        assert job.status == "completed"
        metadata = json.loads(db.exec(select(SceneAsset)).one().metadata_json)
        assert metadata["audio_end"] == 15  # saved song window, not changed scene settings
        assert metadata["reference_mode"] == "references"
        assert metadata["resumed"] is True


@pytest.mark.asyncio
async def test_seedance_still_uses_its_distinct_fields_and_audio_margin(test_engine, tmp_path, fal_transport):
    with Session(test_engine) as db:
        scene = make_scene(db, tmp_path, "seedance-2.0")
        scene.audio_sync_enabled = True
        await generation._run_pipeline(scene, db, test_engine, phase="video")
        _, payload = fal_transport["payloads"][0]
        assert payload["duration"] == "15"
        assert payload["generate_audio"] is False
        assert "image_urls" in payload and "audio_urls" in payload
        assert "reference_audio_urls" not in payload
        assert fal_transport["slices"] == [(0, 14.85, {})]
        assert not fal_transport["conform"]


@pytest.mark.parametrize("size,alpha,valid", [
    ("240x240", None, True), ("200x240", None, False), ("2160x240", None, False),
    ("240x240", "1", True), ("240x240", "0.5", False),
])
def test_real_wan_image_dimensions_and_transparency(tmp_path, size, alpha, valid):
    image = tmp_path / "reference.png"
    filters = "format=rgba" + (f",colorchannelmixer=aa={alpha}" if alpha else "")
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=blue:s={size}",
        "-vf", filters, "-frames:v", "1", str(image),
    ], check=True, capture_output=True)
    if valid:
        generation._validate_wan_reference_image(str(image))
    else:
        with pytest.raises(RuntimeError, match="240–8000|opaque"):
            generation._validate_wan_reference_image(str(image))
