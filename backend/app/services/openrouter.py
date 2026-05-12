"""OpenRouter API client — covers video, image, audio, music, and LLM calls."""

import asyncio
import base64
import os
import httpx
from typing import Optional
from app.config import settings, OPENROUTER_BASE


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "Music Video Studio",
    }


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

async def chat(messages: list, model: Optional[str] = None, json_mode: bool = False, timeout: int = 300) -> str:
    """Send a chat completion. Default timeout is 300s — large scene plans
    can produce 50KB+ JSON responses that take 60-120s on slower models."""
    model = model or settings.default_llm_model
    payload: dict = {"model": model, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers=_headers(),
            json=payload,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"OpenRouter chat {r.status_code}: {r.text[:600]}")
        return r.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Image generation  (chat completions with image modality)
# ---------------------------------------------------------------------------

async def generate_image(
    prompt: str,
    model: Optional[str] = None,
    reference_image_paths: Optional[list[str]] = None,
    aspect_ratio: Optional[str] = None,
    _retry_count: int = 0,
) -> bytes:
    """Returns raw image bytes.

    If reference_image_paths is provided, they're sent as multi-image input
    so the model preserves character/style identity. Only useful with image
    models that support multi-image conditioning (e.g. Gemini 2.5 Flash Image).

    aspect_ratio (e.g. "16:9", "9:16", "1:1") is appended to the prompt as
    an explicit instruction. Image models on OpenRouter don't take aspect
    ratio as a structured parameter, but they reliably honor prompt-level
    cues when they're emphatic.

    Retry-on-text: Gemini 2.5 Flash Image sometimes returns a text description
    instead of an image (typical when the prompt mentions a real person's name
    and trips the likeness filter — the model "describes" the portrait but
    refuses to render). When that happens we retry once with a stricter
    "OUTPUT IMAGE ONLY" prefix to try to break the conversational fallback.
    """
    from app.config import IMAGE_MODELS
    model_key = model or settings.default_image_model
    model_id = IMAGE_MODELS.get(model_key, {}).get("model_id", "openai/gpt-image-1")

    # Inject aspect ratio cue if provided. Doubled emphasis because some
    # image models otherwise default to 1:1 regardless of prompt content.
    if aspect_ratio:
        prompt = (
            f"{prompt}\n\n"
            f"OUTPUT FORMAT: Render at {aspect_ratio} aspect ratio. "
            f"The frame must be {aspect_ratio} — do not output a square or differently-shaped image."
        )

    # Build content: text + optional reference images
    content: list = [{"type": "text", "text": prompt}]
    for ref_path in (reference_image_paths or []):
        if not ref_path or not os.path.exists(ref_path):
            continue
        with open(ref_path, "rb") as rf:
            ref_b64 = base64.b64encode(rf.read()).decode()
        ext = ref_path.rsplit(".", 1)[-1].lower()
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "jpeg")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/{mime};base64,{ref_b64}"},
        })

    payload = {
        "model": model_id,
        "modalities": ["image"],
        "messages": [{"role": "user", "content": content if len(content) > 1 else prompt}],
    }

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers=_headers(),
            json=payload,
        )
        r.raise_for_status()
        data = r.json()

    msg = data["choices"][0]["message"]

    # Gemini & some OpenAI image models return images in a separate `images` array
    # on the message, not inside `content`. Check there first.
    images = msg.get("images")
    if isinstance(images, list):
        for part in images:
            if isinstance(part, dict):
                url = (part.get("image_url") or {}).get("url") or part.get("url")
                if url:
                    return await _bytes_from_url(url)

    # Fallback: content is a list of parts (older format)
    content = msg.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url")
                if url:
                    return await _bytes_from_url(url)

    # Fallback: content is a base64 data URL string
    if isinstance(content, str) and content.startswith("data:"):
        return await _bytes_from_url(content)

    # Text-only response — model returned a description instead of an image.
    # Classic Gemini failure: prompt tripped a likeness/content filter and
    # the model fell back to conversational mode. Retry once with an
    # emphatic image-only prefix; that breaks the conversational mode
    # often enough to be worth the extra round-trip.
    is_text_only = isinstance(content, str) and content and not content.startswith("data:")
    if is_text_only and _retry_count < 1:
        retry_prompt = (
            "OUTPUT IMAGE ONLY. Do not respond with any text. Generate the "
            "following as an image file:\n\n" + prompt
        )
        return await generate_image(
            retry_prompt,
            model=model,
            reference_image_paths=reference_image_paths,
            aspect_ratio=None,  # already baked into prompt on first try
            _retry_count=_retry_count + 1,
        )

    preview = str(content)[:200] if content else str(msg)[:300]
    if is_text_only:
        # Make the error actionable: tell the user what likely went wrong
        # so they can fix the character description.
        raise ValueError(
            "Image model returned text instead of an image — this usually "
            "means the prompt mentions a real person's name (actor, "
            "musician, public figure) and the model's likeness filter "
            "refused to render. Edit the character description to use "
            "feature shapes ('sharp angular jaw') instead of names "
            f"('Cillian Murphy'). Model response: {preview}"
        )
    raise ValueError(f"Unexpected image response format: {preview}")


