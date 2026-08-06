"""Generation orchestrator: image → video per scene.

Two video paths, picked per scene:

1) OpenRouter image-to-video (default) — most scenes. Sends a first_frame
   (the scene's reference still or, when chained, the prev scene's
   extracted last frame) + optional input_references (Seedance only) to
   OpenRouter's /videos endpoint. Cheap, no audio.

2) fal audio-driven video (opt-in via `scene.audio_sync_enabled`). Seedance
   R2V uses the song slice + character references with no exact first frame;
   Wan 2.7 I2V uses the song slice + the exact scene/chained first frame.

Other lipsync paths (post-process via OmniHuman / LatentSync / etc) were
explored and removed; the song's audio is otherwise muxed verbatim at the
assembly stage. Runs as a FastAPI BackgroundTask.
"""

import asyncio
import json
import os
import re
import subprocess
import uuid
from datetime import datetime
from sqlmodel import Session, select
from app.config import settings, VIDEO_MODELS, IMAGE_MODELS
from app.models import Scene, SceneAsset, GenerationJob, Project, Character, Song
from app.services import openrouter, fal_client, pricing
from app.services.media_files import remove_storage_file, validate_media
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


def _probe_duration(path: str) -> float | None:
    """Return the exact duration of an audio/video file in seconds (via
    ffprobe), or None if ffprobe isn't available or the file is unreadable.

    Used by the audio-sync route to diagnose duration drift — e.g. when
    fal returns a 9s clip after we asked for 15s, the audio file's actual
    length explains it (the song ended before the requested window did)."""
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if proc.returncode != 0:
            return None
        return float(proc.stdout.strip())
    except Exception:
        return None


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


