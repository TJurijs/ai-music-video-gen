"""Scene length edits preserve media and cannot silently desynchronize a song."""

import json
from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.models import GenerationJob, Project, Scene, SceneAsset, Song
from app.routers import scenes as router
from app.routers.scenes import SceneTimingUpdate, update_scene_timing
from app.services.scene_timing import (
    active_video_timing_matches,
    video_timing_matches,
)


def _timeline(db, tmp_path, *, with_assets=True):
    project = Project(name="Timing tests", aspect_ratio="16:9")
    db.add(project)
    db.flush()
    result = []
    for index in range(4):
        video = tmp_path / f"scene-{index}.mp4"
        video.write_bytes(b"original video bytes")
        still = tmp_path / f"scene-{index}.png"
        still.write_bytes(b"original image bytes")
        scene = Scene(project_id=project.id, order=index + 1, audio_start=index * 8,
                      audio_end=(index + 1) * 8, video_model="wan-2.7", resolution="720p",
                      audio_sync_enabled=False, status="done", video_path=str(video),
                      reference_image_path=str(still), video_prompt="Saved motion prompt",
                      lyrics_segment=f"Original words {index}")
        db.add(scene)
        db.flush()
        if with_assets:
            db.add(SceneAsset(scene_id=scene.id, asset_type="video", file_path=str(video),
                              model_used="wan-2.7", metadata_json=json.dumps({"quality": "original"})))
        result.append(scene)
    db.commit()
    return project, result


def _saved_state(db):
    return {
        "scenes": [scene.model_dump() for scene in db.exec(select(Scene).order_by(Scene.id)).all()],
        "assets": [asset.model_dump() for asset in db.exec(select(SceneAsset).order_by(SceneAsset.id)).all()],
    }


def _assets(db, scene):
    return db.exec(select(SceneAsset).where(SceneAsset.scene_id == scene.id).order_by(SceneAsset.id)).all()


def test_timing_clears_chain_anchor_and_blocks_during_assembly(test_engine, tmp_path):
    with Session(test_engine) as db:
        project, timeline = _timeline(db, tmp_path)
        scene = timeline[0]
        scene.extracted_last_frame_path = "old-anchor.jpg"
        job = GenerationJob(project_id=project.id, job_type="assembly", status="running")
        db.add(scene); db.add(job); db.commit()
        with pytest.raises(HTTPException, match="assembly"):
            update_scene_timing(scene.id, SceneTimingUpdate(duration=10), db)
        db.refresh(scene)
        assert scene.audio_end == 8 and scene.extracted_last_frame_path == "old-anchor.jpg"
        job.status = "completed"
        db.add(job); db.commit()
        update_scene_timing(scene.id, SceneTimingUpdate(duration=10), db)
        assert scene.extracted_last_frame_path is None


def test_deleting_current_version_does_not_extract_stale_anchor(test_engine, tmp_path, monkeypatch):
    monkeypatch.setattr(router, "_refresh_scene_last_frame", lambda *a: pytest.fail("Stale anchor must not be extracted"))
    monkeypatch.setattr(router, "remove_storage_file", lambda *a: None)
    with Session(test_engine) as db:
        _, timeline = _timeline(db, tmp_path)
        scene = timeline[0]
        original = _assets(db, scene)[0]
        update_scene_timing(scene.id, SceneTimingUpdate(duration=10), db)
        original.is_active = False
        db.add(original); db.flush()
        current = SceneAsset(scene_id=scene.id, asset_type="video", file_path="new.mp4",
            metadata_json=json.dumps({"audio_start": 0, "audio_end": 10}))
        db.add(current); db.commit()
        router.delete_scene_asset(scene.id, current.id, db)
        assert scene.extracted_last_frame_path is None
        assert scene.status == "image_ready"
        assert not active_video_timing_matches(db, scene)


