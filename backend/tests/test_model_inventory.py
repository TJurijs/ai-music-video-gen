"""Inventory must describe historical attempts without changing their meaning."""

import json

from sqlmodel import Session

from app.models import GenerationJob, Project, Scene, SceneAsset
from app.services.model_inventory import build_video_inventory


def _project_scene(db, name="History"):
    project = Project(name=name)
    db.add(project)
    db.flush()
    scene = Scene(project_id=project.id, order=1, audio_start=0, audio_end=15,
                  video_model="wan-3.0")
    db.add(scene)
    db.flush()
    return project, scene


def _job(db, project, scene, **values):
    fields = dict(project_id=project.id, scene_id=scene.id, job_type="video",
                  provider="openrouter", status="completed")
    fields.update(values)
    job = GenerationJob(**fields)
    db.add(job)
    db.flush()
    return job


def test_snapshot_overrides_billing_label_and_current_scene(test_engine):
    with Session(test_engine) as db:
        project, scene = _project_scene(db)
        _job(db, project, scene, cost_detail="Seedance 2.0 Fast × 15s", request_json=json.dumps({
            "model_key": "wan-2.7", "model_id": "alibaba/wan-2.7", "route": "wan-i2v-audio",
        }))
        inventory = build_video_inventory(db)
        assert inventory["wan-2.7"]["history"]["completed"] == 1
        assert inventory["seedance-2.0-fast"]["history"]["attempts"] == 0
        assert inventory["wan-3.0"]["history"]["attempts"] == 0


def test_legacy_name_matching_keeps_fast_and_mini_distinct(test_engine):
    with Session(test_engine) as db:
        project, scene = _project_scene(db)
        for label in ("Seedance 2.0 Fast × 15s", "Seedance 2.0 Mini × 15s", "Seedance 2.0 × 8s",
                      "Veo 3.1 × 8s", "Veo 3.1 Lite × 8s"):
            _job(db, project, scene, cost_detail=label)
        inventory = build_video_inventory(db)
        for key in ("seedance-2.0-fast", "seedance-2.0-mini", "seedance-2.0", "veo-3.1", "veo-3.1-lite"):
            assert inventory[key]["history"]["attempts"] == 1


def test_submission_rejections_are_used_but_not_accepted_jobs(test_engine):
    with Session(test_engine) as db:
        project, scene = _project_scene(db)
        _job(db, project, scene, status="failed", cost_detail="Seedance 2.0 Mini × 15s",
             error='OpenRouter video submit 400: InputImageSensitiveContentDetected.PrivacyInformation')
        history = build_video_inventory(db)["seedance-2.0-mini"]["history"]
        assert history["attempts"] == history["failed"] == history["provider_attempts"] == 1
        assert history["policy_rejections"] == 1
        assert history["submitted"] == 0
        assert history["guardrail_status"] == "rejections_observed"
        assert history["projects"] == [{"id": project.id, "name": "History"}]


def test_local_failures_and_possible_filtering_do_not_inflate_policy(test_engine):
    with Session(test_engine) as db:
        project, scene = _project_scene(db)
        for error in ("[Errno 22] Invalid argument", "Backend restarted before this local task completed."):
            _job(db, project, scene, status="failed", cost_detail="Veo 3.1 Lite × 8s", error=error)
        _job(db, project, scene, status="failed", external_id="accepted-job",
             cost_detail="Veo 3.1 Lite × 8s",
             error="Video job failed: Video generation completed with no output (content may have been filtered)")
        history = build_video_inventory(db)["veo-3.1-lite"]["history"]
        assert history["failed"] == 3
        assert history["provider_attempts"] == history["submitted"] == 1
        assert history["possible_policy_rejections"] == 1
        assert history["policy_rejections"] == 0
        assert history["guardrail_status"] == "possible_rejections"


def test_assets_and_local_retiming_do_not_count_as_new_generations(test_engine):
    with Session(test_engine) as db:
        project, scene = _project_scene(db)
        _job(db, project, scene, provider="fal", cost_detail="LTX 2.5 Fast × 15s")
        for index, metadata in enumerate(({}, {"retiming": {"method": "slow"}}, {"original_provider_output": True})):
            db.add(SceneAsset(scene_id=scene.id, asset_type="video", model_used="ltx-2.5-fast",
                              file_path=f"variant-{index}.mp4", is_active=index == 1,
                              metadata_json=json.dumps(metadata)))
        db.flush()
        history = build_video_inventory(db)["ltx-2.5-fast"]["history"]
        assert history["attempts"] == history["completed"] == 1
        assert history["video_assets"] == 3
        assert history["local_edits"] == 1
        assert history["active_scenes"] == history["unique_scenes"] == 1


def test_snapshot_provider_id_fallback_and_malformed_json_are_tolerated(test_engine):
    with Session(test_engine) as db:
        project, scene = _project_scene(db)
        _job(db, project, scene, request_json=json.dumps({"model_key": [], "model_id": "fal-ai/wan/v2.7/image-to-video"}))
        _job(db, project, scene, request_json="not json", cost_detail="Kling 3.0 Pro × 15s")
        _job(db, project, scene, request_json="[]", cost_detail="unknown model")
        inventory = build_video_inventory(db)
        assert inventory["wan-2.7"]["history"]["attempts"] == 1
        assert inventory["kling-v3.0-pro"]["history"]["attempts"] == 1


def test_route_outcomes_stay_separate_and_error_inputs_are_not_exposed(test_engine):
    with Session(test_engine) as db:
        project, scene = _project_scene(db)
        _job(db, project, scene, cost_detail="Wan 2.7 × 8s")
        _job(db, project, scene, provider="fal", status="failed", external_id="private-provider-id",
             request_json=json.dumps({"model_key": "wan-2.7", "route": "wan-i2v-audio", "prompt": "private prompt"}),
             error='fal result fetch failed (422): {"type":"content_policy_violation","input":{"prompt":"private prompt"}}')
        inventory = build_video_inventory(db)
        history = inventory["wan-2.7"]["history"]
        assert len(history["routes"]) == 2
        assert next(r for r in history["routes"] if r["provider"] == "fal")["policy_rejections"] == 1
        assert next(r for r in history["routes"] if r["provider"] == "openrouter")["policy_rejections"] == 0
        assert "private" not in json.dumps(inventory)


def test_untried_scene_setting_is_not_usage_but_legacy_assets_are_retained(test_engine):
    with Session(test_engine) as db:
        _, scene = _project_scene(db)
        db.add(SceneAsset(scene_id=scene.id, asset_type="video", model_used="kling-v3.0-std",
                          file_path="legacy.mp4", metadata_json=None))
        db.flush()
        inventory = build_video_inventory(db)
        assert inventory["wan-3.0"]["history"]["attempts"] == 0
        legacy = inventory["kling-v3.0-std"]["history"]
        assert legacy["attempts"] == 0
        assert legacy["video_assets"] == legacy["active_scenes"] == 1
