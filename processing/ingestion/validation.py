"""P1.2 integrity checks and bounded offline Khronos validation."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile
from typing import BinaryIO

from atlas_api.assets.validation import (
    CHUNK_HEADER,
    GLB_HEADER,
    GLBValidationError,
    validate_glb_file,
)

MIB = 1024**2
VALIDATOR_VERSION = "2.0.0-dev.3.10"
VALIDATOR_NAME = "Khronos glTF Validator"
VALIDATOR_DIR = Path(__file__).resolve().parent
OUTPUT_SIZE = MIB


class IngestionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Limits:
    input_bytes: int = 64 * MIB
    json_bytes: int = 2 * MIB
    wall_seconds: int = 25
    cpu_seconds: int = 15
    memory_mib: int = 1024

    def __post_init__(self) -> None:
        if not (
            24 <= self.input_bytes <= 256 * MIB
            and 4 <= self.json_bytes <= min(16 * MIB, self.input_bytes)
            and 1 <= self.cpu_seconds <= self.wall_seconds <= 120
            and 512 <= self.memory_mib <= 1536
        ):
            raise ValueError("Invalid limits. See the processing ingestion guide.")


def verify_container(file: BinaryIO, size: int, sha256: str, limits: Limits) -> None:
    try:
        validate_glb_file(file, expected_size=size, expected_sha256=sha256)
    except GLBValidationError as exc:
        raise IngestionError("GLB_INTEGRITY", str(exc)) from None
    file.seek(GLB_HEADER.size)
    length, _ = CHUNK_HEADER.unpack(file.read(CHUNK_HEADER.size))
    if length > limits.json_bytes:
        raise IngestionError(
            "GLTF_JSON_TOO_LARGE", "GLB JSON chunk exceeds the configured limit."
        )


def validate_semantics(file: BinaryIO, limits: Limits) -> dict:
    node = shutil.which("node")
    dependency = VALIDATOR_DIR / "node_modules" / "gltf-validator"
    script = VALIDATOR_DIR / "validator.cjs"
    if node is None or not dependency.is_dir():
        raise IngestionError(
            "VALIDATOR_UNAVAILABLE", "Install Node.js and the pinned validator."
        )

    file.seek(0)
    with TemporaryFile() as output:
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(VALIDATOR_DIR / "sandbox.py"),
                    str(os.getpid()),
                    str(limits.cpu_seconds),
                    str(limits.memory_mib * MIB),
                    str(OUTPUT_SIZE),
                    os.path.abspath(node),
                    "--permission",
                    "--no-addons",
                    "--jitless",
                    "--v8-pool-size=1",
                    f"--max-old-space-size={limits.memory_mib // 4}",
                    f"--allow-fs-read={script}",
                    f"--allow-fs-read={dependency}",
                    str(script),
                    VALIDATOR_VERSION,
                ],
                cwd=VALIDATOR_DIR,
                env={"LANG": "C.UTF-8", "UV_THREADPOOL_SIZE": "1"},
                stdin=file,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=limits.wall_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise IngestionError(
                "VALIDATOR_TIMEOUT", "Validator exceeded its wall time limit."
            ) from None
        except OSError:
            raise IngestionError(
                "VALIDATOR_UNAVAILABLE", "Could not launch the validator."
            ) from None
        if os.fstat(output.fileno()).st_size >= OUTPUT_SIZE:
            raise IngestionError(
                "VALIDATOR_OUTPUT_LIMIT", "Validator reached its output limit."
            )
        if result.returncode:
            raise IngestionError(
                "VALIDATOR_PROCESS_FAILED",
                "Validator failed to start or exceeded a process limit.",
            )
        output.seek(0)
        try:
            report = json.load(output)
            if report["version"] != VALIDATOR_VERSION or not isinstance(
                report["messages"], list
            ):
                raise ValueError
        except (UnicodeError, ValueError, KeyError, TypeError):
            raise IngestionError(
                "VALIDATOR_REPORT_INVALID", "Validator returned an invalid report."
            ) from None
        return report
