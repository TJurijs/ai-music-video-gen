import asyncio
import json
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import text
from sqlmodel import Session, select

from app.models import GenerationJob, Project, Scene, SceneAsset, Song
from app.config import settings
from app.routers.generation import trigger_assembly
from app.routers.scenes import GenerateBatchRequest, generate_scene_batch
from app.routers import songs as songs_router
from app.services import openrouter, scene_planner, suno
from app.services.versioning import make_active


def _scene(project_id: int, order: int, status: str) -> Scene:
    return Scene(
        project_id=project_id,
        order=order,
        audio_start=float(order - 1),
        audio_end=float(order),
        description=f"Scene {order}",
        image_prompt=f"Image {order}",
        video_prompt=f"Video {order}",
        status=status,
    )


def test_project_accepts_only_one_song(test_engine):
    with Session(test_engine) as db:
        project = Project(name="One song")
        db.add(project)
        db.commit()
        db.refresh(project)
        db.add(Song(project_id=project.id, title="Existing", status="ready"))
        db.commit()

        with pytest.raises(HTTPException) as caught:
            songs_router._ensure_song_slot_available(db, project.id)

        assert caught.value.status_code == 409
        assert "already has a song" in caught.value.detail


def test_switching_active_asset_respects_partial_unique_index(test_engine):
    with test_engine.begin() as connection:
        connection.execute(text(
            "CREATE UNIQUE INDEX uq_test_sceneasset_active "
            "ON sceneasset(scene_id, asset_type) WHERE is_active = 1"
        ))

    with Session(test_engine) as db:
        project = Project(name="Variants")
        db.add(project)
        db.commit()
        db.refresh(project)
        scene = _scene(project.id, 1, "done")
        db.add(scene)
        db.commit()
        db.refresh(scene)
        old = SceneAsset(
            scene_id=scene.id,
            asset_type="video",
            file_path="old.mp4",
            is_active=True,
        )
        replacement = SceneAsset(
            scene_id=scene.id,
            asset_type="video",
            file_path="replacement.mp4",
            is_active=False,
        )
        db.add(old)
        db.add(replacement)
        db.commit()
        db.refresh(replacement)

        make_active(
            db,
            target=replacement,
            siblings_filter=[
                SceneAsset.scene_id == scene.id,
                SceneAsset.asset_type == "video",
            ],
        )
        db.commit()

        active = db.exec(select(SceneAsset).where(
            SceneAsset.scene_id == scene.id,
            SceneAsset.asset_type == "video",
            SceneAsset.is_active == True,  # noqa: E712
        )).all()
        assert [asset.id for asset in active] == [replacement.id]


@pytest.mark.asyncio
async def test_assembly_rejects_completed_scenes_after_a_gap(test_engine):
    with Session(test_engine) as db:
        project = Project(name="Assembly")
        db.add(project)
        db.commit()
        db.refresh(project)
        scenes = [
            _scene(project.id, 1, "done"),
            _scene(project.id, 2, "pending"),
            _scene(project.id, 3, "done"),
        ]
        db.add_all(scenes)
        db.commit()

        with pytest.raises(HTTPException) as caught:
            await trigger_assembly(project.id, BackgroundTasks(), db)
        assert caught.value.status_code == 400
        assert "contiguous" in caught.value.detail
        assert not db.exec(select(GenerationJob)).all()

        scenes[2].status = "pending"
        db.add(scenes[2])
        db.commit()
        tasks = BackgroundTasks()
        result = await trigger_assembly(project.id, tasks, db)
        assert result["message"] == "Assembling 1 scenes"
        assert len(tasks.tasks) == 1


