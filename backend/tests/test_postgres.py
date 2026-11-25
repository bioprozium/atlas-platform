import os
import shlex
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from atlas_api.config import (
    DATABASE_NAME,
    MIGRATION_DATABASE_ENV,
    MIGRATION_ROLE,
    RUNTIME_DATABASE_ENV,
    RUNTIME_ROLE,
)
from atlas_api.main import create_app

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[1]


def run_postgres(*args: str) -> str:
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if result.returncode:
        pytest.fail(f"{args[0]} failed.\n{result.stdout}{result.stderr}", pytrace=False)
    return result.stdout


@pytest.fixture(scope="module")
def postgres_socket():
    if any(shutil.which(tool) is None for tool in ("initdb", "pg_ctl", "psql")):
        pytest.skip("Put PostgreSQL 16 server tools on PATH to run integration tests.")
    if "pg_ctl (PostgreSQL) 16." not in run_postgres("pg_ctl", "--version"):
        pytest.skip("The integration tests require PostgreSQL 16.")

    with (
        TemporaryDirectory(prefix="atlas-pg-", dir="/tmp") as directory,
        pytest.MonkeyPatch.context() as env,
    ):
        # libpq can inherit a host address or service even with an explicit socket host.
        for name in tuple(os.environ):
            if name.startswith("PG"):
                env.delenv(name)

        data = Path(directory) / "data"
        run_postgres(
            "initdb",
            "-D",
            str(data),
            "-U",
            "atlas_test_admin",
            "--locale=C",
            "--encoding=UTF8",
            "--auth-local=trust",
            "--auth-host=scram-sha-256",
        )
        try:
            run_postgres(
                "pg_ctl",
                "-D",
                str(data),
                "-l",
                str(Path(directory) / "postgres.log"),
                "-o",
                f"-h '' -k {shlex.quote(directory)} -p 5432",
                "-w",
                "start",
            )
            run_postgres(
                "psql",
                "-X",
                "-h",
                directory,
                "-p",
                "5432",
                "-U",
                "atlas_test_admin",
                "-d",
                "postgres",
                "-f",
                str(BACKEND / "provision_local.sql"),
            )
            yield Path(directory)
        finally:
            if (data / "postmaster.pid").exists():
                run_postgres("pg_ctl", "-D", str(data), "-m", "fast", "-w", "stop")


def socket_database_url(socket_dir: Path, role: str) -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=role,
        database=DATABASE_NAME,
        port=5432,
        query={"host": str(socket_dir)},
    )


def test_runtime_readiness_with_postgres(monkeypatch, postgres_socket):
    url = socket_database_url(postgres_socket, RUNTIME_ROLE)
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


def test_runtime_readiness_with_unavailable_postgres(monkeypatch, postgres_socket):
    url = socket_database_url(postgres_socket / "missing", RUNTIME_ROLE)
    monkeypatch.setenv(RUNTIME_DATABASE_ENV, url.render_as_string())
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/ready")
        health = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert health.status_code == 200


def test_migrations_use_owner_role_and_atlas_schema(monkeypatch, postgres_socket):
    url = socket_database_url(postgres_socket, MIGRATION_ROLE)
    monkeypatch.setenv(MIGRATION_DATABASE_ENV, url.render_as_string())
    config = Config(str(BACKEND / "alembic.ini"))

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

    command.upgrade(config, "head")
    command.check(config)

    with create_engine(url, poolclass=NullPool).connect() as connection:
        locations = connection.execute(
            text(
                "SELECT schemaname, tableowner FROM pg_tables WHERE tablename = 'alembic_version'"
            )
        ).all()
        assert locations == [("atlas", "atlas_owner")]

    runtime_url = socket_database_url(postgres_socket, RUNTIME_ROLE)
    with create_engine(runtime_url, poolclass=NullPool).connect() as connection:
        revision = connection.scalar(
            text("SELECT version_num FROM atlas.alembic_version")
        )
        assert revision == ScriptDirectory.from_config(config).get_current_head()
        access = connection.execute(
            text(
                "SELECT has_schema_privilege(current_user, 'atlas', 'CREATE'), "
                "has_table_privilege(current_user, 'atlas.alembic_version', "
                "'INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')"
            )
        ).one()
        assert access == (False, False)
