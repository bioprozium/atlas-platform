# Scene manifest and asset delivery

## Manifest schema version 1

Use [OpenAPI v1](../contracts/openapi.v1.json) as the machine-readable contract. `GET /api/v1/scenes/{code}/versions/{version}/manifest` returns one JSON document for the requested scene version.

| Field | Contents |
| --- | --- |
| `schema_version` | Integer `1`, independently versioned from the REST API and scene. |
| `scene` | Scene code, exact opaque version, title, creation time, origin description, profile and units. |
| `nodes` | Complete array in editorial order and then node code order. Each node has a code, nullable parent code, type, sort order, local transform, nullable exact asset reference and saved anatomy annotations. Resolve parents by code within the same array. A parent can sort after a child. |
| `assets` | One record per exact asset code and version pair, sorted by code and then version. Records include file size in bytes, SHA-256, format, MIME type, an origin-relative download URL and the version's public source records. Provenance and license data include disclosures about modifications, creator, rights holder, original source, attribution, and applicable license or permission. |
| `anatomy` | Referenced structures sorted by code, with structure kind, patient laterality, and available preferred names sorted by language. A node's annotation references a structure by code and carries the registry role saved when the draft mapping was written. |

The version can contain multiple roots. A group can carry an asset pin. XYZ translations, XYZW quaternion rotations, and XYZ scale preserve the P1.3 local transforms exactly. Translation uses meters. In the right-handed `atlas-anatomy-y-up-v1` profile, positive X points to the patient's left, positive Y is superior, and positive Z is anterior. Compose transforms according to the P1.3 rules. [Scene coordinates and transforms](SCENE-COORDINATES.md)

The backend reads the requested published scene, published version, all nodes, and required published assets and anatomy in one PostgreSQL repeatable-read snapshot. Any ineligible required asset or structure blocks the whole document. The backend uses the exact stored pins, canonical node ordering, stable source record ordering, and compact UTF-8 JSON. The manifest omits private review references, internal storage references, and ingestion records. Scene models include only published public fields.

In that snapshot, PostgreSQL checks the byte length of the unbounded origin description before returning it and checks aggregate asset source text before loading the source records. The API also bounds node and asset counts and checks the final serialized JSON size.

The filesystem preflight confirms that each distinct required object can be opened safely and has the registered length. This also runs for conditional manifest GETs. Full byte integrity checks run on every binary GET, including conditional binary GETs. A manifest therefore describes an available registry snapshot and its expected content hashes. Clients verify the downloaded files before activating it.

## Binary endpoint and caching

Use the manifest's URL, such as `/api/v1/assets/synthetic-triangle/versions/1/file`. The API checks the exact asset and version's publication eligibility before opening storage. It uses the registered content address under the trusted runtime root, rejects symlinks and non-regular files, checks GLB framing, byte length and SHA-256, and then serves the verified descriptor. A database connection is checked back into its pool before filesystem verification or delivery begins.

A successful binary response sends `Content-Type: model/gltf-binary`, the verified `Content-Length`, a strong quoted SHA-256 `ETag`, and `X-Content-Type-Options: nosniff`. The manifest ETag hashes the exact JSON representation. Changes to public source or anatomy metadata can change the manifest ETag even when the scene version identifier stays the same. Both endpoints send `Cache-Control: no-cache, must-revalidate, no-transform`. Revalidate when connected. `If-None-Match` accepts `*` or a comma-separated list and uses the weak comparison required for GET. A current, available match returns 304 with the cache headers and an empty body.

The file endpoint responds to Range requests with the complete 200 representation, or with the usual conditional status if a validator matches. It sends no range advertisement. After verification, one absolute deadline covers sending headers and the entire response. Progress does not reset the deadline. If a transfer times out after headers are sent, the server aborts the connection without finishing the response. Discard partial bytes and retry a full GET with backoff. Withdrawals take effect for requests whose database snapshot observes the updated publication state. A response already authorized may finish. Offline copies already stored by clients cannot be remotely revoked.

## Errors and limits

