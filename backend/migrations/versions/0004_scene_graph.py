"""Create versioned scene graphs with spatial and publication constraints."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION atlas.scene_vector_is_finite(
            coordinates double precision[], dimensions integer
        ) RETURNS boolean
        LANGUAGE sql IMMUTABLE AS $$
            SELECT array_ndims(coordinates) IS NOT DISTINCT FROM 1
                AND array_lower(coordinates, 1) IS NOT DISTINCT FROM 1
                AND cardinality(coordinates) = dimensions
                AND NOT EXISTS (
                    SELECT 1 FROM unnest(coordinates) AS input(component)
                    WHERE component IS NULL
                        OR NOT (component > '-Infinity' AND component < 'Infinity')
                )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION atlas.scene_quaternion_is_unit(rotation double precision[])
        RETURNS boolean
        LANGUAGE sql IMMUTABLE AS $$
            SELECT CASE WHEN atlas.scene_vector_is_finite(rotation, 4) THEN (
                -- Numeric arithmetic avoids overflow and underflow on input doubles.
                SELECT abs(sum(component::numeric * component::numeric) - 1) <= 0.000001
                FROM unnest(rotation) AS input(component)
            ) ELSE false END
        $$
        """
    )
    op.create_table(
        "scenes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("review_state", sa.String(9), server_default="draft", nullable=False),
        sa.UniqueConstraint("code", name="uq_scenes_code"),
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'", name="ck_scenes_code"),
        sa.CheckConstraint("title ~ '[^[:space:]]'", name="ck_scenes_title"),
        sa.CheckConstraint(
            "review_state IN ('draft', 'published', 'withdrawn')", name="ck_scenes_review_state"
        ),
        schema="atlas",
    )
    op.create_table(
        "scene_versions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("scene_id", sa.Uuid(), sa.ForeignKey("atlas.scenes.id"), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column(
            "coordinate_profile",
            sa.String(80),
            server_default="atlas-anatomy-y-up-v1",
            nullable=False,
        ),
        sa.Column("translation_unit", sa.String(3), server_default="m", nullable=False),
        sa.Column("angle_unit", sa.String(3), server_default="rad", nullable=False),
        sa.Column("origin_description", sa.Text(), nullable=False),
        sa.Column("spatial_review_reference", sa.Text()),
        sa.Column("publication_state", sa.String(9), server_default="draft", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("scene_id", "version", name="uq_scene_versions_identifier"),
        sa.CheckConstraint(
            "version ~ '^[a-z0-9]+([._-][a-z0-9]+)*$'", name="ck_scene_versions_identifier"
        ),
        sa.CheckConstraint(
            "coordinate_profile = 'atlas-anatomy-y-up-v1' "
            "AND translation_unit = 'm' AND angle_unit = 'rad'",
            name="ck_scene_versions_coordinate_profile",
        ),
        sa.CheckConstraint(
            "origin_description ~ '[^[:space:]]'", name="ck_scene_versions_origin_description"
        ),
        sa.CheckConstraint(
            "spatial_review_reference ~ '[^[:space:]]'", name="ck_scene_versions_review_reference"
        ),
        sa.CheckConstraint(
            "publication_state IN ('draft', 'published', 'withdrawn')",
            name="ck_scene_versions_publication",
        ),
        sa.CheckConstraint(
            "publication_state = 'draft' OR spatial_review_reference IS NOT NULL",
            name="ck_scene_versions_publishable",
        ),
        schema="atlas",
    )
    op.create_index(
        "ix_scene_versions_listing",
        "scene_versions",
        ["scene_id", "publication_state", "created_at", "version"],
        schema="atlas",
    )
    op.create_table(
        "scene_nodes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "scene_version_id", sa.Uuid(), sa.ForeignKey("atlas.scene_versions.id"), nullable=False
        ),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("parent_id", sa.Uuid()),
        sa.Column("node_type", sa.String(5), nullable=False),
        sa.Column("asset_version_id", sa.Uuid(), sa.ForeignKey("atlas.asset_versions.id")),
        sa.Column(
            "translation",
            sa.ARRAY(sa.Double()),
            server_default=sa.text("'{0,0,0}'"),
            nullable=False,
        ),
        sa.Column(
            "rotation",
            sa.ARRAY(sa.Double()),
            server_default=sa.text("'{0,0,0,1}'"),
            nullable=False,
        ),
        sa.Column(
            "scale", sa.ARRAY(sa.Double()), server_default=sa.text("'{1,1,1}'"), nullable=False
        ),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.UniqueConstraint("scene_version_id", "code", name="uq_scene_nodes_version_code"),
        sa.UniqueConstraint("scene_version_id", "id", name="uq_scene_nodes_version_node"),
        sa.UniqueConstraint("id", "asset_version_id", name="uq_scene_nodes_asset_pin"),
        sa.ForeignKeyConstraint(
            ["scene_version_id", "parent_id"],
            ["atlas.scene_nodes.scene_version_id", "atlas.scene_nodes.id"],
            name="fk_scene_nodes_same_version_parent",
        ),
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'", name="ck_scene_nodes_code"),
        sa.CheckConstraint("id <> parent_id", name="ck_scene_nodes_distinct_parent"),
        sa.CheckConstraint(
            "node_type = 'group' OR (node_type = 'asset' AND asset_version_id IS NOT NULL)",
            name="ck_scene_nodes_type_and_asset",
        ),
        sa.CheckConstraint(
            "atlas.scene_vector_is_finite(translation, 3)",
            name="ck_scene_nodes_translation",
        ),
        sa.CheckConstraint(
            "atlas.scene_quaternion_is_unit(rotation)", name="ck_scene_nodes_rotation"
        ),
        sa.CheckConstraint(
            "atlas.scene_vector_is_finite(scale, 3) AND 0 < ALL(scale)",
            name="ck_scene_nodes_scale",
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_scene_nodes_sort_order"),
        schema="atlas",
    )
    op.create_index(
        "ix_scene_nodes_asset_version", "scene_nodes", ["asset_version_id"], schema="atlas"
    )
    op.create_index(
        "ix_scene_nodes_listing",
        "scene_nodes",
        ["scene_version_id", "sort_order", "code"],
        schema="atlas",
    )
    op.create_index(
        "ix_scene_nodes_parent", "scene_nodes", ["scene_version_id", "parent_id"], schema="atlas"
    )
    op.create_unique_constraint(
        "uq_anatomy_asset_links_scene_pin",
        "anatomy_asset_links",
        ["id", "version_id", "structure_id"],
        schema="atlas",
    )
    op.create_table(
        "scene_node_anatomy_links",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("scene_node_id", sa.Uuid(), nullable=False),
        sa.Column("anatomy_asset_link_id", sa.Uuid(), nullable=False),
        sa.Column("asset_version_id", sa.Uuid(), nullable=False),
        sa.Column("structure_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint(
            "scene_node_id", "anatomy_asset_link_id", name="uq_scene_node_anatomy_links_pair"
        ),
        sa.ForeignKeyConstraint(
            ["scene_node_id", "asset_version_id"],
            ["atlas.scene_nodes.id", "atlas.scene_nodes.asset_version_id"],
            name="fk_scene_node_anatomy_links_node_pin",
        ),
        sa.ForeignKeyConstraint(
            ["anatomy_asset_link_id", "asset_version_id", "structure_id"],
            [
                "atlas.anatomy_asset_links.id",
                "atlas.anatomy_asset_links.version_id",
                "atlas.anatomy_asset_links.structure_id",
            ],
            name="fk_scene_node_anatomy_links_anatomy_pin",
        ),
        schema="atlas",
    )
    op.create_index(
        "ix_scene_node_anatomy_links_anatomy",
        "scene_node_anatomy_links",
        ["anatomy_asset_link_id"],
        schema="atlas",
    )
    add_scene_triggers()


