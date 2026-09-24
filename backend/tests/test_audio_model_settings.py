"""Scene settings and downloaded audio must use the actual provider route.

These tests use a disposable database and replace audio extraction; no provider
requests or changes to a user's scenes are made.
"""

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.models import Project, Scene, ScenePromptVersion
from app.routers.scenes import SceneUpdate, chain_to_next, download_audio_chunk, update_scene
from app.services import generation_service


def _scene(db, *, aspect="16:9", **overrides):
    project = Project(name="Audio settings", aspect_ratio=aspect)
    db.add(project)
    db.commit()
    db.refresh(project)
    values = dict(
        project_id=project.id, order=1, audio_start=30, audio_end=45,
        video_model="wan-2.7", resolution="720p", audio_sync_enabled=True,
        description="Original scene", video_prompt="Original motion",
    )
    values.update(overrides)
    scene = Scene(**values)
    db.add(scene)
    db.commit()
    db.refresh(scene)
    return scene


@pytest.mark.parametrize("resolution", ["720p", "1080p"])
def test_wan_can_select_15_seconds_with_song_sync(test_engine, resolution):
    with Session(test_engine) as db:
        scene = _scene(db, video_model="kling-v3.0-pro", audio_sync_enabled=False)
        result = update_scene(scene.id, SceneUpdate(
            video_model="wan-2.7", audio_sync_enabled=True, resolution=resolution,
        ), db)
        assert result["video_model"] == "wan-2.7"
        assert result["audio_sync_enabled"] is True
        assert result["duration"] == 15
        assert result["resolution"] == resolution


@pytest.mark.parametrize("scene_values,payload,expected_message", [
    ({}, {"audio_sync_enabled": False}, "cannot render this 15s scene"),
    ({"video_model": "kling-v3.0-pro", "audio_sync_enabled": False},
     {"video_model": "wan-2.7"}, "cannot render this 15s scene"),
    ({}, {"video_model": "ltx-2.5-fast", "resolution": "720p"}, "supports 1080p"),
    ({"video_model": "ltx-2.5-fast", "resolution": "1080p"},
     {"audio_sync_enabled": False}, "requires song sync"),
    ({"aspect": "1:1"}, {"video_model": "ltx-2.5-fast", "resolution": "1080p"}, "1:1 aspect ratio"),
    ({}, {"audio_end": 46}, "cannot render this 16s scene"),
    ({"video_model": "ltx-2.5-fast", "resolution": "1080p"},
     {"audio_end": 51}, "cannot render this 21s scene"),
])
def test_invalid_route_changes_leave_scene_and_prompt_history_unchanged(
    test_engine, scene_values, payload, expected_message,
):
    with Session(test_engine) as db:
        scene = _scene(db, **scene_values)
        original = scene.model_dump()
        with pytest.raises(HTTPException) as failure:
            update_scene(scene.id, SceneUpdate(
                **payload, description="Must not persist", video_prompt="Must not version",
            ), db)
        assert failure.value.status_code == 400
        assert expected_message in failure.value.detail
        # Catch both persisted partial writes and dirty ORM attributes that a
        # later successful operation could inadvertently commit.
        assert scene.model_dump() == original
        db.commit()
        db.refresh(scene)
        assert scene.model_dump() == original
        assert db.exec(select(ScenePromptVersion)).all() == []


def test_ltx_selection_enables_required_audio_and_clears_character_mode(test_engine):
    with Session(test_engine) as db:
        scene = _scene(
            db, video_model="kling-v3.0-pro", audio_sync_enabled=False,
            video_reference_mode="character",
        )
        result = update_scene(scene.id, SceneUpdate(
            video_model="ltx-2.5-fast", resolution="1080p",
        ), db)
        assert result["audio_sync_enabled"] is True
        assert result["video_reference_mode"] == "frame"
        assert result["duration"] == 15
        assert result["resolution"] == "1080p"


