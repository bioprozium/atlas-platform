\set ON_ERROR_STOP on

CREATE ROLE atlas_owner NOLOGIN;
CREATE ROLE atlas_app LOGIN;
CREATE ROLE atlas_migrator LOGIN;
GRANT atlas_owner TO atlas_migrator WITH INHERIT FALSE, SET TRUE;

CREATE DATABASE atlas_db OWNER atlas_owner ENCODING 'UTF8' TEMPLATE template0;
\connect atlas_db
REVOKE ALL ON DATABASE atlas_db FROM PUBLIC;
GRANT CONNECT ON DATABASE atlas_db TO atlas_app, atlas_migrator;

CREATE SCHEMA atlas AUTHORIZATION atlas_owner;
GRANT USAGE ON SCHEMA atlas TO atlas_app;
ALTER DEFAULT PRIVILEGES FOR ROLE atlas_owner IN SCHEMA atlas
    GRANT SELECT ON TABLES TO atlas_app;
