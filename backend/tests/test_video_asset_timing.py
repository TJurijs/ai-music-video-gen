import json

from sqlmodel import Session

from app.models import Project, Scene
from app.services.generation_service import _save_asset


def test_video_asset_captures_scene_song_window_without_modifying_metadata(test_engine):
    metadata = {"duration": 6.096, "route": "local-retime"}
    with Session(test_engine) as db:
        project = Project(name="Song window")
        db.add(project)
        db.commit()
        scene = Scene(project_id=project.id, order=1, audio_start=210, audio_end=216.096)
        db.add(scene)
        db.commit()
        asset = _save_asset(db, scene, "video", "retimed.mp4", "ltx-2.5-fast", 0, metadata=metadata)
        assert json.loads(asset.metadata_json) == {
            "duration": 6.096, "route": "local-retime",
            "audio_start": 210, "audio_end": 216.096,
        }
    assert metadata == {"duration": 6.096, "route": "local-retime"}


def test_video_asset_keeps_explicit_original_window(test_engine):
    with Session(test_engine) as db:
        project = Project(name="Saved request window")
        db.add(project)
        db.commit()
        scene = Scene(project_id=project.id, order=1, audio_start=10, audio_end=15)
        db.add(scene)
        db.commit()
        asset = _save_asset(
            db, scene, "video", "saved.mp4", "ltx-2.5-fast", 0,
            metadata={"audio_start": 0, "audio_end": 5},
        )
        assert json.loads(asset.metadata_json) == {"audio_start": 0, "audio_end": 5}
