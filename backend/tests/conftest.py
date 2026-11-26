"""Disposable PostgreSQL 16 cluster for backend integration tests."""

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from sqlalchemy.engine import URL

from atlas_api.config import DATABASE_NAME

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


@pytest.fixture(scope="module")
def postgres_url(postgres_socket):
    def url_for_role(role: str) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=role,
            database=DATABASE_NAME,
            port=5432,
            query={"host": str(postgres_socket)},
        )

    return url_for_role
