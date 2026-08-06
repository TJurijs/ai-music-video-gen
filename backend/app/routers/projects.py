import os
import shutil
import json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlmodel import Session, select
from sqlalchemy import update
from datetime import datetime
from typing import Optional
from pydantic import BaseModel

from app.database import get_session, engine
from app.models import Project, Song, Scene, Character, CharacterAsset, GenerationJob
from app.config import settings, IMAGE_MODELS
from app.services import openrouter, pricing
from app.services.urls import to_storage_url
from app.services.media_files import (
    IMAGE_EXTENSIONS,
    MAX_IMAGE_UPLOAD_BYTES,
    MediaValidationError,
    remove_storage_file,
    remove_storage_files,
    save_upload_limited,
    storage_path_is_safe,
    upload_destination,
    validate_media_async,
    validated_extension,
)

router = APIRouter()


def _ensure_character_idle(character: Character) -> None:
    if character.portrait_status == "generating":
        raise HTTPException(
            409,
            "Portrait generation is already running for this character. "
            "Wait for it to finish before changing or deleting portraits.",
        )


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    style: Optional[str] = None
    aspect_ratio: str = "16:9"
    story_seed: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    style: Optional[str] = None
    aspect_ratio: Optional[str] = None
    story_seed: Optional[str] = None


@router.get("")
def list_projects(db: Session = Depends(get_session)):
    projects = db.exec(select(Project).order_by(Project.created_at.desc())).all()
    result = []
    for p in projects:
        songs = db.exec(select(Song).where(Song.project_id == p.id)).all()
        scenes = db.exec(select(Scene).where(Scene.project_id == p.id)).all()
        result.append({
            **p.model_dump(),
            "song_count": len(songs),
            "scene_count": len(scenes),
            "scenes_done": sum(1 for s in scenes if s.status == "done"),
        })
    return result


