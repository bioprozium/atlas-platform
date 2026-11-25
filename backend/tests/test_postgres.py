import os
import socket
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
    read_database_url,
)
from atlas_api.main import create_app

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def configure_test_database(monkeypatch, env, role):
    test_env = env.replace("ATLAS_", "ATLAS_TEST_", 1)
    if not os.environ.get(test_env):
        pytest.skip(f"Set {test_env} to use a disposable PostgreSQL 16 database.")
    monkeypatch.setenv(env, os.environ[test_env])
    return read_database_url(env, role)


def test_runtime_readiness_with_postgres(monkeypatch):
    url = configure_test_database(monkeypatch, RUNTIME_DATABASE_ENV, RUNTIME_ROLE)
    with create_engine(url, poolclass=NullPool).connect() as connection:
        assert connection.scalar(text("SELECT current_user")) == RUNTIME_ROLE
        version = connection.scalar(text("SHOW server_version_num"))
        assert 160000 <= int(version) < 170000

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_runtime_readiness_with_unavailable_postgres(monkeypatch):
    with socket.socket() as reserved_port:
        reserved_port.bind(("127.0.0.1", 0))
        host, port = reserved_port.getsockname()
        monkeypatch.setenv(
            RUNTIME_DATABASE_ENV,
            f"postgresql+psycopg://atlas_app@{host}:{port}/atlas_db",
        )
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/ready")
            health = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert health.status_code == 200


def test_migrations_use_owner_role_and_atlas_schema(monkeypatch):
    url = configure_test_database(monkeypatch, MIGRATION_DATABASE_ENV, MIGRATION_ROLE)
    config = Config(str(ALEMBIC_INI))

    with create_engine(url, poolclass=NullPool).connect() as connection:
        version = connection.scalar(text("SHOW server_version_num"))
        assert 160000 <= int(version) < 170000
        identity = connection.execute(
            text(
                "SELECT session_user, current_user, "
                "has_schema_privilege(current_user, 'atlas', 'CREATE')"
            )
        ).one()
        assert identity == ("atlas_migrator", "atlas_migrator", False)

    command.upgrade(config, "head")
    command.check(config)

    with create_engine(url, poolclass=NullPool).connect() as connection:
        locations = connection.execute(
            text(
                "SELECT schemaname, tableowner FROM pg_tables WHERE tablename = 'alembic_version'"
            )
        ).all()
        assert locations == [("atlas", "atlas_owner")]

        connection.exec_driver_sql("SET ROLE atlas_owner")
        revision = connection.scalar(
            text("SELECT version_num FROM atlas.alembic_version")
        )
        assert revision == ScriptDirectory.from_config(config).get_current_head()
