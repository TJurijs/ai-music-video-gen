"""Generation orchestrator: image → video per scene.

Two video paths, picked per scene:

1) OpenRouter image-to-video (default) — most scenes. Sends a first_frame
   (the scene's reference still or, when chained, the prev scene's
   extracted last frame) + optional input_references (Seedance only) to
   OpenRouter's /videos endpoint. Cheap, no audio.

2) fal Seedance reference-to-video (opt-in via `scene.audio_sync_enabled`)
   — only available when the chosen video model has
   `supports_audio_input=True` (Seedance variants). Sends a sliced audio
   clip + character refs to fal's R2V endpoint. No first_frame — the
   model owns composition entirely. Costs ~6× more per second but the
   character "performs" the audio (lipsynced when faces are present),
   which OpenRouter's I2V route can't do.

Other lipsync paths (post-process via OmniHuman / LatentSync / etc) were
explored and removed; the song's audio is otherwise muxed verbatim at the
assembly stage. Runs as a FastAPI BackgroundTask.
"""

import json
import os
import re
import subprocess
from datetime import datetime
from sqlmodel import Session, select
from app.config import settings, VIDEO_MODELS
from app.models import Scene, SceneAsset, GenerationJob, Project, Character, Song
from app.services import openrouter, fal_client, pricing
from app.services.versioning import make_active


