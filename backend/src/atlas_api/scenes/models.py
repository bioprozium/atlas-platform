"""SQLAlchemy table definitions for versioned scene graphs."""

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Table,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.engine import Dialect

from atlas_api.anatomy.models import CODE_LENGTH, CODE_PATTERN, NAME_LENGTH, uuid_primary_key
from atlas_api.assets.models import VERSION_LENGTH, VERSION_PATTERN
from atlas_api.db import SCHEMA, metadata

COORDINATE_PROFILE = "atlas-anatomy-y-up-v1"
TRANSLATION_UNIT = "m"
ANGLE_UNIT = "rad"


class TransformComponent(TypeDecorator[float]):
    impl = Double
    cache_ok = True

    def process_bind_param(self, value: float | None, dialect: Dialect) -> float | None:
        # psycopg requires a common Python element type for array parameters.
        return None if value is None else float(value)


scenes = Table(
    "scenes",
    metadata,
    uuid_primary_key(),
    Column("code", String(CODE_LENGTH), nullable=False),
    Column("title", String(NAME_LENGTH), nullable=False),
    Column("review_state", String(9), nullable=False, server_default="draft"),
    UniqueConstraint("code", name="uq_scenes_code"),
    CheckConstraint(f"code ~ '{CODE_PATTERN}'", name="ck_scenes_code"),
    CheckConstraint("title ~ '[^[:space:]]'", name="ck_scenes_title"),
    CheckConstraint(
        "review_state IN ('draft', 'published', 'withdrawn')", name="ck_scenes_review_state"
    ),
)

scene_versions = Table(
    "scene_versions",
    metadata,
    uuid_primary_key(),
    Column("scene_id", Uuid, ForeignKey(f"{SCHEMA}.scenes.id"), nullable=False),
    Column("version", String(VERSION_LENGTH), nullable=False),
    Column("coordinate_profile", String(80), nullable=False, server_default=COORDINATE_PROFILE),
    Column("translation_unit", String(3), nullable=False, server_default=TRANSLATION_UNIT),
    Column("angle_unit", String(3), nullable=False, server_default=ANGLE_UNIT),
    Column("origin_description", Text, nullable=False),
    Column("spatial_review_reference", Text),
    Column("publication_state", String(9), nullable=False, server_default="draft"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("scene_id", "version", name="uq_scene_versions_identifier"),
    CheckConstraint(f"version ~ '{VERSION_PATTERN}'", name="ck_scene_versions_identifier"),
    CheckConstraint(
        f"coordinate_profile = '{COORDINATE_PROFILE}' "
        f"AND translation_unit = '{TRANSLATION_UNIT}' AND angle_unit = '{ANGLE_UNIT}'",
        name="ck_scene_versions_coordinate_profile",
    ),
    CheckConstraint(
        "origin_description ~ '[^[:space:]]'", name="ck_scene_versions_origin_description"
    ),
    CheckConstraint(
        "spatial_review_reference ~ '[^[:space:]]'", name="ck_scene_versions_review_reference"
    ),
    CheckConstraint(
        "publication_state IN ('draft', 'published', 'withdrawn')",
        name="ck_scene_versions_publication",
    ),
    CheckConstraint(
        "publication_state = 'draft' OR spatial_review_reference IS NOT NULL",
        name="ck_scene_versions_publishable",
    ),
    Index(
        "ix_scene_versions_listing",
        "scene_id",
        "publication_state",
        "created_at",
        "version",
    ),
)

scene_nodes = Table(
    "scene_nodes",
    metadata,
    uuid_primary_key(),
    Column("scene_version_id", Uuid, ForeignKey(f"{SCHEMA}.scene_versions.id"), nullable=False),
    Column("code", String(CODE_LENGTH), nullable=False),
    Column("parent_id", Uuid),
    Column("node_type", String(5), nullable=False),
    Column("asset_version_id", Uuid, ForeignKey(f"{SCHEMA}.asset_versions.id")),
    Column(
        "translation", ARRAY(TransformComponent), nullable=False, server_default=text("'{0,0,0}'")
    ),
    Column(
        "rotation", ARRAY(TransformComponent), nullable=False, server_default=text("'{0,0,0,1}'")
    ),
    Column("scale", ARRAY(TransformComponent), nullable=False, server_default=text("'{1,1,1}'")),
    Column("sort_order", Integer, nullable=False, server_default=text("0")),
    UniqueConstraint("scene_version_id", "code", name="uq_scene_nodes_version_code"),
    UniqueConstraint("scene_version_id", "id", name="uq_scene_nodes_version_node"),
    UniqueConstraint("id", "asset_version_id", name="uq_scene_nodes_asset_pin"),
    ForeignKeyConstraint(
        ["scene_version_id", "parent_id"],
        [f"{SCHEMA}.scene_nodes.scene_version_id", f"{SCHEMA}.scene_nodes.id"],
        name="fk_scene_nodes_same_version_parent",
    ),
    CheckConstraint(f"code ~ '{CODE_PATTERN}'", name="ck_scene_nodes_code"),
    CheckConstraint("id <> parent_id", name="ck_scene_nodes_distinct_parent"),
    CheckConstraint(
        "node_type = 'group' OR (node_type = 'asset' AND asset_version_id IS NOT NULL)",
        name="ck_scene_nodes_type_and_asset",
    ),
    CheckConstraint(
        "atlas.scene_vector_is_finite(translation, 3)", name="ck_scene_nodes_translation"
    ),
    CheckConstraint("atlas.scene_quaternion_is_unit(rotation)", name="ck_scene_nodes_rotation"),
    CheckConstraint(
        "atlas.scene_vector_is_finite(scale, 3) AND 0 < ALL(scale)",
        name="ck_scene_nodes_scale",
    ),
    CheckConstraint("sort_order >= 0", name="ck_scene_nodes_sort_order"),
    Index("ix_scene_nodes_listing", "scene_version_id", "sort_order", "code"),
    Index("ix_scene_nodes_parent", "scene_version_id", "parent_id"),
    Index("ix_scene_nodes_asset_version", "asset_version_id"),
)

scene_node_anatomy_links = Table(
    "scene_node_anatomy_links",
    metadata,
    uuid_primary_key(),
    Column("scene_node_id", Uuid, nullable=False),
    Column("anatomy_asset_link_id", Uuid, nullable=False),
    # Both referenced rows must carry the same exact registry version.
    Column("asset_version_id", Uuid, nullable=False),
    Column("structure_id", Uuid, nullable=False),
    Column("role_snapshot", String(CODE_LENGTH)),
    UniqueConstraint(
        "scene_node_id", "anatomy_asset_link_id", name="uq_scene_node_anatomy_links_pair"
    ),
    ForeignKeyConstraint(
        ["scene_node_id", "asset_version_id"],
        [f"{SCHEMA}.scene_nodes.id", f"{SCHEMA}.scene_nodes.asset_version_id"],
        name="fk_scene_node_anatomy_links_node_pin",
    ),
    ForeignKeyConstraint(
        ["anatomy_asset_link_id", "asset_version_id", "structure_id"],
        [
            f"{SCHEMA}.anatomy_asset_links.id",
            f"{SCHEMA}.anatomy_asset_links.version_id",
            f"{SCHEMA}.anatomy_asset_links.structure_id",
        ],
        name="fk_scene_node_anatomy_links_anatomy_pin",
    ),
    Index("ix_scene_node_anatomy_links_anatomy", "anatomy_asset_link_id"),
)
