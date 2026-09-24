"""fal.ai client — covers:
  1) fal-ai/whisper (word-level lyric transcription, audio_analysis.py)
  2) Seedance reference-to-video for the audio-sync route (per-scene opt-in
     when scene.audio_sync_enabled and the video model has supports_audio_input).
  3) Wan 2.7 and LTX 2.5 Fast audio-driven first-frame video.

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
from app.services.provider_io import log_provider, RemoteJobFailedError, download_result, retry_delay


class RemoteJobPendingError(RuntimeError):
    """The local poll failed, but the paid fal job may still be running."""


class RemoteJobCancelledError(asyncio.CancelledError):
    """fal confirmed the upstream cancellation, so the handle is terminal."""


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


async def cancel_submission(submission: dict) -> bool:
    """Cancel through fal's queue-provided URL and report confirmation."""
    cancel_url = submission.get("cancel_url")
    if not cancel_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.put(cancel_url, headers=_headers())
            if response.status_code < 400:
                return True
            if response.status_code >= 400:
                log_provider(
                    f"[fal cancel] upstream returned {response.status_code}: "
                    f"{response.text[:300]}"
                )
    except httpx.RequestError as exc:
        log_provider(f"[fal cancel] request failed: {type(exc).__name__}: {str(exc)[:200]}")
    return False


async def poll(
    submission: dict,
    timeout: int = 900,
    interval: int = 8,
    is_cancelled=None,
) -> dict:
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
    consecutive_network_failures = 0
    result_failures = 0
    async with httpx.AsyncClient(timeout=30) as client:
        while asyncio.get_event_loop().time() < deadline:
            if is_cancelled is not None and is_cancelled():
                if await cancel_submission(submission):
                    raise RemoteJobCancelledError(
                        f"fal job {request_id} cancelled by user"
                    )
                raise RemoteJobPendingError(
                    f"Cancellation could not be confirmed for fal request "
                    f"{request_id}. The provider handle was preserved; retry "
                    "the scene to check or resume it without another submission."
                )
            try:
                status = await client.get(status_url, headers=_headers())
            except httpx.RequestError as exc:
                consecutive_network_failures += 1
                if consecutive_network_failures >= 4:
                    raise RemoteJobPendingError(
                        f"fal polling lost contact with request {request_id}; "
                        "the remote job is preserved and can be resumed by retrying."
                    ) from exc
                await asyncio.sleep(interval)
                continue
            if status.status_code in (408, 429) or status.status_code >= 500:
                consecutive_network_failures += 1
                if consecutive_network_failures >= 4:
                    raise RemoteJobPendingError(
                        f"fal status endpoint repeatedly returned HTTP "
                        f"{status.status_code} for request {request_id}; the "
                        "remote job was preserved and can be resumed."
                    )
                await asyncio.sleep(max(interval, retry_delay(status, consecutive_network_failures - 1)))
                continue
            if status.status_code >= 400:
                body = status.text[:600]
                raise RemoteJobPendingError(
                    f"fal status check failed ({status.status_code}) for request "
                    f"{request_id}: {body}"
                )
            consecutive_network_failures = 0
            try:
                sd = status.json()
            except ValueError as exc:
                raise RemoteJobPendingError(
                    f"fal returned malformed status JSON for request {request_id}; "
                    "the remote job was preserved and can be resumed."
                ) from exc
            if not isinstance(sd, dict):
                raise RemoteJobPendingError(
                    f"fal returned invalid status data for {request_id}; the saved job can be resumed."
                )
            state = sd.get("status")

            if state == "COMPLETED":
                try:
                    result = await client.get(response_url, headers=_headers())
                except httpx.RequestError as exc:
                    result_failures += 1
                    if result_failures < 4:
                        await asyncio.sleep(interval)
                        continue
                    raise RemoteJobPendingError(
                        f"fal completed request {request_id}, but the result "
                        "download endpoint is temporarily unreachable. Retry "
                        "to fetch the same result."
                    ) from exc
                if result.status_code in (408, 429) or result.status_code >= 500:
                    result_failures += 1
                    if result_failures < 4:
                        await asyncio.sleep(max(interval, retry_delay(result, result_failures - 1)))
                        continue
                    raise RemoteJobPendingError(
                        f"fal completed request {request_id}, but result fetch "
                        f"returned HTTP {result.status_code}. Retry to fetch "
                        "the same result."
                    )
                if result.status_code >= 400:
                    body = result.text[:1500]
                    # 422 here typically means the model failed at inference
                    # time (no face detected, audio too short, etc.) and fal
                    # surfaces the reason in the body's `detail` field.
                    error_type = RemoteJobFailedError if result.status_code == 422 else RemoteJobPendingError
                    raise error_type(
                        f"fal result fetch failed ({result.status_code}) for request "
                        f"{request_id} — the model accepted the job and reported "
                        f"COMPLETED but the result endpoint refused. "
                        f"Most likely the model errored at inference time (e.g. "
                        f"no face detected in the video, audio too short, audio/video "
                        f"format unsupported). Full response: {body}"
                    )
                try:
                    payload = result.json()
                except ValueError as exc:
                    raise RemoteJobPendingError(
                        f"fal result JSON for {request_id} was unreadable; resume to fetch the same render."
                    ) from exc
                if not isinstance(payload, dict):
                    raise RemoteJobPendingError(
                        f"fal returned invalid result data for {request_id}; the saved job can be resumed."
                    )
                return payload

            if state in ("FAILED", "CANCELLED"):
                # `sd` typically carries the failure reason — surface it raw.
                raise RemoteJobFailedError(f"fal job {state}: {json.dumps(sd)[:600]}")

            await asyncio.sleep(interval)
    raise RemoteJobPendingError(
        f"fal job {request_id} exceeded the local {timeout}s polling window; "
        "the remote job is preserved and can be resumed by retrying."
    )


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
    log_provider(
        f"[fal seedance r2v] submit model={fal_model_id} duration={duration}s "
        f"resolution={resolution} aspect={aspect_ratio} "
        f"image_urls={len(image_urls)} audio_urls={len(audio_urls)} "
        f"prompt[:120]={prompt[:120]!r}"
    )
    return await submit(fal_model_id, payload)