@pytest.mark.asyncio
async def test_failed_replan_preserves_existing_plan(
    test_engine, monkeypatch,
):
    with Session(test_engine) as db:
        project = Project(name="Plan", story_seed="old seed")
        db.add(project)
        db.commit()
        db.refresh(project)
        song = Song(
            project_id=project.id,
            title="Track",
            duration=10,
            status="ready",
        )
        existing = _scene(project.id, 1, "pending")
        existing.description = "Keep me"
        db.add(song)
        db.add(existing)
        db.commit()
        db.refresh(song)

        async def provider_failure(**_kwargs):
            raise RuntimeError("provider unavailable")

        monkeypatch.setattr(scene_planner, "plan_scene_batch", provider_failure)
        request = GenerateBatchRequest(
            project_id=project.id,
            song_id=song.id,
            target_scene_duration=5,
            start_index=0,
            batch_size=2,
            story_seed="new seed",
        )
        with pytest.raises(HTTPException) as caught:
            await generate_scene_batch(request, db)
        assert caught.value.status_code == 500

    with Session(test_engine) as verify:
        scenes = verify.exec(select(Scene)).all()
        saved_project = verify.get(Project, project.id)
        assert len(scenes) == 1
        assert scenes[0].description == "Keep me"
        assert saved_project.story_seed == "old seed"


@pytest.mark.asyncio
async def test_fal_cancellation_keeps_remote_handle_when_unconfirmed(monkeypatch):
    from app.services import fal_client

    submission = {
        "request_id": "request-1",
        "status_url": "https://example.invalid/status",
        "response_url": "https://example.invalid/result",
        "cancel_url": "https://example.invalid/cancel",
    }

    async def cancelled(_submission):
        return True

    monkeypatch.setattr(fal_client, "cancel_submission", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await fal_client.poll(submission, is_cancelled=lambda: True)

    async def not_cancelled(_submission):
        return False

    monkeypatch.setattr(fal_client, "cancel_submission", not_cancelled)
    with pytest.raises(fal_client.RemoteJobPendingError, match="preserved"):
        await fal_client.poll(submission, is_cancelled=lambda: True)


@pytest.mark.asyncio
async def test_suno_retry_polls_saved_task_without_resubmitting(
    test_engine, tmp_path: Path, monkeypatch,
):
    monkeypatch.setattr(songs_router, "engine", test_engine)
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    calls: list[str] = []

    async def submit_song(**_kwargs):
        raise AssertionError("resume must not submit a second Suno task")

    async def poll_song(task_id):
        calls.append(task_id)
        return {
            "audio_url": "https://provider.invalid/song.mp3",
            "lyrics": "saved lyrics",
        }

    async def download(_url, destination):
        Path(destination).write_bytes(b"audio")

    async def validate(_path, _kind):
        return {}

    async def skip_analysis(_song_id):
        return None

    monkeypatch.setattr(suno, "submit_song", submit_song)
    monkeypatch.setattr(suno, "poll_song", poll_song)
    monkeypatch.setattr(openrouter, "download_file", download)
    monkeypatch.setattr(songs_router, "validate_media_async", validate)
    monkeypatch.setattr(songs_router, "_analyze_song_bg", skip_analysis)

    with Session(test_engine) as db:
        project = Project(name="Song resume")
        db.add(project)
        db.commit()
        db.refresh(project)
        song = Song(
            project_id=project.id,
            title="Track",
            source="suno",
            status="error",
        )
        db.add(song)
        db.commit()
        db.refresh(song)
        job = GenerationJob(
            project_id=project.id,
            job_type="music",
            provider="suno",
            status="running",
            external_id="suno-task-1",
            request_json=json.dumps({"song_id": song.id, "request": {}}),
            cost_usd=0.118,
        )
        db.add(job)
        db.commit()
        song_id = song.id
        job_id = job.id

    await songs_router._generate_and_analyze_bg(song_id, None)

    with Session(test_engine) as verify:
        saved_song = verify.get(Song, song_id)
        saved_job = verify.get(GenerationJob, job_id)
        assert calls == ["suno-task-1"]
        assert saved_song.file_path and Path(saved_song.file_path).is_file()
        assert saved_song.lyrics == "saved lyrics"
        assert saved_job.status == "completed"
