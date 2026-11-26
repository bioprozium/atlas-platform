"""Public filters and response schemas for the 3D Asset Registry."""

from typing import Literal

from pydantic import BaseModel, Field

from atlas_api.anatomy.schemas import Page, Pagination, SearchTerm


class AssetFilters(Pagination):
    q: SearchTerm | None = Field(None, description="Substring of the asset code or title.")


class AssetSource(BaseModel):
    creator: str
    rights_holder: str
    source_reference: str = Field(description="Original source URL or citation.")
    spdx_license_id: str | None
    license_reference: str = Field(description="License URL or public permission reference.")
    attribution: str
    modifications: str


class AssetVersion(BaseModel):
    version: str = Field(description="Immutable version identifier within the asset.")
    sha256: str = Field(description="Lowercase hexadecimal digest of the GLB bytes.")
    file_size: int = Field(description="Size of the GLB in bytes.")
    format: Literal["glb"]
    mime_type: Literal["model/gltf-binary"]
    sources: list[AssetSource]


class Asset(BaseModel):
    code: str = Field(description="Stable public asset code.")
    title: str


class AssetSummary(Asset):
    latest_version: AssetVersion


class AssetDetail(Asset):
    versions: Page[AssetVersion]


class AssetVersionDetail(Asset, AssetVersion):
    pass


class StructureAsset(AssetVersionDetail):
    role: str | None
