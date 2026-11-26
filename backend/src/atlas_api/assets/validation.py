"""Bounded file integrity and GLB 2.0 container checks for editorial use."""

import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from struct import Struct
from typing import BinaryIO

ASSET_ROOT = Path("/srv/atlas/assets")
SHA256_PATTERN = r"^[0-9a-f]{64}$"
PATH_COMPONENT = re.compile(r"[a-zA-Z0-9_-][a-zA-Z0-9._-]*")
GLB_HEADER = Struct("<4sII")
CHUNK_HEADER = Struct("<I4s")
GLB_MIN_SIZE = GLB_HEADER.size + CHUNK_HEADER.size + 4
GLB_MAX_SIZE = 2**32 - 4
BLOCK_SIZE = 256 * 1024


class GLBValidationError(ValueError):
    """An asset file failed the integrity or GLB container checks."""


def glb_storage_reference(sha256: str) -> str:
    if re.fullmatch(SHA256_PATTERN, sha256) is None:
        raise GLBValidationError("Expected a lowercase hexadecimal SHA-256 digest.")
    return f"sha256/{sha256[:2]}/{sha256}.glb"


@contextmanager
def open_directory(path: Path, *, trusted_ancestors: bool = False) -> Iterator[int]:
    """Open an absolute directory without following a symlink in any component."""
    if path.anchor != "/" or ".." in path.parts:
        raise GLBValidationError(
            "Expected an absolute directory without parent components."
        )
    # Resolve every component relative to an open directory, with symlinks disabled.
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory_fd = os.open("/", directory_flags)
    try:
        # A user namespace can present the filesystem root owner under a mapped UID.
        trusted_uids = {0, os.geteuid(), os.fstat(directory_fd).st_uid}
        for part in path.parts[1:]:
            if trusted_ancestors:
                entry = os.fstat(directory_fd)
                if entry.st_uid not in trusted_uids or (
                    entry.st_mode & 0o022 and not entry.st_mode & stat.S_ISVTX
                ):
                    raise GLBValidationError(
                        "Storage path has an unsafe ancestor directory."
                    )
            child_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        yield directory_fd
    finally:
        os.close(directory_fd)


@contextmanager
def open_regular_file(directory_fd: int, name: str) -> Iterator[BinaryIO]:
    if name in ("", ".", "..") or "/" in name:
        raise GLBValidationError("Expected a single filename.")
    entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    if stat.S_ISLNK(entry.st_mode):
        raise OSError("Symlinks are not allowed.")
    if not stat.S_ISREG(entry.st_mode):
        raise GLBValidationError("The asset must be a regular file.")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise GLBValidationError("The asset must be a regular file.")
        with open(fd, "rb", closefd=False) as file:
            yield file
    finally:
        os.close(fd)


def validate_glb(
    reference: str, *, expected_size: int, expected_sha256: str, root: Path = ASSET_ROOT
) -> None:
    """Verify a GLB file's size, digest, and container framing.

    See Khronos glTF 2.0, Binary glTF Layout. Chunk payloads stay opaque.
    """
    parts = reference.split("/")
    if len(reference) > 255 or any(
        PATH_COMPONENT.fullmatch(part) is None for part in parts
    ):
        raise GLBValidationError("Invalid relative storage reference.")
    try:
        with (
            open_directory(root.joinpath(*parts[:-1])) as directory,
            open_regular_file(directory, parts[-1]) as file,
        ):
            validate_glb_file(
                file, expected_size=expected_size, expected_sha256=expected_sha256
            )
    except OSError:
        raise GLBValidationError(
            "Unable to open or read the asset file safely."
        ) from None


def validate_glb_file(
    file: BinaryIO, *, expected_size: int, expected_sha256: str
) -> None:
    """Verify an already securely opened, regular GLB file."""
    glb_storage_reference(expected_sha256)
    if not GLB_MIN_SIZE <= expected_size <= GLB_MAX_SIZE or expected_size % 4:
        raise GLBValidationError(
            "Expected file size is outside GLB 2.0 bounds or unaligned."
        )
    if os.fstat(file.fileno()).st_size != expected_size:
        raise GLBValidationError("Actual file size differs from the expected size.")
    file.seek(0)
    if _glb_sha256(file, expected_size) != expected_sha256:
        raise GLBValidationError("SHA-256 digest does not match.")


def _glb_sha256(file: BinaryIO, size: int) -> str:
    digest = hashlib.sha256()

    def read_exact(length: int) -> bytes:
        data = file.read(length)
        if len(data) != length:
            raise GLBValidationError("Truncated GLB container.")
        digest.update(data)
        return data

    magic, version, declared_size = GLB_HEADER.unpack(read_exact(GLB_HEADER.size))
    if magic != b"glTF" or version != 2:
        raise GLBValidationError("Expected a GLB 2.0 header.")
    if declared_size != size:
        raise GLBValidationError("Declared GLB length differs from the file size.")

    remaining = size - GLB_HEADER.size
    chunk_index = 0
    while remaining:
        if remaining < CHUNK_HEADER.size:
            raise GLBValidationError("Incomplete GLB chunk header.")
        length, kind = CHUNK_HEADER.unpack(read_exact(CHUNK_HEADER.size))
        remaining -= CHUNK_HEADER.size
        if length % 4 or length > remaining:
            raise GLBValidationError(
                "GLB chunk is unaligned or exceeds the container length."
            )
        if chunk_index == 0 and (kind != b"JSON" or length == 0):
            raise GLBValidationError("The first GLB chunk must contain JSON.")
        if (kind == b"JSON" and chunk_index != 0) or (
            kind == b"BIN\x00" and chunk_index != 1
        ):
            raise GLBValidationError("Unexpected position or duplicate GLB chunk type.")

        remaining -= length
        while length:
            block_size = min(length, BLOCK_SIZE)
            read_exact(block_size)
            length -= block_size
        chunk_index += 1

    if file.read(1):
        raise GLBValidationError("GLB file grew during validation.")
    return digest.hexdigest()
