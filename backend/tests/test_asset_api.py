import pytest
from fastapi.testclient import TestClient

from atlas_api.config import RUNTIME_DATABASE_ENV
from atlas_api.main import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv(RUNTIME_DATABASE_ENV, raising=False)
    with TestClient(create_app()) as client:
        yield client


@pytest.mark.parametrize(
    "path",
    [
        "/assets",
        "/assets/femur",
        "/assets/femur/versions/v1",
        "/anatomy/structures/femur/assets",
    ],
)
def test_registry_requires_a_database(client, path):
    response = client.get(f"/api/v1{path}")
    assert response.status_code == 503
    assert response.json() == {"detail": "Asset registry database unavailable."}


@pytest.mark.parametrize(
    "path",
    [
        "/assets?limit=0",
        "/assets?limit=101",
        "/assets?offset=-1",
        "/assets?offset=9223372036854775808",
        "/assets?q=%00",
        "/assets/Invalid",
        "/assets/femur?limit=101",
        "/assets/femur/versions/bad..version",
        "/anatomy/structures/Invalid/assets",
        "/anatomy/structures/femur/assets?offset=-1",
    ],
)
def test_invalid_registry_arguments(client, path):
    response = client.get(f"/api/v1{path}")
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