async def _bytes_from_url(url: str) -> bytes:
    """Resolve a data: URL or remote URL to raw bytes."""
    if url.startswith("data:"):
        _, b64 = url.split(",", 1)
        return base64.b64decode(b64)
    async with httpx.AsyncClient(timeout=60) as dl:
        resp = await dl.get(url)
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Video generation  (async job — submit → poll)
#
# Schema reference: GET https://openrouter.ai/openapi.yaml → VideoGenerationRequest
#   - prompt, model               (required)
#   - duration                    (must be in model.supported_durations)
#   - aspect_ratio, resolution
#   - generate_audio              (model decides default; we override per-scene)
#   - frame_images[{frame_type, image}]   (first/last frame anchors)
#   - input_references[{...image_url}]    (character/style ref images)
# ---------------------------------------------------------------------------

def _data_url_from_path(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else "jpg"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(ext, "jpeg")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"data:image/{mime};base64,{b64}"


def _is_image_filter_error(error_text: str) -> bool:
    """Detect provider image-content / likeness rejections so we can degrade
    the payload and retry. Different models surface this with different
    error codes:
      - Seedance: 'InputImageSensitiveContentDetected.PrivacyInformation' /
        'input image may contain real person'
      - Kling: 'image contains sensitive content' (varies)
      - Veo: usually accepts photoreal portraits; if it rejects, message
        mentions 'safety' or 'personGeneration'
    Substring-matching against a few common signals keeps this resilient
    to error-message wording drift.
    """
    t = (error_text or "").lower()
    return any(
        sig in t for sig in (
            "inputimagesensitivecontentdetected",
            "privacyinformation",
            "real person",
            "real-person",
            "image may contain",
            "image contains sensitive",
            "persongeneration",
            "person generation",
            "celebrity",
            "likeness",
        )
    )


async def submit_video_job(
    prompt: str,
    model_id: str,
    duration: int = 8,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
    generate_audio: bool = False,
    first_frame_path: Optional[str] = None,
    last_frame_path: Optional[str] = None,
    reference_image_paths: Optional[list[str]] = None,
    _retry_count: int = 0,
) -> str:
    """Submit a video generation job. Returns the job ID for polling.

    Auto-degrade on image-content filter rejection:
      Attempt 1 — first_frame + last_frame + character refs (full identity)
      Attempt 2 — drop character refs (keeps scene composition, loses
                  identity anchor — model improvises character looks)
      Attempt 3 — also drop frame images (pure text-to-video — last resort,
                  loses BOTH composition and identity)
    Final error surfaces the actual filter signal + suggests a model switch
    (Veo and Kling accept photoreal portraits where Seedance refuses).
    """
    payload: dict = {
        "model": model_id,
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "generate_audio": generate_audio,
    }

    # Veo accepts a personGeneration passthrough that controls its real-person
    # filter (default = dont_allow). Opt into allow_adult so photoreal
    # portraits / first-frames featuring people don't get rejected. Other
    # providers ignore unknown fields.
    if model_id.startswith("google/veo"):
        payload["personGeneration"] = "allow_adult"

    # FrameImage extends ContentPartImage: needs type="image_url" + image_url{url}
    # and adds frame_type alongside.
    frame_images: list = []
    if first_frame_path and os.path.exists(first_frame_path):
        frame_images.append({
            "type": "image_url",
            "image_url": {"url": _data_url_from_path(first_frame_path)},
            "frame_type": "first_frame",
        })
    if last_frame_path and os.path.exists(last_frame_path):
        frame_images.append({
            "type": "image_url",
            "image_url": {"url": _data_url_from_path(last_frame_path)},
            "frame_type": "last_frame",
        })
    if frame_images:
        payload["frame_images"] = frame_images

    refs: list = []
    for ref in (reference_image_paths or []):
        if ref and os.path.exists(ref):
            refs.append({
                "type": "image_url",
                "image_url": {"url": _data_url_from_path(ref)},
            })
    if refs:
        payload["input_references"] = refs

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}/videos",
            headers=_headers(),
            json=payload,
        )
        if r.status_code >= 400:
            error_text = r.text or ""

            # Image-content filter: degrade payload and retry.
            if _is_image_filter_error(error_text) and _retry_count < 2:
                # Attempt 2: drop character refs but keep first_frame.
                if _retry_count == 0 and (reference_image_paths or []):
                    print(
                        f"[video] {model_id} image filter triggered with "
                        f"{len(refs)} char refs + {len(frame_images)} frames — "
                        f"retrying without character refs (scene composition preserved, "
                        f"identity will be model's guess)"
                    )
                    return await submit_video_job(
                        prompt=prompt, model_id=model_id, duration=duration,
                        aspect_ratio=aspect_ratio, resolution=resolution,
                        generate_audio=generate_audio,
                        first_frame_path=first_frame_path,
                        last_frame_path=last_frame_path,
                        reference_image_paths=None,
                        _retry_count=_retry_count + 1,
                    )
                # Attempt 3: also drop frame images (pure text-to-video).
                if _retry_count == 1 and (first_frame_path or last_frame_path):
                    print(
                        f"[video] {model_id} still rejecting images — "
                        f"retrying as text-to-video (no first_frame, no refs)"
                    )
                    return await submit_video_job(
                        prompt=prompt, model_id=model_id, duration=duration,
                        aspect_ratio=aspect_ratio, resolution=resolution,
                        generate_audio=generate_audio,
                        first_frame_path=None, last_frame_path=None,
                        reference_image_paths=None,
                        _retry_count=_retry_count + 1,
                    )

            # Make filter errors actionable.
            if _is_image_filter_error(error_text):
                suggestion = (
                    "Try a different video model — "
                    "Veo 3.1 Lite or Kling 3.0 Pro accept photoreal portraits "
                    "where Seedance refuses them. (Seedance has stricter image-input "
                    "filters than text-prompt filters — different from Gemini's behaviour.)"
                )
                raise RuntimeError(
                    f"OpenRouter video submit {r.status_code}: image content filter "
                    f"refused after {_retry_count + 1} attempt(s) (degrading the payload "
                    f"didn't help). Model: {model_id}. {suggestion} "
                    f"Raw provider error: {error_text[:400]}"
                )
            # Other 4xx/5xx — surface as before.
            raise RuntimeError(
                f"OpenRouter video submit {r.status_code}: {error_text[:600]} "
                f"(model={model_id}, duration={duration}, resolution={resolution}, "
                f"aspect={aspect_ratio}, refs={len(refs)}, frames={len(frame_images)})"
            )
        return r.json()["id"]


