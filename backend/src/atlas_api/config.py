import os

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

RUNTIME_DATABASE_ENV = "ATLAS_DATABASE_URL"
MIGRATION_DATABASE_ENV = "ATLAS_MIGRATION_DATABASE_URL"
DATABASE_NAME = "atlas_db"
RUNTIME_ROLE = "atlas_app"
MIGRATION_ROLE = "atlas_migrator"


def read_database_url(variable: str, role: str) -> URL | None:
    value = os.environ.get(variable)
    if not value:
        return None

    message = (
        f"{variable} must be a postgresql+psycopg URL for {role} on {DATABASE_NAME}."
    )
    try:
        url = make_url(value)
    except (ArgumentError, ValueError):
        raise ValueError(message) from None

    if (
        url.drivername != "postgresql+psycopg"
        or url.username != role
        or url.database != DATABASE_NAME
        or {"user", "dbname", "service"}.intersection(url.query)
    ):
        raise ValueError(message)
    return url
