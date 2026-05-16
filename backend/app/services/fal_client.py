"""fal.ai client — minimal helpers for the one fal endpoint we still use:
fal-ai/whisper (word-level lyric transcription, called from audio_analysis.py).

Audio-conditioned video gen + lipsync paths were removed; this file is now
a thin wrapper around fal's queue API: upload a file, submit a job, poll
until completion. If FAL_API_KEY isn't set, audio_analysis falls back to
OpenRouter transcription (no word-level timestamps).

All fal queue endpoints use the pattern:
  POST   https://queue.fal.run/{model_id}                 -> { request_id }
  GET    https://queue.fal.run/{model_id}/requests/{id}/status
  GET    https://queue.fal.run/{model_id}/requests/{id}    -> final result with file URLs

Storage upload uses two steps: initiate (signed URL) -> PUT bytes.
"""

import asyncio
import os
import httpx
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

async def submit(model_id: str, payload: dict) -> dict:
    """Submit a job to a fal model endpoint. Returns the full response —
    `request_id`, `status_url`, `response_url`, `cancel_url`.

    Why return the whole dict? Models with nested paths like
    `fal-ai/bytedance/seedance-2.0/reference-to-video` have their queue
    polling endpoints under the APP (`fal-ai/bytedance/seedance-2.0`), NOT
    the full route. Constructing the status URL by string-concatenation
    works for simple models (`fal-ai/whisper`) but 405s on the nested ones.
    Always trust the URLs fal returns instead.
    """
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"https://queue.fal.run/{model_id}",
            headers={**_headers(), "Content-Type": "application/json"},
            json=payload,
        )
        if r.status_code >= 400:
            body = r.text[:1500]
            # 422 at submit = schema rejection. fal returns Pydantic-style
            # detail arrays. Surface the body so the caller sees exactly
            # which field name / type is wrong.
            raise RuntimeError(
                f"fal submit failed ({r.status_code}) to {model_id}: {body}"
            )
        return r.json()


async def poll(submission: dict, timeout: int = 900, interval: int = 8) -> dict:
    """Poll a fal submission until completion. `submission` is the dict
    returned by `submit()` — its `status_url` / `response_url` are used
    directly so nested-path models work without URL-construction tricks.

    Returns the final response body (which `extract_video_url()` knows
    how to dig the asset URL out of). On non-2xx from status or response
    endpoints, raises RuntimeError with the actual response body included
    so the actionable error makes it to the user (instead of httpx's
    `Client error '422 Unprocessable Entity'` that hides the JSON detail).
    """
    status_url = submission.get("status_url")
    response_url = submission.get("response_url")
    if not status_url or not response_url:
        raise RuntimeError(
            f"fal submit response missing status_url/response_url: {submission}"
        )
    request_id = submission.get("request_id", "?")

    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=30) as client:
        while asyncio.get_event_loop().time() < deadline:
            status = await client.get(status_url, headers=_headers())
            if status.status_code >= 400:
                body = status.text[:600]
                raise RuntimeError(
                    f"fal status check failed ({status.status_code}) for request "
                    f"{request_id}: {body}"
                )
            sd = status.json()
            state = sd.get("status")

            if state == "COMPLETED":
                result = await client.get(response_url, headers=_headers())
                if result.status_code >= 400:
                    body = result.text[:1500]
                    # 422 here typically means the model failed at inference
                    # time (no face detected, audio too short, etc.) and fal
                    # surfaces the reason in the body's `detail` field.
                    raise RuntimeError(
                        f"fal result fetch failed ({result.status_code}) for request "
                        f"{request_id} — the model accepted the job and reported "
                        f"COMPLETED but the result endpoint refused. "
                        f"Most likely the model errored at inference time (e.g. "
                        f"no face detected in the video, audio too short, audio/video "
                        f"format unsupported). Full response: {body}"
                    )
                return result.json()

            if state in ("FAILED", "CANCELLED"):
                # `sd` typically carries the failure reason — surface it raw.
                raise RuntimeError(f"fal job {state}: {json.dumps(sd)[:600]}")

            await asyncio.sleep(interval)
    raise TimeoutError(f"fal job {request_id} timed out after {timeout}s")