async def get_video_status(job_id: str) -> dict:
    """One-shot poll: returns the raw video job status dict from OpenRouter."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{OPENROUTER_BASE}/videos/{job_id}",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()


async def poll_video_job(
    job_id: str,
    timeout: int = 600,
    interval: int = 15,
    is_cancelled = None,
) -> str:
    """Poll until video is ready; returns the download URL.

    is_cancelled is an optional sync callable returning True to abort polling
    (e.g. user pressed Stop). When triggered we raise asyncio.CancelledError so
    the surrounding pipeline can mark the scene cancelled.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if is_cancelled is not None and is_cancelled():
            raise asyncio.CancelledError(f"Video job {job_id} cancelled by user")
        data = await get_video_status(job_id)
        status = data.get("status")
        if status == "completed":
            urls = data.get("unsigned_urls") or data.get("urls") or []
            if urls:
                return urls[0]
            raise ValueError("Job completed but no URLs returned")
        if status in ("failed", "error"):
            raise RuntimeError(f"Video job failed: {data.get('error', 'unknown')}")
        await asyncio.sleep(interval)
    raise TimeoutError(f"Video job {job_id} timed out after {timeout}s")


# ---------------------------------------------------------------------------
# Audio transcription  (word-level timestamps via Whisper)
# ---------------------------------------------------------------------------

