# Asset delivery deployment

The API serves verified objects from private local storage. Keep the API listening on localhost and terminate TLS at the existing Apache virtual host. Keep PostgreSQL revision `0005` and the existing `atlas_app` read-only grants.

## Storage identities and modes

| Directory or file | Owner and group | Mode |
| --- | --- | --- |
| `/srv/atlas/assets`, `sha256`, and two-digit buckets | `atlas-ingest:atlasdelivery` | `2750` |
| Verified `<digest>.glb` | `atlas-ingest:atlasdelivery` | `0440` |
| `.quarantine` | `atlas-ingest`, group immaterial | `0700` permission bits |
| Staged objects | `atlas-ingest` | `0600` during copying and `0400` during validation |

Use a dedicated `atlasdelivery` Linux group. Grant membership to `atlasapi` and to the storage owner `atlas-ingest` where needed for chgrp. Keep `atlasdeploy` and Apache out of this group. Use trusted, non-writable ancestors and grant the API and importer permission to open the ancestors for directory traversal. The current implementation opens directory descriptors read-only and needs read plus execute on the path. For example, `/srv/atlas` can be administrator-owned with mode `0750` and group `atlasdelivery` if the services already using it retain their required access. Review ACLs or service sandbox settings for each identity. The API cannot write to the content tree or traverse quarantine. The group for application code, `atlascode`, is independent.

The importer supports `import INPUT --metadata MANIFEST --delivery-group atlasdelivery` after the directory transition. It also recognizes a setgid storage root owned by `atlasdelivery` as delivery-enabled. It creates directories with the intended owner, group and mode, and sets the verified inode's delivery group and mode before linking it out of private quarantine. Retries verify the existing object's contents and permissions without overwriting it. A private store without this delivery configuration still supports the original `0400` promotion.

## Controlled transition of existing objects

1. Schedule a maintenance window, pause imports and publication, and keep delivery disabled while changing permissions. Verify the mount and filesystem ownership, inventory the live content paths, and record their original permissions. Confirm the expected `atlasdelivery` membership and the effective API service identity.
2. As an authorized operator, check the root and each intended `sha256` directory and bucket with no symlink following. Restrict changes to ingest-owned directories in this layout. Set their group to `atlasdelivery` and mode to `2750`. Keep quarantine private and outside the migration traversal.
3. For verified ingest-owned regular files in the expected digest buckets, check the registered size, content SHA-256, and absence of outside hard links. Apply group `atlasdelivery` and mode `0440` explicitly to the vetted files. Investigate unexpected entries and permissions before continuing. Keep other files and other service roots outside this operation.
4. Configure the API service's `ATLAS_ASSET_STORAGE_ROOT` using the same absolute path and mount as the ingestion registry. An authorized database operator can run `SELECT storage_root FROM atlas.ingestion_storage WHERE singleton;` and compare it with the service configuration. Compare the actual filesystem mount and a verified digest object's device/inode or other mount identity as seen by both services. The database path alone cannot prove that differently sandboxed processes see the same bytes. Keep the canonical table and audit history private.
5. In a controlled service check, confirm that the effective API identity can open a verified published object read-only, cannot write it or create or replace files in the content buckets, and cannot traverse quarantine. Confirm the deployment identity has no asset access. Verify a binary GET's length and digest and a subsequent conditional GET. Verify the API database role still lacks write permissions and cannot read `atlas.ingestion_storage` or `atlas.asset_ingestions`. Resume imports using the delivery group after verifying the transition. [Ingestion guide](../processing/README.md)

## Apache reverse proxy example

Adapt this example to the existing TLS virtual host and certificates. Keep the GLB and manifest response bytes intact across the proxy so strong validators and declared byte lengths continue to identify the same representation. Skip proxy compression for these paths. Forward conditional headers through Apache to FastAPI, and keep proxy caching disabled for the API. The origin handles eligibility and 304 decisions.

```apacheconf
<VirtualHost *:443>
    ServerName atlas.example.invalid
    SSLEngine On
    SSLCertificateFile /path/to/fullchain.pem
    SSLCertificateKeyFile /path/to/privkey.pem

    ProxyRequests Off
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https"

    SetEnvIf Request_URI "^/api/v1/(assets/[^/]+/versions/[^/]+/file|scenes/[^/]+/versions/[^/]+/manifest)$" no-gzip=1 no-brotli=1
    <IfModule mod_cache.c>
        CacheDisable /api/v1/
    </IfModule>

    ProxyPass        "/api/v1/" "http://127.0.0.1:8000/api/v1/" connectiontimeout=5 timeout=120
    ProxyPassReverse "/api/v1/" "http://127.0.0.1:8000/api/v1/"
</VirtualHost>
```

Enable the applicable SSL, HTTP proxy, headers and environment modules for this example. Route application API paths through the proxy. Keep the asset tree outside Apache's document root and static mappings. [Scene manifest and cache contract](../docs/SCENE-MANIFEST.md)