@router.post("", status_code=201)
def create_project(data: ProjectCreate, db: Session = Depends(get_session)):
    project = Project(**data.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


class ExpandStyleRequest(BaseModel):
    style: str
    llm_model: Optional[str] = None


@router.post("/expand-style")
async def expand_style(req: ExpandStyleRequest):
    """Expand a short rough style/mood string into a detailed style guide.
    Used by the New Project modal and the project header edit button.
    No project_id required — works pre-creation too."""
    if not (req.style or "").strip():
        raise HTTPException(400, "style is required")
    from app.services.scene_planner import expand_style_description
    result = await expand_style_description(
        raw_style=req.style,
        llm_model=req.llm_model or "google/gemini-3-flash-preview",
    )
    return {"expanded": (result.get("expanded") or req.style).strip()}


@router.get("/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_session)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    songs = db.exec(select(Song).where(Song.project_id == project_id)).all()
    scenes = db.exec(
        select(Scene).where(Scene.project_id == project_id).order_by(Scene.order)
    ).all()
    characters = db.exec(select(Character).where(Character.project_id == project_id)).all()

    from app.routers.scenes import _scene_with_urls
    return {
        **project.model_dump(),
        "songs": [_song_with_url(s) for s in songs],
        "scenes": [_scene_with_urls(s, db) for s in scenes],
        "characters": [_char_with_url(c, db) for c in characters],
    }


@router.patch("/{project_id}")
def update_project(project_id: int, data: ProjectUpdate, db: Session = Depends(get_session)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(project, k, v)
    project.updated_at = datetime.utcnow()
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_session)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    active_job = db.exec(
        select(GenerationJob)
        .where(
            GenerationJob.project_id == project_id,
            GenerationJob.status.in_(["pending", "running"]),
        )
        .order_by(GenerationJob.id.desc())
    ).first()
    if active_job:
        raise HTTPException(
            409,
            f"Project has an active {active_job.job_type} job "
            f"({active_job.external_id or active_job.id}). Stop or reconcile "
            "it before deleting the project.",
        )
    active_song = db.exec(
        select(Song).where(
            Song.project_id == project_id,
            Song.status.in_(["pending", "generating", "analyzing"]),
        )
    ).first()
    active_character = db.exec(
        select(Character).where(
            Character.project_id == project_id,
            Character.portrait_status == "generating",
        )
    ).first()
    if active_song or active_character:
        raise HTTPException(
            409,
            "Project still has background audio or portrait work running. "
            "Wait for it to finish before deleting the project.",
        )

    db.delete(project)
    db.commit()

    # Best-effort cleanup of generated assets on disk
    asset_dir = os.path.join(settings.storage_dir, str(project_id))
    if os.path.isdir(asset_dir) and storage_path_is_safe(asset_dir):
        try:
            shutil.rmtree(asset_dir)
        except OSError:
            pass  # don't fail the API call on filesystem issues


# Characters
class CharacterCreate(BaseModel):
    name: str
    description: str
    trigger_word: Optional[str] = None


class CharacterUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    trigger_word: Optional[str] = None


@router.post("/{project_id}/characters", status_code=201)
def create_character(project_id: int, data: CharacterCreate, db: Session = Depends(get_session)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    char = Character(project_id=project_id, **data.model_dump())
    db.add(char)
    db.commit()
    db.refresh(char)
    return _char_with_url(char, db)


@router.patch("/{project_id}/characters/{char_id}")
def update_character(project_id: int, char_id: int, data: CharacterUpdate, db: Session = Depends(get_session)):
    char = db.get(Character, char_id)
    if not char or char.project_id != project_id:
        raise HTTPException(404, "Character not found")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(char, k, v)
    db.add(char); db.commit(); db.refresh(char)
    return _char_with_url(char, db)


@router.delete("/{project_id}/characters/{char_id}", status_code=204)
def delete_character(project_id: int, char_id: int, db: Session = Depends(get_session)):
    char = db.get(Character, char_id)
    if not char or char.project_id != project_id:
        raise HTTPException(404, "Character not found")
    _ensure_character_idle(char)
    _ensure_character_idle(char)
    portrait_paths = [asset.file_path for asset in char.portraits]
    if char.reference_image_path:
        portrait_paths.append(char.reference_image_path)
    db.delete(char)
    db.commit()
    remove_storage_files(portrait_paths)


# ---------------------------------------------------------------------------
# Character image: upload your own
# ---------------------------------------------------------------------------
@router.post("/{project_id}/characters/{char_id}/image", status_code=201)
async def upload_character_image(
    project_id: int, char_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    char = db.get(Character, char_id)
    if not char or char.project_id != project_id:
        raise HTTPException(404, "Character not found")
    _ensure_character_idle(char)

    try:
        ext = validated_extension(
            file.filename, IMAGE_EXTENSIONS, fallback="portrait.jpg",
        )
        dest_path = upload_destination(
            project_id, "characters", f"char_{char_id}_upload", ext,
        )
        await save_upload_limited(
            file, dest_path, max_bytes=MAX_IMAGE_UPLOAD_BYTES,
        )
        await validate_media_async(dest_path, "image")
    except MediaValidationError as exc:
        remove_storage_file(locals().get("dest_path"))
        raise HTTPException(400, str(exc)) from exc

    # Deactivate prior portraits, save this upload as a new active asset.
    # Snapshot the current description so the variant + description stay
    # bundled — activating this asset later will also restore that snapshot
    # onto the parent character.
    from app.services.versioning import make_active
    asset = CharacterAsset(
        character_id=char_id,
        file_path=dest_path,
        model_used="uploaded",
        cost_usd=0.0,
        description=char.description,
    )
    make_active(
        db,
        target=asset,
        siblings_filter=[CharacterAsset.character_id == char_id],
        on_active_change=lambda a: (
            setattr(char, "reference_image_path", a.file_path),
            db.add(char),
        ),
    )
    db.commit()
    db.refresh(char)
    return _char_with_url(char, db)


# ---------------------------------------------------------------------------
# Character image: AI-generate from description
# ---------------------------------------------------------------------------
class GeneratePortraitRequest(BaseModel):
    image_model: str = "gemini-flash-image"


@router.post("/{project_id}/characters/{char_id}/portrait", status_code=201)
async def generate_character_portrait(
    project_id: int, char_id: int,
    req: GeneratePortraitRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    char = db.get(Character, char_id)
    if not char or char.project_id != project_id:
        raise HTTPException(404, "Character not found")
    if req.image_model not in IMAGE_MODELS:
        raise HTTPException(400, f"Unknown image model: {req.image_model}")
    if not settings.openrouter_api_key:
        raise HTTPException(400, "OPENROUTER_API_KEY is missing")
    claimed = db.exec(
        update(Character)
        .where(
            Character.id == char_id,
            Character.project_id == project_id,
            Character.portrait_status != "generating",
        )
        .values(
            portrait_status="generating",
            portrait_error=None,
            portrait_model=req.image_model,
        )
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Portrait generation is already running")
    db.commit()
    background_tasks.add_task(_generate_portrait_bg, char_id, req.image_model)
    return {"message": "Portrait generation started", "character_id": char_id}


# ---------------------------------------------------------------------------
# Portrait history — list / activate / delete versions
# ---------------------------------------------------------------------------
@router.get("/{project_id}/characters/{char_id}/portraits")
def list_character_portraits(project_id: int, char_id: int, db: Session = Depends(get_session)):
    char = db.get(Character, char_id)
    if not char or char.project_id != project_id:
        raise HTTPException(404, "Character not found")
    rows = db.exec(
        select(CharacterAsset)
        .where(CharacterAsset.character_id == char_id)
        .order_by(CharacterAsset.created_at.desc())
    ).all()
    return [_portrait_to_dict(p) for p in rows]


@router.post("/{project_id}/characters/{char_id}/portraits/{asset_id}/activate")
def activate_character_portrait(project_id: int, char_id: int, asset_id: int, db: Session = Depends(get_session)):
    char = db.get(Character, char_id)
    if not char or char.project_id != project_id:
        raise HTTPException(404, "Character not found")
    _ensure_character_idle(char)
    asset = db.get(CharacterAsset, asset_id)
    if not asset or asset.character_id != char_id:
        raise HTTPException(404, "Portrait not found")

    # On activate: restore both the file pointer AND the description snapshot
    # that was current when this variant was generated. Without the description
    # swap, the character's description (which drives every scene's
    # image_prompt via AI Expand) stays out-of-sync with the visible portrait.
    from app.services.versioning import make_active
    make_active(
        db,
        target=asset,
        siblings_filter=[CharacterAsset.character_id == char_id],
        on_active_change=lambda a: (
            setattr(char, "reference_image_path", a.file_path),
            setattr(char, "description", a.description) if a.description else None,
            db.add(char),
        ),
    )
    db.commit()
    return _char_with_url(char, db)


class PortraitDescriptionUpdate(BaseModel):
    description: str


@router.patch("/{project_id}/characters/{char_id}/portraits/{asset_id}")
def update_portrait_description(
    project_id: int, char_id: int, asset_id: int,
    payload: PortraitDescriptionUpdate,
    db: Session = Depends(get_session),
):
    """Edit a portrait variant's bundled description. When the variant is
    currently active, also propagates the new description onto the parent
    character so AI Expand picks it up immediately."""
    char = db.get(Character, char_id)
    asset = db.get(CharacterAsset, asset_id)
    if not char or char.project_id != project_id or not asset or asset.character_id != char_id:
        raise HTTPException(404, "Portrait not found")
    _ensure_character_idle(char)
    asset.description = payload.description
    db.add(asset)
    if asset.is_active:
        char.description = payload.description
        db.add(char)
    db.commit()
    db.refresh(asset)
    return _portrait_to_dict(asset)


@router.delete("/{project_id}/characters/{char_id}/portraits/{asset_id}", status_code=204)
def delete_character_portrait(project_id: int, char_id: int, asset_id: int, db: Session = Depends(get_session)):
    from app.services.versioning import delete_and_promote
    asset = db.get(CharacterAsset, asset_id)
    char = db.get(Character, char_id)
    if not asset or asset.character_id != char_id or not char or char.project_id != project_id:
        raise HTTPException(404, "Portrait not found")
    _ensure_character_idle(char)

    file_path = asset.file_path
    delete_and_promote(
        db,
        deleted=asset,
        siblings_filter=[CharacterAsset.character_id == char_id],
        on_active_change=lambda nxt: (
            setattr(char, "reference_image_path", nxt.file_path if nxt else None),
            db.add(char),
        ),
    )
    db.commit()

    remove_storage_file(file_path)


# ---------------------------------------------------------------------------
# AI Expand for character description — deepen it and align with style + song
# ---------------------------------------------------------------------------
class ExpandCharacterRequest(BaseModel):
    llm_model: Optional[str] = None


@router.post("/{project_id}/characters/{char_id}/expand")
async def expand_character(
    project_id: int, char_id: int,
    req: ExpandCharacterRequest,
    db: Session = Depends(get_session),
):
    char = db.get(Character, char_id)
    if not char or char.project_id != project_id:
        raise HTTPException(404, "Character not found")
    project = db.get(Project, project_id)
    song = db.exec(select(Song).where(Song.project_id == project_id)).first()

    theme: dict = {}
    if song and song.theme_analysis:
        try:
            import json as _json
            theme = _json.loads(song.theme_analysis)
        except Exception:
            theme = {}

    from app.services.scene_planner import expand_character_description
    result = await expand_character_description(
        name=char.name,
        current_description=char.description,
        style=project.style if project else "",
        theme_analysis=theme,
        llm_model=req.llm_model or "google/gemini-3-flash-preview",
    )
    new_desc = (result.get("description") or "").strip()
    if not new_desc:
        raise HTTPException(500, "AI Expand returned empty description")
    char.description = new_desc
    if result.get("trigger_word"):
        char.trigger_word = result["trigger_word"][:40]

    cost, detail = pricing.llm_expand_cost()
    cost, detail, external_id, request_json = openrouter.consume_chat_billing(
        cost,
        detail,
    )
    db.add(GenerationJob(
        project_id=project_id, job_type="llm_expand",
        provider="openrouter", status="completed",
        external_id=external_id, request_json=request_json,
        cost_usd=cost, cost_detail=f"Character expand — {detail}",
        completed_at=datetime.utcnow(),
    ))
    db.add(char); db.commit(); db.refresh(char)
    return _char_with_url(char, db)


@router.post("/{project_id}/characters/{char_id}/regenerate")
async def regenerate_character(
    project_id: int, char_id: int,
    req: ExpandCharacterRequest,
    db: Session = Depends(get_session),
):
    """Reroll a character: throw away the current description and design a
    fresh take from the name + project style + song theme. Name and
    portrait history are preserved — only the description (and trigger
    word) get replaced. Useful when the existing description doesn't
    match your vision or got too off-style and you want a fresh attempt
    that keeps the character's identity (name) intact.
    """
    char = db.get(Character, char_id)
    if not char or char.project_id != project_id:
        raise HTTPException(404, "Character not found")
    project = db.get(Project, project_id)
    song = db.exec(select(Song).where(Song.project_id == project_id)).first()

    theme: dict = {}
    if song and song.theme_analysis:
        try:
            import json as _json
            theme = _json.loads(song.theme_analysis)
        except Exception:
            theme = {}

    # Collect other characters so the reroll can contrast against them.
    other_chars = [
        {"name": c.name, "description": c.description}
        for c in db.exec(select(Character).where(Character.project_id == project_id)).all()
        if c.id != char_id and c.description
    ]

    from app.services.scene_planner import expand_character_description
    result = await expand_character_description(
        name=char.name,
        current_description="",
        style=project.style if project else "",
        theme_analysis=theme,
        llm_model=req.llm_model or "google/gemini-3-flash-preview",
        other_characters=other_chars or None,
        previous_description=char.description or None,
    )
    new_desc = (result.get("description") or "").strip()
    if not new_desc:
        raise HTTPException(500, "Regenerate returned empty description")
    char.description = new_desc
    if result.get("trigger_word"):
        char.trigger_word = result["trigger_word"][:40]

    cost, detail = pricing.llm_expand_cost()
    cost, detail, external_id, request_json = openrouter.consume_chat_billing(
        cost,
        detail,
    )
    db.add(GenerationJob(
        project_id=project_id, job_type="llm_expand",
        provider="openrouter", status="completed",
        external_id=external_id, request_json=request_json,
        cost_usd=cost, cost_detail=f"Character regenerate — {detail}",
        completed_at=datetime.utcnow(),
    ))
    db.add(char); db.commit(); db.refresh(char)
    return _char_with_url(char, db)


async def _generate_portrait_bg(char_id: int, image_model: str):
    from app.services import openrouter
    with Session(engine) as db:
        char = db.get(Character, char_id)
        if not char:
            return
        project = db.get(Project, char.project_id)
        style = (project.style or "").strip() if project else ""
        dest_path: str | None = None
        temporary_path: str | None = None

        # Mark as generating
        char.portrait_status = "generating"
        char.portrait_error = None
        char.portrait_model = image_model
        db.add(char); db.commit()

        try:
            # Reference portraits feed downstream image + video gen via
            # input_references for identity anchoring. The portrait simply
            # reflects the project's visual style — no forced stylization.
            # If a particular video model refuses photoreal portraits
            # (Seedance has this filter), the user picks a permissive
            # alternative (Kling, MiniMax Hailuo, Wan, Veo with
            # personGeneration). See VIDEO_MODELS in config.py for the
            # permissiveness notes.
            base = (
                f"Generate a reference portrait image of {char.name}. "
                f"Subject: {char.description} "
                "Composition: head-and-shoulders framing, looking at camera, sharp focus, "
                "neutral simple background, even lighting so the character is fully readable. "
                "Output the image directly — do NOT respond with a text description."
            )
            if style:
                prompt = f"{base}\n\nVisual style guide (apply throughout): {style}"
            else:
                prompt = base + " Photorealistic, professional reference photo."
            image_result = await openrouter.generate_image(prompt, image_model)
            image_bytes = image_result.data
            dest_path = upload_destination(
                char.project_id, "characters", f"char_{char_id}_generated", "jpg",
            )
            temporary_path = f"{dest_path}.tmp"
            with open(temporary_path, "xb") as f:
                f.write(image_bytes)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary_path, dest_path)
            await validate_media_async(dest_path, "image")

            estimated_cost, detail = pricing.image_cost(image_model)
            cost = image_result.cost_usd if image_result.cost_usd is not None else estimated_cost
            if image_result.cost_usd is not None:
                detail += " · actual OpenRouter usage"

            # Deactivate prior actives, save new asset, point char.reference_image_path at it.
            # The description that drove this generation is snapshot onto the
            # asset so the variant + description stay bundled (activating an
            # old variant later restores its matching description).
            from app.services.versioning import make_active
            asset = CharacterAsset(
                character_id=char_id,
                file_path=dest_path,
                model_used=image_model,
                cost_usd=cost,
                description=char.description,
            )
            make_active(
                db,
                target=asset,
                siblings_filter=[CharacterAsset.character_id == char_id],
                on_active_change=lambda a: (
                    setattr(char, "reference_image_path", a.file_path),
                    db.add(char),
                ),
            )
            char.portrait_status = "done"
            db.add(char)

            db.add(GenerationJob(
                project_id=char.project_id, job_type="image",
                provider="openrouter", status="completed",
                cost_usd=cost, cost_detail=f"Character portrait — {detail}",
                external_id=image_result.generation_id,
                request_json=json.dumps({
                    "generation_id": image_result.generation_id,
                    "usage": image_result.usage,
                }, ensure_ascii=False),
                completed_at=datetime.utcnow(),
            ))
            db.commit()
        except Exception as e:
            remove_storage_file(temporary_path)
            remove_storage_file(dest_path)
            char.portrait_status = "error"
            char.portrait_error = str(e)[:300]
            db.add(char); db.commit()
            print(f"Character portrait gen failed: {e}")


# ---------------------------------------------------------------------------
# Suggest characters with AI — uses song's theme analysis + visual style
# ---------------------------------------------------------------------------
class SuggestCharactersRequest(BaseModel):
    visual_style: Optional[str] = None
    count: int = 3


@router.post("/{project_id}/characters/suggest", status_code=201)
async def suggest_characters(
    project_id: int,
    req: SuggestCharactersRequest,
    db: Session = Depends(get_session),
):
    import json as _json
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    # Pull theme from the project's first song (if any)
    song = db.exec(select(Song).where(Song.project_id == project_id)).first()
    theme_data: dict = {}
    if song and song.theme_analysis:
        try:
            theme_data = _json.loads(song.theme_analysis)
        except Exception:
            theme_data = {}

    style = req.visual_style or project.style or theme_data.get("suggested_visual_style") or ""

    from app.services.scene_planner import suggest_characters as _suggest
    suggestions = await _suggest(theme_data, style, req.count)

    # Persist each as a Character row (no portraits yet — user generates 1 by 1)
    created = []
    for s in suggestions[:req.count]:
        c = Character(
            project_id=project_id,
            name=(s.get("name") or "Character")[:80],
            description=s.get("description", "") or s.get("role_in_song", ""),
        )
        db.add(c); db.commit(); db.refresh(c)
        created.append(c)

    # Cost tracking
    cost, detail = pricing.character_suggest_cost(req.count)
    cost, detail, external_id, request_json = openrouter.consume_chat_billing(
        cost,
        detail,
    )
    db.add(GenerationJob(
        project_id=project_id, job_type="llm_chars",
        provider="openrouter", status="completed",
        external_id=external_id, request_json=request_json,
        cost_usd=cost, cost_detail=detail,
        completed_at=datetime.utcnow(),
    ))
    db.commit()

    return {
        "characters": [_char_with_url(c, db) for c in created],
        "visual_style_used": style,
    }


# ---------------------------------------------------------------------------
# Helper: serialize character with image URL + portrait history
# ---------------------------------------------------------------------------
def _portrait_to_dict(a: CharacterAsset) -> dict:
    return {
        "id": a.id,
        "character_id": a.character_id,
        "model_used": a.model_used,
        "cost_usd": a.cost_usd,
        "is_active": a.is_active,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "description": a.description,
        "url": to_storage_url(a.file_path),
    }


def _char_with_url(char: Character, db: Optional[Session] = None) -> dict:
    d = char.model_dump()
    d["reference_image_url"] = to_storage_url(char.reference_image_path)
    if db is not None:
        portraits = db.exec(
            select(CharacterAsset)
            .where(CharacterAsset.character_id == char.id)
            .order_by(CharacterAsset.created_at.desc())
        ).all()
        d["portraits"] = [_portrait_to_dict(p) for p in portraits]
    else:
        d["portraits"] = []
    return d


def _song_with_url(song: Song) -> dict:
    d = song.model_dump()
    d["file_url"] = to_storage_url(song.file_path) if song.file_path and os.path.exists(song.file_path) else None
    return d
