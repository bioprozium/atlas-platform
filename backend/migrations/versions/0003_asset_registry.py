"""Create the versioned 3D Asset Registry."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("review_state", sa.String(9), server_default="draft", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'", name="ck_assets_code"),
        sa.CheckConstraint("title ~ '[^[:space:]]'", name="ck_assets_title"),
        sa.CheckConstraint(
            "review_state IN ('draft', 'published', 'withdrawn')", name="ck_assets_review_state"
        ),
        sa.UniqueConstraint("code", name="uq_assets_code"),
        schema="atlas",
    )
    op.create_table(
        "asset_versions",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column("asset_id", sa.Uuid(), sa.ForeignKey("atlas.assets.id"), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("format", sa.String(3), nullable=False),
        sa.Column("mime_type", sa.String(40), nullable=False),
        sa.Column("storage_reference", sa.String(80), nullable=False),
        sa.Column("validation_state", sa.String(7), server_default="pending", nullable=False),
        sa.Column("publication_state", sa.String(9), server_default="draft", nullable=False),
        sa.Column("rights_verified_at", sa.DateTime(timezone=True)),
        sa.Column("rights_review_reference", sa.Text()),
        sa.Column("primary_source_id", sa.Uuid()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version ~ '^[a-z0-9]+([._-][a-z0-9]+)*$'", name="ck_asset_versions_identifier"
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_asset_versions_sha256"),
        sa.CheckConstraint(
            "file_size BETWEEN 24 AND 4294967292 AND mod(file_size, 4) = 0",
            name="ck_asset_versions_size",
        ),
        sa.CheckConstraint(
            "format = 'glb' AND mime_type = 'model/gltf-binary'", name="ck_asset_versions_glb"
        ),
        sa.CheckConstraint(
            "storage_reference = 'sha256/' || left(sha256, 2) || '/' || sha256 || '.glb'",
            name="ck_asset_versions_storage_reference",
        ),
        sa.CheckConstraint(
            "validation_state IN ('pending', 'valid', 'invalid')",
            name="ck_asset_versions_validation",
        ),
        sa.CheckConstraint(
            "publication_state IN ('draft', 'published', 'withdrawn')",
            name="ck_asset_versions_publication",
        ),
        sa.CheckConstraint(
            "rights_review_reference ~ '[^[:space:]]'",
            name="ck_asset_versions_rights_reference",
        ),
        sa.CheckConstraint(
            "publication_state <> 'published' OR (validation_state = 'valid' "
            "AND rights_verified_at IS NOT NULL AND rights_review_reference IS NOT NULL "
            "AND primary_source_id IS NOT NULL)",
            name="ck_asset_versions_publishable",
        ),
        sa.UniqueConstraint("asset_id", "version", name="uq_asset_versions_identifier"),
        schema="atlas",
    )
    op.create_index(
        "ix_asset_versions_listing",
        "asset_versions",
        ["asset_id", "publication_state", "created_at", "version"],
        schema="atlas",
    )
    op.create_table(
        "asset_sources",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "version_id", sa.Uuid(), sa.ForeignKey("atlas.asset_versions.id"), nullable=False
        ),
        sa.Column("creator", sa.Text(), nullable=False),
        sa.Column("rights_holder", sa.Text(), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("spdx_license_id", sa.String(120)),
        sa.Column("license_reference", sa.Text(), nullable=False),
        sa.Column("attribution", sa.Text(), nullable=False),
        sa.Column("modifications", sa.Text(), nullable=False),
        sa.UniqueConstraint("version_id", "id", name="uq_asset_sources_version_source"),
        sa.CheckConstraint(
            "creator ~ '[^[:space:]]' AND rights_holder ~ '[^[:space:]]' "
            "AND source_reference ~ '[^[:space:]]' AND license_reference ~ '[^[:space:]]' "
            "AND attribution ~ '[^[:space:]]' AND modifications ~ '[^[:space:]]'",
            name="ck_asset_sources_required_text",
        ),
        sa.CheckConstraint(
            "spdx_license_id ~ '^[a-zA-Z0-9][a-zA-Z0-9.+-]*$'", name="ck_asset_sources_spdx"
        ),
        schema="atlas",
    )
    op.create_foreign_key(
        "fk_asset_versions_primary_source",
        "asset_versions",
        "asset_sources",
        ["id", "primary_source_id"],
        ["version_id", "id"],
        source_schema="atlas",
        referent_schema="atlas",
    )
    op.create_table(
        "anatomy_asset_links",
        sa.Column(
            "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "structure_id",
            sa.Uuid(),
            sa.ForeignKey("atlas.anatomical_structures.id"),
            nullable=False,
        ),
        sa.Column(
            "version_id", sa.Uuid(), sa.ForeignKey("atlas.asset_versions.id"), nullable=False
        ),
        sa.Column("role", sa.String(80)),
        sa.UniqueConstraint("structure_id", "version_id", name="uq_anatomy_asset_links_pair"),
        sa.CheckConstraint(
            "role ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'", name="ck_anatomy_asset_links_role"
        ),
        schema="atlas",
    )
    op.create_index(
        "ix_anatomy_asset_links_version", "anatomy_asset_links", ["version_id"], schema="atlas"
    )
    op.execute(
        """
        CREATE FUNCTION atlas.preserve_asset_identity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE'
                OR (NEW.id, NEW.code, NEW.created_at)
                    IS DISTINCT FROM (OLD.id, OLD.code, OLD.created_at)
            THEN
                RAISE EXCEPTION 'Asset identity is immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION atlas.preserve_asset_version() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE'
                OR (NEW.id, NEW.asset_id, NEW.version, NEW.sha256, NEW.file_size,
                    NEW.format, NEW.mime_type, NEW.storage_reference, NEW.created_at)
                    IS DISTINCT FROM
                   (OLD.id, OLD.asset_id, OLD.version, OLD.sha256, OLD.file_size,
                    OLD.format, OLD.mime_type, OLD.storage_reference, OLD.created_at)
            THEN
                RAISE EXCEPTION 'Asset version identity and file metadata are immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION atlas.invalidate_asset_rights_review() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            affected_version uuid;
        BEGIN
            IF TG_OP = 'UPDATE' AND NEW.version_id IS DISTINCT FROM OLD.version_id THEN
                RAISE EXCEPTION 'An asset source cannot move between versions'
                    USING ERRCODE = '23514';
            END IF;
            affected_version := CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
            -- The update serializes source changes with publication and invalidates approval.
            -- The publishable constraint rejects a source change until the version is withdrawn.
            UPDATE atlas.asset_versions
                SET rights_verified_at = NULL, rights_review_reference = NULL
                WHERE id = affected_version;
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
        CREATE TRIGGER preserve_asset_identity BEFORE UPDATE OR DELETE ON atlas.assets
        FOR EACH ROW EXECUTE FUNCTION atlas.preserve_asset_identity()
        """
    )
    op.execute(
        """
        CREATE TRIGGER preserve_asset_version BEFORE UPDATE OR DELETE ON atlas.asset_versions
        FOR EACH ROW EXECUTE FUNCTION atlas.preserve_asset_version()
        """
    )
    op.execute(
        """
        CREATE TRIGGER invalidate_asset_rights_review
        BEFORE INSERT OR UPDATE OR DELETE ON atlas.asset_sources
        FOR EACH ROW EXECUTE FUNCTION atlas.invalidate_asset_rights_review()
        """
    )


def downgrade() -> None:
    op.drop_table("anatomy_asset_links", schema="atlas")
    op.drop_constraint("fk_asset_versions_primary_source", "asset_versions", schema="atlas")
    op.drop_table("asset_sources", schema="atlas")
    op.drop_table("asset_versions", schema="atlas")
    op.drop_table("assets", schema="atlas")
    op.execute("DROP FUNCTION atlas.invalidate_asset_rights_review()")
    op.execute("DROP FUNCTION atlas.preserve_asset_version()")
    op.execute("DROP FUNCTION atlas.preserve_asset_identity()")
