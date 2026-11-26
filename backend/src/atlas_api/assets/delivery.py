"""Descriptor-based delivery of exact, published GLB versions."""

import os
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore
from typing import BinaryIO

from anyio import CancelScope, to_thread
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from atlas_api.assets.validation import (
    BLOCK_SIZE,
    GLBValidationError,
    glb_storage_reference,
    open_directory,
    open_regular_file,
    validate_glb_file,
)

GLB_MIME = "model/gltf-binary"
STORAGE_UNAVAILABLE = "Published asset storage unavailable."
DELIVERY_BUSY = "Asset delivery capacity unavailable."
_ENTITY_TAG = r'(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"'
_TAG_LIST = re.compile(rf"(?:[ \t]*(?:{_ENTITY_TAG})?[ \t]*,)*[ \t]*(?:{_ENTITY_TAG})?[ \t]*")


@dataclass(frozen=True)
class StoredObject:
    sha256: str
    file_size: int
    storage_reference: str


def cache_headers(sha256: str) -> dict[str, str]:
    return {
        "ETag": f'"{sha256}"',
        "Cache-Control": "no-cache, must-revalidate, no-transform",
        "X-Content-Type-Options": "nosniff",
    }


def matches_etag(if_none_match: str | None, etag: str) -> bool:
    if if_none_match is None:
        return False
    if if_none_match.strip(" \t") == "*":
        return True
    if _TAG_LIST.fullmatch(if_none_match) is None:
        return False
    return any(
        match.group().removeprefix("W/") == etag
        for match in re.finditer(_ENTITY_TAG, if_none_match)
    )


@contextmanager
def _storage_subdirectory(parent: int, name: str, owner: int) -> Iterator[int]:
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        entry = os.fstat(fd)
        if entry.st_uid != owner or entry.st_mode & 0o022:
            raise GLBValidationError("Unsafe storage directory.")
        yield fd
    finally:
        os.close(fd)


@contextmanager
def open_object(root: Path, obj: StoredObject) -> Iterator[BinaryIO]:
    reference = glb_storage_reference(obj.sha256)
    if obj.storage_reference != reference:
        raise GLBValidationError("Noncanonical storage reference.")
    prefix, bucket, filename = reference.split("/")
    with open_directory(root, trusted_ancestors=True) as root_fd:
        entry = os.fstat(root_fd)
        if entry.st_mode & 0o022:
            raise GLBValidationError("Unsafe storage root.")
        with (
            _storage_subdirectory(root_fd, prefix, entry.st_uid) as objects,
            _storage_subdirectory(objects, bucket, entry.st_uid) as leaf,
            open_regular_file(leaf, filename) as file,
        ):
            info = os.fstat(file.fileno())
            if (
                info.st_uid != entry.st_uid
                or info.st_mode & 0o7222
                or info.st_size != obj.file_size
            ):
                raise GLBValidationError("Unsafe or incorrectly sized stored object.")
            yield file


def open_verified(stack: ExitStack, root: Path, obj: StoredObject) -> BinaryIO:
    file = stack.enter_context(open_object(root, obj))
    before = os.fstat(file.fileno())
    validate_glb_file(file, expected_size=obj.file_size, expected_sha256=obj.sha256)
    after = os.fstat(file.fileno())
    if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
        before.st_size, before.st_mtime_ns, before.st_ctime_ns
    ):
        raise GLBValidationError("Stored object changed during verification.")
    file.seek(0)
    return file


async def file_chunks(file: BinaryIO, size: int) -> AsyncIterator[bytes]:
    remaining = size
    while remaining:
        block = await to_thread.run_sync(file.read, min(remaining, BLOCK_SIZE))
        if not block:
            raise OSError("Verified asset ended during streaming.")
        remaining -= len(block)
        yield block


class AssetFileResponse(Response):
    media_type = GLB_MIME

    def __init__(
        self,
        root: Path,
        obj: StoredObject,
        slots: BoundedSemaphore,
        if_none_match: str | None,
    ) -> None:
        super().__init__()
        self.root = root
        self.obj = obj
        self.slots = slots
        self.if_none_match = if_none_match

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.slots.acquire(blocking=False):
            await self._unavailable(DELIVERY_BUSY, scope, receive, send)
            return
        stack = ExitStack()
        try:
            try:
                file = await to_thread.run_sync(open_verified, stack, self.root, self.obj)
            except (OSError, GLBValidationError):
                await self._unavailable(STORAGE_UNAVAILABLE, scope, receive, send)
                return

            headers = cache_headers(self.obj.sha256)
            if matches_etag(self.if_none_match, headers["ETag"]):
                response = Response(status_code=304, headers=headers)
            else:
                headers["Content-Length"] = str(self.obj.file_size)
                response = StreamingResponse(
                    file_chunks(file, self.obj.file_size), media_type=GLB_MIME, headers=headers
                )
            await response(scope, receive, send)
        finally:
            try:
                with CancelScope(shield=True):
                    await to_thread.run_sync(stack.close)
            finally:
                self.slots.release()

    @staticmethod
    async def _unavailable(detail: str, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": detail}, status_code=503, headers={"Cache-Control": "no-store"}
        )
        await response(scope, receive, send)
