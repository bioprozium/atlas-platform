"""SQLAlchemy table definitions for the 3D Asset Registry."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)

from atlas_api.anatomy.models import (
    CODE_LENGTH,
    CODE_PATTERN,
    NAME_LENGTH,
    uuid_primary_key,
)
from atlas_api.assets.validation import GLB_MAX_SIZE, GLB_MIN_SIZE, SHA256_PATTERN
from atlas_api.db import SCHEMA, metadata

VERSION_LENGTH = 80
VERSION_PATTERN = r"^[a-z0-9]+([._-][a-z0-9]+)*$"

assets = Table(
    "assets",
    metadata,
    uuid_primary_key(),
    Column("code", String(CODE_LENGTH), nullable=False),
    Column("title", String(NAME_LENGTH), nullable=False),
    Column("review_state", String(9), nullable=False, server_default="draft"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("code", name="uq_assets_code"),
    CheckConstraint(f"code ~ '{CODE_PATTERN}'", name="ck_assets_code"),
    CheckConstraint("title ~ '[^[:space:]]'", name="ck_assets_title"),
    CheckConstraint(
        "review_state IN ('draft', 'published', 'withdrawn')", name="ck_assets_review_state"
    ),
)

asset_versions = Table(
    "asset_versions",
    metadata,
    uuid_primary_key(),
    Column("asset_id", Uuid, ForeignKey(f"{SCHEMA}.assets.id"), nullable=False),
    Column("version", String(VERSION_LENGTH), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("file_size", BigInteger, nullable=False),
    Column("format", String(3), nullable=False),
    Column("mime_type", String(40), nullable=False),
    Column("storage_reference", String(80), nullable=False),
    Column("validation_state", String(7), nullable=False, server_default="pending"),
    Column("publication_state", String(9), nullable=False, server_default="draft"),
    Column("rights_verified_at", DateTime(timezone=True)),
    Column("rights_review_reference", Text),
    Column("primary_source_id", Uuid),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["id", "primary_source_id"],
        [f"{SCHEMA}.asset_sources.version_id", f"{SCHEMA}.asset_sources.id"],
        name="fk_asset_versions_primary_source",
        use_alter=True,
    ),
    UniqueConstraint("asset_id", "version", name="uq_asset_versions_identifier"),
    CheckConstraint(f"version ~ '{VERSION_PATTERN}'", name="ck_asset_versions_identifier"),
    CheckConstraint(f"sha256 ~ '{SHA256_PATTERN}'", name="ck_asset_versions_sha256"),
    CheckConstraint(
        f"file_size BETWEEN {GLB_MIN_SIZE} AND {GLB_MAX_SIZE} AND mod(file_size, 4) = 0",
        name="ck_asset_versions_size",
    ),
    CheckConstraint(
        "format = 'glb' AND mime_type = 'model/gltf-binary'", name="ck_asset_versions_glb"
    ),
    CheckConstraint(
        "storage_reference = 'sha256/' || left(sha256, 2) || '/' || sha256 || '.glb'",
        name="ck_asset_versions_storage_reference",
    ),
    CheckConstraint(
        "validation_state IN ('pending', 'valid', 'invalid')",
        name="ck_asset_versions_validation",
    ),
    CheckConstraint(
        "publication_state IN ('draft', 'published', 'withdrawn')",
        name="ck_asset_versions_publication",
    ),
    CheckConstraint(
        "rights_review_reference ~ '[^[:space:]]'", name="ck_asset_versions_rights_reference"
    ),
    CheckConstraint(
        "publication_state <> 'published' OR (validation_state = 'valid' "
        "AND rights_verified_at IS NOT NULL AND rights_review_reference IS NOT NULL "
        "AND primary_source_id IS NOT NULL)",
        name="ck_asset_versions_publishable",
    ),
    Index(
        "ix_asset_versions_listing", "asset_id", "publication_state", "created_at", "version"
    ),
)

asset_sources = Table(
    "asset_sources",
    metadata,
    uuid_primary_key(),
    Column("version_id", Uuid, ForeignKey(f"{SCHEMA}.asset_versions.id"), nullable=False),
    Column("creator", Text, nullable=False),
    Column("rights_holder", Text, nullable=False),
    Column("source_reference", Text, nullable=False),
    Column("spdx_license_id", String(120)),
    Column("license_reference", Text, nullable=False),
    Column("attribution", Text, nullable=False),
    Column("modifications", Text, nullable=False),
    UniqueConstraint("version_id", "id", name="uq_asset_sources_version_source"),
    CheckConstraint(
        "creator ~ '[^[:space:]]' AND rights_holder ~ '[^[:space:]]' "
        "AND source_reference ~ '[^[:space:]]' AND license_reference ~ '[^[:space:]]' "
        "AND attribution ~ '[^[:space:]]' AND modifications ~ '[^[:space:]]'",
        name="ck_asset_sources_required_text",
    ),
    CheckConstraint(
        "spdx_license_id ~ '^[a-zA-Z0-9][a-zA-Z0-9.+-]*$'", name="ck_asset_sources_spdx"
    ),
)

anatomy_asset_links = Table(
    "anatomy_asset_links",
    metadata,
    uuid_primary_key(),
    Column(
        "structure_id", Uuid, ForeignKey(f"{SCHEMA}.anatomical_structures.id"), nullable=False
    ),
    Column("version_id", Uuid, ForeignKey(f"{SCHEMA}.asset_versions.id"), nullable=False),
    Column("role", String(CODE_LENGTH)),
    UniqueConstraint("structure_id", "version_id", name="uq_anatomy_asset_links_pair"),
    UniqueConstraint("id", "version_id", "structure_id", name="uq_anatomy_asset_links_scene_pin"),
    CheckConstraint(f"role ~ '{CODE_PATTERN}'", name="ck_anatomy_asset_links_role"),
    Index("ix_anatomy_asset_links_version", "version_id"),
)
