from datetime import datetime
import asyncio
import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import Session, select
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
from typing import List, Optional

from app.database import get_session, engine
from app.models import Scene, GenerationJob, Project
from app.services.generation_service import generate_scene, scene_generation_preflight
from app.services.generation_state import (
    release_stale_project_claims,
    release_stale_scene_claim,
)
from app.services.assembly import assemble_project

router = APIRouter()


def _claim_scene_run(db: Session, scene_id: int, phase: str) -> str | None:
    """Atomically claim a scene so retries cannot queue duplicate paid work."""
    run_id = uuid.uuid4().hex
    result = db.exec(
        update(Scene)
        .where(
            Scene.id == scene_id,
            Scene.generation_run_id.is_(None),
        )
        .values(
            generation_run_id=run_id,
            generation_phase=phase,
            generation_requested_at=datetime.utcnow(),
            status="pending",
            error_message=None,
            cancel_requested=False,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return run_id


class GenerateSceneRequest(BaseModel):
    scene_id: int
    force: bool = False  # re-generate even if already done
    phase: str = "all"   # "image" | "video" | "all"


class GenerateBatchRequest(BaseModel):
    project_id: int
    scene_ids: Optional[List[int]] = None  # None = all pending scenes
    force: bool = False
    phase: str = "all"   # "image" | "video" | "all"


def _batch_scenes(db: Session, req: GenerateBatchRequest) -> list[Scene]:
    if req.scene_ids:
        requested = set(req.scene_ids)
        scenes = db.exec(
            select(Scene).where(
                Scene.project_id == req.project_id,
                Scene.id.in_(requested),
            )
        ).all()
    elif req.force:
        scenes = db.exec(
            select(Scene).where(Scene.project_id == req.project_id)
        ).all()
    elif req.phase == "image":
        scenes = db.exec(
            select(Scene).where(
                Scene.project_id == req.project_id,
                Scene.reference_image_path.is_(None),
                Scene.generation_run_id.is_(None),
            )
        ).all()
    else:
        scenes = db.exec(
            select(Scene).where(
                Scene.project_id == req.project_id,
                Scene.status.in_(["pending", "error", "image_ready", "cancelled"]),
            )
        ).all()
    return sorted(scenes, key=lambda scene: (scene.order, scene.id or 0))


@router.post("/preflight")
def preflight_batch_generation(
    req: GenerateBatchRequest,
    db: Session = Depends(get_session),
):
    if req.phase not in ("image", "video", "all"):
        raise HTTPException(400, "phase must be one of: image, video, all")
    if not db.get(Project, req.project_id):
        raise HTTPException(404, "Project not found")
    release_stale_project_claims(req.project_id, db)
    reports = [
        scene_generation_preflight(
            scene,
            db,
            phase=req.phase,
            force=req.force,
        )
        for scene in _batch_scenes(db, req)
    ]
    return {
        "phase": req.phase,
        "scene_count": len(reports),
        "ready_count": sum(1 for report in reports if report["ready"]),
        "estimated_cost": round(
            sum(report["estimated_cost"] for report in reports), 4,
        ),
        "scenes": reports,
    }


@router.post("/scene/{scene_id}/cancel")
async def cancel_scene_generation(scene_id: int, db: Session = Depends(get_session)):
    """Soft-cancel a running scene generation.

    Sets a flag the pipeline checks between phases and poll iterations. fal
    jobs are cancelled through their queue-provided cancellation URL.
    OpenRouter jobs retain their provider ID and can be resumed without a
    duplicate submission because this integration has no upstream cancel API.
    """
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    if release_stale_scene_claim(scene, db):
        return {
            "message": "Abandoned queued generation was released.",
            "scene_id": scene_id,
        }
    if not scene.generation_run_id and scene.status not in ("generating_image", "generating_video"):
        return {"message": f"Scene not running (status={scene.status}); nothing to cancel.", "scene_id": scene_id}
    scene.cancel_requested = True
    db.add(scene); db.commit()
    return {
        "message": (
            "Cancel requested. fal jobs will be cancelled upstream; "
            "OpenRouter polling will stop and keep the saved job resumable."
        ),
        "scene_id": scene_id,
    }


@router.post("/scene")
async def trigger_scene_generation(
    req: GenerateSceneRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    scene = db.get(Scene, req.scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    release_stale_scene_claim(scene, db)
    if req.phase not in ("image", "video", "all"):
        raise HTTPException(400, "phase must be one of: image, video, all")
    # Image gen is ADDITIVE on a done scene — it creates a new still variant
    # without overwriting the video. Only video-phase regeneration on a done
    # scene requires explicit force, because that replaces the rendered clip.
    if scene.status == "done" and not req.force and req.phase != "image":
        raise HTTPException(400, "Scene already done. Use force=true to regenerate.")
    report = scene_generation_preflight(
        scene,
        db,
        phase=req.phase,
        force=req.force,
    )
    if not report["ready"]:
        raise HTTPException(
            400,
            "; ".join(report["errors"]),
        )
    run_id = _claim_scene_run(db, scene.id, req.phase)
    if not run_id:
        raise HTTPException(409, "Scene generation is already queued or running")

    background_tasks.add_task(
        generate_scene, scene.id, engine, req.phase, run_id, req.force,
    )
    return {
        "message": f"Generation started ({req.phase})",
        "scene_id": scene.id,
        "phase": req.phase,
        "run_id": run_id,
    }


@router.post("/batch")
async def trigger_batch_generation(
    req: GenerateBatchRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    if req.phase not in ("image", "video", "all"):
        raise HTTPException(400, "phase must be one of: image, video, all")
    if not db.get(Project, req.project_id):
        raise HTTPException(404, "Project not found")
    release_stale_project_claims(req.project_id, db)
    scenes = _batch_scenes(db, req)
    reports = [
        scene_generation_preflight(
            scene,
            db,
            phase=req.phase,
            force=req.force,
        )
        for scene in scenes
    ]
    blocked = [report for report in reports if not report["ready"]]
    if blocked:
        summary = " | ".join(
            f"Scene {report['scene_order']}: {', '.join(report['errors'])}"
            for report in blocked[:5]
        )
        if len(blocked) > 5:
            summary += f" | plus {len(blocked) - 5} more blocked scene(s)"
        raise HTTPException(400, f"Generation preflight failed. {summary}")

    queued = []
    for scene in scenes:
        run_id = _claim_scene_run(db, scene.id, req.phase)
        if not run_id:
            continue
        background_tasks.add_task(
            generate_scene, scene.id, engine, req.phase, run_id, req.force,
        )
        queued.append(scene.id)

    return {"message": f"Queued {len(queued)} scenes ({req.phase})", "scene_ids": queued, "phase": req.phase}


@router.post("/assemble/{project_id}")
async def trigger_assembly(
    project_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    scenes = db.exec(
        select(Scene).where(Scene.project_id == project_id).order_by(Scene.order)
    ).all()
    done_count = 0
    while done_count < len(scenes) and scenes[done_count].status == "done":
        done_count += 1
    if done_count == 0:
        raise HTTPException(400, "Scene 1 must be complete before assembly")
    if any(scene.status == "done" for scene in scenes[done_count:]):
        raise HTTPException(
            400,
            "Partial assembly requires a contiguous completed sequence starting at scene 1",
        )

    job = GenerationJob(
        project_id=project_id,
        job_type="assembly",
        provider="ffmpeg",
        status="running",
        cost_usd=0.0,
        cost_detail=f"Concatenating {done_count} scenes + muxing audio",
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        active = db.exec(
            select(GenerationJob).where(
                GenerationJob.project_id == project_id,
                GenerationJob.job_type == "assembly",
                GenerationJob.status == "running",
            )
        ).first()
        raise HTTPException(
            409,
            f"Assembly is already running (job {active.id if active else 'unknown'})",
        ) from exc
    db.refresh(job)

    background_tasks.add_task(_assemble_bg, project_id, job.id)
    return {"message": f"Assembling {done_count} scenes", "project_id": project_id, "job_id": job.id}


@router.get("/assemble/{project_id}/status")
def get_assembly_status(project_id: int, db: Session = Depends(get_session)):
    """Return the latest assembly job's status + a relative URL when complete.

    Frontend polls this to know when the assembled video is ready, where it
    lives, and to render error messages on failure.
    """
    job = db.exec(
        select(GenerationJob)
        .where(
            GenerationJob.project_id == project_id,
            GenerationJob.job_type == "assembly",
        )
        .order_by(GenerationJob.id.desc())
    ).first()
    if not job:
        return {"status": "none", "url": None}

    url = None
    if job.status == "completed" and job.result_path:
        from app.services.urls import to_storage_url
        url = to_storage_url(job.result_path)

    return {
        "status": job.status,
        "url": url,
        "error": job.error,
        "started_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "job_id": job.id,
    }


@router.get("/jobs/{project_id}")
def get_jobs(project_id: int, db: Session = Depends(get_session)):
    jobs = db.exec(
        select(GenerationJob)
        .where(GenerationJob.project_id == project_id)
        .order_by(GenerationJob.created_at.desc())
    ).all()
    return jobs


@router.get("/costs/{project_id}")
def get_project_costs(project_id: int, db: Session = Depends(get_session)):
    """Aggregate cost breakdown for a project."""
    jobs = db.exec(
        select(GenerationJob).where(GenerationJob.project_id == project_id)
    ).all()

    by_type: dict = {}
    by_provider: dict = {}
    by_scene: dict = {}
    total = 0.0

    for j in jobs:
        # A local job row is created before some provider calls. Failed jobs
        # without an external handle were never confirmed as submitted and
        # must not inflate the UI's "spent" total. Completed work and queued
        # video jobs with provider IDs are billable attempts.
        billable = j.status == "completed" or bool(j.external_id)
        if not billable:
            continue
        c = j.cost_usd or 0.0
        total += c
        by_type[j.job_type] = round(by_type.get(j.job_type, 0.0) + c, 4)
        by_provider[j.provider] = round(by_provider.get(j.provider, 0.0) + c, 4)
        if j.scene_id:
            by_scene[j.scene_id] = round(by_scene.get(j.scene_id, 0.0) + c, 4)

    return {
        "total_usd": round(total, 4),
        "by_type": by_type,
        "by_provider": by_provider,
        "by_scene": by_scene,
        "job_count": sum(
            1 for job in jobs
            if job.status == "completed" or bool(job.external_id)
        ),
    }


@router.get("/status/{project_id}")
def get_project_status(project_id: int, db: Session = Depends(get_session)):
    scenes = db.exec(select(Scene).where(Scene.project_id == project_id)).all()
    by_status: dict = {}
    for s in scenes:
        by_status[s.status] = by_status.get(s.status, 0) + 1
    return {
        "total": len(scenes),
        "by_status": by_status,
        "complete_pct": round(by_status.get("done", 0) / max(len(scenes), 1) * 100),
    }


async def _assemble_bg(project_id: int, job_id: int):
    """Run assembly + update the GenerationJob row so the frontend can
    poll for completion + retrieve the final video URL."""
    try:
        output = await asyncio.to_thread(assemble_project, project_id, engine, job_id)
        print(f"Assembly complete: {output}")
        with Session(engine) as db:
            job = db.get(GenerationJob, job_id)
            if job:
                job.status = "completed"
                job.result_path = output
                job.completed_at = datetime.utcnow()
                db.add(job); db.commit()
    except Exception as e:
        print(f"Assembly failed: {e}")
        with Session(engine) as db:
            job = db.get(GenerationJob, job_id)
            if job:
                job.status = "failed"
                job.error = str(e)[:1000]
                job.completed_at = datetime.utcnow()
                db.add(job); db.commit()
