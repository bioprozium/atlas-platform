"""Read queries for published assets and versions."""

from collections import defaultdict
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Connection, RowMapping, Select, or_, select, true

from atlas_api.anatomy.queries import fetch_page, published_structure
from atlas_api.anatomy.schemas import Page, Pagination
from atlas_api.assets.models import (
    anatomy_asset_links,
    asset_sources,
    asset_versions,
    assets,
)
from atlas_api.assets.schemas import (
    AssetDetail,
    AssetFilters,
    AssetSource,
    AssetSummary,
    AssetVersion,
    AssetVersionDetail,
    StructureAsset,
)

asset = assets.c
version = asset_versions.c
source = asset_sources.c
link = anatomy_asset_links.c

ASSET_FIELDS = (asset.code, asset.title)
VERSION_FIELDS = (
    version.id.label("version_id"),
    version.version,
    version.sha256,
    version.file_size,
    version.format,
    version.mime_type,
)
VERSION_ORDER = (version.created_at.desc(), version.version.desc())
PUBLISHED_ASSET = asset.review_state == "published"
PUBLISHED_VERSION = version.publication_state == "published"


def matching_assets(query: Select, filters: AssetFilters) -> Select:
    if filters.q is None:
        return query
    return query.where(
        or_(
            asset.code.icontains(filters.q, autoescape=True),
            asset.title.icontains(filters.q, autoescape=True),
        )
    )


def sources_by_version(
    connection: Connection, rows: Sequence[RowMapping]
) -> dict[UUID, list[AssetSource]]:
    grouped = defaultdict(list)
    if rows:
        query = (
            select(
                source.version_id,
                source.creator,
                source.rights_holder,
                source.source_reference,
                source.spdx_license_id,
                source.license_reference,
                source.attribution,
                source.modifications,
            )
            .where(source.version_id.in_({row.version_id for row in rows}))
            .order_by(source.version_id, source.id)
        )
        for row in connection.execute(query).mappings():
            grouped[row.version_id].append(AssetSource.model_validate(row))
    return grouped


def list_assets(connection: Connection, filters: AssetFilters) -> Page[AssetSummary]:
    latest = (
        select(*VERSION_FIELDS)
        .where(version.asset_id == asset.id, PUBLISHED_VERSION)
        .order_by(*VERSION_ORDER)
        .limit(1)
        .lateral("latest")
    )
    query = (
        select(*ASSET_FIELDS, *latest.c)
        .select_from(assets.join(latest, true()))
        .where(PUBLISHED_ASSET)
        .order_by(asset.code)
    )
    rows, has_more = fetch_page(connection, matching_assets(query, filters), filters)
    sources = sources_by_version(connection, rows)
    return Page[AssetSummary](
        items=[
            AssetSummary(
                code=row.code,
                title=row.title,
                latest_version=AssetVersion.model_validate(
                    dict(row, sources=sources[row.version_id])
                ),
            )
            for row in rows
        ],
        limit=filters.limit,
        offset=filters.offset,
        has_more=has_more,
    )


def get_asset(connection: Connection, code: str, filters: Pagination) -> AssetDetail | None:
    has_published_version = (
        select(1).where(version.asset_id == asset.id, PUBLISHED_VERSION).exists()
    )
    query = select(asset.id, *ASSET_FIELDS).where(
        asset.code == code, PUBLISHED_ASSET, has_published_version
    )
    parent = connection.execute(query).mappings().one_or_none()
    if parent is None:
        return None

    versions = (
        select(*VERSION_FIELDS)
        .where(version.asset_id == parent.id, PUBLISHED_VERSION)
        .order_by(*VERSION_ORDER)
    )
    rows, has_more = fetch_page(connection, versions, filters)
    sources = sources_by_version(connection, rows)
    return AssetDetail(
        code=parent.code,
        title=parent.title,
        versions=Page[AssetVersion](
            items=[
                AssetVersion.model_validate(dict(row, sources=sources[row.version_id]))
                for row in rows
            ],
            limit=filters.limit,
            offset=filters.offset,
            has_more=has_more,
        ),
    )


def get_version(
    connection: Connection, code: str, identifier: str
) -> AssetVersionDetail | None:
    query = (
        select(*ASSET_FIELDS, *VERSION_FIELDS)
        .select_from(assets.join(asset_versions, asset.id == version.asset_id))
        .where(
            asset.code == code,
            version.version == identifier,
            PUBLISHED_ASSET,
            PUBLISHED_VERSION,
        )
    )
    row = connection.execute(query).mappings().one_or_none()
    if row is None:
        return None
    sources = sources_by_version(connection, [row])
    return AssetVersionDetail.model_validate(dict(row, sources=sources[row.version_id]))


def list_structure_assets(
    connection: Connection, code: str, filters: AssetFilters
) -> Page[StructureAsset] | None:
    structure = published_structure(connection, code)
    if structure is None:
        return None

    query = (
        select(*ASSET_FIELDS, *VERSION_FIELDS, link.role)
        .select_from(
            anatomy_asset_links.join(asset_versions, link.version_id == version.id).join(
                assets, version.asset_id == asset.id
            )
        )
        .where(link.structure_id == structure.id, PUBLISHED_ASSET, PUBLISHED_VERSION)
        .order_by(asset.code, *VERSION_ORDER)
    )
    rows, has_more = fetch_page(connection, matching_assets(query, filters), filters)
    sources = sources_by_version(connection, rows)
    return Page[StructureAsset](
        items=[
            StructureAsset.model_validate(dict(row, sources=sources[row.version_id]))
            for row in rows
        ],
        limit=filters.limit,
        offset=filters.offset,
        has_more=has_more,
    )