def add_scene_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION atlas.preserve_scene_identity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE'
                OR (NEW.id, NEW.code) IS DISTINCT FROM (OLD.id, OLD.code)
            THEN
                RAISE EXCEPTION 'Scene identity is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION atlas.check_scene_version_state() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            pinned_asset record;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.publication_state <> 'draft' THEN
                    RAISE EXCEPTION 'A scene version must start as a draft'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'DELETE'
                OR (NEW.id, NEW.scene_id, NEW.version, NEW.created_at,
                    NEW.coordinate_profile, NEW.translation_unit, NEW.angle_unit)
                    IS DISTINCT FROM (OLD.id, OLD.scene_id, OLD.version, OLD.created_at,
                        OLD.coordinate_profile, OLD.translation_unit, OLD.angle_unit)
            THEN
                RAISE EXCEPTION 'Scene version identity is immutable' USING ERRCODE = '23514';
            END IF;

            IF OLD.publication_state = 'draft'
                AND NEW.origin_description IS DISTINCT FROM OLD.origin_description
            THEN
                NEW.spatial_review_reference := NULL;
            END IF;

            IF OLD.publication_state <> 'draft' THEN
                IF (NEW.origin_description, NEW.spatial_review_reference)
                        IS DISTINCT FROM (OLD.origin_description, OLD.spatial_review_reference)
                    OR (NEW.publication_state IS DISTINCT FROM OLD.publication_state
                        AND NOT (OLD.publication_state = 'published'
                            AND NEW.publication_state = 'withdrawn'))
                THEN
                    RAISE EXCEPTION 'Published scene version content is immutable'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.publication_state = 'withdrawn' THEN
                RAISE EXCEPTION 'Only a published scene version can be withdrawn'
                    USING ERRCODE = '23514';
            ELSIF NEW.publication_state = 'published' THEN
                -- Serialize the eligibility check with registry status changes.
                FOR pinned_asset IN
                    SELECT asset.review_state, version.publication_state
                    FROM atlas.asset_versions AS version
                    JOIN atlas.assets AS asset ON asset.id = version.asset_id
                    WHERE EXISTS (
                        SELECT 1 FROM atlas.scene_nodes AS node
                        WHERE node.scene_version_id = NEW.id AND node.asset_version_id = version.id
                    )
                    ORDER BY asset.id, version.id
                    FOR SHARE OF asset, version
                LOOP
                    IF pinned_asset.review_state <> 'published'
                        OR pinned_asset.publication_state <> 'published'
                    THEN
                        RAISE EXCEPTION 'Scene version references an ineligible asset version'
                            USING ERRCODE = '23514';
                    END IF;
                END LOOP;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION atlas.guard_scene_graph_edit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            affected_version uuid;
            affected_node uuid;
            profile text;
        BEGIN
            IF TG_OP = 'TRUNCATE' THEN
                RAISE EXCEPTION 'Scene tables preserve historical scene identities and graphs'
                    USING ERRCODE = '23514';
            END IF;
            IF TG_TABLE_NAME = 'scene_nodes' THEN
                IF TG_OP = 'UPDATE' AND (NEW.id, NEW.scene_version_id)
                    IS DISTINCT FROM (OLD.id, OLD.scene_version_id)
                THEN
                    RAISE EXCEPTION 'Scene nodes cannot change identity or move between versions'
                        USING ERRCODE = '23514';
                END IF;
                affected_version := CASE WHEN TG_OP = 'DELETE'
                    THEN OLD.scene_version_id ELSE NEW.scene_version_id END;
            ELSE
                IF TG_OP = 'UPDATE' AND (NEW.id, NEW.scene_node_id)
                    IS DISTINCT FROM (OLD.id, OLD.scene_node_id)
                THEN
                    RAISE EXCEPTION 'An anatomy mapping cannot change identity or move between nodes'
                        USING ERRCODE = '23514';
                END IF;
                affected_node := CASE WHEN TG_OP = 'DELETE'
                    THEN OLD.scene_node_id ELSE NEW.scene_node_id END;
                SELECT scene_version_id INTO affected_version
                    FROM atlas.scene_nodes WHERE id = affected_node;
            END IF;

            -- Write the version row even when the review is already NULL. This serializes
            -- graph edits and publication and forces snapshot writers to retry stale writes.
            UPDATE atlas.scene_versions SET spatial_review_reference = NULL
                WHERE id = affected_version AND publication_state = 'draft'
                RETURNING coordinate_profile INTO profile;
            IF NOT FOUND THEN
                IF NOT EXISTS (SELECT 1 FROM atlas.scene_versions WHERE id = affected_version) THEN
                    RAISE EXCEPTION 'Scene version or node does not exist' USING ERRCODE = '23503';
                END IF;
                RAISE EXCEPTION 'The scene graph can only be edited in draft versions'
                    USING ERRCODE = '23514';
            END IF;
            IF TG_TABLE_NAME = 'scene_nodes' AND TG_OP <> 'DELETE' THEN
                IF profile = 'atlas-anatomy-y-up-v1'
                    AND NEW.scale IS DISTINCT FROM '{1,1,1}'::double precision[]
                THEN
                    RAISE EXCEPTION 'The anatomy coordinate profile requires unit scale'
                        USING ERRCODE = '23514', CONSTRAINT = 'ck_scene_nodes_anatomy_scale';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION atlas.check_scene_hierarchy() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                WITH RECURSIVE ancestors(id) AS (
                    SELECT NEW.parent_id
                    UNION
                    SELECT node.parent_id
                    FROM atlas.scene_nodes AS node
                    JOIN ancestors ON ancestors.id = node.id
                    WHERE node.scene_version_id = NEW.scene_version_id
                        AND node.parent_id IS NOT NULL
                )
                SELECT 1 FROM ancestors WHERE id = NEW.id
            ) THEN
                RAISE EXCEPTION 'Scene hierarchy contains a cycle' USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER preserve_scene_identity BEFORE UPDATE OR DELETE ON atlas.scenes
        FOR EACH ROW EXECUTE FUNCTION atlas.preserve_scene_identity()
        """
    )
    op.execute(
        """
        CREATE TRIGGER check_scene_version_state
        BEFORE INSERT OR UPDATE OR DELETE ON atlas.scene_versions
        FOR EACH ROW EXECUTE FUNCTION atlas.check_scene_version_state()
        """
    )
    for table in ("scene_nodes", "scene_node_anatomy_links"):
        op.execute(
            f"""
            CREATE TRIGGER guard_scene_graph_edit BEFORE INSERT OR UPDATE OR DELETE ON atlas.{table}
            FOR EACH ROW EXECUTE FUNCTION atlas.guard_scene_graph_edit()
            """
        )
    for table in ("scenes", "scene_versions", "scene_nodes", "scene_node_anatomy_links"):
        op.execute(
            f"""
            CREATE TRIGGER reject_scene_truncate BEFORE TRUNCATE ON atlas.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION atlas.guard_scene_graph_edit()
            """
        )
    op.execute(
        """
        CREATE TRIGGER check_scene_hierarchy
        AFTER INSERT OR UPDATE OF parent_id ON atlas.scene_nodes
        FOR EACH ROW WHEN (NEW.parent_id IS NOT NULL)
        EXECUTE FUNCTION atlas.check_scene_hierarchy()
        """
    )


def downgrade() -> None:
    op.drop_table("scene_node_anatomy_links", schema="atlas")
    op.drop_constraint(
        "uq_anatomy_asset_links_scene_pin", "anatomy_asset_links", type_="unique", schema="atlas"
    )
    op.drop_table("scene_nodes", schema="atlas")
    op.drop_table("scene_versions", schema="atlas")
    op.drop_table("scenes", schema="atlas")
    op.execute("DROP FUNCTION atlas.check_scene_hierarchy()")
    op.execute("DROP FUNCTION atlas.guard_scene_graph_edit()")
    op.execute("DROP FUNCTION atlas.check_scene_version_state()")
    op.execute("DROP FUNCTION atlas.preserve_scene_identity()")
    op.execute("DROP FUNCTION atlas.scene_quaternion_is_unit(double precision[])")
    op.execute("DROP FUNCTION atlas.scene_vector_is_finite(double precision[], integer)")
