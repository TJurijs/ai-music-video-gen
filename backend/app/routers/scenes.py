import json
import traceback
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Optional

from app.database import get_session
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
    elif asset_type == "lipsync":
        scene.lipsync_path = file_path


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
    generate_audio: bool = False
    lipsync_enabled: bool = False
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
    lipsync_model: Optional[str] = None
    resolution: Optional[str] = None
    generate_audio: Optional[bool] = None
    lipsync_enabled: Optional[bool] = None
    align_to_beats: Optional[bool] = None
    lyrics_segment: Optional[str] = None
    chain_from_prev: Optional[bool] = None


class AutoPlanRequest(BaseModel):
    project_id: int
    song_id: int
    target_scene_duration: float = 8.0
    replace_existing: bool = True
    llm_model: Optional[str] = None  # default: google/gemini-3-flash-preview
    story_seed: Optional[str] = None  # short narrative direction the planner anchors on


class ExpandPromptsRequest(BaseModel):
    llm_model: Optional[str] = None  # default: google/gemini-3-flash-preview


class ExpandAllRequest(BaseModel):
    project_id: int
    llm_model: Optional[str] = None
    only_empty: bool = True  # only expand scenes whose video_prompt is blank


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
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    db.delete(scene)
    db.commit()


@router.post("/auto-plan")
async def auto_plan_scenes(req: AutoPlanRequest, db: Session = Depends(get_session)):
    """Generate scene plan using LLM aligned to song analysis."""
    # Top-level try/except — anything that escapes here would otherwise
    # become an opaque "Internal Server Error" with no detail in the toast.
    try:
        song = db.get(Song, req.song_id)
        if not song or song.status != "ready":
            raise HTTPException(400, "Song not analyzed yet — wait for status: ready")

        project = db.get(Project, req.project_id)
        if not project:
            raise HTTPException(404, "Project not found")

        characters = db.exec(
            select(Character).where(Character.project_id == req.project_id)
        ).all()

        # Defensive json.loads — if any of these blobs are corrupted (e.g.
        # the song analysis was interrupted), don't 500 the whole request.
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

        # Theme analysis was generated as a separate post-transcription pass.
        # Feed it back so the planner respects the lyric narrative we already discovered.
        theme_analysis: dict = {}
        if song.theme_analysis:
            try:
                theme_analysis = json.loads(song.theme_analysis)
            except Exception:
                theme_analysis = {}
        return await _do_auto_plan(req, db, song, project, characters, beats, sections, words, theme_analysis)
    except HTTPException:
        raise
    except Exception as e:
        print("[auto-plan] uncaught exception in handler:")
        traceback.print_exc()
        raise HTTPException(
            500,
            f"Auto-plan failed ({type(e).__name__}: {str(e)[:300]}). "
            f"See backend log for the full traceback.",
        )


