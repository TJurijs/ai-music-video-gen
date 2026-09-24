"""Exercise real local media assembly without contacting a provider."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlmodel import Session

from app.config import settings
from app.models import Project, Scene, SceneAsset, Song
from app.services import assembly
from app.services.assembly import assemble_project


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
@pytest.mark.parametrize("song_duration", [2.96, 4.0, 5.1])
def test_last_full_length_clip_is_trimmed_at_song_end(test_engine, tmp_path, monkeypatch, song_duration):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    clip = tmp_path / "clip.mp4"
    song_file = tmp_path / "song.wav"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=64x64:r=24:d=2", "-c:v", "libx264", str(clip),
    ], check=True, capture_output=True, timeout=30)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        f"sine=frequency=440:duration={song_duration}", str(song_file),
    ], check=True, capture_output=True, timeout=30)
    with Session(test_engine) as db:
        project = Project(name="Fixed clips", target_scene_duration=2)
        db.add(project)
        db.commit()
        project_id = project.id
        db.add(Song(project_id=project_id, title="Song", file_path=str(song_file), duration=song_duration))
        for index in range(2):
            db.add(Scene(project_id=project_id, order=index + 1, audio_start=index * 2,
                         audio_end=(index + 1) * 2, status="done", video_path=str(clip)))
        db.commit()
    output = assemble_project(project_id, test_engine)
    assert Path(output).exists()
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
        "-of", "json", output,
    ], check=True, capture_output=True, text=True, timeout=30)
    streams = json.loads(probe.stdout)["streams"]
    assert {stream["codec_type"] for stream in streams} == {"audio", "video"}
    for stream in streams:
        assert abs(float(stream["duration"]) - min(song_duration, 4.0)) <= 1 / 24 + 0.01


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required")
def test_chained_scenes_keep_every_frame_and_planned_duration(test_engine, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    clip = tmp_path / "moving.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "testsrc2=s=64x64:r=24:d=2", "-c:v", "libx264", str(clip),
    ], check=True, capture_output=True, timeout=30)
    with Session(test_engine) as db:
        project = Project(name="Chained timing")
        db.add(project)
        db.commit()
        project_id = project.id
        for index in range(3):
            db.add(Scene(
                project_id=project_id, order=index + 1, audio_start=index * 2,
                audio_end=(index + 1) * 2, status="done", video_path=str(clip),
                chain_from_prev=index > 0,
            ))
        db.commit()

    output = assemble_project(project_id, test_engine)
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=duration,nb_read_frames", "-of", "json", output,
    ], check=True, capture_output=True, text=True, timeout=30)
    stream = json.loads(probe.stdout)["streams"][0]
    assert int(stream["nb_read_frames"]) == 144
    assert float(stream["duration"]) == pytest.approx(6.0, abs=0.001)


@pytest.mark.parametrize("missing_kind", ["no_path", "absent_file", "directory"])
def test_missing_active_scene_video_stops_assembly_before_encode(
    test_engine, tmp_path, monkeypatch, missing_kind,
):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    existing_clip = tmp_path / "existing.mp4"
    existing_clip.write_bytes(b"An existing active clip")
    missing_path = {
        "no_path": None,
        "absent_file": str(tmp_path / "missing.mp4"),
        "directory": str(tmp_path),
    }[missing_kind]
    with Session(test_engine) as db:
        project = Project(name="Missing clip")
        db.add(project)
        db.commit()
        project_id = project.id
        for index, path in enumerate([str(existing_clip), missing_path, str(existing_clip)]):
            db.add(Scene(
                project_id=project_id, order=index + 1, audio_start=index * 2,
                audio_end=(index + 1) * 2, status="done", video_path=path,
            ))
        db.commit()
    prior_export = tmp_path / str(project_id) / "Missing clip_final.mp4"
    prior_export.parent.mkdir(exist_ok=True)
    prior_export.write_bytes(b"previous completed export")

    def no_encode(_cmd):
        pytest.fail("Missing active clips must be reported before encoding")

    monkeypatch.setattr(assembly, "_run_ffmpeg", no_encode)
    with pytest.raises(RuntimeError, match=r"missing for scene\(s\) 2"):
        assemble_project(project_id, test_engine)
    assert prior_export.read_bytes() == b"previous completed export"


def test_assembly_rejects_same_length_video_from_previous_song_window(
    test_engine, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    clip = tmp_path / "old_window.mp4"
    clip.write_bytes(b"previous render")
    with Session(test_engine) as db:
        project = Project(name="Moved timing")
        db.add(project)
        db.commit()
        project_id = project.id
        scene = Scene(
            project_id=project_id, order=1, audio_start=2, audio_end=4,
            status="done", video_path=str(clip),
        )
        db.add(scene)
        db.commit()
        db.add(SceneAsset(
            scene_id=scene.id, asset_type="video", file_path=str(clip),
            metadata_json=json.dumps({"duration": 2, "audio_start": 0, "audio_end": 2}),
        ))
        db.commit()

    def no_encode(_cmd):
        pytest.fail("Stale song windows must be rejected before encoding")

    monkeypatch.setattr(assembly, "_run_ffmpeg", no_encode)
    with pytest.raises(RuntimeError, match="earlier song window"):
        assemble_project(project_id, test_engine)
