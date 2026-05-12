import os
import shutil
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from sqlmodel import Session
from pydantic import BaseModel

from app.database import get_session, engine
from app.models import Song, Project, GenerationJob
from app.config import settings
from app.services import pricing

router = APIRouter()


class GenerateMusicRequest(BaseModel):
    project_id: int
    title: str = ""
    artist: str = ""
    description: str
    style_tags: str = ""
    lyrics: str = ""
    instrumental: bool = False
    source: str = "lyria"  # "lyria" or "suno"


@router.post("/upload", status_code=201)
async def upload_song(
    project_id: int,
    title: str,
    artist: str = "",
    background_tasks: BackgroundTasks = BackgroundTasks(),
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    dest_dir = os.path.join(settings.storage_dir, str(project_id), "audio")
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, file.filename or "song.mp3")

    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    song = Song(
        project_id=project_id,
        title=title,
        artist=artist,
        source="upload",
        file_path=dest_path,
        status="pending",
    )
    db.add(song)
    db.commit()
    db.refresh(song)

    background_tasks.add_task(_analyze_song_bg, song.id)
    return song


@router.post("/generate", status_code=201)
async def generate_song(
    req: GenerateMusicRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    project = db.get(Project, req.project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    song = Song(
        project_id=req.project_id,
        title=req.title or req.description[:60],
        artist=req.artist,
        source=req.source,
        lyrics=req.lyrics or None,
        status="pending",
    )
    db.add(song)
    db.commit()
    db.refresh(song)

    background_tasks.add_task(_generate_and_analyze_bg, song.id, req)
    return song


@router.get("/{song_id}")
def get_song(song_id: int, db: Session = Depends(get_session)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(404, "Song not found")
    return song


@router.delete("/{song_id}", status_code=204)
def delete_song(song_id: int, db: Session = Depends(get_session)):
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(404, "Song not found")
    db.delete(song)
    db.commit()


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

async def _analyze_song_bg(song_id: int):
    from app.services.audio_analysis import analyze_song
    import json
    with Session(engine) as db:
        song = db.get(Song, song_id)
        if not song or not song.file_path:
            return
        song.status = "analyzing"
        # Clear any prior error text from previous failed run
        if song.lyrics and "[Analysis error:" in song.lyrics:
            song.lyrics = None
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

            # Record transcription cost
            cost, detail = pricing.transcription_cost(result["duration"])
            db.add(GenerationJob(
                project_id=song.project_id, job_type="transcription",
                provider="openrouter", status="completed",
                cost_usd=cost, cost_detail=detail,
                completed_at=datetime.utcnow(),
            ))

            # Theme analysis — best-effort, don't fail the song if this errors
            if song.lyrics:
                try:
                    from app.services.scene_planner import analyze_song_theme
                    theme = await analyze_song_theme(song.title, song.artist or "", song.lyrics)
                    song.theme_analysis = json.dumps(theme, ensure_ascii=False)
                    tcost, tdetail = pricing.theme_analysis_cost()
                    db.add(GenerationJob(
                        project_id=song.project_id, job_type="llm_theme",
                        provider="openrouter", status="completed",
                        cost_usd=tcost, cost_detail=tdetail,
                        completed_at=datetime.utcnow(),
                    ))
                except Exception as e:
                    print(f"Theme analysis failed (non-fatal): {e}")
        except Exception as e:
            song.status = "error"
            song.lyrics = (song.lyrics or "") + f"\n\n[Analysis error: {e}]"
        db.add(song); db.commit()


async def _generate_and_analyze_bg(song_id: int, req: GenerateMusicRequest):
    import json
    from app.services import openrouter, suno
    with Session(engine) as db:
        song = db.get(Song, song_id)
        if not song:
            return
        song.status = "analyzing"
        db.add(song)
        db.commit()

        try:
            dest_dir = os.path.join(settings.storage_dir, str(req.project_id), "audio")
            os.makedirs(dest_dir, exist_ok=True)

            # Suno is the only working music gen path. Lyria is not actually
            # available on OpenRouter (despite earlier research suggesting it).
            result = await suno.generate_song(
                description=req.description,
                title=req.title,
                style_tags=req.style_tags,
                lyrics=req.lyrics,
                instrumental=req.instrumental,
            )
            dest_path = os.path.join(dest_dir, f"song_{song_id}.mp3")
            await openrouter.download_file(result["audio_url"], dest_path)
            if result.get("lyrics") and not song.lyrics:
                song.lyrics = result["lyrics"]

            cost, detail = pricing.music_cost("suno")
            db.add(GenerationJob(
                project_id=song.project_id, job_type="music",
                provider="suno", status="completed",
                cost_usd=cost, cost_detail=detail,
                completed_at=datetime.utcnow(),
            ))

            song.file_path = dest_path
            db.add(song); db.commit()

            # Now analyze
            from app.services.audio_analysis import analyze_song
            analysis = await analyze_song(dest_path, song.lyrics)
            song.duration = analysis["duration"]
            song.bpm = analysis["bpm"]
            song.key = analysis["key"]
            song.beats_json = json.dumps(analysis["beats"])
            song.sections_json = json.dumps(analysis["sections"])
            song.transcription_json = json.dumps(analysis["transcription"])
            if analysis["lyrics"]:
                song.lyrics = analysis["lyrics"]
            song.status = "ready"

        except Exception as e:
            song.status = "error"
            print(f"[song] analysis failed: {e}")
        db.add(song)
        db.commit()
