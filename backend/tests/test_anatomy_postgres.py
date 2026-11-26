"""Integration tests for the anatomy schema and public read API."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from time import monotonic, sleep
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.pool import NullPool

from atlas_api.anatomy.models import (
    StructureKind,
    anatomical_structures,
    anatomical_systems,
    external_identifiers,
    structure_names,
    structure_relations,
    structure_systems,
)
from atlas_api.config import (
    MIGRATION_DATABASE_ENV,
    MIGRATION_ROLE,
    RUNTIME_DATABASE_ENV,
    RUNTIME_ROLE,
)
from atlas_api.main import create_app

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[1]
PREFIX = "/api/v1/anatomy"

SYSTEMS = {
    "cardiovascular": "Cardiovascular system",
    "hematopoietic": "Hematopoietic system",
    "skeletal": "Skeletal system",
    "draft-system": "Draft system",
}
STRUCTURES = {
    "bone-marrow": ("tissue", "none"),
    "femur": ("bone", "none"),
    "heart": ("organ", "none"),
    "left-femur": ("bone", "left"),
    "right-femur": ("bone", "right"),
    "skeleton": ("aggregate", "none"),
    "draft-ventricle": ("organ_part", "left"),
}
SYSTEM_IDS = {code: UUID(int=index) for index, code in enumerate(SYSTEMS, start=1)}
STRUCTURE_IDS = {code: UUID(int=index) for index, code in enumerate(STRUCTURES, start=11)}
UNKNOWN_ID = UUID(int=999)
FIXTURE_IDENTIFIER = {
    "namespace": "test",
    "value": "fixture-femur",
    "source": "Synthetic integration fixture",
    "source_version": "1",
}


def edge(source, relation_type, target):
    return {
        "source_id": STRUCTURE_IDS[source],
        "target_id": STRUCTURE_IDS[target],
        "relation_type": relation_type,
    }


@pytest.fixture(scope="module")
def anatomy_engine(postgres_url):
    url = postgres_url(MIGRATION_ROLE)
    with pytest.MonkeyPatch.context() as env:
        env.setenv(MIGRATION_DATABASE_ENV, url.render_as_string())
        command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")

    engine = create_engine(url, poolclass=NullPool)
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE atlas_owner"))
        connection.execute(
            anatomical_systems.insert(),
            [
                {
                    "id": SYSTEM_IDS[code],
                    "code": code,
                    "name": name,
                    "review_state": "draft" if code == "draft-system" else "published",
                }
                for code, name in SYSTEMS.items()
            ],
        )
        connection.execute(
            anatomical_structures.insert(),
            [
                {
                    "id": STRUCTURE_IDS[code],
                    "code": code,
                    "structure_kind": kind,
                    "laterality": laterality,
                    "review_state": "draft" if code == "draft-ventricle" else "published",
                }
                for code, (kind, laterality) in STRUCTURES.items()
            ],
        )
        connection.execute(
            structure_names.insert(),
            [
                {
                    "structure_id": STRUCTURE_IDS[code],
                    "language": language,
                    "name": name,
                    "is_preferred": preferred,
                }
                for code, language, name, preferred in [
                    ("left-femur", "la", "Femur sinistrum", True),
                    ("left-femur", "en", "Left femur", True),
                    ("left-femur", "en", "Left thigh bone", False),
                    ("left-femur", "az", "Sol bud sümüyü", True),
                    ("right-femur", "en", "Right femur", True),
                    ("heart", "en", "Heart", True),
                    ("femur", "en", "Femur", True),
                    ("bone-marrow", "en", "Bone marrow", True),
                    ("skeleton", "en", "Skeleton", True),
                ]
            ],
        )
        connection.execute(
            structure_systems.insert(),
            [
                {"structure_id": STRUCTURE_IDS[structure], "system_id": SYSTEM_IDS[system]}
                for structure, system in [
                    ("bone-marrow", "hematopoietic"),
                    ("bone-marrow", "skeletal"),
                    ("heart", "cardiovascular"),
                    ("heart", "draft-system"),
                    ("left-femur", "skeletal"),
                    ("right-femur", "skeletal"),
                ]
            ],
        )
        connection.execute(
            structure_relations.insert(),
            [
                edge("left-femur", "is_a", "femur"),
                edge("left-femur", "part_of", "skeleton"),
                edge("right-femur", "is_a", "femur"),
                edge("draft-ventricle", "part_of", "heart"),
            ],
        )
        connection.execute(
            external_identifiers.insert(),
            [
                dict(structure_id=STRUCTURE_IDS[code], **FIXTURE_IDENTIFIER)
                for code in ("left-femur", "right-femur")
            ],
        )

    return engine


@pytest.fixture
def owner(anatomy_engine):
    with anatomy_engine.connect() as connection:
        connection.execute(text("SET LOCAL ROLE atlas_owner"))
        yield connection


@pytest.fixture
def client(anatomy_engine, monkeypatch, postgres_url):
    monkeypatch.setenv(RUNTIME_DATABASE_ENV, postgres_url(RUNTIME_ROLE).render_as_string())
    with TestClient(create_app()) as client:
        assert client.app.state.database_engine.pool.size() == 2
        yield client


def assert_constraint(connection, statement, sqlstate, constraint=None):
    with pytest.raises(IntegrityError) as error, connection.begin_nested():
        connection.execute(statement)
    assert error.value.orig.sqlstate == sqlstate
    if constraint is not None:
        assert error.value.orig.diag.constraint_name == constraint


def test_public_codes_review_and_laterality_constraints(owner):
    identifier, state = owner.execute(
        anatomical_structures.insert()
        .values(code="sternum", structure_kind="bone", laterality="none")
        .returning(anatomical_structures.c.id, anatomical_structures.c.review_state)
    ).one()
    assert isinstance(identifier, UUID)
    assert state == "draft"
    for table, values in [
        (anatomical_structures, {"code": "heart", "structure_kind": "organ", "laterality": "none"}),
        (anatomical_systems, {"code": "skeletal", "name": "Skeletal system"}),
    ]:
        assert_constraint(owner, table.insert().values(**values), "23505")
        assert_constraint(owner, table.insert().values(**(values | {"code": "Bad code"})), "23514")
        assert_constraint(
            owner,
            update(table).where(table.c.code == values["code"]).values(code="renamed"),
            "23514",
        )
        assert_constraint(
            owner,
            table.insert().values(**(values | {"code": "new-code", "review_state": "invalid"})),
            "23514",
        )

    assert_constraint(
        owner,
        anatomical_structures.insert().values(
            code="new-code", structure_kind="bone", laterality="both"
        ),
        "23514",
        "ck_structures_laterality",
    )


def test_structure_kind_is_required_and_controlled(owner):
    insertion = anatomical_structures.insert().values(code="new-structure", laterality="none")
    assert_constraint(owner, insertion, "23502")
    assert_constraint(
        owner,
        insertion.values(structure_kind="invented"),
        "23514",
        "ck_anatomical_structures_kind",
    )
    for kind in StructureKind:
        stored = owner.scalar(
            insertion.values(
                code=f"test-{kind.value.replace('_', '-')}", structure_kind=kind
            ).returning(anatomical_structures.c.structure_kind)
        )
        assert stored == kind


def test_names_memberships_and_identifier_constraints(owner):
    left = STRUCTURE_IDS["left-femur"]
    skeletal = SYSTEM_IDS["skeletal"]
    name = structure_names.insert().values(structure_id=left, language="en", name="Left leg bone")
    assert_constraint(
        owner, name.values(is_preferred=True), "23505", "uq_structure_names_preferred"
    )
    assert_constraint(owner, name.values(name="Left thigh bone"), "23505")
    assert_constraint(owner, name.values(language="fr"), "23514")
    assert_constraint(owner, name.values(name=" \t"), "23514")
    assert_constraint(owner, name.values(structure_id=UNKNOWN_ID), "23503")

    membership = structure_systems.insert().values(structure_id=left, system_id=skeletal)
    assert_constraint(owner, membership, "23505")
    assert_constraint(owner, membership.values(system_id=UNKNOWN_ID), "23503")
    assert_constraint(owner, membership.values(structure_id=UNKNOWN_ID), "23503")

    identifier = external_identifiers.insert().values(structure_id=left, **FIXTURE_IDENTIFIER)
    assert_constraint(owner, identifier, "23505")
    assert_constraint(owner, identifier.values(namespace="Invalid"), "23514")
    assert_constraint(owner, identifier.values(source=" "), "23514")
    assert_constraint(owner, identifier.values(structure_id=UNKNOWN_ID), "23503")


def test_hierarchy_constraints_and_cycles(owner):
    insertion = structure_relations.insert()
    assert_constraint(owner, insertion.values(edge("left-femur", "is_a", "femur")), "23505")
    assert_constraint(
        owner,
        insertion.values(edge("heart", "is_a", "heart")),
        "23514",
        "ck_structure_relations_distinct_nodes",
    )
    assert_constraint(
        owner,
        insertion.values(edge("left-femur", "has_a", "femur")),
        "23514",
        "ck_structure_relations_type",
    )
    assert_constraint(
        owner,
        insertion.values(edge("left-femur", "is_a", "femur") | {"target_id": UNKNOWN_ID}),
        "23503",
    )
    assert_constraint(
        owner,
        insertion.values(edge("femur", "is_a", "left-femur")),
        "23514",
        "ck_structure_relations_acyclic",
    )
    assert_constraint(
        owner,
        insertion.values(edge("skeleton", "is_a", "left-femur")),
        "23514",
        "ck_structure_relations_acyclic",
    )

    assert_constraint(
        owner,
        insertion.values(
            [
                edge("femur", "part_of", "skeleton"),
                edge("skeleton", "part_of", "right-femur"),
            ]
        ),
        "23514",
        "ck_structure_relations_acyclic",
    )
    assert_constraint(
        owner,
        update(structure_relations)
        .where(
            structure_relations.c.source_id == STRUCTURE_IDS["left-femur"],
            structure_relations.c.relation_type == "part_of",
        )
        .values(edge("femur", "part_of", "left-femur")),
        "23514",
        "ck_structure_relations_acyclic",
    )


@pytest.mark.parametrize(
    ("isolation_level", "error_type", "sqlstate"),
    [("READ COMMITTED", IntegrityError, "23514"), ("REPEATABLE READ", OperationalError, "40001")],
)
def test_concurrent_writes_cannot_create_a_cycle(
    anatomy_engine, isolation_level, error_type, sqlstate
):
    relations = structure_relations
    waiting_pid = Queue()

    def insert_reverse_edge():
        with anatomy_engine.connect().execution_options(isolation_level=isolation_level) as other:
            other.execute(text("SET LOCAL ROLE atlas_owner"))
            other.execute(text("SET LOCAL statement_timeout = '5s'"))
            waiting_pid.put(other.scalar(text("SELECT pg_backend_pid()")))
            other.execute(relations.insert().values(edge("skeleton", "part_of", "femur")))

    committed_edge = None
    try:
        with ThreadPoolExecutor(max_workers=1) as workers, anatomy_engine.connect() as writer:
            writer.execute(text("SET LOCAL ROLE atlas_owner"))
            created = writer.scalar(
                relations.insert()
                .values(edge("femur", "part_of", "skeleton"))
                .returning(relations.c.id)
            )
            reverse = workers.submit(insert_reverse_edge)
            pid = waiting_pid.get(timeout=5)
            deadline = monotonic() + 3
            while not writer.scalar(
                text("SELECT pg_backend_pid() = ANY(pg_blocking_pids(:pid))"), {"pid": pid}
            ):
                assert not reverse.done() and monotonic() < deadline, (
                    "Writer did not wait for the guard"
                )
                sleep(0.01)

            writer.commit()
            committed_edge = created
            with pytest.raises(error_type) as error:
                reverse.result(timeout=5)
            assert error.value.orig.sqlstate == sqlstate
    finally:
        if committed_edge is not None:
            with anatomy_engine.begin() as cleanup:
                cleanup.execute(text("SET LOCAL ROLE atlas_owner"))
                cleanup.execute(delete(relations).where(relations.c.id == committed_edge))


def read(client, path, params=None):
    response = client.get(f"{PREFIX}{path}", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def codes(page):
    return [item["code"] for item in page["items"]]


def test_system_list_detail_filters_and_pagination(client):
    page = read(client, "/systems", {"limit": 1})
    assert page == {
        "items": [{"code": "cardiovascular", "name": "Cardiovascular system"}],
        "limit": 1,
        "offset": 0,
        "has_more": True,
    }
    page = read(client, "/systems", {"limit": 2, "offset": 1, "q": "SYSTEM"})
    assert codes(page) == ["hematopoietic", "skeletal"]
    assert page["has_more"] is False
    assert read(client, "/systems/skeletal") == {"code": "skeletal", "name": "Skeletal system"}
    assert codes(read(client, "/systems", {"q": "%"})) == []


def test_structure_list_filters_and_pagination(client):
    page = read(client, "/structures", {"limit": 2})
    assert codes(page) == ["bone-marrow", "femur"]
    assert [item["structure_kind"] for item in page["items"]] == ["tissue", "bone"]
    assert page["has_more"] is True
    page = read(client, "/structures", {"offset": 2})
    assert codes(page) == ["heart", "left-femur", "right-femur", "skeleton"]
    assert [item["structure_kind"] for item in page["items"]] == [
        "organ",
        "bone",
        "bone",
        "aggregate",
    ]
    assert page["has_more"] is False
    assert {name["language"] for name in page["items"][1]["preferred_names"]} == {"la", "en", "az"}
    assert all(name["is_preferred"] for name in page["items"][1]["preferred_names"])

    for filters, expected in [
        ({"system_code": "skeletal"}, ["bone-marrow", "left-femur", "right-femur"]),
        ({"system_code": "draft-system"}, []),
        ({"laterality": "none", "system_code": "cardiovascular"}, ["heart"]),
        ({"laterality": "right"}, ["right-femur"]),
        ({"laterality": "left", "language": "az", "q": "bud sümüyü"}, ["left-femur"]),
        ({"language": "en", "q": "THIGH BONE"}, ["left-femur"]),
        ({"language": "az", "q": "thigh bone"}, []),
        ({"q": "_"}, []),
        ({"offset": 100}, []),
    ]:
        assert codes(read(client, "/structures", filters)) == expected


def test_structure_detail_names_memberships_and_identifier_provenance(client):
    femur = read(client, "/structures/left-femur")
    assert femur == {
        "code": "left-femur",
        "structure_kind": "bone",
        "laterality": "left",
        "names": [
            {"language": "az", "name": "Sol bud sümüyü", "is_preferred": True},
            {"language": "en", "name": "Left femur", "is_preferred": True},
            {"language": "en", "name": "Left thigh bone", "is_preferred": False},
            {"language": "la", "name": "Femur sinistrum", "is_preferred": True},
        ],
        "systems": [{"code": "skeletal", "name": "Skeletal system"}],
        "external_identifiers": [FIXTURE_IDENTIFIER],
    }
    assert read(client, "/structures/right-femur")["external_identifiers"] == [FIXTURE_IDENTIFIER]
    assert [item["code"] for item in read(client, "/structures/bone-marrow")["systems"]] == [
        "hematopoietic",
        "skeletal",
    ]
    heart = read(client, "/structures/heart")
    assert heart["structure_kind"] == "organ"
    assert heart["systems"] == [{"code": "cardiovascular", "name": "Cardiovascular system"}]


def test_relation_order_directions_filters_and_pagination(client):
    path = "/structures/left-femur/relations"
    page = read(client, path, {"limit": 1})
    assert page == {
        "items": [{"source_code": "left-femur", "relation_type": "is_a", "target_code": "femur"}],
        "limit": 1,
        "offset": 0,
        "has_more": True,
    }
    page = read(client, path, {"direction": "outgoing", "limit": 1, "offset": 1})
    assert page["items"] == [
        {"source_code": "left-femur", "relation_type": "part_of", "target_code": "skeleton"}
    ]
    assert page["has_more"] is False
    assert read(client, path, {"direction": "incoming"})["items"] == []
    incoming = read(
        client, "/structures/femur/relations", {"direction": "incoming", "relation_type": "is_a"}
    )
    assert [item["source_code"] for item in incoming["items"]] == ["left-femur", "right-femur"]
    assert read(client, path, {"relation_type": "part_of"})["items"] == page["items"]
    assert read(client, "/structures/heart/relations")["items"] == []


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        ("/systems/unknown", "Anatomical system not found."),
        ("/systems/draft-system", "Anatomical system not found."),
        ("/structures/unknown", "Anatomical structure not found."),
        ("/structures/draft-ventricle", "Anatomical structure not found."),
        ("/structures/unknown/relations", "Anatomical structure not found."),
        ("/structures/draft-ventricle/relations", "Anatomical structure not found."),
    ],
)
def test_missing_or_draft_objects_return_404(client, path, detail):
    response = client.get(f"{PREFIX}{path}")
    assert response.status_code == 404
    assert response.json() == {"detail": detail}
