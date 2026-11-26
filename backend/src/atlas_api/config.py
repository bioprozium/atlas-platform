import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from atlas_api.assets.validation import ASSET_ROOT

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


@dataclass(frozen=True)
class DeliverySettings:
    storage_root: Path
    max_nodes: int
    max_assets: int
    max_manifest_bytes: int
    manifest_slots: int
    file_slots: int
    file_send_timeout: int

    @classmethod
    def from_environment(cls) -> "DeliverySettings":
        configured = os.environ.get("ATLAS_ASSET_STORAGE_ROOT", str(ASSET_ROOT))
        root = Path(configured)
        if (
            root.anchor != "/"
            or root == Path("/")
            or ".." in root.parts
            or str(root) != configured
            or any(ord(char) < 32 or ord(char) == 127 for char in configured)
        ):
            raise ValueError("ATLAS_ASSET_STORAGE_ROOT must identify a safe absolute directory.")
        return cls(
            storage_root=root,
            max_nodes=_bounded_integer("ATLAS_MANIFEST_MAX_NODES", 2048, 10000),
            max_assets=_bounded_integer("ATLAS_MANIFEST_MAX_ASSETS", 256, 1024),
            max_manifest_bytes=_bounded_integer("ATLAS_MANIFEST_MAX_BYTES", 2**20, 8 * 2**20),
            manifest_slots=_bounded_integer("ATLAS_MANIFEST_SLOTS", 1, 8),
            file_slots=_bounded_integer("ATLAS_DELIVERY_FILE_SLOTS", 2, 8),
            file_send_timeout=_bounded_integer("ATLAS_DELIVERY_SEND_TIMEOUT_SECONDS", 120, 900),
        )


def _bounded_integer(key: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(key, str(default)))
    except ValueError:
        value = 0
    if not 1 <= value <= maximum:
        raise ValueError(f"{key} must be an integer between 1 and {maximum}.")
    return value
