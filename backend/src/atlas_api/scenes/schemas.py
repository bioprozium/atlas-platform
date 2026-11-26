"""Public response schemas for the read-only scene API."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, FiniteFloat, field_validator

from atlas_api.anatomy.schemas import Page
from atlas_api.scenes.models import ANGLE_UNIT, COORDINATE_PROFILE, TRANSLATION_UNIT

type Vector3 = tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class LocalTransform(BaseModel):
    translation: Vector3 = Field(description="Local XYZ translation in meters.")
    rotation: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat] = Field(
        description="Local unit quaternion in glTF XYZW order with squared norm within 1e-6 of 1."
    )
    scale: Vector3 = Field(description="Local XYZ scale. The anatomy profile requires unit scale.")


class Scene(BaseModel):
    code: str = Field(description="Stable public scene code.")
    title: str


class SceneVersion(BaseModel):
    version: str = Field(description="Immutable version identifier within the scene.")
    coordinate_profile: Literal[COORDINATE_PROFILE]
    translation_unit: Literal[TRANSLATION_UNIT]
    angle_unit: Literal[ANGLE_UNIT]
    origin_description: str = Field(description="Editorial description of the scene origin.")
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def to_utc(cls, created_at: datetime) -> datetime:
        return created_at.astimezone(UTC)


class SceneSummary(Scene):
    latest_version: SceneVersion


class SceneDetail(Scene):
    versions: Page[SceneVersion]


class SceneVersionDetail(Scene, SceneVersion):
    pass


class SceneAssetReference(BaseModel):
    code: str
    version: str = Field(description="Exact immutable version identifier within the asset.")


class SceneAnatomyLink(BaseModel):
    structure_code: str
    role: str | None = Field(
        description="Registry role captured when the draft mapping was saved."
    )


class SceneNode(BaseModel):
    code: str = Field(description="Stable public node code within the scene version.")
    parent_code: str | None = Field(description="Code of the parent in this version, if any.")
    node_type: Literal["group", "asset"]
    sort_order: int = Field(description="Editorial order, with ties resolved by node code.")
    transform: LocalTransform
    asset: SceneAssetReference | None
    anatomy_links: list[SceneAnatomyLink]


class SceneNodesPage(Page[SceneNode]):
    scene_code: str
    version: str
