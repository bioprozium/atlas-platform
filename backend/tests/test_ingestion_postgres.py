"""Exercise the operator CLI and least-privilege boundary on disposable PostgreSQL 16."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from struct import pack

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import NullPool

from atlas_api.assets.ingestion import INGESTION_DATABASE_ENV, INGESTION_ROLE
from atlas_api.config import (
    MIGRATION_DATABASE_ENV,
    MIGRATION_ROLE,
    RUNTIME_DATABASE_ENV,
    RUNTIME_ROLE,
)
from atlas_api.main import create_app

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent


@pytest.fixture(scope="module")
def database(postgres_url, postgres_socket):
    config = Config(str(BACKEND / "alembic.ini"))
    with pytest.MonkeyPatch.context() as env:
        env.setenv(
            MIGRATION_DATABASE_ENV, postgres_url(MIGRATION_ROLE).render_as_string()
        )
        command.upgrade(config, "0004")
        command.upgrade(config, "head")
        subprocess.run(
            [
                "psql",
                "-X",
                "-h",
                str(postgres_socket),
                "-U",
                "atlas_test_admin",
                "-d",
                "atlas_db",
                "-f",
                str(BACKEND / "provision_ingestion.sql"),
            ],
            check=True,
            capture_output=True,
        )
        env.setenv(
            INGESTION_DATABASE_ENV, postgres_url(INGESTION_ROLE).render_as_string()
        )
        env.setenv(RUNTIME_DATABASE_ENV, postgres_url(RUNTIME_ROLE).render_as_string())

        def engine(role):
            return create_engine(postgres_url(role), poolclass=NullPool)

        yield config, engine


@pytest.fixture
def paths(tmp_path, synthetic_glb):
    source = tmp_path / "fixture.bin"
    source.write_bytes(synthetic_glb)
    metadata = tmp_path / "metadata.json"
    root = tmp_path / "objects"
    root.mkdir(mode=0o700)
    return source, metadata, root


def manifest(path, code, version="1", creator="Synthetic Author"):
    path.write_text(
        json.dumps(
            {
                "asset_code": code,
                "version": version,
                "title": "Synthetic Asset",
                "source_geometry": {
                    "units": "m",
                    "orientation": "Y-up",
                    "geometry_notes": "Synthetic fixture",
                    "export_parameters": "Deterministic GLB 2.0 fixture",
                },
                "sources": [
                    {
                        "creator": creator,
                        "rights_holder": creator,
                        "source_reference": "Synthetic fixture for ingestion tests",
                        "spdx_license_id": "CC0-1.0",
                        "license_reference": "https://creativecommons.org/publicdomain/zero/1.0/",
                        "attribution": "Synthetic fixture",
                        "modifications": "No modifications",
                    }
                ],
            }
        )
    )


def invoke(paths, *, launch=None):
    source, metadata, root = paths
    return subprocess.run(
        [
            sys.executable,
            *(launch or ["-m", "processing.ingestion"]),
            "--storage-root",
            str(root),
            "import",
            str(source),
            "--metadata",
            str(metadata),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def run_import(paths, status=0):
    result = invoke(paths)
    assert result.returncode == status, (result.stdout, result.stderr)
    return json.loads(result.stdout)


def changed_glb():
    payload = b'{"asset":{"version":"2.0","generator":"synthetic"}}'
    payload += b" " * (-len(payload) % 4)
    return (
        pack("<4sIII4s", b"glTF", 2, 20 + len(payload), len(payload), b"JSON") + payload
    )


def test_draft_idempotence_conflicts_and_exact_version_provenance(database, paths):
    _config, engine = database
    source, metadata, root = paths
    code = "synthetic-ingestion"
    manifest(metadata, code)

    created = run_import(paths)
    first_id = created["registration"]["version_id"]
    assert created["registration"]["outcome"] == "created"
    assert created["storage_outcome"] == "created"
    assert created["storage_verified"]
    destination = root / created["storage_reference"]
    inode = destination.stat().st_ino

    replay = run_import(paths)
    assert replay["registration"] == {"outcome": "reused", "version_id": first_id}
    assert replay["storage_outcome"] == "reused"
    assert destination.stat().st_ino == inode
    assert replay["ingestion_id"] != created["ingestion_id"]

    manifest(metadata, code, creator="Different Author")
    conflict = run_import(paths, status=1)
    assert conflict["registration"]["outcome"] == "metadata_conflict"
    assert conflict["issues"][-1]["code"] == "VERSION_METADATA_CONFLICT"

    manifest(metadata, code)
    original = source.read_bytes()
    source.write_bytes(changed_glb())
    conflict = run_import(paths, status=1)
    assert conflict["registration"]["outcome"] == "content_conflict"
    assert conflict["issues"][-1]["code"] == "VERSION_CONTENT_CONFLICT"
    assert destination.read_bytes() == original
    assert (root / conflict["storage_reference"]).read_bytes() == changed_glb()

    manifest(metadata, code, version="2", creator="Other Synthetic Author")
    second = run_import(paths)
    second_id = second["registration"]["version_id"]
    assert second["storage_outcome"] == "reused"
    assert second_id != first_id
    with engine(MIGRATION_ROLE).begin() as conn:
        conn.execute(text("SET LOCAL ROLE atlas_owner"))
        rows = conn.execute(
            text("""
            SELECT v.id::text, a.review_state, v.publication_state, v.validation_state,
              v.rights_verified_at, v.rights_review_reference, v.primary_source_id,
              v.format, v.mime_type, s.creator, s.license_reference, v.sha256, v.file_size
            FROM atlas.assets a JOIN atlas.asset_versions v ON a.id = v.asset_id
            JOIN atlas.asset_sources s ON v.id = s.version_id
            WHERE a.code = :code ORDER BY v.version
        """),
            {"code": code},
        ).all()
        assert [row.id for row in rows] == [first_id, second_id]
        assert [row.creator for row in rows] == [
            "Synthetic Author",
            "Other Synthetic Author",
        ]
        for row, report in zip(rows, (created, second), strict=True):
            assert row[1:9] == (
                "draft",
                "draft",
                "valid",
                None,
                None,
                None,
                "glb",
                "model/gltf-binary",
            )
            assert row.sha256 == report["sha256"]
            assert row.file_size == report["byte_length"]
            assert (
                row.license_reference
                == "https://creativecommons.org/publicdomain/zero/1.0/"
            )

        audit = conn.execute(
            text("""
            SELECT actor, sha256, validation_status, outcome, report, recorded_at
            FROM atlas.asset_ingestions WHERE id = :id
        """),
            {"id": created["ingestion_id"]},
        ).one()
        assert audit[:4] == ("atlas_ingest", created["sha256"], "passed", "created")
        assert audit.recorded_at is not None
        assert audit.report["validator"]["version"] == "2.0.0-dev.3.10"
        assert audit.report["started_at"] <= audit.report["finished_at"]
        assert audit.report["source_geometry"]["orientation"] == "Y-up"

    with TestClient(create_app()) as client:
        path = f"/api/v1/assets/{code}/versions/1"
        assert client.get(path).status_code == 404
        with engine(MIGRATION_ROLE).begin() as conn:
            conn.execute(text("SET LOCAL ROLE atlas_owner"))
            conn.execute(
                text("""
                    UPDATE atlas.asset_versions AS v
                    SET publication_state = 'published', rights_verified_at = now(),
                        rights_review_reference = 'synthetic editorial witness',
                        primary_source_id = (SELECT s.id FROM atlas.asset_sources s
                            WHERE s.version_id = v.id)
                    WHERE v.id = :id
                """),
                {"id": first_id},
            )
            conn.execute(
                text(
                    "UPDATE atlas.assets SET review_state = 'published' WHERE code = :code"
                ),
                {"code": code},
            )
            scene_id = conn.scalar(
                text("""
                INSERT INTO atlas.scenes (code, title, review_state)
                VALUES ('ingestion-scene', 'Synthetic Scene', 'published') RETURNING id
            """)
            )
            scene_version_id = conn.scalar(
                text("""
                INSERT INTO atlas.scene_versions (scene_id, version, origin_description)
                VALUES (:id, '1', 'Synthetic origin') RETURNING id
            """),
                {"id": scene_id},
            )
            conn.execute(
                text("""
                INSERT INTO atlas.scene_nodes (scene_version_id, code, node_type, asset_version_id)
                VALUES (:scene, 'synthetic-pin', 'asset', :pin)
            """),
                {"scene": scene_version_id, "pin": first_id},
            )
            conn.execute(
                text("""
                UPDATE atlas.scene_versions SET publication_state = 'published',
                    spatial_review_reference = 'synthetic scene review' WHERE id = :id
            """),
                {"id": scene_version_id},
            )
        published = client.get(path)
        assert published.status_code == 200
        assert (
            "storage_reference" not in published.text
            and str(root) not in published.text
        )
        assert client.get(f"/api/v1/assets/{code}/versions/2").status_code == 404

        source.write_bytes(original)
        manifest(metadata, code)
        assert run_import(paths)["registration"]["version_id"] == first_id
        manifest(metadata, code, version="3")
        assert run_import(paths)["registration"]["outcome"] == "created"
        scene = client.get("/api/v1/scenes/ingestion-scene/versions/1/nodes")
        assert scene.json()["items"][0]["asset"] == {"code": code, "version": "1"}
        assert client.get(path).json() == published.json()


def test_rejected_validation_is_audited_without_registered_asset(database, paths):
    _config, engine = database
    source, metadata, root = paths
    manifest(metadata, "rejected-ingestion")
    source.write_bytes(b"not a GLB from any source")
    report = run_import(paths, status=1)
    assert report["validation_status"] == "rejected"
    assert report["registration"]["outcome"] == "not_registered"
    assert not (root / "sha256").exists()
    with engine(MIGRATION_ROLE).begin() as conn:
        conn.execute(text("SET LOCAL ROLE atlas_owner"))
        assert (
            conn.scalar(
                text(
                    "SELECT count(*) FROM atlas.assets WHERE code = 'rejected-ingestion'"
                )
            )
            == 0
        )
        assert (
            conn.scalar(
                text("""
            SELECT report#>>'{issues,0,code}' FROM atlas.asset_ingestions WHERE id = :id
        """),
                {"id": report["ingestion_id"]},
            )
            == "GLB_INTEGRITY"
        )


def test_warnings_are_preserved_for_editorial_review(database, paths):
    _config, engine = database
    source, metadata, _root = paths
    manifest(metadata, "warning-ingestion")
    payload = b'{"asset":{"version":"2.0","syntheticUnexpectedProperty":true}}'
    payload += b" " * (-len(payload) % 4)
    source.write_bytes(
        pack("<4sIII4s", b"glTF", 2, 20 + len(payload), len(payload), b"JSON") + payload
    )
    report = run_import(paths)
    assert any(issue["severity"] == 1 for issue in report["issues"])
    with engine(MIGRATION_ROLE).begin() as conn:
        conn.execute(text("SET LOCAL ROLE atlas_owner"))
        assert (
            conn.scalar(
                text("""
            SELECT report->'issues' FROM atlas.asset_ingestions WHERE id = :id
        """),
                {"id": report["ingestion_id"]},
            )
            == report["issues"]
        )


@pytest.mark.parametrize(
    ("role", "statement"),
    [
        (RUNTIME_ROLE, "SELECT * FROM atlas.asset_ingestions"),
        (RUNTIME_ROLE, "SELECT atlas.record_asset_ingestion(NULL, NULL, NULL)"),
        (INGESTION_ROLE, "SELECT * FROM atlas.asset_ingestions"),
        (INGESTION_ROLE, "INSERT INTO atlas.asset_ingestions DEFAULT VALUES"),
        (
            INGESTION_ROLE,
            "INSERT INTO atlas.assets (code, title, review_state) VALUES ('x', 'x', 'published')",
        ),
        (INGESTION_ROLE, "UPDATE atlas.assets SET review_state = 'published'"),
        (
            INGESTION_ROLE,
            "UPDATE atlas.asset_versions SET publication_state = 'published'",
        ),
        (INGESTION_ROLE, "UPDATE atlas.asset_versions SET rights_verified_at = now()"),
        (
            INGESTION_ROLE,
            "UPDATE atlas.asset_versions SET rights_review_reference = 'x'",
        ),
        (INGESTION_ROLE, "INSERT INTO atlas.asset_sources DEFAULT VALUES"),
        (INGESTION_ROLE, "DELETE FROM atlas.scene_nodes"),
        (INGESTION_ROLE, "CREATE TABLE atlas.escalation (id integer)"),
        (INGESTION_ROLE, "SET ROLE atlas_owner"),
        (INGESTION_ROLE, "SET ROLE atlas_migrator"),
    ],
)
def test_database_role_permissions(database, role, statement):
    _config, engine = database
    with engine(role).begin() as conn:
        with pytest.raises(ProgrammingError) as exc:
            conn.execute(text(statement))
        assert exc.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("point", ["before", "after"])
def test_retry_after_interrupted_database_commit(database, paths, point):
    _config, engine = database
    _source, metadata, root = paths
    code = f"crash-{point}"
    manifest(metadata, code)
    crash = """
