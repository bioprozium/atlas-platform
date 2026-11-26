\set ON_ERROR_STOP on
\connect atlas_db

\if :{?atlas_storage_root}
\else
\set atlas_storage_root /srv/atlas/assets
\endif

BEGIN;
INSERT INTO atlas.ingestion_storage (storage_root) VALUES (:'atlas_storage_root')
    ON CONFLICT (singleton) DO NOTHING;
SELECT storage_root = :'atlas_storage_root' AS storage_root_matches
    FROM atlas.ingestion_storage WHERE singleton FOR SHARE
\gset
\if :storage_root_matches
\else
DO $$
BEGIN
    RAISE EXCEPTION 'The registry is already bound to a different storage root';
END
$$;
\endif

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'atlas_ingest') THEN
        CREATE ROLE atlas_ingest LOGIN;
    END IF;
END
$$;
GRANT CONNECT ON DATABASE atlas_db TO atlas_ingest;
GRANT USAGE ON SCHEMA atlas TO atlas_ingest;
GRANT EXECUTE ON FUNCTION atlas.ingestion_storage_root(),
    atlas.record_asset_ingestion(text, jsonb, text, jsonb) TO atlas_ingest;
COMMIT;
