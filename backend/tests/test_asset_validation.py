import hashlib
import os
from struct import pack

import pytest

from atlas_api.assets import validation
from atlas_api.assets.validation import (
    GLBValidationError,
    glb_storage_reference,
    validate_glb,
)


def container(*chunks, magic=b"glTF", version=2, length=None):
    payload = b"".join(chunks)
    return (
        pack("<4sII", magic, version, length if length is not None else 12 + len(payload))
        + payload
    )


def chunk(kind, data):
    return pack("<I4s", len(data), kind) + data


JSON = chunk(b"JSON", b'{"asset":{"version":"2.0"}} ')
BIN = chunk(b"BIN\x00", b"\x00" * 4)


def write_file(root, reference, data):
    path = root / reference
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("extra_chunks", [(), (BIN,), (chunk(b"TEST", b"more"),)])
def test_valid_glb_containers(tmp_path, extra_chunks):
    data = container(JSON, *extra_chunks)
    digest = hashlib.sha256(data).hexdigest()
    reference = glb_storage_reference(digest)
    assert reference == f"sha256/{digest[:2]}/{digest}.glb"
    write_file(tmp_path, reference, data)
    validate_glb(reference, root=tmp_path, expected_size=len(data), expected_sha256=digest)


def test_streams_across_multiple_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(validation, "BLOCK_SIZE", 8)
    data = container(JSON, chunk(b"BIN\x00", b"\x00" * 20))
    write_file(tmp_path, "model.glb", data)
    validate_glb(
        "model.glb",
        root=tmp_path,
        expected_size=len(data),
        expected_sha256=hashlib.sha256(data).hexdigest(),
    )


def test_glb_integrity_checks(tmp_path, synthetic_glb):
    write_file(tmp_path, "model.glb", synthetic_glb)
    digest = hashlib.sha256(synthetic_glb).hexdigest()
    with pytest.raises(GLBValidationError, match="Actual file size"):
        validate_glb(
            "model.glb",
            root=tmp_path,
            expected_size=len(synthetic_glb) + 4,
            expected_sha256=digest,
        )
    with pytest.raises(GLBValidationError, match="SHA-256 digest does not match"):
        validate_glb(
            "model.glb",
            root=tmp_path,
            expected_size=len(synthetic_glb),
            expected_sha256="0" * 64,
        )
    with pytest.raises(GLBValidationError, match="lowercase hexadecimal"):
        glb_storage_reference(digest.upper())


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (container(JSON, magic=b"glb!"), "Expected a GLB 2.0 header"),
        (container(JSON, version=1), "Expected a GLB 2.0 header"),
        (container(JSON, length=12), "Declared GLB length"),
        (container(BIN), "first GLB chunk must contain JSON"),
        (container(chunk(b"JSON", b""), BIN), "first GLB chunk must contain JSON"),
        (container(JSON, JSON), "Unexpected position or duplicate"),
        (container(JSON, BIN, BIN), "Unexpected position or duplicate"),
        (container(JSON, chunk(b"TEST", b"data"), BIN), "Unexpected position or duplicate"),
        (container(JSON, b"tail"), "Incomplete GLB chunk header"),
        (container(JSON, chunk(b"TEST", b"odd") + b"\x00"), "GLB chunk is unaligned"),
        (container(JSON, pack("<I4s", 8, b"TEST") + b"data"), "exceeds the container length"),
    ],
)
def test_malformed_glb_containers(tmp_path, data, message):
    write_file(tmp_path, "bad.glb", data)
    with pytest.raises(GLBValidationError, match=message):
        validate_glb(
            "bad.glb",
            root=tmp_path,
            expected_size=len(data),
            expected_sha256=hashlib.sha256(data).hexdigest(),
        )


@pytest.mark.parametrize(
    "reference",
    [
        "/model.glb",
        "../model.glb",
        "a/../model.glb",
        "./model.glb",
        "a//model.glb",
        "model.glb/",
        "a\\model.glb",
        "model.glb\x00",
        "",
        "a%2fmodel.glb",
    ],
)
def test_unsafe_paths_fail_before_file_access(tmp_path, reference):
    with pytest.raises(GLBValidationError, match="Invalid relative storage reference"):
        validate_glb(reference, root=tmp_path, expected_size=24, expected_sha256="0" * 64)


def test_symlinks_and_non_regular_files_are_rejected(tmp_path, synthetic_glb):
    root = tmp_path / "store"
    root.mkdir()
    outside = write_file(tmp_path, "outside/model.glb", synthetic_glb)
    (root / "leaf.glb").symlink_to(outside)
    (root / "subdir").symlink_to(outside.parent, target_is_directory=True)
    (root / "inner-link.glb").symlink_to(write_file(root, "model.glb", synthetic_glb))
    (tmp_path / "root-link").symlink_to(root, target_is_directory=True)
    os.mkfifo(root / "fifo.glb")

    expected = {
        "expected_size": len(synthetic_glb),
        "expected_sha256": hashlib.sha256(synthetic_glb).hexdigest(),
    }
    for reference in ("leaf.glb", "subdir/model.glb", "inner-link.glb", "missing.glb"):
        with pytest.raises(GLBValidationError, match="safely"):
            validate_glb(reference, root=root, **expected)
    with pytest.raises(GLBValidationError, match="safely"):
        validate_glb("model.glb", root=tmp_path / "root-link", **expected)
    with pytest.raises(GLBValidationError, match="safely"):
        validate_glb("model.glb", root=tmp_path / "root-link" / "nested", **expected)
    (root / "directory.glb").mkdir()
    for reference in ("fifo.glb", "directory.glb"):
        with pytest.raises(GLBValidationError, match="regular file"):
            validate_glb(reference, root=root, **expected)
