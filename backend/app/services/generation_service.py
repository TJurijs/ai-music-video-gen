"""Generation orchestrator: image → video → lipsync per scene.

Video generation goes through OpenRouter (the 3 supported models all live
there). Lipsync is a post-hoc step on fal since none of our video models
accept audio input. Runs as a FastAPI BackgroundTask.
"""

import json
import os
from datetime import datetime
from sqlmodel import Session, select
from app.config import settings, VIDEO_MODELS, LIPSYNC_MODELS
from app.models import Scene, SceneAsset, GenerationJob, Project, Song, Character
from app.services import openrouter, fal_client, pricing
from app.services.versioning import make_active


def _sync_scene_pointer(scene: Scene, asset_type: str, file_path: str | None) -> None:
    """Mirror an active SceneAsset's file_path onto the matching `Scene.*_path`
    convenience field that other code reads directly."""
    if asset_type == "image":
        scene.reference_image_path = file_path
    elif asset_type == "video":
        scene.video_path = file_path
    elif asset_type == "lipsync":
        scene.lipsync_path = file_path


def _save_asset(
    db: Session, scene: Scene, asset_type: str, file_path: str,
    model_used: str, cost_usd: float, cost_detail: str | None = None,
    metadata: dict | None = None,
) -> SceneAsset:
    """Insert a new SceneAsset, deactivate prior actives of the same type for
    this scene, and update the Scene's compat path pointer to the new file."""
    asset = SceneAsset(
        scene_id=scene.id,
        asset_type=asset_type,
        file_path=file_path,
        model_used=model_used,
        cost_usd=cost_usd,
        cost_detail=cost_detail,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    make_active(
        db,
        target=asset,
        siblings_filter=[
            SceneAsset.scene_id == scene.id,
            SceneAsset.asset_type == asset_type,
        ],
        on_active_change=lambda a: (
            _sync_scene_pointer(scene, asset_type, a.file_path),
            db.add(scene),
        ),
    )
    db.commit()
    db.refresh(asset)
    return asset


def _activate_asset(db: Session, asset: SceneAsset) -> None:
    """Mark this asset as the active one for its (scene, asset_type)."""
    scene = db.get(Scene, asset.scene_id)
    make_active(
        db,
        target=asset,
        siblings_filter=[
            SceneAsset.scene_id == asset.scene_id,
            SceneAsset.asset_type == asset.asset_type,
        ],
        on_active_change=lambda a: scene and (
            _sync_scene_pointer(scene, a.asset_type, a.file_path),
            db.add(scene),
        ),
    )
    db.commit()


def _storage(project_id: int, *parts) -> str:
    path = os.path.join(settings.storage_dir, str(project_id), *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _extract_last_frame(video_path: str, dest_path: str) -> bool:
    """Extract the last frame of `video_path` to `dest_path` as a JPG.

    Used by scene-chaining: when scene N+1 has `chain_from_prev=True`, its
    video gen takes scene N's actual last rendered frame (this JPG) as its
    `first_frame_path`, producing a pixel-perfect handoff at the seam.

    Uses ffmpeg's `-sseof` to seek relative to the END of the file — far
    cheaper than transcoding through the whole video.

    Returns True on success, False on failure (e.g. ffmpeg not installed,
    video unreadable). Failure is non-fatal: chaining simply falls back to
    the planned still on consumption.
    """
    import subprocess
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-sseof", "-0.5",         # seek to 0.5s before EOF
                "-i", video_path,
                "-update", "1",
                "-frames:v", "1",
                "-q:v", "2",              # high JPEG quality
                dest_path,
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        if proc.returncode != 0 or not os.path.exists(dest_path):
            print(f"[chain] last-frame extract failed for {video_path}: {(proc.stderr or '')[-300:]}")
            return False
        return True
    except Exception as e:
        print(f"[chain] last-frame extract exception for {video_path}: {e}")
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

import asyncio


async def generate_scene(scene_id: int, engine, phase: str = "all") -> None:
    """Run the scene pipeline.

    phase:
      - "image":  reference still only (cheap, ~$0.04). Lets the user review
                  before paying for video.
      - "video":  video gen + optional lipsync. Skips image step if a
                  reference already exists; auto-generates one if missing.
      - "all":    image → video → optional lipsync (legacy behavior).
    """
    with Session(engine) as db:
        scene = db.get(Scene, scene_id)
        if not scene:
            return
        # Clear any stale cancel flag from a previous run
        if scene.cancel_requested:
            scene.cancel_requested = False
            db.add(scene); db.commit()
        try:
            await _run_pipeline(scene, db, engine, phase=phase)
        except asyncio.CancelledError:
            # User pressed Stop — mark cancelled, don't propagate
            db.refresh(scene)
            scene.status = "cancelled"
            scene.cancel_requested = False
            db.add(scene); db.commit()
        except Exception as e:
            scene.status = "error"
            scene.error_message = str(e)[:500]
            db.add(scene)
            db.commit()
            raise


def _check_cancelled(engine, scene_id: int) -> bool:
    """Re-read the scene from DB to see if cancel was requested mid-flight."""
    with Session(engine) as s:
        sc = s.get(Scene, scene_id)
        return bool(sc and sc.cancel_requested)


async def _run_pipeline(scene: Scene, db: Session, engine, phase: str = "all") -> None:
    model_cfg = VIDEO_MODELS.get(scene.video_model, VIDEO_MODELS["kling-v3.0-pro"])

    if phase == "image":
        await _generate_image(scene, db)
        if _check_cancelled(engine, scene.id):
            raise asyncio.CancelledError()
        scene.status = "image_ready"
        db.add(scene); db.commit()
        return

    if phase == "lipsync":
        # User-driven explicit lipsync. Requires an existing video.
        if not scene.video_path or not os.path.exists(scene.video_path):
            raise RuntimeError("Generate a video first before running lipsync.")
        await _run_lipsync(scene, db)
        scene.status = "done"
        db.add(scene); db.commit()
        return

    # phases "video" and "all" both need a reference image first
    if not scene.reference_image_path:
        await _generate_image(scene, db)
        if _check_cancelled(engine, scene.id):
            raise asyncio.CancelledError()

    await _generate_video_openrouter(scene, db, model_cfg, engine=engine)

    scene.status = "done"
    db.add(scene)
    db.commit()


# ---------------------------------------------------------------------------
# Image generation (OpenRouter)
# ---------------------------------------------------------------------------

async def _generate_image(scene: Scene, db: Session) -> None:
    scene.status = "generating_image"
    db.add(scene); db.commit()

    prompt = scene.image_prompt or scene.description or "Cinematic music video scene"
    prompt = _append_style(prompt, db, scene.project_id)

    # Aspect ratio matters: this image becomes the first frame of the video,
    # which is rendered at project.aspect_ratio. If the image is 1:1 and the
    # video is 16:9, the model has to crop or letterbox — looks bad.
    project = db.get(Project, scene.project_id)
    aspect = project.aspect_ratio if project else "16:9"

    # Find any characters mentioned in the prompt → use their portraits as references
    ref_paths = _find_character_references(scene, db, prompt)

    cost, detail = pricing.image_cost(scene.image_model)
    if ref_paths:
        detail += f" + {len(ref_paths)} character ref(s)"
    job = _create_job(db, scene, "image", "openrouter", cost, detail)
    try:
        image_bytes = await openrouter.generate_image(
            prompt, scene.image_model, reference_image_paths=ref_paths,
            aspect_ratio=aspect,
        )
        # Reserve an asset row first so we can use its id in the filename;
        # then write the bytes to that filename.
        ts = int(datetime.utcnow().timestamp())
        dest = _storage(scene.project_id, "images", f"scene_{scene.id}_{ts}.jpg")
        with open(dest, "wb") as f:
            f.write(image_bytes)
        _save_asset(
            db, scene, "image", dest,
            model_used=scene.image_model, cost_usd=cost, cost_detail=detail,
            metadata={"prompt": prompt, "char_refs": len(ref_paths or [])},
        )
        job.status = "completed"
        job.result_path = dest
    except Exception as e:
        job.status = "failed"; job.error = str(e); raise
    finally:
        job.completed_at = datetime.utcnow()
        db.add(job); db.commit()


def _find_character_references(scene: Scene, db: Session, prompt: str) -> list[str]:
    """Return reference image paths for any characters whose name appears in the prompt."""
    chars = db.exec(
        select(Character).where(Character.project_id == scene.project_id)
    ).all()
    haystack = f"{prompt} {scene.description or ''}".lower()
    refs: list[str] = []
    for c in chars:
        if c.reference_image_path and os.path.exists(c.reference_image_path):
            if c.name.lower() in haystack:
                refs.append(c.reference_image_path)
    return refs


# ---------------------------------------------------------------------------
# Video generation — OpenRouter (text/image to video)
# ---------------------------------------------------------------------------

async def _generate_video_openrouter(scene: Scene, db: Session, model_cfg: dict, engine=None) -> None:
    scene.status = "generating_video"
    db.add(scene); db.commit()

    # Constrain duration to what the model supports.
    raw_duration = int(scene.audio_end - scene.audio_start)
    durations = model_cfg.get("durations") or [model_cfg.get("max_duration", 8)]
    duration = _closest_supported(raw_duration, durations)

    # Validate resolution against the model; fall back to its first option.
    supported_res = model_cfg.get("resolutions") or ["720p"]
    resolution = scene.resolution if scene.resolution in supported_res else supported_res[0]

    prompt = scene.video_prompt or scene.description or "Cinematic music video scene"
    prompt = _append_style(prompt, db, scene.project_id)
    project = db.get(Project, scene.project_id)
    aspect_ratio = project.aspect_ratio if project else "16:9"
    if aspect_ratio not in (model_cfg.get("aspects") or [aspect_ratio]):
        aspect_ratio = (model_cfg.get("aspects") or ["16:9"])[0]

    # Character refs: portraits whose name appears in the prompt go to
    # input_references so the model preserves identity through the clip.
    char_refs = _find_character_references(scene, db, prompt)

    # Resolve first_frame_path. Default: this scene's planned reference still.
    # Chained: previous scene's EXTRACTED LAST FRAME — its actual final
    # rendered pixels, not its planned still. This produces an invisible
    # handoff at the seam (next clip opens on exact prev-clip-final pixels).
    first_frame_path = scene.reference_image_path
    if scene.chain_from_prev:
        prev_scene = db.exec(
            select(Scene).where(
                Scene.project_id == scene.project_id,
                Scene.order == scene.order - 1,
            )
        ).first()
        if not prev_scene:
            raise RuntimeError(
                f"Scene {scene.id} has chain_from_prev=True but no previous "
                f"scene exists at order {scene.order - 1}."
            )
        if not prev_scene.extracted_last_frame_path or not os.path.exists(prev_scene.extracted_last_frame_path):
            raise RuntimeError(
                f"Scene {scene.id} is chained from scene {prev_scene.id} (order "
                f"{prev_scene.order}), but that scene's video hasn't been "
                f"rendered yet (no extracted last frame). Generate scene "
                f"{prev_scene.order} first, then retry."
            )
        first_frame_path = prev_scene.extracted_last_frame_path

    # generate_audio is always false now — the user's song is the audio track.
    cost, detail = pricing.video_cost(
        scene.video_model, duration, resolution, with_audio=False,
    )
    if char_refs:
        detail += f" + {len(char_refs)} char ref(s)"
    if scene.chain_from_prev:
        detail += " + chained"
    job = _create_job(db, scene, "video", "openrouter", cost, detail)
    try:
        job_id = await openrouter.submit_video_job(
            prompt=prompt,
            model_id=model_cfg["model_id"],
            duration=duration,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            generate_audio=False,
            first_frame_path=first_frame_path,
            reference_image_paths=char_refs,
        )
        scene.openrouter_job_id = job_id
        job.external_id = job_id
        job.status = "running"
        db.add(scene); db.add(job); db.commit()

        scene_id = scene.id
        is_cancelled = (lambda: _check_cancelled(engine, scene_id)) if engine else None
        video_url = await openrouter.poll_video_job(job_id, is_cancelled=is_cancelled)
        ts = int(datetime.utcnow().timestamp())
        dest = _storage(scene.project_id, "videos", f"scene_{scene.id}_{ts}.mp4")
        await openrouter.download_file(video_url, dest)

        _save_asset(
            db, scene, "video", dest,
            model_used=scene.video_model, cost_usd=cost, cost_detail=detail,
            metadata={
                "duration": duration, "resolution": resolution,
                "aspect": aspect_ratio, "audio": scene.generate_audio,
                "char_refs": len(char_refs or []),
                "chained_from": prev_scene.id if scene.chain_from_prev else None,
            },
        )

        # Extract the actual last frame so the NEXT scene can chain from it.
        # We do this for every video gen (not just when the next scene is
        # chained), so enabling chaining downstream doesn't require re-
        # rendering this scene. Stored alongside the video at a stable path.
        last_frame_dest = _storage(
            scene.project_id, "extracted", f"scene_{scene.order}_last.jpg",
        )
        if _extract_last_frame(dest, last_frame_dest):
            scene.extracted_last_frame_path = last_frame_dest

        job.status = "completed"
        job.result_url = video_url
        job.result_path = dest
    except Exception as e:
        job.status = "failed"; job.error = str(e); raise
    finally:
        job.completed_at = datetime.utcnow()
        db.add(job); db.add(scene); db.commit()


def _closest_supported(value: int, options: list[int]) -> int:
    if not options:
        return value
    return min(options, key=lambda x: (abs(x - value), x))


def _append_style(prompt: str, db: Session, project_id: int) -> str:
    """Append the project's style/mood as a guaranteed suffix on every render
    prompt so the look stays consistent across images and videos regardless
    of what the LLM put in the per-scene prompt.

    Idempotent: skipped if the prompt already contains a [STYLE] block (the
    new structured tagged format), since the planner has already woven the
    project style into that block. Old-format prompts (free-form text) still
    get the suffix as a safety net."""
    project = db.get(Project, project_id)
    style = (project.style or "").strip() if project else ""
    if not style:
        return prompt
    if "[STYLE]" in prompt:
        return prompt
    return f"{prompt}\n\nVisual style guide (apply throughout): {style}"


# ---------------------------------------------------------------------------
# Post-hoc lipsync (LatentSync on fal)
# ---------------------------------------------------------------------------

async def _run_lipsync(scene: Scene, db: Session) -> None:
    if not settings.fal_api_key:
        raise RuntimeError("FAL_API_KEY required for lipsync. Add it to .env.")

    scene.status = "lipsync"
    db.add(scene); db.commit()

    lipsync_cfg = LIPSYNC_MODELS.get(scene.lipsync_model, LIPSYNC_MODELS["fal-latentsync"])
    lipsync_model_id = lipsync_cfg["model_id"]

    cost, detail = pricing.lipsync_cost(scene.lipsync_model)
    job = _create_job(db, scene, "lipsync", "fal", cost, detail)
    try:
        if not scene.video_path or not os.path.exists(scene.video_path):
            raise RuntimeError("No video found — generate a video first before lipsync.")
        audio_path = await _extract_audio_segment(scene, db)
        video_url = await fal_client.upload_file(scene.video_path)
        audio_url = await fal_client.upload_file(audio_path)

        request_id = await fal_client.submit(
            lipsync_model_id,
            {"video_url": video_url, "audio_url": audio_url},
        )
        result = await fal_client.poll(lipsync_model_id, request_id)
        out_url = fal_client.extract_video_url(result)
        if not out_url:
            raise RuntimeError(f"{lipsync_cfg['name']} returned no URL: {str(result)[:300]}")

        ts = int(datetime.utcnow().timestamp())
        dest = _storage(scene.project_id, "lipsync", f"scene_{scene.id}_{ts}.mp4")
        await openrouter.download_file(out_url, dest)

        # Lipsync output is a NEW VIDEO VARIANT, not a separate concept.
        # Save it as a `video` asset so it shows up alongside the source in
        # the video chooser; the model label includes "+ LatentSync" so the
        # user can see at a glance it's a post-processed variant.
        prior_video = db.exec(
            select(SceneAsset).where(
                SceneAsset.scene_id == scene.id,
                SceneAsset.asset_type == "video",
                SceneAsset.is_active == True,  # noqa: E712
            )
        ).first()
        source_label = prior_video.model_used if prior_video else scene.video_model
        lipsync_label = LIPSYNC_MODELS.get(scene.lipsync_model, {}).get("name", scene.lipsync_model)
        combined_label = f"{source_label} + {lipsync_label}"
        _save_asset(
            db, scene, "video", dest,
            model_used=combined_label, cost_usd=cost, cost_detail=detail,
            metadata={
                "lipsynced": True,
                "lipsync_model": scene.lipsync_model,
                "source_video_asset_id": prior_video.id if prior_video else None,
            },
        )
        job.status = "completed"
        job.result_path = dest
    except Exception as e:
        job.status = "failed"; job.error = str(e); raise
    finally:
        job.completed_at = datetime.utcnow()
        db.add(job); db.add(scene); db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _extract_audio_segment(scene: Scene, db: Session) -> str:
    """Extract scene's audio range from the song file via ffmpeg."""
    import ffmpeg as ff

    song = db.exec(select(Song).where(Song.project_id == scene.project_id)).first()
    if not song or not song.file_path:
        raise RuntimeError("No song file found for audio extraction")

    dest = _storage(scene.project_id, "audio_segments", f"scene_{scene.id}.mp3")
    duration = scene.audio_end - scene.audio_start
    (
        ff.input(song.file_path, ss=scene.audio_start, t=duration)
        .output(dest, acodec="libmp3lame", q=2)
        .overwrite_output()
        .run(quiet=True)
    )
    return dest


def _create_job(
    db: Session, scene: Scene, job_type: str, provider: str,
    cost_usd: float = 0.0, cost_detail: str | None = None,
) -> GenerationJob:
    job = GenerationJob(
        project_id=scene.project_id,
        scene_id=scene.id,
        job_type=job_type,
        provider=provider,
        status="pending",
        cost_usd=cost_usd,
        cost_detail=cost_detail,
    )
    db.add(job); db.commit(); db.refresh(job)
    return job
