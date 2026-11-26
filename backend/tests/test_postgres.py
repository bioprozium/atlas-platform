from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from atlas_api.config import (
    MIGRATION_DATABASE_ENV,
    MIGRATION_ROLE,
    RUNTIME_DATABASE_ENV,
    RUNTIME_ROLE,
)
from atlas_api.main import create_app

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[1]
ANATOMY_TABLES = {
    "anatomical_systems",
    "anatomical_structures",
    "structure_names",
    "structure_systems",
    "structure_relations",
    "external_identifiers",
}
REGISTRY_TABLES = {"assets", "asset_versions", "asset_sources", "anatomy_asset_links"}
SCENE_TABLES = {"scenes", "scene_versions", "scene_nodes", "scene_node_anatomy_links"}
REGISTRY_FUNCTIONS = [
    "preserve_asset_identity",
    "preserve_asset_version",
    "invalidate_asset_rights_review",
]
SCENE_FUNCTIONS = [
    "scene_vector_is_finite",
    "scene_quaternion_is_unit",
    "preserve_scene_identity",
    "check_scene_version_state",
    "guard_scene_graph_edit",
    "check_scene_hierarchy",
]


def test_runtime_readiness_with_postgres(monkeypatch, postgres_url):
    url = postgres_url(RUNTIME_ROLE)
    monkeypatch.setenv(RUNTIME_DATABASE_ENV, url.render_as_string())
    with create_engine(url, poolclass=NullPool).connect() as connection:
        assert connection.scalar(text("SELECT current_user")) == RUNTIME_ROLE
        version = connection.scalar(text("SHOW server_version_num"))
        assert 160000 <= int(version) < 170000
        assert connection.scalar(text("SHOW listen_addresses")) == ""

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_runtime_readiness_with_unavailable_postgres(monkeypatch, postgres_url):
    url = postgres_url(RUNTIME_ROLE)
    url = url.update_query_dict({"host": url.query["host"] + "/missing"})
    monkeypatch.setenv(RUNTIME_DATABASE_ENV, url.render_as_string())
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/ready")
        health = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert health.status_code == 200


