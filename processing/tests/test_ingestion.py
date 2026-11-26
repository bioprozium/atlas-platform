"""Synthetic GLB inputs, secure storage and the real offline validator."""

import argparse
import json
import os
import stat
import subprocess
import sys
from base64 import b64encode
from fcntl import LOCK_EX, LOCK_NB, flock
from hashlib import sha256
from pathlib import Path
from struct import pack
from zlib import compress, crc32

import pytest

from atlas_api.assets.validation import glb_storage_reference, open_directory
from processing.ingestion import __main__ as cli
from processing.ingestion import storage, validation
from processing.ingestion.validation import IngestionError, Limits

REPO = Path(__file__).resolve().parents[2]
DEFAULT_LIMITS = Limits()


def glb(document, binary=b""):
    def chunk(kind, data, padding):
        data += padding * (-len(data) % 4)
        return pack("<I4s", len(data), kind) + data

    raw = document if isinstance(document, bytes) else json.dumps(document).encode()
    chunks = chunk(b"JSON", raw, b" ")
    if binary:
        chunks += chunk(b"BIN\x00", binary, b"\x00")
    return pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def triangle():
    return {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "buffers": [{"byteLength": 36}],
        "bufferViews": [{"buffer": 0, "byteLength": 36, "target": 34962}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 3,
                "type": "VEC3",
                "min": [0, 0, 0],
                "max": [1, 1, 0],
            }
        ],
    }


TRIANGLE_BYTES = pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)


@pytest.fixture
def input_and_root(tmp_path):
    source = tmp_path / "asset with an arbitrary extension.data"
    source.write_bytes(glb(triangle(), TRIANGLE_BYTES))
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    return source, root


def inspect(source, root, *, limits=DEFAULT_LIMITS, expected=None):
    args = argparse.Namespace(
        operation="inspect", input=source, storage_root=root, expected_sha256=expected
    )
    return cli.run(args, limits, None)


def codes(report):
    return {issue["code"] for issue in report["issues"]}


def test_cli_inspects_deterministic_triangle(input_and_root):
    source, root = input_and_root
    expected = sha256(source.read_bytes()).hexdigest()
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "processing.ingestion",
            "--storage-root",
            str(root),
            "inspect",
            str(source),
            "--expected-sha256",
            expected,
        ],
        cwd=REPO,
        capture_output=True,
        check=True,
    )
    report = json.loads(done.stdout)
    assert report["sha256"] == expected
    assert report["byte_length"] == source.stat().st_size
    assert report["validation_status"] == "passed"
    assert report["validator"] == {
        "name": "Khronos glTF Validator",
        "version": "2.0.0-dev.3.10",
        "completed": True,
    }
    assert report["summary"]["totalTriangleCount"] == 1
    assert not report["issues"]
    assert str(source) not in done.stdout.decode()
    assert list((root / ".quarantine").iterdir()) == []
    assert not (root / "sha256").exists()


