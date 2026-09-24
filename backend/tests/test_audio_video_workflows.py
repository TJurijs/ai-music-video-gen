"""Offline fal contracts, scene routing, saved-job recovery and real media timing.

HTTP requests are intercepted; no authenticated provider call is made.
"""

import json
import subprocess
import wave
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

from app.config import VIDEO_MODELS, settings, video_durations
from app.models import Character, GenerationJob, Project, Scene, SceneAsset, Song
from app.services import fal_client, generation_service as generation, pricing


def make_scene(db, tmp_path, model="ltx-2.5-fast", *, chained=False, duration=15):
    project = Project(name="Offline audio contract", aspect_ratio="16:9")
    db.add(project)
    db.commit()
    still = tmp_path / "still.jpg"
    still.write_bytes(b"planned frame")
    portrait = tmp_path / "portrait.jpg"
    portrait.write_bytes(b"separate portrait must not be submitted")
    song_path = tmp_path / "song.wav"
    song_path.write_bytes(b"mock song")
    db.add(Song(project_id=project.id, title="Track", file_path=str(song_path), duration=30))
    db.add(Character(project_id=project.id, name="Alex", description="Lead performer", reference_image_path=str(portrait)))
    if chained:
        previous = tmp_path / "previous.jpg"
        previous.write_bytes(b"previous final frame")
        db.add(Scene(project_id=project.id, order=1, audio_start=0, audio_end=15,
                     extracted_last_frame_path=str(previous)))
    scene = Scene(
        project_id=project.id, order=2 if chained else 1,
        audio_start=15 if chained else 0, audio_end=(15 if chained else 0) + duration,
        reference_image_path=str(still), video_model=model,
        resolution="1080p" if model == "ltx-2.5-fast" else "720p",
        audio_sync_enabled=model == "wan-2.7", chain_from_prev=chained,
        video_prompt="Alex moves to the song", video_reference_mode="frame",
    )
    db.add(scene)
    db.commit()
    db.refresh(scene)
    return scene


@pytest.fixture
def fal_transport(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    monkeypatch.setattr(settings, "fal_api_key", "offline-test")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    state = {"payloads": [], "uploads": [], "conform": [], "slices": [], "polls": 0}

    def capture(request):
        assert request.method == "POST"
        assert request.url.host == "queue.fal.run"
        state["payloads"].append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={
            "request_id": "saved-render", "status_url": "https://queue.invalid/status",
            "response_url": "https://queue.invalid/result",
        })

    original = httpx.AsyncClient
    monkeypatch.setattr(fal_client.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(capture), **kwargs,
    ))

    async def upload(path):
        state["uploads"].append(Path(path))
        return f"https://media.invalid/{Path(path).name}"

    async def extract(scene, db, max_duration=None, **kwargs):
        state["slices"].append((scene.audio_start, max_duration, kwargs))
        path = tmp_path / "exact.wav"
        path.write_bytes(b"exact audio")
        return str(path)

    async def poll(submission, **kwargs):
        state["polls"] += 1
        if state.get("fail_poll"):
            state["fail_poll"] = False
            raise fal_client.RemoteJobPendingError("Temporary status failure; saved job preserved")
        return {"video": {"url": "https://media.invalid/video.mp4"}}

    async def download(url, destination):
        Path(destination).write_bytes(b"video")

    async def prepare(scene, destination):
        assert Path(destination).exists()

    async def no_image(*args, **kwargs):
        pytest.fail("An existing/chained frame must not regenerate an image")

    monkeypatch.setattr(fal_client, "upload_file", upload)
    monkeypatch.setattr(fal_client, "poll", poll)
    monkeypatch.setattr(fal_client, "download_file", download)
    monkeypatch.setattr(generation, "_extract_audio_segment", extract)
    monkeypatch.setattr(generation, "_probe_duration", lambda path: 15.0)
    monkeypatch.setattr(generation, "_prepare_generated_video", prepare)
    monkeypatch.setattr(generation, "_generate_image", no_image)
    monkeypatch.setattr(generation, "_conform_audio_driven_video_duration", lambda path, duration: state["conform"].append(duration))
    return state


