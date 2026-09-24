"""Safe, reusable I/O for provider results; paid submissions are never retried."""

import asyncio
import logging
import os
import uuid

import httpx


def log_provider(message: str) -> None:
    """Diagnostic output must never interrupt a paid render on Windows."""
    try:
        logging.getLogger("app.providers").info(message)
    except (OSError, ValueError):
        # Hidden launchers can inherit invalid or already-closed handles.
        pass


class RemoteJobFailedError(RuntimeError):
    """The provider explicitly confirmed a terminal render failure."""


def retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Honor bounded Retry-After delays, otherwise use a short backoff."""
    if response is not None:
        try:
            return min(30.0, max(0.0, float(response.headers["Retry-After"])))
        except (KeyError, ValueError):
            pass
    return min(8.0, float(2 ** attempt))


async def download_result(
    url: str, destination: str, *, headers: dict | None = None,
) -> str:
    """Retry safe GETs and publish only a complete, nonempty download.

    A dropped connection cannot leave partial bytes at the destination or
    replace an existing good file. Each retry starts a fresh temporary file.
    """
    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    temporary = f"{destination}.download-{uuid.uuid4().hex}.tmp"
    try:
        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            for attempt in range(3):
                try:
                    async with client.stream("GET", url, headers=headers or {}) as response:
                        response.raise_for_status()
                        written = 0
                        with open(temporary, "wb") as output:
                            async for chunk in response.aiter_bytes(65536):
                                output.write(chunk)
                                written += len(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                        if not written:
                            raise httpx.ReadError("Provider returned an empty media file")
                    os.replace(temporary, destination)
                    return destination
                except httpx.HTTPStatusError as exc:
                    if attempt == 2 or not (
                        exc.response.status_code in (408, 429)
                        or exc.response.status_code >= 500
                    ):
                        raise
                    await asyncio.sleep(retry_delay(exc.response, attempt))
                except httpx.RequestError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(retry_delay(None, attempt))
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass
