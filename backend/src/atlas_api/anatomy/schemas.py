"""Request filters and response schemas for the anatomy API."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

from atlas_api.anatomy.models import CODE_LENGTH, CODE_PATTERN, NAME_LENGTH, StructureKind

SearchTerm = Annotated[str, Field(min_length=1, max_length=NAME_LENGTH, pattern=r"^[^\x00]+$")]


class Language(StrEnum):
    LATIN = "la"
    ENGLISH = "en"
    AZERBAIJANI = "az"


class Laterality(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    NONE = "none"


class RelationType(StrEnum):
    PART_OF = "part_of"
    IS_A = "is_a"


class Direction(StrEnum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"


class Pagination(BaseModel):
    limit: int = Field(50, ge=1, le=100, description="Maximum number of items to return.")
    offset: int = Field(0, ge=0, le=2**63 - 1, description="Number of matching items to skip.")


class SystemFilters(Pagination):
    q: SearchTerm | None = Field(None, description="Substring of the system code or name.")


class StructureFilters(Pagination):
    system_code: str | None = Field(
        None,
        max_length=CODE_LENGTH,
        pattern=CODE_PATTERN,
        description="Include members of this published system.",
    )
    laterality: Laterality | None = None
    language: Language | None = Field(
        None, description="Require a name in this language. Also limits the names matched by q."
    )
    q: SearchTerm | None = Field(None, description="Substring of a preferred name or synonym.")


class RelationFilters(Pagination):
    relation_type: RelationType | None = None
    direction: Direction | None = Field(
        None, description="Direction relative to the requested structure. Omit for both directions."
    )


class Page[T](BaseModel):
    items: list[T]
    limit: int
    offset: int
    has_more: bool = Field(description="Whether more matching items followed this page.")


class System(BaseModel):
    code: str
    name: str


class Structure(BaseModel):
    code: str
    structure_kind: StructureKind = Field(description="Anatomical category of the structure.")
    laterality: Laterality = Field(description="Use none for non-lateral structures.")


class StructureName(BaseModel):
    language: Language
    name: str
    is_preferred: bool


class StructureSummary(Structure):
    preferred_names: list[StructureName]


class ExternalIdentifier(BaseModel):
    namespace: str
    value: str
    source: str = Field(description="Citation or source of the external mapping.")
    source_version: str | None


class StructureDetail(Structure):
    names: list[StructureName]
    systems: list[System]
    external_identifiers: list[ExternalIdentifier]


class StructureRelation(BaseModel):
    source_code: str = Field(description="Code of the part or subtype.")
    relation_type: RelationType
    target_code: str = Field(description="Code of the whole or supertype.")


class ErrorResponse(BaseModel):
    detail: str
