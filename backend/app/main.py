from contextlib import asynccontextmanager
import os
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from app.database import create_db_and_tables
from app.config import settings
from app.routers import projects, songs, scenes, generation


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    os.makedirs(settings.storage_dir, exist_ok=True)
    _apply_schema_migrations()
    _reset_zombie_scenes()
    yield


def _apply_schema_migrations():
    """Add columns that the current model defines but the existing SQLite
    database is missing. `SQLModel.metadata.create_all()` only creates new
    tables — it doesn't ALTER existing ones to add new fields. Without this
    hook, adding a new field to a SQLModel class would silently fail until
    you nuke the database.
    """
    from sqlalchemy import inspect, text
    from app.database import engine as _engine
    expected = {
        # table -> { column: SQL definition fragment used for ALTER TABLE }
        "scene": {
            "chain_from_prev": "BOOLEAN NOT NULL DEFAULT 0",
            "extracted_last_frame_path": "VARCHAR",
        },
    }
    insp = inspect(_engine)
    added: list[str] = []
    with _engine.begin() as conn:
        for table, cols in expected.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for col_name, col_def in cols.items():
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
                    added.append(f"{table}.{col_name}")
    if added:
        print(f"[startup] schema migration added: {', '.join(added)}")


def _reset_zombie_scenes():
    """On startup, reconcile scene status with on-disk reality.

    Two failure modes get fixed:

    1) STUCK MID-FLIGHT — status in generating_image/generating_video/lipsync
       but the BackgroundTask that owned the row is gone (backend crashed or
       reloaded). These rows would otherwise sit forever. Reset to a
       recoverable state and surface a message so the user knows to retry.

    2) STUCK PRE-DONE — status is anything *except* "done" but the scene
       has BOTH a reference image AND a video on disk. This means generation
       actually completed but the final `scene.status = "done"` commit got
       lost (e.g. process exit between writing the file and committing the
       row). Auto-promote to "done" — the assets exist, we just need to
       reflect that.
    """
    from sqlmodel import Session, select
    from app.database import engine as _engine
    from app.models import Scene

    transient = {"generating_image", "generating_video", "lipsync"}
    with Session(_engine) as db:
        # (1) Reset mid-flight zombies.
        stuck = db.exec(
            select(Scene).where(Scene.status.in_(transient))
        ).all()
        for s in stuck:
            if s.cancel_requested:
                s.status = "cancelled"
            else:
                # Pick the most-conservative recoverable state. If a still
                # exists, the user can re-trigger video; otherwise back to pending.
                s.status = "image_ready" if s.reference_image_path else "pending"
                s.error_message = "Backend restarted mid-generation. Please retry."
            s.cancel_requested = False
            db.add(s)

        # (2) Heal scenes whose assets are on disk but status never advanced
        # to "done". Limited to image+video both present — we don't want to
        # silently mark partially-rendered scenes as complete.
        not_done = db.exec(
            select(Scene).where(Scene.status != "done")
        ).all()
        healed = 0
        for s in not_done:
            if s.id in {z.id for z in stuck}:
                continue  # already handled above
            has_img = bool(s.reference_image_path and os.path.exists(s.reference_image_path))
            has_vid = bool(s.video_path and os.path.exists(s.video_path))
            if has_img and has_vid:
                s.status = "done"
                s.error_message = None
                db.add(s)
                healed += 1

        if stuck or healed:
            db.commit()
            msg = []
            if stuck:  msg.append(f"reset {len(stuck)} zombie scene(s)")
            if healed: msg.append(f"healed {healed} stuck-pending scene(s) with assets on disk")
            print(f"[startup] {', '.join(msg)}")


app = FastAPI(title="Music Video Studio API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url, "http://localhost:3000", "http://localhost:3001"],
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

_RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


def _resolve_storage_path(path: str) -> str:
    """Resolve a /storage/... path against settings.storage_dir, blocking
    any traversal outside the storage root."""
    abs_root = os.path.abspath(settings.storage_dir)
    target = os.path.abspath(os.path.join(settings.storage_dir, path))
    if not target.startswith(abs_root + os.sep) and target != abs_root:
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
            headers={"Accept-Ranges": "bytes"},
        )

    m = _RANGE_RE.match(range_header.strip())
    if not m:
        raise HTTPException(416, "Invalid Range header")
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else file_size - 1
    end = min(end, file_size - 1)
    if start > end or start >= file_size:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{file_size}"},
        )

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
    from app.config import VIDEO_MODELS, IMAGE_MODELS, LLM_MODELS, LIPSYNC_MODELS
    return {
        "video": VIDEO_MODELS,
        "image": IMAGE_MODELS,
        "llm": LLM_MODELS,
        "lipsync": LIPSYNC_MODELS,
    }
