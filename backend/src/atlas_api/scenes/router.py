"""Read-only routes for versioned scene graphs."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import RowMapping

from atlas_api.anatomy.router import PublicCode
from atlas_api.anatomy.schemas import ErrorResponse, Page, Pagination
from atlas_api.api.dependencies import read_connection
from atlas_api.assets.router import VersionIdentifier
from atlas_api.scenes import queries
from atlas_api.scenes.schemas import SceneDetail, SceneNodesPage, SceneSummary, SceneVersionDetail

DATABASE_UNAVAILABLE = "Scene database unavailable."
ASSETS_UNAVAILABLE = "Scene asset versions unavailable."
SCENE_NOT_FOUND = {
    404: {"model": ErrorResponse, "description": "The published scene was not found."}
}
VERSION_NOT_FOUND = {
    404: {"model": ErrorResponse, "description": "The published scene version was not found."}
}
DEPENDENCIES_UNAVAILABLE = {
    503: {
        "model": ErrorResponse,
        "description": "The scene database or required published asset versions are unavailable.",
    }
}

router = APIRouter(
    prefix="/scenes",
    tags=["Scenes"],
    responses={503: {"model": ErrorResponse, "description": "The scene database is unavailable."}},
)


def require_available(row: RowMapping | None, not_found: str) -> RowMapping:
    if row is None:
        raise HTTPException(404, not_found)
    if not row.assets_available:
        raise HTTPException(503, ASSETS_UNAVAILABLE)
    return row


@router.get("", operation_id="listScenes")
def list_scenes(request: Request, filters: Annotated[Pagination, Query()]) -> Page[SceneSummary]:
    """List published scenes with their latest eligible version in scene code order."""
    with read_connection(request, DATABASE_UNAVAILABLE) as connection:
        return queries.list_scenes(connection, filters)


@router.get(
    "/{code}", operation_id="getScene", responses=SCENE_NOT_FOUND | DEPENDENCIES_UNAVAILABLE
)
def get_scene(
    request: Request, code: PublicCode, filters: Annotated[Pagination, Query()]
) -> SceneDetail:
    """Retrieve a published scene with a page of eligible versions, newest first."""
    with read_connection(request, DATABASE_UNAVAILABLE) as connection:
        scene = require_available(queries.get_scene(connection, code), "Scene not found.")
        return SceneDetail(
            code=scene.code,
            title=scene.title,
            versions=queries.list_versions(connection, scene.id, filters),
        )


@router.get(
    "/{code}/versions/{version}",
    operation_id="getSceneVersion",
    responses=VERSION_NOT_FOUND | DEPENDENCIES_UNAVAILABLE,
)
def get_version(
    request: Request, code: PublicCode, version: VersionIdentifier
) -> SceneVersionDetail:
    """Retrieve coordinate and origin metadata for an exact published scene version."""
    with read_connection(request, DATABASE_UNAVAILABLE) as connection:
        row = require_available(
            queries.get_version(connection, code, version), "Scene version not found."
        )
        return SceneVersionDetail.model_validate(row)


@router.get(
    "/{code}/versions/{version}/nodes",
    operation_id="listSceneNodes",
    responses=VERSION_NOT_FOUND | DEPENDENCIES_UNAVAILABLE,
)
def list_nodes(
    request: Request,
    code: PublicCode,
    version: VersionIdentifier,
    filters: Annotated[Pagination, Query()],
) -> SceneNodesPage:
    """List nodes in editorial order, with ties sorted by node code."""
    with read_connection(request, DATABASE_UNAVAILABLE) as connection:
        row = require_available(
            queries.get_version(connection, code, version), "Scene version not found."
        )
        return queries.list_nodes(connection, row, filters)
