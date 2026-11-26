"""Build the public scene manifest from a single database snapshot."""

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Connection, RowMapping, func, select

from atlas_api.anatomy.models import anatomical_structures
from atlas_api.anatomy.queries import preferred_names_by_structure
from atlas_api.anatomy.schemas import StructureSummary
from atlas_api.assets import queries as registry
from atlas_api.assets.delivery import StoredObject
from atlas_api.assets.schemas import AssetVersionDetail
from atlas_api.config import DeliverySettings
from atlas_api.scenes import queries
from atlas_api.scenes.models import scene_node_anatomy_links
from atlas_api.scenes.schemas import SceneNode, SceneVersionDetail

NODE_LIMIT = "Scene manifest node limit exceeded."
ASSET_LIMIT = "Scene manifest asset version limit exceeded."
SIZE_LIMIT = "Scene manifest serialized size limit exceeded."
ASSETS_UNAVAILABLE = "Scene asset versions unavailable."
ANATOMY_UNAVAILABLE = "Scene anatomy annotations unavailable."


class ManifestAsset(AssetVersionDetail):
    download_url: str = Field(description="Origin-relative URL for this exact GLB version.")


class SceneManifest(BaseModel):
    schema_version: Literal[1] = Field(description="Version of the scene manifest format.")
    scene: SceneVersionDetail
    nodes: list[SceneNode]
    assets: list[ManifestAsset]
    anatomy: list[StructureSummary]


def _assets(
    connection: Connection, scene_id: UUID, limit: int, byte_limit: int
) -> tuple[list[ManifestAsset], list[StoredObject]]:
    node = queries.node
    pins = (
        select(node.asset_version_id)
        .distinct()
        .where(node.scene_version_id == scene_id, node.asset_version_id.is_not(None))
        .limit(limit + 1)
    )
    ids = list(connection.scalars(pins))
    if len(ids) > limit:
        raise HTTPException(503, ASSET_LIMIT)
    if not ids:
        return [], []

    storage = registry.version.storage_reference
    statement = (
        registry.eligible_versions(*registry.ASSET_FIELDS, *registry.VERSION_FIELDS, storage)
        .where(registry.version.id.in_(ids))
        .order_by(registry.asset.code, registry.version.version)
    )
    rows = connection.execute(statement).mappings().all()
    if len(rows) != len(ids):
        raise HTTPException(503, ASSETS_UNAVAILABLE)
    _check_source_bytes(connection, ids, byte_limit)
    sources = registry.sources_by_version(connection, rows)
    if any(not sources.get(row.version_id) for row in rows):
        raise HTTPException(503, ASSETS_UNAVAILABLE)

    return (
        [
            ManifestAsset.model_validate(
                dict(
                    row,
                    sources=sources[row.version_id],
                    download_url=f"/api/v1/assets/{row.code}/versions/{row.version}/file",
                )
            )
            for row in rows
        ],
        [StoredObject(row.sha256, row.file_size, row.storage_reference) for row in rows],
    )


def _check_source_bytes(connection: Connection, ids: Sequence[UUID], limit: int) -> None:
    # Asset citations are unbounded text in the registry. Count their raw bytes
    # before materializing the sources. JSON adds overhead and may escape them.
    source = registry.source
    text_bytes = (
        func.octet_length(source.creator)
        + func.octet_length(source.rights_holder)
        + func.octet_length(source.source_reference)
        + func.octet_length(source.license_reference)
        + func.octet_length(source.attribution)
        + func.octet_length(source.modifications)
        + func.coalesce(func.octet_length(source.spdx_license_id), 0)
        + 80  # Lower bound on JSON key and delimiter overhead per record.
    )
    size = connection.scalar(select(func.sum(text_bytes)).where(source.version_id.in_(ids)))
    if size is not None and size > limit:
        raise HTTPException(503, SIZE_LIMIT)


def _anatomy(connection: Connection, node_ids: list[UUID]) -> list[StructureSummary]:
    if not node_ids:
        return []
    structure = anatomical_structures.c
    mapping = scene_node_anatomy_links.c
    ids = select(mapping.structure_id).where(mapping.scene_node_id.in_(node_ids))
    query = (
        select(structure.id, structure.code, structure.structure_kind, structure.laterality)
        .where(structure.id.in_(ids), structure.review_state == "published")
        .order_by(structure.code)
    )
    rows = connection.execute(query).mappings().all()
    names = preferred_names_by_structure(connection, [row.id for row in rows])
    return [
        StructureSummary.model_validate(dict(row, preferred_names=names.get(row.id, [])))
        for row in rows
    ]


def build_manifest(
    connection: Connection, scene: RowMapping, settings: DeliverySettings
) -> tuple[bytes, list[StoredObject]]:
    if scene.origin_description is None:
        raise HTTPException(503, SIZE_LIMIT)
    query = queries.nodes_query(scene.id).limit(settings.max_nodes + 1)
    rows = connection.execute(query).mappings().all()
    if len(rows) > settings.max_nodes:
        raise HTTPException(503, NODE_LIMIT)
    assets, objects = _assets(connection, scene.id, settings.max_assets, settings.max_manifest_bytes)
    node_ids = [row.id for row in rows]
    if node_ids:
        mapping = scene_node_anatomy_links.c
        number_of_links = connection.scalar(
            select(func.count()).where(mapping.scene_node_id.in_(node_ids))
        )
        # Each annotation needs at least 30 bytes for its keys, values and braces.
        if number_of_links * 30 > settings.max_manifest_bytes:
            raise HTTPException(503, SIZE_LIMIT)
    nodes = queries.build_nodes(connection, rows)
    anatomy = _anatomy(connection, node_ids)
    pins = {(asset.code, asset.version) for asset in assets}
    if any(node.asset and (node.asset.code, node.asset.version) not in pins for node in nodes):
        raise HTTPException(503, ASSETS_UNAVAILABLE)
    structures = {structure.code for structure in anatomy}
    if any(link.structure_code not in structures for node in nodes for link in node.anatomy_links):
        raise HTTPException(503, ANATOMY_UNAVAILABLE)

    manifest = SceneManifest(
        schema_version=1,
        scene=SceneVersionDetail.model_validate(scene),
        nodes=nodes,
        assets=assets,
        anatomy=anatomy,
    )
    body = manifest.model_dump_json().encode("utf-8")
    if len(body) > settings.max_manifest_bytes:
        raise HTTPException(503, SIZE_LIMIT)
    return body, objects
