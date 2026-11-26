"""Database boundary for the offline draft asset importer."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import (
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
from sqlalchemy.pool import NullPool

from atlas_api.config import read_database_url
from atlas_api.db import metadata

INGESTION_DATABASE_ENV = "ATLAS_INGESTION_DATABASE_URL"
INGESTION_ROLE = "atlas_ingest"

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
    FROM atlas.record_asset_ingestion(:report, :title, :sources)
    """
).bindparams(bindparam("report", type_=JSONB), bindparam("sources", type_=JSONB))


@dataclass(frozen=True)
class Registration:
    outcome: str
    version_id: UUID | None


def record_ingestion(report: dict, title: str, sources: list[dict]) -> Registration:
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
            row = connection.execute(
                REGISTER, {"report": report, "title": title, "sources": sources}
            ).one()
        return Registration(row.outcome, row.version_id)
    finally:
        engine.dispose()
