import asyncio
import json
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

from app.config import settings
from app.models import GenerationJob, Project, Scene, SceneAsset
from app.routers import generation as generation_router
from app.services import fal_client, generation_service, openrouter, provider_io


def _scene(db, **values):
    project = Project(name="Recovery")
    db.add(project)
    db.commit()
    db.refresh(project)
    scene = Scene(
        project_id=project.id, order=1, audio_start=0, audio_end=4,
        **values,
    )
    db.add(scene)
    db.commit()
    db.refresh(scene)
    return scene


def _job(db, scene, provider="openrouter"):
    job = GenerationJob(
        scene_id=scene.id, project_id=scene.project_id, job_type="video",
        provider=provider, status="running", external_id="paid-task",
        request_json=json.dumps({
            "route": "openrouter-video" if provider == "openrouter" else "seedance-r2v",
            "model_key": "seedance-2.0", "duration": 4,
            "submission": {"request_id": "paid-task", "status_url": "https://fal.invalid/status", "response_url": "https://fal.invalid/result"},
        }),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


@pytest.mark.asyncio
async def test_resume_precedes_catalog_inputs_and_force(test_engine, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", "test")
    calls = []

    async def resume(scene, db, job, engine):
        calls.append(job.external_id)

    async def no_image(*_args, **_kwargs):
        raise AssertionError("An existing video job must not generate another image")

    monkeypatch.setattr(generation_service, "_resume_openrouter_video_job", resume)
    monkeypatch.setattr(generation_service, "_generate_image", no_image)
    with Session(test_engine) as db:
        scene = _scene(db, video_model="removed-model", image_model="removed-image", audio_sync_enabled=True)
        job = _job(db, scene)
        report = generation_service.scene_generation_preflight(scene, db, phase="all", force=True)
        assert report["ready"]
        assert report["resuming"]
        assert report["resumable_job_id"] == job.id
        assert report["estimated_cost"] == 0
        assert not report["will_generate_image"]
        await generation_service._run_pipeline(scene, db, test_engine, phase="all", force=True)
        assert scene.status == "done"
    assert calls == ["paid-task"]


@pytest.mark.asyncio
async def test_image_only_ignores_video_catalog(test_engine, monkeypatch):
    calls = []

    async def image(scene, db):
        calls.append(scene.id)

    monkeypatch.setattr(generation_service, "_generate_image", image)
    with Session(test_engine) as db:
        scene = _scene(db, video_model="removed-model")
        await generation_service._run_pipeline(scene, db, test_engine, phase="image")
        assert calls == [scene.id]
        assert scene.status == "image_ready"


@pytest.mark.asyncio
async def test_queued_cancellation_submits_nothing(test_engine, monkeypatch):
    async def no_pipeline(*_args, **_kwargs):
        raise AssertionError("Stopped queued scene must not run")

    monkeypatch.setattr(generation_service, "_run_pipeline", no_pipeline)
    with Session(test_engine) as db:
        scene = _scene(db, generation_run_id="queued", cancel_requested=True)
        scene_id = scene.id
    await generation_service.generate_scene(scene_id, test_engine, run_id="queued")
    with Session(test_engine) as db:
        scene = db.get(Scene, scene_id)
        assert scene.status == "cancelled"
        assert scene.generation_run_id is None
        assert not scene.cancel_requested


@pytest.mark.parametrize("provider", ["openrouter", "fal"])
@pytest.mark.parametrize("failure", ["download", "decode"])
@pytest.mark.asyncio
async def test_result_failure_keeps_paid_job_and_active_video(
    test_engine, tmp_path, monkeypatch, provider, failure,
):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    old_video = tmp_path / "existing.mp4"
    old_video.write_bytes(b"good original")

    async def poll(*_args, **_kwargs):
        return "https://provider.invalid/result.mp4" if provider == "openrouter" else {"video": {"url": "https://provider.invalid/result.mp4"}}

    async def download(url, destination):
        Path(destination).write_bytes(b"partial")
        if failure == "download":
            raise httpx.ReadError("connection lost")

    async def prepare(*_args):
        raise ValueError("undecodable video")

    client = openrouter if provider == "openrouter" else fal_client
    monkeypatch.setattr(client, "poll_video_job" if provider == "openrouter" else "poll", poll)
    monkeypatch.setattr(client, "download_file", download)
    monkeypatch.setattr(generation_service, "_prepare_generated_video", prepare)
    with Session(test_engine) as db:
        scene = _scene(db, video_path=str(old_video))
        job = _job(db, scene, provider)
        scene_id, job_id = scene.id, job.id
    with pytest.raises((httpx.ReadError, ValueError)):
        await generation_service.generate_scene(scene_id, test_engine, phase="video")
    with Session(test_engine) as db:
        job = db.get(GenerationJob, job_id)
        scene = db.get(Scene, scene_id)
        assert job.status == "running"
        assert job.external_id == "paid-task"
        assert job.result_url == "https://provider.invalid/result.mp4"
        assert job.completed_at is None
        assert scene.status == "done"
        assert scene.video_path == str(old_video)
        assert "without a new render charge" in scene.error_message
        assert not db.exec(select(SceneAsset)).all()
    assert old_video.read_bytes() == b"good original"
    assert not list((tmp_path / "1" / "videos").glob("*.mp4"))


@pytest.mark.asyncio
async def test_confirmed_failure_allows_new_attempt(test_engine, monkeypatch):
    async def poll(*_args, **_kwargs):
        raise provider_io.RemoteJobFailedError("provider confirmed failure")

    monkeypatch.setattr(openrouter, "poll_video_job", poll)
    with Session(test_engine) as db:
        scene = _scene(db)
        job = _job(db, scene)
        with pytest.raises(provider_io.RemoteJobFailedError):
            await generation_service._resume_openrouter_video_job(scene, db, job)
        assert job.status == "failed"
        assert job.completed_at
        assert generation_service._find_resumable_video_job(db, scene) is None


@pytest.mark.asyncio
async def test_poll_recovers_rate_limit_without_resubmit(monkeypatch):
    calls = []

    async def status(job_id):
        calls.append(job_id)
        if len(calls) == 1:
            response = httpx.Response(429, request=httpx.Request("GET", "https://example.invalid"), headers={"Retry-After": "0"})
            response.raise_for_status()
        return {"status": "completed", "urls": ["https://provider.invalid/video.mp4"]}

    monkeypatch.setattr(openrouter, "get_video_status", status)
    assert await openrouter.poll_video_job("paid", interval=0) == "https://provider.invalid/video.mp4"
    assert calls == ["paid", "paid"]


@pytest.mark.asyncio
async def test_poll_auth_error_preserves_job(monkeypatch):
    async def status(job_id):
        httpx.Response(401, request=httpx.Request("GET", "https://example.invalid")).raise_for_status()

    monkeypatch.setattr(openrouter, "get_video_status", status)
    with pytest.raises(openrouter.RemoteJobPendingError, match="preserved"):
        await openrouter.poll_video_job("paid", interval=0)


@pytest.mark.asyncio
async def test_download_retries_atomically(tmp_path, monkeypatch):
    destination = tmp_path / "result.mp4"
    destination.write_bytes(b"previous")
    calls = []

    class DroppedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial"
            raise httpx.ReadError("disconnected")

    def handle(request):
        calls.append(request)
        assert destination.read_bytes() == b"previous"
        return httpx.Response(200, stream=DroppedStream()) if len(calls) == 1 else httpx.Response(200, content=b"complete")

    original_client = httpx.AsyncClient
    monkeypatch.setattr(provider_io.httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(handle), **kw))

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(provider_io.asyncio, "sleep", no_sleep)
    await provider_io.download_result("https://provider.invalid/video.mp4", str(destination))
    assert len(calls) == 2
    assert destination.read_bytes() == b"complete"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_download_does_not_send_token_to_lookalike_host(monkeypatch):
    captured = []

    async def download(url, destination, **kwargs):
        captured.append(kwargs["headers"])
        return destination

    monkeypatch.setattr(openrouter, "download_result", download)
    await openrouter.download_file("https://untrusted.invalid/openrouter.ai/api/file", "result.mp4")
    await openrouter.download_file("https://openrouter.ai/api/v1/videos/paid/content", "result.mp4")
    assert captured[0] == {}
    assert "Authorization" in captured[1]


@pytest.mark.asyncio
async def test_batch_failure_does_not_strand_later_scenes(test_engine, monkeypatch):
    monkeypatch.setattr(generation_router, "engine", test_engine)
    calls, running = [], []
    peak = 0

    async def generate(scene_id, *args):
        nonlocal peak
        calls.append(scene_id)
        running.append(scene_id)
        peak = max(peak, len(running))
        await asyncio.sleep(0)
        running.remove(scene_id)
        if scene_id == 1:
            raise RuntimeError("first scene failed")

    monkeypatch.setattr(generation_router, "generate_scene", generate)
    await generation_router._run_generation_batch([(1, "a", None), (2, "b", None), (3, "c", None)], "video", False)
    assert calls == [1, 2, 3]
    assert peak == 2


@pytest.mark.asyncio
async def test_failed_dependency_blocks_chained_submission(test_engine, monkeypatch):
    monkeypatch.setattr(generation_router, "engine", test_engine)
    calls = []
    with Session(test_engine) as db:
        parent = _scene(db)
        child = _scene(db, generation_run_id="child")
        parent_id, child_id = parent.id, child.id

    async def generate(scene_id, *args):
        calls.append(scene_id)
        raise RuntimeError("upstream render failed")

    monkeypatch.setattr(generation_router, "generate_scene", generate)
    await generation_router._run_generation_batch([(parent_id, "parent", None), (child_id, "child", parent_id)], "video", False)
    assert calls == [parent_id]
    with Session(test_engine) as db:
        child = db.get(Scene, child_id)
        assert child.generation_run_id is None
        assert "previous scene" in child.error_message


@pytest.mark.parametrize("provider", ["openrouter", "fal"])
@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.asyncio
async def test_saved_result_works_without_polling_until_url_expires(
    test_engine, tmp_path, monkeypatch, provider, expired,
):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    polls, downloads = [], []

    async def poll(*_args, **_kwargs):
        polls.append(True)
        assert expired, "Saved result should work without the status endpoint"
        return "https://provider.invalid/fresh.mp4" if provider == "openrouter" else {"video_url": "https://provider.invalid/fresh.mp4"}

    async def download(url, destination):
        downloads.append(url)
        if expired and url.endswith("saved.mp4"):
            httpx.Response(403, request=httpx.Request("GET", url)).raise_for_status()
        Path(destination).write_bytes(b"video")

    async def prepare(*args):
        return None

    client = openrouter if provider == "openrouter" else fal_client
    monkeypatch.setattr(client, "poll_video_job" if provider == "openrouter" else "poll", poll)
    monkeypatch.setattr(client, "download_file", download)
    monkeypatch.setattr(generation_service, "_prepare_generated_video", prepare)
    monkeypatch.setattr(generation_service, "_probe_duration", lambda _path: 4)
    with Session(test_engine) as db:
        scene = _scene(db)
        job = _job(db, scene, provider)
        job.result_url = "https://provider.invalid/saved.mp4"
        db.add(job)
        db.commit()
        await generation_service._run_pipeline(scene, db, test_engine, phase="video")
        assert job.status == "completed"
        assert scene.status == "done"
    assert downloads == ["https://provider.invalid/saved.mp4"] + (["https://provider.invalid/fresh.mp4"] if expired else [])
    assert len(polls) == int(expired)


def test_diagnostics_cannot_fail_when_launcher_output_is_invalid(monkeypatch):
    def broken_output(_message):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(provider_io.logging.getLogger("app.providers"), "info", broken_output)
    provider_io.log_provider("render progress")


@pytest.mark.asyncio
async def test_image_payload_uses_structured_aspect_ratio(monkeypatch):
    import base64

    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"images": [{"image_url": {"url": "data:image/png;base64," + base64.b64encode(b"image").decode()}}]}}],
        })

    original_client = httpx.AsyncClient
    monkeypatch.setattr(openrouter.httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(handle), **kw))
    result = await openrouter.generate_image("A wide shot", "gemini-3.1-flash-image", aspect_ratio="16:9")
    assert result.data == b"image"
    assert requests[0]["image_config"] == {"aspect_ratio": "16:9"}


