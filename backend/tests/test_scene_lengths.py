"""Fixed-length planning must not stretch scenes to absorb the song tail."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import Session, select

from app.models import Project, Scene, Song
from app.routers.scenes import GenerateBatchRequest, generate_scene_batch
from app.services import scene_planner
from app.services.scene_planner import compute_scene_windows


@pytest.mark.parametrize("duration,length,count", [
    (151.96, 15, 11), (150, 15, 10), (152, 15, 11),
    (31, 15, 3), (15, 15, 1), (1.2, 15, 1),
    (24, 8, 3), (25, 8, 4), (180.5, 3, 61),
])
def test_windows_keep_exact_requested_length_and_cover_song(duration, length, count):
    windows = compute_scene_windows(duration, length)
    assert len(windows) == count
    assert windows[0][0] == 0
    assert all(end - start == length for start, end in windows)
    assert all(left[1] == right[0] for left, right in zip(windows, windows[1:]))
    assert windows[-1][0] < duration <= windows[-1][1]


@pytest.mark.parametrize("duration,length", [
    (0, 15), (-1, 15), (float("nan"), 15), (float("inf"), 15),
    (100, 0), (100, 15.5), (100, float("nan")), (100, 61),
])
def test_invalid_timing_is_rejected(duration, length):
    with pytest.raises(ValueError):
        compute_scene_windows(duration, length)


def test_request_rejects_fractional_scene_length():
    with pytest.raises(ValidationError):
        GenerateBatchRequest(project_id=1, song_id=1, target_scene_duration=15.5)


@pytest.mark.asyncio
async def test_batched_plan_persists_exact_length_despite_llm_timestamps(test_engine, monkeypatch):
    calls = []

    async def plan(**kwargs):
        calls.append(kwargs)
        return [{
            "description": "Alex walks", "image_prompt": "Alex standing",
            "video_prompt": "Alex walks", "audio_start": 999, "audio_end": 1015,
        } for _ in kwargs["batch_windows"]]

    monkeypatch.setattr(scene_planner, "plan_scene_batch", plan)
    with Session(test_engine) as db:
        project = Project(name="Fixed length")
        db.add(project)
        db.commit()
        song = Song(project_id=project.id, title="Song", duration=151.96, status="ready")
        db.add(song)
        db.commit()
        start = 0
        while True:
            result = await generate_scene_batch(GenerateBatchRequest(
                project_id=project.id, song_id=song.id, target_scene_duration=15,
                start_index=start, batch_size=3,
            ), db)
            if not result["has_more"]:
                break
            start = result["next_start_index"]
        scenes = db.exec(select(Scene).order_by(Scene.order)).all()
        assert [(s.audio_start, s.audio_end) for s in scenes] == compute_scene_windows(151.96, 15)
        assert project.target_scene_duration == 15
        assert len(calls) == 4
        assert result["total_planned"] == 11
        assert result["scenes_so_far"] == 11
        # Replaying the final batch must not charge another planning request.
        replay = await generate_scene_batch(GenerateBatchRequest(
            project_id=project.id, song_id=song.id, target_scene_duration=15,
            start_index=9, batch_size=3,
        ), db)
        assert replay["has_more"] is False
        assert len(calls) == 4


@pytest.mark.parametrize("saved_length,requested_length,end", [(15, 8, 15), (None, 15, 16)])
@pytest.mark.asyncio
async def test_continuation_rejects_changed_or_legacy_timings_before_llm(
    test_engine, monkeypatch, saved_length, requested_length, end,
):
    plan = AsyncMock()
    monkeypatch.setattr(scene_planner, "plan_scene_batch", plan)
    with Session(test_engine) as db:
        project = Project(name="Existing plan", target_scene_duration=saved_length)
        db.add(project)
        db.commit()
        song = Song(project_id=project.id, title="Song", duration=151.96, status="ready")
        db.add(song)
        db.add(Scene(project_id=project.id, order=1, audio_start=0, audio_end=end))
        db.commit()
        with pytest.raises(HTTPException) as error:
            await generate_scene_batch(GenerateBatchRequest(
                project_id=project.id, song_id=song.id, target_scene_duration=requested_length,
                start_index=1,
            ), db)
        assert error.value.status_code == 409
        plan.assert_not_awaited()
