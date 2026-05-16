import json
import os
import shutil
import traceback
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Optional

from app.database import engine, get_session
from app.models import Scene, SceneAsset, ScenePromptVersion, Song, Project, Character, GenerationJob
from app.config import settings, VIDEO_MODELS
from app.services import pricing


def _sync_scene_prompt_pointer(scene: Scene, prompt_type: str, text: str | None) -> None:
    """Mirror an active ScenePromptVersion's text onto the matching `Scene.*_prompt`
    convenience field that downstream code (image/video gen) reads directly."""
    if prompt_type == "image":
        scene.image_prompt = text
    elif prompt_type == "video":
        scene.video_prompt = text


def _sync_scene_asset_pointer(scene: Scene, asset_type: str, file_path: str | None) -> None:
    """Mirror an active SceneAsset's file_path onto the matching `Scene.*_path`
    convenience field."""
    if asset_type == "image":
        scene.reference_image_path = file_path
    elif asset_type == "video":
        scene.video_path = file_path


def _save_prompt_version(
    db: Session, scene: Scene, prompt_type: str, text: str,
    source: str, cost_usd: float = 0.0,
) -> Optional[ScenePromptVersion]:
    """Save a new prompt version, mark it active, deactivate prior ones,
    and update the Scene's compat field (image_prompt or video_prompt) so
    downstream code that reads scene.image_prompt etc. picks up the change.

    Skips writing a version when text is empty or unchanged from the active.
    """
    from app.services.versioning import make_active
    if not (text or "").strip():
        return None
    if prompt_type not in ("image", "video"):
        return None

    # Skip if identical to current active version (no spurious history rows)
    current = db.exec(
        select(ScenePromptVersion).where(
            ScenePromptVersion.scene_id == scene.id,
            ScenePromptVersion.prompt_type == prompt_type,
            ScenePromptVersion.is_active == True,  # noqa: E712
        )
    ).first()
    if current and current.text.strip() == text.strip() and current.source == source:
        return current

    version = ScenePromptVersion(
        scene_id=scene.id,
        prompt_type=prompt_type,
        text=text,
        source=source,
        cost_usd=cost_usd,
    )
    make_active(
        db,
        target=version,
        siblings_filter=[
            ScenePromptVersion.scene_id == scene.id,
            ScenePromptVersion.prompt_type == prompt_type,
        ],
        on_active_change=lambda v: (
            _sync_scene_prompt_pointer(scene, prompt_type, v.text),
            db.add(scene),
        ),
    )
    db.commit()
    db.refresh(version)
    return version


def _prompt_version_to_dict(v: ScenePromptVersion) -> dict:
    return {
        "id": v.id,
        "scene_id": v.scene_id,
        "prompt_type": v.prompt_type,
        "text": v.text,
        "source": v.source,
        "cost_usd": v.cost_usd,
        "is_active": v.is_active,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }

router = APIRouter()


class SceneCreate(BaseModel):
    project_id: int
    order: int
    audio_start: float
    audio_end: float
    description: Optional[str] = None
    video_prompt: Optional[str] = None
    image_prompt: Optional[str] = None
    video_model: str = "kling-v3.0-pro"
    image_model: str = "gemini-3.1-flash-image"
    resolution: str = "720p"
    align_to_beats: bool = True
    lyrics_segment: Optional[str] = None


class SceneUpdate(BaseModel):
    order: Optional[int] = None
    audio_start: Optional[float] = None
    audio_end: Optional[float] = None
    description: Optional[str] = None
    video_prompt: Optional[str] = None
    image_prompt: Optional[str] = None
    video_model: Optional[str] = None
    image_model: Optional[str] = None
    resolution: Optional[str] = None
    align_to_beats: Optional[bool] = None
    lyrics_segment: Optional[str] = None
    chain_from_prev: Optional[bool] = None


class ExpandPromptsRequest(BaseModel):
    llm_model: Optional[str] = None  # default: google/gemini-3-flash-preview


class GenerateBatchRequest(BaseModel):
    project_id: int
    song_id: int
    target_scene_duration: float = 8.0
    llm_model: Optional[str] = None
    story_seed: Optional[str] = None
    # Where in the global scene list this batch fills. Frontend starts at 0
    # and increments by `batch_size` until `has_more=False`. First batch
    # (start_index=0) wipes any existing scenes; subsequent batches append.
    start_index: int = 0
    batch_size: int = 3


@router.get("")
def list_scenes(project_id: int, db: Session = Depends(get_session)):
    scenes = db.exec(
        select(Scene)
        .where(Scene.project_id == project_id)
        .order_by(Scene.order)
    ).all()
    return [_scene_with_urls(s, db) for s in scenes]


@router.post("", status_code=201)
def create_scene(data: SceneCreate, db: Session = Depends(get_session)):
    scene = Scene(**data.model_dump())
    db.add(scene)
    db.commit()
    db.refresh(scene)
    return scene


@router.get("/{scene_id}")
def get_scene(scene_id: int, db: Session = Depends(get_session)):
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    return _scene_with_urls(scene, db)