def test_duration_model_quality_change_is_atomic_and_only_shifts_following(test_engine, tmp_path):
    with Session(test_engine) as db:
        project, timeline = _timeline(db, tmp_path)
        preceding, selected, following, last = timeline
        db.refresh(preceding)
        preceding_before = preceding.model_dump()
        unrelated = Project(name="Other project")
        db.add(unrelated)
        db.flush()
        other_scene = Scene(project_id=unrelated.id, order=3, audio_start=0, audio_end=8)
        db.add(other_scene)
        db.commit()
        db.refresh(other_scene)
        other_before = other_scene.model_dump()

        response = update_scene_timing(selected.id, SceneTimingUpdate(
            duration=15, video_model="ltx-2.5-fast", resolution="1080p",
        ), db)

        assert response["shifted_scenes"] == 2
        assert [item["id"] for item in response["scenes"]] == [selected.id, following.id, last.id]
        assert [(s.audio_start, s.audio_end) for s in timeline] == [(0, 8), (8, 23), (23, 31), (31, 39)]
        assert selected.video_model == "ltx-2.5-fast"
        assert selected.resolution == "1080p"
        assert selected.audio_sync_enabled is True
        assert selected.video_reference_mode == "frame"
        assert (following.video_model, following.resolution, following.audio_sync_enabled) == ("wan-2.7", "720p", False)
        db.refresh(other_scene)
        assert preceding.model_dump() == preceding_before
        assert other_scene.model_dump() == other_before
        assert all(s.status == "image_ready" and "timing changed" in s.error_message.lower() for s in timeline[1:])
        assert all(s.video_prompt == "Saved motion prompt" for s in timeline)


def test_preserves_all_variants_metadata_and_original_bytes(test_engine, tmp_path):
    with Session(test_engine) as db:
        _, timeline = _timeline(db, tmp_path)
        selected = timeline[1]
        prior_file = tmp_path / "prior-variant.mp4"
        prior_file.write_bytes(b"older variant")
        prior = SceneAsset(scene_id=selected.id, asset_type="video", file_path=str(prior_file),
                           model_used="wan-2.7", is_active=False,
                           metadata_json=json.dumps({"audio_start": 8, "audio_end": 14, "source": "older"}))
        image = SceneAsset(scene_id=selected.id, asset_type="image", file_path=selected.reference_image_path,
                           metadata_json='{"image_field": "preserve"}')
        db.add(prior)
        db.add(image)
        db.commit()
        before = {a.id: (a.file_path, a.is_active) for a in _assets(db, selected)}

        update_scene_timing(selected.id, SceneTimingUpdate(duration=10), db)

        assert {a.id: (a.file_path, a.is_active) for a in _assets(db, selected)} == before
        active = next(a for a in _assets(db, selected) if a.asset_type == "video" and a.is_active)
        assert json.loads(active.metadata_json) == {"quality": "original", "audio_start": 8, "audio_end": 16}
        assert json.loads(prior.metadata_json) == {"audio_start": 8, "audio_end": 14, "source": "older"}
        assert image.metadata_json == '{"image_field": "preserve"}'
        assert prior_file.read_bytes() == b"older variant"
        assert (tmp_path / "scene-1.mp4").read_bytes() == b"original video bytes"
        assert selected.video_path == str(tmp_path / "scene-1.mp4")
        assert active_video_timing_matches(db, selected) is False


def test_noop_does_not_mark_clips_stale_or_touch_following(test_engine, tmp_path):
    with Session(test_engine) as db:
        _, timeline = _timeline(db, tmp_path)
        before = _saved_state(db)
        result = update_scene_timing(timeline[1].id, SceneTimingUpdate(duration=8), db)
        assert result["shifted_scenes"] == 0
        assert len(result["scenes"]) == 1
        assert _saved_state(db) == before


