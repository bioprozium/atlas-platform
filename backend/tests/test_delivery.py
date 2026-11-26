"""Path, descriptor ownership, and resource tests for GLB delivery."""

import os
from hashlib import sha256
from struct import pack
from threading import BoundedSemaphore

import anyio
import pytest
from fastapi.testclient import TestClient

from atlas_api.assets import delivery
from atlas_api.assets.validation import BLOCK_SIZE, GLBValidationError, glb_storage_reference
from atlas_api.config import RUNTIME_DATABASE_ENV
from atlas_api.main import create_app

FILE = "/api/v1/assets/example/versions/v1/file"
MANIFEST = "/api/v1/scenes/example/versions/v1/manifest"


@pytest.fixture
def stored(tmp_path):
    payload = b'{"asset":{"version":"2.0"}}'
    payload += b" " * (BLOCK_SIZE * 2 - len(payload))
    data = pack("<4sIII4s", b"glTF", 2, 20 + len(payload), len(payload), b"JSON") + payload
    digest = sha256(data).hexdigest()
    obj = delivery.StoredObject(digest, len(data), glb_storage_reference(digest))
    root = tmp_path / "assets"
    path = root / obj.storage_reference
    path.parent.mkdir(parents=True, mode=0o750)
    path.write_bytes(data)
    path.chmod(0o440)
    return root, obj, path, data


def test_rejects_unsafe_storage(stored, tmp_path):
    root, obj, path, data = stored
    for reference in ("../secret.glb", str(path), f"sha256/../{path.name}"):
        bad = delivery.StoredObject(obj.sha256, obj.file_size, reference)
        with pytest.raises(GLBValidationError), delivery.open_object(root, bad):
            pytest.fail("Opened a noncanonical path.")
    link_root = tmp_path / "link-root"
    link_root.symlink_to(root, target_is_directory=True)
    with pytest.raises(OSError), delivery.open_object(link_root, obj):
        pytest.fail("Opened a symlinked root.")

    moved = path.rename(path.with_suffix(".original"))
    path.symlink_to(moved)
    with pytest.raises(OSError), delivery.open_object(root, obj):
        pytest.fail("Opened a symlinked file.")
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(GLBValidationError), delivery.open_object(root, obj):
        pytest.fail("Opened a FIFO.")
    path.unlink()
    path.write_bytes(data[:-4])
    path.chmod(0o440)
    with pytest.raises(GLBValidationError), delivery.open_object(root, obj):
        pytest.fail("Opened an incorrectly sized file.")


def test_delivery_errors_are_uncacheable(monkeypatch):
    monkeypatch.delenv(RUNTIME_DATABASE_ENV, raising=False)
    with TestClient(create_app()) as client:
        for path in (FILE, MANIFEST):
            response = client.get(path, headers={"If-None-Match": "*"})
            assert response.status_code == 503
            assert response.headers["cache-control"] == "no-store"
            invalid = client.get(path.replace("/versions/v1/", "/versions/%5C../"))
            assert invalid.status_code == 422
            assert invalid.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("disconnect", [False, True])
def test_stream_uses_verified_descriptor_and_releases_resources(stored, monkeypatch, disconnect):
    root, obj, path, data = stored
    slots = BoundedSemaphore(1)
    opened = []
    original = delivery.open_verified

    def replace_name(*args):
        file = original(*args)
        opened.append(file)
        path.rename(path.with_suffix(".original"))
        path.write_bytes(b"different")
        return file

    monkeypatch.setattr(delivery, "open_verified", replace_name)

    async def exchange():
        first_body = anyio.Event()
        responses = []
        scope = {"type": "http", "asgi": {"spec_version": "2.3"}}

        async def receive():
            await first_body.wait()
            if disconnect:
                return {"type": "http.disconnect"}
            await anyio.sleep_forever()

        async def send(message):
            responses.append(message)
            if message["type"] != "http.response.body" or not message.get("body"):
                return
            assert slots.acquire(blocking=False) is False
            other = []

            async def capture(part):
                other.append(part)

            await delivery.AssetFileResponse(root, obj, slots, None)(scope, receive, capture)
            assert other[0]["status"] == 503
            first_body.set()
            if disconnect:
                await anyio.sleep_forever()

        await delivery.AssetFileResponse(root, obj, slots, None)(scope, receive, send)
        assert responses[0]["status"] == 200
        blocks = [message["body"] for message in responses if "body" in message]
        assert max(map(len, blocks)) <= BLOCK_SIZE
        assert b"".join(blocks) == (data[:BLOCK_SIZE] if disconnect else data)
        assert opened[0].closed
        assert slots.acquire(blocking=False)
        slots.release()

    anyio.run(exchange)


@pytest.mark.parametrize(
    ("header", "matches"),
    [
        (None, False),
        ("*", True),
        ('W/"digest"', True),
        ('"other,tag", W/"digest"', True),
        (', "digest", ,', True),
        ('"other" "digest"', False),
        ('"digest", invalid', False),
        ('"other, W/digest"', False),
        ("  , " * 1000 + "bogus", False),
    ],
)
def test_if_none_match_parses_tag_lists_without_accepting_malformed_values(header, matches):
    assert delivery.matches_etag(header, '"digest"') is matches
