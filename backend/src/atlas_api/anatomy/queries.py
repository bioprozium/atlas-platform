"""Read queries for published Human Anatomy data."""

from collections import defaultdict
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Connection, RowMapping, Select, or_, select

from atlas_api.anatomy.models import (
    anatomical_structures,
    anatomical_systems,
    external_identifiers,
    structure_names,
    structure_relations,
    structure_systems,
)
from atlas_api.anatomy.schemas import (
    Direction,
    ExternalIdentifier,
    Page,
    Pagination,
    RelationFilters,
    StructureDetail,
    StructureFilters,
    StructureName,
    StructureRelation,
    StructureSummary,
    System,
    SystemFilters,
)

systems = anatomical_systems.c
structures = anatomical_structures.c
names = structure_names.c
memberships = structure_systems.c
identifiers = external_identifiers.c
relations = structure_relations.c


def fetch_page(
    connection: Connection, query: Select, filters: Pagination
) -> tuple[Sequence[RowMapping], bool]:
    rows = (
        connection.execute(query.limit(filters.limit + 1).offset(filters.offset)).mappings().all()
    )
    return rows[: filters.limit], len(rows) > filters.limit


def list_systems(connection: Connection, filters: SystemFilters) -> Page[System]:
    query = select(systems.code, systems.name).where(systems.review_state == "published")
    if filters.q is not None:
        query = query.where(
            or_(
                systems.code.icontains(filters.q, autoescape=True),
                systems.name.icontains(filters.q, autoescape=True),
            )
        )

    rows, has_more = fetch_page(connection, query.order_by(systems.code), filters)
    return Page[System](
        items=[System.model_validate(row) for row in rows],
        limit=filters.limit,
        offset=filters.offset,
        has_more=has_more,
    )


def get_system(connection: Connection, code: str) -> System | None:
    row = (
        connection.execute(
            select(systems.code, systems.name).where(
                systems.code == code, systems.review_state == "published"
            )
        )
        .mappings()
        .one_or_none()
    )
    return System.model_validate(row) if row is not None else None


def list_structures(connection: Connection, filters: StructureFilters) -> Page[StructureSummary]:
    query = select(structures.id, structures.code, structures.laterality).where(
        structures.review_state == "published"
    )
    if filters.system_code is not None:
        membership = (
            select(1)
            .select_from(structure_systems.join(anatomical_systems))
            .where(
                memberships.structure_id == structures.id,
                systems.code == filters.system_code,
                systems.review_state == "published",
            )
        )
        query = query.where(membership.exists())
    if filters.laterality is not None:
        query = query.where(structures.laterality == filters.laterality)
    if filters.language is not None or filters.q is not None:
        name_match = select(1).where(names.structure_id == structures.id)
        if filters.language is not None:
            name_match = name_match.where(names.language == filters.language)
        if filters.q is not None:
            name_match = name_match.where(names.name.icontains(filters.q, autoescape=True))
        query = query.where(name_match.exists())

    rows, has_more = fetch_page(connection, query.order_by(structures.code), filters)
    preferred_names: dict[UUID, list[StructureName]] = defaultdict(list)
    if rows:
        for row in connection.execute(
            select(names.structure_id, names.language, names.name, names.is_preferred)
            .where(names.structure_id.in_([item.id for item in rows]), names.is_preferred)
            .order_by(names.language)
        ).mappings():
            preferred_names[row.structure_id].append(StructureName.model_validate(row))

    return Page[StructureSummary](
        items=[
            StructureSummary(
                code=row.code, laterality=row.laterality, preferred_names=preferred_names[row.id]
            )
            for row in rows
        ],
        limit=filters.limit,
        offset=filters.offset,
        has_more=has_more,
    )


def published_structure(connection: Connection, code: str) -> RowMapping | None:
    return (
        connection.execute(
            select(structures.id, structures.code, structures.laterality).where(
                structures.code == code, structures.review_state == "published"
            )
        )
        .mappings()
        .one_or_none()
    )


def get_structure(connection: Connection, code: str) -> StructureDetail | None:
    structure = published_structure(connection, code)
    if structure is None:
        return None

    name_rows = connection.execute(
        select(names.language, names.name, names.is_preferred)
        .where(names.structure_id == structure.id)
        .order_by(names.language, names.is_preferred.desc(), names.name)
    ).mappings()
    system_rows = connection.execute(
        select(systems.code, systems.name)
        .select_from(anatomical_systems.join(structure_systems))
        .where(memberships.structure_id == structure.id, systems.review_state == "published")
        .order_by(systems.code)
    ).mappings()
    identifier_rows = connection.execute(
        select(
            identifiers.namespace, identifiers.value, identifiers.source, identifiers.source_version
        )
        .where(identifiers.structure_id == structure.id)
        .order_by(identifiers.namespace, identifiers.value)
    ).mappings()

    return StructureDetail(
        code=structure.code,
        laterality=structure.laterality,
        names=[StructureName.model_validate(row) for row in name_rows],
        systems=[System.model_validate(row) for row in system_rows],
        external_identifiers=[ExternalIdentifier.model_validate(row) for row in identifier_rows],
    )


def list_relations(
    connection: Connection, code: str, filters: RelationFilters
) -> Page[StructureRelation] | None:
    structure = published_structure(connection, code)
    if structure is None:
        return None

    source = anatomical_structures.alias("source")
    target = anatomical_structures.alias("target")
    query = (
        select(
            source.c.code.label("source_code"),
            relations.relation_type,
            target.c.code.label("target_code"),
        )
        .select_from(
            structure_relations.join(source, relations.source_id == source.c.id).join(
                target, relations.target_id == target.c.id
            )
        )
        .where(source.c.review_state == "published", target.c.review_state == "published")
    )

    incoming = relations.target_id == structure.id
    outgoing = relations.source_id == structure.id
    if filters.direction == Direction.INCOMING:
        query = query.where(incoming)
    elif filters.direction == Direction.OUTGOING:
        query = query.where(outgoing)
    else:
        query = query.where(or_(incoming, outgoing))
    if filters.relation_type is not None:
        query = query.where(relations.relation_type == filters.relation_type)

    ordered = query.order_by(source.c.code, target.c.code, relations.relation_type)
    rows, has_more = fetch_page(connection, ordered, filters)
    return Page[StructureRelation](
        items=[StructureRelation.model_validate(row) for row in rows],
        limit=filters.limit,
        offset=filters.offset,
        has_more=has_more,
    )
