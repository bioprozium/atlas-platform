"""Snapshot reads of published scenes and their exact asset pins."""

from collections import defaultdict
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Connection, RowMapping, or_, select, true

from atlas_api.anatomy.models import anatomical_structures
from atlas_api.anatomy.queries import fetch_page
from atlas_api.anatomy.schemas import Page, Pagination
from atlas_api.assets.models import anatomy_asset_links, asset_versions, assets
from atlas_api.assets.queries import PUBLISHED_ASSET, PUBLISHED_VERSION
from atlas_api.scenes.models import scene_node_anatomy_links, scene_nodes, scene_versions, scenes
from atlas_api.scenes.schemas import (
    LocalTransform,
    SceneAnatomyLink,
    SceneAssetReference,
    SceneNode,
    SceneNodesPage,
    SceneSummary,
    SceneVersion,
)

scene = scenes.c
version = scene_versions.c
node = scene_nodes.c
asset = assets.c
asset_version = asset_versions.c
structure = anatomical_structures.c
mapping = scene_node_anatomy_links.c
anatomy_link = anatomy_asset_links.c

SCENE_FIELDS = (scene.code, scene.title)
VERSION_FIELDS = (
    version.version,
    version.coordinate_profile,
    version.translation_unit,
    version.angle_unit,
    version.origin_description,
    version.created_at,
)
VERSION_ORDER = (version.created_at.desc(), version.version.desc())
PUBLISHED_SCENE = scene.review_state == "published"
PUBLISHED_SCENE_VERSION = version.publication_state == "published"

# With non-null pins, the foreign keys guarantee both joined registry rows exist.
# A published registry version already satisfies the registry's validation and rights checks.
ASSETS_AVAILABLE = ~(
    select(1)
    .select_from(
        scene_nodes.join(asset_versions, node.asset_version_id == asset_version.id).join(
            assets, asset_version.asset_id == asset.id
        )
    )
    .where(node.scene_version_id == version.id, or_(~PUBLISHED_ASSET, ~PUBLISHED_VERSION))
    .correlate(scene_versions)
    .exists()
)


def list_scenes(connection: Connection, filters: Pagination) -> Page[SceneSummary]:
    latest = (
        select(*VERSION_FIELDS)
        .where(version.scene_id == scene.id, PUBLISHED_SCENE_VERSION, ASSETS_AVAILABLE)
        .order_by(*VERSION_ORDER)
        .limit(1)
        .lateral("latest")
    )
    query = (
        select(*SCENE_FIELDS, *latest.c)
        .select_from(scenes.join(latest, true()))
        .where(PUBLISHED_SCENE)
        .order_by(scene.code)
    )
    rows, has_more = fetch_page(connection, query, filters)
    return Page[SceneSummary](
        items=[
            SceneSummary(
                code=row.code, title=row.title, latest_version=SceneVersion.model_validate(row)
            )
            for row in rows
        ],
        limit=filters.limit,
        offset=filters.offset,
        has_more=has_more,
    )


def get_scene(connection: Connection, code: str) -> RowMapping | None:
    published = select(1).where(version.scene_id == scene.id, PUBLISHED_SCENE_VERSION)
    query = select(
        scene.id,
        *SCENE_FIELDS,
        published.where(ASSETS_AVAILABLE).exists().label("assets_available"),
    ).where(scene.code == code, PUBLISHED_SCENE, published.exists())
    return connection.execute(query).mappings().one_or_none()


def list_versions(
    connection: Connection, scene_id: UUID, filters: Pagination
) -> Page[SceneVersion]:
    query = (
        select(*VERSION_FIELDS)
        .where(version.scene_id == scene_id, PUBLISHED_SCENE_VERSION, ASSETS_AVAILABLE)
        .order_by(*VERSION_ORDER)
    )
    rows, has_more = fetch_page(connection, query, filters)
    return Page[SceneVersion](
        items=[SceneVersion.model_validate(row) for row in rows],
        limit=filters.limit,
        offset=filters.offset,
        has_more=has_more,
    )


def get_version(connection: Connection, code: str, identifier: str) -> RowMapping | None:
    query = (
        select(
            version.id, *SCENE_FIELDS, *VERSION_FIELDS, ASSETS_AVAILABLE.label("assets_available")
        )
        .select_from(scenes.join(scene_versions, scene.id == version.scene_id))
        .where(
            scene.code == code,
            version.version == identifier,
            PUBLISHED_SCENE,
            PUBLISHED_SCENE_VERSION,
        )
    )
    return connection.execute(query).mappings().one_or_none()


def anatomy_links_by_node(
    connection: Connection, rows: Sequence[RowMapping]
) -> dict[UUID, list[SceneAnatomyLink]]:
    grouped = defaultdict(list)
    if rows:
        query = (
            select(
                mapping.scene_node_id, structure.code.label("structure_code"), anatomy_link.role
            )
            .select_from(
                scene_node_anatomy_links.join(
                    anatomy_asset_links, mapping.anatomy_asset_link_id == anatomy_link.id
                ).join(anatomical_structures, anatomy_link.structure_id == structure.id)
            )
            .where(
                mapping.scene_node_id.in_({row.id for row in rows}),
                structure.review_state == "published",
            )
            .order_by(structure.code)
        )
        for row in connection.execute(query).mappings():
            grouped[row.scene_node_id].append(SceneAnatomyLink.model_validate(row))
    return grouped


def list_nodes(
    connection: Connection, scene_version: RowMapping, filters: Pagination
) -> SceneNodesPage:
    parent = scene_nodes.alias("parent")
    query = (
        select(
            node.id,
            node.code,
            parent.c.code.label("parent_code"),
            node.node_type,
            node.sort_order,
            node.translation,
            node.rotation,
            node.scale,
            asset.code.label("asset_code"),
            asset_version.version.label("asset_version"),
        )
        .select_from(
            scene_nodes.outerjoin(parent, node.parent_id == parent.c.id)
            .outerjoin(asset_versions, node.asset_version_id == asset_version.id)
            .outerjoin(assets, asset_version.asset_id == asset.id)
        )
        .where(node.scene_version_id == scene_version.id)
        .order_by(node.sort_order, node.code)
    )
    rows, has_more = fetch_page(connection, query, filters)
    links = anatomy_links_by_node(connection, rows)
    return SceneNodesPage(
        scene_code=scene_version.code,
        version=scene_version.version,
        items=[
            SceneNode(
                code=row.code,
                parent_code=row.parent_code,
                node_type=row.node_type,
                sort_order=row.sort_order,
                transform=LocalTransform.model_validate(row),
                asset=SceneAssetReference(code=row.asset_code, version=row.asset_version)
                if row.asset_code is not None
                else None,
                anatomy_links=links[row.id],
            )
            for row in rows
        ],
        limit=filters.limit,
        offset=filters.offset,
        has_more=has_more,
    )
