import pytest

from atlas_api.config import RUNTIME_DATABASE_ENV, RUNTIME_ROLE, read_database_url


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://atlas_app@localhost/atlas_db",
        "postgresql+psycopg://atlas_migrator@localhost/atlas_db",
        "postgresql+psycopg://atlas_app@localhost/other_db",
        "postgresql+psycopg://atlas_app@localhost/atlas_db?user=atlas_owner",
        "postgresql+psycopg://atlas_app@localhost/atlas_db?dbname=other_db",
        "postgresql+psycopg://atlas_app@localhost/atlas_db?service=other",
    ],
)
def test_database_url_must_use_expected_driver_role_and_database(monkeypatch, url):
    monkeypatch.setenv(RUNTIME_DATABASE_ENV, url)
    with pytest.raises(ValueError, match=RUNTIME_DATABASE_ENV):
        read_database_url(RUNTIME_DATABASE_ENV, RUNTIME_ROLE)


def test_invalid_url_is_omitted_from_error(monkeypatch):
    value = "invalid-database-url"
    monkeypatch.setenv(RUNTIME_DATABASE_ENV, value)
    with pytest.raises(ValueError, match=RUNTIME_DATABASE_ENV) as error:
        read_database_url(RUNTIME_DATABASE_ENV, RUNTIME_ROLE)

    assert value not in str(error.value)
    assert error.value.__suppress_context__
