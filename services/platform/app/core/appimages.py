"""An app instance's photos (#468): files in `apps/<instance_id>/images/`.

The host stores what the user picked (`image.pick` in the SDK) and reads it
back for the sandbox; an id is the server's own `<32 hex>.<ext>`, so a
caller never names a path. Opened like the rest of the instance's storage:
from the instance's fd, `O_NOFOLLOW`, root:<space gid>.
"""

from __future__ import annotations

import os
import re
import secrets

from app.core import storage
from app.core.errors import InvalidInput, NotFound

IMAGES_DIR = "images"
MAX_BYTES = 512 * 1024
FILE_MODE = 0o640
_ID = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")
MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def sniff(data: bytes) -> str | None:
    """The extension for JPEG, PNG or WebP bytes, else None."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def check_id(image_id: str) -> str:
    if not _ID.match(image_id):
        raise NotFound("not_found")
    return image_id


def mime_of(image_id: str) -> str:
    return MIME[image_id.rsplit(".", 1)[1]]


def save(instance_fd: int, gid: int, data: bytes) -> str:
    """Store `data` (a JPEG, PNG or WebP of at most `MAX_BYTES`) and return its new id."""
    if len(data) > MAX_BYTES:
        raise InvalidInput("image_too_large")
    ext = sniff(data)
    if ext is None:
        raise InvalidInput("not_an_image")
    image_id = f"{secrets.token_hex(16)}.{ext}"
    dir_fd = storage._ensure_dir(IMAGES_DIR, gid, dir_fd=instance_fd, mode=storage.APPS_MODE)
    try:
        tmp = f".{image_id}.tmp"
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=dir_fd,
        )
        try:
            os.write(fd, data)
            storage._fchown(fd, storage.OWNER_UID, gid)
            os.fchmod(fd, FILE_MODE)
        finally:
            os.close(fd)
        os.rename(tmp, image_id, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    return image_id


def _open_images(instance_fd: int) -> int:
    try:
        return os.open(
            IMAGES_DIR,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=instance_fd,
        )
    except FileNotFoundError:
        raise NotFound("not_found") from None


def read(instance_fd: int, image_id: str) -> bytes:
    dir_fd = _open_images(instance_fd)
    try:
        try:
            fd = os.open(
                check_id(image_id), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd
            )
        except (FileNotFoundError, OSError):
            raise NotFound("not_found") from None
        try:
            chunks, size = [], 0
            while chunk := os.read(fd, 1 << 16):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    break
            return b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def delete(instance_fd: int, image_id: str) -> None:
    dir_fd = _open_images(instance_fd)
    try:
        os.unlink(check_id(image_id), dir_fd=dir_fd)
    except FileNotFoundError:
        raise NotFound("not_found") from None
    finally:
        os.close(dir_fd)
