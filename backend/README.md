# Backend

The Open 3D Atlas REST API uses Python 3.12+, FastAPI, SQLAlchemy 2, psycopg 3, and PostgreSQL 16. The [OpenAPI v1 contract](../contracts/openapi.v1.json) describes the endpoints and response bodies.

| Endpoint | Response |
| --- | --- |
| `GET /api/v1/health` | HTTP 200 with `{"status":"ok"}`. This check runs without a database connection. |
| `GET /api/v1/ready` | HTTP 200 with `{"status":"ready"}` after a successful database query. Missing configuration or a failed query returns HTTP 503 with `{"status":"unavailable"}`. |

## Install locally

From the repository root, create a virtual environment and install the backend and test dependencies.

```sh
cd backend
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
```

## Configure PostgreSQL

Use a disposable local PostgreSQL 16 instance for development and integration tests. On a fresh instance, run the following in `psql` as a local database administrator. The password commands prompt interactively.

```sql
CREATE ROLE atlas_owner NOLOGIN;
CREATE ROLE atlas_app LOGIN;
CREATE ROLE atlas_migrator LOGIN;
GRANT atlas_owner TO atlas_migrator WITH INHERIT FALSE, SET TRUE;
\password atlas_app
\password atlas_migrator
CREATE DATABASE atlas_db WITH ENCODING 'UTF8' TEMPLATE template0;
\connect atlas_db
REVOKE ALL ON DATABASE atlas_db FROM PUBLIC;
GRANT CONNECT ON DATABASE atlas_db TO atlas_app, atlas_migrator;
CREATE SCHEMA atlas AUTHORIZATION atlas_owner;
```

Copy `.env.example` to the ignored `.env` file in `backend/`. Add the corresponding local passwords to the URLs and percent-encode any URL-reserved characters in them. The example assumes a loopback connection on port 5432.

| Variable | Purpose |
| --- | --- |
| `ATLAS_DATABASE_URL` | Runtime connection as `atlas_app`. Unset it to start without a database. |
| `ATLAS_MIGRATION_DATABASE_URL` | Alembic connection as `atlas_migrator`. Required for online Alembic commands. |

Both variables accept `postgresql+psycopg` URLs for `atlas_db`. The API opens connections on demand and uses at most two PostgreSQL connections per worker. Alembic uses an unpooled connection, explicitly runs `SET ROLE atlas_owner`, and tracks revisions in `atlas.alembic_version`. The database roles and schema must already exist when migrations run.

From `backend/`, load the local configuration, apply the baseline, and start Uvicorn with its default single worker.

```sh
set -a
. ./.env
set +a
env -u ATLAS_DATABASE_URL alembic upgrade head
env -u ATLAS_MIGRATION_DATABASE_URL uvicorn atlas_api.main:app --host 127.0.0.1
```

Check the running API from another shell.

```sh
curl -i http://127.0.0.1:8000/api/v1/health
curl -i http://127.0.0.1:8000/api/v1/ready
```

## Test locally

From `backend/` with the virtual environment active, run unit tests without PostgreSQL.

```sh
python -m pytest -m 'not integration'
```

With the local URLs loaded, pass them to the integration tests explicitly. The tests check PostgreSQL 16 connectivity, readiness during a connection failure, migration state, and ownership of the version table. The migration test applies migrations to the database supplied in the test environment.

```sh
ATLAS_TEST_DATABASE_URL="$ATLAS_DATABASE_URL" \
ATLAS_TEST_MIGRATION_DATABASE_URL="$ATLAS_MIGRATION_DATABASE_URL" \
python -m pytest -m integration
```

After changing the API, regenerate the committed contract from `backend/` and rerun the tests.

```sh
python -c 'import json; from atlas_api.main import app; print(json.dumps(app.openapi(), indent=2, sort_keys=True))' > ../contracts/openapi.v1.json
```
