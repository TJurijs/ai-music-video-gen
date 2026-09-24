"""Read-only video inventory from generation history, never scene defaults.

Job snapshots are authoritative. Older jobs predate snapshots, so their saved
billing label identifies the model. Assets count as saved variants, not renders:
local retiming can create several assets from one provider job.
"""

import json
import re
from collections import defaultdict

from sqlmodel import Session, select

from ..config import VIDEO_MODELS
from ..models import GenerationJob, Project, Scene, SceneAsset


def _object(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _aliases() -> list[tuple[str, str]]:
    aliases = []
    for key, model in VIDEO_MODELS.items():
        for value in (key, model.get("name"), model.get("model_id"),
                      model.get("fal_audio_model_id"), model.get("fal_r2v_model_id")):
            if value:
                aliases.append((str(value).casefold(), key))
        # UI qualifiers were added after some billing labels were stored.
        name = re.sub(r"\s*\([^)]*\)\s*$", "", model.get("name", ""))
        if name:
            aliases.append((name.casefold(), key))
    return sorted(set(aliases), key=lambda entry: -len(entry[0]))


def _resolve_model(value: str | None, aliases: list[tuple[str, str]]) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    for alias, key in aliases:
        if normalized == alias or (
            normalized.startswith(alias)
            and normalized[len(alias):len(alias) + 1] in (" ", "\t", "\n", "×", "·", "(")
        ):
            return key
    return None


def _job_model(job: GenerationJob, aliases: list[tuple[str, str]]) -> str | None:
    snapshot = _object(job.request_json)
    for field in ("model_key", "model_id"):
        key = _resolve_model(snapshot.get(field), aliases)
        if key:
            return key
    return _resolve_model(job.cost_detail, aliases)


def _error_evidence(error: str | None) -> tuple[bool, bool, bool]:
    """Return confirmed policy, possible policy, provider-response evidence.

    Explicit provider markers count; a generic failed job or local OS error does
    not. In particular, an empty result mentioning possible filtering is not a
    confirmed rejection. Never return provider error bodies to the caller.
    """
    message = (error or "").casefold()
    policy = (
        "inputimagesensitivecontentdetected" in message
        or bool(re.search(r'"type"\s*:\s*"content_policy_violation"', message))
        or message.startswith("image content filter refused:")
    )
    possible = not policy and "no output (content may have been filtered)" in message
    provider_response = policy or possible or any(marker in message for marker in (
        "openrouter video submit ", "fal result fetch failed (", "video job failed:",
    ))
    return policy, possible, provider_response


def _counts() -> dict:
    return dict(attempts=0, submitted=0, provider_attempts=0, completed=0, failed=0,
                pending=0, cancelled=0, policy_rejections=0, possible_policy_rejections=0)


def _add_job(counts: dict, job: GenerationJob) -> None:
    counts["attempts"] += 1
    if job.status in ("pending", "running"):
        counts["pending"] += 1
    elif job.status in ("completed", "failed", "cancelled"):
        counts[job.status] += 1
    accepted = bool(job.external_id or job.result_url or job.result_path or job.status == "completed")
    policy, possible, responded = _error_evidence(job.error)
    counts["submitted"] += int(accepted)
    counts["provider_attempts"] += int(accepted or responded)
    counts["policy_rejections"] += int(policy)
    counts["possible_policy_rejections"] += int(possible)


def build_video_inventory(db: Session) -> dict[str, dict]:
    """Return all catalog keys with a safe, JSON-ready ``history`` summary.

    ``attempts`` counts local generation-job records. ``submitted`` counts jobs
    accepted by a provider (external handle, saved result, or completed status).
    ``provider_attempts`` additionally includes explicit submission rejections.
    None of these counts are inferred from assets or currently selected models.
    """
    aliases = _aliases()
    projects = {project.id: project.name for project in db.exec(select(Project)).all()}
    scenes = {scene.id: scene.project_id for scene in db.exec(select(Scene)).all()}
    histories = {
        key: {**_counts(), "video_assets": 0, "local_edits": 0,
              "active_scenes": 0, "unique_scenes": 0}
        for key in VIDEO_MODELS
    }
    project_ids = defaultdict(set)
    scene_ids = defaultdict(set)
    active_scenes = defaultdict(set)
    routes = defaultdict(dict)

    for job in db.exec(select(GenerationJob).where(GenerationJob.job_type == "video")).all():
        key = _job_model(job, aliases)
        if not key:
            continue
        _add_job(histories[key], job)
        project_ids[key].add(job.project_id)
        if job.scene_id is not None:
            scene_ids[key].add(job.scene_id)
        snapshot = _object(job.request_json)
        route = snapshot.get("route") or ("openrouter-video" if job.provider == "openrouter" else "legacy-fal-video")
        # Route is a local adapter label, not a provider URL or request body.
        if not isinstance(route, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", route):
            route = "unknown"
        route_key = (job.provider, route)
        route_history = routes[key].setdefault(route_key, {
            "provider": job.provider, "route": route, **_counts(),
        })
        _add_job(route_history, job)

    for asset in db.exec(select(SceneAsset).where(SceneAsset.asset_type == "video")).all():
        key = _resolve_model(asset.model_used, aliases)
        if not key:
            continue
        history = histories[key]
        history["video_assets"] += 1
        metadata = _object(asset.metadata_json)
        history["local_edits"] += int(bool(metadata.get("retiming")))
        scene_ids[key].add(asset.scene_id)
        if asset.scene_id in scenes:
            project_ids[key].add(scenes[asset.scene_id])
        if asset.is_active:
            active_scenes[key].add(asset.scene_id)

    inventory = {}
    for key, history in histories.items():
        history["projects"] = [
            {"id": project_id, "name": projects.get(project_id, f"Project {project_id}")}
            for project_id in sorted(project_ids[key])
        ]
        history["unique_scenes"] = len(scene_ids[key])
        history["active_scenes"] = len(active_scenes[key])
        history["routes"] = sorted(routes[key].values(), key=lambda route: (route["provider"], route["route"]))
        if history["policy_rejections"]:
            history["guardrail_status"] = "rejections_observed"
            observation = f"{history['policy_rejections']} confirmed policy rejection(s) in {history['provider_attempts']} provider attempt(s)."
        elif history["possible_policy_rejections"]:
            history["guardrail_status"] = "possible_rejections"
            observation = f"{history['possible_policy_rejections']} empty output(s) mentioned possible filtering; policy rejection is unconfirmed."
        elif history["provider_attempts"]:
            history["guardrail_status"] = "none_observed"
            observation = f"No confirmed policy rejections in {history['provider_attempts']} provider attempt(s)."
        else:
            history["guardrail_status"] = "no_history"
            observation = "No provider outcome evidence in retained history."
        history["evidence_note"] = observation + " This is local history, not a guarantee of future acceptance. Retried jobs count separately; deleted history is unavailable."
        inventory[key] = {"history": history}
    return inventory
