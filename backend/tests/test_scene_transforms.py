from math import sqrt

import pytest

from atlas_api.scenes.schemas import LocalTransform
from atlas_api.scenes.transforms import IDENTITY, dicom_lps_mm_to_atlas, local_matrix, world_matrix


def transform(translation=(0, 0, 0), rotation=(0, 0, 0, 1), scale=(1, 1, 1)):
    return LocalTransform(translation=translation, rotation=rotation, scale=scale)


def apply(matrix, point):
    return tuple(sum(a * b for a, b in zip(row, (*point, 1), strict=True)) for row in matrix)


def test_identity_and_parent_to_child_composition():
    assert local_matrix(transform()) == IDENTITY
    assert world_matrix([]) == IDENTITY
    half_sqrt = sqrt(0.5)
    root = transform((10, 0, 0), (0, 0, half_sqrt, half_sqrt))
    child = transform((2, 0, 0))
    grandchild = transform((0, 3, 1))
    assert apply(world_matrix([root, child, grandchild]), (1, 0, 0)) == pytest.approx((7, 3, 1, 1))
    assert apply(world_matrix([root, child]), (0, 0, 0)) == pytest.approx((10, 2, 0, 1))


def test_general_trs_order_and_near_unit_rotation():
    # Non-unit scale here demonstrates TRS order. The anatomy schema accepts only unit scale.
    half_sqrt = sqrt(0.5)
    local = transform((10, 0, 0), (0, 0, half_sqrt, half_sqrt), (2, 3, 4))
    assert apply(local_matrix(local), (1, 0, 0)) == pytest.approx((10, 2, 0, 1))
    near_unit = transform(rotation=(0, 0, half_sqrt * 1.0000002, half_sqrt * 1.0000002))
    assert apply(local_matrix(near_unit), (1, 0, 0)) == pytest.approx((0, 1, 0, 1))


@pytest.mark.parametrize(
    ("lps", "atlas"),
    [
        ((0, 0, 0), (0, 0, 0)),
        ((10, 20, 30), (0.01, 0.03, -0.02)),
        ((-250, -100, 1250), (-0.25, 1.25, 0.1)),
        ((1000, 0, 0), (1, 0, 0)),
        ((0, 1000, 0), (0, 0, -1)),
        ((0, 0, 1000), (0, 1, 0)),
    ],
)
def test_dicom_lps_axis_and_unit_conversion(lps, atlas):
    assert dicom_lps_mm_to_atlas(lps) == pytest.approx(atlas)
