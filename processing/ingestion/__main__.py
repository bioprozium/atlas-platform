"""Inspect a GLB or register a technically validated draft from a source manifest."""

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from fcntl import LOCK_EX, LOCK_NB, flock
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from atlas_api.assets.ingestion import UnconfiguredRegistryError, ingestion_registry
from atlas_api.assets.validation import ASSET_ROOT, GLBValidationError, open_directory

from .metadata import Manifest, digest_argument, read_manifest
from .storage import StagedAsset, check_directory, delivery_group, promote, stage
from .validation import (
    MIB,
    VALIDATOR_NAME,
    VALIDATOR_VERSION,
    IngestionError,
    Limits,
    validate_semantics,
    verify_container,
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def add_issue(report: dict, code: str, message: str) -> None:
    report["issues"].append({"code": code, "message": message, "severity": 0})


def run(
    args: argparse.Namespace, limits: Limits, manifest: Manifest | None
) -> tuple[dict, int]:
    report = {
        "schema_version": 1,
        "ingestion_id": str(uuid4()),
        "operation": args.operation,
        "started_at": now(),
        "sha256": None,
        "byte_length": None,
        "expected_sha256": args.expected_sha256,
        "validation_status": "error",
        "storage_verified": False,
        "validator": {
            "name": VALIDATOR_NAME,
            "version": VALIDATOR_VERSION,
            "completed": False,
        },
        "limits": asdict(limits),
        "issues": [],
    }
    if manifest is not None:
        report.update(
            asset_code=manifest.asset_code,
            version=manifest.version,
            source_geometry=manifest.source_geometry.model_dump(),
        )

    if manifest is None:
        process_storage(
            args.storage_root or ASSET_ROOT, args, limits, report, store=False
        )
        return report, 0 if report["validation_status"] == "passed" and not any(
            issue["severity"] == 0 for issue in report["issues"]
        ) else 1

    try:
        with ingestion_registry() as registry:
            storage_root = args.storage_root or registry.storage_root
            if storage_root != registry.storage_root:
                raise IngestionError(
                    "STORAGE_ROOT_MISMATCH",
                    "Requested storage root differs from the registry configuration.",
                )
            process_storage(storage_root, args, limits, report, store=True)
            registration = registry.record_ingestion(
                storage_root,
                report,
                manifest.title,
                [s.model_dump() for s in manifest.sources],
            )
    except UnconfiguredRegistryError:
        return registration_blocked(
            report,
            "STORAGE_ROOT_UNCONFIGURED",
            "The registry has no configured storage root.",
        )
    except IngestionError as exc:
        return registration_blocked(report, exc.code, str(exc))
    except (SQLAlchemyError, ValueError):
        report.setdefault("finished_at", now())
        report["registration"] = {"outcome": "unconfirmed"}
        add_issue(
            report,
            "DATABASE_UNCONFIRMED",
            "Registration could not be confirmed. Retry the same import.",
        )
        return report, 1

    report["registration"] = {
        "outcome": registration.outcome,
        "version_id": str(registration.version_id) if registration.version_id else None,
    }
    if registration.outcome == "content_conflict":
        add_issue(
            report,
            "VERSION_CONTENT_CONFLICT",
            "The exact asset code and version have different bytes.",
        )
    elif registration.outcome == "metadata_conflict":
        add_issue(
            report,
            "VERSION_METADATA_CONFLICT",
            "The asset title, version state or provenance differ.",
        )
    return report, 0 if registration.outcome in {"created", "reused"} else 1


def registration_blocked(report: dict, code: str, message: str) -> tuple[dict, int]:
    report["finished_at"] = now()
    report["registration"] = {"outcome": "not_registered"}
    add_issue(report, code, message)
    return report, 1


def process_storage(
    storage_root: Path,
    args: argparse.Namespace,
    limits: Limits,
    report: dict,
    *,
    store: bool,
) -> None:
    try:
        with open_directory(storage_root, trusted_ancestors=True) as root:
            gid = delivery_group(root, getattr(args, "delivery_group", None)) if store else None
            check_directory(root, delivery_gid=gid)
            try:
                flock(root, LOCK_EX | LOCK_NB)
            except BlockingIOError:
                raise IngestionError(
                    "INGESTION_BUSY",
                    "Another operation is validating this storage root.",
                ) from None
            with stage(root, args.input, limits) as asset:
                report.update(sha256=asset.digest, byte_length=asset.size)
                validate_staged(asset, limits, args.expected_sha256, report)
                if store and report["validation_status"] == "passed":
                    created = promote(root, asset, limits, delivery_gid=gid)
                    report.update(
                        storage_verified=True,
                        storage_reference=asset.reference,
                        storage_outcome="created" if created else "reused",
                    )
    except IngestionError as exc:
        if exc.code in {
            "INPUT_TOO_LARGE",
            "UNSAFE_INPUT",
            "INPUT_CHANGED",
            "SHA256_MISMATCH",
            "GLB_INTEGRITY",
            "GLTF_JSON_TOO_LARGE",
        }:
            report["validation_status"] = "rejected"
        add_issue(report, exc.code, str(exc))
    except (OSError, GLBValidationError):
        report["storage_verified"] = False
        add_issue(
            report,
            "STAGING_FAILED",
            "Could not safely stage or clean up the input file.",
        )
    report["finished_at"] = now()


def validate_staged(
    asset: StagedAsset, limits: Limits, expected_digest: str | None, report: dict
) -> None:
    if expected_digest is not None and asset.digest != expected_digest:
        raise IngestionError(
            "SHA256_MISMATCH", "The staged bytes differ from the expected SHA-256."
        )
    verify_container(asset.file, asset.size, asset.digest, limits)
    semantic = validate_semantics(asset.file, limits)
    report["validator"]["completed"] = "numErrors" in semantic
    report["issues"].extend(semantic["messages"])
    report["summary"] = semantic.get("summary", {})
    if semantic.get("truncated"):
        add_issue(
            report, "VALIDATOR_ISSUE_LIMIT", "The validator stopped at its issue limit."
        )
    report["validation_status"] = (
        "rejected"
        if semantic.get("numErrors")
        or any(issue["severity"] == 0 for issue in report["issues"])
        else "passed"
    )


def parser() -> argparse.ArgumentParser:
    defaults = Limits()
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--storage-root",
        type=Path,
        help="Override the inspection directory or assert the database-configured root for import.",
    )
    result.add_argument(
        "--max-input-mib", type=int, default=defaults.input_bytes // MIB
    )
    result.add_argument("--max-json-kib", type=int, default=defaults.json_bytes // 1024)
    result.add_argument("--wall-seconds", type=int, default=defaults.wall_seconds)
    result.add_argument("--cpu-seconds", type=int, default=defaults.cpu_seconds)
    result.add_argument("--memory-mib", type=int, default=defaults.memory_mib)
    operations = result.add_subparsers(dest="operation", required=True)
    for name in ("inspect", "import"):
        operation = operations.add_parser(name)
        operation.add_argument("input", type=Path)
        operation.add_argument("--expected-sha256", type=digest_argument)
        if name == "import":
            operation.add_argument("--metadata", type=Path, required=True)
            operation.add_argument(
                "--delivery-group",
                choices=["atlasdelivery"],
                help="Require atlasdelivery traversal and install verified objects with mode 0440.",
            )
    return result


def main() -> int:
    os.umask(0o077)
    options = parser()
    args = options.parse_args()
    try:
        limits = Limits(
            input_bytes=args.max_input_mib * MIB,
            json_bytes=args.max_json_kib * 1024,
            wall_seconds=args.wall_seconds,
            cpu_seconds=args.cpu_seconds,
            memory_mib=args.memory_mib,
        )
    except ValueError as exc:
        options.error(str(exc))

    try:
        manifest = read_manifest(args.metadata) if args.operation == "import" else None
    except IngestionError as exc:
        report = {"schema_version": 1, "validation_status": "error", "issues": []}
        add_issue(report, exc.code, str(exc))
        print(json.dumps(report, sort_keys=True))
        return 1

    report, exit_code = run(args, limits, manifest)
    print(json.dumps(report, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