def test_same_duration_model_switch_keeps_current_video_usable(test_engine, tmp_path):
    with Session(test_engine) as db:
        _, timeline = _timeline(db, tmp_path)
        scene = timeline[1]
        result = update_scene_timing(scene.id, SceneTimingUpdate(
            duration=8, video_model="ltx-2.5-fast", resolution="1080p",
        ), db)
        assert result["shifted_scenes"] == 0
        assert scene.status == "done"
        assert scene.error_message is None
        assert active_video_timing_matches(db, scene) is True
        assert json.loads(_assets(db, scene)[0].metadata_json) == {"quality": "original"}


@pytest.mark.parametrize("payload,expected", [
    ({"duration": 15}, "cannot render this 15s"),
    ({"duration": 6.5, "video_model": "ltx-2.5-fast", "resolution": "1080p"}, "whole-second"),
    ({"duration": 10, "video_model": "ltx-2.5-fast", "resolution": "720p"}, "supports 1080p"),
    ({"duration": 10, "video_model": "missing-model"}, "Unknown video model"),
])
def test_invalid_edit_leaves_no_partial_timing_settings_or_asset_writes(test_engine, tmp_path, payload, expected):
    with Session(test_engine) as db:
        _, timeline = _timeline(db, tmp_path)
        before = _saved_state(db)
        with pytest.raises(HTTPException, match=expected) as failure:
            update_scene_timing(timeline[1].id, SceneTimingUpdate(**payload), db)
        assert failure.value.status_code == 400
        db.commit()
        assert _saved_state(db) == before


@pytest.mark.parametrize("blocker", ["busy", "claimed", "saved_job", "unsubmitted_running_job"])
def test_following_busy_or_saved_job_blocks_entire_timing_edit(test_engine, tmp_path, blocker):
    with Session(test_engine) as db:
        project, timeline = _timeline(db, tmp_path)
        following = timeline[2]
        if blocker == "busy":
            following.status = "generating_video"
        elif blocker == "claimed":
            following.generation_run_id = "active-claim"
            following.generation_requested_at = datetime.utcnow()
        else:
            db.add(GenerationJob(project_id=project.id, scene_id=following.id, job_type="video",
                                 status="running", provider="fal",
                                 external_id="saved-provider-job" if blocker == "saved_job" else None))
        db.add(following)
        db.commit()
        before = _saved_state(db)
        with pytest.raises(HTTPException) as failure:
            update_scene_timing(timeline[1].id, SceneTimingUpdate(duration=10), db)
        assert failure.value.status_code == 409
        db.commit()
        assert _saved_state(db) == before


@pytest.mark.parametrize("offset", [-1, 1])
def test_existing_overlap_or_gap_blocks_shifting_without_partial_writes(test_engine, tmp_path, offset):
    with Session(test_engine) as db:
        _, timeline = _timeline(db, tmp_path)
        timeline[2].audio_start += offset
        timeline[2].audio_end += offset
        db.add(timeline[2])
        db.commit()
        before = _saved_state(db)
        with pytest.raises(HTTPException, match="gap or overlap") as failure:
            update_scene_timing(timeline[1].id, SceneTimingUpdate(duration=10), db)
        assert failure.value.status_code == 409
        assert _saved_state(db) == before


def test_stale_version_cannot_be_activated_or_its_warning_dismissed(test_engine, tmp_path):
    with Session(test_engine) as db:
        _, timeline = _timeline(db, tmp_path)
        selected = timeline[1]
        asset = _assets(db, selected)[0]
        update_scene_timing(selected.id, SceneTimingUpdate(duration=10), db)
        before = _saved_state(db)
        with pytest.raises(HTTPException, match="older scene timing") as activation:
            router.activate_scene_asset(selected.id, asset.id, db)
        assert activation.value.status_code == 409
        with pytest.raises(HTTPException, match="older song window") as dismissal:
            router.dismiss_scene_error(selected.id, db)
        assert dismissal.value.status_code == 409
        assert _saved_state(db) == before


