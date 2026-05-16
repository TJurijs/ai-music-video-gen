"""Final video assembly: concatenate scene clips + add audio track via ffmpeg.

Uses raw subprocess calls instead of ffmpeg-python for reliability — the
latter mishandles multi-input stream mapping for the audio mux step on
some Windows builds and was producing 1-frame outputs.

Scenes are independent shots joined by hard cuts — straight concat, no
inpoint/outpoint trim, no fades. Total assembled video duration ≈ sum of
clip durations, capped at the song length by `-shortest`.
"""

import os
import subprocess
from sqlmodel import Session, select
from app.config import settings
from app.models import Project, Scene, Song


def _run_ffmpeg(cmd: list[str]) -> None:
    """Run ffmpeg, raising with stderr on failure so callers see real errors."""
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        # Last 1.5KB of stderr usually carries the actionable error
        tail = (proc.stderr or "")[-1500:]
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}): ...{tail}")


async def assemble_project(project_id: int, engine) -> str:
    """Concatenate all done scenes, mux with song audio. Returns output path."""
    with Session(engine) as db:
        project = db.get(Project, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        scenes = db.exec(
            select(Scene)
            .where(Scene.project_id == project_id)
            .order_by(Scene.order)
        ).all()

        song = db.exec(
            select(Song).where(Song.project_id == project_id)
        ).first()

    done_scenes = [s for s in scenes if s.status == "done"]
    if not done_scenes:
        raise RuntimeError("No completed scenes to assemble")

    output_dir = os.path.join(settings.storage_dir, str(project_id))
    os.makedirs(output_dir, exist_ok=True)

    # Build the concat list file. ffmpeg's concat demuxer needs forward
    # slashes on Windows or escaped backslashes — using forward slashes is
    # simpler and works everywhere.
    #
    # For chained scenes (chain_from_prev=True) the clip's first frame is
    # identical to the previous clip's last frame — skip it with inpoint so
    # we don't get a duplicate frame at every seam. One frame at 24 fps ≈
    # 0.042 s; 0.04 s is a safe trim that removes the dupe without cutting
    # into real content.
    concat_path = os.path.join(output_dir, "concat.txt")
    file_count = 0
    with open(concat_path, "w", encoding="utf-8") as f:
        for scene in done_scenes:
            video = scene.video_path
            if video and os.path.exists(video):
                normalized = os.path.abspath(video).replace("\\", "/")
                f.write(f"file '{normalized}'\n")
                if scene.chain_from_prev:
                    f.write("inpoint 0.04\n")
                file_count += 1
    if file_count == 0:
        raise RuntimeError("No video files found on disk for any done scene")

    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in project.name)
    output_path = os.path.join(output_dir, f"{safe_name}_final.mp4")

    has_audio = bool(song and song.file_path and os.path.exists(song.file_path))

    if has_audio:
        # Step 1 — concat video-only into intermediate
        intermediate = os.path.join(output_dir, "assembled_noaudio.mp4")
        _run_ffmpeg([
            "ffmpeg", "-y", "-v", "warning",
            "-f", "concat", "-safe", "0",
            "-i", concat_path,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-an",
            intermediate,
        ])

        # Step 2 — mux intermediate video + song audio with explicit -map flags
        # so streams are unambiguous. -shortest cuts to the shorter of the two.
        # `-movflags +faststart` relocates the moov atom to the start of the
        # file so browsers can seek to any timestamp without downloading the
        # entire file first. Without this, scrubbing past the buffered region
        # silently fails (browser rejects the seek), which is the symptom of
        # "the playhead moves but the video doesn't actually change position".
        _run_ffmpeg([
            "ffmpeg", "-y", "-v", "warning",
            "-i", intermediate,
            "-i", song.file_path,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "320k",
            "-movflags", "+faststart",
            "-shortest",
            output_path,
        ])

        try:
            os.remove(intermediate)
        except OSError:
            pass
    else:
        # No audio — single-pass concat with re-encode
        _run_ffmpeg([
            "ffmpeg", "-y", "-v", "warning",
            "-f", "concat", "-safe", "0",
            "-i", concat_path,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path,
        ])

    return output_path


def get_scene_video_path(scene: Scene) -> str | None:
    """Return the best available video file for a scene."""
    if scene.video_path and os.path.exists(scene.video_path):
        return scene.video_path
    return None
