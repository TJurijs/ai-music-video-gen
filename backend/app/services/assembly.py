"""Final video assembly: concatenate scene clips + add audio track via ffmpeg.

Uses raw subprocess calls instead of ffmpeg-python for reliability — the
latter mishandles multi-input stream mapping for the audio mux step on
some Windows builds and was producing 1-frame outputs.

Scenes are independent shots joined by hard cuts — straight concat, no
inpoint/outpoint trim, no fades. Total assembled video duration ≈ sum of
clip durations, capped at the song length by `-shortest`.

Concat path: ffmpeg's concat FILTER (not the demuxer). Different video
models render at different resolutions / fps / SAR / pixel formats —
e.g. scene N might be 864×496 from Seedance R2V and scene N+1 might be
1284×716 from Seedance I2V. The concat DEMUXER requires identical specs
across inputs and produces visible FREEZES on mismatched seams (the
decoder holds the last good frame until timestamps re-align). The
concat FILTER decodes each input and lets us scale+pad+fps-force each
one to a common target spec before concatenating — slower but robust.
"""

import os
import subprocess
from typing import Optional
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


def _probe_video_dims(path: str) -> Optional[tuple[int, int, float]]:
    """ffprobe one .mp4 → (width, height, fps).

    Returns None if ffprobe isn't available or the file is unreadable; the
    caller substitutes a fallback target. Cheap (single-file metadata read,
    no decode), fine to call N times during assembly setup."""
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate",
                "-of", "default=noprint_wrappers=1",
                path,
            ],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return None
        kv: dict = {}
        for line in proc.stdout.strip().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k.strip()] = v.strip()
        w = int(kv["width"]); h = int(kv["height"])
        rf = kv.get("r_frame_rate", "24/1")
        if "/" in rf:
            num, den = rf.split("/")
            fps = float(num) / float(den) if float(den) > 0 else 24.0
        else:
            fps = float(rf)
        return (w, h, fps)
    except Exception:
        return None