async def _prepare_generated_video(scene: Scene, video_path: str) -> str:
    """Validate a provider download and atomically refresh its chain anchor.

    The prior anchor remains available until both the new video and extracted
    frame pass validation.  A failed provider download can therefore never
    poison downstream chaining or replace a known-good last frame.
    """
    await asyncio.to_thread(validate_media, video_path, "video")
    final_frame = _storage(
        scene.project_id, "extracted", f"scene_{scene.order}_last.jpg",
    )
    temporary_frame = _storage(
        scene.project_id,
        "extracted",
        f"scene_{scene.order}_last_{uuid.uuid4().hex}.tmp.jpg",
    )
    try:
        extracted = await asyncio.to_thread(
            _extract_last_frame, video_path, temporary_frame,
        )
        if not extracted:
            raise RuntimeError(
                "The generated video downloaded, but its final frame could not "
                "be decoded. The render was not activated; retry generation."
            )
        await asyncio.to_thread(validate_media, temporary_frame, "image")
        os.replace(temporary_frame, final_frame)
    finally:
        remove_storage_file(temporary_frame)
    scene.extracted_last_frame_path = final_frame
    return final_frame


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def generate_scene(
    scene_id: int,
    engine,
    phase: str = "all",
    run_id: str | None = None,
    force: bool = False,
) -> None:
    """Run the scene pipeline.

    phase:
      - "image":  reference still only. Lets the user review
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
        if run_id and scene.generation_run_id != run_id:
            # A stale background callback must never run after a newer claim.
            return
        # Clear any stale cancel flag from a previous run
        if scene.cancel_requested:
            scene.cancel_requested = False
            db.add(scene); db.commit()
        try:
            await _run_pipeline(scene, db, engine, phase=phase, force=force)
        except asyncio.CancelledError:
            # User pressed Stop — mark cancelled, don't propagate
            db.refresh(scene)
            has_existing_video = bool(
                scene.video_path and os.path.exists(scene.video_path)
            )
            scene.status = "done" if has_existing_video else "cancelled"
            scene.error_message = (
                "New generation was cancelled; the existing active video "
                "was preserved."
                if has_existing_video
                else None
            )
            scene.cancel_requested = False
            db.add(scene); db.commit()
        except Exception as e:
            # Variant generation is non-destructive. If an older active video
            # still exists, keep the scene assembly-ready and surface the new
            # attempt's error separately instead of invalidating good work.
            scene.status = (
                "done"
                if scene.video_path and os.path.exists(scene.video_path)
                else "error"
            )
            scene.error_message = str(e)[:500]
            db.add(scene)
            db.commit()
            raise
        finally:
            db.refresh(scene)
            if not run_id or scene.generation_run_id == run_id:
                scene.generation_run_id = None
                scene.generation_phase = None
                scene.generation_requested_at = None
                db.add(scene)
                db.commit()


def _check_cancelled(engine, scene_id: int) -> bool:
    """Re-read the scene from DB to see if cancel was requested mid-flight."""
    with Session(engine) as s:
        sc = s.get(Scene, scene_id)
        return bool(sc and sc.cancel_requested)


async def _run_pipeline(
    scene: Scene,
    db: Session,
    engine,
    phase: str = "all",
    force: bool = False,
) -> None:
    model_cfg = VIDEO_MODELS.get(scene.video_model)
    if not model_cfg:
        raise RuntimeError(
            f"Unknown video model '{scene.video_model}'. Choose an available "
            "model from the video model reference before generating."
        )

    if phase == "image":
        await _generate_image(scene, db)
        if _check_cancelled(engine, scene.id):
            raise asyncio.CancelledError()
        scene.status = (
            "done"
            if scene.video_path and os.path.exists(scene.video_path)
            else "image_ready"
        )
        db.add(scene); db.commit()
        return

    # Decide which video route to take.
    use_audio_sync = (
        scene.audio_sync_enabled
        and bool(model_cfg.get("supports_audio_input"))
        and bool(model_cfg.get("fal_audio_model_id") or model_cfg.get("fal_r2v_model_id"))
    )
    if use_audio_sync and not settings.fal_api_key:
        raise RuntimeError(
            "Audio-sync requires FAL_API_KEY in .env. Add the key or turn "
            "off audio-sync on this scene. No image or video generation was submitted."
        )

    # Gen the reference still in BOTH routes (unless one already exists, or
    # we're chaining from the prev scene). In OpenRouter I2V it becomes the
    # first_frame; in fal Seedance R2V it goes into image_urls as another
    # reference image alongside the character portraits. R2V doesn't treat
    # it specially — Seedance gets up to 9 reference images and the planned
    # still is one of them, giving the model compositional/style anchoring
    # on top of the character identity refs.
    character_reference_only = (
        not use_audio_sync
        and getattr(scene, "video_reference_mode", "frame") == "character"
        and bool(model_cfg.get("supports_reference_images"))
    )
    if (
        not character_reference_only
        and (not scene.reference_image_path or (force and phase == "all"))
        and not scene.chain_from_prev
    ):
        await _generate_image(scene, db)
        if _check_cancelled(engine, scene.id):
            raise asyncio.CancelledError()

    if use_audio_sync:
        if model_cfg.get("audio_input_mode") == "wan_i2v":
            await _generate_video_fal_wan_audio(scene, db, model_cfg, engine=engine)
        else:
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
    dest: str | None = None
    temporary: str | None = None
    try:
        image_result = await openrouter.generate_image(
            prompt, scene.image_model, reference_image_paths=ref_paths,
            aspect_ratio=aspect,
        )
        image_bytes = image_result.data
        if image_result.cost_usd is not None:
            cost = image_result.cost_usd
            detail = f"{detail} · actual OpenRouter usage"
        job.cost_usd = cost
        job.cost_detail = detail
        job.external_id = image_result.generation_id
        job.request_json = json.dumps({
            "generation_id": image_result.generation_id,
            "usage": image_result.usage,
        }, ensure_ascii=False)
        # Reserve an asset row first so we can use its id in the filename;
        # then write the bytes to that filename.
        dest = _storage(
            scene.project_id,
            "images",
            f"scene_{scene.id}_{uuid.uuid4().hex}.jpg",
        )
        temporary = f"{dest}.{uuid.uuid4().hex}.tmp"
        with open(temporary, "xb") as f:
            f.write(image_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, dest)
        await asyncio.to_thread(validate_media, dest, "image")
        _save_asset(
            db, scene, "image", dest,
            model_used=scene.image_model, cost_usd=cost, cost_detail=detail,
            metadata={
                "prompt": prompt,
                "char_refs": len(ref_paths or []),
                "provider_cost_usd": image_result.cost_usd,
                "generation_id": image_result.generation_id,
            },
        )
        job.status = "completed"
        job.result_path = dest
    except Exception as e:
        remove_storage_file(temporary)
        if dest and job.result_path != dest:
            remove_storage_file(dest)
        job.status = "failed"; job.error = str(e); raise
    finally:
        job.completed_at = datetime.utcnow()
        db.add(job); db.commit()


def _find_character_references(scene: Scene, db: Session, prompt: str) -> list[str]:
    """Return reference image paths for any characters whose name appears in
    ANY of: the supplied prompt (usually video_prompt), the scene's
    image_prompt, or its description.

    Why all three: when a scene is rewritten to use "they" / "the trio" /
    "she" in the motion prompt (video_prompt) but still names characters
    in the still composition (image_prompt), the names exist in the scene
    record — just not in the field we'd otherwise inspect. Without
    matching against all three, a user who writes pronoun-heavy video
    prompts gets ZERO character refs passed, which on the audio-sync
    (R2V) route means the model has no identity anchor at all.

    Also matches single tokens of multi-word names (e.g. "Elias" matches
    a character named "Elias Thorne"), since people commonly drop surnames
    in dialogue-style prompts.
    """
    chars = db.exec(
        select(Character).where(Character.project_id == scene.project_id)
    ).all()
    haystack = " ".join([
        prompt or "",
        scene.image_prompt or "",
        scene.description or "",
    ]).lower()
    refs: list[str] = []
    for c in chars:
        if not c.reference_image_path or not os.path.exists(c.reference_image_path):
            continue
        name = (c.name or "").lower().strip()
        if not name:
            continue
        # Match full name OR any individual token (single-word names match
        # themselves; multi-word names like "Elias Thorne" also match
        # bare "Elias").
        candidates = [name, *name.split()]
        matched = any(
            candidate
            and re.search(
                rf"(?<!\w){re.escape(candidate)}(?!\w)",
                haystack,
                flags=re.IGNORECASE,
            )
            for candidate in candidates
        )
        if matched:
            refs.append(c.reference_image_path)
    return refs


# ---------------------------------------------------------------------------
# Video generation — OpenRouter (text/image to video)
# ---------------------------------------------------------------------------

async def _resume_openrouter_video_job(
    scene: Scene,
    db: Session,
    job: GenerationJob,
    engine=None,
) -> None:
    """Finish an already-submitted OpenRouter job without rebuilding inputs."""
    try:
        snapshot = json.loads(job.request_json or "{}")
    except json.JSONDecodeError:
        snapshot = {}
    job_id = job.external_id
    if not job_id:
        raise RuntimeError("Saved OpenRouter job has no provider request ID.")
    print(f"[resume] scene {scene.id}: polling existing OpenRouter job {job_id}")

    dest: str | None = None
    try:
        is_cancelled = (
            (lambda: _check_cancelled(engine, scene.id)) if engine else None
        )
        video_url = await openrouter.poll_video_job(
            job_id,
            is_cancelled=is_cancelled,
        )
        dest = _storage(
            scene.project_id,
            "videos",
            f"scene_{scene.id}_{uuid.uuid4().hex}.mp4",
        )
        await openrouter.download_file(video_url, dest)
        await _prepare_generated_video(scene, dest)
        _save_asset(
            db,
            scene,
            "video",
            dest,
            model_used=snapshot.get("model_key", scene.video_model),
            cost_usd=job.cost_usd,
            cost_detail=job.cost_detail,
            metadata={
                "duration": snapshot.get("duration"),
                "resolution": snapshot.get("resolution"),
                "aspect": snapshot.get("aspect_ratio"),
                "provider": "openrouter",
                "char_refs": snapshot.get("char_refs", 0),
                "reference_mode": snapshot.get("reference_mode", "frame"),
                "chained_from": snapshot.get("chained_from"),
                "resumed": True,
            },
        )
        job.status = "completed"
        job.result_url = video_url
        job.result_path = dest
        job.error = None
    except asyncio.CancelledError:
        job.status = "running"
        job.error = (
            "Local polling stopped by user; retry to resume this existing "
            "provider job."
        )
        raise
    except openrouter.RemoteJobPendingError as exc:
        job.status = "running"
        job.error = str(exc)
        raise
    except Exception as exc:
        if dest and job.result_path != dest:
            remove_storage_file(dest)
        job.status = "failed"
        job.error = str(exc)
        raise
    finally:
        job.completed_at = (
            datetime.utcnow()
            if job.status in ("completed", "failed", "cancelled")
            else None
        )
        db.add(job)
        db.add(scene)
        db.commit()


async def _resume_fal_video_job(
    scene: Scene,
    db: Session,
    job: GenerationJob,
    engine=None,
) -> None:
    """Finish an already-submitted fal job from its persisted queue URLs."""
    try:
        snapshot = json.loads(job.request_json or "{}")
    except json.JSONDecodeError:
        snapshot = {}
    submission = snapshot.get("submission")
    if not isinstance(submission, dict):
        job.status = "failed"
        job.error = "Saved fal job is missing its queue URLs and cannot be resumed."
        job.completed_at = datetime.utcnow()
        db.add(job)
        db.commit()
        raise RuntimeError(job.error)

    print(f"[resume] scene {scene.id}: polling existing fal job {job.external_id}")
    dest: str | None = None
    try:
        is_cancelled = (
            (lambda: _check_cancelled(engine, scene.id)) if engine else None
        )
        result = await fal_client.poll(
            submission,
            timeout=900,
            interval=8,
            is_cancelled=is_cancelled,
        )
        video_url = fal_client.extract_video_url(result)
        if not video_url:
            raise RuntimeError(
                "fal completed the saved job but returned no playable video URL."
            )
        dest = _storage(
            scene.project_id,
            "videos",
            f"scene_{scene.id}_{uuid.uuid4().hex}.mp4",
        )
        await fal_client.download_file(video_url, dest)
        await _prepare_generated_video(scene, dest)
        rendered_duration = _probe_duration(dest)
        audio_duration = snapshot.get("audio_duration")
        route = snapshot.get("route", "fal-video")
        _save_asset(
            db,
            scene,
            "video",
            dest,
            model_used=snapshot.get("model_key", scene.video_model),
            cost_usd=job.cost_usd,
            cost_detail=job.cost_detail,
            metadata={
                "duration": snapshot.get("duration"),
                "rendered_duration": (
                    round(rendered_duration, 2) if rendered_duration else None
                ),
                "audio_duration": audio_duration,
                "resolution": snapshot.get("resolution"),
                "aspect": snapshot.get("aspect_ratio"),
                "provider": "fal",
                "route": route,
                "audio_synced": True,
                "reference_mode": (
                    "frame" if route == "wan-i2v-audio" else "references"
                ),
                "image_refs": snapshot.get("image_refs"),
                "char_refs": snapshot.get("char_refs"),
                "frame_ref_included": snapshot.get("frame_ref_included"),
                "chained_from": snapshot.get("chained_from"),
                "resumed": True,
            },
        )
        job.status = "completed"
        job.result_url = video_url
        job.result_path = dest
        job.error = None
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.error = "Cancelled by user."
        raise
    except fal_client.RemoteJobPendingError as exc:
        job.status = "running"
        job.error = str(exc)
        raise
    except Exception as exc:
        if dest and job.result_path != dest:
            remove_storage_file(dest)
        job.status = "failed"
        job.error = str(exc)
        raise
    finally:
        job.completed_at = (
            datetime.utcnow()
            if job.status in ("completed", "failed", "cancelled")
            else None
        )
        db.add(job)
        db.add(scene)
        db.commit()

async def _generate_video_openrouter(scene: Scene, db: Session, model_cfg: dict, engine=None) -> None:
    scene.status = "generating_video"
    db.add(scene); db.commit()

    resumable = _find_resumable_video_job(
        db, scene, "openrouter", route="openrouter-video",
    )
    if resumable:
        await _resume_openrouter_video_job(scene, db, resumable, engine)
        return

    duration = _exact_video_duration(scene, model_cfg)

    # Validate resolution against the model; fall back to its first option.
    supported_res = model_cfg.get("resolutions") or ["720p"]
    resolution = scene.resolution if scene.resolution in supported_res else supported_res[0]

    prompt = scene.video_prompt or scene.description or "Cinematic music video scene"
    prompt = _append_style(prompt, db, scene.project_id)
    project = db.get(Project, scene.project_id)
    aspect_ratio = project.aspect_ratio if project else "16:9"
    if aspect_ratio not in (model_cfg.get("aspects") or [aspect_ratio]):
        aspect_ratio = (model_cfg.get("aspects") or ["16:9"])[0]

    # Frame conditioning and character-reference conditioning are distinct
    # OpenRouter modes. Sending both does not blend them: frame_images takes
    # precedence and the provider ignores input_references. Construct exactly
    # the mode selected on this scene.
    requested_reference_mode = getattr(scene, "video_reference_mode", "frame") or "frame"
    use_character_references = (
        requested_reference_mode == "character"
        and bool(model_cfg.get("supports_reference_images"))
    )
    if requested_reference_mode == "character" and not model_cfg.get("supports_reference_images"):
        raise RuntimeError(
            f"{model_cfg.get('name', scene.video_model)} does not support "
            "separate character-reference mode. Switch this scene to Frame "
            "mode or choose Seedance/Wan. No generation was submitted."
        )
    if use_character_references and scene.chain_from_prev:
        raise RuntimeError(
            "Character-reference mode cannot be combined with scene chaining. "
            "Switch this scene to Frame mode or turn chaining off."
        )

    char_refs = _find_character_references(scene, db, prompt) if use_character_references else []
    if use_character_references and not char_refs:
        raise RuntimeError(
            "Character-reference mode needs at least one named cast character "
            "with a portrait. Mention the character by name in this scene's "
            "prompt and generate/upload their portrait first. No generation was submitted."
        )

    # Frame mode defaults to this scene's planned still. A chained scene uses
    # the previous clip's extracted final pixels. Character mode deliberately
    # sends no frame_images, otherwise OpenRouter would ignore the portraits.
    first_frame_path = None if use_character_references else scene.reference_image_path
    if scene.chain_from_prev and not use_character_references:
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

    # Audio is never sent to this OpenRouter route — the song's audio is muxed
    # in verbatim at assembly time, so model-generated audio would just be
    # overwritten. Pricing reflects video-only (no audio surcharge).
    cost, detail = pricing.video_cost(
        scene.video_model, duration, resolution, with_audio=False,
    )
    if char_refs:
        detail += f" + {len(char_refs)} char ref(s)"
        detail += " + character-reference mode"
    elif first_frame_path:
        detail += " + frame mode"
    if scene.chain_from_prev:
        detail += " + chained"
    request_snapshot = {
        "route": "openrouter-video",
        "model_key": scene.video_model,
        "model_id": model_cfg["model_id"],
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "reference_mode": "character" if use_character_references else "frame",
        "char_refs": len(char_refs or []),
        "chained_from": prev_scene.id if scene.chain_from_prev else None,
    }
    job = _find_resumable_video_job(
        db, scene, "openrouter", route="openrouter-video",
    )
    if job:
        try:
            request_snapshot = json.loads(job.request_json or "{}") or request_snapshot
        except json.JSONDecodeError:
            pass
        cost = job.cost_usd
        detail = job.cost_detail or detail
    else:
        job = _create_job(
            db, scene, "video", "openrouter", cost, detail,
            request_snapshot=request_snapshot,
        )
    dest: str | None = None
    try:
        if job.external_id:
            job_id = job.external_id
            print(f"[resume] scene {scene.id}: polling existing OpenRouter job {job_id}")
        else:
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
        dest = _storage(
            scene.project_id,
            "videos",
            f"scene_{scene.id}_{uuid.uuid4().hex}.mp4",
        )
        await openrouter.download_file(video_url, dest)
        await _prepare_generated_video(scene, dest)

        _save_asset(
            db, scene, "video", dest,
            model_used=request_snapshot.get("model_key", scene.video_model),
            cost_usd=cost, cost_detail=detail,
            metadata={
                "duration": request_snapshot.get("duration", duration),
                "resolution": request_snapshot.get("resolution", resolution),
                "aspect": request_snapshot.get("aspect_ratio", aspect_ratio),
                "provider": "openrouter",
                "char_refs": request_snapshot.get("char_refs", len(char_refs or [])),
                "reference_mode": request_snapshot.get("reference_mode", "frame"),
                "chained_from": request_snapshot.get("chained_from"),
            },
        )

        job.status = "completed"
        job.result_url = video_url
        job.result_path = dest
        job.error = None
    except asyncio.CancelledError:
        # OpenRouter exposes no cancellation endpoint in this integration.
        # Keep the handle so a retry resumes polling rather than paying twice.
        job.status = "running"
        job.error = (
            "Local polling stopped by user; retry to resume this existing "
            "provider job."
        )
        raise
    except openrouter.RemoteJobPendingError as e:
        job.status = "running"
        job.error = str(e)
        raise
    except Exception as e:
        if dest and job.result_path != dest:
            remove_storage_file(dest)
        job.status = "failed"; job.error = str(e); raise
    finally:
        job.completed_at = (
            datetime.utcnow()
            if job.status in ("completed", "failed", "cancelled")
            else None
        )
        db.add(job); db.add(scene); db.commit()


# ---------------------------------------------------------------------------
# fal Wan 2.7 image-to-video (exact first frame + driving audio)
# ---------------------------------------------------------------------------

async def _generate_video_fal_wan_audio(
    scene: Scene, db: Session, model_cfg: dict, engine=None,
) -> None:
    """Render Wan 2.7 through fal with the scene song slice as audio_url.

    Unlike Seedance R2V, Wan's audio endpoint preserves an exact image_url
    first frame. Separate character portraits are not accepted by this I2V
    endpoint; identity comes from the planned still or chained final frame.
    """
    if not settings.fal_api_key:
        raise RuntimeError("Audio-sync requires FAL_API_KEY in .env.")

    scene.status = "generating_video"
    db.add(scene); db.commit()

    resumable = _find_resumable_video_job(
        db, scene, "fal", route="wan-i2v-audio",
    )
    if resumable:
        await _resume_fal_video_job(scene, db, resumable, engine)
        return

    duration = _exact_video_duration(scene, model_cfg)
    supported_res = (
        model_cfg.get("audio_resolutions")
        or model_cfg.get("resolutions")
        or ["720p", "1080p"]
    )
    resolution = scene.resolution if scene.resolution in supported_res else supported_res[0]
    prompt = _append_style(
        scene.video_prompt or scene.description or "Cinematic music video shot",
        db,
        scene.project_id,
    )
    cost, detail = pricing.video_cost_fal_wan_audio(
        scene.video_model, duration, resolution,
    )
    detail += " + exact first frame"
    if scene.chain_from_prev:
        detail += " + chained"

    snapshot: dict = {
        "route": "wan-i2v-audio",
        "model_key": scene.video_model,
        "model_id": model_cfg["fal_audio_model_id"],
        "duration": duration,
        "resolution": resolution,
        "chained_from": None,
        "audio_duration": None,
    }
    submission: dict | None = None
    job = _find_resumable_video_job(
        db, scene, "fal", route="wan-i2v-audio",
    )

    if job:
        try:
            snapshot = json.loads(job.request_json or "{}") or snapshot
        except json.JSONDecodeError:
            snapshot = {}
        submission = snapshot.get("submission")
        if not isinstance(submission, dict):
            job.status = "failed"
            job.error = "Saved fal job is missing its queue URLs and cannot be resumed."
            job.completed_at = datetime.utcnow()
            db.add(job); db.commit()
            raise RuntimeError(job.error)
        duration = int(snapshot.get("duration", duration))
        resolution = snapshot.get("resolution", resolution)
        cost = job.cost_usd
        detail = job.cost_detail or detail
        print(f"[resume] scene {scene.id}: polling existing fal job {job.external_id}")
    else:
        first_frame_path = scene.reference_image_path
        previous_scene_id: int | None = None
        if scene.chain_from_prev:
            prev_scene = db.exec(select(Scene).where(
                Scene.project_id == scene.project_id,
                Scene.order == scene.order - 1,
            )).first()
            if (
                not prev_scene
                or not prev_scene.extracted_last_frame_path
                or not os.path.exists(prev_scene.extracted_last_frame_path)
            ):
                raise RuntimeError(
                    "Wan audio mode needs the previous scene's extracted last "
                    "frame. Generate the previous scene first or turn chaining off."
                )
            first_frame_path = prev_scene.extracted_last_frame_path
            previous_scene_id = prev_scene.id
        if not first_frame_path or not os.path.exists(first_frame_path):
            raise RuntimeError(
                "Wan audio mode needs an exact first frame. Generate this "
                "scene's still first, or chain it from a rendered previous scene."
            )

        # Wan accepts 2-30s driving audio. Keep the slice at or below the
        # chosen output duration so scene timing and assembly stay aligned.
        audio_path = await _extract_audio_segment(scene, db, max_duration=duration)
        audio_actual_dur = _probe_duration(audio_path)
        audio_url, image_url = await asyncio.gather(
            fal_client.upload_file(audio_path),
            fal_client.upload_file(first_frame_path),
        )
        snapshot.update({
            "chained_from": previous_scene_id,
            "audio_duration": audio_actual_dur,
        })
        job = _create_job(
            db,
            scene,
            "video",
            "fal",
            cost,
            detail,
            request_snapshot=snapshot,
        )

    dest: str | None = None
    try:
        if submission is None:
            if engine and _check_cancelled(engine, scene.id):
                raise asyncio.CancelledError()
            submission = await fal_client.submit_wan_audio_video(
                fal_model_id=snapshot.get("model_id", model_cfg["fal_audio_model_id"]),
                prompt=prompt,
                image_url=image_url,
                audio_url=audio_url,
                duration=duration,
                resolution=resolution,
            )
            request_id = submission.get("request_id")
            if not request_id:
                raise RuntimeError(f"fal returned no request_id: {submission}")
            snapshot["submission"] = submission
            job.request_json = json.dumps(snapshot, ensure_ascii=False)
            scene.openrouter_job_id = request_id
            job.external_id = request_id
            job.status = "running"
            db.add(scene); db.add(job); db.commit()

        is_cancelled = (
            (lambda: _check_cancelled(engine, scene.id)) if engine else None
        )
        result = await fal_client.poll(
            submission,
            timeout=900,
            interval=8,
            is_cancelled=is_cancelled,
        )
        video_url = fal_client.extract_video_url(result)
        if not video_url:
            raise RuntimeError(
                "fal Wan 2.7 returned no video URL. "
                f"Full body: {str(result)[:600]}"
            )

        dest = _storage(
            scene.project_id,
            "videos",
            f"scene_{scene.id}_{uuid.uuid4().hex}.mp4",
        )
        await fal_client.download_file(video_url, dest)
        await _prepare_generated_video(scene, dest)
        rendered_dur = _probe_duration(dest)
        audio_actual_dur = snapshot.get("audio_duration")

        _save_asset(
            db, scene, "video", dest,
            model_used=snapshot.get("model_key", scene.video_model),
            cost_usd=cost,
            cost_detail=detail,
            metadata={
                "duration": duration,
                "rendered_duration": round(rendered_dur, 2) if rendered_dur else None,
                "audio_duration": round(audio_actual_dur, 2) if audio_actual_dur else None,
                "resolution": resolution,
                "provider": "fal",
                "route": "wan-i2v-audio",
                "audio_synced": True,
                "reference_mode": "frame",
                "chained_from": snapshot.get("chained_from"),
            },
        )
        job.status = "completed"
        job.result_url = video_url
        job.result_path = dest
        job.error = None
    except asyncio.CancelledError:
        # fal_client.poll has already sent the queue's cancel request.
        job.status = "cancelled"
        job.error = "Cancelled by user."
        raise
    except fal_client.RemoteJobPendingError as e:
        job.status = "running"
        job.error = str(e)
        raise
    except Exception as e:
        if dest and job.result_path != dest:
            remove_storage_file(dest)
        job.status = "failed"
        job.error = str(e)
        raise
    finally:
        job.completed_at = (
            datetime.utcnow()
            if job.status in ("completed", "failed", "cancelled")
            else None
        )
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

    resumable = _find_resumable_video_job(
        db, scene, "fal", route="seedance-r2v",
    )
    if resumable:
        await _resume_fal_video_job(scene, db, resumable, engine)
        return

    fal_model_id = model_cfg["fal_r2v_model_id"]

    duration = _exact_video_duration(scene, model_cfg)

    supported_res = (
        model_cfg.get("audio_resolutions")
        or model_cfg.get("resolutions")
        or ["720p"]
    )
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

    # Probe the actual on-disk audio duration. This is the most common
    # cause of "I asked for 15s but got 9s out of Seedance R2V": the song
    # ended before the scene window did, so ffmpeg's slice is shorter than
    # requested, and the model caps the video to match the audio.
    audio_actual_dur = _probe_duration(audio_path)
    if audio_actual_dur is not None and audio_actual_dur < (duration - 0.3):
        # The slice is meaningfully shorter than we asked for — log a
        # warning so the user sees the cap reason in the backend output.
        print(
            f"[fal seedance r2v] WARNING scene {scene.id}: requested duration "
            f"{duration}s but audio slice is only {audio_actual_dur:.2f}s long. "
            f"The song probably ended before the scene window did. fal will "
            f"likely cap the rendered video to ~{audio_actual_dur:.1f}s."
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

    request_snapshot = {
        "route": "seedance-r2v",
        "model_key": scene.video_model,
        "model_id": fal_model_id,
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "audio_duration": audio_actual_dur,
        "image_refs": len(image_ref_urls),
        "char_refs": len(char_ref_paths),
        "frame_ref_included": len(image_ref_paths) > len(char_ref_paths),
    }
    submission: dict | None = None
    job = _find_resumable_video_job(
        db, scene, "fal", route="seedance-r2v",
    )
    if job:
        try:
            request_snapshot = json.loads(job.request_json or "{}") or request_snapshot
        except json.JSONDecodeError:
            request_snapshot = {}
        submission = request_snapshot.get("submission")
        if not isinstance(submission, dict):
            job.status = "failed"
            job.error = "Saved fal job is missing its queue URLs and cannot be resumed."
            job.completed_at = datetime.utcnow()
            db.add(job); db.commit()
            raise RuntimeError(job.error)
        duration = int(request_snapshot.get("duration", duration))
        resolution = request_snapshot.get("resolution", resolution)
        aspect_ratio = request_snapshot.get("aspect_ratio", aspect_ratio)
        audio_actual_dur = request_snapshot.get("audio_duration")
        cost = job.cost_usd
        detail = job.cost_detail or detail
        print(f"[resume] scene {scene.id}: polling existing fal job {job.external_id}")
    else:
        job = _create_job(
            db, scene, "video", "fal", cost, detail,
            request_snapshot=request_snapshot,
        )

    dest: str | None = None
    try:
        if submission is None:
            if engine and _check_cancelled(engine, scene.id):
                raise asyncio.CancelledError()
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
            if not request_id:
                raise RuntimeError(f"fal returned no request_id: {submission}")
            request_snapshot["submission"] = submission
            job.request_json = json.dumps(request_snapshot, ensure_ascii=False)
            scene.openrouter_job_id = request_id
            job.external_id = request_id
            job.status = "running"
            db.add(scene); db.add(job); db.commit()

        # Cancel-aware poll. fal_client.poll doesn't accept a cancel
        # callback today; we use a long timeout and rely on the user
        # restarting the scene rather than killing the upstream job.
        is_cancelled = (
            (lambda: _check_cancelled(engine, scene.id)) if engine else None
        )
        result = await fal_client.poll(
            submission,
            timeout=900,
            interval=8,
            is_cancelled=is_cancelled,
        )
        video_url = fal_client.extract_video_url(result)
        if not video_url:
            raise RuntimeError(
                f"fal Seedance R2V returned no .mp4 URL in the response. "
                f"Full body: {str(result)[:600]}"
            )

        dest = _storage(
            scene.project_id,
            "videos",
            f"scene_{scene.id}_{uuid.uuid4().hex}.mp4",
        )
        await fal_client.download_file(video_url, dest)
        await _prepare_generated_video(scene, dest)

        # Probe the actual rendered duration. If it's noticeably shorter
        # than what we requested, the metadata makes the cause obvious in
        # the UI (and the backend log surfaces it on the spot).
        rendered_dur = _probe_duration(dest)
        if rendered_dur is not None and rendered_dur < (duration - 0.3):
            print(
                f"[fal seedance r2v] scene {scene.id}: requested {duration}s, "
                f"got {rendered_dur:.2f}s rendered. "
                f"Audio slice was {audio_actual_dur or '?'}s — likely the cap."
            )

        _save_asset(
            db, scene, "video", dest,
            model_used=request_snapshot.get("model_key", scene.video_model),
            cost_usd=cost,
            cost_detail=detail,
            metadata={
                "duration": duration,
                # Actual on-disk duration of the rendered video. When this
                # is < `duration`, audio was the limiting factor (fal caps
                # video to audio length when audio is provided).
                "rendered_duration": round(rendered_dur, 2) if rendered_dur else None,
                "audio_duration": round(audio_actual_dur, 2) if audio_actual_dur else None,
                "resolution": resolution,
                "aspect": aspect_ratio,
                "provider": "fal",
                "route": "seedance-r2v",
                "audio_synced": True,
                "image_refs": request_snapshot.get("image_refs", len(image_ref_urls)),
                "char_refs": request_snapshot.get("char_refs", len(char_ref_paths)),
                "frame_ref_included": request_snapshot.get(
                    "frame_ref_included",
                    len(image_ref_paths) > len(char_ref_paths),
                ),
            },
        )

        # Extract the actual last frame — same as the OpenRouter path —
        # so downstream chaining works even when this scene used R2V.
        job.status = "completed"
        job.result_url = video_url
        job.result_path = dest
        job.error = None
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.error = "Cancelled by user."
        raise
    except fal_client.RemoteJobPendingError as e:
        job.status = "running"
        job.error = str(e)
        raise
    except Exception as e:
        if dest and job.result_path != dest:
            remove_storage_file(dest)
        job.status = "failed"
        job.error = str(e)
        raise
    finally:
        job.completed_at = (
            datetime.utcnow()
            if job.status in ("completed", "failed", "cancelled")
            else None
        )
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
    proc = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if proc.returncode != 0 or not os.path.exists(dest):
        raise RuntimeError(
            f"ffmpeg failed to slice scene audio: {(proc.stderr or '')[-400:]}"
        )
    print(
        f"[audio slice] scene {scene.id}: {duration:.2f}s from {start:.2f}s -> {end:.2f}s "
        f"out of song={song.file_path} -> {dest}"
    )
    return dest


def _closest_supported(value: int, options: list[int]) -> int:
    if not options:
        return value
    return min(options, key=lambda x: (abs(x - value), x))


def _exact_video_duration(scene: Scene, model_cfg: dict) -> int:
    """Require the provider to support the planned scene length exactly."""
    duration = int(round(scene.audio_end - scene.audio_start))
    supported = model_cfg.get("durations") or []
    if duration not in supported:
        supported_label = ", ".join(f"{value}s" for value in supported)
        raise RuntimeError(
            f"{model_cfg.get('name', scene.video_model)} cannot render scene "
            f"#{scene.order} at {duration}s. Supported durations: "
            f"{supported_label}. Choose a compatible model or change the "
            "scene length. No generation was submitted."
        )
    return duration


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


def scene_generation_preflight(
    scene: Scene,
    db: Session,
    *,
    phase: str,
    force: bool = False,
) -> dict:
    """Describe what a scene generation request will submit before billing."""
    errors: list[str] = []
    warnings: list[str] = []
    estimated_cost = 0.0
    provider: str | None = None
    route: str | None = None
    video_cost_estimate = 0.0
    image_cost_estimate = 0.0

    if scene.generation_run_id:
        errors.append(
            f"Scene generation is already queued or running ({scene.generation_phase or 'unknown phase'})."
        )

    model_cfg = VIDEO_MODELS.get(scene.video_model)
    prompt = scene.video_prompt or scene.description or ""
    char_refs = _find_character_references(scene, db, prompt)
    audio_sync = bool(
        model_cfg
        and scene.audio_sync_enabled
        and model_cfg.get("supports_audio_input")
        and (model_cfg.get("fal_audio_model_id") or model_cfg.get("fal_r2v_model_id"))
    )
    character_mode = bool(
        model_cfg
        and not audio_sync
        and scene.video_reference_mode == "character"
        and model_cfg.get("supports_reference_images")
    )
    has_still = bool(
        scene.reference_image_path and os.path.exists(scene.reference_image_path)
    )
    image_will_generate = phase == "image" or (
        phase in ("video", "all")
        and not scene.chain_from_prev
        and not character_mode
        and (not has_still or (force and phase == "all"))
    )

    if image_will_generate:
        if not settings.openrouter_api_key:
            errors.append("OPENROUTER_API_KEY is missing; image generation cannot be submitted.")
        if scene.image_model not in IMAGE_MODELS:
            errors.append(
                f"Unknown image model '{scene.image_model}'. Choose an available image model."
            )
        else:
            image_cost_estimate, _ = pricing.image_cost(scene.image_model)
            estimated_cost += image_cost_estimate

    if phase in ("video", "all"):
        if not model_cfg:
            errors.append(
                f"Unknown video model '{scene.video_model}'. Choose an available video model."
            )
        else:
            raw_duration = int(round(scene.audio_end - scene.audio_start))
            durations = model_cfg.get("durations") or []
            duration = raw_duration
            supported_res = (
                model_cfg.get("audio_resolutions")
                if audio_sync
                else model_cfg.get("resolutions")
            ) or ["720p"]
            resolution = (
                scene.resolution
                if scene.resolution in supported_res
                else supported_res[0]
            )
            if duration not in durations:
                supported_label = ", ".join(f"{value}s" for value in durations)
                errors.append(
                    f"{model_cfg.get('name', scene.video_model)} cannot render "
                    f"this {duration}s scene. Supported durations: "
                    f"{supported_label}. Choose a compatible model or change "
                    "the scene length."
                )
            if resolution != scene.resolution:
                warnings.append(
                    f"{scene.resolution} is unsupported; this request will use {resolution}."
                )

            project = db.get(Project, scene.project_id)
            aspect = project.aspect_ratio if project else "16:9"
            if aspect not in (model_cfg.get("aspects") or [aspect]):
                warnings.append(
                    f"Project aspect {aspect} is unsupported; the provider's "
                    "first supported aspect will be used."
                )

            if scene.audio_sync_enabled and not audio_sync:
                warnings.append(
                    "Audio sync is enabled on the scene but this model cannot "
                    "accept reference audio; the OpenRouter video-only route will be used."
                )

            if audio_sync:
                provider = "fal"
                if model_cfg.get("audio_input_mode") == "wan_i2v":
                    route = "wan-i2v-audio"
                    video_cost_estimate, _ = pricing.video_cost_fal_wan_audio(
                        scene.video_model, duration, resolution,
                    )
                else:
                    route = "seedance-r2v"
                    video_cost_estimate, _ = pricing.video_cost_fal_seedance_r2v(
                        scene.video_model, duration, resolution,
                    )
                if not settings.fal_api_key:
                    errors.append("FAL_API_KEY is missing; audio-sync cannot be submitted.")
                song = db.exec(
                    select(Song).where(Song.project_id == scene.project_id)
                ).first()
                if not song or not song.file_path or not os.path.exists(song.file_path):
                    errors.append("The project song file is missing; reference audio cannot be sliced.")
            else:
                provider = "openrouter"
                route = "openrouter-video"
                if not settings.openrouter_api_key:
                    errors.append(
                        "OPENROUTER_API_KEY is missing; video generation cannot be submitted."
                    )
                video_cost_estimate, _ = pricing.video_cost(
                    scene.video_model, duration, resolution, with_audio=False,
                )

            previous_scene = None
            if scene.chain_from_prev:
                previous_scene = db.exec(select(Scene).where(
                    Scene.project_id == scene.project_id,
                    Scene.order == scene.order - 1,
                )).first()
            has_chain_frame = bool(
                previous_scene
                and previous_scene.extracted_last_frame_path
                and os.path.exists(previous_scene.extracted_last_frame_path)
            )

            if audio_sync and model_cfg.get("audio_input_mode") == "wan_i2v":
                if scene.chain_from_prev and not has_chain_frame:
                    errors.append(
                        "Wan audio mode needs the previous scene's rendered last frame."
                    )
                elif not scene.chain_from_prev and not (has_still or image_will_generate):
                    errors.append("Wan audio mode needs a scene reference still.")
            elif audio_sync:
                has_seedance_reference = bool(
                    has_chain_frame
                    or (not scene.chain_from_prev and (has_still or image_will_generate))
                    or char_refs
                )
                if not has_seedance_reference:
                    errors.append(
                        "Seedance audio mode needs a scene image or a named cast "
                        "character with an active portrait."
                    )
                warnings.append(
                    "Reference audio mode composes a new opening frame; exact "
                    "first/last-frame conditioning is not used."
                )
            elif character_mode:
                if scene.chain_from_prev:
                    errors.append(
                        "Character-reference mode and scene chaining are mutually exclusive."
                    )
                if not char_refs:
                    errors.append(
                        "Character-reference mode needs a named cast character "
                        "with an active portrait."
                    )
                warnings.append(
                    "Character-reference mode does not send the saved first frame."
                )
            else:
                if scene.video_reference_mode == "character" and not model_cfg.get(
                    "supports_reference_images"
                ):
                    errors.append(
                        f"{model_cfg.get('name', scene.video_model)} does not "
                        "support separate character references on this route."
                    )
                if scene.chain_from_prev and not has_chain_frame:
                    errors.append(
                        "The previous scene must finish rendering before this "
                        "chained scene can generate."
                    )
                elif not scene.chain_from_prev and not (has_still or image_will_generate):
                    errors.append("A reference still is required for frame mode.")

            resumable = (
                _find_resumable_video_job(db, scene, provider, route=route)
                if provider and route
                else None
            )
            if resumable:
                warnings.append(
                    f"Provider job {resumable.external_id} is already submitted; "
                    "this action will resume it at no new estimated charge."
                )
                video_cost_estimate = 0.0
            estimated_cost += video_cost_estimate

    return {
        "scene_id": scene.id,
        "scene_order": scene.order,
        "ready": not errors,
        "errors": errors,
        "warnings": warnings,
        "provider": provider,
        "route": route,
        "will_generate_image": image_will_generate,
        "estimated_image_cost": round(image_cost_estimate, 4),
        "estimated_video_cost": round(video_cost_estimate, 4),
        "estimated_cost": round(estimated_cost, 4),
    }


# ---------------------------------------------------------------------------
# Helpers
def _create_job(
    db: Session, scene: Scene, job_type: str, provider: str,
    cost_usd: float = 0.0, cost_detail: str | None = None,
    request_snapshot: dict | None = None,
) -> GenerationJob:
    job = GenerationJob(
        project_id=scene.project_id,
        scene_id=scene.id,
        job_type=job_type,
        provider=provider,
        status="pending",
        cost_usd=cost_usd,
        cost_detail=cost_detail,
        request_json=json.dumps(request_snapshot, ensure_ascii=False) if request_snapshot else None,
    )
    db.add(job); db.commit(); db.refresh(job)
    return job


def _find_resumable_video_job(
    db: Session,
    scene: Scene,
    provider: str,
    route: str | None = None,
) -> GenerationJob | None:
    jobs = db.exec(
        select(GenerationJob)
        .where(
            GenerationJob.scene_id == scene.id,
            GenerationJob.job_type == "video",
            GenerationJob.provider == provider,
            GenerationJob.status == "running",
            GenerationJob.external_id.isnot(None),
        )
        .order_by(GenerationJob.id.desc())
    ).all()
    if route is None:
        return jobs[0] if jobs else None
    for job in jobs:
        try:
            snapshot = json.loads(job.request_json or "{}")
        except json.JSONDecodeError:
            continue
        if snapshot.get("route") == route:
            return job
    return None