def test_migrations_use_owner_role_and_atlas_schema(monkeypatch, postgres_url):
    url = postgres_url(MIGRATION_ROLE)
    monkeypatch.setenv(MIGRATION_DATABASE_ENV, url.render_as_string())
    config = Config(str(BACKEND / "alembic.ini"))
    tables = sorted(
        ANATOMY_TABLES
        | REGISTRY_TABLES
        | SCENE_TABLES
        | {"anatomy_hierarchy_write_guard", "alembic_version"}
    )

    with create_engine(url, poolclass=NullPool).connect() as connection:
        identity = connection.execute(
            text(
                "SELECT session_user, current_user, "
                "has_schema_privilege(current_user, 'atlas', 'CREATE')"
            )
        ).one()
        assert identity == ("atlas_migrator", "atlas_migrator", False)
        owners = connection.execute(
            text(
                "SELECT (SELECT pg_get_userbyid(datdba) FROM pg_database "
                "WHERE datname = current_database()), "
                "(SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'atlas')"
            )
        ).one()
        assert owners == ("atlas_owner", "atlas_owner")

    runtime_url = postgres_url(RUNTIME_ROLE)
    monkeypatch.setenv(RUNTIME_DATABASE_ENV, runtime_url.render_as_string())
    command.upgrade(config, "0001")
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/ready").status_code == 200
        unavailable = client.get("/api/v1/anatomy/systems")
        assert unavailable.status_code == 503
        assert unavailable.json() == {"detail": "Anatomy database unavailable."}

    command.upgrade(config, "0002")
    with create_engine(url, poolclass=NullPool).begin() as connection:
        connection.execute(text("SET LOCAL ROLE atlas_owner"))
        connection.execute(
            text(
                "INSERT INTO atlas.anatomical_structures "
                "(code, structure_kind, laterality, review_state) "
                "VALUES ('migration-witness', 'bone', 'none', 'published')"
            )
        )
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/assets").status_code == 503

    command.upgrade(config, "0003")
    with create_engine(url, poolclass=NullPool).begin() as connection:
        connection.execute(text("SET LOCAL ROLE atlas_owner"))
        connection.execute(
            text("INSERT INTO atlas.assets (code, title) VALUES ('migration-asset', 'Synthetic')")
        )
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/scenes").status_code == 503
        assert client.get("/api/v1/assets").json()["items"] == []

    command.upgrade(config, "0004")
    command.check(config)

    schema_functions = text(
        "SELECT proname, pg_get_userbyid(proowner) FROM pg_proc "
        "JOIN pg_namespace ON pronamespace = pg_namespace.oid "
        "WHERE nspname = 'atlas' AND proname = ANY(:names)"
    )
    with create_engine(url, poolclass=NullPool).connect() as connection:
        locations = connection.execute(
            text(
                "SELECT tablename, schemaname, tableowner FROM pg_tables "
                "WHERE tablename = ANY(:tables)"
            ),
            {"tables": tables},
        ).all()
        assert set(locations) == {(table, "atlas", "atlas_owner") for table in tables}
        names = REGISTRY_FUNCTIONS + SCENE_FUNCTIONS
        functions = connection.execute(schema_functions, {"names": names}).all()
        assert set(functions) == {(name, "atlas_owner") for name in names}

    with create_engine(runtime_url, poolclass=NullPool).connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM atlas.alembic_version"))
        assert revision == ScriptDirectory.from_config(config).get_current_head() == "0004"
        access = connection.execute(
            text(
                "SELECT has_schema_privilege(current_user, 'atlas', 'CREATE'), "
                "has_table_privilege(current_user, 'atlas.alembic_version', "
                "'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')"
            )
        ).one()
        assert access == (False, False)
        for table in ANATOMY_TABLES | REGISTRY_TABLES | SCENE_TABLES:
            access = connection.execute(
                text(
                    "SELECT has_table_privilege(current_user, :table, 'SELECT'), "
                    "has_table_privilege(current_user, :table, "
                    "'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')"
                ),
                {"table": f"atlas.{table}"},
            ).one()
            assert access == (True, False)
            assert connection.scalar(text(f"SELECT count(*) FROM atlas.{table}")) == (
                1 if table in {"anatomical_structures", "assets"} else 0
            )
        assert not connection.scalar(
            text("SELECT has_table_privilege('atlas.anatomy_hierarchy_write_guard', 'SELECT')")
        )

    path = "/api/v1/anatomy/structures/migration-witness"
    with TestClient(create_app()) as client:
        assert client.get(f"{path}/assets").json()["items"] == []
        assert client.get("/api/v1/scenes").json()["items"] == []
        with create_engine(url, poolclass=NullPool).begin() as connection:
            connection.execute(text("SET LOCAL ROLE atlas_owner"))
            connection.execute(
                text(
                    "INSERT INTO atlas.scenes (code, title) VALUES ('migration-scene', 'Synthetic')"
                )
            )
        command.downgrade(config, "0003")
        assert client.get("/api/v1/scenes").status_code == 503
        assert client.get(f"{path}/assets").json()["items"] == []
        with create_engine(runtime_url, poolclass=NullPool).connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM atlas.alembic_version")) == "0003"
            )
            assert connection.scalar(text("SELECT code FROM atlas.assets")) == "migration-asset"
            assert connection.execute(schema_functions, {"names": SCENE_FUNCTIONS}).all() == []
            assert (
                connection.scalar(
                    text("SELECT to_regclass('atlas.uq_anatomy_asset_links_scene_pin')")
                )
                is None
            )
            remaining = connection.execute(
                text("SELECT tablename FROM pg_tables WHERE tablename = ANY(:tables)"),
                {"tables": sorted(SCENE_TABLES)},
            ).all()
            assert remaining == []
        command.upgrade(config, "0004")
        command.check(config)
        with create_engine(runtime_url, poolclass=NullPool).connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM atlas.scenes")) == 0
            assert connection.scalar(text("SELECT code FROM atlas.assets")) == "migration-asset"

        command.downgrade(config, "0002")
        assert client.get("/api/v1/assets").status_code == 503
        assert client.get(path).json()["code"] == "migration-witness"
        with create_engine(runtime_url, poolclass=NullPool).connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM atlas.alembic_version"))
            assert revision == "0002"
            remaining = connection.execute(
                text("SELECT tablename FROM pg_tables WHERE tablename = ANY(:tables)"),
                {"tables": sorted(REGISTRY_TABLES)},
            ).all()
            assert remaining == []
            assert connection.execute(schema_functions, {"names": REGISTRY_FUNCTIONS}).all() == []
        command.upgrade(config, "0004")
        assert client.get("/api/v1/assets").json()["items"] == []
        assert client.get(f"{path}/assets").json()["items"] == []
        command.check(config)

    command.downgrade(config, "0001")
    with create_engine(runtime_url, poolclass=NullPool).connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM atlas.alembic_version")) == "0001"
        assert connection.execute(
            text("SELECT tablename FROM pg_tables WHERE tablename = ANY(:tables)"),
            {"tables": tables},
        ).scalars().all() == ["alembic_version"]
        functions = connection.execute(
            text(
                "SELECT to_regprocedure('atlas.check_anatomy_hierarchy()'), "
                "to_regprocedure('atlas.reject_anatomy_code_change()')"
            )
        ).one()
        assert functions == (None, None)

    command.upgrade(config, "head")
    command.check(config)
