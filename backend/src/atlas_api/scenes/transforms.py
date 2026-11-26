"""Reference math for the atlas-anatomy-y-up-v1 coordinate profile."""

from collections.abc import Iterable
from math import hypot

from atlas_api.scenes.schemas import LocalTransform, Vector3

type Matrix = tuple[tuple[float, ...], ...]

IDENTITY: Matrix = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)


def local_matrix(transform: LocalTransform) -> Matrix:
    """Compute T @ R @ S for a validated local transform acting on column vectors."""
    tx, ty, tz = transform.translation
    sx, sy, sz = transform.scale
    # Project accepted near-unit quaternions onto the unit sphere before composing.
    norm = hypot(*transform.rotation)
    x, y, z, w = (component / norm for component in transform.rotation)
    return (
        ((1 - 2 * (y * y + z * z)) * sx, 2 * (x * y - z * w) * sy, 2 * (x * z + y * w) * sz, tx),
        (2 * (x * y + z * w) * sx, (1 - 2 * (x * x + z * z)) * sy, 2 * (y * z - x * w) * sz, ty),
        (2 * (x * z - y * w) * sx, 2 * (y * z + x * w) * sy, (1 - 2 * (x * x + y * y)) * sz, tz),
        (0.0, 0.0, 0.0, 1.0),
    )


def world_matrix(root_to_node: Iterable[LocalTransform]) -> Matrix:
    """Compose the ancestor and node transforms in root-to-node order."""
    world = IDENTITY
    for transform in root_to_node:
        local_columns = tuple(zip(*local_matrix(transform), strict=True))
        world = tuple(
            tuple(sum(a * b for a, b in zip(row, column, strict=True)) for column in local_columns)
            for row in world
        )
    return world


def dicom_lps_mm_to_atlas(point: Vector3) -> Vector3:
    """Convert DICOM LPS axes and units, preserving the input origin."""
    left, posterior, superior = point
    return left / 1000, superior / 1000, -posterior / 1000
