"""fal.ai client — covers file upload, audio-conditioned video generation, and lipsync.

All fal queue endpoints use the pattern:
  POST   https://queue.fal.run/{model_id}                 -> { request_id }
  GET    https://queue.fal.run/{model_id}/requests/{id}/status
  GET    https://queue.fal.run/{model_id}/requests/{id}    -> final result with file URLs

Storage upload uses two steps: initiate (signed URL) -> PUT bytes.
"""

import asyncio
import os
import httpx
from typing import Optional
from app.config import settings


def _headers() -> dict:
    if not settings.fal_api_key:
        raise RuntimeError("FAL_API_KEY is not set")
    return {"Authorization": f"Key {settings.fal_api_key}"}


# ---------------------------------------------------------------------------
# Storage upload
# ---------------------------------------------------------------------------

CONTENT_TYPES = {
    "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
    "mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "m4a": "audio/mp4",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
}


async def upload_file(local_path: str) -> str:
    """Upload a local file to fal.storage and return the public URL."""
    with open(local_path, "rb") as f:
        data = f.read()
    filename = os.path.basename(local_path)
    ext = filename.rsplit(".", 1)[-1].lower()
    content_type = CONTENT_TYPES.get(ext, "application/octet-stream")

    async with httpx.AsyncClient(timeout=300) as client:
        init = await client.post(
            "https://rest.alpha.fal.ai/storage/upload/initiate",
            headers={**_headers(), "Content-Type": "application/json"},
            json={"file_name": filename, "content_type": content_type},
        )
        init.raise_for_status()
        info = init.json()

        put = await client.put(
            info["upload_url"],
            content=data,
            headers={"Content-Type": content_type},
        )
        put.raise_for_status()

    return info["file_url"]


# ---------------------------------------------------------------------------
# Generic queue submit + poll
# ---------------------------------------------------------------------------

async def submit(model_id: str, payload: dict) -> str:
    """Submit a job to a fal model endpoint. Returns request_id."""
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"https://queue.fal.run/{model_id}",
            headers={**_headers(), "Content-Type": "application/json"},
            json=payload,
        )
        r.raise_for_status()
        return r.json()["request_id"]


async def poll(model_id: str, request_id: str, timeout: int = 900, interval: int = 8) -> dict:
    """Poll until completion. Returns the final response body."""
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=30) as client:
        while asyncio.get_event_loop().time() < deadline:
            status = await client.get(
                f"https://queue.fal.run/{model_id}/requests/{request_id}/status",
                headers=_headers(),
            )
            status.raise_for_status()
            sd = status.json()
            state = sd.get("status")

            if state == "COMPLETED":
                # Fetch the result
                result = await client.get(
                    f"https://queue.fal.run/{model_id}/requests/{request_id}",
                    headers=_headers(),
                )
                result.raise_for_status()
                return result.json()

            if state in ("FAILED", "CANCELLED"):
                raise RuntimeError(f"fal job failed: {sd}")

            await asyncio.sleep(interval)
    raise TimeoutError(f"fal job {request_id} timed out after {timeout}s")


def extract_video_url(result: dict) -> Optional[str]:
    """fal models return video URL under various keys; try common ones."""
    if not isinstance(result, dict):
        return None
    # Top-level video field
    for key in ("video", "output_video", "result_video"):
        v = result.get(key)
        if isinstance(v, dict) and "url" in v:
            return v["url"]
        if isinstance(v, str) and v.startswith("http"):
            return v
    # Nested under "output"
    output = result.get("output") or {}
    if isinstance(output, dict):
        for key in ("video", "url"):
            v = output.get(key)
            if isinstance(v, dict) and "url" in v:
                return v["url"]
            if isinstance(v, str) and v.startswith("http"):
                return v
    # First URL-shaped value anywhere in the dict
    return _find_first_url(result)


def _find_first_url(obj) -> Optional[str]:
    if isinstance(obj, str) and obj.startswith("http"):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            url = _find_first_url(v)
            if url:
                return url
    elif isinstance(obj, list):
        for v in obj:
            url = _find_first_url(v)
            if url:
                return url
    return None