async def _do_auto_plan(
    req: "AutoPlanRequest", db: Session, song, project, characters, beats, sections, words, theme_analysis,
):
    """Inner implementation of auto-plan. Split out so the outer handler is
    just an exception-safety wrapper — any unhandled exception in this
    function gets logged and surfaced with a useful message."""

    from app.services.scene_planner import auto_plan_scenes as plan
    try:
        scene_dicts = await plan(
            title=song.title,
            artist=song.artist or "",
            style=project.style or "",
            aspect_ratio=project.aspect_ratio,
            bpm=song.bpm or 120,
            key=song.key or "C",
            sections=sections,
            beats=beats,
            words=words,
            characters=[{"name": c.name, "description": c.description} for c in characters],
            target_scene_duration=req.target_scene_duration,
            duration=song.duration or 0,
            llm_model=req.llm_model or "google/gemini-3-flash-preview",
            story_seed=req.story_seed,
            theme_analysis=theme_analysis,
            full_lyrics=song.lyrics,
        )
    except Exception as e:
        print("[auto-plan] LLM call failed:")
        traceback.print_exc()
        raise HTTPException(
            500,
            f"Auto-plan LLM call failed ({type(e).__name__}: {str(e)[:300]}). "
            f"Try again, or pick a different LLM in the Plan picker.",
        )

    # Hard fail if the LLM gave us nothing usable — never silently delete
    # existing scenes and replace with an empty plan.
    if not scene_dicts or not isinstance(scene_dicts, list):
        raise HTTPException(
            502,
            f"Auto-plan returned no scenes (got {type(scene_dicts).__name__}). "
            "The LLM response was malformed or empty. Existing scenes (if any) were NOT deleted. Try again, or pick a different LLM in the Plan picker.",
        )

    # Everything past the LLM call gets wrapped in a single try/except — DB
    # constraint violations, malformed scene dicts (LLM returning the wrong
    # shape), or serialization quirks here would otherwise become an
    # unhelpful generic 500. Log the full traceback so we can debug from
    # the backend log; surface the exception class + first 300 chars to the
    # client so the toast says something actionable.
    try:
        # Record LLM scene plan cost
        plan_cost, plan_detail = pricing.llm_plan_cost()
        db.add(GenerationJob(
            project_id=req.project_id, job_type="llm_plan",
            provider="openrouter", status="completed",
            cost_usd=plan_cost, cost_detail=plan_detail,
            completed_at=datetime.utcnow(),
        ))

        if req.replace_existing:
            existing = db.exec(select(Scene).where(Scene.project_id == req.project_id)).all()
            for s in existing:
                db.delete(s)
            db.commit()

        # Default resolution = first one supported by the default video model.
        default_res = (VIDEO_MODELS.get(settings.default_video_model, {})
                       .get("resolutions") or ["720p"])[0]

        # Snap scene boundaries to whole seconds so the duration slider's
        # 3-15s integer values reflect exactly what's stored.
        from app.services.audio_analysis import words_in_range
        created = []
        skipped: list[dict] = []  # malformed scenes that couldn't be inserted
        for i, s in enumerate(scene_dicts):
            try:
                if not isinstance(s, dict):
                    skipped.append({"index": i, "reason": f"non-dict ({type(s).__name__})"})
                    continue
                raw_start = float(s.get("audio_start", 0))
                raw_end = float(s.get("audio_end", 0))
                start = round(raw_start)
                end = max(start + 3, round(raw_end))  # enforce minimum 3s

                # Compute lyrics_segment deterministically from word
                # timestamps rather than trusting the LLM to extract verbatim.
                if words:
                    sliced = words_in_range(words, float(start), float(end))
                else:
                    sliced = s.get("lyrics_segment") or ""

                scene = Scene(
                    project_id=req.project_id,
                    order=s.get("order", i + 1),
                    audio_start=float(start),
                    audio_end=float(end),
                    description=s.get("description"),
                    video_prompt=s.get("video_prompt"),
                    image_prompt=s.get("image_prompt"),
                    lyrics_segment=sliced,
                    lipsync_enabled=bool(s.get("lipsync_suggested", False)),
                    video_model=settings.default_video_model,
                    image_model=settings.default_image_model,
                    resolution=default_res,
                    generate_audio=False,
                )
                db.add(scene)
                created.append((scene, s.get("video_prompt") or "", s.get("image_prompt") or ""))
            except Exception as scene_err:
                # One bad scene shouldn't drop the whole plan.
                skipped.append({
                    "index": i,
                    "reason": f"{type(scene_err).__name__}: {str(scene_err)[:200]}",
                })

        if not created:
            raise HTTPException(
                502,
                "Auto-plan returned scenes but none could be inserted into the database. "
                f"Skipped: {skipped[:3]}{'...' if len(skipped) > 3 else ''}",
            )

        db.commit()

        # Save initial prompt versions as source="plan" so the history UI
        # shows where these came from and can compare against later expansions.
        for scene, vp, ip in created:
            db.refresh(scene)
            try:
                if vp.strip():
                    _save_prompt_version(db, scene, "video", vp, source="plan")
                if ip.strip():
                    _save_prompt_version(db, scene, "image", ip, source="plan")
            except Exception as pv_err:
                # Prompt-version save failure isn't worth losing the scene over.
                print(f"[auto-plan] prompt-version save failed for scene {scene.id}: {pv_err}")

        if skipped:
            print(f"[auto-plan] skipped {len(skipped)} malformed scene(s): {skipped}")

        return [_scene_with_urls(scene, db) for scene, _, _ in created]

    except HTTPException:
        raise  # let FastAPI handle the ones we deliberately raised
    except Exception as e:
        # Anything else: log the full traceback so the cause is visible in
        # backend logs, surface a short useful message to the client.
        print("[auto-plan] post-LLM exception:")
        traceback.print_exc()
        raise HTTPException(
            500,
            f"Auto-plan crashed after LLM call ({type(e).__name__}: {str(e)[:300]}). "
            f"The LLM response was received successfully but database writes failed. "
            f"Check the backend log for the full traceback.",
        )


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