def _pick_common_dims(
    dims: list[tuple[int, int, float]],
) -> tuple[int, int, int]:
    """From a list of (width, height, fps) per clip, pick the target spec
    every clip will be normalized to.

    Strategy: take the MINIMUM width and MINIMUM height across all clips.
    That's the "lowest common quality" — every clip scales down or stays
    the same; nothing has to upscale (which would look worse than the
    sharpest clip's native quality anyway). fps gets forced to 24 since
    every video model in this codebase renders at 24fps natively; this
    just makes the timebase explicit so concat-filter doesn't drift.

    Returns (target_w, target_h, target_fps). Both dimensions are rounded
    DOWN to the nearest even number — libx264 + yuv420p requires even
    pixel dimensions, otherwise the encode 422s.
    """
    target_w = min(d[0] for d in dims)
    target_h = min(d[1] for d in dims)
    target_w -= target_w % 2
    target_h -= target_h % 2
    return (target_w, target_h, 24)


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

    # Collect every on-disk clip in scene order, alongside per-clip flags
    # (whether to trim the duplicated first frame of chained scenes).
    clips: list[tuple[str, bool]] = []  # (path, is_chained)
    for scene in done_scenes:
        if scene.video_path and os.path.exists(scene.video_path):
            clips.append((scene.video_path, bool(scene.chain_from_prev)))
    if not clips:
        raise RuntimeError("No video files found on disk for any done scene")

    # Probe each clip's dimensions; if any probe fails we still get a sane
    # fallback. We need this BEFORE building the filtergraph because the
    # target dims become a literal in the scale= filter.
    dims_results = [_probe_video_dims(p) for p, _ in clips]
    valid_dims = [d for d in dims_results if d is not None]
    if not valid_dims:
        # Shouldn't happen in practice — ffprobe failing on every clip means
        # something's badly broken with the install. Fall back to 720p 16:9
        # so assembly still produces something rather than 500ing out.
        target_w, target_h, target_fps = 1280, 720, 24
        print("[assembly] WARNING: couldn't probe any clip dimensions, "
              "falling back to 1280x720@24")
    else:
        target_w, target_h, target_fps = _pick_common_dims(valid_dims)
        # Log what we're normalizing to + which clips deviate, so the user
        # can see why some clips are being downscaled.
        unique = {(w, h) for (w, h, _) in valid_dims}
        if len(unique) > 1:
            print(
                f"[assembly] clip dimensions vary across scenes "
                f"({sorted(unique)}); normalizing all to {target_w}x{target_h}@{target_fps}fps "
                f"via concat filter (prevents freezing at scene boundaries)."
            )
        else:
            print(
                f"[assembly] all clips at {next(iter(unique))[0]}x{next(iter(unique))[1]}; "
                f"normalizing to {target_w}x{target_h}@{target_fps}fps."
            )

    # Build the ffmpeg command:
    #   -i <clip1> -i <clip2> ... -filter_complex "<per-clip normalize>;<concat>" -map "[out]" ...
    #
    # Each clip gets a filter chain:
    #   [N:v]<trim?>,scale=W:H:force_original_aspect_ratio=decrease,pad=W:H:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=24,format=yuv420p[vN]
    #
    #   - trim=start=0.04,setpts=PTS-STARTPTS: only on chained scenes, skips
    #     the duplicated first frame (the prev's extracted last frame is
    #     this clip's first_frame, so the concat would show it twice).
    #     setpts resets timestamps after the trim or concat drifts.
    #   - scale + pad with force_original_aspect_ratio=decrease: fit each
    #     clip into the target box preserving its native aspect. If a clip's
    #     aspect doesn't match the target, padding adds black bars instead
    #     of stretching. In practice all clips in a project share an aspect
    #     so padding is a no-op — this is defense-in-depth.
    #   - setsar=1: force square pixel aspect ratio. Some models emit
    #     non-square SAR (e.g. 1:1 from 1080×1080 but encoded as 1920×1080
    #     with SAR 9:16). Normalizing here prevents the concat filter from
    #     refusing to mix inputs with different SARs.
    #   - fps=24: standardize timebase. All our video models render 24fps
    #     natively but vary in how they EXPOSE the timestamps (some emit
    #     30/1.001, some 24/1, some 25/1). Forcing 24 makes everything
    #     comparable.
    #   - format=yuv420p: standard pixel format for h264 + browser playback.
    inputs: list[str] = []
    filter_parts: list[str] = []
    for i, (path, is_chained) in enumerate(clips):
        inputs.extend(["-i", path])
        chain = (
            f"[{i}:v]"
            + ("trim=start=0.04,setpts=PTS-STARTPTS," if is_chained else "")
            + f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            + f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
            + f"setsar=1,fps={target_fps},format=yuv420p"
            + f"[v{i}]"
        )
        filter_parts.append(chain)
    concat_inputs = "".join(f"[v{i}]" for i in range(len(clips)))
    filter_complex = (
        ";".join(filter_parts)
        + f";{concat_inputs}concat=n={len(clips)}:v=1:a=0[out]"
    )

    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in project.name)
    output_path = os.path.join(output_dir, f"{safe_name}_final.mp4")

    has_audio = bool(song and song.file_path and os.path.exists(song.file_path))

    if has_audio:
        # Step 1 — concat-filter all clips into a silent intermediate.
        # The concat filter normalizes each input (scale/pad/setsar/fps/format)
        # before concatenating, which is what fixes the freeze-on-spec-mismatch
        # bug the concat demuxer had.
        intermediate = os.path.join(output_dir, "assembled_noaudio.mp4")
        _run_ffmpeg([
            "ffmpeg", "-y", "-v", "warning",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-an",
            intermediate,
        ])

        # Step 2 — mux intermediate video + song audio with explicit -map flags
        # so streams are unambiguous. -shortest cuts to the shorter of the two.
        # `-movflags +faststart` relocates the moov atom to the start of the
        # file so browsers can seek to any timestamp without downloading the
        # entire file first.
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
        # No audio — single-pass concat-filter with re-encode straight to output.
        _run_ffmpeg([
            "ffmpeg", "-y", "-v", "warning",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[out]",
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
