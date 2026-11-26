# Backend

The Open 3D Atlas REST API uses Python 3.12+, FastAPI, SQLAlchemy 2, psycopg 3, and PostgreSQL 16. The [OpenAPI v1 contract](../contracts/openapi.v1.json) describes the endpoints and response bodies.

| Endpoint | Response |
| --- | --- |
| `GET /api/v1/health` | HTTP 200 with `{"status":"ok"}`. This check runs without a database connection. |
| `GET /api/v1/ready` | HTTP 200 with `{"status":"ready"}` after a successful database query. Missing configuration or a failed query returns HTTP 503 with `{"status":"unavailable"}`. |

## Human Anatomy API

The endpoints under `/api/v1/anatomy` serve published anatomy content. Use the public `code` to identify a system or structure.

| GET path relative to `/api/v1/anatomy` | Response and optional filters |
| --- | --- |
| `/systems` | Page of system codes and names. Use `q` to match a code or name. |
| `/systems/{code}` | System code and name. |
| `/structures` | Page of structure codes, laterality, and preferred names. Filter by `system_code`, `laterality` (`left`, `right`, `none`), `language` (`la`, `en`, `az`), or `q`. |
| `/structures/{code}` | Structure code, laterality, preferred names and synonyms, published system memberships, and external identifiers with provenance. |
| `/structures/{code}/relations` | Page of direct relationships with `source_code`, `relation_type`, and `target_code`. Filter by `relation_type` (`part_of`, `is_a`) or `direction` (`incoming`, `outgoing`). The default includes both directions. |

List responses contain `items`, `limit`, `offset`, and `has_more`. The default page size is 50, the maximum is 100, and the starting offset is 0. Systems and structures sort by code. Relations sort by source code, target code, and relation type. Advance `offset` by `limit` when `has_more` is true.

The `q` filter matches literal substrings, with case handling determined by the database locale. On structures, `q` searches preferred names and synonyms. Combining `language` and `q` requires a matching name in that language. List items still include preferred names in all available languages.

A missing or draft detail resource returns HTTP 404 with a JSON `detail` message. A published structure with no matching relations returns an empty page. A database failure returns HTTP 503 with `{"detail":"Anatomy database unavailable."}`. Invalid query or path parameters return HTTP 422. [OpenAPI v1](../contracts/openapi.v1.json)

### Anatomy content model

Revision `0002` adds the anatomy tables to the existing `atlas` schema. Systems and structures start in the `draft` review state and become visible when marked `published`. The API also omits memberships in draft systems and relations with a draft endpoint. Revision `0002` initializes an internal hierarchy guard. Anatomy content is loaded separately, and the test fixture is confined to disposable databases. Downgrading to `0001` drops the anatomy tables and their contents.

- Each system and structure has an internal UUID and an immutable public code. Codes are lowercase ASCII slugs of up to 80 characters. Preserve existing codes when names change and avoid reusing retired codes.
- A structure can have one preferred name per language and multiple synonyms. Languages use `la` for Latin, `en` for English, and `az` for Azerbaijani. Use `none` for non-lateral structures and separate codes for paired left and right structures. A structure can belong to multiple systems.
- A `part_of` edge points from the part to the whole. An `is_a` edge points from the subtype to the supertype. The database rejects self-edges, duplicate typed edges, and cycles across either or both edge types. Graph changes serialize through the guard row. Transactions at stricter isolation levels can receive a serialization failure and need a retry.
- An external mapping records the namespace, identifier value, source, and source version when known. The same namespace and value can map to several atlas structures. Verify third-party terms and attribution before importing scientific content and use verified identifiers for external mappings. [Asset licensing policy](../docs/ASSET-LICENSING.md)

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

From `backend/`, load the local configuration, apply the migrations, and start Uvicorn with its default single worker.

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

**Integration tests apply migrations to a disposable PostgreSQL 16 cluster created and removed by pytest.** The fixture uses a private Unix socket with TCP disabled and generates its own connection URLs. Put the PostgreSQL 16 server tools (`initdb`, `pg_ctl`, and `psql`) on `PATH` and run pytest as a regular user. The suite checks readiness, migration ownership and rollback, read-only runtime access, anatomy data constraints, and the public API using a small deterministic fixture.

```sh
python -m pytest -m integration
```

After changing the API, regenerate the committed contract from `backend/` and rerun the tests.

```sh
python -c 'import json; from atlas_api.main import app; print(json.dumps(app.openapi(), indent=2, sort_keys=True))' > ../contracts/openapi.v1.json
```
