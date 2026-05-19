"""fal.ai client — covers:
  1) fal-ai/whisper (word-level lyric transcription, audio_analysis.py)
  2) Seedance reference-to-video for the audio-sync route (per-scene opt-in
     when scene.audio_sync_enabled and the video model has supports_audio_input).

The Seedance R2V endpoint accepts character reference images + an audio
clip + a text prompt and renders video where the character "performs" the
audio (lipsynced to the audio when faces are present). It does NOT take a
first_frame — that's the trade-off vs OpenRouter's image-to-video route.
Pricing: ~$0.30/s standard, ~$0.15/s fast at 720p, vs ~$0.05/s on OpenRouter.

Post-process lipsync (LatentSync / MuseTalk / Wav2Lip / OmniHuman) and
the OpenRouter image-to-video path live elsewhere — those are NOT in
this file.

All fal queue endpoints use the pattern:
  POST   https://queue.fal.run/{model_id}                 -> { request_id }
  GET    https://queue.fal.run/{model_id}/requests/{id}/status
  GET    https://queue.fal.run/{model_id}/requests/{id}    -> final result with file URLs

Storage upload uses two steps: initiate (signed URL) -> PUT bytes.
"""

import asyncio
import json
import os
from typing import Optional
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


# ---------------------------------------------------------------------------
# Result shape: pulling the rendered video URL out of fal's response
# ---------------------------------------------------------------------------

def extract_video_url(result: dict) -> Optional[str]:
    """Find the rendered video URL in a fal response.

    Different fal model variants surface the URL at different paths:
      - Most: result["video"]["url"]
      - Some: result["output"][0]["url"]
      - Some: result["video_url"]
    Rather than coding all of them, recursively search for the first
    .mp4 / .mov / .webm URL in the response dict.
    """
    return _find_first_url(result, suffixes=(".mp4", ".mov", ".webm"))


def _find_first_url(obj, suffixes: tuple[str, ...]) -> Optional[str]:
    """Walk a nested dict/list and return the first string value that looks
    like a URL ending in one of `suffixes`. Used by extract_video_url."""
    if isinstance(obj, str):
        if obj.startswith(("http://", "https://")) and any(obj.lower().split("?")[0].endswith(s) for s in suffixes):
            return obj
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            found = _find_first_url(v, suffixes)
            if found:
                return found
        return None
    if isinstance(obj, list):
        for v in obj:
            found = _find_first_url(v, suffixes)
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# Seedance reference-to-video (audio + character refs → video)
# ---------------------------------------------------------------------------
#
# Endpoint shape (verified on fal.ai 2026-05):
#   POST https://queue.fal.run/{model_id}
#   {
#     "prompt": "<text>",
#     "image_urls": ["<character portrait>", ...],   # 1-9
#     "audio_urls": ["<scene audio slice>"],         # up to 3
#     "duration": "<int seconds as string>" | "auto",
#     "resolution": "480p" | "720p" | "1080p" | "auto",
#     "aspect_ratio": "16:9" | "9:16" | ... | "auto",
#     "generate_audio": false,  # we supply our own audio; don't synthesize more
#   }
#
# Path note: the model_id path uses `/fast/` (not `-fast`) for the cheap
# variant. Live slugs:
#   bytedance/seedance-2.0/reference-to-video
#   bytedance/seedance-2.0/fast/reference-to-video
# Collapsing to `seedance-2.0-fast/reference-to-video` 404s with
# "Application 'seedance-2.0-fast' not found".
#
# Audio constraint: audio MUST be shorter than the requested video
# duration. fal 422s with "Audio cannot be longer than the duration of
# the video" otherwise. We trim ~150ms in _extract_audio_segment.
#
# This endpoint does NOT accept a first_frame — that's the trade-off vs
# OpenRouter I2V. Identity anchoring comes from image_urls (~70% weight
# per ByteDance R2V docs), much stronger than the ~30% soft hint refs
# get in I2V mode.

async def submit_seedance_audio_video(
    fal_model_id: str,
    prompt: str,
    image_urls: list[str],
    audio_urls: list[str],
    duration: int,
    resolution: str = "720p",
    aspect_ratio: str = "16:9",
) -> dict:
    """Submit a Seedance R2V job. Returns the submission dict (pass to poll()).

    `fal_model_id` is the FULL queue path
    (e.g. "bytedance/seedance-2.0/reference-to-video") — comes from the
    VIDEO_MODELS entry's `fal_r2v_model_id` field.

    `image_urls` / `audio_urls` are plural by design: Seedance R2V takes
    up to 9 reference images and up to 3 audio clips. We pass one audio
    clip (the scene's window) and one or more character portraits.
    """
    payload = {
        "prompt": prompt,
        "image_urls": image_urls,
        "audio_urls": audio_urls,
        # duration goes as a numeric string per fal's docs (their default
        # "auto" is also a string, so the field is string-typed).
        "duration": str(duration),
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        # We provide audio explicitly — don't let the model add MORE audio
        # on top. The song's audio is muxed verbatim at assembly time.
        "generate_audio": False,
    }
    print(
        f"[fal seedance r2v] submit model={fal_model_id} duration={duration}s "
        f"resolution={resolution} aspect={aspect_ratio} "
        f"image_urls={len(image_urls)} audio_urls={len(audio_urls)} "
        f"prompt[:120]={prompt[:120]!r}"
    )
    return await submit(fal_model_id, payload)


async def download_file(url: str, dest_path: str) -> None:
    """Stream-download a remote file to dest_path. Used for the rendered
    Seedance R2V .mp4. Idempotent: overwrites dest_path."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                async for chunk in r.aiter_bytes(1 << 16):
                    f.write(chunk)