@router.patch("/{scene_id}")
def update_scene(scene_id: int, data: SceneUpdate, db: Session = Depends(get_session)):
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    payload = data.model_dump(exclude_none=True)
    boundary_changed = "audio_start" in payload or "audio_end" in payload

    # If user manually edited a prompt, save it as a "manual" version.
    # Pop these out of the payload and handle via _save_prompt_version so
    # versioning stays consistent.
    new_image_prompt = payload.pop("image_prompt", None)
    new_video_prompt = payload.pop("video_prompt", None)

    for k, v in payload.items():
        setattr(scene, k, v)

    if new_image_prompt is not None:
        _save_prompt_version(db, scene, "image", new_image_prompt, source="manual")
    if new_video_prompt is not None:
        _save_prompt_version(db, scene, "video", new_video_prompt, source="manual")
    # Reset status if prompts changed (allow re-generation)
    if any(k in payload for k in ["video_prompt", "image_prompt", "video_model"]):
        if scene.status in ("done", "error"):
            scene.status = "pending"

    # If audio_start/audio_end moved, recompute lyrics_segment from word
    # timestamps so the planner-side prompts and AI Expand always see the
    # actual lyrics that play during this scene.
    if boundary_changed:
        song = db.exec(select(Song).where(Song.project_id == scene.project_id)).first()
        if song and song.transcription_json:
            try:
                words = json.loads(song.transcription_json)
                from app.services.audio_analysis import words_in_range
                sliced = words_in_range(words, float(scene.audio_start), float(scene.audio_end))
                if sliced:
                    scene.lyrics_segment = sliced
            except Exception:
                pass

    db.add(scene)
    db.commit()
    db.refresh(scene)
    return _scene_with_urls(scene, db)


@router.delete("/{scene_id}", status_code=204)
def delete_scene(scene_id: int, db: Session = Depends(get_session)):
    """Delete a scene and everything attached to it.

    Cascade (handled by SQLModel relationships in `models.py`):
      - SceneAsset rows + on-disk files referenced by them
      - ScenePromptVersion rows (full prompt history for this scene)

    GenerationJob rows are intentionally LEFT in place — they reference
    scene_id but are owned by the project, so per-scene cost history stays
    visible in the project-level breakdown even after a scene is deleted.

    Chain cleanup: if the NEXT scene (order+1) had `chain_from_prev=True`
    pointing at this one, we clear that flag. Otherwise we'd leave a
    dangling link — scene N+1 would think it's chained to a now-deleted
    scene, the chain badge would show "#N" as its source even though N is
    gone, and video gen would fail at the "where's the extracted last
    frame?" check. Clearing the flag keeps the next scene intact (the user
    might still want it) but as a standalone scene that uses its own
    first_frame.
    """
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    # Find scene N+1 BEFORE deleting N — once N is gone, the FK lookup
    # by order is the only way to find the orphan-link candidate.
    next_scene = db.exec(select(Scene).where(
        Scene.project_id == scene.project_id,
        Scene.order == scene.order + 1,
    )).first()
    if next_scene and next_scene.chain_from_prev:
        next_scene.chain_from_prev = False
        db.add(next_scene)
    db.delete(scene)
    db.commit()


@router.delete("", status_code=200)
def delete_all_scenes(project_id: int, db: Session = Depends(get_session)):
    """Delete every scene in a project (and cascade their assets / prompt
    versions / generation jobs). Returns the count deleted so the UI can
    show feedback. Disk files are left behind — they're cheap and the user
    can wipe storage/<project_id>/ manually if needed."""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    scenes = db.exec(select(Scene).where(Scene.project_id == project_id)).all()
    count = len(scenes)
    for s in scenes:
        db.delete(s)
    db.commit()
    return {"deleted": count}


