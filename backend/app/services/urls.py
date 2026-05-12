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


def to_storage_url(file_path: Optional[str]) -> Optional[str]:
    """Convert an absolute on-disk file path under `settings.storage_dir` into
    a fully-qualified URL the browser can GET.

    Returns None if the path is None or empty. Does NOT verify the file
    exists on disk — that's the caller's responsibility (they often want a
    different fallback if missing).

    >>> # storage_dir = "C:/.../backend/storage", public_base_url = "http://localhost:8010"
    >>> to_storage_url("C:/.../backend/storage/1/videos/scene_5.mp4")
    'http://localhost:8010/storage/1/videos/scene_5.mp4'
    """
    if not file_path:
        return None
    rel = os.path.relpath(file_path, settings.storage_dir).replace("\\", "/")
    base = settings.public_base_url.rstrip("/")
    return f"{base}/storage/{rel}"
