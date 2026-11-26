"""Add private ingestion history and the draft registration boundary."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE atlas.asset_ingestions (
            id uuid PRIMARY KEY,
            actor text NOT NULL DEFAULT session_user,
            asset_code varchar(80) NOT NULL CHECK (asset_code ~ '^[a-z][a-z0-9]*(-[a-z0-9]+)*$'),
            version varchar(80) NOT NULL CHECK (version ~ '^[a-z0-9]+([._-][a-z0-9]+)*$'),
            sha256 varchar(64) CHECK (sha256 ~ '^[0-9a-f]{64}$'),
            version_id uuid REFERENCES atlas.asset_versions(id),
            validation_status text NOT NULL CHECK (validation_status IN ('passed', 'rejected', 'error')),
            outcome text NOT NULL CHECK (outcome IN ('created', 'reused', 'content_conflict', 'metadata_conflict', 'not_registered')),
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            report jsonb NOT NULL CHECK (octet_length(report::text) <= 1048576)
        )
        """
    )
    op.execute("REVOKE ALL ON atlas.asset_ingestions FROM PUBLIC, atlas_app")
    op.execute(
        """
        CREATE UNIQUE INDEX ix_asset_ingestions_created_version
            ON atlas.asset_ingestions (version_id) WHERE outcome = 'created'
        """
    )
    op.execute(
        """
        CREATE FUNCTION atlas.record_asset_ingestion(p_report jsonb, p_title text, p_sources jsonb)
        RETURNS TABLE(outcome text, version_id uuid)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            asset_key text := p_report->>'asset_code';
            version_key text := p_report->>'version';
            digest text := p_report->>'sha256';
            byte_count bigint := p_report->>'byte_length';
            reference text := p_report->>'storage_reference';
            validation text := p_report->>'validation_status';
            registered_asset atlas.assets%ROWTYPE;
            registered_version atlas.asset_versions%ROWTYPE;
            incoming_sources jsonb;
            recorded_sources jsonb;
            recorded_geometry jsonb;
        BEGIN
            outcome := 'not_registered';
            IF validation = 'passed' AND p_report->'storage_verified' = 'true'::jsonb THEN
                IF p_report#>>'{validator,name}' IS DISTINCT FROM 'Khronos glTF Validator'
                    OR coalesce(p_report#>>'{validator,version}', '') = ''
                    OR p_report#>'{validator,completed}' IS DISTINCT FROM 'true'::jsonb
                    OR jsonb_typeof(p_report->'source_geometry') IS DISTINCT FROM 'object'
                    OR jsonb_typeof(p_report->'issues') IS DISTINCT FROM 'array'
                    OR EXISTS (
                        SELECT FROM jsonb_array_elements(p_report->'issues') AS issue
                        WHERE issue->'severity' = '0'::jsonb
                    )
                    OR jsonb_typeof(p_sources) IS DISTINCT FROM 'array'
                    OR jsonb_array_length(p_sources) NOT BETWEEN 1 AND 32
                THEN
                    RAISE EXCEPTION 'Missing validation or provenance' USING ERRCODE = '23514';
                END IF;

                INSERT INTO atlas.assets (code, title)
                    VALUES (asset_key, p_title) ON CONFLICT ON CONSTRAINT uq_assets_code DO NOTHING;
                SELECT asset.* INTO STRICT registered_asset FROM atlas.assets AS asset
                    WHERE asset.code = asset_key FOR SHARE;

                IF registered_asset.title = p_title THEN
                    INSERT INTO atlas.asset_versions (
                        asset_id, version, sha256, file_size, format, mime_type,
                        storage_reference, validation_state
                    ) VALUES (
                        registered_asset.id, version_key, digest, byte_count, 'glb',
                        'model/gltf-binary', reference, 'valid'
                    ) ON CONFLICT ON CONSTRAINT uq_asset_versions_identifier DO NOTHING
                    RETURNING * INTO registered_version;
                END IF;

                IF registered_version.id IS NOT NULL THEN
                    version_id := registered_version.id;
                    INSERT INTO atlas.asset_sources (
                        version_id, creator, rights_holder, source_reference, spdx_license_id,
                        license_reference, attribution, modifications
                    )
                    SELECT registered_version.id, s.creator, s.rights_holder, s.source_reference,
                        s.spdx_license_id, s.license_reference, s.attribution, s.modifications
                    FROM jsonb_populate_recordset(NULL::atlas.asset_sources, p_sources) AS s;
                    outcome := 'created';
                ELSE
                    SELECT stored.* INTO registered_version
                        FROM atlas.asset_versions AS stored
                        WHERE stored.asset_id = registered_asset.id AND stored.version = version_key
                        FOR SHARE;
                    IF FOUND AND (registered_version.sha256, registered_version.file_size,
                            registered_version.format, registered_version.mime_type,
                            registered_version.storage_reference)
                        IS DISTINCT FROM (digest, byte_count, 'glb', 'model/gltf-binary', reference)
                    THEN
                        outcome := 'content_conflict';
                    ELSIF registered_asset.title IS DISTINCT FROM p_title THEN
                        outcome := 'metadata_conflict';
                    ELSE
                        SELECT jsonb_agg(item ORDER BY item) INTO incoming_sources
                            FROM jsonb_array_elements(p_sources) AS item;
                        SELECT jsonb_agg(item ORDER BY item) INTO recorded_sources
                            FROM (
                                SELECT to_jsonb(s) - 'id' - 'version_id' AS item
                                FROM atlas.asset_sources AS s WHERE s.version_id = registered_version.id
                            ) AS sources;
                        SELECT history.report->'source_geometry' INTO recorded_geometry
                            FROM atlas.asset_ingestions AS history
                            WHERE history.version_id = registered_version.id AND history.outcome = 'created';
                        IF registered_version.validation_state <> 'valid'
                            OR recorded_sources IS DISTINCT FROM incoming_sources
                            OR (recorded_geometry IS NOT NULL
                                AND recorded_geometry IS DISTINCT FROM p_report->'source_geometry')
                        THEN
                            outcome := 'metadata_conflict';
                        ELSE
                            outcome := 'reused';
                            version_id := registered_version.id;
                        END IF;
                    END IF;
                END IF;
            END IF;

            INSERT INTO atlas.asset_ingestions (
                id, asset_code, version, sha256, version_id, validation_status, outcome, report
            ) VALUES (
                (p_report->>'ingestion_id')::uuid, asset_key, version_key, digest, version_id,
                validation, outcome, p_report
            );
            RETURN NEXT;
        END;
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION atlas.record_asset_ingestion(jsonb, text, jsonb) FROM PUBLIC"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'atlas_ingest') THEN
                GRANT EXECUTE ON FUNCTION atlas.record_asset_ingestion(jsonb, text, jsonb)
                    TO atlas_ingest;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION atlas.record_asset_ingestion(jsonb, text, jsonb)")
    op.drop_table("asset_ingestions", schema="atlas")
