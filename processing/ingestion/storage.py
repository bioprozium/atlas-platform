"""Private staging and durable, no-clobber content-addressed storage."""

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from atlas_api.assets.validation import (
    BLOCK_SIZE,
    GLBValidationError,
    glb_storage_reference,
    open_directory,
    open_regular_file,
)

from .validation import IngestionError, Limits, verify_container

DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def check_directory(directory: int, *, private: bool = False) -> None:
    entry = os.fstat(directory)
    forbidden = 0o077 if private else 0o022
    if entry.st_uid != os.geteuid() or entry.st_mode & forbidden:
        raise IngestionError(
            "UNSAFE_STORAGE", "Storage directory ownership or mode is unsafe."
        )


@contextmanager
def subdirectory(parent: int, name: str, *, private: bool = False) -> Iterator[int]:
    try:
        os.mkdir(name, mode=0o700 if private else 0o750, dir_fd=parent)
    except FileExistsError:
        pass
    fd = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
    try:
        check_directory(fd, private=private)
        os.fsync(parent)
        yield fd
    finally:
        os.close(fd)


def copy_input(path: Path, target: BinaryIO, limit: int) -> tuple[int, str]:
    if ".." in path.parts:
        raise IngestionError("UNSAFE_INPUT", "Input paths must omit parent components.")
    digest = sha256()
    size = 0
    try:
        with (
            open_directory(path.absolute().parent) as parent,
            open_regular_file(parent, path.name) as file,
        ):
            before = os.fstat(file.fileno())
            if before.st_size > limit:
                raise IngestionError(
                    "INPUT_TOO_LARGE", "Input exceeds the configured byte limit."
                )
            while block := file.read(min(BLOCK_SIZE, limit - size + 1)):
                size += len(block)
                if size > limit:
                    raise IngestionError(
                        "INPUT_TOO_LARGE", "Input exceeds the configured byte limit."
                    )
                digest.update(block)
                target.write(block)
            after = os.fstat(file.fileno())
            if (size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) or size != before.st_size:
                raise IngestionError(
                    "INPUT_CHANGED", "Input changed while being copied."
                )
    except (OSError, GLBValidationError):
        raise IngestionError(
            "UNSAFE_INPUT", "Could not safely copy the input file."
        ) from None
    return size, digest.hexdigest()


@dataclass(frozen=True)
class StagedAsset:
    directory: int
    name: str
    file: BinaryIO
    size: int
    digest: str

    @property
    def reference(self) -> str:
        return glb_storage_reference(self.digest)


@contextmanager
def stage(root: int, path: Path, limits: Limits) -> Iterator[StagedAsset]:
    with subdirectory(root, ".quarantine", private=True) as quarantine:
        name = uuid4().hex + ".glb"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(name, flags, 0o600, dir_fd=quarantine)
        try:
            with os.fdopen(fd, "wb") as output:
                size, digest = copy_input(path, output, limits.input_bytes)
                output.flush()
                os.fchmod(output.fileno(), 0o400)
                os.fsync(output.fileno())
            with open_regular_file(quarantine, name) as file:
                yield StagedAsset(quarantine, name, file, size, digest)
        finally:
            os.unlink(name, dir_fd=quarantine)
            os.fsync(quarantine)


def promote(root: int, asset: StagedAsset, limits: Limits) -> bool:
    """Link validated bytes into place and verify and sync the final object."""
    prefix, bucket, name = asset.reference.split("/")
    try:
        with (
            subdirectory(root, prefix) as objects,
            subdirectory(objects, bucket) as leaf,
        ):
            created = True
            try:
                os.link(
                    asset.name,
                    name,
                    src_dir_fd=asset.directory,
                    dst_dir_fd=leaf,
                    follow_symlinks=False,
                )
            except FileExistsError:
                created = False
            with open_regular_file(leaf, name) as final:
                entry = os.fstat(final.fileno())
                if entry.st_uid != os.geteuid() or entry.st_mode & (
                    0o222 | stat.S_ISUID | stat.S_ISGID
                ):
                    raise IngestionError(
                        "UNSAFE_DESTINATION",
                        "Stored object ownership or mode is unsafe.",
                    )
                try:
                    verify_container(final, asset.size, asset.digest, limits)
                except IngestionError:
                    raise IngestionError(
                        "DESTINATION_INTEGRITY",
                        "Stored object does not match the validated bytes.",
                    ) from None
                os.fsync(final.fileno())
            os.fsync(leaf)
            return created
    except (OSError, GLBValidationError):
        raise IngestionError(
            "UNSAFE_DESTINATION", "Could not safely promote the stored object."
        ) from None
