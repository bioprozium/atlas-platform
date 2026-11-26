# Scientific processing

Python and Blender workflows for preparing versioned scientific 3D assets. Record provenance and licensing for inputs and derivatives.

## Operator-controlled GLB ingestion

From a trusted checkout on Linux, install Python 3.12+, the [backend package](../backend/README.md#install-locally), Node.js 24 LTS, and the libseccomp shared runtime (`libseccomp2` on Debian and Ubuntu). Install the official Khronos glTF Validator **2.0.0-dev.3.10** with the repository lockfile. The lockfile pins the npm package and its integrity hash. Use the checked-in launcher and keep its software and dependencies outside the asset intake directory. [Khronos glTF Validator](https://github.com/KhronosGroup/glTF-Validator)

```sh
npm --prefix processing/ingestion ci --ignore-scripts --no-audit --no-fund
python -m processing.ingestion --help
```

Run the CLI as a dedicated, unprivileged operator account on the host that mounts the registry's canonical store. Keep staging and content buckets on the same local filesystem with hard-link, file and directory sync, and advisory lock support. Inspection defaults to `/srv/atlas/assets` and can use any safe directory supplied with `--storage-root`. Import uses the connected registry's canonical root. If supplied for import, `--storage-root` must match that binding.

```sh
umask 077
mkdir -m 700 "$PWD/local-assets"
python -m processing.ingestion --storage-root "$PWD/local-assets" inspect input.glb > inspect.json
python -m processing.ingestion import input.glb --metadata source.json > ingestion.json
```

Both commands accept `--expected-sha256 <lowercase-digest>` after the input. Use it when you have a trusted digest from the source. `inspect` runs the same integrity and semantic checks as `import`, then removes its quarantine copy. `import` also verifies the final stored object and registers its immutable version UUID and source records in a database transaction.

### Registration role

On a disposable local PostgreSQL 16 instance, apply Alembic revision `0005` using the existing migration workflow. Run `backend/provision_ingestion.sql` as a database administrator. It binds the registry to `/srv/atlas/assets` by default. For an explicitly isolated local or test registry, provide `atlas_storage_root` when provisioning it and point the CLI at that database. Run this example from the repository root against the local instance. [Backend PostgreSQL setup](../backend/README.md#configure-postgresql)

```sh
psql -X -d postgres -v "atlas_storage_root=$PWD/local-assets" -f backend/provision_ingestion.sql
```

Provisioning accepts the same binding on reruns and refuses a different one. Set up authentication for `atlas_ingest` using the instance's local policy. For password authentication, use the interactive `\password atlas_ingest` prompt. Give the CLI `ATLAS_INGESTION_DATABASE_URL`, a `postgresql+psycopg` URL for `atlas_ingest` on `atlas_db`. Store credentials through the normal secret handling workflow.

### Storage trust boundary

- The database owner controls the canonical absolute root in the private `atlas.ingestion_storage` table. The CLI reads that binding using the same database transaction it will use for registration. A different requested root stops the import before staging, promotion, or any registry or audit write. The binding stays locked through promotion and registration. The database function independently checks the root claimed by its caller before it makes any writes.
- `atlas_ingest` receives database `CONNECT`, schema `USAGE`, and `EXECUTE` on the binding reader and draft registration function. It has no table grants or migration role membership. `atlas_app` has no access to these functions, the binding, or ingestion history. Registry records and reports carry relative content references.
- The CLI verifies the bytes in the configured store. PostgreSQL checks the claimed root against the configured path and trusts the CLI's validation and storage attestation. A caller with direct use of the ingestion credential can fabricate an attestation, and the configured path alone cannot establish that two hosts or mounts share the same files. Restrict direct use of the credential, control the mount and its ancestors as deployment configuration, and run the importer against the designated filesystem.

A rollback to `0004` removes the root binding, ingestion functions, and audit history. It preserves registry rows, scene pins, and storage objects. Archive the binding and any required audit records before rollback. After reapplying `0005`, rerun provisioning with the original root before resuming imports.

### Source manifest

Supply one UTF-8 JSON object per asset version. Use the registry's lowercase asset code and version identifier, the asset title, the source geometry declaration, and 1–32 source records. Record source units and orientation before export, relevant geometry and transformation details, and the actual tool versions and export parameters. Add a source entry for each original asset and dependency, including embedded textures and underlying data. Describe each modification or state `No modifications`. Use publishable source and license citations and include an SPDX identifier when applicable. Omit the SPDX field or use `null` when none applies. Keep local paths and confidential evidence in the internal editorial record. The manifest is capped at 128 KiB. [Asset licensing](../docs/ASSET-LICENSING.md)

```json
{
  "asset_code": "synthetic-triangle",
  "version": "1",
  "title": "Synthetic Triangle",
  "source_geometry": {
    "units": "m",
    "orientation": "Y-up, right handed",
    "geometry_notes": "Single flat triangle generated for pipeline testing",
    "export_parameters": "Synthetic generator, GLB 2.0, float32 positions"
  },
  "sources": [
    {
      "creator": "Example author",
      "rights_holder": "Example rights holder",
      "source_reference": "Synthetic fixture generation record",
      "spdx_license_id": "CC0-1.0",
      "license_reference": "https://creativecommons.org/publicdomain/zero/1.0/",
      "attribution": "Synthetic example",
      "modifications": "No modifications"
    }
  ]
}
```

### Validation and review

The JSON report carries an attempt UUID, UTC timestamps, digest and byte count when available, validation status, the validator identity and version, configured limits, and issue codes, severities and locations. A successful import includes the exact immutable version UUID and the relative content reference. Protect reports as internal records. The shell opens redirected files before the CLI starts, so set `umask 077` before redirecting.

- Exit `0` means inspection passed or the matching draft registration was created or reused. Exit `1` means the check failed, registration conflicted, or the commit could not be confirmed. Command syntax errors return `2`.
- Severity `0` blocks technical acceptance. Severity `1` warnings require explicit editorial review. Severities `2` and `3` carry information and hints from Khronos. A truncated validator issue list blocks acceptance.
- The CLI checks the GLB 2.0 container, size and SHA-256, then checks UTF-8 JSON, glTF schema, internal references, meshes, accessors, buffers, buffer views, images, textures, and materials. The required-extension profile supports `KHR_materials_unlit`. Assets may use core glTF without extensions. The CLI rejects any other required extension and any external buffer or image, including local paths and URLs. Embedded buffer views, BIN chunks, and data URIs can be validated.
- Successful technical validation qualifies the bytes for editorial review. An editor must check creator and rights holder, original source and permissions, redistribution and offline rights, attribution and modifications, scientific and anatomical correctness, and source units, orientation and geometry. Review validator warnings and record the disposition. Choose the primary source, record rights verification and its internal reference, and publish the asset and exact version through the controlled registry workflow. [Editorial publication](../backend/README.md#editorial-publication)

### Storage and limits

The CLI opens path components without following symlinks or resolving parent traversal. The storage root and content subdirectories must be operator-owned and protected from group and other writers. Keep their ancestors under trusted ownership and deny group or other writes except under an administrator-owned sticky directory such as `/tmp`. The private `.quarantine` directory is created with mode `0700`. Staged files start with mode `0600` and become read-only before validation. A completed file is linked into `sha256/<first-two-hex-digits>/<digest>.glb` with mode `0400`. An existing object must be operator-owned, have no write bits, and match the staged file's size, framing and digest. Keep the store out of publicly served directories.

| Bound | Default | CLI flag |
| --- | --- | --- |
| Input size | 64 MiB | `--max-input-mib`, up to 256 |
| JSON chunk size | 2 MiB | `--max-json-kib`, up to 16384 and at most input size |
| Validator wall time | 25 seconds | `--wall-seconds`, up to 120 |
| Validator CPU time | 15 seconds | `--cpu-seconds`, at most wall time |
| Validator address space | 1,024 MiB | `--memory-mib`, 512–1536 |
| Validator report / issue count | 1 MiB / 100 reported issues | Fixed |

Python copies and hashes the asset in bounded blocks. Khronos validation loads the bytes inside the bounded child process. The child receives the staged file on standard input and a restricted environment. Linux seccomp denies socket creation and network connections. Node permissions restrict filesystem access to its checked-in wrapper and pinned dependency and deny child processes, workers, and native addons. The launcher also restricts file descriptors and disables core dumps. Only one operation can stage and run validation against a given storage root at a time. The defaults leave capacity for PostgreSQL and FastAPI on a small host. Large or complex assets can hit resource limits even within the byte caps. Source read and destination sync depend on the local filesystem and are outside the validator wall clock.

### Recovery and audit

Filesystem promotion precedes PostgreSQL registration. Successful promotion verifies the final content and syncs the file and directories before the registry transaction. The two systems have separate commit boundaries.

- If interrupted during staging, rerun the same inputs. Each run uses a fresh exclusive quarantine filename. After confirming that no ingestion process is running against that root, remove abandoned files from `.quarantine` by their generated names. A forced exit can leave an incomplete staging file there.
- If interrupted after promotion or around the database commit, rerun with the same input and manifest. The importer checks the stored object and either completes registration or returns the existing exact version UUID. A successful retry can produce another audit attempt. A different object at the expected path fails integrity checks and requires operator investigation.
- A code and version with different bytes fails explicitly. A mismatch in stored title, technical state, or source metadata also fails. Use a new version for changed bytes. Route changes to existing metadata through editorial review.
- A failed or interrupted transaction or a version conflict can leave a verified content-addressed file with no registry row. Keep verified objects. Administrators can reconcile by digest against `atlas.asset_versions` and review `atlas.asset_ingestions` by attempt UUID, asset code and version. Import attempts that reached the database commit boundary carry the private JSON validation report, PostgreSQL login and recorded time. A force-kill or database outage can leave only the storage object or local report. Inspection reports stay with the operator.

The initial profile covers self-contained GLB 2.0. It provides technical checks for ingestion and leaves anatomical judgment, source claims, usage rights, and eventual client rendering behavior to their respective reviews.

### Checks

Install the backend test extras and the pinned validator, and put PostgreSQL 16 server tools on `PATH`. Run these commands from the repository root.

```sh
python -m pytest -c backend/pyproject.toml processing/tests
python -m pytest -c backend/pyproject.toml backend/tests
node --check processing/ingestion/validator.cjs
```

The processing suite uses deterministic synthetic GLBs. The backend suite provisions its roles and runs its migrations in disposable PostgreSQL clusters.
