import os
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel

from app.database import get_session, engine
from app.models import Song, Project, GenerationJob
from app.config import settings
from app.services import pricing
from app.services.media_files import (
    AUDIO_EXTENSIONS,
    MAX_AUDIO_UPLOAD_BYTES,
    MediaValidationError,
    remove_storage_file,
    save_upload_limited,
    upload_destination,
    validate_media_async,
    validated_extension,
)
from app.services.urls import to_storage_url

router = APIRouter()


class GenerateMusicRequest(BaseModel):
    project_id: int
    title: str = ""
    artist: str = ""
    description: str
    style_tags: str = ""
    lyrics: str = ""
    instrumental: bool = False
    source: str = "suno"


def _song_payload(song: Song) -> dict:
    payload = song.model_dump()
    payload["file_url"] = (
        to_storage_url(song.file_path)
        if song.file_path and os.path.isfile(song.file_path)
        else None
    )
    return payload


def _ensure_song_slot_available(db: Session, project_id: int) -> None:
    existing = db.exec(
        select(Song).where(Song.project_id == project_id)
    ).first()
    if existing:
        raise HTTPException(
            409,
            f"This project already has a song ({existing.title}). Remove it "
            "before uploading or generating a replacement.",
        )


def _find_recoverable_music_job(
    db: Session,
    song: Song,
) -> GenerationJob | None:
    jobs = db.exec(
        select(GenerationJob)
        .where(
            GenerationJob.project_id == song.project_id,
            GenerationJob.job_type == "music",
            GenerationJob.provider == "suno",
        )
        .order_by(GenerationJob.id.desc())
    ).all()
    for job in jobs:
        try:
            snapshot = json.loads(job.request_json or "{}")
        except json.JSONDecodeError:
            continue
        if snapshot.get("song_id") != song.id:
            continue
        if job.status == "running" and job.external_id:
            return job
        if job.status == "completed" and job.result_url:
            return job
    return None


