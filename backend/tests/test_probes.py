import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from atlas_api.config import RUNTIME_DATABASE_ENV
from atlas_api.main import create_app

RUNTIME_URL = "postgresql+psycopg://atlas_app@localhost/atlas_db"
CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.v1.json"


def test_health_does_not_connect_to_database(monkeypatch):
    monkeypatch.setenv(RUNTIME_DATABASE_ENV, RUNTIME_URL)
    with (
        patch("sqlalchemy.Engine.connect") as connect,
        TestClient(create_app()) as client,
    ):
        response = client.get("/api/v1/health")

    connect.assert_not_called()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_without_database_configuration(monkeypatch):
    monkeypatch.delenv(RUNTIME_DATABASE_ENV, raising=False)
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_readiness_executes_a_database_query(monkeypatch):
    monkeypatch.setenv(RUNTIME_DATABASE_ENV, RUNTIME_URL)
    with (
        patch("sqlalchemy.Engine.connect") as connect,
        TestClient(create_app()) as client,
    ):
        connection = connect.return_value.__enter__.return_value
        connection.scalar.return_value = 1
        response = client.get("/api/v1/ready")

    connect.assert_called_once_with()
    connection.scalar.assert_called_once()
    assert str(connection.scalar.call_args.args[0]) == "SELECT 1"
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_hides_database_errors(monkeypatch):
    monkeypatch.setenv(RUNTIME_DATABASE_ENV, RUNTIME_URL)
    error = OperationalError(None, None, RuntimeError("internal database error"))
    with (
        patch("sqlalchemy.Engine.connect", side_effect=error),
        TestClient(create_app()) as client,
    ):
        readiness = client.get("/api/v1/ready")
        health = client.get("/api/v1/health")

    assert readiness.status_code == 503
    assert readiness.json() == {"status": "unavailable"}
    assert health.status_code == 200


def test_served_openapi_matches_contract(monkeypatch):
    monkeypatch.delenv(RUNTIME_DATABASE_ENV, raising=False)
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json() == json.loads(CONTRACT.read_text(encoding="utf-8"))
