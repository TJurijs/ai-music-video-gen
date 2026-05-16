"""Helpers for converting on-disk storage paths into URLs the frontend can fetch.

All `/storage/*` URLs in API responses go through `to_storage_url`. This is
the single place that knows:
- the public base URL of the backend (from settings.public_base_url)
- how to turn an absolute on-disk path into the `storage_dir`-relative
  path component
- that Windows path separators must become forward slashes for URLs
"""

from __future__ import annotations
import os
from typing import Optional

from app.config import settings


def to_storage_url(file_path: Optional[str], cache_bust: bool = False) -> Optional[str]:
    """Convert an absolute on-disk file path under `settings.storage_dir` into
    a fully-qualified URL the browser can GET.

    Returns None if the path is None or empty. Does NOT verify the file
    exists on disk — that's the caller's responsibility (they often want a
    different fallback if missing).

    `cache_bust=True` appends `?v={file_mtime}` so the URL changes whenever
    the file is rewritten. Use this for files that get MUTATED IN PLACE —
    i.e. same filename, new bytes (like `extracted_last_frame_path`, which
    is always `scene_N_last.jpg` and gets overwritten on every regen of
    that scene's video). Without this, the browser holds the stale old
    bytes because the URL didn't change.

    Don't enable cache_bust for write-once files (videos, stills, audio):
    their filenames already include unique timestamps, so the URL changes
    naturally on replacement, and bypassing the cache for every poll
    request would re-download the whole video.

    >>> # storage_dir = "C:/.../backend/storage", public_base_url = "http://localhost:8010"
    >>> to_storage_url("C:/.../backend/storage/1/videos/scene_5.mp4")
    'http://localhost:8010/storage/1/videos/scene_5.mp4'
    """
    if not file_path:
        return None
    rel = os.path.relpath(file_path, settings.storage_dir).replace("\\", "/")
    base = settings.public_base_url.rstrip("/")
    url = f"{base}/storage/{rel}"
    if cache_bust:
        try:
            mtime = int(os.path.getmtime(file_path))
            url = f"{url}?v={mtime}"
        except OSError:
            # File missing — return URL without buster; consumer will 404.
            pass
    return url