def test_restoring_original_window_makes_original_version_usable(test_engine, tmp_path, monkeypatch):
    with Session(test_engine) as db:
        _, timeline = _timeline(db, tmp_path)
        selected = timeline[1]
        asset = _assets(db, selected)[0]
        update_scene_timing(selected.id, SceneTimingUpdate(duration=10), db)
        update_scene_timing(selected.id, SceneTimingUpdate(duration=8), db)
        assert active_video_timing_matches(db, selected) is True
        monkeypatch.setattr(router, "_refresh_scene_last_frame", lambda *_: True)
        router.activate_scene_asset(selected.id, asset.id, db)
        router.dismiss_scene_error(selected.id, db)
        assert selected.status == "done"
        assert selected.error_message is None
        assert json.loads(asset.metadata_json)["audio_end"] == 16


def test_legacy_pointer_is_preserved_as_version_before_window_moves(test_engine, tmp_path):
    with Session(test_engine) as db:
        _, timeline = _timeline(db, tmp_path, with_assets=False)
        selected = timeline[1]
        original_path = selected.video_path
        update_scene_timing(selected.id, SceneTimingUpdate(duration=10), db)
        assets = _assets(db, selected)
        assert len(assets) == 1
        assert assets[0].file_path == selected.video_path == original_path
        assert assets[0].is_active is True
        assert json.loads(assets[0].metadata_json) == {"audio_start": 8, "audio_end": 16}
        assert active_video_timing_matches(db, selected) is False
        assert _assets(db, timeline[0]) == []
        assert (tmp_path / "scene-1.mp4").read_bytes() == b"original video bytes"


def test_shift_refreshes_lyrics_for_changed_song_windows(test_engine, tmp_path):
    with Session(test_engine) as db:
        project, timeline = _timeline(db, tmp_path)
        db.add(Song(project_id=project.id, title="Test song", transcription_json=json.dumps([
            {"word": "first", "start": 9, "end": 10},
            {"word": "moved", "start": 16.5, "end": 17},
            {"word": "following", "start": 20, "end": 21},
        ])))
        db.commit()
        update_scene_timing(timeline[1].id, SceneTimingUpdate(duration=10), db)
        assert "first" in timeline[1].lyrics_segment
        assert "moved" in timeline[1].lyrics_segment
        assert "following" in timeline[2].lyrics_segment
        assert timeline[3].lyrics_segment == ""


def test_late_failure_rolls_back_earlier_scene_and_legacy_version_writes(test_engine, tmp_path, monkeypatch):
    with Session(test_engine) as db:
        _, timeline = _timeline(db, tmp_path, with_assets=False)
        before = _saved_state(db)
        real_preserve = router.preserve_video_timing
        def fail_on_following(db, scene):
            if scene.id == timeline[2].id:
                raise RuntimeError("storage metadata failure")
            return real_preserve(db, scene)
        monkeypatch.setattr(router, "preserve_video_timing", fail_on_following)
        with pytest.raises(HTTPException, match="Could not update scene timing") as failure:
            update_scene_timing(timeline[1].id, SceneTimingUpdate(duration=10), db)
        assert failure.value.status_code == 500
        db.commit()
        assert _saved_state(db) == before


@pytest.mark.parametrize("metadata,expected", [
    ({"audio_start": 8, "audio_end": 16}, True),
    ({"audio_start": 9, "audio_end": 17}, False),
    ({"audio_start": 8, "audio_end": 15}, False),
    ({"audio_start": "invalid"}, False),
    ([], False),
    ({}, True),
])
def test_video_timing_matches_requires_both_recorded_boundaries(metadata, expected):
    scene = Scene(project_id=1, order=1, audio_start=8, audio_end=16)
    asset = SceneAsset(scene_id=1, asset_type="video", file_path="unused.mp4",
                       metadata_json=json.dumps(metadata))
    assert video_timing_matches(scene, asset) is expected
