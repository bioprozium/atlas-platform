"""Integration tests for the scene graph and publication workflow."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from queue import Queue
from time import monotonic, sleep
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import case, create_engine, delete, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import NullPool

from atlas_api.anatomy.models import anatomical_structures, structure_names
from atlas_api.assets.models import anatomy_asset_links, asset_sources, asset_versions, assets
from atlas_api.assets.validation import glb_storage_reference
from atlas_api.config import (
    MIGRATION_DATABASE_ENV,
    MIGRATION_ROLE,
    RUNTIME_DATABASE_ENV,
    RUNTIME_ROLE,
)
from atlas_api.main import create_app
from atlas_api.scenes import queries
from atlas_api.scenes.models import scene_node_anatomy_links, scene_nodes, scene_versions, scenes

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[1]
PREFIX = "/api/v1/scenes"
DEMO = "/synthetic-overview"
DEMO_V1 = f"{DEMO}/versions/v1"
INSTANT = datetime(2026, 1, 1, tzinfo=UTC)
ORIGIN = "Center of the synthetic reference markers."
REVIEW = "internal-spatial-review-1"
ASSET_ID = UUID(int=1)
PIN_V1 = UUID(int=2)
PIN_V2 = UUID(int=3)
PIN_DRAFT = UUID(int=4)
PUBLIC_STRUCTURE = UUID(int=5)
DRAFT_STRUCTURE = UUID(int=6)
PUBLIC_LINK = UUID(int=7)
DRAFT_LINK = UUID(int=8)
V2_LINK = UUID(int=9)
SCENE_ID = UUID(int=10)
V1 = UUID(int=11)
V2 = UUID(int=12)
DRAFT = UUID(int=13)
WITHDRAWN = UUID(int=14)
ROOT = UUID(int=15)
MESH = UUID(int=16)
GROUP_WITH_ASSET = UUID(int=17)
MESH_MAPPING = UUID(int=18)
OTHER_VERSION = UUID(int=21)
OTHER_ROOT = UUID(int=22)


def set_owner(connection):
    connection.execute(text("SET LOCAL ROLE atlas_owner"))


def add_version(connection, scene_id, identifier, **values):
    return connection.scalar(
        scene_versions.insert()
        .values(
            scene_id=scene_id,
            version=identifier,
            origin_description=ORIGIN,
            created_at=INSTANT,
            **values,
        )
        .returning(scene_versions.c.id)
    )


def change_version(version_id, **values):
    return update(scene_versions).where(scene_versions.c.id == version_id).values(**values)


def publish(version_id, **values):
    return change_version(
        version_id, publication_state="published", spatial_review_reference=REVIEW, **values
    )


def add_node(version_id, code, **values):
    return scene_nodes.insert().values(
        scene_version_id=version_id,
        code=code,
        node_type=values.pop("node_type", "group"),
        **values,
    )


def change_node(node_id, **values):
    return update(scene_nodes).where(scene_nodes.c.id == node_id).values(**values)


def add_mapping(node_id, link_id, version_id, structure_id=PUBLIC_STRUCTURE, **values):
    return scene_node_anatomy_links.insert().values(
        scene_node_id=node_id,
        anatomy_asset_link_id=link_id,
        asset_version_id=version_id,
        structure_id=structure_id,
        **values,
    )


@pytest.fixture(scope="module")
def scene_engine(postgres_url, synthetic_glb):
    url = postgres_url(MIGRATION_ROLE)
    with pytest.MonkeyPatch.context() as env:
        env.setenv(MIGRATION_DATABASE_ENV, url.render_as_string())
        command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")

    engine = create_engine(url, poolclass=NullPool)
    digest = sha256(synthetic_glb).hexdigest()
    with engine.begin() as connection:
        set_owner(connection)
        connection.execute(
            assets.insert().values(
                id=ASSET_ID,
                code="synthetic-mesh",
                title="Synthetic mesh",
                review_state="published",
            )
        )
        for pin, identifier, eligible in [
            (PIN_V1, "v1", True),
            (PIN_V2, "v2", True),
            (PIN_DRAFT, "draft", False),
        ]:
            connection.execute(
                asset_versions.insert().values(
                    id=pin,
                    asset_id=ASSET_ID,
                    version=identifier,
                    sha256=digest,
                    file_size=len(synthetic_glb),
                    format="glb",
                    mime_type="model/gltf-binary",
                    storage_reference=glb_storage_reference(digest),
                )
            )
            if eligible:
                source_id = connection.scalar(
                    asset_sources.insert()
                    .values(
                        version_id=pin,
                        creator="Fixture",
                        rights_holder="Fixture author",
                        source_reference="Synthetic scene fixture",
                        license_reference="Permission" if pin == PIN_V1 else "Second version permission",
                        attribution="Fixture author" if pin == PIN_V1 else "Second version author",
                        modifications="None",
                    )
                    .returning(asset_sources.c.id)
                )
                connection.execute(
                    update(asset_versions)
                    .where(asset_versions.c.id == pin)
                    .values(
                        validation_state="valid",
                        publication_state="published",
                        rights_verified_at=INSTANT,
                        rights_review_reference="internal-rights-review",
                        primary_source_id=source_id,
                    )
                )

        for structure_id, code, state in [
            (PUBLIC_STRUCTURE, "synthetic-region", "published"),
            (DRAFT_STRUCTURE, "hidden-region", "draft"),
        ]:
            connection.execute(
                anatomical_structures.insert().values(
                    id=structure_id,
                    code=code,
                    structure_kind="region",
                    laterality="none",
                    review_state=state,
                )
            )
        for link_id, structure_id, pin in [
            (PUBLIC_LINK, PUBLIC_STRUCTURE, PIN_V1),
            (DRAFT_LINK, DRAFT_STRUCTURE, PIN_V1),
            (V2_LINK, PUBLIC_STRUCTURE, PIN_V2),
        ]:
            connection.execute(
                anatomy_asset_links.insert().values(
                    id=link_id,
                    structure_id=structure_id,
                    version_id=pin,
                    role=None if link_id == V2_LINK else "context",
                )
            )
        connection.execute(
            scenes.insert().values(
                id=SCENE_ID,
                code="synthetic-overview",
                title="Synthetic Overview",
                review_state="published",
            )
        )
        for version_id, identifier in [
            (V1, "v1"),
            (V2, "v2"),
            (DRAFT, "draft"),
            (WITHDRAWN, "v0"),
        ]:
            add_version(connection, SCENE_ID, identifier, id=version_id)
        connection.execute(add_node(V1, "root", id=ROOT))
        connection.execute(
            add_node(
                V1,
                "mesh-a",
                id=MESH,
                parent_id=ROOT,
                node_type="asset",
                asset_version_id=PIN_V1,
                translation=[0.1, 0.2, 0.3],
                sort_order=1,
            )
        )
        connection.execute(
            add_node(
                V1,
                "mesh-b",
                id=GROUP_WITH_ASSET,
                parent_id=ROOT,
                asset_version_id=PIN_V2,
                sort_order=1,
            )
        )
        connection.execute(add_mapping(MESH, PUBLIC_LINK, PIN_V1, id=MESH_MAPPING))
        connection.execute(add_mapping(GROUP_WITH_ASSET, V2_LINK, PIN_V2))
        connection.execute(add_node(V2, "root"))
        for version_id in (V1, V2, WITHDRAWN):
            connection.execute(publish(version_id))
        connection.execute(change_version(WITHDRAWN, publication_state="withdrawn"))

        for code, scene_state in [
            ("auxiliary", "published"),
            ("hidden", "draft"),
            ("withdrawn", "withdrawn"),
        ]:
            scene_id = connection.scalar(
                scenes.insert()
                .values(code=code, title="Synthetic", review_state=scene_state)
                .returning(scenes.c.id)
            )
            values = {"id": OTHER_VERSION} if code == "auxiliary" else {}
            version_id = add_version(connection, scene_id, "v1", **values)
            if code == "auxiliary":
                connection.execute(add_node(version_id, "root", id=OTHER_ROOT))
            connection.execute(publish(version_id))
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def owner(scene_engine):
    with scene_engine.connect() as connection:
        set_owner(connection)
        yield connection


@pytest.fixture
def client(scene_engine, postgres_url, monkeypatch):
    monkeypatch.setenv(RUNTIME_DATABASE_ENV, postgres_url(RUNTIME_ROLE).render_as_string())
    with TestClient(create_app()) as client:
        yield client


def violates(connection, statement, sqlstate="23514", constraint=None):
    with pytest.raises(IntegrityError) as error, connection.begin_nested():
        connection.execute(statement)
    assert error.value.orig.sqlstate == sqlstate
    if constraint is not None:
        assert error.value.orig.diag.constraint_name == constraint


def read(client, path, **params):
    response = client.get(f"{PREFIX}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_transform_defaults_and_accepted_quaternions(owner):
    node_id = owner.scalar(add_node(DRAFT, "identity").returning(scene_nodes.c.id))
    cols = scene_nodes.c
    defaults = owner.execute(
        select(cols.translation, cols.rotation, cols.scale).where(cols.id == node_id)
    ).one()
    assert defaults == ([0, 0, 0], [0, 0, 0, 1], [1, 1, 1])
    for quaternion in ([0, 0, 0, -1], [0, 0, 0, 1.0000004], [1e-200, 0, 0, 1]):
        owner.execute(change_node(node_id, rotation=quaternion))
        assert owner.scalar(select(cols.rotation).where(cols.id == node_id)) == quaternion


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("translation", [1, 2]),
        ("translation", []),
        ("translation", [[1, 2, 3]]),
        ("translation", [1, 2, None]),
        ("translation", [float("nan"), 0, 0]),
        ("translation", [float("inf"), 0, 0]),
        ("translation", [0, float("-inf"), 0]),
        ("rotation", [0, 0, 1]),
        ("rotation", [0, 0, 0, 0]),
        ("rotation", [0, 0, 0, 1.000001]),
        ("rotation", [0, 0, 0, 1e308]),
        ("rotation", [float("nan"), 0, 0, 1]),
        ("rotation", [0, 0, float("inf"), 1]),
        ("rotation", [0, 0, 0, None]),
        ("scale", [1, 1, 0]),
        ("scale", [1, 1, -1]),
        ("scale", [-1, -1, 1]),
        ("scale", [2, 2, 2]),
        ("scale", [1, 2, 1]),
        ("scale", [1, float("nan"), 1]),
        ("scale", [1, float("inf"), 1]),
    ],
)
def test_reject_invalid_transforms(owner, field, value):
    violates(
        owner,
        add_node(DRAFT, "bad-transform", **{field: value}),
        constraint="ck_scene_nodes_anatomy_scale"
        if field == "scale"
        else f"ck_scene_nodes_{field}",
    )


def test_transform_arrays_must_have_standard_lower_bounds(owner):
    for field, array in [
        ("translation", "[0:2]={0,0,0}"),
        ("rotation", "[0:3]={0,0,0,1}"),
        ("scale", "[0:2]={1,1,1}"),
    ]:
        violates(
            owner,
            add_node(DRAFT, "bad-array", **{field: text(f"'{array}'::double precision[]")}),
            constraint="ck_scene_nodes_anatomy_scale"
            if field == "scale"
            else f"ck_scene_nodes_{field}",
        )


def test_hierarchy_constraints_and_bulk_cycles(owner):
    a, b, c = (UUID(int=value) for value in (100, 101, 102))
    owner.execute(add_node(DRAFT, "a", id=a))
    owner.execute(add_node(DRAFT, "b", id=b, parent_id=a))
    owner.execute(add_node(DRAFT, "c", id=c, parent_id=b))
    parent = select(scene_nodes.c.parent_id).where(scene_nodes.c.id == c)
    assert owner.scalar(parent) == b
    violates(owner, change_node(a, parent_id=a), constraint="ck_scene_nodes_distinct_parent")
    violates(owner, change_node(a, parent_id=c))
    violates(owner, change_node(a, parent_id=ROOT), "23503", "fk_scene_nodes_same_version_parent")
    violates(
        owner, change_node(a, parent_id=OTHER_ROOT), "23503", "fk_scene_nodes_same_version_parent"
    )
    violates(owner, change_node(a, scene_version_id=V1))
    violates(owner, add_node(DRAFT, "a"), "23505", "uq_scene_nodes_version_code")
    bulk = (
        update(scene_nodes)
        .where(scene_nodes.c.id.in_([a, b]))
        .values(parent_id=case((scene_nodes.c.id == a, b), else_=a))
    )
    violates(owner, bulk)
    owner.execute(change_node(c, parent_id=a))
    assert owner.scalar(parent) == a


def test_asset_and_anatomy_pins(owner):
    group = owner.scalar(add_node(DRAFT, "group").returning(scene_nodes.c.id))
    mesh = owner.scalar(
        add_node(DRAFT, "mesh", node_type="asset", asset_version_id=PIN_V1).returning(
            scene_nodes.c.id
        )
    )
    violates(owner, add_node(DRAFT, "missing-pin", node_type="asset"))
    violates(owner, add_node(DRAFT, "invalid-type", node_type="mesh", asset_version_id=PIN_V1))
    violates(
        owner,
        add_mapping(group, PUBLIC_LINK, PIN_V1),
        "23503",
        "fk_scene_node_anatomy_links_node_pin",
    )
    violates(
        owner,
        add_mapping(mesh, V2_LINK, PIN_V1),
        "23503",
        "fk_scene_node_anatomy_links_anatomy_pin",
    )
    violates(
        owner, add_mapping(mesh, V2_LINK, PIN_V2), "23503", "fk_scene_node_anatomy_links_node_pin"
    )
    violates(
        owner,
        add_mapping(mesh, PUBLIC_LINK, PIN_V1, DRAFT_STRUCTURE),
        "23503",
        "fk_scene_node_anatomy_links_anatomy_pin",
    )

    mapping = add_mapping(mesh, PUBLIC_LINK, PIN_V1)
    owner.execute(mapping)
    violates(owner, mapping, "23505", "uq_scene_node_anatomy_links_pair")
    violates(
        owner,
        change_node(mesh, asset_version_id=PIN_V2),
        "23503",
        "fk_scene_node_anatomy_links_node_pin",
    )
    violates(
        owner,
        update(anatomy_asset_links)
        .where(anatomy_asset_links.c.id == PUBLIC_LINK)
        .values(version_id=PIN_DRAFT),
        "23503",
        "fk_scene_node_anatomy_links_anatomy_pin",
    )
    new_structure = owner.scalar(
        anatomical_structures.insert()
        .values(code="other-region", structure_kind="region", laterality="none")
        .returning(anatomical_structures.c.id)
    )
    violates(
        owner,
        update(anatomy_asset_links)
        .where(anatomy_asset_links.c.id == PUBLIC_LINK)
        .values(structure_id=new_structure),
        "23503",
        "fk_scene_node_anatomy_links_anatomy_pin",
    )


def test_publication_prerequisites_and_draft_review_invalidation(owner):
    violates(owner, scenes.update().where(scenes.c.id == SCENE_ID).values(code="renamed"))
    violates(owner, change_version(DRAFT, version="renamed"))
    violates(owner, change_version(DRAFT, origin_description=" \t"))
    violates(owner, change_version(DRAFT, translation_unit="mm"))
    violates(owner, change_version(DRAFT, coordinate_profile="unknown"))
    violates(owner, change_version(DRAFT, publication_state="withdrawn"))
    violates(
        owner,
        change_version(DRAFT, publication_state="published"),
        constraint="ck_scene_versions_publishable",
    )
    direct_publish = scene_versions.insert().values(
        scene_id=SCENE_ID,
        version="instant",
        origin_description=ORIGIN,
        publication_state="published",
        spatial_review_reference=REVIEW,
    )
    violates(owner, direct_publish)

    stored_review = select(scene_versions.c.spatial_review_reference).where(
        scene_versions.c.id == DRAFT
    )
    owner.execute(change_version(DRAFT, spatial_review_reference=REVIEW))
    owner.execute(change_version(DRAFT, origin_description="Updated reference markers"))
    assert owner.scalar(stored_review) is None

    owner.execute(change_version(DRAFT, spatial_review_reference=REVIEW))
    pin = owner.scalar(
        add_node(DRAFT, "draft-pin", asset_version_id=PIN_DRAFT).returning(scene_nodes.c.id)
    )
    assert owner.scalar(stored_review) is None
    violates(owner, publish(DRAFT))

    owner.execute(change_node(pin, asset_version_id=PIN_V1))
    owner.execute(assets.update().where(assets.c.id == ASSET_ID).values(review_state="withdrawn"))
    violates(owner, publish(DRAFT))
    owner.execute(assets.update().where(assets.c.id == ASSET_ID).values(review_state="published"))
    owner.execute(publish(DRAFT))


def test_published_and_withdrawn_graphs_are_immutable(owner):
    mapping_update = update(scene_node_anatomy_links).where(
        scene_node_anatomy_links.c.id == MESH_MAPPING
    )
    node_delete = delete(scene_nodes).where(scene_nodes.c.id == MESH)
    mapping_delete = delete(scene_node_anatomy_links).where(
        scene_node_anatomy_links.c.id == MESH_MAPPING
    )
    for statement in (
        change_version(V1, publication_state="draft"),
        change_version(V1, origin_description="Updated origin"),
        change_version(V1, spatial_review_reference="replacement"),
        change_version(V1, publication_state="withdrawn", origin_description="Updated origin"),
        delete(scene_versions).where(scene_versions.c.id == V1),
        add_node(V1, "new-node"),
        change_node(MESH, translation=[1, 2, 3]),
        change_node(MESH, asset_version_id=PIN_V2),
        change_node(MESH, parent_id=None),
        node_delete,
        add_mapping(GROUP_WITH_ASSET, V2_LINK, PIN_V2),
        mapping_update.values(anatomy_asset_link_id=DRAFT_LINK),
        mapping_update.values(role_snapshot=None),
        mapping_delete,
        text("TRUNCATE atlas.scene_node_anatomy_links"),
    ):
        violates(owner, statement)

    node_before = owner.execute(select(scene_nodes).where(scene_nodes.c.id == MESH)).one()
    mapping_before = owner.execute(
        select(scene_node_anatomy_links).where(scene_node_anatomy_links.c.id == MESH_MAPPING)
    ).one()
    owner.execute(change_version(V1, publication_state="withdrawn"))
    for statement in (
        publish(V1),
        change_version(V1, publication_state="draft"),
        change_node(MESH, translation=[3, 2, 1]),
        node_delete,
        mapping_update.values(role_snapshot="replacement"),
        mapping_delete,
    ):
        violates(owner, statement)
    owner.execute(
        anatomy_asset_links.update()
        .where(anatomy_asset_links.c.id == PUBLIC_LINK)
        .values(role="replacement")
    )
    assert owner.execute(select(scene_nodes).where(scene_nodes.c.id == MESH)).one() == node_before
    assert (
        owner.execute(
            select(scene_node_anatomy_links).where(scene_node_anatomy_links.c.id == MESH_MAPPING)
        ).one()
        == mapping_before
    )


def test_draft_mapping_captures_role_again_when_saved(owner):
    node_id = owner.scalar(
        add_node(DRAFT, "annotated", asset_version_id=PIN_V1).returning(scene_nodes.c.id)
    )
    mapping_id = owner.scalar(
        add_mapping(node_id, PUBLIC_LINK, PIN_V1, role_snapshot="provided").returning(
            scene_node_anatomy_links.c.id
        )
    )
    mapping = scene_node_anatomy_links.c
    saved_role = select(mapping.role_snapshot).where(mapping.id == mapping_id)
    assert owner.execute(saved_role).scalar_one() == "context"

    owner.execute(
        anatomy_asset_links.update()
        .where(anatomy_asset_links.c.id == PUBLIC_LINK)
        .values(role=None)
    )
    assert owner.execute(saved_role).scalar_one() == "context"
    owner.execute(change_version(DRAFT, spatial_review_reference=REVIEW))
    owner.execute(
        scene_node_anatomy_links.update()
        .where(mapping.id == mapping_id)
        .values(role_snapshot="provided")
    )
    assert owner.execute(saved_role).scalar_one() is None
    review = select(scene_versions.c.spatial_review_reference).where(scene_versions.c.id == DRAFT)
    assert owner.execute(review).scalar_one() is None


def test_publication_requires_published_annotated_structures(owner):
    node_id = owner.scalar(
        add_node(DRAFT, "annotated", asset_version_id=PIN_V1).returning(scene_nodes.c.id)
    )
    owner.execute(add_mapping(node_id, DRAFT_LINK, PIN_V1, DRAFT_STRUCTURE))
    violates(owner, publish(DRAFT))
    owner.execute(
        anatomical_structures.update()
        .where(anatomical_structures.c.id == DRAFT_STRUCTURE)
        .values(review_state="published")
    )
    owner.execute(publish(DRAFT))


def concurrent_write(engine, first_statement, second_statement, isolation_level, error_type):
    waiting_pid = Queue()

    def run_second():
        with engine.connect().execution_options(isolation_level=isolation_level) as other:
            set_owner(other)
            other.execute(text("SET LOCAL statement_timeout = '5s'"))
            waiting_pid.put(other.scalar(text("SELECT pg_backend_pid()")))
            other.execute(second_statement)
            other.commit()

    with ThreadPoolExecutor(max_workers=1) as workers, engine.connect() as writer:
        set_owner(writer)
        writer.execute(first_statement)
        second = workers.submit(run_second)
        pid = waiting_pid.get(timeout=5)
        deadline = monotonic() + 3
        while not writer.scalar(
            text("SELECT pg_backend_pid() = ANY(pg_blocking_pids(:pid))"), {"pid": pid}
        ):
            assert not second.done() and monotonic() < deadline, (
                "The second writer did not wait for the first transaction"
            )
            sleep(0.01)
        writer.commit()
        with pytest.raises(error_type) as error:
            second.result(timeout=5)
        return error.value.orig.sqlstate


@pytest.mark.parametrize(
    ("isolation_level", "error_type", "sqlstate"),
    [("READ COMMITTED", IntegrityError, "23514"), ("REPEATABLE READ", OperationalError, "40001")],
)
def test_concurrent_reparenting_cannot_create_a_cycle(
    scene_engine, isolation_level, error_type, sqlstate
):
    with scene_engine.begin() as connection:
        set_owner(connection)
        version_id = add_version(connection, SCENE_ID, isolation_level.lower().replace(" ", "-"))
        first, second = [
            connection.scalar(add_node(version_id, code).returning(scene_nodes.c.id))
            for code in ("first", "second")
        ]
    assert (
        concurrent_write(
            scene_engine,
            change_node(first, parent_id=second),
            change_node(second, parent_id=first),
            isolation_level,
            error_type,
        )
        == sqlstate
    )
    with scene_engine.connect() as connection:
        set_owner(connection)
        assert connection.execute(
            select(scene_nodes.c.code, scene_nodes.c.parent_id)
            .where(scene_nodes.c.scene_version_id == version_id)
            .order_by(scene_nodes.c.code)
        ).all() == [("first", second), ("second", None)]


def test_publication_rechecks_graph_after_a_concurrent_edit(scene_engine):
    with scene_engine.begin() as connection:
        set_owner(connection)
        version_id = add_version(connection, SCENE_ID, "concurrent-publish")
        node_id = connection.scalar(
            add_node(version_id, "mesh", node_type="asset", asset_version_id=PIN_V1).returning(
                scene_nodes.c.id
            )
        )
    assert (
        concurrent_write(
            scene_engine,
            change_node(node_id, asset_version_id=PIN_DRAFT),
            publish(version_id),
            "READ COMMITTED",
            IntegrityError,
        )
        == "23514"
    )


def test_edit_waiting_for_publication_observes_immutability(scene_engine):
    with scene_engine.begin() as connection:
        set_owner(connection)
        version_id = add_version(connection, SCENE_ID, "concurrent-edit")
        node_id = connection.scalar(add_node(version_id, "root").returning(scene_nodes.c.id))
    try:
        assert (
            concurrent_write(
                scene_engine,
                publish(version_id),
                change_node(node_id, translation=[1, 1, 1]),
                "READ COMMITTED",
                IntegrityError,
            )
            == "23514"
        )
    finally:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(change_version(version_id, publication_state="withdrawn"))


def test_publication_checks_concurrent_asset_withdrawal(scene_engine):
    with scene_engine.begin() as connection:
        set_owner(connection)
        version_id = add_version(connection, SCENE_ID, "asset-withdrawal")
        connection.execute(
            add_node(version_id, "mesh", node_type="asset", asset_version_id=PIN_V1)
        )
    asset_status = asset_versions.update().where(asset_versions.c.id == PIN_V1)
    try:
        assert (
            concurrent_write(
                scene_engine,
                asset_status.values(publication_state="withdrawn"),
                publish(version_id),
                "READ COMMITTED",
                IntegrityError,
            )
            == "23514"
        )
    finally:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(asset_status.values(publication_state="published"))


def test_publication_checks_concurrent_anatomy_unpublication(scene_engine):
    with scene_engine.begin() as connection:
        set_owner(connection)
        version_id = add_version(connection, SCENE_ID, "concurrent-anatomy")
        node_id = connection.scalar(
            add_node(version_id, "annotated", asset_version_id=PIN_V1).returning(scene_nodes.c.id)
        )
        connection.execute(add_mapping(node_id, PUBLIC_LINK, PIN_V1))
    status = anatomical_structures.update().where(anatomical_structures.c.id == PUBLIC_STRUCTURE)
    try:
        assert (
            concurrent_write(
                scene_engine,
                status.values(review_state="draft"),
                publish(version_id),
                "READ COMMITTED",
                IntegrityError,
            )
            == "23514"
        )
    finally:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(status.values(review_state="published"))


def test_api_deterministic_pagination_metadata_and_exact_asset_references(client):
    page = read(client, "", limit=1)
    assert (page["limit"], page["offset"], page["has_more"]) == (1, 0, True)
    assert [item["code"] for item in page["items"]] == ["auxiliary"]
    page = read(client, "", limit=1, offset=1)
    assert [item["code"] for item in page["items"]] == ["synthetic-overview"]
    assert page["items"][0]["latest_version"]["version"] == "v2"
    assert page["has_more"] is False

    detail = read(client, DEMO, limit=1, offset=1)
    assert detail["versions"]["has_more"] is False
    version = {
        "version": "v1",
        "coordinate_profile": "atlas-anatomy-y-up-v1",
        "translation_unit": "m",
        "angle_unit": "rad",
        "origin_description": ORIGIN,
        "created_at": "2026-01-01T00:00:00Z",
    }
    assert detail["versions"]["items"] == [version]
    assert read(client, DEMO_V1) == dict(version, code=detail["code"], title=detail["title"])
    assert read(client, DEMO, offset=100)["versions"]["items"] == []

    nodes = f"{DEMO_V1}/nodes"
    first = read(client, nodes, limit=1)
    assert first["scene_code"] == detail["code"]
    assert first["version"] == "v1"
    assert first["has_more"] is True
    assert first["items"] == [
        {
            "code": "root",
            "parent_code": None,
            "node_type": "group",
            "sort_order": 0,
            "transform": {"translation": [0, 0, 0], "rotation": [0, 0, 0, 1], "scale": [1, 1, 1]},
            "asset": None,
            "anatomy_links": [],
        }
    ]
    rest = read(client, nodes, offset=1)
    assert rest["has_more"] is False
    assert [node["code"] for node in rest["items"]] == ["mesh-a", "mesh-b"]
    mesh, group = rest["items"]
    assert mesh["parent_code"] == group["parent_code"] == "root"
    assert mesh["node_type"] == "asset"
    assert mesh["transform"]["translation"] == [0.1, 0.2, 0.3]
    assert mesh["asset"] == {"code": "synthetic-mesh", "version": "v1"}
    assert mesh["anatomy_links"] == [{"structure_code": "synthetic-region", "role": "context"}]
    assert group["node_type"] == "group"
    assert group["asset"] == {"code": "synthetic-mesh", "version": "v2"}
    assert group["anatomy_links"] == [{"structure_code": "synthetic-region", "role": None}]
    assert read(client, nodes, offset=3)["items"] == []


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        ("/missing", "Scene not found."),
        ("/hidden", "Scene not found."),
        ("/withdrawn", "Scene not found."),
        ("/hidden/versions/v1", "Scene version not found."),
        ("/withdrawn/versions/v1/nodes", "Scene version not found."),
        (f"{DEMO}/versions/draft", "Scene version not found."),
        (f"{DEMO}/versions/v0/nodes", "Scene version not found."),
        (f"{DEMO}/versions/missing/nodes", "Scene version not found."),
    ],
)
def test_unpublished_and_withdrawn_scenes_and_versions_return_404(client, path, detail):
    response = client.get(f"{PREFIX}{path}")
    assert response.status_code == 404
    assert response.json() == {"detail": detail}


@pytest.mark.parametrize("withdraw_asset", [False, True])
def test_api_rejects_whole_version_when_any_pinned_asset_is_withdrawn(
    client, scene_engine, withdraw_asset
):
    if withdraw_asset:
        statement = assets.update().where(assets.c.id == ASSET_ID)
        status_column = "review_state"
    else:
        # The last node (not on the first page) is the only node pinning this asset version.
        statement = asset_versions.update().where(asset_versions.c.id == PIN_V2)
        status_column = "publication_state"

    try:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(statement.values({status_column: "withdrawn"}))
        for path in (DEMO_V1, f"{DEMO_V1}/nodes?limit=1", f"{DEMO_V1}/nodes?offset=100"):
            response = client.get(f"{PREFIX}{path}")
            assert response.status_code == 503
            assert response.json() == {"detail": "Scene asset versions unavailable."}
        assert [item["version"] for item in read(client, DEMO)["versions"]["items"]] == ["v2"]
        assert read(client, "")["items"][1]["latest_version"]["version"] == "v2"
    finally:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(statement.values({status_column: "published"}))


def test_published_nodes_return_saved_roles_after_registry_edits(client, scene_engine):
    nodes = f"{DEMO_V1}/nodes"
    before = read(client, nodes)
    link = anatomy_asset_links.c
    change_roles = anatomy_asset_links.update().where(link.id.in_([PUBLIC_LINK, V2_LINK]))
    try:
        with scene_engine.begin() as connection:
            set_owner(connection)
            result = connection.execute(change_roles.values(role="changed"))
            assert result.rowcount == 2
        assert read(client, nodes) == before
    finally:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(
                change_roles.values(role=case((link.id == PUBLIC_LINK, "context"), else_=None))
            )


def test_unpublished_structure_blocks_every_page_until_republished(client, scene_engine):
    nodes = f"{DEMO_V1}/nodes"
    before = read(client, nodes)
    status = anatomical_structures.update().where(anatomical_structures.c.id == PUBLIC_STRUCTURE)
    try:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(status.values(review_state="draft"))
        for path in (DEMO_V1, f"{nodes}?limit=1", f"{nodes}?offset=100"):
            response = client.get(f"{PREFIX}{path}")
            assert response.status_code == 503
            assert response.json() == {"detail": "Scene anatomy annotations unavailable."}
        assert [item["version"] for item in read(client, DEMO)["versions"]["items"]] == ["v2"]
        assert read(client, "")["items"][1]["latest_version"]["version"] == "v2"
    finally:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(status.values(review_state="published"))
    assert read(client, nodes) == before


@pytest.mark.parametrize(
    ("dependency", "detail"),
    [
        ("assets", "Scene asset versions unavailable."),
        ("anatomy", "Scene anatomy annotations unavailable."),
    ],
)
def test_scene_discovery_when_no_published_version_is_eligible(
    client, scene_engine, dependency, detail
):
    code = f"temporarily-unavailable-{dependency}"
    if dependency == "assets":
        status = asset_versions.update().where(asset_versions.c.id == PIN_V1)
        unpublished = status.values(publication_state="withdrawn")
        republished = status.values(publication_state="published")
    else:
        status = anatomical_structures.update().where(
            anatomical_structures.c.id == PUBLIC_STRUCTURE
        )
        unpublished = status.values(review_state="draft")
        republished = status.values(review_state="published")
    with scene_engine.begin() as connection:
        set_owner(connection)
        scene_id = connection.scalar(
            scenes.insert()
            .values(code=code, title="Synthetic", review_state="published")
            .returning(scenes.c.id)
        )
        version_id = add_version(connection, scene_id, "v1")
        node_id = connection.scalar(
            add_node(version_id, "root", asset_version_id=PIN_V1).returning(scene_nodes.c.id)
        )
        connection.execute(add_mapping(node_id, PUBLIC_LINK, PIN_V1))
    assert client.get(f"{PREFIX}/{code}").status_code == 404
    try:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(publish(version_id))
            connection.execute(unpublished)
        assert code not in [item["code"] for item in read(client, "")["items"]]
        response = client.get(f"{PREFIX}/{code}?offset=100")
        assert response.status_code == 503
        assert response.json() == {"detail": detail}
    finally:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(republished)
            connection.execute(change_version(version_id, publication_state="withdrawn"))


MANIFEST = f"{PREFIX}{DEMO_V1}/manifest"
FILE = "/api/v1/assets/synthetic-mesh/versions/v1/file"


@pytest.fixture
def delivery_root(tmp_path, synthetic_glb, monkeypatch):
    root = tmp_path / "assets"
    target = root / glb_storage_reference(sha256(synthetic_glb).hexdigest())
    target.parent.mkdir(parents=True, mode=0o750)
    target.write_bytes(synthetic_glb)
    target.chmod(0o440)
    monkeypatch.setenv("ATLAS_ASSET_STORAGE_ROOT", str(root))
    return target


def test_manifest_exact_pins_roles_and_attribution(
    delivery_root, client, scene_engine, synthetic_glb
):
    with scene_engine.begin() as connection:
        set_owner(connection)
        duplicate_test = add_version(connection, SCENE_ID, "manifest-fixture")
        parent = connection.scalar(
            add_node(duplicate_test, "root").returning(scene_nodes.c.id)
        )
        for code, pin in (("mesh-a", PIN_V1), ("mesh-b", PIN_V2), ("mesh-c", PIN_V1)):
            connection.execute(
                add_node(duplicate_test, code, parent_id=parent, asset_version_id=pin, sort_order=1)
            )
        connection.execute(publish(duplicate_test))
        connection.execute(
            structure_names.insert().values(
                structure_id=PUBLIC_STRUCTURE, language="en", name="Synthetic region",
                is_preferred=True,
            )
        )
        connection.execute(
            anatomy_asset_links.update().where(anatomy_asset_links.c.id == PUBLIC_LINK)
            .values(role="changed")
        )

    result = client.get(MANIFEST)
    assert result.status_code == 200, result.json()
    body = result.json()
    assert result.content == client.get(MANIFEST).content
    assert result.headers["etag"] == f'"{sha256(result.content).hexdigest()}"'
    assert client.get(MANIFEST, headers={"If-None-Match": result.headers["etag"]}).status_code == 304
    assert body["schema_version"] == 1
    assert body["scene"]["coordinate_profile"] == "atlas-anatomy-y-up-v1"
    assert body["scene"]["origin_description"] == ORIGIN
    assert body["scene"]["version"] == "v1"
    assert [(node["code"], node["parent_code"]) for node in body["nodes"]] == [
        ("root", None), ("mesh-a", "root"), ("mesh-b", "root"),
    ]
    assert body["nodes"][1]["transform"] == {
        "translation": [0.1, 0.2, 0.3], "rotation": [0.0, 0.0, 0.0, 1.0],
        "scale": [1.0, 1.0, 1.0],
    }
    assert body["nodes"][1]["asset"] == {"code": "synthetic-mesh", "version": "v1"}
    assert body["nodes"][1]["anatomy_links"] == [
        {"structure_code": "synthetic-region", "role": "context"},
    ]
    assert body["nodes"][2]["anatomy_links"][0]["role"] is None
    assert body["anatomy"][0]["preferred_names"][0]["name"] == "Synthetic region"
    assert body["anatomy"][0]["laterality"] == "none"
    assert [asset["version"] for asset in body["assets"]] == ["v1", "v2"]
    assert body["assets"][0]["download_url"] == FILE
    assert body["assets"][0]["sha256"] == sha256(synthetic_glb).hexdigest()
    assert body["assets"][0]["file_size"] == len(synthetic_glb)
    assert body["assets"][0]["mime_type"] == "model/gltf-binary"
    assert body["assets"][0]["sources"][0]["attribution"] == "Fixture author"
    assert body["assets"][0]["sources"][0]["license_reference"] == "Permission"
    assert body["assets"][1]["sources"][0]["license_reference"] == "Second version permission"
    assert body["assets"][1]["sources"][0]["attribution"] == "Second version author"
    assert b"internal-rights-review" not in result.content
    assert b"internal-spatial-review" not in result.content
    assert b"storage_reference" not in result.content

    separate = client.get(f"{PREFIX}{DEMO}/versions/manifest-fixture/manifest").json()
    assert [asset["version"] for asset in separate["assets"]] == ["v1", "v2"]
    assert [node["asset"]["version"] for node in separate["nodes"][1:]] == ["v1", "v2", "v1"]

    with scene_engine.begin() as connection:
        set_owner(connection)
        connection.execute(
            structure_names.update().where(structure_names.c.structure_id == PUBLIC_STRUCTURE)
            .values(name="Renamed region")
        )
    changed = client.get(MANIFEST, headers={"If-None-Match": result.headers["etag"]})
    assert changed.status_code == 200
    assert changed.headers["etag"] != result.headers["etag"]
    assert changed.json()["anatomy"][0]["preferred_names"][0]["name"] == "Renamed region"


def test_binary_validators_and_storage_errors(delivery_root, client, synthetic_glb):
    success = client.get(FILE, headers={"Range": "bytes=0-1"})
    digest = sha256(synthetic_glb).hexdigest()
    assert success.status_code == 200
    assert success.content == synthetic_glb
    assert success.headers["content-type"] == "model/gltf-binary"
    assert success.headers["content-length"] == str(len(synthetic_glb))
    assert success.headers["etag"] == f'"{digest}"'
    assert success.headers["x-content-type-options"] == "nosniff"
    assert "must-revalidate" in success.headers["cache-control"]
    assert "accept-ranges" not in success.headers
    condition = {"If-None-Match": f'"other,tag", W/"{digest}"'}
    for headers in (condition, {"If-None-Match": "*"}):
        cached = client.get(FILE, headers=headers)
        assert cached.status_code == 304
        assert cached.content == b""
        assert cached.headers["etag"] == success.headers["etag"]

    delivery_root.chmod(0o600)
    delivery_root.write_bytes(synthetic_glb[:-1] + b"x")
    delivery_root.chmod(0o440)
    assert client.get(FILE, headers=condition).status_code == 503

    delivery_root.unlink()
    for path in (FILE, MANIFEST):
        unavailable = client.get(path, headers={"If-None-Match": "*"})
        assert unavailable.status_code == 503
        assert unavailable.headers["cache-control"] == "no-store"
        assert unavailable.json() == {"detail": "Published asset storage unavailable."}


def test_delivery_rechecks_publication_before_using_validators(delivery_root, client, scene_engine):
    asset = client.get(FILE)
    manifest = client.get(MANIFEST)
    assert asset.status_code == manifest.status_code == 200
    for path in (
        f"{PREFIX}/hidden/versions/v1/manifest",
        f"{PREFIX}/withdrawn/versions/v1/manifest",
        f"{PREFIX}{DEMO}/versions/draft/manifest",
        f"{PREFIX}{DEMO}/versions/v0/manifest",
        FILE.replace("/v1/file", "/draft/file"),
    ):
        unavailable = client.get(path, headers={"If-None-Match": "*"})
        assert unavailable.status_code == 404
        assert unavailable.headers["cache-control"] == "no-store"

    pin = asset_versions.update().where(asset_versions.c.id == PIN_V1)
    structure = anatomical_structures.update().where(anatomical_structures.c.id == PUBLIC_STRUCTURE)
    try:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(pin.values(publication_state="withdrawn"))
        assert client.get(FILE, headers={"If-None-Match": asset.headers["etag"]}).status_code == 404
        invalid_scene = client.get(MANIFEST, headers={"If-None-Match": manifest.headers["etag"]})
        assert invalid_scene.status_code == 503
        assert invalid_scene.json() == {"detail": "Scene asset versions unavailable."}
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(pin.values(publication_state="published"))
            connection.execute(structure.values(review_state="draft"))
        invalid_scene = client.get(MANIFEST, headers={"If-None-Match": manifest.headers["etag"]})
        assert invalid_scene.status_code == 503
        assert invalid_scene.json() == {"detail": "Scene anatomy annotations unavailable."}
    finally:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(pin.values(publication_state="published"))
            connection.execute(structure.values(review_state="published"))


@pytest.mark.parametrize(
    ("variable", "message"),
    [
        ("ATLAS_MANIFEST_MAX_NODES", "Scene manifest node limit exceeded."),
        ("ATLAS_MANIFEST_MAX_ASSETS", "Scene manifest asset version limit exceeded."),
        ("ATLAS_MANIFEST_MAX_BYTES", "Scene manifest serialized size limit exceeded."),
    ],
)
def test_manifest_limits(
    delivery_root, scene_engine, postgres_url, monkeypatch, variable, message
):
    monkeypatch.setenv(variable, "1")
    monkeypatch.setenv(RUNTIME_DATABASE_ENV, postgres_url(RUNTIME_ROLE).render_as_string())
    with TestClient(create_app()) as api:
        response = api.get(MANIFEST)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"detail": message}


def test_manifest_build_uses_one_snapshot(delivery_root, client, scene_engine, monkeypatch):
    original = queries.get_version
    pin = asset_versions.update().where(asset_versions.c.id == PIN_V1)

    def withdraw_after_scene_read(*args):
        scene = original(*args)
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(pin.values(publication_state="withdrawn"))
        return scene

    try:
        with monkeypatch.context() as patch:
            patch.setattr(queries, "get_version", withdraw_after_scene_read)
            response = client.get(MANIFEST)
        assert response.status_code == 200
        assert response.json()["assets"][0]["version"] == "v1"
        later = client.get(MANIFEST)
        assert later.status_code == 503
        assert later.json() == {"detail": "Scene asset versions unavailable."}
    finally:
        with scene_engine.begin() as connection:
            set_owner(connection)
            connection.execute(pin.values(publication_state="published"))
