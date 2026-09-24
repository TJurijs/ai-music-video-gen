"""Stale upstream anchors cannot reach any provider, even outside the UI."""

import json

import pytest
from sqlmodel import Session, select

from app.config import VIDEO_MODELS, settings
from app.models import Character, GenerationJob, Project, Scene, SceneAsset, Song
from app.services import generation_service as generation


@pytest.mark.parametrize("model,audio,route", [
    ("wan-2.7", False, "_generate_video_openrouter"),
    ("wan-2.7", True, "_generate_video_fal_frame_audio"),
    ("ltx-2.5-fast", True, "_generate_video_fal_frame_audio"),
    ("seedance-2.0", True, "_generate_video_fal_seedance_audio"),
])
@pytest.mark.asyncio
async def test_stale_upstream_video_blocks_preflight_and_direct_provider_route(
    test_engine, tmp_path, monkeypatch, model, audio, route,
):
    monkeypatch.setattr(settings, "fal_api_key", "offline-test")
    monkeypatch.setattr(settings, "openrouter_api_key", "offline-test")
    frame = tmp_path / "stale-frame.jpg"
    frame.write_bytes(b"previously extracted frame")
    song = tmp_path / "song.wav"
    song.write_bytes(b"song present")

    async def no_provider_work(*args, **kwargs):
        pytest.fail("A stale chain anchor must be rejected before media/provider work")

    monkeypatch.setattr(generation, "_extract_audio_segment", no_provider_work)
    monkeypatch.setattr(generation.fal_client, "upload_file", no_provider_work)
    monkeypatch.setattr(generation.openrouter, "submit_video_job", no_provider_work)
    with Session(test_engine) as db:
        project = Project(name="Stale anchor")
        db.add(project)
        db.flush()
        previous = Scene(
            project_id=project.id, order=1, audio_start=0, audio_end=8,
            video_path="old.mp4", extracted_last_frame_path=str(frame),
        )
        db.add(previous)
        db.flush()
        db.add(SceneAsset(
            scene_id=previous.id, asset_type="video", file_path="old.mp4",
            metadata_json=json.dumps({"audio_start": 0, "audio_end": 6}),
        ))
        # A separate portrait must not allow Seedance to silently ignore its
        # selected chain and proceed with a different reference set.
        db.add(Character(project_id=project.id, name="Alex", description="Singer",
                         reference_image_path=str(frame)))
        db.add(Song(project_id=project.id, title="Song", file_path=str(song)))
        child = Scene(
            project_id=project.id, order=2, audio_start=8, audio_end=16,
            video_model=model, audio_sync_enabled=audio, resolution="1080p",
            chain_from_prev=True, video_prompt="Alex sings",
        )
        db.add(child)
        db.commit()

        report = generation.scene_generation_preflight(child, db, phase="video")
        assert not report["ready"]
        assert any("earlier song window" in error for error in report["errors"])
        with pytest.raises(RuntimeError, match="earlier song window"):
            await getattr(generation, route)(child, db, VIDEO_MODELS[model])
        assert not db.exec(select(GenerationJob)).all()