@pytest.mark.parametrize("model,route,cost", [
    ("wan-2.7", "wan-i2v-audio", 1.5),
    ("ltx-2.5-fast", "ltx-a2v", 1.95),
])
@pytest.mark.parametrize("chained", [False, True])
@pytest.mark.asyncio
async def test_song_route_submits_exact_contract_and_frame_and_price(
    test_engine, tmp_path, fal_transport, model, route, cost, chained,
):
    with Session(test_engine) as db:
        scene = make_scene(db, tmp_path, model, chained=chained)
        report = generation.scene_generation_preflight(scene, db, phase="video")
        assert report["ready"], report["errors"]
        assert report["provider"] == "fal"
        assert report["route"] == route
        assert report["estimated_video_cost"] == pytest.approx(cost)
        await generation._run_pipeline(scene, db, test_engine, phase="video")

        path, payload = fal_transport["payloads"][0]
        assert path == "/" + VIDEO_MODELS[model]["fal_audio_model_id"]
        assert payload["audio_url"] == "https://media.invalid/exact.wav"
        assert payload["image_url"].endswith("/previous.jpg" if chained else "/still.jpg")
        assert [p.name for p in fal_transport["uploads"]] == ["exact.wav", "previous.jpg" if chained else "still.jpg"]
        assert fal_transport["slices"] == [(15 if chained else 0, 15, {"lossless": True})]
        if model == "wan-2.7":
            assert payload == {
                "prompt": scene.video_prompt, "image_url": payload["image_url"],
                "audio_url": payload["audio_url"], "duration": 15, "resolution": "720p",
                "enable_prompt_expansion": True, "enable_safety_checker": True,
            }
        else:
            assert payload == {
                "prompt": scene.video_prompt, "image_url": payload["image_url"],
                "audio_url": payload["audio_url"], "aspect_ratio": "16:9",
            }
        job = db.exec(select(GenerationJob)).one()
        asset = db.exec(select(SceneAsset)).one()
        assert job.provider == "fal" and job.status == "completed"
        assert job.cost_usd == asset.cost_usd == pytest.approx(cost)
        assert json.loads(job.request_json)["duration"] == 15
        assert json.loads(asset.metadata_json)["reference_mode"] == "frame"
        assert json.loads(asset.metadata_json)["route"] == route
        assert fal_transport["conform"] == [15]
        assert scene.status == "done"


@pytest.mark.parametrize("model", ["wan-2.7", "ltx-2.5-fast"])
@pytest.mark.asyncio
async def test_recovery_retrieves_saved_route_without_second_charge(
    test_engine, tmp_path, fal_transport, model,
):
    fal_transport["fail_poll"] = True
    with Session(test_engine) as db:
        scene = make_scene(db, tmp_path, model)
        with pytest.raises(fal_client.RemoteJobPendingError):
            await generation._run_pipeline(scene, db, test_engine, phase="video")
        job = db.exec(select(GenerationJob)).one()
        assert job.status == "running" and job.external_id == "saved-render"
        scene.video_model = "removed-model"
        scene.audio_end = 8
        scene.audio_sync_enabled = False
        db.add(scene)
        db.commit()
        report = generation.scene_generation_preflight(scene, db, phase="video", force=True)
        assert report["ready"] and report["resuming"]
        assert report["estimated_cost"] == 0
        await generation._run_pipeline(scene, db, test_engine, phase="video", force=True)
        assert len(fal_transport["payloads"]) == 1
        assert len(fal_transport["uploads"]) == 2
        assert fal_transport["conform"] == [15]
        assert job.status == "completed"
        assert json.loads(db.exec(select(SceneAsset)).one().metadata_json)["reference_mode"] == "frame"


