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
    "path", ["", "/sample", "/sample/versions/v1", "/sample/versions/v1/nodes"]
)
def test_scene_api_requires_database(client, path):
    response = client.get(f"/api/v1/scenes{path}")
    assert response.status_code == 503
    assert response.json() == {"detail": "Scene database unavailable."}


@pytest.mark.parametrize(
    "path",
    [
        "?limit=0",
        "?offset=9223372036854775808",
        "/Invalid",
        "/sample?limit=101",
        "/sample/versions/v..1",
        "/sample/versions/v1/nodes?offset=-1",
    ],
)
def test_scene_path_and_pagination_validation(client, path):
    response = client.get(f"/api/v1/scenes{path}")
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


def test_scenes_have_no_public_write_routes(client):
    for path in ("", "/sample", "/sample/versions/v1", "/sample/versions/v1/nodes"):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            assert client.request(method, f"/api/v1/scenes{path}").status_code == 405