def _sync_scene_pointer(scene: Scene, asset_type: str, file_path: str | None) -> None:
    """Mirror an active SceneAsset's file_path onto the matching `Scene.*_path`
    convenience field that other code reads directly."""
    if asset_type == "image":
        scene.reference_image_path = file_path
    elif asset_type == "video":
        scene.video_path = file_path


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

    Implementation: seek to a few seconds before EOF (cheap), then decode
    every frame in that tail window with `-update 1` (no `-frames:v 1`).
    `-update` overwrites the same output file on each decoded frame, so
    the final file on disk is the genuine last frame of the video.

    Historical bug (fixed 2026-05): we used `-sseof -0.5 ... -frames:v 1`,
    which writes the FIRST frame inside the last 0.5s window — i.e. ~12
    frames (at 24fps) before the real end. Scene N+1's chained first
    frame visibly differed from the end of scene N's video.

    Returns True on success, False on failure (e.g. ffmpeg not installed,
    video unreadable). Failure is non-fatal: chaining falls back to the
    planned still on consumption.
    """
    import subprocess
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                # Seek to 3s before EOF. Cheap (no full-video decode) and
                # wide enough to cover any reasonable video framerate +
                # GOP boundary so the decoder lands on a keyframe before
                # the tail.
                "-sseof", "-3",
                "-i", video_path,
                # Overwrite the output file on every decoded frame. After
                # ffmpeg processes the entire tail window, the file on
                # disk holds the *last* decoded frame. NO `-frames:v 1`
                # here — that would stop at the first frame instead.
                "-update", "1",
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
      - "video":  video gen. Skips image step if a reference already exists;
                  auto-generates one if missing. Skips image entirely when
                  scene.chain_from_prev is True (the prev scene's extracted
                  last frame is used as the first_frame instead).
      - "all":    image → video.
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

    # Decide which video route to take.
    use_audio_sync = (
        scene.audio_sync_enabled
        and bool(model_cfg.get("supports_audio_input"))
        and bool(model_cfg.get("fal_r2v_model_id"))
    )

    # Gen the reference still in BOTH routes (unless one already exists, or
    # we're chaining from the prev scene). In OpenRouter I2V it becomes the
    # first_frame; in fal Seedance R2V it goes into image_urls as another
    # reference image alongside the character portraits. R2V doesn't treat
    # it specially — Seedance gets up to 9 reference images and the planned
    # still is one of them, giving the model compositional/style anchoring
    # on top of the character identity refs.
    if not scene.reference_image_path and not scene.chain_from_prev:
        await _generate_image(scene, db)
        if _check_cancelled(engine, scene.id):
            raise asyncio.CancelledError()

    if use_audio_sync:
        await _generate_video_fal_seedance_audio(scene, db, model_cfg, engine=engine)
    else:
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
    # BUT only Seedance variants effectively use input_references on the
    # OpenRouter route:
    #   - Seedance: triggers R2V pathway, strong identity anchor (~70% weight)
    #   - Kling: refs silently ignored by OpenRouter passthrough
    #   - Veo: refs rejected when first_frame is also sent (we always send it)
    # For Kling/Veo we send NO refs — saves bandwidth and avoids the bad
    # signal that "we tried to pass them, they just didn't take." The model
    # card's `supports_reference_images` flag drives both this decision and
    # the UI badge.
    if model_cfg.get("supports_reference_images"):
        char_refs = _find_character_references(scene, db, prompt)
    else:
        char_refs = []

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

    # Audio is never sent to the video model — the song's audio is muxed
    # in verbatim at assembly time, so model-generated audio would just be
    # overwritten. Pricing reflects video-only (no audio surcharge).
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
                "aspect": aspect_ratio,
                "provider": "openrouter",
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


# ---------------------------------------------------------------------------
# fal Seedance reference-to-video (audio-sync path, per-scene opt-in)
# ---------------------------------------------------------------------------

async def _generate_video_fal_seedance_audio(
    scene: Scene, db: Session, model_cfg: dict, engine=None,
) -> None:
    """Video gen via fal's Seedance reference-to-video endpoint.

    Used ONLY when scene.audio_sync_enabled AND the model has both
    supports_audio_input=True and a fal_r2v_model_id. The decision happens
    in `_run_pipeline`; this function trusts that gate.

    Submits a SHORT audio slice + character refs to fal's R2V endpoint. No
    first_frame in this mode (the endpoint doesn't accept one). The model
    composes the shot itself with audio as a strong driver — character
    "performs" the audio when faces are present.

    Audio constraint: fal rejects audio that's >= video duration. We slice
    the scene's window from the song and trim ~150ms off the end to land
    safely under. _extract_audio_segment does the slicing.
    """
    if not settings.fal_api_key:
        raise RuntimeError(
            "Audio-sync requires FAL_API_KEY in .env — the fal Seedance R2V "
            "endpoint isn't reachable via OpenRouter. Add the key or turn "
            "off audio-sync on this scene."
        )

    scene.status = "generating_video"
    db.add(scene); db.commit()

    fal_model_id = model_cfg["fal_r2v_model_id"]

    raw_duration = int(round(scene.audio_end - scene.audio_start))
    durations = model_cfg.get("durations") or [model_cfg.get("max_duration", 8)]
    duration = _closest_supported(raw_duration, durations)

    supported_res = model_cfg.get("resolutions") or ["720p"]
    resolution = scene.resolution if scene.resolution in supported_res else supported_res[0]

    project = db.get(Project, scene.project_id)
    aspect_ratio = project.aspect_ratio if project else "16:9"
    if aspect_ratio not in (model_cfg.get("aspects") or []):
        aspect_ratio = (model_cfg.get("aspects") or ["16:9"])[0]

    # Build the prompt + style suffix (same as OpenRouter route).
    prompt = scene.video_prompt or scene.description or "Cinematic music video shot"
    prompt = _append_style(prompt, db, scene.project_id)

    # Build the list of reference images Seedance R2V will see. The model
    # accepts up to 9 image_urls and treats them all as references with no
    # "first frame" semantics — it composes the shot itself, biased by
    # whichever images it sees. We pack the list in this order:
    #
    #   1. Scene first-frame source (chained prev's last frame, OR this
    #      scene's planned still). Gives compositional + setting anchor.
    #   2. Character portraits for any cast member named in the prompt.
    #      Provides identity anchor (~70% weight per ByteDance docs).
    #
    # At least ONE image is required by fal — if no first-frame source AND
    # no named character with a portrait, we surface an actionable error.
    image_ref_paths: list[str] = []

    # 1) First-frame source
    if scene.chain_from_prev:
        prev_scene = db.exec(select(Scene).where(
            Scene.project_id == scene.project_id,
            Scene.order == scene.order - 1,
        )).first()
        if prev_scene and prev_scene.extracted_last_frame_path and os.path.exists(
            prev_scene.extracted_last_frame_path
        ):
            image_ref_paths.append(prev_scene.extracted_last_frame_path)
    elif scene.reference_image_path and os.path.exists(scene.reference_image_path):
        image_ref_paths.append(scene.reference_image_path)

    # 2) Character portraits
    char_ref_paths = _find_character_references(scene, db, prompt)
    image_ref_paths.extend(char_ref_paths)

    if not image_ref_paths:
        raise RuntimeError(
            "Audio-sync (Seedance R2V) needs at least one reference image. "
            "Either generate this scene's reference still (click Img), or "
            "mention a cast character with a portrait in the prompt. The "
            "fal endpoint doesn't accept a first_frame, but it does take "
            "up to 9 image_urls — your still and character portraits both "
            "go in there as references."
        )

    # fal caps image_urls at 9 — if we'd exceed it, drop excess character
    # refs (the first-frame source and the FIRST few characters are most
    # important). Realistically this never trips with our 1-frame + ~3
    # characters projects.
    if len(image_ref_paths) > 9:
        print(
            f"[fal seedance r2v] capping image_urls at 9 (had "
            f"{len(image_ref_paths)}); dropping the last "
            f"{len(image_ref_paths) - 9}"
        )
        image_ref_paths = image_ref_paths[:9]

    # Slice the scene's audio window from the song. fal needs a public URL
    # so we upload after slicing.
    audio_path = await _extract_audio_segment(
        scene, db,
        max_duration=duration - 0.15,  # leave ~150ms under video duration
    )

    # Upload audio + each image ref to fal.storage in parallel.
    import asyncio as _asyncio
    upload_tasks = [
        fal_client.upload_file(audio_path),
        *[fal_client.upload_file(p) for p in image_ref_paths],
    ]
    uploaded = await _asyncio.gather(*upload_tasks)
    audio_url = uploaded[0]
    image_ref_urls = list(uploaded[1:])

    cost, detail = pricing.video_cost_fal_seedance_r2v(
        scene.video_model, duration, resolution,
    )
    n_frame = 1 if (scene.chain_from_prev or scene.reference_image_path) and len(image_ref_paths) > len(char_ref_paths) else 0
    detail += f" + {n_frame} still + {len(char_ref_paths)} char ref(s)"

    job = _create_job(db, scene, "video", "fal", cost, detail)
    try:
        submission = await fal_client.submit_seedance_audio_video(
            fal_model_id=fal_model_id,
            prompt=prompt,
            image_urls=image_ref_urls,
            audio_urls=[audio_url],
            duration=duration,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
        )
        request_id = submission.get("request_id")
        scene.openrouter_job_id = request_id  # field is now generic "external job ID"
        job.external_id = request_id
        job.status = "running"
        db.add(scene); db.add(job); db.commit()

        # Cancel-aware poll. fal_client.poll doesn't accept a cancel
        # callback today; we use a long timeout and rely on the user
        # restarting the scene rather than killing the upstream job.
        result = await fal_client.poll(submission, timeout=900, interval=8)
        video_url = fal_client.extract_video_url(result)
        if not video_url:
            raise RuntimeError(
                f"fal Seedance R2V returned no .mp4 URL in the response. "
                f"Full body: {str(result)[:600]}"
            )

        ts = int(datetime.utcnow().timestamp())
        dest = _storage(scene.project_id, "videos", f"scene_{scene.id}_{ts}.mp4")
        await fal_client.download_file(video_url, dest)

        _save_asset(
            db, scene, "video", dest,
            model_used=scene.video_model, cost_usd=cost, cost_detail=detail,
            metadata={
                "duration": duration, "resolution": resolution,
                "aspect": aspect_ratio,
                "provider": "fal",
                "route": "seedance-r2v",
                "audio_synced": True,
                "image_refs": len(image_ref_urls),
                "char_refs": len(char_ref_paths),
                "frame_ref_included": len(image_ref_paths) > len(char_ref_paths),
            },
        )

        # Extract the actual last frame — same as the OpenRouter path —
        # so downstream chaining works even when this scene used R2V.
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


async def _extract_audio_segment(
    scene: Scene, db: Session, max_duration: float | None = None,
) -> str:
    """Slice the scene's audio window from the project's song into an MP3.

    Caller passes `max_duration` to cap the slice — fal's Seedance R2V
    rejects audio that's >= the video duration, so we trim ~150ms under.
    Idempotent per (scene_id, start, end) — same window → same filename →
    reusable across retries.
    """
    song = db.exec(select(Song).where(Song.project_id == scene.project_id)).first()
    if not song or not song.file_path:
        raise RuntimeError(
            "No song file on disk for this project — can't slice audio. "
            "Re-upload or re-generate the song before using audio-sync."
        )

    start = round(scene.audio_start, 2)
    end = round(scene.audio_end, 2)
    natural_dur = max(0.0, end - start)
    duration = min(natural_dur, max_duration) if max_duration is not None else natural_dur
    if duration <= 0:
        raise RuntimeError(
            f"Scene {scene.id} has audio_start={start} >= audio_end={end}; "
            f"can't slice a non-positive duration."
        )

    dest = _storage(
        scene.project_id, "audio_segments",
        f"scene_{scene.id}_{start}-{end}_d{duration:.3f}.mp3",
    )
    # Skip re-running ffmpeg if the file's already on disk for this exact window.
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"[audio slice] scene {scene.id}: reusing cached {dest}")
        return dest

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start}",
        "-t", f"{duration}",
        "-i", song.file_path,
        "-acodec", "libmp3lame", "-q:a", "2",
        dest,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0 or not os.path.exists(dest):
        raise RuntimeError(
            f"ffmpeg failed to slice scene audio: {(proc.stderr or '')[-400:]}"
        )
    print(
        f"[audio slice] scene {scene.id}: {duration:.2f}s from {start:.2f}s → {end:.2f}s "
        f"out of song={song.file_path} → {dest}"
    )
    return dest


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
# Helpers
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