@pytest.mark.parametrize(
    ("transform", "code"),
    [
        (lambda b: b"x" + b[1:], "GLB_INTEGRITY"),
        (lambda b: b[:12] + pack("<I", 3) + b[16:], "GLB_INTEGRITY"),
        (lambda b: glb(b'{"asset":'), "GLTF_JSON_INVALID"),
        (
            lambda b: glb(b'{"asset":{"version":"2.0","generator":"\xff"}}'),
            "GLTF_JSON_INVALID",
        ),
        (lambda b: glb({"asset": {"version": 2}}), "TYPE_MISMATCH"),
    ],
)
def test_invalid_container_or_json(input_and_root, transform, code):
    source, root = input_and_root
    source.write_bytes(transform(source.read_bytes()))
    report, status = inspect(source, root)
    assert status == 1
    assert report["validation_status"] == "rejected"
    assert code in codes(report)
    assert list((root / ".quarantine").iterdir()) == []


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        (
            {
                "accessors": [
                    {"bufferView": 9, "componentType": 5126, "count": 3, "type": "VEC3"}
                ]
            },
            "UNRESOLVED_REFERENCE",
        ),
        ({"bufferViews": [{"buffer": 0, "byteLength": 40}]}, "BUFFER_VIEW_TOO_LONG"),
        (
            {
                "materials": [
                    {"pbrMetallicRoughness": {"baseColorTexture": {"index": 9}}}
                ]
            },
            "UNRESOLVED_REFERENCE",
        ),
        ({"textures": [{"source": 9}]}, "UNRESOLVED_REFERENCE"),
        (
            {"images": [{"bufferView": 9, "mimeType": "image/png"}]},
            "UNRESOLVED_REFERENCE",
        ),
        ({"images": [{"uri": "missing.png"}]}, "EXTERNAL_RESOURCE"),
        (
            {"images": [{"uri": "https://example.invalid/missing.png"}]},
            "EXTERNAL_RESOURCE",
        ),
        (
            {"buffers": [{"uri": "file:///etc/passwd", "byteLength": 36}]},
            "EXTERNAL_RESOURCE",
        ),
        (
            {"buffers": [{"uri": "../../outside.bin", "byteLength": 36}]},
            "EXTERNAL_RESOURCE",
        ),
        (
            {
                "extensionsUsed": ["KHR_draco_mesh_compression"],
                "extensionsRequired": ["KHR_draco_mesh_compression"],
            },
            "REQUIRED_EXTENSION_UNSUPPORTED",
        ),
    ],
)
def test_validator_checks_references_and_embedded_policy(input_and_root, changes, code):
    source, root = input_and_root
    source.write_bytes(glb(triangle() | changes, TRIANGLE_BYTES))
    report, status = inspect(source, root)
    assert status == 1
    assert report["validation_status"] == "rejected"
    assert code in codes(report)
    assert report["validator"]["completed"] == (
        code
        not in {
            "REQUIRED_EXTENSION_UNSUPPORTED",
            "EXTERNAL_RESOURCE",
        }
    )


def test_supported_extension_and_warnings(input_and_root):
    source, root = input_and_root
    document = triangle()
    document.update(
        extensionsUsed=["KHR_materials_unlit"],
        extensionsRequired=["KHR_materials_unlit"],
        materials=[{"extensions": {"KHR_materials_unlit": {}}}],
    )
    document["asset"]["syntheticUnexpectedProperty"] = True
    document["meshes"][0]["primitives"][0]["material"] = 0
    source.write_bytes(glb(document, TRIANGLE_BYTES))
    report, status = inspect(source, root)
    assert status == 0
    assert any(
        issue["code"] == "UNEXPECTED_PROPERTY" and issue["severity"] == 1
        for issue in report["issues"]
    )


