"""Create the Human Anatomy tables and enforce hierarchy integrity."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anatomical_structures",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("laterality", sa.String(length=5), nullable=False),
        sa.Column("review_state", sa.String(length=9), server_default="draft", nullable=False),
        sa.CheckConstraint(
            "code ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'", name="ck_anatomical_structures_code"
        ),
        sa.CheckConstraint(
            "laterality IN ('left', 'right', 'none')", name="ck_structures_laterality"
        ),
        sa.CheckConstraint(
            "review_state IN ('draft', 'published')", name="ck_structures_review_state"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_anatomical_structures_code"),
        schema="atlas",
    )
    op.create_index(
        "ix_anatomical_structures_laterality_code",
        "anatomical_structures",
        ["laterality", "code"],
        unique=False,
        schema="atlas",
    )
    op.create_table(
        "anatomical_systems",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("review_state", sa.String(length=9), server_default="draft", nullable=False),
        sa.CheckConstraint(
            "code ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'", name="ck_anatomical_systems_code"
        ),
        sa.CheckConstraint("name ~ '[^[:space:]]'", name="ck_anatomical_systems_name"),
        sa.CheckConstraint(
            "review_state IN ('draft', 'published')", name="ck_anatomical_systems_review_state"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_anatomical_systems_code"),
        schema="atlas",
    )
    op.create_table(
        "anatomy_hierarchy_write_guard",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "id = '00000000-0000-0000-0000-000000000000'",
            name="ck_anatomy_hierarchy_write_guard_singleton",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="atlas",
    )
    op.create_table(
        "external_identifiers",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("structure_id", sa.Uuid(), nullable=False),
        sa.Column("namespace", sa.String(length=80), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=512), nullable=False),
        sa.Column("source_version", sa.String(length=120), nullable=True),
        sa.CheckConstraint(
            "namespace ~ '^[a-z][a-z0-9]*([._-][a-z0-9]+)*$'",
            name="ck_external_identifiers_namespace",
        ),
        sa.CheckConstraint("source ~ '[^[:space:]]'", name="ck_external_identifiers_source"),
        sa.CheckConstraint(
            "source_version ~ '[^[:space:]]'", name="ck_external_identifiers_source_version"
        ),
        sa.CheckConstraint("value ~ '[^[:space:]]'", name="ck_external_identifiers_value"),
        sa.ForeignKeyConstraint(
            ["structure_id"], ["atlas.anatomical_structures.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "structure_id", "namespace", "value", name="uq_external_identifiers_mapping"
        ),
        schema="atlas",
    )
    op.create_index(
        "ix_external_identifiers_namespace_value",
        "external_identifiers",
        ["namespace", "value"],
        unique=False,
        schema="atlas",
    )
    op.create_table(
        "structure_names",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("structure_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_preferred", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint("language IN ('la', 'en', 'az')", name="ck_structure_names_language"),
        sa.CheckConstraint("name ~ '[^[:space:]]'", name="ck_structure_names_name"),
        sa.ForeignKeyConstraint(
            ["structure_id"], ["atlas.anatomical_structures.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("structure_id", "language", "name", name="uq_structure_names_name"),
        schema="atlas",
    )
    op.create_index(
        "uq_structure_names_preferred",
        "structure_names",
        ["structure_id", "language"],
        unique=True,
        schema="atlas",
        postgresql_where=sa.text("is_preferred"),
    )
    op.create_table(
        "structure_relations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=7), nullable=False),
        sa.CheckConstraint(
            "relation_type IN ('part_of', 'is_a')", name="ck_structure_relations_type"
        ),
        sa.CheckConstraint("source_id <> target_id", name="ck_structure_relations_distinct_nodes"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["atlas.anatomical_structures.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_id"], ["atlas.anatomical_structures.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id", "target_id", "relation_type", name="uq_structure_relations_edge"
        ),
        schema="atlas",
    )
    op.create_index(
        "ix_structure_relations_target_source",
        "structure_relations",
        ["target_id", "source_id"],
        unique=False,
        schema="atlas",
    )
    op.create_table(
        "structure_systems",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("structure_id", sa.Uuid(), nullable=False),
        sa.Column("system_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["structure_id"], ["atlas.anatomical_structures.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["system_id"], ["atlas.anatomical_systems.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("structure_id", "system_id", name="uq_structure_systems_membership"),
        schema="atlas",
    )
    op.create_index(
        "ix_structure_systems_system_structure",
        "structure_systems",
        ["system_id", "structure_id"],
        unique=False,
        schema="atlas",
    )

    op.execute(
        "INSERT INTO atlas.anatomy_hierarchy_write_guard (id) "
        "VALUES ('00000000-0000-0000-0000-000000000000')"
    )
    op.execute("REVOKE ALL ON TABLE atlas.anatomy_hierarchy_write_guard FROM atlas_app")
    create_integrity_triggers()


def create_integrity_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION atlas.check_anatomy_hierarchy() RETURNS trigger
        LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM changed_edges) THEN
                RETURN NULL;
            END IF;

            -- Take a write lock so competing hierarchy changes serialize. A stale
            -- repeatable-read or serializable snapshot fails the update and must retry.
            UPDATE atlas.anatomy_hierarchy_write_guard SET id = id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Anatomy hierarchy write guard is missing';
            END IF;

            -- At READ COMMITTED, this query sees changes committed by the previous
            -- writer while this transaction waited. UNION also terminates on a cycle.
            IF EXISTS (
                WITH RECURSIVE reachable(origin_id, node_id) AS (
                    SELECT source_id, target_id FROM changed_edges
                    UNION
                    SELECT reachable.origin_id, edge.target_id
                    FROM reachable
                    JOIN atlas.structure_relations AS edge
                        ON edge.source_id = reachable.node_id
                )
                SELECT 1 FROM reachable WHERE origin_id = node_id
            ) THEN
                RAISE EXCEPTION 'Anatomical hierarchy cannot contain a cycle'
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_structure_relations_acyclic';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    for event in ("INSERT", "UPDATE"):
        op.execute(
            f"CREATE TRIGGER check_anatomy_hierarchy_{event.lower()} "
            f"AFTER {event} ON atlas.structure_relations "
            "REFERENCING NEW TABLE AS changed_edges FOR EACH STATEMENT "
            "EXECUTE FUNCTION atlas.check_anatomy_hierarchy()"
        )

    op.execute(
        """
        CREATE FUNCTION atlas.reject_anatomy_code_change() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'Anatomy public codes are immutable'
                USING ERRCODE = '23514';
        END;
        $$
        """
    )
    for table in ("anatomical_systems", "anatomical_structures"):
        op.execute(
            f"CREATE TRIGGER reject_anatomy_code_change BEFORE UPDATE OF code ON atlas.{table} "
            "FOR EACH ROW WHEN (OLD.code IS DISTINCT FROM NEW.code) "
            "EXECUTE FUNCTION atlas.reject_anatomy_code_change()"
        )


def downgrade() -> None:
    # Dropping the tables also drops their indexes and triggers.
    op.drop_table("structure_systems", schema="atlas")
    op.drop_table("structure_relations", schema="atlas")
    op.drop_table("structure_names", schema="atlas")
    op.drop_table("external_identifiers", schema="atlas")
    op.drop_table("anatomical_systems", schema="atlas")
    op.drop_table("anatomical_structures", schema="atlas")
    op.execute("DROP FUNCTION atlas.check_anatomy_hierarchy()")
    op.execute("DROP FUNCTION atlas.reject_anatomy_code_change()")
    op.drop_table("anatomy_hierarchy_write_guard", schema="atlas")