@pytest.mark.asyncio
async def test_fal_local_cancel_during_result_download_preserves_handle(test_engine, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))

    async def interrupted(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(fal_client, "download_file", interrupted)
    with Session(test_engine) as db:
        scene = _scene(db)
        job = _job(db, scene, "fal")
        job.result_url = "https://provider.invalid/saved.mp4"
        db.add(job)
        db.commit()
        with pytest.raises(asyncio.CancelledError):
            await generation_service._resume_fal_video_job(scene, db, job)
        assert job.status == "running"
        assert job.external_id == "paid-task"
        assert job.completed_at is None
        assert generation_service._find_resumable_video_job(db, scene).id == job.id


@pytest.mark.asyncio
async def test_fal_confirmed_cancel_is_terminal(test_engine, monkeypatch):
    async def confirmed(*args, **kwargs):
        raise fal_client.RemoteJobCancelledError()

    monkeypatch.setattr(fal_client, "poll", confirmed)
    with Session(test_engine) as db:
        scene = _scene(db)
        job = _job(db, scene, "fal")
        with pytest.raises(asyncio.CancelledError):
            await generation_service._resume_fal_video_job(scene, db, job)
        assert job.status == "cancelled"
        assert job.completed_at is not None
        assert generation_service._find_resumable_video_job(db, scene) is None


@pytest.mark.asyncio
async def test_resume_batch_does_not_wait_for_changed_chain_parent(test_engine, monkeypatch):
    from fastapi import BackgroundTasks

    def preflight(scene, db, **kwargs):
        return {"ready": True, "scene_id": scene.id, "resuming": scene.chain_from_prev}

    monkeypatch.setattr(generation_router, "scene_generation_preflight", preflight)
    with Session(test_engine) as db:
        parent = _scene(db)
        child = Scene(project_id=parent.project_id, order=2, audio_start=4, audio_end=8, chain_from_prev=True)
        db.add(child)
        db.commit()
        db.refresh(child)
        background = BackgroundTasks()
        await generation_router.trigger_batch_generation(
            generation_router.GenerateBatchRequest(project_id=parent.project_id, phase="video"),
            background, db,
        )
        assert len(background.tasks) == 1
        runs = background.tasks[0].args[0]
        assert [run[0] for run in runs] == [parent.id, child.id]
        assert runs[1][2] is None


@pytest.mark.asyncio
async def test_expired_remote_render_is_terminal(monkeypatch):
    async def expired(job_id):
        return {"status": "expired"}

    monkeypatch.setattr(openrouter, "get_video_status", expired)
    with pytest.raises(provider_io.RemoteJobFailedError, match="expired"):
        await openrouter.poll_video_job("expired-task")


@pytest.mark.asyncio
async def test_fal_retries_result_fetch_when_provider_is_temporarily_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "fal_api_key", "test")
    results = []

    def handle(request):
        if request.url.path == "/status":
            return httpx.Response(200, json={"status": "COMPLETED"})
        results.append(request)
        if len(results) == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"video_url": "https://provider.invalid/finished.mp4"})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(fal_client.httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(handle), **kw))
    result = await fal_client.poll({
        "request_id": "paid", "status_url": "https://fal.invalid/status", "response_url": "https://fal.invalid/result",
    }, interval=0)
    assert result["video_url"].endswith("finished.mp4")
    assert len(results) == 2
