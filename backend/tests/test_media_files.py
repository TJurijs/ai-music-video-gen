from io import BytesIO
from pathlib import Path

import pytest
from fastapi import UploadFile

from app.config import settings
from app.services.media_files import (
    MediaValidationError,
    remove_storage_file,
    safe_original_name,
    save_upload_limited,
    storage_path_is_safe,
    upload_destination,
    validated_extension,
)


def test_filename_is_display_only_and_extension_is_allowlisted():
    assert safe_original_name(r"..\..\portrait.PNG", "fallback.png") == "portrait.PNG"
    assert validated_extension(r"..\portrait.PNG", {"png"}, fallback="x.png") == "png"
    with pytest.raises(MediaValidationError, match="Unsupported"):
        validated_extension("payload.exe", {"png"}, fallback="x.png")


@pytest.mark.asyncio
async def test_upload_is_bounded_and_atomic(tmp_path: Path):
    destination = tmp_path / "safe.bin"
    upload = UploadFile(filename="safe.bin", file=BytesIO(b"1234"))
    written = await save_upload_limited(upload, str(destination), max_bytes=4)
    assert written == 4
    assert destination.read_bytes() == b"1234"
    assert not list(tmp_path.glob("*.tmp"))

    oversized = UploadFile(filename="large.bin", file=BytesIO(b"12345"))
    with pytest.raises(MediaValidationError, match="too large"):
        await save_upload_limited(oversized, str(destination), max_bytes=4)
    # A rejected replacement never corrupts the previous good file.
    assert destination.read_bytes() == b"1234"


def test_managed_storage_cannot_delete_outside_root(tmp_path: Path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(settings, "storage_dir", str(storage))

    assert storage_path_is_safe(str(storage / "project"))
    assert not storage_path_is_safe(str(outside))
    assert not remove_storage_file(str(outside))
    assert outside.exists()

    managed = Path(upload_destination(1, "images", "scene", "png"))
    managed.write_bytes(b"image")
    assert remove_storage_file(str(managed))
    assert not managed.exists()
