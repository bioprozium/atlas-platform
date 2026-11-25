# Repository guidance

## Architecture and boundaries

- Use Python 3.12+ and FastAPI for the backend, with PostgreSQL 16 for server-side persistence. Keep database access, migrations, and server-side authorization in `backend/`.
- Use Kotlin and Jetpack Compose for Android, Swift and SwiftUI for iOS, and React and TypeScript for the web. Clients communicate with the backend through REST and must never connect directly to PostgreSQL or depend on backend internals.
- Maintain the versioned OpenAPI contract in `contracts/`. Update it with API changes, keep existing clients compatible where possible, and explicitly version breaking changes.
- Keep Python and Blender processing in `processing/`. Record tool versions, source provenance, and export parameters so the published assets can be traced and regenerated. Keep source assets and generated outputs subject to the [asset licensing policy](docs/ASSET-LICENSING.md).

## Offline-first behavior

- Store downloaded models, annotations, and their manifests locally so users can reopen and browse them after restarting without connectivity.
- Show whether content is available locally and surface download or synchronization errors without blocking access to the last usable copy.
- Validate the integrity and version of new content before making it active. Interrupted downloads and updates must be safe to retry and must preserve the previous usable version.

## Testing

- Run the documented lint, build, and test checks for each affected component and report the results in the pull request. Cover changed behavior with targeted tests when introducing functionality.
- Test API implementation against OpenAPI and run database integration and migration checks against PostgreSQL 16 when persistence changes.
- Test affected client flows with no connection, after restart, and after an interrupted download or update. Check that recovery preserves previously downloaded content.
- Verify processing outputs with small, deterministic fixtures that can be redistributed. Keep test data, accounts, and services isolated from production.

## Security

- Never commit database credentials, tokens, private keys, signing files, real secrets, or production configuration. Inject sensitive values at runtime through the environment or an approved secrets manager.
- Validate untrusted API input and imported asset bundles. Enforce authorization server-side and grant services the minimum permissions they need.
- Keep sensitive data out of logs and fixtures. Treat scripts and archives from external sources as untrusted.
- Keep development and test activities isolated from production infrastructure. Deployments require separate authorization.
