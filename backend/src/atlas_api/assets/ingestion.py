"""Database boundary for the offline draft asset importer."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    Uuid,
    bindparam,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

from atlas_api.config import read_database_url
from atlas_api.db import metadata

INGESTION_DATABASE_ENV = "ATLAS_INGESTION_DATABASE_URL"
INGESTION_ROLE = "atlas_ingest"

ingestion_storage = Table(
    "ingestion_storage",
    metadata,
    Column("singleton", Boolean, primary_key=True, server_default=text("true")),
    Column("storage_root", Text, nullable=False),
    CheckConstraint("singleton"),
    CheckConstraint(
        "storage_root ~ '^/[^/]+(/[^/]+)*$' "
        "AND storage_root !~ '(^|/)[.]{1,2}(/|$)' "
        "AND storage_root !~ '[[:cntrl:]]'"
    ),
)

asset_ingestions = Table(
    "asset_ingestions",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("actor", Text, server_default=text("session_user"), nullable=False),
    Column("asset_code", String(80), nullable=False),
    Column("version", String(80), nullable=False),
    Column("sha256", String(64)),
    Column("version_id", Uuid, ForeignKey("atlas.asset_versions.id")),
    Column("validation_status", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column(
        "recorded_at",
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    ),
    Column("report", JSONB, nullable=False),
    CheckConstraint("asset_code ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'"),
    CheckConstraint("version ~ '^[a-z0-9]+([._-][a-z0-9]+)*$'"),
    CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'"),
    CheckConstraint("validation_status IN ('passed', 'rejected', 'error')"),
    CheckConstraint(
        "outcome IN ('created', 'reused', 'content_conflict', 'metadata_conflict', 'not_registered')"
    ),
    CheckConstraint("octet_length(report::text) <= 1048576"),
    Index(
        "ix_asset_ingestions_created_version",
        "version_id",
        unique=True,
        postgresql_where=text("outcome = 'created'"),
    ),
)

REGISTER = text(
    """
    SELECT outcome, version_id
    FROM atlas.record_asset_ingestion(:storage_root, :report, :title, :sources)
    """
).bindparams(bindparam("report", type_=JSONB), bindparam("sources", type_=JSONB))


@dataclass(frozen=True)
class Registration:
    outcome: str
    version_id: UUID | None


class UnconfiguredRegistryError(ValueError):
    """The database owner has yet to bind the registry to a storage root."""


@dataclass(frozen=True)
class IngestionRegistry:
    connection: Connection
    storage_root: Path

    def record_ingestion(
        self, storage_root: Path, report: dict, title: str, sources: list[dict]
    ) -> Registration:
        row = self.connection.execute(
            REGISTER,
            {
                "storage_root": str(storage_root),
                "report": report,
                "title": title,
                "sources": sources,
            },
        ).one()
        return Registration(row.outcome, row.version_id)


@contextmanager
def ingestion_registry() -> Iterator[IngestionRegistry]:
    """Keep the database-owned storage binding locked through promotion and registration."""
    url = read_database_url(INGESTION_DATABASE_ENV, INGESTION_ROLE)
    if url is None:
        raise ValueError(f"Set {INGESTION_DATABASE_ENV} for draft imports.")

    engine = create_engine(
        url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=15000"},
    )
    try:
        with engine.begin() as connection:
            root = connection.scalar(text("SELECT atlas.ingestion_storage_root()"))
            if root is None:
                raise UnconfiguredRegistryError
            yield IngestionRegistry(connection, Path(root))
    finally:
        engine.dispose()
