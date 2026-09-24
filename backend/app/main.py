from contextlib import asynccontextmanager
import os
import re
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from app.database import create_db_and_tables
from app.config import settings
from app.routers import projects, songs, scenes, generation


def _configure_stdio_utf8() -> None:
    """Keep Windows console/log encoding from crashing background jobs.

    Python may inherit a legacy cp1252-style encoding when uvicorn is started
    in a hidden redirected process. Prompts, paths, and diagnostic messages
    legitimately contain Unicode; logging them must never abort generation.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass


_configure_stdio_utf8()


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    os.makedirs(settings.storage_dir, exist_ok=True)
    _apply_schema_migrations()
    from app.database import ensure_integrity_indexes
    ensure_integrity_indexes()
    _backfill_portrait_descriptions()
    _reset_zombie_scenes()
    yield


def _apply_schema_migrations():
    """Reconcile the existing SQLite schema with the current SQLModel classes.

    Two directions:

    1) ADDITIONS: columns the current model defines but the DB is missing.
       SQLModel's `create_all()` only creates new TABLES, not new columns
       on existing ones. We `ALTER TABLE ADD COLUMN` for the gap.

    2) REMOVALS: columns that EXIST in the DB but were dropped from the
       model. SQLite tolerates orphan columns on SELECT, but enforces any
       NOT NULL constraint on INSERT — so an old NOT NULL column the
       application no longer supplies a value for will 500 every insert.
       For SQLite >= 3.35 (March 2021) we can `ALTER TABLE DROP COLUMN`
       directly; the bundled sqlite3 module ships >= 3.40 on Python 3.11+.

    Removals are conservative — only drop columns we KNOW were retired
    (i.e. listed here explicitly), never inferred. Adding the wrong name
    here would silently delete a user's data.
    """
    from sqlalchemy import inspect, text
    from app.database import engine as _engine
    expected = {
        # table -> { column: SQL definition fragment used for ALTER TABLE }
        "scene": {
            "chain_from_prev": "BOOLEAN NOT NULL DEFAULT 0",
            "extracted_last_frame_path": "VARCHAR",
            # audio_sync_enabled was retired then reintroduced (Seedance R2V
            # path). On fresh DBs SQLModel.create_all makes this column; on
            # DBs that went through the v1 cleanup it was dropped, so this
            # entry re-creates it. DEFAULT 0 keeps existing rows valid.
            "audio_sync_enabled": "BOOLEAN NOT NULL DEFAULT 0",
            "video_reference_mode": "VARCHAR NOT NULL DEFAULT 'frame'",
            "generation_run_id": "VARCHAR",
            "generation_phase": "VARCHAR",
            "generation_requested_at": "TIMESTAMP",
        },
        "project": {
            "story_seed": "VARCHAR",
        },
        "characterasset": {
            "description": "VARCHAR",
        },
        "song": {
            "error_message": "TEXT",
        },
        "generationjob": {
            "request_json": "TEXT",
        },
    }
    # Columns retired in v1. Old DBs may have them as NOT NULL, which
    # breaks INSERTs because the application no longer writes a value.
    # Drop them on startup. Listed explicitly per-table — no auto-inference.
    # NOTE: audio_sync_enabled was originally retired but came back when
    # Seedance audio-sync was reintroduced; it's no longer in this list.
    retired = {
        "scene": [
            "lipsync_model",
            "lipsync_path",
            "lipsync_enabled",
            "generate_audio",
        ],
    }
    insp = inspect(_engine)
    pending_schema_change = False
    for table, cols in expected.items():
        if insp.has_table(table):
            existing = {column["name"] for column in insp.get_columns(table)}
            pending_schema_change = pending_schema_change or any(
                column not in existing for column in cols
            )
    for table, cols in retired.items():
        if insp.has_table(table):
            existing = {column["name"] for column in insp.get_columns(table)}
            pending_schema_change = pending_schema_change or any(
                column in existing for column in cols
            )
    if pending_schema_change:
        from app.database import backup_database
        backup_database("compatibility schema migration")
    added: list[str] = []
    dropped: list[str] = []
    with _engine.begin() as conn:
        # ─── Additions ────────────────────────────────────────────────
        for table, cols in expected.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for col_name, col_def in cols.items():
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
                    added.append(f"{table}.{col_name}")

        # The generic auto-migrator runs before this compatibility migration
        # and adds new columns as nullable. Backfill rows created before the
        # reference-mode feature so the API and UI always receive a real mode.
        if insp.has_table("scene"):
            conn.execute(text(
                "UPDATE scene SET video_reference_mode = 'frame' "
                "WHERE video_reference_mode IS NULL "
                "OR video_reference_mode NOT IN ('frame', 'character')"
            ))
            conn.execute(text(
                "UPDATE scene SET audio_sync_enabled = 0 "
                "WHERE audio_sync_enabled IS NULL"
            ))
            conn.execute(text(
                "UPDATE scene SET chain_from_prev = 0 "
                "WHERE chain_from_prev IS NULL"
            ))
        # ─── Removals (DROP COLUMN, SQLite 3.35+) ─────────────────────
        for table, cols in retired.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for col_name in cols:
                if col_name in existing:
                    try:
                        conn.execute(text(f'ALTER TABLE {table} DROP COLUMN "{col_name}"'))
                        dropped.append(f"{table}.{col_name}")
                    except Exception as e:
                        # If we're on a pre-3.35 SQLite the DROP fails; the
                        # NOT NULL constraint on the orphan column will then
                        # break INSERTs. Surface the failure loudly so the
                        # user knows to upgrade Python (which bundles sqlite).
                        print(
                            f"[startup] WARNING: couldn't drop retired column "
                            f"{table}.{col_name}: {e}. Existing INSERTs may "
                            f"fail with NOT NULL constraint errors. Update "
                            f"Python (or sqlite3) to >= 3.35."
                        )
    if added:
        print(f"[startup] schema migration added: {', '.join(added)}")
    if dropped:
        print(f"[startup] schema migration dropped retired columns: {', '.join(dropped)}")


def _backfill_portrait_descriptions():
    """One-time backfill: every existing portrait variant gets its parent
    character's current description as a default. New variants snapshot the
    description at the moment of creation; this just bootstraps the old ones
    so the activate-restores-description behaviour works retroactively."""
    from sqlmodel import Session
    from app.database import engine as _engine
    from app.models import Character, CharacterAsset
    with Session(_engine) as db:
        rows = db.exec(
            __import__("sqlmodel").select(CharacterAsset).where(
                CharacterAsset.description.is_(None)  # type: ignore[union-attr]
            )
        ).all()
        if not rows:
            return
        char_by_id: dict[int, Character] = {}
        for a in rows:
            ch = char_by_id.get(a.character_id)
            if ch is None:
                ch = db.get(Character, a.character_id)
                if ch is None:
                    continue
                char_by_id[a.character_id] = ch
            a.description = ch.description
            db.add(a)
        db.commit()
        print(f"[startup] backfilled description on {len(rows)} portrait variant(s)")


def _reset_zombie_scenes():
    """On startup, reconcile scene status with on-disk reality.

    Two failure modes get fixed:

    1) STUCK MID-FLIGHT — status in generating_image/generating_video but
       the BackgroundTask that owned the row is gone (backend crashed or
       reloaded). These rows would otherwise sit forever. Reset to a
       recoverable state and surface a message so the user knows to retry.

    2) STUCK PRE-DONE — status is anything *except* "done" but the scene
       has BOTH a reference image AND a video on disk. This means generation
       actually completed but the final `scene.status = "done"` commit got
       lost (e.g. process exit between writing the file and committing the
       row). Auto-promote to "done" — the assets exist, we just need to
       reflect that.
    """
    from datetime import datetime
    from sqlmodel import Session, select
    from app.database import engine as _engine
    from app.models import Character, GenerationJob, Scene, Song

    transient = {"generating_image", "generating_video"}
    with Session(_engine) as db:
        # (1) Reset mid-flight zombies.
        stuck = db.exec(
            select(Scene).where(Scene.status.in_(transient))
        ).all()
        stuck_ids = {scene.id for scene in stuck}
        for s in stuck:
            remote_job = db.exec(
                select(GenerationJob)
                .where(
                    GenerationJob.scene_id == s.id,
                    GenerationJob.job_type == "video",
                    GenerationJob.status == "running",
                    GenerationJob.external_id.isnot(None),
                )
                .order_by(GenerationJob.id.desc())
            ).first()
            if remote_job:
                s.status = "error"
                s.error_message = (
                    "Backend restarted while the provider job was active. "
                    "Retry this scene to resume the saved job without another "
                    "paid submission."
                )
            elif s.cancel_requested:
                s.status = "cancelled"
            else:
                # Pick the most-conservative recoverable state. If a still
                # exists, the user can re-trigger video; otherwise back to pending.
                s.status = "image_ready" if s.reference_image_path else "pending"
                s.error_message = "Backend restarted mid-generation. Please retry."
            s.cancel_requested = False
            s.generation_run_id = None
            s.generation_phase = None
            s.generation_requested_at = None
            db.add(s)

        # Clear orphaned ownership tokens even if the visible status had
        # already moved away from a transient state before the process died.
        claimed = db.exec(
            select(Scene).where(Scene.generation_run_id.isnot(None))
        ).all()
        for s in claimed:
            if s.id in stuck_ids:
                continue
            s.generation_run_id = None
            s.generation_phase = None
            s.generation_requested_at = None
            s.cancel_requested = False
            db.add(s)

        # In-process tasks cannot survive a restart. Preserve provider jobs
        # that expose a resumable external handle; every other active job is
        # terminally failed so unique locks and UI spinners are released.
        active_jobs = db.exec(
            select(GenerationJob).where(
                GenerationJob.status.in_(["pending", "running"])
            )
        ).all()
        failed_jobs = 0
        for job in active_jobs:
            resumable_remote = bool(
                job.external_id
                and job.status == "running"
                and (
                    (
                        job.job_type == "video"
                        and job.provider in ("openrouter", "fal")
                    )
                    or (job.job_type == "music" and job.provider == "suno")
                )
            )
            if resumable_remote:
                continue
            job.status = "failed"
            job.error = "Backend restarted before this local task completed. Please retry."
            job.completed_at = datetime.utcnow()
            db.add(job)
            failed_jobs += 1

        interrupted_songs = db.exec(
            select(Song).where(
                Song.status.in_(["pending", "generating", "analyzing"])
            )
        ).all()
        from app.routers.songs import _find_recoverable_music_job
        for song in interrupted_songs:
            song.status = "error"
            recoverable_music = _find_recoverable_music_job(db, song)
            if recoverable_music and recoverable_music.status == "running":
                song.error_message = (
                    "Backend restarted while Suno task "
                    f"{recoverable_music.external_id} was active. Use Retry to "
                    "resume it without another paid submission."
                )
            else:
                song.error_message = (
                    "Backend restarted during song generation or analysis. "
                    "Please retry."
                )
            db.add(song)

        interrupted_characters = db.exec(
            select(Character).where(Character.portrait_status == "generating")
        ).all()
        for character in interrupted_characters:
            character.portrait_status = "error"
            character.portrait_error = (
                "Backend restarted during portrait generation. Please retry."
            )
            db.add(character)

        # (2) Heal scenes whose assets are on disk but status never advanced
        # to "done". Limited to image+video both present — we don't want to
        # silently mark partially-rendered scenes as complete.
        not_done = db.exec(
            select(Scene).where(Scene.status != "done")
        ).all()
        healed = 0
        for s in not_done:
            if s.id in stuck_ids:
                continue  # already handled above
            has_img = bool(s.reference_image_path and os.path.exists(s.reference_image_path))
            has_vid = bool(s.video_path and os.path.exists(s.video_path))
            if has_img and has_vid:
                s.status = "done"
                s.error_message = None
                db.add(s)
                healed += 1

        if (
            stuck
            or claimed
            or failed_jobs
            or interrupted_songs
            or interrupted_characters
            or healed
        ):
            db.commit()
            msg = []
            if stuck:  msg.append(f"reset {len(stuck)} zombie scene(s)")
            if failed_jobs: msg.append(f"failed {failed_jobs} interrupted local job(s)")
            if interrupted_songs: msg.append(f"reset {len(interrupted_songs)} audio analysis task(s)")
            if interrupted_characters: msg.append(f"reset {len(interrupted_characters)} portrait task(s)")
            if healed: msg.append(f"healed {healed} stuck-pending scene(s) with assets on disk")
            print(f"[startup] {', '.join(msg)}")


app = FastAPI(title="Music Video Studio API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # The launcher binds Next to 127.0.0.1. Media URLs may still use localhost;
    # fetch-based downloads need CORS permission for either frontend spelling.
    allow_origins=[
        settings.frontend_url,
        "http://localhost:3000", "http://localhost:3001",
        "http://127.0.0.1:3000", "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(settings.storage_dir, exist_ok=True)


# ---------------------------------------------------------------------------
# Storage server with HTTP Range support
#
# Why a custom handler instead of `app.mount("/storage", StaticFiles(...))`?
# Browsers seek video by sending Range: bytes=X-Y headers. The HTML5 <video>
# scrubber only works if the server responds with 206 Partial Content and
# only the requested bytes. Starlette's StaticFiles in this version returns
# 200 with the full file, which makes the scrubber refuse to seek past the
# buffered region — exact symptom: drag-handle moves but video doesn't.
# ---------------------------------------------------------------------------

MEDIA_TYPES = {
    "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
    "gif": "image/gif", "svg": "image/svg+xml",
    "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4", "ogg": "audio/ogg",
    "txt": "text/plain", "json": "application/json",
}

_RANGE_RE = re.compile(r"bytes=(?:(\d+)-(\d*)|-(\d+))$")


def _parse_byte_range(header: str, file_size: int) -> tuple[int, int] | None:
    """Parse one RFC 7233 byte range, including suffix ranges."""
    match = _RANGE_RE.fullmatch(header.strip())
    if not match or file_size <= 0:
        return None
    if match.group(3) is not None:
        suffix_length = int(match.group(3))
        if suffix_length <= 0:
            return None
        start = max(0, file_size - suffix_length)
        return start, file_size - 1
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else file_size - 1
    end = min(end, file_size - 1)
    if start > end or start >= file_size:
        return None
    return start, end


def _resolve_storage_path(path: str) -> str:
    """Resolve a /storage/... path against settings.storage_dir, blocking
    any traversal outside the storage root."""
    abs_root = os.path.realpath(settings.storage_dir)
    target = os.path.realpath(os.path.join(settings.storage_dir, path))
    try:
        inside_storage = os.path.commonpath([abs_root, target]) == abs_root
    except ValueError:
        inside_storage = False
    if not inside_storage:
        raise HTTPException(403, "Forbidden")
    if not os.path.isfile(target):
        raise HTTPException(404, "Not found")
    return target


def _media_type_for(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return MEDIA_TYPES.get(ext, "application/octet-stream")


@app.head("/storage/{path:path}")
async def storage_head(path: str):
    abs_file = _resolve_storage_path(path)
    return Response(
        status_code=200,
        headers={
            "Content-Length": str(os.path.getsize(abs_file)),
            "Content-Type": _media_type_for(abs_file),
            "Accept-Ranges": "bytes",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/storage/{path:path}")
async def storage_get(path: str, request: Request):
    abs_file = _resolve_storage_path(path)
    file_size = os.path.getsize(abs_file)
    media_type = _media_type_for(abs_file)
    range_header = request.headers.get("range") or request.headers.get("Range")

    if not range_header:
        # No Range header — serve the whole file, but advertise that we
        # support ranges so the browser knows to use them for seeking.
        return FileResponse(
            abs_file,
            media_type=media_type,
            headers={
                "Accept-Ranges": "bytes",
                "X-Content-Type-Options": "nosniff",
            },
        )

    byte_range = _parse_byte_range(range_header, file_size)
    if byte_range is None:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )
    start, end = byte_range

    length = end - start + 1
    CHUNK = 1024 * 1024  # 1 MB

    def iterfile():
        with open(abs_file, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                data = f.read(min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        iterfile(),
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "X-Content-Type-Options": "nosniff",
            # Allow JS in the page to read the response if it ever needs to
            # (the download button does fetch+blob — it benefits from CORS
            # being permissive, which our middleware already provides).
        },
    )

app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(songs.router,    prefix="/api/songs",    tags=["songs"])
app.include_router(scenes.router,   prefix="/api/scenes",   tags=["scenes"])
app.include_router(generation.router, prefix="/api/generation", tags=["generation"])


@app.get("/", include_in_schema=False)
async def root(request: Request):
    """Redirect browsers to the frontend so the preview pane never lands on
    a bare 404 if it happens to point at the backend port."""
    accepts = request.headers.get("accept", "")
    if "text/html" in accepts:
        return RedirectResponse(url=settings.frontend_url, status_code=307)
    return {
        "service": "Music Video Studio API",
        "frontend_url": settings.frontend_url,
        "docs_url": "/docs",
    }


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/models")
async def list_models():
    from sqlmodel import Session
    from app.database import engine
    from app.config import VIDEO_MODELS, IMAGE_MODELS, LLM_MODELS, MODEL_CATALOG_VERIFIED_AT
    from app.services.model_inventory import build_video_inventory
    with Session(engine) as db:
        inventory = build_video_inventory(db)
    used_models = {
        key: {**config, **inventory[key]}
        for key, config in VIDEO_MODELS.items()
        if config.get("show_in_catalog") or inventory[key]["history"]["attempts"] or inventory[key]["history"]["video_assets"]
    }
    return {
        "verified_at": MODEL_CATALOG_VERIFIED_AT,
        "video": used_models,
        "image": IMAGE_MODELS,
        "llm": LLM_MODELS,
    }