def test_wan_can_disable_song_sync_when_standard_route_supports_scene_length(test_engine):
    with Session(test_engine) as db:
        scene = _scene(db, audio_end=40)
        result = update_scene(scene.id, SceneUpdate(audio_sync_enabled=False), db)
        assert result["audio_sync_enabled"] is False
        assert result["duration"] == 10


@pytest.mark.parametrize("model,audio_enabled,resolution,duration,expected_audio", [
    ("wan-2.7", True, "720p", 15, True),
    ("ltx-2.5-fast", False, "1080p", 15, True),
    ("kling-v3.0-pro", False, "720p", 15, False),
    ("ltx-2.5-fast", False, "1080p", 2, True),
])
def test_new_chained_scene_preserves_duration_and_effective_audio_route(
    test_engine, model, audio_enabled, resolution, duration, expected_audio,
):
    with Session(test_engine) as db:
        scene = _scene(
            db, video_model=model, audio_sync_enabled=audio_enabled,
            resolution=resolution, audio_start=30, audio_end=30 + duration,
        )
        result = chain_to_next(scene.id, db)
        created = db.get(Scene, result["id"])
        assert created.id != scene.id
        assert created.project_id == scene.project_id
        assert created.order == scene.order + 1
        assert created.chain_from_prev is True
        assert created.video_model == model
        assert created.image_model == scene.image_model
        assert created.resolution == resolution
        assert created.audio_sync_enabled is expected_audio
        assert created.audio_start == 30 + duration
        assert created.audio_end == 30 + 2 * duration
        assert result["duration"] == duration
        assert scene.audio_sync_enabled is audio_enabled


@pytest.mark.parametrize("model,audio_enabled,duration,lossless,media_type,extension", [
    ("ltx-2.5-fast", False, 15, True, "audio/wav", "wav"),
    ("wan-2.7", True, 15, True, "audio/wav", "wav"),
    ("wan-3.0", True, 15, True, "audio/wav", "wav"),
    ("seedance-2.0", True, 14.85, False, "audio/mpeg", "mp3"),
    ("wan-2.7", False, 15, False, "audio/mpeg", "mp3"),
])
@pytest.mark.asyncio
async def test_downloaded_audio_matches_route_input_encoding_and_duration(
    test_engine, tmp_path, monkeypatch, model, audio_enabled,
    duration, lossless, media_type, extension,
):
    audio_path = tmp_path / f"scene-audio.{extension}"
    audio_path.write_bytes(b"audio fixture")
    extracted = []

    async def extract(scene, db, **kwargs):
        extracted.append((scene.audio_start, scene.audio_end, kwargs))
        return str(audio_path)

    monkeypatch.setattr(generation_service, "_extract_audio_segment", extract)
    with Session(test_engine) as db:
        scene = _scene(db, video_model=model, audio_sync_enabled=audio_enabled)
        response = await download_audio_chunk(scene.id, db)

    assert len(extracted) == 1
    start, end, options = extracted[0]
    assert (start, end) == (30, 45)
    assert options["max_duration"] == pytest.approx(duration)
    assert options["lossless"] is lossless
    assert response.path == str(audio_path)
    assert response.media_type == media_type
    assert response.filename == f"scene_1_30.0-45.0s.{extension}"
    assert "attachment" in response.headers["content-disposition"]


def test_wan3_song_reference_limits_do_not_shorten_standard_scene(test_engine):
    with Session(test_engine) as db:
        scene = _scene(db, video_model="wan-3.0", audio_sync_enabled=False, audio_end=60)
        with pytest.raises(HTTPException, match="cannot render this 30s scene"):
            update_scene(scene.id, SceneUpdate(audio_sync_enabled=True), db)
        assert scene.audio_end == 60 and scene.audio_sync_enabled is False
        response = update_scene(scene.id, SceneUpdate(audio_sync_enabled=True, audio_end=45), db)
        assert response["duration"] == 15
        assert response["audio_sync_enabled"] is True
