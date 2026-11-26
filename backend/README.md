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
| `/structures` | Page of structure codes, `structure_kind`, laterality, and preferred names. Filter by `system_code`, `laterality` (`left`, `right`, `none`), `language` (`la`, `en`, `az`), or `q`. |
| `/structures/{code}` | Structure code, `structure_kind`, laterality, preferred names and synonyms, published system memberships, and external identifiers with provenance. |
| `/structures/{code}/relations` | Page of direct relationships with `source_code`, `relation_type`, and `target_code`. Filter by `relation_type` (`part_of`, `is_a`) or `direction` (`incoming`, `outgoing`). The default includes both directions. |

List responses contain `items`, `limit`, `offset`, and `has_more`. The default page size is 50, the maximum is 100, and the starting offset is 0. Systems and structures sort by code. Relations sort by source code, target code, and relation type. Advance `offset` by `limit` when `has_more` is true.

The `q` filter matches literal substrings, with case handling determined by the database locale. On structures, `q` searches preferred names and synonyms. Combining `language` and `q` requires a matching name in that language. List items still include preferred names in all available languages.

A missing or draft detail resource returns HTTP 404 with a JSON `detail` message. A published structure with no matching relations returns an empty page. A database failure returns HTTP 503 with `{"detail":"Anatomy database unavailable."}`. Invalid query or path parameters return HTTP 422. [OpenAPI v1](../contracts/openapi.v1.json)

### Anatomy content model

Revision `0002` adds the anatomy tables to the existing `atlas` schema. Systems and structures start in the `draft` review state and become visible when marked `published`. The API also omits memberships in draft systems and relations with a draft endpoint. Revision `0002` initializes an internal hierarchy guard. Anatomy content is loaded separately, and the test fixture is confined to disposable databases. Downgrading to `0001` drops the anatomy tables and their contents.

- A structure can have one preferred name per language and multiple synonyms. Languages use `la` for Latin, `en` for English, and `az` for Azerbaijani. Use `none` for non-lateral structures and separate codes for paired left and right structures. A structure can belong to multiple systems.
- A `part_of` edge points from the part to the whole. An `is_a` edge points from the subtype to the supertype. The database rejects self-edges, duplicate typed edges, and cycles across either or both edge types. Graph changes serialize through the guard row. Transactions at stricter isolation levels can receive a serialization failure and need a retry.
- An external mapping records the namespace, identifier value, source, and source version when known. The same namespace and value can map to several atlas structures. Verify third-party terms and attribution before importing scientific content and use verified identifiers for external mappings. [Asset licensing policy](../docs/ASSET-LICENSING.md)

### Structure kinds

Each structure requires one `structure_kind`. Choose the most specific category for the structure being described. Use `is_a` relations to record its scientific taxonomy and system memberships to record which systems it belongs to.

| Value | Use |
| --- | --- |
| `bone` | A named bone such as the left femur. |
| `organ` | A whole organ such as the heart. |
| `organ_part` | A distinct part of an organ such as the left ventricle. |
| `region` | An anatomical region such as the thorax. |
| `tissue` | A tissue such as bone marrow. |
| `joint` | An articulation such as the knee joint. |
| `aggregate` | A collection of anatomical structures such as the skeleton. |

Adding a kind requires a schema migration and an update to the API vocabulary.

### Public anatomy codes

Each system and structure has an internal UUID and an immutable public code. Use a lowercase ASCII slug of up to 80 characters that starts with a letter and uses hyphens to separate alphanumeric tokens. Choose a code for the anatomical concept itself, such as `left-femur` or `heart`. Omit system membership, structure kind, and hierarchy paths from the code. Keep the code when a name, category, or hierarchy changes. Retired codes stay reserved.

Use the same code verbatim in API URLs, Android records, 3D asset references, and offline manifests.

### Minimum metadata for publication

Before setting a structure's review state to `published`, verify the following during content review.

- The public code identifies a distinct anatomical concept and follows the code convention. Its structure kind and laterality match the concept.
- At least one preferred name is verified against a credible anatomy reference in a supported language. Verify any additional names and synonyms that will be published.
- The material submitted for review includes a citation for the name and anatomical identity, with the source edition or version when available.
- Any system memberships, relationships, or external mappings are checked for scientific accuracy. External mappings include a verified namespace and identifier, their source, and the source version when available.