Both endpoints use the standard JSON `detail` error response, including FastAPI's validation details for 422. Error responses carry `Cache-Control: no-store`.

| Status | Meaning |
| --- | --- |
| 404 | Asset, scene or exact version is absent or ineligible for public access. |
| 422 | Invalid API path parameter. |
| 503 | Database unavailable, required scene dependency unavailable, manifest limit exceeded, capacity slots full, or published storage unreadable, unsafe, missing or inconsistent. |

Required scene dependencies return `Scene asset versions unavailable.` or `Scene anatomy annotations unavailable.` Published storage errors return `Published asset storage unavailable.` Retry service or capacity failures with backoff. Manifest limits return the specific node, asset-version, or serialized-size detail below. An operator must adjust a manifest that exceeds the configured bounds or change the limits.

| Runtime setting | Default | Allowed range | Limit error detail |
| --- | ---: | ---: | --- |
| `ATLAS_MANIFEST_MAX_NODES` | 2048 | 1–10000 | `Scene manifest node limit exceeded.` |
| `ATLAS_MANIFEST_MAX_ASSETS` | 256 | 1–1024 | `Scene manifest asset version limit exceeded.` |
| `ATLAS_MANIFEST_MAX_BYTES` | 1048576 | 1–8388608 | `Scene manifest serialized size limit exceeded.` |
| `ATLAS_MANIFEST_SLOTS` | 1 | 1–8 | `Scene manifest capacity unavailable.` |
| `ATLAS_DELIVERY_FILE_SLOTS` | 2 | 1–8 | `Asset delivery capacity unavailable.` |
| `ATLAS_DELIVERY_SEND_TIMEOUT_SECONDS` | 120 | 1–900 | Incomplete transfer is aborted. |

Slots are per API worker. A binary transfer occupies a slot during verification and until its response completes, disconnects, or times out. The send deadline starts after verification so it applies to the response, while verification depends on local storage I/O. The separate manifest pool bounds manifest assembly and sequential storage preflight even when downloads occupy every binary slot. The service reads and writes bounded blocks of at most 256 KiB per active transfer. Use a single API worker on a 1 vCPU host so per-worker limits and connection pools remain predictable. Integrity verification reads the bytes once and delivery streams them a second time.

For development, set `ATLAS_ASSET_STORAGE_ROOT` to an isolated, absolute local storage root on the same filesystem seen by the importer. The default is `/srv/atlas/assets`. Run the backend and processing suites against disposable PostgreSQL 16 and the pinned validator. Tests use generated GLBs. [Backend setup and contract generation](../backend/README.md#test-locally) · [Processing checks](../processing/README.md#checks) · [Storage migration and HTTPS proxy](../deployment/ASSET-DELIVERY.md)

## Offline download protocol

1. Fetch the manifest from the chosen service. Validate the manifest schema version, requested scene identity, the complete parent and anatomy references, and every exact asset pin. Treat all version identifiers as opaque strings.
2. Resolve each download URL against the HTTPS service origin. Require the same origin and the exact registered asset path. Build a download set by SHA-256 and reject inconsistent size declarations for the same digest. Keep the manifest's code and version entries even when two entries reference identical bytes.
3. Reuse locally verified objects by digest and size. Download each missing object into temporary local storage. While writing, count bytes and hash the GLB. Accept the object only if both values match the manifest. Discard or isolate partial and inconsistent files. Retry a full GET.
4. Preserve the manifest and all source, attribution, license, and modification records with the verified objects. Keep these records accessible offline.
5. Commit the new active manifest pointer locally only after every required file and its metadata are durable and verified. On interruption or failure, retain the previous usable pointer and its objects. Resume by deduplicating verified objects against a newly validated manifest. Remove unreferenced objects only after accounting for retained manifests and active downloads.

Connection failures and server withdrawals may leave installed data usable offline. The client's offline UI should identify the installed version and when it last synchronized. Where license or redistribution changes require removing data, follow the applicable policy on reconnection. [Asset licensing](ASSET-LICENSING.md)