@pytest.mark.parametrize("model,resolution,aspect,duration,reason", [
    ("ltx-2.5-fast", "720p", "16:9", 15, "720p"),
    ("ltx-2.5-fast", "1080p", "1:1", 15, "1:1"),
    ("ltx-2.5-fast", "1080p", "16:9", 21, "21s"),
    ("wan-2.7", "720p", "16:9", 16, "16s"),
])
@pytest.mark.asyncio
async def test_invalid_song_route_options_block_before_upload_or_image(
    test_engine, tmp_path, fal_transport, model, resolution, aspect, duration, reason,
):
    with Session(test_engine) as db:
        scene = make_scene(db, tmp_path, model, duration=duration)
        scene.resolution = resolution
        project = db.get(Project, scene.project_id)
        project.aspect_ratio = aspect
        db.add(project)
        db.commit()
        report = generation.scene_generation_preflight(scene, db, phase="video")
        assert not report["ready"]
        assert any(reason in error for error in report["errors"])
        with pytest.raises(RuntimeError, match=reason):
            await generation._run_pipeline(scene, db, test_engine, phase="video")
        assert not fal_transport["payloads"] and not fal_transport["uploads"]


def test_wan_preserves_independent_openrouter_and_fal_limits():
    assert max(video_durations(VIDEO_MODELS["wan-2.7"])) == 10
    assert max(video_durations(VIDEO_MODELS["wan-2.7"], True)) == 15
    assert max(video_durations(VIDEO_MODELS["ltx-2.5-fast"])) == 20
    assert pricing.video_cost_fal_frame_audio("wan-2.7", 15, "1080p")[0] == 2.25
    assert pricing.video_cost("ltx-2.5-fast", 15, "1080p")[0] == 1.95


@pytest.mark.parametrize("problem,reason", [
    ("prompt", "5,000"), ("frame", "20 MB"), ("key", "FAL_API_KEY"), ("song", "song file"),
])
def test_preflight_explains_wan_contract_input_failures_before_submission(
    test_engine, tmp_path, fal_transport, monkeypatch, problem, reason,
):
    with Session(test_engine) as db:
        scene = make_scene(db, tmp_path, "wan-2.7")
        if problem == "prompt":
            scene.video_prompt = "a" * 5001
        elif problem == "frame":
            with open(scene.reference_image_path, "wb") as stream:
                stream.truncate(20 * 1024 * 1024 + 1)
        elif problem == "key":
            monkeypatch.setattr(settings, "fal_api_key", "")
        else:
            Path(db.exec(select(Song)).one().file_path).unlink()
        report = generation.scene_generation_preflight(scene, db, phase="video")
        assert not report["ready"]
        assert any(reason in error for error in report["errors"])
        assert not fal_transport["payloads"] and not fal_transport["uploads"]


@pytest.mark.asyncio
async def test_exact_wav_preserves_song_tail_and_pads_to_full_scene(test_engine, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    with Session(test_engine) as db:
        scene = make_scene(db, tmp_path)
        song = db.exec(select(Song)).one()
        with wave.open(song.file_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(48000)
            wav.writeframes(b"\x80\x10" * (2 * 48000))
        scene.audio_start, scene.audio_end = 1.5, 16.5
        output = await generation._extract_audio_segment(scene, db, max_duration=15, lossless=True)
        with wave.open(output, "rb") as wav:
            assert wav.getnframes() / wav.getframerate() == 15
            assert any(wav.readframes(12000))
            wav.setpos(48000)
            assert not any(wav.readframes(14 * 48000))
        assert await generation._extract_audio_segment(scene, db, max_duration=15, lossless=True) == output
        # Replacing song bytes invalidates cached slices even with identical windows.
        Path(song.file_path).touch()
        assert await generation._extract_audio_segment(scene, db, max_duration=15, lossless=True) != output


@pytest.mark.parametrize("frames,expected,accepted", [(49, 2, True), (47, 2, True), (36, 2, False), (60, 2, False), (353, 15, True), (351, 15, False)])
def test_actual_video_frame_rounding_and_short_render_rejection(tmp_path, monkeypatch, frames, expected, accepted):
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path))
    video = tmp_path / "provider.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=24",
        "-frames:v", str(frames), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video),
    ], check=True, capture_output=True)
    if accepted:
        generation._conform_audio_driven_video_duration(str(video), expected)
        assert generation._probe_duration(str(video)) == pytest.approx(expected, abs=0.005)
    else:
        original = video.read_bytes()
        with pytest.raises(RuntimeError, match="not activated"):
            generation._conform_audio_driven_video_duration(str(video), expected)
        assert video.read_bytes() == original
