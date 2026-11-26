"""Validate the operator's version-specific source declaration."""

import re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from atlas_api.anatomy.models import CODE_LENGTH, CODE_PATTERN, NAME_LENGTH
from atlas_api.assets.models import VERSION_LENGTH, VERSION_PATTERN
from atlas_api.assets.validation import (
    SHA256_PATTERN,
    GLBValidationError,
    open_directory,
    open_regular_file,
)

from .validation import IngestionError

TEXT = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=2048, pattern=r"^[^\x00]+$"),
]
SPDX = Annotated[
    str, StringConstraints(max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9.+-]*$")
]
METADATA_BYTES = 128 * 1024


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    creator: TEXT
    rights_holder: TEXT
    source_reference: TEXT
    spdx_license_id: SPDX | None = None
    license_reference: TEXT
    attribution: TEXT
    modifications: TEXT


class SourceGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    units: TEXT
    orientation: TEXT
    geometry_notes: TEXT
    export_parameters: TEXT


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    asset_code: Annotated[
        str, StringConstraints(max_length=CODE_LENGTH, pattern=CODE_PATTERN)
    ]
    version: Annotated[
        str, StringConstraints(max_length=VERSION_LENGTH, pattern=VERSION_PATTERN)
    ]
    title: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, max_length=NAME_LENGTH, pattern=r"^[^\x00]+$"
        ),
    ]
    source_geometry: SourceGeometry
    sources: list[Source] = Field(min_length=1, max_length=32)


def read_manifest(path: Path) -> Manifest:
    if ".." in path.parts:
        raise IngestionError(
            "METADATA_INVALID", "Metadata paths must omit parent components."
        )
    try:
        with (
            open_directory(path.absolute().parent) as parent,
            open_regular_file(parent, path.name) as file,
        ):
            raw = file.read(METADATA_BYTES + 1)
        if len(raw) > METADATA_BYTES:
            raise IngestionError("METADATA_INVALID", "Metadata exceeds 128 KiB.")
        return Manifest.model_validate_json(raw)
    except (GLBValidationError, OSError):
        raise IngestionError(
            "METADATA_INVALID", "Could not safely read source metadata."
        ) from None
    except ValueError:
        raise IngestionError(
            "METADATA_INVALID", "Source metadata does not match the manifest schema."
        ) from None


def digest_argument(value: str) -> str:
    if not re.fullmatch(SHA256_PATTERN, value):
        raise ValueError("Expected lowercase SHA-256.")
    return value
