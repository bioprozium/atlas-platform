\set ON_ERROR_STOP on
\connect atlas_db

BEGIN;
CREATE ROLE atlas_ingest LOGIN;
GRANT CONNECT ON DATABASE atlas_db TO atlas_ingest;
GRANT USAGE ON SCHEMA atlas TO atlas_ingest;
GRANT EXECUTE ON FUNCTION atlas.record_asset_ingestion(jsonb, text, jsonb) TO atlas_ingest;
COMMIT;
