"""Helpers for recovering abandoned scene-generation ownership claims."""

from datetime import datetime, timedelta
import os

from sqlmodel import Session, select

from app.models import GenerationJob, Scene


SCENE_CLAIM_GRACE_PERIOD = timedelta(minutes=15)
TRANSIENT_SCENE_STATUSES = {"generating_image", "generating_video"}


def active_generation_job(scene_id: int, db: Session) -> GenerationJob | None:
    """Return the newest provider/local job that can still be doing work."""
    return db.exec(
        select(GenerationJob)
        .where(
            GenerationJob.scene_id == scene_id,
            GenerationJob.status.in_(["pending", "running"]),
        )
        .order_by(GenerationJob.id.desc())
    ).first()


def _claim_age(
    requested_at: datetime | None,
    *,
    now: datetime,
) -> timedelta | None:
    if requested_at is None:
        return None
    if requested_at.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=requested_at.tzinfo)
    elif requested_at.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    return now - requested_at


def release_stale_scene_claim(
    scene: Scene,
    db: Session,
    *,
    now: datetime | None = None,
    grace_period: timedelta = SCENE_CLAIM_GRACE_PERIOD,
) -> bool:
    """Release an old claim when no generation job exists to justify it.

    A newly queued callback gets a grace period because it may not have created
    its first GenerationJob row yet. Once that period passes, retaining the
    claim only makes the scene permanently uneditable. Any callback that later
    wakes up is harmless: ``generate_scene`` rejects callbacks whose run id no
    longer matches the scene.
    """
    if not scene.generation_run_id:
        return False
    if scene.id is not None and active_generation_job(scene.id, db):
        return False

    age = _claim_age(scene.generation_requested_at, now=now or datetime.utcnow())
    if age is not None and age < grace_period:
        return False

    scene.generation_run_id = None
    scene.generation_phase = None
    scene.generation_requested_at = None
    scene.cancel_requested = False

    if scene.status in TRANSIENT_SCENE_STATUSES or scene.status == "pending":
        if scene.video_path and os.path.isfile(scene.video_path):
            scene.status = "done"
        elif scene.reference_image_path and os.path.isfile(scene.reference_image_path):
            scene.status = "image_ready"
        else:
            scene.status = "pending"

    db.add(scene)
    db.commit()
    db.refresh(scene)
    return True


def release_stale_project_claims(project_id: int, db: Session) -> list[int]:
    """Release abandoned ownership claims before project-wide queue actions."""
    claimed = db.exec(
        select(Scene).where(
            Scene.project_id == project_id,
            Scene.generation_run_id.isnot(None),
        )
    ).all()
    return [
        scene.id
        for scene in claimed
        if release_stale_scene_claim(scene, db) and scene.id is not None
    ]