async def transcribe_audio(audio_path: str, model: str = "google/gemini-2.5-flash") -> dict:
    """Transcribe audio via OpenRouter chat completions (audio input modality).

    OpenRouter does NOT host a Whisper-style transcription endpoint. Audio is
    sent as a base64 part in a chat message, and the response is plain text
    lyrics (no word-level timestamps). For timing we rely on librosa beats +
    section boundaries, not per-word timestamps.

    Returns {"text": "...", "segments": [], "words": []} for compatibility
    with the previous shape.
    """
    with open(audio_path, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode()

    filename = os.path.basename(audio_path)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp3"
    fmt = {"m4a": "m4a", "mp3": "mp3", "wav": "wav", "ogg": "ogg",
           "flac": "flac", "aac": "aac"}.get(ext, "mp3")

    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text":
                    "Transcribe this song's lyrics exactly as sung. "
                    "Output ONLY the lyrics, line by line, no commentary, no timestamps. "
                    "If the song is instrumental or you cannot make out lyrics, output the single word: instrumental"},
                {"type": "input_audio", "input_audio": {"data": audio_b64, "format": fmt}},
            ],
        }],
    }

    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers={**_headers(), "Content-Type": "application/json"},
            json=payload,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Transcription HTTP {r.status_code}: {r.text[:600]}")
        data = r.json()

    text = data["choices"][0]["message"]["content"]
    if isinstance(text, list):
        # Some providers return content as parts
        text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))

    text = (text or "").strip()
    if text.lower() in ("instrumental", "(instrumental)", "[instrumental]"):
        text = ""

    return {"text": text, "segments": [], "words": []}


# ---------------------------------------------------------------------------
# Music generation  (Google Lyria via OpenRouter)
# ---------------------------------------------------------------------------

async def generate_music_lyria(
    description: str,
    duration: str = "full",  # "full" = Lyria 3 Pro, "clip" = Lyria 3 Clip
) -> str:
    """Generate music with Lyria; returns a URL to the audio file."""
    model_id = "google/lyria-3-pro" if duration == "full" else "google/lyria-3-clip"

    # Lyria uses the audio generation endpoint (similar to TTS but for music)
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": description}],
        "modalities": ["audio"],
    }

    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers=_headers(),
            json=payload,
        )
        r.raise_for_status()
        data = r.json()

    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "audio_url":
                return part["audio_url"]["url"]
    if isinstance(content, str):
        return content

    raise ValueError(f"Unexpected Lyria response: {str(content)[:200]}")


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------

async def download_file(url: str, dest_path: str) -> str:
    """Download a URL to a local path; returns the path.

    OpenRouter's video content endpoint (/api/v1/videos/{id}/content) requires
    auth even though the response calls them "unsigned_urls". Send the bearer
    token whenever the URL is on the OpenRouter API host.
    """
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    headers = _headers() if "openrouter.ai/api" in url else {}
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                async for chunk in r.aiter_bytes(65536):
                    f.write(chunk)
    return dest_path