@router.post("/upload", status_code=201)
async def upload_song(
    project_id: int,
    title: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    artist: str = "",
    db: Session = Depends(get_session),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    _ensure_song_slot_available(db, project_id)

    try:
        extension = validated_extension(
            file.filename, AUDIO_EXTENSIONS, fallback="song.mp3",
        )
        dest_path = upload_destination(project_id, "audio", "song", extension)
        await save_upload_limited(
            file, dest_path, max_bytes=MAX_AUDIO_UPLOAD_BYTES,
        )
        await validate_media_async(dest_path, "audio")
    except MediaValidationError as exc:
        remove_storage_file(locals().get("dest_path"))
        raise HTTPException(400, str(exc)) from exc

    song = Song(
        project_id=project_id,
        title=title,
        artist=artist,
        source="upload",
        file_path=dest_path,
        status="pending",
    )
    db.add(song)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        remove_storage_file(dest_path)
        raise HTTPException(
            409,
            "Another song was added to this project at the same time. "
            "The duplicate upload was discarded.",
        ) from exc
    db.refresh(song)

    background_tasks.add_task(_analyze_song_bg, song.id)
    return _song_payload(song)


@router.post("/generate", status_code=201)
async def generate_song(
    req: GenerateMusicRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    project = db.get(Project, req.project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    _ensure_song_slot_available(db, req.project_id)
    if req.source != "suno":
        raise HTTPException(
            400,
            "Only the Suno music-generation route is available. Choose Suno "
            "or upload an existing audio file.",
        )
    if not settings.suno_api_key:
        raise HTTPException(400, "SUNO_API_KEY is missing")

    song = Song(
        project_id=req.project_id,
        title=req.title or req.description[:60],
        artist=req.artist,
        source=req.source,
        lyrics=req.lyrics or None,
        status="generating",
    )
    db.add(song)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409,
            "Another song generation was started for this project at the "
            "same time; no duplicate task was queued.",
        ) from exc
    db.refresh(song)

    background_tasks.add_task(_generate_and_analyze_bg, song.id, req)
    return _song_payload(song)


@router.get("/{song_id}")
def get_song(song_id: int, db: Session = Depends(get_session)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(404, "Song not found")
    return _song_payload(song)


@router.post("/{song_id}/analyze", status_code=202)
async def retry_song_analysis(
    song_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(404, "Song not found")
    if song.status in ("pending", "generating", "analyzing"):
        raise HTTPException(409, "Song analysis is already running")
    has_file = bool(song.file_path and os.path.isfile(song.file_path))
    recoverable_music_job = _find_recoverable_music_job(db, song)
    if not has_file and not recoverable_music_job:
        raise HTTPException(
            400,
            "The song file is missing and there is no resumable provider task; "
            "remove the song and generate or upload it again.",
        )
    song.status = "pending" if has_file else "generating"
    song.error_message = None
    db.add(song); db.commit()
    if has_file:
        background_tasks.add_task(_analyze_song_bg, song.id)
        message = "Song analysis queued"
    else:
        background_tasks.add_task(_generate_and_analyze_bg, song.id, None)
        message = "Song generation resume queued"
    return {
        "message": message,
        "song_id": song.id,
        "song": _song_payload(song),
    }


@router.delete("/{song_id}", status_code=204)
def delete_song(song_id: int, db: Session = Depends(get_session)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(404, "Song not found")
    if song.status in ("pending", "generating", "analyzing"):
        raise HTTPException(
            409,
            "Song generation or analysis is still running. Wait for it to "
            "finish before deleting it.",
        )
    recoverable_music_job = _find_recoverable_music_job(db, song)
    if recoverable_music_job and recoverable_music_job.status == "running":
        raise HTTPException(
            409,
            f"Suno task {recoverable_music_job.external_id} is still active. "
            "Retry the song to reconcile it before deleting the song.",
        )
    file_path = song.file_path
    db.delete(song)
    db.commit()
    remove_storage_file(file_path)


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

async def _analyze_song_bg(song_id: int):
    from app.services.audio_analysis import analyze_song
    with Session(engine) as db:
        song = db.get(Song, song_id)
        if not song or not song.file_path:
            return
        song.status = "analyzing"
        song.error_message = None
        # Clear any prior error text from previous failed run
        if song.lyrics and "[Analysis error:" in song.lyrics:
            song.lyrics = song.lyrics.split("\n\n[Analysis error:", 1)[0] or None
        db.add(song); db.commit()
        try:
            result = await analyze_song(song.file_path, song.lyrics)
            song.duration = result["duration"]
            song.bpm = result["bpm"]
            song.key = result["key"]
            song.beats_json = json.dumps(result["beats"])
            song.sections_json = json.dumps(result["sections"])
            song.transcription_json = json.dumps(result["transcription"])
            if result["lyrics"]:
                song.lyrics = result["lyrics"]
            song.status = "ready"
            song.error_message = None

            # Record provider cost only when an API actually transcribed it.
            transcription_provider = result.get("transcription_provider")
            if transcription_provider in ("fal", "openrouter"):
                cost, detail = pricing.transcription_cost(result["duration"])
                db.add(GenerationJob(
                    project_id=song.project_id, job_type="transcription",
                    provider=transcription_provider, status="completed",
                    cost_usd=cost, cost_detail=detail,
                    completed_at=datetime.utcnow(),
                ))

            # Theme analysis — best-effort, don't fail the song if this errors
            if song.lyrics:
                try:
                    from app.services.scene_planner import analyze_song_theme
                    from app.services import openrouter
                    theme = await analyze_song_theme(song.title, song.artist or "", song.lyrics)
                    song.theme_analysis = json.dumps(theme, ensure_ascii=False)
                    tcost, tdetail = pricing.theme_analysis_cost()
                    tcost, tdetail, external_id, request_json = openrouter.consume_chat_billing(
                        tcost,
                        tdetail,
                    )
                    db.add(GenerationJob(
                        project_id=song.project_id, job_type="llm_theme",
                        provider="openrouter", status="completed",
                        cost_usd=tcost, cost_detail=tdetail,
                        external_id=external_id, request_json=request_json,
                        completed_at=datetime.utcnow(),
                    ))
                except Exception as e:
                    from app.services import openrouter
                    tcost, tdetail = pricing.theme_analysis_cost()
                    tcost, tdetail, external_id, request_json = openrouter.consume_chat_billing(
                        tcost,
                        tdetail,
                    )
                    if external_id:
                        db.add(GenerationJob(
                            project_id=song.project_id,
                            job_type="llm_theme",
                            provider="openrouter",
                            status="failed",
                            cost_usd=tcost,
                            cost_detail=f"{tdetail} · response could not be used",
                            external_id=external_id,
                            request_json=request_json,
                            error=str(e)[:500],
                            completed_at=datetime.utcnow(),
                        ))
                    print(f"Theme analysis failed (non-fatal): {e}")
        except Exception as e:
            song.status = "error"
            song.error_message = str(e)[:500]
        db.add(song); db.commit()


async def _generate_and_analyze_bg(
    song_id: int,
    req: GenerateMusicRequest | None,
):
    from app.services import openrouter, suno
    with Session(engine) as db:
        song = db.get(Song, song_id)
        if not song:
            return
        song.status = "generating"
        song.error_message = None
        db.add(song)
        db.commit()

        job = _find_recoverable_music_job(db, song)
        cost, detail = pricing.music_cost("suno")
        destination: str | None = None
        try:
            if job and job.status == "completed" and job.result_url:
                result = {"audio_url": job.result_url}
            else:
                if not job:
                    if req is None:
                        raise RuntimeError(
                            "No saved Suno task is available to resume."
                        )
                    job = GenerationJob(
                        project_id=song.project_id,
                        job_type="music",
                        provider="suno",
                        status="pending",
                        cost_usd=cost,
                        cost_detail=detail,
                        request_json=json.dumps({
                            "song_id": song.id,
                            "request": req.model_dump(),
                        }, ensure_ascii=False),
                    )
                    db.add(job)
                    db.commit()
                    db.refresh(job)
                    task_id = await suno.submit_song(
                        description=req.description,
                        title=req.title,
                        style_tags=req.style_tags,
                        lyrics=req.lyrics,
                        instrumental=req.instrumental,
                    )
                    job.external_id = task_id
                    job.status = "running"
                    db.add(job)
                    db.commit()
                result = await suno.poll_song(job.external_id)

            dest_path = upload_destination(
                song.project_id, "audio", f"song_{song_id}_generated", "mp3",
            )
            destination = dest_path
            await openrouter.download_file(result["audio_url"], dest_path)
            try:
                await validate_media_async(dest_path, "audio")
            except MediaValidationError:
                remove_storage_file(dest_path)
                raise
            if result.get("lyrics") and not song.lyrics:
                song.lyrics = result["lyrics"]
            song.file_path = dest_path
            job.status = "completed"
            job.result_url = result["audio_url"]
            job.result_path = dest_path
            job.error = None
            job.completed_at = datetime.utcnow()
            db.add(song)
            db.add(job)
            db.commit()

            # Reuse the same analysis pipeline as uploads so generated songs
            # also get transcription cost attribution and theme analysis.
            await _analyze_song_bg(song_id)
            return

        except suno.RemoteJobPendingError as e:
            if job:
                job.status = "running"
                job.error = str(e)[:500]
                job.completed_at = None
                db.add(job)
            song.status = "error"
            song.error_message = (
                f"{str(e)[:400]} Use Retry to resume without paying for "
                "another song generation."
            )
            print(f"[song] provider task preserved: {e}")
        except suno.SunoError as e:
            if job:
                job.status = "failed"
                job.error = str(e)[:500]
                job.completed_at = datetime.utcnow()
                db.add(job)
            song.status = "error"
            song.error_message = str(e)[:500]
            print(f"[song] provider generation failed: {e}")
        except Exception as e:
            if destination and song.file_path != destination:
                remove_storage_file(destination)
            if job:
                if job.external_id:
                    job.status = "running"
                    job.error = (
                        f"Local result processing failed: {str(e)[:400]}. "
                        "Retry to fetch the same provider task."
                    )
                    job.completed_at = None
                else:
                    job.status = "failed"
                    job.error = str(e)[:500]
                    job.completed_at = datetime.utcnow()
                db.add(job)
            song.status = "error"
            song.error_message = (
                f"{str(e)[:400]}"
                + (
                    " Use Retry to fetch the existing Suno task."
                    if job and job.external_id
                    else ""
                )
            )
            print(f"[song] generation failed: {e}")
        db.add(song)
        db.commit()