@router.post("/expand-all")
async def expand_all_scenes(req: ExpandAllRequest, db: Session = Depends(get_session)):
    """Run AI Expand on every scene in the project (sequential, with neighbor
    context). Returns the count of scenes expanded + total cost.

    Wrapped in the same defensive try/except pattern as auto_plan_scenes —
    no exception escapes as opaque "Internal Server Error". Per-scene errors
    are already isolated by an inner try/except in the loop so one bad scene
    doesn't drop the rest.
    """
    try:
        project = db.get(Project, req.project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        characters = db.exec(
            select(Character).where(Character.project_id == req.project_id)
        ).all()
        char_dicts = [{"name": c.name, "description": c.description} for c in characters]
        scenes = db.exec(
            select(Scene).where(Scene.project_id == req.project_id).order_by(Scene.order)
        ).all()
        if req.only_empty:
            targets = [s for s in scenes if not (s.video_prompt or "").strip()]
        else:
            targets = scenes
        if not targets:
            return {"expanded": 0, "skipped": len(scenes), "total_cost_usd": 0.0}

        from app.services.scene_planner import generate_scene_prompts
        llm = req.llm_model or "google/gemini-3-flash-preview"
        expanded = 0
        failed_scenes: list[dict] = []
        total_cost = 0.0
        for s in targets:
            idx = scenes.index(s)
            prev = scenes[idx - 1] if idx > 0 else None
            nxt = scenes[idx + 1] if idx + 1 < len(scenes) else None
            try:
                prompts = await generate_scene_prompts(
                    description=s.description or "",
                    style=project.style or "",
                    characters=char_dicts,
                    lyrics=s.lyrics_segment or "",
                    previous_scene=prev.description if prev else None,
                    next_scene=nxt.description if nxt else None,
                    previous_image_prompt=prev.image_prompt if prev else None,
                    next_image_prompt=nxt.image_prompt if nxt else None,
                    duration_seconds=s.audio_end - s.audio_start,
                    llm_model=llm,
                )
                new_vp = (prompts.get("video_prompt") or "").strip()
                new_ip = (prompts.get("image_prompt") or "").strip()
                if not new_vp and not new_ip:
                    # Treat an all-empty response as a skip — don't burn an llm_expand cost row
                    print(f"[expand-all] skipped scene {s.id}: empty response")
                    failed_scenes.append({"scene_id": s.id, "reason": "empty response"})
                    continue
                if new_vp:
                    _save_prompt_version(db, s, "video", new_vp, source="expand")
                if new_ip:
                    _save_prompt_version(db, s, "image", new_ip, source="expand")
                s.prompts_expanded = True
                db.add(s)
                cost, detail = pricing.llm_expand_cost()
                total_cost += cost
                db.add(GenerationJob(
                    project_id=req.project_id, scene_id=s.id, job_type="llm_expand",
                    provider="openrouter", status="completed",
                    cost_usd=cost, cost_detail=detail,
                    completed_at=datetime.utcnow(),
                ))
                expanded += 1
            except Exception as e:
                print(f"[expand-all] failed on scene {s.id}: {type(e).__name__}: {e}")
                failed_scenes.append({"scene_id": s.id, "reason": f"{type(e).__name__}: {str(e)[:200]}"})
                continue
        db.commit()
        return {
            "expanded": expanded,
            "skipped": len(scenes) - expanded,
            "failed": failed_scenes,  # surface per-scene failures to the UI
            "total_cost_usd": round(total_cost, 4),
        }
    except HTTPException:
        raise
    except Exception as e:
        print("[expand-all] uncaught exception in handler:")
        traceback.print_exc()
        raise HTTPException(
            500,
            f"AI Expand all crashed ({type(e).__name__}: {str(e)[:300]}). "
            f"See backend log for the full traceback.",
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
    """Wipe all generated assets for a scene (images, videos, lipsync clips +
    files on disk) but keep the scene row + plan/prompts. Resets status to
    'pending' so the user can re-generate fresh."""
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
    scene.lipsync_path = None
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
    # video_url reflects the active video asset (which may be a lipsynced
    # variant). Lipsync results are saved as video assets now, so the old
    # `lipsync_path or video_path` is gone.
    d["video_url"] = _to_storage_url(scene.video_path)
    # Used by the chain UI to display the actual handoff frame.
    d["extracted_last_frame_url"] = _to_storage_url(scene.extracted_last_frame_path)
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