def test_embedded_data_uri_is_validated_without_external_access(input_and_root):
    source, root = input_and_root

    def png_chunk(kind, data):
        return pack(">I", len(data)) + kind + data + pack(">I", crc32(kind + data))

    png = b"\x89PNG\r\n\x1a\n"
    png += png_chunk(b"IHDR", pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    png += png_chunk(b"IDAT", compress(bytes([0, 255, 0, 0])))
    png += png_chunk(b"IEND", b"")
    source.write_bytes(
        glb(
            triangle()
            | {
                "images": [
                    {"uri": "data:image/png;base64," + b64encode(png).decode("ascii")}
                ],
                "textures": [{"source": 0}],
            },
            TRIANGLE_BYTES,
        )
    )
    report, status = inspect(source, root)
    assert status == 0
    assert "DATA_URI_GLB" in codes(report)


def test_truncated_issue_list_blocks_import_even_when_issues_are_warnings(
    input_and_root,
):
    source, root = input_and_root
    document = triangle()
    document["asset"].update({f"unknown{i}": True for i in range(128)})
    source.write_bytes(glb(document, TRIANGLE_BYTES))
    report, status = inspect(source, root)
    assert status == 1
    assert "UNEXPECTED_PROPERTY" in codes(report)
    assert "VALIDATOR_ISSUE_LIMIT" in codes(report)


def test_limits_and_expected_digest(input_and_root):
    source, root = input_and_root
    assert "SHA256_MISMATCH" in codes(inspect(source, root, expected="0" * 64)[0])
    assert "INPUT_TOO_LARGE" in codes(
        inspect(source, root, limits=Limits(input_bytes=128, json_bytes=64))[0]
    )
    assert "GLTF_JSON_TOO_LARGE" in codes(
        inspect(source, root, limits=Limits(json_bytes=64))[0]
    )
    with pytest.raises(ValueError):
        Limits(memory_mib=2048)
    with pytest.raises(ValueError):
        Limits(cpu_seconds=5, wall_seconds=3)
    with pytest.raises(ValueError):
        Limits(input_bytes=257 * validation.MIB)


def test_rejects_unsafe_paths_and_modes(input_and_root, tmp_path):
    source, root = input_and_root
    link = tmp_path / "input-link"
    link.symlink_to(source)
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(tmp_path, target_is_directory=True)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    for bad in (
        link,
        fifo,
        tmp_path,
        parent_link / source.name,
        tmp_path / ".." / source.name,
    ):
        report, status = inspect(bad, root)
        assert status == 1
        assert "UNSAFE_INPUT" in codes(report)
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    moved_root = unsafe / "store"
    moved_root.mkdir(mode=0o700)
    assert inspect(source, moved_root)[1] == 1
    (root / ".quarantine").chmod(0o755)
    assert "UNSAFE_STORAGE" in codes(inspect(source, root)[0])


def test_validation_is_against_the_staged_snapshot(input_and_root, monkeypatch):
    source, root = input_and_root
    original = sha256(source.read_bytes()).hexdigest()
    verify = cli.verify_container

    def change_input(*args):
        source.write_bytes(b"replacement")
        return verify(*args)

    monkeypatch.setattr(cli, "verify_container", change_input)
    report, status = inspect(source, root, expected=original)
    assert status == 0
    assert report["sha256"] == original


def test_concurrent_validation_on_same_store_is_rejected(input_and_root):
    source, root = input_and_root
    with open_directory(root) as directory:
        flock(directory, LOCK_EX | LOCK_NB)
        report, status = inspect(source, root)
    assert status == 1
    assert "INGESTION_BUSY" in codes(report)
    assert not (root / ".quarantine").exists()


def test_staging_cleanup_and_retry_after_interruption(input_and_root, monkeypatch):
    source, root = input_and_root

    def interrupt(_source, output, _limit):
        output.write(b"partial")
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(storage, "copy_input", interrupt)
        with pytest.raises(KeyboardInterrupt):
            inspect(source, root)
    assert list((root / ".quarantine").iterdir()) == []

    crash = """
import os
import sys
from processing.ingestion import __main__, storage

def crash(_source, output, _limit):
    output.write(b'partial')
    output.flush()
    os._exit(73)

storage.copy_input = crash
sys.exit(__main__.main())
"""
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            crash,
            "--storage-root",
            str(root),
            "inspect",
            str(source),
        ],
        cwd=REPO,
        check=False,
    )
    assert done.returncode == 73
    leftovers = list((root / ".quarantine").iterdir())
    assert len(leftovers) == 1 and leftovers[0].read_bytes() == b"partial"
    assert leftovers[0].stat().st_mode & 0o777 == 0o600
    assert inspect(source, root)[1] == 0
    assert list((root / ".quarantine").iterdir()) == leftovers


def test_no_clobber_promote_and_verify_existing_objects(input_and_root, tmp_path):
    source, root = input_and_root
    limits = Limits()
    expected = sha256(source.read_bytes()).hexdigest()
    destination = root / glb_storage_reference(expected)
    with (
        open_directory(root) as directory,
        storage.stage(directory, source, limits) as staged,
    ):
        validation.verify_container(staged.file, staged.size, staged.digest, limits)
        assert cli.validate_semantics(staged.file, limits)["numErrors"] == 0
        assert storage.promote(directory, staged, limits)
        inode = destination.stat().st_ino
        assert destination.stat().st_mode & 0o777 == 0o400
        assert not storage.promote(directory, staged, limits)
        assert destination.stat().st_ino == inode
        destination.unlink()
        destination.symlink_to(source)
        with pytest.raises(IngestionError, match="safely promote"):
            storage.promote(directory, staged, limits)
        destination.unlink()
        destination.write_bytes(source.read_bytes().replace(b"2.0", b"2.1"))
        destination.chmod(0o400)
        with pytest.raises(IngestionError, match="does not match"):
            storage.promote(directory, staged, limits)
        assert b"2.1" in destination.read_bytes()
        destination.unlink()
        destination.parent.rmdir()
        destination.parent.symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(IngestionError, match="safely promote"):
            storage.promote(directory, staged, limits)
    assert not (tmp_path / destination.name).exists()