import os
import sys
from sqlalchemy import event
from sqlalchemy.engine import Engine
from processing.ingestion import __main__ as cli

if sys.argv.pop(1) == "before":
    @event.listens_for(Engine, "commit")
    def crash_on_commit(_connection):
        os._exit(73)
else:
    register = cli.record_ingestion
    def crash_after_commit(*args):
        register(*args)
        os._exit(73)
    cli.record_ingestion = crash_after_commit

sys.exit(cli.main())
"""
    interrupted = invoke(paths, launch=["-c", crash, point])
    assert interrupted.returncode == 73, (interrupted.stdout, interrupted.stderr)
    (only_object,) = (root / "sha256").glob("*/*.glb")
    assert not list((root / ".quarantine").iterdir())
    with engine(MIGRATION_ROLE).begin() as conn:
        conn.execute(text("SET LOCAL ROLE atlas_owner"))
        assert conn.scalar(
            text(
                "SELECT count(*) FROM atlas.asset_ingestions WHERE asset_code = :code"
            ),
            {"code": code},
        ) == int(point == "after")

    result = run_import(paths)
    assert result["storage_outcome"] == "reused"
    assert only_object == root / result["storage_reference"]
    assert result["registration"]["outcome"] == (
        "created" if point == "before" else "reused"
    )


def test_concurrent_registration_returns_same_draft(database, paths, tmp_path):
    _source, metadata, _root = paths
    manifest(metadata, "parallel-ingestion")
    second_root = tmp_path / "alternate-objects"
    second_root.mkdir(mode=0o700)
    # Separate stores bypass the per-store validation lock to exercise the database race.
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run_import, paths)
        second = executor.submit(run_import, (*paths[:2], second_root))
        registrations = [result.result()["registration"] for result in (first, second)]
    assert {r["outcome"] for r in registrations} == {"created", "reused"}
    assert registrations[0]["version_id"] == registrations[1]["version_id"]


def test_ingestion_migration_roundtrip_preserves_registry(database, paths):
    config, engine = database
    _source, metadata, _root = paths
    manifest(metadata, "migration-ingestion")
    created = run_import(paths)
    exact_id = created["registration"]["version_id"]
    command.downgrade(config, "0004")
    with engine(MIGRATION_ROLE).begin() as conn:
        conn.execute(text("SET LOCAL ROLE atlas_owner"))
        assert conn.scalar(text("SELECT to_regclass('atlas.asset_ingestions')")) is None
        assert (
            conn.scalar(
                text(
                    "SELECT to_regprocedure('atlas.record_asset_ingestion(jsonb,text,jsonb)')"
                )
            )
            is None
        )
        assert (
            conn.scalar(
                text("SELECT id::text FROM atlas.asset_versions WHERE id = :id"),
                {"id": exact_id},
            )
            == exact_id
        )

    command.upgrade(config, "head")
    command.check(config)
    assert run_import(paths)["registration"] == {
        "outcome": "reused",
        "version_id": exact_id,
    }
