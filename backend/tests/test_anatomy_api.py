import pytest
from fastapi.testclient import TestClient

from atlas_api.config import RUNTIME_DATABASE_ENV
from atlas_api.main import create_app


@pytest.fixture
def client_without_database(monkeypatch):
    monkeypatch.delenv(RUNTIME_DATABASE_ENV, raising=False)
    with TestClient(create_app()) as client:
        yield client


@pytest.mark.parametrize(
    "path",
    [
        "/systems",
        "/systems/skeletal",
        "/structures",
        "/structures/left-femur",
        "/structures/left-femur/relations",
    ],
)
def test_anatomy_without_a_database_returns_503(client_without_database, path):
    response = client_without_database.get(f"/api/v1/anatomy{path}")
    assert response.status_code == 503
    assert response.json() == {"detail": "Anatomy database unavailable."}


@pytest.mark.parametrize(
    "path",
    [
        "/systems?limit=101",
        "/structures?offset=-1",
        "/structures?offset=9223372036854775808",
        "/structures?q=%00",
        "/structures?language=fr",
        "/structures?laterality=both",
        "/structures/Bad-code",
        "/structures/left-femur/relations?direction=forward",
    ],
)
def test_anatomy_rejects_invalid_inputs(client_without_database, path):
    assert client_without_database.get(f"/api/v1/anatomy{path}").status_code == 422


def test_anatomy_does_not_expose_write_methods(client_without_database):
    response = client_without_database.post("/api/v1/anatomy/structures", json={})
    assert response.status_code == 405
