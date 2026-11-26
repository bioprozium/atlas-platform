"""SQLAlchemy table definitions for the Human Anatomy domain."""

from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Enum,
    ForeignKey,
    Index,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    text,
)

from atlas_api.db import SCHEMA, metadata

CODE_PATTERN = r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$"
CODE_LENGTH = 80
NAME_LENGTH = 255


class StructureKind(StrEnum):
    BONE = "bone"
    ORGAN = "organ"
    ORGAN_PART = "organ_part"
    REGION = "region"
    TISSUE = "tissue"
    JOINT = "joint"
    AGGREGATE = "aggregate"


def uuid_primary_key() -> Column:
    return Column("id", Uuid, primary_key=True, server_default=text("gen_random_uuid()"))


anatomical_systems = Table(
    "anatomical_systems",
    metadata,
    uuid_primary_key(),
    Column("code", String(CODE_LENGTH), nullable=False),
    Column("name", String(NAME_LENGTH), nullable=False),
    Column("review_state", String(9), nullable=False, server_default="draft"),
    UniqueConstraint("code", name="uq_anatomical_systems_code"),
    CheckConstraint(f"code ~ '{CODE_PATTERN}'", name="ck_anatomical_systems_code"),
    CheckConstraint("name ~ '[^[:space:]]'", name="ck_anatomical_systems_name"),
    CheckConstraint(
        "review_state IN ('draft', 'published')", name="ck_anatomical_systems_review_state"
    ),
)

anatomical_structures = Table(
    "anatomical_structures",
    metadata,
    uuid_primary_key(),
    Column("code", String(CODE_LENGTH), nullable=False),
    Column(
        "structure_kind",
        Enum(
            StructureKind,
            values_callable=lambda kinds: [kind.value for kind in kinds],
            native_enum=False,
            create_constraint=True,
            name="ck_anatomical_structures_kind",
        ),
        nullable=False,
    ),
    Column("laterality", String(5), nullable=False),
    Column("review_state", String(9), nullable=False, server_default="draft"),
    UniqueConstraint("code", name="uq_anatomical_structures_code"),
    CheckConstraint(f"code ~ '{CODE_PATTERN}'", name="ck_anatomical_structures_code"),
    CheckConstraint("laterality IN ('left', 'right', 'none')", name="ck_structures_laterality"),
    CheckConstraint("review_state IN ('draft', 'published')", name="ck_structures_review_state"),
    Index("ix_anatomical_structures_laterality_code", "laterality", "code"),
)

structure_names = Table(
    "structure_names",
    metadata,
    uuid_primary_key(),
    Column(
        "structure_id",
        Uuid,
        ForeignKey(f"{SCHEMA}.anatomical_structures.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("language", String(2), nullable=False),
    Column("name", String(NAME_LENGTH), nullable=False),
    Column("is_preferred", Boolean, nullable=False, server_default=text("false")),
    CheckConstraint("language IN ('la', 'en', 'az')", name="ck_structure_names_language"),
    CheckConstraint("name ~ '[^[:space:]]'", name="ck_structure_names_name"),
    UniqueConstraint("structure_id", "language", "name", name="uq_structure_names_name"),
    Index(
        "uq_structure_names_preferred",
        "structure_id",
        "language",
        unique=True,
        postgresql_where=text("is_preferred"),
    ),
)

structure_systems = Table(
    "structure_systems",
    metadata,
    uuid_primary_key(),
    Column(
        "structure_id",
        Uuid,
        ForeignKey(f"{SCHEMA}.anatomical_structures.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "system_id",
        Uuid,
        ForeignKey(f"{SCHEMA}.anatomical_systems.id", ondelete="CASCADE"),
        nullable=False,
    ),
    UniqueConstraint("structure_id", "system_id", name="uq_structure_systems_membership"),
    Index("ix_structure_systems_system_structure", "system_id", "structure_id"),
)

structure_relations = Table(
    "structure_relations",
    metadata,
    uuid_primary_key(),
    Column(
        "source_id",
        Uuid,
        ForeignKey(f"{SCHEMA}.anatomical_structures.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "target_id",
        Uuid,
        ForeignKey(f"{SCHEMA}.anatomical_structures.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("relation_type", String(7), nullable=False),
    CheckConstraint("relation_type IN ('part_of', 'is_a')", name="ck_structure_relations_type"),
    CheckConstraint("source_id <> target_id", name="ck_structure_relations_distinct_nodes"),
    UniqueConstraint("source_id", "target_id", "relation_type", name="uq_structure_relations_edge"),
    Index("ix_structure_relations_target_source", "target_id", "source_id"),
)

external_identifiers = Table(
    "external_identifiers",
    metadata,
    uuid_primary_key(),
    Column(
        "structure_id",
        Uuid,
        ForeignKey(f"{SCHEMA}.anatomical_structures.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("namespace", String(80), nullable=False),
    Column("value", String(255), nullable=False),
    Column("source", String(512), nullable=False),
    Column("source_version", String(120)),
    CheckConstraint(
        "namespace ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+)*$'",
        name="ck_external_identifiers_namespace",
    ),
    CheckConstraint("value ~ '[^[:space:]]'", name="ck_external_identifiers_value"),
    CheckConstraint("source ~ '[^[:space:]]'", name="ck_external_identifiers_source"),
    CheckConstraint(
        "source_version ~ '[^[:space:]]'", name="ck_external_identifiers_source_version"
    ),
    UniqueConstraint("structure_id", "namespace", "value", name="uq_external_identifiers_mapping"),
    Index("ix_external_identifiers_namespace_value", "namespace", "value"),
)

# Updating this singleton serializes hierarchy changes, even at stricter isolation levels.
hierarchy_write_guard = Table(
    "anatomy_hierarchy_write_guard",
    metadata,
    Column("id", Uuid, primary_key=True),
    CheckConstraint(
        "id = '00000000-0000-0000-0000-000000000000'",
        name="ck_anatomy_hierarchy_write_guard_singleton",
    ),
)
