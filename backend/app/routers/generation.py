from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import List, Optional

from app.database import get_session, engine
from app.models import Scene, GenerationJob, Project
from app.services.generation_service import generate_scene
from app.services.assembly import assemble_project

router = APIRouter()


class GenerateSceneRequest(BaseModel):
    scene_id: int
    force: bool = False  # re-generate even if already done
    phase: str = "all"   # "image" | "video" | "all"


class GenerateBatchRequest(BaseModel):
    project_id: int
    scene_ids: Optional[List[int]] = None  # None = all pending scenes
    force: bool = False
    phase: str = "all"   # "image" | "video" | "all"


@router.post("/scene/{scene_id}/cancel")
async def cancel_scene_generation(scene_id: int, db: Session = Depends(get_session)):
    """Soft-cancel a running scene generation.

    Sets a flag the pipeline checks between phases (and between video poll
    iterations). Note: an OpenRouter video job already submitted will still
    be billed — this only stops us waiting for it.
    """
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    if scene.status not in ("pending", "generating_image", "generating_video", "lipsync"):
        return {"message": f"Scene not running (status={scene.status}); nothing to cancel.", "scene_id": scene_id}
    scene.cancel_requested = True
    db.add(scene); db.commit()
    return {"message": "Cancel requested. Pipeline will stop at next checkpoint.", "scene_id": scene_id}


@router.post("/scene")
async def trigger_scene_generation(
    req: GenerateSceneRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    scene = db.get(Scene, req.scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    if req.phase not in ("image", "video", "lipsync", "all"):
        raise HTTPException(400, "phase must be one of: image, video, lipsync, all")
    if scene.status == "done" and not req.force and req.phase != "image":
        raise HTTPException(400, "Scene already done. Use force=true to regenerate.")
    if scene.status in ("generating_image", "generating_video", "lipsync"):
        raise HTTPException(400, f"Scene is already being processed: {scene.status}")

    scene.status = "pending"
    scene.error_message = None
    db.add(scene)
    db.commit()

    background_tasks.add_task(generate_scene, scene.id, engine, req.phase)
    return {"message": f"Generation started ({req.phase})", "scene_id": scene.id, "phase": req.phase}


@router.post("/batch")
async def trigger_batch_generation(
    req: GenerateBatchRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    if req.scene_ids:
        scenes = [db.get(Scene, sid) for sid in req.scene_ids if db.get(Scene, sid)]
    elif req.force:
        scenes = db.exec(
            select(Scene).where(Scene.project_id == req.project_id)
        ).all()
    else:
        scenes = db.exec(
            select(Scene).where(
                Scene.project_id == req.project_id,
                Scene.status.in_(["pending", "error", "image_ready", "cancelled"]),
            )
        ).all()

    queued = []
    for scene in scenes:
        if scene.status in ("generating_image", "generating_video", "lipsync"):
            continue
        scene.status = "pending"
        scene.error_message = None
        db.add(scene)
        background_tasks.add_task(generate_scene, scene.id, engine, req.phase)
        queued.append(scene.id)

    db.commit()
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

    scenes = db.exec(select(Scene).where(Scene.project_id == project_id)).all()
    done_count = sum(1 for s in scenes if s.status == "done")
    if done_count == 0:
        raise HTTPException(400, "No completed scenes to assemble")

    # Cancel any prior running assembly job for this project so the latest
    # request is the canonical "current" one.
    prior_running = db.exec(
        select(GenerationJob).where(
            GenerationJob.project_id == project_id,
            GenerationJob.job_type == "assembly",
            GenerationJob.status == "running",
        )
    ).all()
    for j in prior_running:
        j.status = "failed"
        j.error = "Superseded by a new assembly request"
        j.completed_at = datetime.utcnow()
        db.add(j)

    job = GenerationJob(
        project_id=project_id,
        job_type="assembly",
        provider="ffmpeg",
        status="running",
        cost_usd=0.0,
        cost_detail=f"Concatenating {done_count} scenes + muxing audio",
    )
    db.add(job); db.commit(); db.refresh(job)

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
        "job_count": len(jobs),
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
        output = await assemble_project(project_id, engine)
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
