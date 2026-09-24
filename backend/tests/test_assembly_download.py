import httpx
import pytest
from fastapi import FastAPI
from sqlmodel import Session

from app.config import settings
from app.database import get_session
from app.models import GenerationJob, Project
from app.routers.generation import router


@pytest.mark.parametrize("state,status", [
    ("completed", 200), ("running", 409), ("missing", 404), ("outside", 404),
])
@pytest.mark.asyncio
async def test_download_streams_only_a_saved_completed_export(test_engine, tmp_path, monkeypatch, state, status):
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(settings, "storage_dir", str(storage))
    path = (tmp_path if state == "outside" else storage) / "final.mp4"
    content = b"saved-video-bytes"
    if state != "missing":
        path.write_bytes(content)
    with Session(test_engine) as db:
        project = Project(name="My video")
        db.add(project)
        db.commit()
        project_id = project.id
        db.add(GenerationJob(project_id=project_id, job_type="assembly", provider="ffmpeg",
                             status="running" if state == "running" else "completed", result_path=str(path)))
        db.commit()

    app = FastAPI()
    app.include_router(router, prefix="/api/generation")

    def session():
        with Session(test_engine) as db:
            yield db

    app.dependency_overrides[get_session] = session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        url = f"/api/generation/assemble/{project_id}/download"
        response = await client.get(url)
        assert response.status_code == status
        if status == 200:
            assert response.content == content
            assert response.headers["content-type"] == "video/mp4"
            assert response.headers["content-disposition"].startswith("attachment;")
            assert "My%20video.mp4" in response.headers["content-disposition"]
            assert response.headers["cache-control"] == "no-store"
            partial = await client.get(url, headers={"Range": "bytes=0-4"})
            assert partial.status_code == 206
            assert partial.content == content[:5]