Scientific verification is an editorial requirement. The public API serves records according to the stored review state.

## 3D Asset Registry

| GET path relative to `/api/v1` | Response |
| --- | --- |
| `/assets` | Page of assets in code order, each with its newest eligible version. |
| `/assets/{code}` | Asset metadata and a page of eligible versions in descending creation order, with ties sorted by descending version identifier. |
| `/assets/{code}/versions/{version}` | Metadata and attribution for the specified eligible version. |
| `/anatomy/structures/{code}/assets` | Page of eligible versions linked to a published structure, with each link's optional role. Items sort by asset code, then by version creation time and identifier as above. |

An asset and its version both need published status for the API to return that version. The page format, default limit of 50, and maximum limit of 100 match the anatomy API. The asset and structure asset listings accept `q` for case-insensitive literal substring matching on asset code or title. A missing or ineligible detail returns 404. A published structure with no eligible links returns an empty page. Invalid parameters return 422. A database failure returns 503 with `{"detail":"Asset registry database unavailable."}`.

Use the asset code and version identifier together as the public identifier. Asset codes follow the anatomy code convention. Version identifiers are lowercase ASCII alphanumeric tokens separated by dots, underscores, or hyphens, up to 80 characters. Treat them as opaque identifiers. Each version includes its SHA-256 digest, file size in bytes, format, MIME type, and source records. The API exposes metadata only. [OpenAPI v1](../contracts/openapi.v1.json)

### Editorial publication

1. Use the [operator ingestion CLI](../processing/README.md) to validate and register the exact GLB version as a draft. It uses the registry storage reference under `/srv/atlas/assets/sha256/<first-two-hex-digits>/<lowercase-digest>.glb` and verifies existing content before reuse.
2. Record each original or third-party source against the specific version, including dependencies and upstream notices. Review the version-specific source declarations and verify redistribution, adaptation, and offline rights under the [asset licensing policy](../docs/ASSET-LICENSING.md).
3. Review validation warnings, source units, orientation and geometry, and verify scientific accuracy. Use anatomy links to associate the reviewed version with each applicable structure. The same structure and version can be linked once.
4. Through the controlled database workflow, set the primary source, record `rights_verified_at` and the internal `rights_review_reference`, then explicitly publish the version and asset. PostgreSQL requires valid status, a primary source belonging to the version, and a recorded rights review before a version can be marked published.
5. Set a version to `withdrawn` to hide it while preserving its identity and file metadata. Source edits require the version to be unpublished and clear its recorded rights review. Reverify rights before publishing again. Any change to the GLB bytes requires a new version and content address.

The operator CLI combines the registry's integrity checks with Khronos glTF semantic validation. API reads use the recorded publication state and file metadata. Revision `0005` adds a private canonical storage binding, ingestion history, and a draft registration function for the separate `atlas_ingest` role. See the [ingestion guide](../processing/README.md#registration-role) for installation and provisioning.

Revision `0003` adds the registry to the existing `atlas` schema under `atlas_owner`. Rolling back to `0002` drops registry tables and their contents. The P1.1 anatomy tables and data survive that rollback.

## 3D Scene Graph

The read-only endpoints under `/api/v1/scenes` serve versioned scene graphs with exact asset pins. API behavior, transform conventions, and publication prerequisites are covered in the scene documentation. [Scene coordinates and transforms](../docs/SCENE-COORDINATES.md)

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

**Integration tests apply migrations to a disposable PostgreSQL 16 cluster created and removed by pytest.** The fixture uses a private Unix socket with TCP disabled and generates its own connection URLs. Put the PostgreSQL 16 server tools (`initdb`, `pg_ctl`, and `psql`) on `PATH` and run pytest as a regular user. The suite checks readiness, migration ownership and rollback, read-only runtime access, data constraints, and the public API using deterministic anatomy and synthetic GLB metadata fixtures.

```sh
python -m pytest -m integration
```

After changing the API, regenerate the committed contract from `backend/` and rerun the tests.

```sh
python -c 'import json; from atlas_api.main import app; print(json.dumps(app.openapi(), indent=2, sort_keys=True))' > ../contracts/openapi.v1.json
```
