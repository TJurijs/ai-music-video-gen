import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.config import settings
from app.models import Character, GenerationJob, Project, Scene, SceneAsset, Song
from app.routers.generation import _claim_scene_run
from app.services.generation_state import release_stale_scene_claim
from app.services.generation_service import (
    _find_character_references,
    scene_generation_preflight,
)
from app.services import generation_service


def _project_and_scene(db: Session, **scene_values) -> tuple[Project, Scene]:
    project = Project(name="Test", aspect_ratio="16:9")
    db.add(project)
    db.commit()
    db.refresh(project)
    values = {
        "project_id": project.id,
        "order": 1,
        "audio_start": 0,
        "audio_end": 4,
        "description": "A cinematic opening",
        "image_prompt": "A cinematic opening",
        "video_prompt": "A cinematic opening",
    }
    values.update(scene_values)
    scene = Scene(**values)
    db.add(scene)
    db.commit()
    db.refresh(scene)
    return project, scene


def test_scene_claim_is_atomic(test_engine):
    with Session(test_engine) as db:
        _, scene = _project_and_scene(db)
        run_id = _claim_scene_run(db, scene.id, "video")
        assert run_id
        assert _claim_scene_run(db, scene.id, "video") is None
        db.refresh(scene)
        assert scene.generation_run_id == run_id
        assert scene.generation_phase == "video"


def test_old_claim_without_active_job_is_released(test_engine, tmp_path: Path):
    video = tmp_path / "existing.mp4"
    video.write_bytes(b"existing")

    with Session(test_engine) as db:
        _, scene = _project_and_scene(
            db,
            status="pending",
            video_path=str(video),
            generation_run_id="abandoned-run",
            generation_phase="video",
            generation_requested_at=datetime.utcnow() - timedelta(hours=1),
        )

        assert release_stale_scene_claim(scene, db) is True
        assert scene.generation_run_id is None
        assert scene.generation_phase is None
        assert scene.generation_requested_at is None
        assert scene.status == "done"


def test_old_claim_with_active_job_stays_locked(test_engine):
    with Session(test_engine) as db:
        project, scene = _project_and_scene(
            db,
            status="generating_video",
            generation_run_id="active-run",
            generation_phase="video",
            generation_requested_at=datetime.utcnow() - timedelta(hours=1),
        )
        db.add(GenerationJob(
            project_id=project.id,
            scene_id=scene.id,
            job_type="video",
            provider="fal",
            status="running",
            external_id="provider-task",
        ))
        db.commit()

        assert release_stale_scene_claim(scene, db) is False
        assert scene.generation_run_id == "active-run"
        assert scene.status == "generating_video"


def test_audio_preflight_uses_fal_route_pricing_and_resolutions(
    test_engine, tmp_path: Path, monkeypatch,
):
    still = tmp_path / "still.jpg"
    song_file = tmp_path / "song.mp3"
    still.write_bytes(b"still")
    song_file.write_bytes(b"song")
    monkeypatch.setattr(settings, "fal_api_key", "test-key")

    with Session(test_engine) as db:
        project, scene = _project_and_scene(
            db,
            video_model="seedance-2.0",
            resolution="4K",
            audio_sync_enabled=True,
            reference_image_path=str(still),
        )
        db.add(Song(
            project_id=project.id,
            title="Track",
            file_path=str(song_file),
            status="ready",
        ))
        db.commit()

        report = scene_generation_preflight(scene, db, phase="video")

    assert report["ready"] is True
    assert report["provider"] == "fal"
    assert report["route"] == "seedance-r2v"
    assert report["estimated_video_cost"] == pytest.approx(0.72)
    assert any("480p" in warning for warning in report["warnings"])
    assert any("exact first/last-frame" in warning for warning in report["warnings"])


def test_preflight_rejects_model_that_cannot_render_exact_scene_length(
    test_engine, tmp_path: Path, monkeypatch,
):
    still = tmp_path / "still.jpg"
    still.write_bytes(b"still")
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")

    with Session(test_engine) as db:
        _, scene = _project_and_scene(
            db,
            audio_end=10,
            video_model="veo-3.1",
            reference_image_path=str(still),
        )
        report = scene_generation_preflight(scene, db, phase="video")

    assert report["ready"] is False
    assert any("cannot render this 10s scene" in error for error in report["errors"])
    assert not any("closest supported" in warning for warning in report["warnings"])


def test_character_matcher_uses_word_boundaries(test_engine, tmp_path: Path):
    portrait = tmp_path / "al.jpg"
    portrait.write_bytes(b"portrait")
    with Session(test_engine) as db:
        project, scene = _project_and_scene(
            db,
            description="A wall fills the frame",
            image_prompt="A wall fills the frame",
            video_prompt="A wall fills the frame",
        )
        db.add(Character(
            project_id=project.id,
            name="Al",
            description="Singer",
            reference_image_path=str(portrait),
        ))
        db.commit()

        assert _find_character_references(scene, db, scene.video_prompt) == []
        scene.video_prompt = "Al walks into frame"
        assert _find_character_references(scene, db, scene.video_prompt) == [str(portrait)]


@pytest.mark.asyncio
async def test_openrouter_resume_does_not_rebuild_missing_inputs(
    test_engine, tmp_path: Path, monkeypatch,
):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    calls: list[str] = []

    async def poll(job_id, **_kwargs):
        calls.append(job_id)
        return "https://provider.invalid/result.mp4"

    async def download(_url, destination):
        Path(destination).write_bytes(b"video")

    async def prepare(_scene, _destination):
        return None

    monkeypatch.setattr(generation_service.openrouter, "poll_video_job", poll)
    monkeypatch.setattr(generation_service.openrouter, "download_file", download)
    monkeypatch.setattr(generation_service, "_prepare_generated_video", prepare)

    with Session(test_engine) as db:
        _, scene = _project_and_scene(
            db,
            video_model="model-removed-after-submit",
            reference_image_path=None,
            video_prompt="",
            image_prompt="",
            description="",
        )
        job = GenerationJob(
            project_id=scene.project_id,
            scene_id=scene.id,
            job_type="video",
            provider="openrouter",
            external_id="paid-job-1",
            status="running",
            request_json=json.dumps({
                "route": "openrouter-video",
                "model_key": "seedance-2.0",
                "duration": 4,
                "resolution": "720p",
                "aspect_ratio": "16:9",
                "reference_mode": "character",
                "char_refs": 1,
            }),
            cost_usd=0.5,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        await generation_service._generate_video_openrouter(
            scene, db, {}, test_engine,
        )
        db.refresh(job)
        db.refresh(scene)
        assets = db.exec(select(SceneAsset)).all()

        assert calls == ["paid-job-1"]
        assert job.status == "completed"
        assert scene.video_path
        assert len(assets) == 1