async def submit_wan_audio_video(
    fal_model_id: str,
    prompt: str,
    image_url: str,
    audio_url: str,
    duration: int,
    resolution: str = "720p",
) -> dict:
    """Submit Wan 2.7 I2V with an exact first frame and driving audio."""
    payload = {
        "prompt": prompt,
        "image_url": image_url,
        "audio_url": audio_url,
        "duration": duration,
        "resolution": resolution,
        "enable_prompt_expansion": True,
        "enable_safety_checker": True,
    }
    log_provider(
        f"[fal wan i2v audio] submit model={fal_model_id} duration={duration}s "
        f"resolution={resolution} image_url=yes audio_url=yes "
        f"prompt[:120]={prompt[:120]!r}"
    )
    return await submit(fal_model_id, payload)


async def submit_wan_reference_audio_video(
    fal_model_id: str,
    prompt: str,
    image_urls: list[str],
    audio_urls: list[str],
    duration: int,
    resolution: str = "720p",
    aspect_ratio: str = "16:9",
) -> dict:
    """Wan 3 R2V: reference media, without first-frame conditioning.

    Audio length is validated from the local WAV before uploading. The
    Keep the documented audiovisual output default; reference-audio input
    is separate and assembly still uses the original song.
    """
    if not 1 <= len(image_urls) <= 10:
        raise ValueError("Wan 3.0 accepts 1–10 reference images in this app.")
    if not 1 <= len(audio_urls) <= 5:
        raise ValueError("Wan 3.0 accepts 1–5 audio references totaling at most 15 seconds.")
    if type(duration) is not int or not 2 <= duration <= 15:
        raise ValueError("Wan 3.0 song reference requires an integer scene length from 2 to 15 seconds.")
    if resolution not in ("480p", "720p", "1080p"):
        raise ValueError("Wan 3.0 song reference requires 480p, 720p or 1080p.")
    if aspect_ratio not in ("16:9", "4:3", "1:1", "3:4", "9:16"):
        raise ValueError("Wan 3.0 song reference does not support this aspect ratio.")
    return await submit(fal_model_id, {
        "prompt": prompt,
        "reference_image_urls": image_urls,
        "reference_audio_urls": audio_urls,
        "duration": duration,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "audio": True,
        "enable_prompt_expansion": True,
        "enable_safety_checker": True,
    })


async def submit_ltx_audio_video(
    fal_model_id: str,
    prompt: str,
    image_url: str,
    audio_url: str,
    aspect_ratio: str = "16:9",
) -> dict:
    """LTX 2.5 Fast A2V: duration follows audio; output is fixed at 1080p.

    Unlike LTX's I2V schema, this endpoint does not accept duration,
    resolution, fps, end_image_url or generate_audio fields.
    """
    return await submit(fal_model_id, {
        "prompt": prompt,
        "image_url": image_url,
        "audio_url": audio_url,
        "aspect_ratio": aspect_ratio,
    })


async def download_file(url: str, dest_path: str) -> None:
    """Stream-download a remote file to dest_path. Used for the rendered
    Seedance R2V .mp4. Idempotent: overwrites dest_path."""
    await download_result(url, dest_path)


