"""Read-only routes for the Human Anatomy domain."""

from contextlib import AbstractContextManager
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from sqlalchemy import Connection

from atlas_api.anatomy import queries
from atlas_api.anatomy.models import CODE_LENGTH, CODE_PATTERN
from atlas_api.anatomy.schemas import (
    ErrorResponse,
    Page,
    RelationFilters,
    StructureDetail,
    StructureFilters,
    StructureRelation,
    StructureSummary,
    System,
    SystemFilters,
)
from atlas_api.api.dependencies import read_connection

router = APIRouter(
    prefix="/anatomy",
    tags=["Anatomy"],
    responses={
        503: {"model": ErrorResponse, "description": "The anatomy database is unavailable."}
    },
)
SYSTEM_NOT_FOUND = {
    404: {"model": ErrorResponse, "description": "The published system was not found."}
}
STRUCTURE_NOT_FOUND = {
    404: {"model": ErrorResponse, "description": "The published structure was not found."}
}
PublicCode = Annotated[
    str, Path(pattern=CODE_PATTERN, max_length=CODE_LENGTH, description="Stable public code.")
]


def anatomy_connection(request: Request) -> AbstractContextManager[Connection]:
    return read_connection(request, "Anatomy database unavailable.")


@router.get("/systems", operation_id="listAnatomicalSystems")
def list_systems(request: Request, filters: Annotated[SystemFilters, Query()]) -> Page[System]:
    """List published anatomical systems in code order."""
    with anatomy_connection(request) as connection:
        return queries.list_systems(connection, filters)


@router.get("/systems/{code}", operation_id="getAnatomicalSystem", responses=SYSTEM_NOT_FOUND)
def get_system(request: Request, code: PublicCode) -> System:
    """Retrieve a published anatomical system by its public code."""
    with anatomy_connection(request) as connection:
        system = queries.get_system(connection, code)
    if system is None:
        raise HTTPException(404, "Anatomical system not found.")
    return system


@router.get("/structures", operation_id="listAnatomicalStructures")
def list_structures(
    request: Request, filters: Annotated[StructureFilters, Query()]
) -> Page[StructureSummary]:
    """List published structures with preferred names in code order."""
    with anatomy_connection(request) as connection:
        return queries.list_structures(connection, filters)


@router.get(
    "/structures/{code}", operation_id="getAnatomicalStructure", responses=STRUCTURE_NOT_FOUND
)
def get_structure(request: Request, code: PublicCode) -> StructureDetail:
    """Retrieve a published structure with names, system memberships, and external identifiers."""
    with anatomy_connection(request) as connection:
        structure = queries.get_structure(connection, code)
    if structure is None:
        raise HTTPException(404, "Anatomical structure not found.")
    return structure


@router.get(
    "/structures/{code}/relations",
    operation_id="listAnatomicalStructureRelations",
    responses=STRUCTURE_NOT_FOUND,
)
def list_relations(
    request: Request, code: PublicCode, filters: Annotated[RelationFilters, Query()]
) -> Page[StructureRelation]:
    """List direct relations between published structures in source, target, and type order."""
    with anatomy_connection(request) as connection:
        page = queries.list_relations(connection, code, filters)
    if page is None:
        raise HTTPException(404, "Anatomical structure not found.")
    return page
