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

For manual development, connect to the `postgres` database on a fresh local PostgreSQL 16 instance as a database administrator. From `backend/`, run the following in `psql`. The provisioning script switches to `atlas_db`, and the password commands prompt interactively.

```text
\i provision_local.sql
\password atlas_app
\password atlas_migrator
```

The [provisioning script](provision_local.sql) follows the P0.3 access model. `atlas_owner` owns `atlas_db` and the `atlas` schema. `atlas_app` has schema `USAGE` and receives `SELECT` on future tables created by `atlas_owner` in `atlas`. `atlas_migrator` acquires owner privileges through `SET ROLE atlas_owner`.

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

**Integration tests apply migrations to a disposable PostgreSQL 16 cluster created and removed by pytest.** The fixture uses a private Unix socket with TCP disabled and generates its own connection URLs. Put the PostgreSQL 16 server tools (`initdb`, `pg_ctl`, and `psql`) on `PATH` and run pytest as a regular user. The suite checks readiness, migration ownership, and the runtime role's read-only access.

```sh
python -m pytest -m integration
```

After changing the API, regenerate the committed contract from `backend/` and rerun the tests.

```sh
python -c 'import json; from atlas_api.main import app; print(json.dumps(app.openapi(), indent=2, sort_keys=True))' > ../contracts/openapi.v1.json
```