@router.post("/generate-batch")
async def generate_scene_batch(req: GenerateBatchRequest, db: Session = Depends(get_session)):
    """Plan + expand a batch of scenes in a single short LLM call.

    Flow (orchestrated by the frontend):
      - First call (start_index=0): deletes any existing scenes, computes the
        full scene-window list from song duration, plans the first batch_size
        scenes. Persists `total_scenes` + `windows` implicitly via the scenes
        themselves (audio_start / audio_end).
      - Subsequent calls (start_index=3, 6, ...): fetches scenes generated so
        far, passes them as continuity context, plans the next batch_size
        scenes with the matching audio windows.
      - Returns has_more=False on the final batch.

    Each call is short (~5-10s for the LLM round-trip + tiny DB writes), so
    the frontend's connection-blip retry logic rarely needs to kick in, and
    the UI sees scenes appear progressively after every batch.
    """
    try:
        song = db.get(Song, req.song_id)
        if not song or song.status != "ready":
            raise HTTPException(400, "Song not analyzed yet — wait for status: ready")
        project = db.get(Project, req.project_id)
        if not project:
            raise HTTPException(404, "Project not found")

        # Persist the seed on first batch so AI tools (and the user, on next
        # reload) inherit the same direction.
        if req.start_index == 0 and req.story_seed is not None and req.story_seed.strip():
            project.story_seed = req.story_seed.strip()
            db.add(project)
            db.commit()

        characters = db.exec(
            select(Character).where(Character.project_id == req.project_id)
        ).all()
        try:
            beats = json.loads(song.beats_json or "[]")
        except Exception:
            beats = []
        try:
            sections = json.loads(song.sections_json or "[]")
        except Exception:
            sections = []
        try:
            words = json.loads(song.transcription_json or "[]")
        except Exception:
            words = []
        try:
            theme_analysis = json.loads(song.theme_analysis or "{}")
        except Exception:
            theme_analysis = {}

        from app.services.scene_planner import plan_scene_batch, compute_scene_windows
        windows = compute_scene_windows(song.duration or 0, req.target_scene_duration)
        total_scenes = len(windows)

        if req.start_index >= total_scenes:
            return {"batch_scenes": [], "scenes_so_far": total_scenes,
                    "total_planned": total_scenes, "has_more": False}

        # First batch wipes any existing scenes so the user gets a clean
        # plan (this matches `replace_existing=true` on the old auto-plan).
        if req.start_index == 0:
            existing = db.exec(select(Scene).where(Scene.project_id == req.project_id)).all()
            for s in existing:
                db.delete(s)
            db.commit()

        # Pull scenes already planned in earlier batches for continuity context.
        previous_scenes_raw = db.exec(
            select(Scene).where(Scene.project_id == req.project_id).order_by(Scene.order)
        ).all()
        previous_scenes = [
            {
                "order": s.order,
                "audio_start": s.audio_start,
                "audio_end": s.audio_end,
                "description": s.description,
                "image_prompt": s.image_prompt,
                "video_prompt": s.video_prompt,
            }
            for s in previous_scenes_raw
        ]

        batch_windows = windows[req.start_index: req.start_index + req.batch_size]

        # ─── Idempotency guard ──────────────────────────────────────────────
        # If the frontend's per-batch retry kicks in after the previous
        # attempt's DB writes landed but the HTTP response was lost (network
        # blip AFTER commit), we'd otherwise re-run the LLM call and
        # double-insert at the same orders. Detect that case by checking
        # whether scenes already exist for all orders in this batch's window —
        # if so, return them as the batch result and short-circuit.
        target_orders = list(range(req.start_index + 1,
                                   req.start_index + 1 + len(batch_windows)))
        existing_by_order = {s.order: s for s in previous_scenes_raw if s.order in target_orders}
        if len(existing_by_order) == len(target_orders) and target_orders:
            print(f"[generate-batch] start_index={req.start_index}: scenes at "
                  f"orders {target_orders} already exist — returning idempotent "
                  f"no-op (retry-safe path).")
            existing = [existing_by_order[o] for o in target_orders]
            next_index = req.start_index + len(existing)
            return {
                "batch_scenes": [_scene_with_urls(s, db) for s in existing],
                "scenes_so_far": next_index,
                "total_planned": total_scenes,
                "has_more": next_index < total_scenes,
                "next_start_index": next_index if next_index < total_scenes else None,
            }
        # Partial-overlap case (some scenes exist, others don't): treat as
        # corrupt state, delete the partial ones in this batch's range so the
        # LLM call below can repopulate cleanly without leaving orphans.
        if existing_by_order:
            print(f"[generate-batch] start_index={req.start_index}: partial overlap "
                  f"({len(existing_by_order)}/{len(target_orders)} scenes already exist) "
                  f"— deleting the partials and re-planning the batch.")
            for s in existing_by_order.values():
                db.delete(s)
            db.commit()

        char_dicts = [{"name": c.name, "description": c.description} for c in characters]
        scene_dicts = await plan_scene_batch(
            title=song.title,
            artist=song.artist or "",
            style=project.style or "",
            aspect_ratio=project.aspect_ratio,
            bpm=song.bpm or 120,
            key=song.key or "C",
            sections=sections,
            beats=beats,
            words=words,
            characters=char_dicts,
            target_scene_duration=req.target_scene_duration,
            duration=song.duration or 0,
            llm_model=req.llm_model or "google/gemini-3-flash-preview",
            story_seed=(req.story_seed or project.story_seed or "").strip() or None,
            theme_analysis=theme_analysis,
            full_lyrics=song.lyrics,
            previous_scenes=previous_scenes,
            batch_windows=batch_windows,
            batch_start_index=req.start_index,
            total_scenes=total_scenes,
        )

        # Sanity-check the LLM response BEFORE attempting to insert. The
        # parser returns whatever shape the model emitted — if it's empty or
        # something other than a list of dicts, we'd silently insert nothing
        # and the frontend would loop forever at the same start_index.
        if not isinstance(scene_dicts, list) or not scene_dicts:
            raise HTTPException(
                502,
                f"Generate-batch: LLM returned no scenes for batch starting "
                f"#{req.start_index + 1} ({req.start_index}-{req.start_index + req.batch_size}). "
                f"Got: {type(scene_dicts).__name__}. The model may have hit a content "
                f"filter or returned malformed JSON. Try re-running — transient "
                f"failures are common on per-batch calls.",
            )

        # Persist this batch's scenes one-by-one so frontend polling sees
        # them appear progressively if the request happens to take longer.
        default_res = (VIDEO_MODELS.get(settings.default_video_model, {})
                       .get("resolutions") or ["720p"])[0]
        from app.services.audio_analysis import words_in_range
        created: list[Scene] = []
        for i, sd in enumerate(scene_dicts):
            if not isinstance(sd, dict):
                continue
            order = req.start_index + i + 1
            # Trust the pre-determined window over whatever the LLM returned.
            try:
                s_start, s_end = batch_windows[i]
            except IndexError:
                continue
            lyrics = words_in_range(words, float(s_start), float(s_end)) if words else ""
            scene = Scene(
                project_id=req.project_id,
                order=order,
                audio_start=float(s_start),
                audio_end=float(s_end),
                description=sd.get("description"),
                video_prompt=sd.get("video_prompt"),
                image_prompt=sd.get("image_prompt"),
                lyrics_segment=lyrics,
                video_model=settings.default_video_model,
                image_model=settings.default_image_model,
                resolution=default_res,
                # The batch flow IS the expansion — every scene comes back
                # with a full video_prompt + image_prompt + description, so
                # mark expanded immediately for the green "AI" badge.
                prompts_expanded=True,
            )
            db.add(scene)
            db.commit()
            db.refresh(scene)
            vp = (sd.get("video_prompt") or "").strip()
            ip = (sd.get("image_prompt") or "").strip()
            if vp:
                _save_prompt_version(db, scene, "video", vp, source="plan")
            if ip:
                _save_prompt_version(db, scene, "image", ip, source="plan")
            created.append(scene)

        # Guard against the "LLM returned a list but every item was unusable"
        # case — without this, next_index would equal start_index, has_more
        # would still be True, and the frontend would re-fire the same batch
        # forever in a tight loop.
        if not created:
            raise HTTPException(
                502,
                f"Generate-batch: LLM returned {len(scene_dicts)} item(s) for batch "
                f"#{req.start_index + 1}, but none were valid scene objects "
                f"(missing description / prompts, wrong shape). Sample: "
                f"{str(scene_dicts[0])[:200] if scene_dicts else '<empty>'}. "
                f"Try re-running.",
            )

        # Per-batch cost row so the project total grows incrementally rather
        # than only at the end.
        cost, detail = pricing.llm_plan_cost()
        # Scale roughly by batch fraction so 1 batch ≠ full plan cost.
        cost = round(cost * (len(batch_windows) / max(total_scenes, 1)), 4)
        db.add(GenerationJob(
            project_id=req.project_id, job_type="llm_plan",
            provider="openrouter", status="completed",
            cost_usd=cost,
            cost_detail=f"{detail} · batch {req.start_index // req.batch_size + 1} ({len(created)} scenes)",
            completed_at=datetime.utcnow(),
        ))
        db.commit()

        next_index = req.start_index + len(created)
        return {
            "batch_scenes": [_scene_with_urls(s, db) for s in created],
            "scenes_so_far": next_index,
            "total_planned": total_scenes,
            "has_more": next_index < total_scenes,
            "next_start_index": next_index if next_index < total_scenes else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        print("[generate-batch] uncaught:")
        traceback.print_exc()
        raise HTTPException(
            500,
            f"Generate-batch crashed ({type(e).__name__}: {str(e)[:300]}). "
            f"start_index={req.start_index}, batch_size={req.batch_size}. "
            f"Check backend log for the traceback.",
        )


@router.post("/{scene_id}/chain-next")
def chain_to_next(scene_id: int, db: Session = Depends(get_session)):
    """Make sure scene N+1 exists and is chained to scene N.

    Used by the "chain to next" icon on each scene row. The icon answers the
    question "should the NEXT clip pick up exactly where this one ends?" —
    not "where did the previous clip end?" That direction makes the icon
    actionable on scene 1 (where it's the natural way to add scene 2), and
    keeps the meaning consistent for every later scene too.

    Three cases:
      1. Scene N+1 doesn't exist yet → create it with chain_from_prev=True,
         empty prompts (user fills via the wand button), inheriting model
         settings from this scene, audio window placed right after this one.
      2. Scene N+1 exists with chain_from_prev=False → flip it on.
      3. Scene N+1 exists with chain_from_prev=True → no-op.

    Returns the (created or updated) next scene.
    """
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")

    next_scene = db.exec(select(Scene).where(
        Scene.project_id == scene.project_id,
        Scene.order == scene.order + 1,
    )).first()

    if next_scene:
        if not next_scene.chain_from_prev:
            next_scene.chain_from_prev = True
            db.add(next_scene)
            db.commit()
            db.refresh(next_scene)
        return _scene_with_urls(next_scene, db)

    # Case 1: create. Inherit per-scene settings from THIS scene so the user
    # doesn't have to re-pick models/resolution every time they add a scene.
    duration = max(3.0, scene.audio_end - scene.audio_start)
    next_scene = Scene(
        project_id=scene.project_id,
        order=scene.order + 1,
        audio_start=scene.audio_end,
        audio_end=scene.audio_end + duration,
        description="",
        # Leave prompts empty — the user populates them via the wand button
        # (vision-grounded continuation prompt) or by typing manually.
        video_prompt=None,
        image_prompt=None,
        chain_from_prev=True,
        video_model=scene.video_model,
        image_model=scene.image_model,
        resolution=scene.resolution,
        align_to_beats=scene.align_to_beats,
        prompts_expanded=False,
        status="pending",
    )
    db.add(next_scene)
    db.commit()
    db.refresh(next_scene)
    return _scene_with_urls(next_scene, db)


@router.post("/{scene_id}/expand-prompts")
async def expand_prompts(
    scene_id: int,
    req: Optional[ExpandPromptsRequest] = None,
    db: Session = Depends(get_session),
):
    """Re-generate detailed video/image prompts for a scene using LLM."""
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")

    project = db.get(Project, scene.project_id)
    characters = db.exec(
        select(Character).where(Character.project_id == scene.project_id)
    ).all()

    # Look up neighboring scenes for narrative context
    prev_scene = db.exec(
        select(Scene).where(
            Scene.project_id == scene.project_id,
            Scene.order < scene.order,
        ).order_by(Scene.order.desc())
    ).first()
    next_scene = db.exec(
        select(Scene).where(
            Scene.project_id == scene.project_id,
            Scene.order > scene.order,
        ).order_by(Scene.order)
    ).first()

    from app.services.scene_planner import generate_scene_prompts
    prompts = await generate_scene_prompts(
        description=scene.description or "",
        style=project.style or "",
        characters=[{"name": c.name, "description": c.description} for c in characters],
        lyrics=scene.lyrics_segment or "",
        previous_scene=prev_scene.description if prev_scene else None,
        next_scene=next_scene.description if next_scene else None,
        previous_image_prompt=prev_scene.image_prompt if prev_scene else None,
        next_image_prompt=next_scene.image_prompt if next_scene else None,
        duration_seconds=scene.audio_end - scene.audio_start,
        story_seed=(project.story_seed or "").strip() or None,
        llm_model=(req.llm_model if req else None) or "google/gemini-3-flash-preview",
    )
    # Only overwrite if the LLM gave us a non-empty string — never clobber
    # existing prompts with empty strings from a flaky response.
    new_vp = (prompts.get("video_prompt") or "").strip()
    new_ip = (prompts.get("image_prompt") or "").strip()
    if new_vp:
        _save_prompt_version(db, scene, "video", new_vp, source="expand")
    if new_ip:
        _save_prompt_version(db, scene, "image", new_ip, source="expand")
    if new_vp or new_ip:
        scene.prompts_expanded = True
    db.add(scene)

    # Record LLM expand cost
    exp_cost, exp_detail = pricing.llm_expand_cost()
    db.add(GenerationJob(
        project_id=scene.project_id, scene_id=scene.id, job_type="llm_expand",
        provider="openrouter", status="completed",
        cost_usd=exp_cost, cost_detail=exp_detail,
        completed_at=datetime.utcnow(),
    ))

    db.commit()
    db.refresh(scene)
    return _scene_with_urls(scene, db)


class ContinuationPromptRequest(BaseModel):
    llm_model: Optional[str] = None  # default: google/gemini-3-flash-preview


@router.post("/{scene_id}/continuation-prompt")
async def generate_continuation_prompt(
    scene_id: int,
    req: Optional[ContinuationPromptRequest] = None,
    db: Session = Depends(get_session),
):
    """Generate this scene's video + image prompts grounded on the PREVIOUS
    scene's actual rendered last frame.

    Used when `chain_from_prev` is on and the user hasn't manually written
    a prompt yet: feeds the real last-frame image into a vision-LLM along
    with the project style, story seed, characters, and lyrics segment,
    so the resulting motion flows naturally from where the previous video
    ended — no teleporting characters, no jump cuts.

    Preconditions:
      - prev scene exists (i.e. this scene's order >= 2)
      - prev scene has a rendered video AND an extracted_last_frame file
        on disk (`generation_service._extract_last_frame` ran successfully)

    If those aren't met, returns a 400 with an actionable message — typically
    "generate scene N first."
    """
    import os
    try:
        scene = db.get(Scene, scene_id)
        if not scene:
            raise HTTPException(404, "Scene not found")

        project = db.get(Project, scene.project_id)
        if not project:
            raise HTTPException(404, "Project not found")

        prev_scene = db.exec(
            select(Scene).where(
                Scene.project_id == scene.project_id,
                Scene.order == scene.order - 1,
            )
        ).first()
        if not prev_scene:
            raise HTTPException(
                400,
                f"Scene #{scene.order} has no previous scene to chain from "
                f"(it's the first scene). Use AI Expand instead.",
            )
        if not prev_scene.extracted_last_frame_path or not os.path.exists(
            prev_scene.extracted_last_frame_path
        ):
            raise HTTPException(
                400,
                f"Scene #{prev_scene.order}'s last frame isn't available yet — "
                f"generate its video first so the chain handoff frame exists. "
                f"This endpoint feeds that real frame into the LLM as visual "
                f"context, so without it there's nothing to continue from.",
            )

        characters = db.exec(
            select(Character).where(Character.project_id == scene.project_id)
        ).all()

        # Song-level context — without these, the continuation LLM has no
        # signal for mood/arc/narrative and every chained scene drifts into
        # "more of the same." Load the song's theme_analysis JSON and full
        # lyrics so the LLM can pitch the new clip to the right emotional
        # beat (early establishment vs climax vs resolution).
        song = db.exec(
            select(Song).where(Song.project_id == scene.project_id)
            .order_by(Song.created_at.desc())
        ).first()
        try:
            theme_analysis = json.loads(song.theme_analysis) if song and song.theme_analysis else None
            if not isinstance(theme_analysis, dict):
                theme_analysis = None
        except Exception:
            theme_analysis = None
        full_lyrics = song.lyrics if song else None

        # Arc-so-far — descriptions of every prior scene, so the LLM sees
        # what beats and verbs already happened and avoids recycling them.
        prior_scenes = db.exec(
            select(Scene).where(
                Scene.project_id == scene.project_id,
                Scene.order < scene.order,
            ).order_by(Scene.order)
        ).all()
        all_prev_scenes = [
            {
                "order": s.order,
                "description": s.description or "",
                "video_prompt": s.video_prompt or "",
            }
            for s in prior_scenes
        ]

        # Narrative position — where this clip sits in the song. Drives
        # the stage cue (OPENING / RISING / MID / CLIMAX / RESOLUTION).
        song_duration = (song.duration if song and song.duration else 0.0)
        position_pct = None
        if song_duration > 0:
            # Use the midpoint of this scene's window so a 4s clip near the
            # end doesn't get bucketed as "earlier than it really is."
            midpoint = (scene.audio_start + scene.audio_end) / 2.0
            position_pct = max(0.0, min(1.0, midpoint / song_duration))
        # Rough scene-count estimate so the prompt can say "scene 4 of ~9"
        total_scenes_estimate = None
        if song_duration > 0:
            avg_dur = scene.audio_end - scene.audio_start
            if avg_dur > 0:
                total_scenes_estimate = max(1, int(round(song_duration / avg_dur)))

        from app.services.scene_planner import generate_continuation_prompts
        prompts = await generate_continuation_prompts(
            last_frame_path=prev_scene.extracted_last_frame_path,
            style=project.style or "",
            characters=[{"name": c.name, "description": c.description} for c in characters],
            lyrics=scene.lyrics_segment or "",
            duration_seconds=scene.audio_end - scene.audio_start,
            story_seed=(project.story_seed or "").strip() or None,
            prev_description=prev_scene.description,
            prev_video_prompt=prev_scene.video_prompt,
            this_description=scene.description,
            theme_analysis=theme_analysis,
            full_lyrics=full_lyrics,
            all_prev_scenes=all_prev_scenes,
            audio_position_pct=position_pct,
            total_scenes_estimate=total_scenes_estimate,
            llm_model=(req.llm_model if req else None) or "google/gemini-3-flash-preview",
        )

        new_vp = (prompts.get("video_prompt") or "").strip()
        new_desc = (prompts.get("description") or "").strip()
        if not new_vp:
            raise HTTPException(
                502,
                "Continuation LLM returned empty video_prompt. The model may "
                "have refused the input image (content filter on the last "
                "frame) or returned malformed JSON. Try a different LLM, or "
                "write the prompt manually.",
            )

        # Persist via the versioning helper — same source label so the
        # history makes it obvious this came from the continuation path
        # (versus a plain `expand` or `plan`).
        # NOTE: deliberately NOT writing image_prompt. Chained scenes use the
        # prev video's extracted last frame as their first_frame at render
        # time; any image_prompt would be silently ignored and would mislead
        # the user about what the model actually sees. Keep it empty here.
        _save_prompt_version(db, scene, "video", new_vp, source="continuation")
        if new_desc:
            # Trim to a sane length so it fits the row's truncated header
            # line. The LLM was told ≤120 chars; clamp defensively anyway.
            scene.description = new_desc[:140]
        scene.prompts_expanded = True
        db.add(scene)

        # Cost row — reuse the existing llm_expand pricing slot. This call
        # ships an image to the LLM so it's a bit pricier than text-only
        # expand, but the difference is small (~$0.001-0.005). Tag the
        # cost_detail so it shows up distinctly in the job log.
        exp_cost, exp_detail = pricing.llm_expand_cost()
        db.add(GenerationJob(
            project_id=scene.project_id, scene_id=scene.id, job_type="llm_expand",
            provider="openrouter", status="completed",
            cost_usd=exp_cost,
            cost_detail=f"{exp_detail} · continuation (vision-grounded on prev last frame)",
            completed_at=datetime.utcnow(),
        ))

        db.commit()
        db.refresh(scene)
        return _scene_with_urls(scene, db)
    except HTTPException:
        raise
    except Exception as e:
        print(f"[continuation-prompt] uncaught:")
        traceback.print_exc()
        raise HTTPException(
            500,
            f"Continuation-prompt endpoint crashed ({type(e).__name__}: {str(e)[:300]}).",
        )


class SoftenPromptRequest(BaseModel):
    field: str  # "video_prompt" | "image_prompt"
    llm_model: Optional[str] = None


@router.post("/{scene_id}/soften-prompt")
async def soften_scene_prompt(
    scene_id: int,
    req: SoftenPromptRequest,
    db: Session = Depends(get_session),
):
    """Rewrite a scene's video_prompt or image_prompt to bypass content
    filters while preserving cinematic intent. Use when video gen returns
    'content may have been filtered'."""
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    if req.field not in ("video_prompt", "image_prompt"):
        raise HTTPException(400, "field must be video_prompt or image_prompt")

    project = db.get(Project, scene.project_id)
    raw = getattr(scene, req.field) or ""
    if not raw.strip():
        raise HTTPException(400, f"{req.field} is empty")

    from app.services.scene_planner import soften_prompt as _soften
    result = await _soften(
        raw_prompt=raw,
        style=project.style if project else "",
        error_message=scene.error_message or "",
        llm_model=req.llm_model or "google/gemini-3-flash-preview",
    )
    new_text = (result.get("softened") or "").strip()
    if not new_text:
        raise HTTPException(500, "Soften returned empty result")
    prompt_type = "video" if req.field == "video_prompt" else "image"
    _save_prompt_version(db, scene, prompt_type, new_text, source="soften")
    # Reset error so the user can retry generation
    scene.error_message = None
    if scene.status == "error":
        scene.status = "image_ready" if scene.reference_image_path else "pending"

    cost, detail = pricing.llm_expand_cost()
    db.add(GenerationJob(
        project_id=scene.project_id, scene_id=scene_id, job_type="llm_expand",
        provider="openrouter", status="completed",
        cost_usd=cost, cost_detail=f"Soften {req.field} — {detail}",
        completed_at=datetime.utcnow(),
    ))
    db.add(scene); db.commit(); db.refresh(scene)
    return _scene_with_urls(scene, db)


@router.post("/{scene_id}/clear")
def clear_scene(scene_id: int, db: Session = Depends(get_session)):
    """Wipe all generated assets for a scene (images, videos + files on disk)
    but keep the scene row + plan/prompts. Resets status to 'pending' so the
    user can re-generate fresh."""
    import os as _os
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")

    assets = db.exec(select(SceneAsset).where(SceneAsset.scene_id == scene_id)).all()
    deleted_files = 0
    for a in assets:
        if a.file_path and _os.path.exists(a.file_path):
            try:
                _os.remove(a.file_path)
                deleted_files += 1
            except OSError:
                pass
        db.delete(a)

    scene.reference_image_path = None
    scene.video_path = None
    scene.openrouter_job_id = None
    scene.cancel_requested = False
    scene.error_message = None
    scene.status = "pending"
    db.add(scene); db.commit()
    return {
        "scene_id": scene_id,
        "assets_removed": len(assets),
        "files_deleted": deleted_files,
    }


# Backward-compat alias — historical callers in this module use this name.
# The actual implementation lives in app.services.urls.to_storage_url.
from app.services.urls import to_storage_url as _to_storage_url  # noqa: E402


def _asset_to_dict(a: SceneAsset) -> dict:
    return {
        "id": a.id,
        "scene_id": a.scene_id,
        "asset_type": a.asset_type,
        "model_used": a.model_used,
        "cost_usd": a.cost_usd,
        "cost_detail": a.cost_detail,
        "metadata_json": a.metadata_json,
        "is_active": a.is_active,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "url": _to_storage_url(a.file_path),
    }


def _scene_with_urls(scene: Scene, db: Optional[Session] = None) -> dict:
    d = scene.model_dump()
    d["reference_image_url"] = _to_storage_url(scene.reference_image_path)
    d["video_url"] = _to_storage_url(scene.video_path)
    # Used by the chain UI to display the actual handoff frame.
    # cache_bust=True because this file is REWRITTEN IN PLACE on every video
    # regen of the source scene (same filename `scene_N_last.jpg`). Without
    # the mtime query string, the URL is stable and the browser serves the
    # cached previous JPG even after the file changes on disk — which makes
    # the "chained from prev" preview look like it's from an earlier video.
    d["extracted_last_frame_url"] = _to_storage_url(scene.extracted_last_frame_path, cache_bust=True)
    d["duration"] = round(scene.audio_end - scene.audio_start, 2)
    if db is not None:
        assets = db.exec(
            select(SceneAsset).where(SceneAsset.scene_id == scene.id).order_by(SceneAsset.created_at.desc())
        ).all()
        d["assets"] = [_asset_to_dict(a) for a in assets]
        prompt_versions = db.exec(
            select(ScenePromptVersion)
            .where(ScenePromptVersion.scene_id == scene.id)
            .order_by(ScenePromptVersion.created_at.desc())
        ).all()
        d["prompt_versions"] = [_prompt_version_to_dict(v) for v in prompt_versions]
    else:
        d["assets"] = []
        d["prompt_versions"] = []
    return d


# ---------------------------------------------------------------------------
# Prompt version endpoints — list / activate / delete
# ---------------------------------------------------------------------------

@router.get("/{scene_id}/prompts")
def list_scene_prompts(
    scene_id: int,
    prompt_type: Optional[str] = None,  # filter to "image" or "video"
    db: Session = Depends(get_session),
):
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    q = select(ScenePromptVersion).where(ScenePromptVersion.scene_id == scene_id)
    if prompt_type in ("image", "video"):
        q = q.where(ScenePromptVersion.prompt_type == prompt_type)
    versions = db.exec(q.order_by(ScenePromptVersion.created_at.desc())).all()
    return [_prompt_version_to_dict(v) for v in versions]


@router.post("/{scene_id}/prompts/{version_id}/activate")
def activate_prompt_version(scene_id: int, version_id: int, db: Session = Depends(get_session)):
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    version = db.get(ScenePromptVersion, version_id)
    if not version or version.scene_id != scene_id:
        raise HTTPException(404, "Prompt version not found")

    from app.services.versioning import make_active
    make_active(
        db,
        target=version,
        siblings_filter=[
            ScenePromptVersion.scene_id == scene_id,
            ScenePromptVersion.prompt_type == version.prompt_type,
        ],
        on_active_change=lambda v: (
            _sync_scene_prompt_pointer(scene, v.prompt_type, v.text),
            db.add(scene),
        ),
    )
    db.commit()
    return _scene_with_urls(scene, db)


@router.delete("/{scene_id}/prompts/{version_id}", status_code=204)
def delete_prompt_version(scene_id: int, version_id: int, db: Session = Depends(get_session)):
    from app.services.versioning import delete_and_promote
    version = db.get(ScenePromptVersion, version_id)
    if not version or version.scene_id != scene_id:
        raise HTTPException(404, "Prompt version not found")
    scene = db.get(Scene, scene_id)
    prompt_type = version.prompt_type
    delete_and_promote(
        db,
        deleted=version,
        siblings_filter=[
            ScenePromptVersion.scene_id == scene_id,
            ScenePromptVersion.prompt_type == prompt_type,
        ],
        on_active_change=lambda nxt: (
            _sync_scene_prompt_pointer(scene, prompt_type, nxt.text if nxt else None),
            db.add(scene),
        ),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Scene asset endpoints — list versions and pick which is active
# ---------------------------------------------------------------------------

@router.get("/{scene_id}/assets")
def list_scene_assets(scene_id: int, db: Session = Depends(get_session)):
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    assets = db.exec(
        select(SceneAsset).where(SceneAsset.scene_id == scene_id).order_by(SceneAsset.created_at.desc())
    ).all()
    return [_asset_to_dict(a) for a in assets]


@router.post("/{scene_id}/assets/{asset_id}/activate")
def activate_scene_asset(scene_id: int, asset_id: int, db: Session = Depends(get_session)):
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    asset = db.get(SceneAsset, asset_id)
    if not asset or asset.scene_id != scene_id:
        raise HTTPException(404, "Asset not found")

    from app.services.versioning import make_active
    make_active(
        db,
        target=asset,
        siblings_filter=[
            SceneAsset.scene_id == scene_id,
            SceneAsset.asset_type == asset.asset_type,
        ],
        on_active_change=lambda a: (
            _sync_scene_asset_pointer(scene, a.asset_type, a.file_path),
            db.add(scene),
        ),
    )
    db.commit()
    return _scene_with_urls(scene, db)


@router.delete("/{scene_id}/assets/{asset_id}", status_code=204)
def delete_scene_asset(scene_id: int, asset_id: int, db: Session = Depends(get_session)):
    """Delete a single asset version. Promotes the next-most-recent asset of
    the same type to active if the deleted one was active."""
    from app.services.versioning import delete_and_promote
    import os as _os
    asset = db.get(SceneAsset, asset_id)
    if not asset or asset.scene_id != scene_id:
        raise HTTPException(404, "Asset not found")
    scene = db.get(Scene, scene_id)
    asset_type = asset.asset_type
    file_path = asset.file_path

    delete_and_promote(
        db,
        deleted=asset,
        siblings_filter=[
            SceneAsset.scene_id == scene_id,
            SceneAsset.asset_type == asset_type,
        ],
        on_active_change=lambda nxt: (
            _sync_scene_asset_pointer(scene, asset_type, nxt.file_path if nxt else None),
            db.add(scene),
        ),
    )
    db.commit()

    # Best-effort delete on disk
    if file_path:
        try:
            if _os.path.exists(file_path):
                _os.remove(file_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Manual asset I/O — first-frame / audio-chunk download + video upload
# ---------------------------------------------------------------------------
# These let the user round-trip a scene's inputs through external tools (e.g.
# fal's Seedance web UI, a local video editor) and bring the result back in.
# Without them the user can't easily verify what the pipeline is actually
# sending the model, or skip generation when they already have the clip.

@router.get("/{scene_id}/first-frame")
def download_first_frame(scene_id: int, db: Session = Depends(get_session)):
    """Return the still that video gen would use as the first frame:
       - Chained scene → the previous scene's extracted last frame
       - Otherwise → this scene's active reference image
    Falls through with 404 when neither exists."""
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")

    src_path: Optional[str] = None
    label = "scene_first_frame"
    if scene.chain_from_prev:
        prev = db.exec(
            select(Scene).where(
                Scene.project_id == scene.project_id,
                Scene.order == scene.order - 1,
            )
        ).first()
        if not prev or not prev.extracted_last_frame_path:
            raise HTTPException(
                400,
                "Chained scene has no upstream frame yet — generate the "
                "previous scene's video first.",
            )
        src_path = prev.extracted_last_frame_path
        label = f"scene_{scene.order}_chained_from_{prev.order}_last_frame"
    elif scene.reference_image_path:
        src_path = scene.reference_image_path
        label = f"scene_{scene.order}_first_frame"

    if not src_path or not os.path.exists(src_path):
        raise HTTPException(404, "First frame not available — nothing generated or chained yet.")

    ext = src_path.rsplit(".", 1)[-1].lower() if "." in src_path else "jpg"
    return FileResponse(
        src_path,
        media_type=f"image/{'jpeg' if ext == 'jpg' else ext}",
        filename=f"{label}.{ext}",
    )


@router.get("/{scene_id}/audio-chunk")
async def download_audio_chunk(scene_id: int, db: Session = Depends(get_session)):
    """Slice the song segment for this scene's [audio_start, audio_end]
    window (via the same ffmpeg helper the audio-sync gen path uses) and
    return it as an MP3 download. Useful for testing fal Seedance's web UI
    with the exact audio our backend sends."""
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    from app.services.generation_service import _extract_audio_segment
    try:
        path = await _extract_audio_segment(scene, db)
    except Exception as e:
        raise HTTPException(500, f"Audio slice failed ({type(e).__name__}: {str(e)[:200]})")
    if not os.path.exists(path):
        raise HTTPException(500, "Audio slice helper returned a path that doesn't exist on disk.")
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=f"scene_{scene.order}_{scene.audio_start:.1f}-{scene.audio_end:.1f}s.mp3",
    )


@router.post("/{scene_id}/upload-video", status_code=201)
def upload_scene_video(
    scene_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    """Skip generation — upload a pre-made MP4 and register it as the
    scene's active video variant. Status flips to 'done', last_frame gets
    re-extracted so downstream chaining stays consistent."""
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")

    filename = file.filename or "uploaded.mp4"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp4"
    if ext not in ("mp4", "mov", "webm"):
        raise HTTPException(400, f"Unsupported video extension: {ext}")

    ts = int(datetime.utcnow().timestamp())
    dest_dir = os.path.join(settings.storage_dir, str(scene.project_id), "videos")
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, f"scene_{scene.id}_upload_{ts}.{ext}")
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Register as a new video asset and make it active via the same path the
    # generated videos take, so the variant gallery / activate / delete flow
    # works identically.
    from app.services.versioning import make_active
    from app.services.generation_service import _extract_last_frame
    asset = SceneAsset(
        scene_id=scene.id,
        asset_type="video",
        file_path=dest_path,
        is_active=False,  # make_active flips this
        model_used="uploaded",
        cost_usd=0.0,
        cost_detail="user-uploaded video (no generation cost)",
        metadata_json=json.dumps({"provider": "upload", "uploaded_filename": filename}),
    )
    make_active(
        db,
        target=asset,
        siblings_filter=[
            SceneAsset.scene_id == scene.id,
            SceneAsset.asset_type == "video",
        ],
        on_active_change=lambda a: setattr(scene, "video_path", a.file_path),
    )

    # Extract the last frame so downstream chained scenes can use it as
    # their first_frame, just like a generated video would.
    last_frame_dest = os.path.join(
        settings.storage_dir, str(scene.project_id), "extracted",
        f"scene_{scene.order}_last.jpg",
    )
    os.makedirs(os.path.dirname(last_frame_dest), exist_ok=True)
    if _extract_last_frame(dest_path, last_frame_dest):
        scene.extracted_last_frame_path = last_frame_dest

    scene.status = "done"
    scene.error_message = None
    db.add(scene)
    db.commit()
    return {"scene_id": scene.id, "asset_id": asset.id, "file_path": dest_path}
