"""Suno music generation client — sunoapi.org wrapper.

The original `sunoaiapi.com` (EvoLink) endpoint is dead. This uses
sunoapi.org which has a working REST API + polling endpoint.

Auth: Authorization: Bearer <key>
Generate: POST /api/v1/generate  → returns { taskId }
Poll:     GET  /api/v1/generate/record-info?taskId=...
          status transitions: PENDING → TEXT_SUCCESS → FIRST_SUCCESS → SUCCESS
"""

import asyncio
import httpx
from app.config import settings


class SunoError(Exception):
    pass


# A dummy callback URL — the API requires the field but we ignore the callback
# and poll the record-info endpoint instead.
_DUMMY_CALLBACK = "https://example.com/suno-callback-noop"


async def generate_song(
    description: str,
    title: str = "",
    style_tags: str = "",
    lyrics: str = "",
    instrumental: bool = False,
    model: str = "V5_5",
) -> dict:
    """Generate a song via sunoapi.org. Returns {id, audio_url, title, duration}."""
    if not settings.suno_api_key:
        raise SunoError("SUNO_API_KEY is not set")

    base = settings.suno_api_base.rstrip("/")

    # Choose custom vs non-custom mode based on whether the user supplied
    # specific style/title/lyrics
    custom = bool(title or style_tags or lyrics)

    if custom:
        payload = {
            "customMode": True,
            "instrumental": instrumental,
            "model": model,
            "callBackUrl": _DUMMY_CALLBACK,
            "title": (title or description[:80])[:80],
            "style": (style_tags or description)[:200],
        }
        if not instrumental and lyrics:
            payload["prompt"] = lyrics[:3000]
    else:
        payload = {
            "customMode": False,
            "instrumental": instrumental,
            "model": model,
            "callBackUrl": _DUMMY_CALLBACK,
            "prompt": description[:500],
        }

    headers = {
        "Authorization": f"Bearer {settings.suno_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{base}/api/v1/generate", headers=headers, json=payload)
        if r.status_code >= 400:
            raise SunoError(f"Suno generate HTTP {r.status_code}: {r.text[:400]}")
        body = r.json()

    if body.get("code") != 200:
        raise SunoError(f"Suno generate failed: {body}")

    task_id = body.get("data", {}).get("taskId")
    if not task_id:
        raise SunoError(f"No taskId in response: {body}")

    return await _poll_until_ready(task_id, headers, base)


async def _poll_until_ready(
    task_id: str,
    headers: dict,
    base: str,
    timeout: int = 360,
    interval: int = 12,
) -> dict:
    """Poll /generate/record-info until status SUCCESS (or first audio available)."""
    deadline = asyncio.get_event_loop().time() + timeout

    async with httpx.AsyncClient(timeout=30) as client:
        while asyncio.get_event_loop().time() < deadline:
            r = await client.get(
                f"{base}/api/v1/generate/record-info",
                headers=headers,
                params={"taskId": task_id},
            )
            if r.status_code >= 400:
                raise SunoError(f"Suno poll HTTP {r.status_code}: {r.text[:400]}")

            data = r.json().get("data") or {}
            status = data.get("status", "")

            # FIRST_SUCCESS or SUCCESS both indicate at least one usable track
            if status in ("FIRST_SUCCESS", "SUCCESS"):
                tracks = (data.get("response") or {}).get("sunoData") or []
                if tracks:
                    track = tracks[0]
                    audio_url = track.get("audioUrl") or track.get("streamAudioUrl")
                    if audio_url:
                        return {
                            "id": track.get("id", task_id),
                            "audio_url": audio_url,
                            "title": track.get("title", ""),
                            "duration": track.get("duration"),
                            "lyrics": track.get("prompt") or track.get("lyrics", ""),
                            "image_url": track.get("imageUrl"),
                        }

            if status in ("FAILED", "ERROR", "SENSITIVE_WORD_ERROR"):
                raise SunoError(f"Suno generation failed: {data}")

            await asyncio.sleep(interval)

    raise TimeoutError(f"Suno task {task_id} timed out after {timeout}s")
