"""Read-only routes for the 3D Asset Registry."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from atlas_api.anatomy.router import STRUCTURE_NOT_FOUND, PublicCode
from atlas_api.anatomy.schemas import ErrorResponse, Page, Pagination
from atlas_api.api.dependencies import read_connection
from atlas_api.assets import queries
from atlas_api.assets.models import VERSION_LENGTH, VERSION_PATTERN
from atlas_api.assets.schemas import (
    AssetDetail,
    AssetFilters,
    AssetSummary,
    AssetVersionDetail,
    StructureAsset,
)

router = APIRouter(
    tags=["Assets"],
    responses={
        503: {
            "model": ErrorResponse,
            "description": "The asset registry database is unavailable.",
        }
    },
)
DATABASE_UNAVAILABLE = "Asset registry database unavailable."
ASSET_NOT_FOUND = {
    404: {"model": ErrorResponse, "description": "The published asset was not found."}
}
VERSION_NOT_FOUND = {
    404: {"model": ErrorResponse, "description": "The published asset version was not found."}
}
VersionIdentifier = Annotated[
    str,
    Path(
        pattern=VERSION_PATTERN,
        max_length=VERSION_LENGTH,
        description="Immutable version identifier.",
    ),
]


@router.get("/assets", operation_id="listAssets")
def list_assets(
    request: Request, filters: Annotated[AssetFilters, Query()]
) -> Page[AssetSummary]:
    """List published assets with their latest eligible version in asset code order."""
    with read_connection(request, DATABASE_UNAVAILABLE) as connection:
        return queries.list_assets(connection, filters)


@router.get("/assets/{code}", operation_id="getAsset", responses=ASSET_NOT_FOUND)
def get_asset(
    request: Request, code: PublicCode, filters: Annotated[Pagination, Query()]
) -> AssetDetail:
    """Retrieve a published asset with a page of its eligible versions, newest first."""
    with read_connection(request, DATABASE_UNAVAILABLE) as connection:
        asset = queries.get_asset(connection, code, filters)
    if asset is None:
        raise HTTPException(404, "Asset not found.")
    return asset


@router.get(
    "/assets/{code}/versions/{version}",
    operation_id="getAssetVersion",
    responses=VERSION_NOT_FOUND,
)
def get_version(
    request: Request, code: PublicCode, version: VersionIdentifier
) -> AssetVersionDetail:
    """Retrieve a published asset version and its attribution."""
    with read_connection(request, DATABASE_UNAVAILABLE) as connection:
        asset_version = queries.get_version(connection, code, version)
    if asset_version is None:
        raise HTTPException(404, "Asset version not found.")
    return asset_version


@router.get(
    "/anatomy/structures/{code}/assets",
    operation_id="listAnatomicalStructureAssets",
    responses=STRUCTURE_NOT_FOUND,
)
def list_structure_assets(
    request: Request, code: PublicCode, filters: Annotated[AssetFilters, Query()]
) -> Page[StructureAsset]:
    """List eligible linked versions by asset code and newest version first."""
    with read_connection(request, DATABASE_UNAVAILABLE) as connection:
        page = queries.list_structure_assets(connection, code, filters)
    if page is None:
        raise HTTPException(404, "Anatomical structure not found.")
    return page
