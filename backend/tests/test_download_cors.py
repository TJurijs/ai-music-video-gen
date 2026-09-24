"""Downloads must work from either supported loopback frontend address."""

import httpx
import pytest

from app.main import app


@pytest.mark.parametrize("origin,allowed", [
    ("http://127.0.0.1:3000", True),
    ("http://127.0.0.1:3001", True),
    ("http://localhost:3000", True),
    ("http://localhost:3001", True),
    ("https://unrelated.example", False),
])
@pytest.mark.asyncio
async def test_browser_get_receives_cors_permission_only_for_app_origins(origin, allowed):
    # ASGI transport does not enter lifespan or touch the user's database.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8010") as client:
        response = await client.get("/api/health", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == (origin if allowed else None)


@pytest.mark.asyncio
async def test_loopback_download_preflight_allows_range_request():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8010") as client:
        response = await client.options("/storage/5/example.mp4", headers={
            "Origin": "http://127.0.0.1:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Range",
        })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