@pytest.mark.parametrize(
    ("javascript", "limits", "code"),
    [
        (
            "setInterval(() => {}, 1000);",
            Limits(wall_seconds=1, cpu_seconds=1),
            "VALIDATOR_TIMEOUT",
        ),
        (
            "while (true) {}",
            Limits(wall_seconds=5, cpu_seconds=1),
            "VALIDATOR_PROCESS_FAILED",
        ),
        (
            "Buffer.alloc(768 * 1024 ** 2);",
            Limits(memory_mib=512),
            "VALIDATOR_PROCESS_FAILED",
        ),
        (
            "process.stdout.write(Buffer.alloc(2 * 1024 ** 2));",
            Limits(),
            "VALIDATOR_OUTPUT_LIMIT",
        ),
    ],
)
def test_validator_process_limits(
    input_and_root, tmp_path, monkeypatch, javascript, limits, code
):
    source, root = input_and_root
    sandbox = (validation.VALIDATOR_DIR / "sandbox.py").read_bytes()
    (tmp_path / "sandbox.py").write_bytes(sandbox)
    (tmp_path / "validator.cjs").write_text(javascript)
    (tmp_path / "node_modules" / "gltf-validator").mkdir(parents=True)
    monkeypatch.setattr(validation, "VALIDATOR_DIR", tmp_path)
    report, status = inspect(source, root, limits=limits)
    assert status == 1
    assert report["validation_status"] == "error"
    assert code in codes(report)


def test_launcher_blocks_network():
    program = """
import errno
import socket
for family in (socket.AF_INET, socket.AF_INET6, socket.AF_UNIX):
    try:
        socket.socket(family)
    except PermissionError as exc:
        assert exc.errno == errno.EPERM
    else:
        raise AssertionError('socket creation must be denied')
"""
    subprocess.run(
        [
            sys.executable,
            "-I",
            str(validation.VALIDATOR_DIR / "sandbox.py"),
            str(os.getpid()),
            "3",
            str(512 * validation.MIB),
            str(validation.OUTPUT_SIZE),
            sys.executable,
            "-I",
            "-c",
            program,
        ],
        check=True,
    )


def test_delivery_promotion_permissions_and_retry(input_and_root):
    source, root = input_and_root
    gid = os.getegid()
    root.chmod(0o2750)
    with (
        pytest.MonkeyPatch.context() as patch,
        open_directory(root, trusted_ancestors=True) as directory,
    ):
        patch.setattr(storage.grp, "getgrnam", lambda name: storage.grp.getgrgid(gid))
        assert storage.delivery_group(directory, "atlasdelivery") == gid
        storage.check_directory(directory, delivery_gid=gid)
        for created in (True, False):
            with storage.stage(directory, source, DEFAULT_LIMITS) as staged:
                quarantine = root / ".quarantine"
                assert stat.S_IMODE(quarantine.stat().st_mode) & 0o777 == 0o700
                assert stat.S_IMODE(os.fstat(staged.file.fileno()).st_mode) == 0o400
                assert storage.promote(
                    directory, staged, DEFAULT_LIMITS, delivery_gid=gid
                ) is created
                path = root / staged.reference
                assert path.stat().st_gid == gid
                assert stat.S_IMODE(path.stat().st_mode) == 0o440
                assert stat.S_IMODE(path.parent.stat().st_mode) == 0o2750
                assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o2750
                assert path.stat().st_uid == os.geteuid()
            assert list(quarantine.iterdir()) == []
