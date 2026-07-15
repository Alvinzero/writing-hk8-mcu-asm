#!/usr/bin/env python3
r"""HK8 compiler adapter that wraps the real hk64s8x-cli ASMC compile command.

The closed-loop ``hk8asm.py`` runner calls adapters through this contract:

    <command...> compiler <probe|run> --input input.json --output output.json

This adapter does not bundle the company compiler.  It requires explicit paths
to:

* ``asmc_compile.py`` from ``D:\hk64s8x-cli`` or an equivalent local checkout.
* the company ``HK_ASM_Compiler`` source root used by that ASMC wrapper.

It fails closed when those paths are missing, and it always runs the ASMC
``compile --json`` command instead of substituting a static check or mock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


ROLE = "compiler"
REQUIRED_COMPILER_FILES = (
    "src/core/assembler.py",
    "src/core/output_generator.py",
    "src/core/chip_manager.py",
    "src/core/online_flasher.py",
    "instruction_set.xlsx",
    "register_set.xlsx",
)
VERSION_FILES = (
    "src/core/assembler.py",
    "src/core/instruction_parser.py",
    "src/core/output_generator.py",
    "src/core/chip_manager.py",
    "instruction_set.xlsx",
    "register_set.xlsx",
    "include/REG825.INC",
)


class AdapterFailure(Exception):
    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdapterFailure("invalid_input", f"cannot read adapter input JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AdapterFailure("invalid_input", "adapter input must be a JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def parse_json_document(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise AdapterFailure("asmc_no_json", "ASMC did not print JSON")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise AdapterFailure("asmc_bad_json", "ASMC stdout did not contain a JSON object")
        try:
            payload = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AdapterFailure("asmc_bad_json", f"cannot parse ASMC JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AdapterFailure("asmc_bad_json", "ASMC JSON result must be an object")
    return payload


def require_path(path: Path | None, code: str, message: str, *, is_dir: bool = False) -> Path:
    if path is None:
        raise AdapterFailure(code, message)
    resolved = path.expanduser().resolve()
    if is_dir:
        if not resolved.is_dir():
            raise AdapterFailure(code, f"{message}: {resolved}")
    elif not resolved.is_file():
        raise AdapterFailure(code, f"{message}: {resolved}")
    return resolved


def validate_compiler_source_root(source_root: Path) -> None:
    missing = [relative for relative in REQUIRED_COMPILER_FILES if not (source_root / relative).is_file()]
    if missing:
        raise AdapterFailure(
            "compiler_source_root_incomplete",
            "company compiler source root is incomplete",
            details={"missing": missing, "compiler_source_root": str(source_root)},
        )


def computed_tool_version(asmc_cli: Path, compiler_source_root: Path, compiler_mcu_type: str) -> str:
    digest = hashlib.sha256()
    digest.update(f"asmc_cli\0{sha256_file(asmc_cli)}\0".encode("utf-8"))
    digest.update(f"compiler_mcu_type\0{compiler_mcu_type}\0".encode("utf-8"))
    for relative in VERSION_FILES:
        path = compiler_source_root / relative
        if path.is_file():
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(sha256_file(path).encode("ascii"))
            digest.update(b"\0")
    return f"hk64s8x-asmc-source-module:{digest.hexdigest()[:16]}"


def make_project(workspace: Path, source: Path, compiler_mcu_type: str) -> Path:
    project_name = source.stem or "main"
    project_path = workspace / f"{project_name}.hkproj"
    project = {
        "name": project_name,
        "files": [str(source)],
        "build_path": "build",
        "settings": {
            "mcu_type": compiler_mcu_type,
            "output_format": "BIN+HEX+MAP",
        },
    }
    write_json(project_path, project)
    return project_path


def run_asmc_compile(args: argparse.Namespace, source: Path, workspace: Path) -> dict[str, Any]:
    project_path = make_project(workspace, source, args.compiler_mcu_type)
    command = [
        str(args.python),
        str(args.asmc_cli),
        "compile",
        "--workspace",
        str(workspace),
        "--source",
        str(source),
        "--project",
        str(project_path),
        "--compiler-source-root",
        str(args.compiler_source_root),
        "--json",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.asmc_timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AdapterFailure("asmc_timeout", f"ASMC compile timed out after {args.asmc_timeout_seconds}s") from exc
    except OSError as exc:
        raise AdapterFailure("asmc_exec_failed", f"cannot execute ASMC compile command: {exc}") from exc

    (workspace / "adapter-asmc.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (workspace / "adapter-asmc.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    result = parse_json_document(completed.stdout)
    if completed.returncode != 0 or result.get("status") != "ok":
        error = result.get("error", {})
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            message = error["message"]
        elif isinstance(result.get("summary"), str):
            message = result["summary"]
        else:
            message = f"ASMC returned exit code {completed.returncode}"
        raise AdapterFailure("asmc_compile_failed", message, details=safe_asmc_failure(result))
    return result


def safe_asmc_failure(result: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key in ("status", "action", "summary", "error", "metrics"):
        value = result.get(key)
        if value is not None:
            safe[key] = value
    details = result.get("details")
    if isinstance(details, dict):
        safe["details"] = {
            key: value
            for key, value in details.items()
            if key in {"returncode", "diagnostics", "log_file", "mcu_type"}
        }
    return safe


def artifact_from_result(result: dict[str, Any], kind: str) -> Path:
    artifacts = result.get("artifacts")
    details = result.get("details")
    key = f"{kind}_file"
    value = None
    if isinstance(artifacts, dict):
        value = artifacts.get(key)
    if not value and isinstance(details, dict):
        value = details.get(key)
    if not isinstance(value, str) or not value:
        raise AdapterFailure("artifact_missing", f"ASMC result did not include {key}")
    path = Path(value)
    if not path.is_file():
        raise AdapterFailure("artifact_missing", f"ASMC artifact is missing: {path}")
    return path.resolve()


def optional_artifact(result: dict[str, Any], key: str) -> Path | None:
    artifacts = result.get("artifacts")
    details = result.get("details")
    value = None
    if isinstance(artifacts, dict):
        value = artifacts.get(key)
    if not value and isinstance(details, dict):
        value = details.get(key)
    if isinstance(value, str) and value:
        path = Path(value)
        if path.is_file():
            return path.resolve()
    return None


def warning_messages(result: dict[str, Any]) -> list[str]:
    details = result.get("details")
    diagnostics = details.get("diagnostics") if isinstance(details, dict) else []
    warnings = []
    if isinstance(diagnostics, list):
        for item in diagnostics:
            if not isinstance(item, dict):
                continue
            if str(item.get("level", "")).lower() != "warning":
                continue
            message = item.get("message")
            warnings.append(str(message) if message else json.dumps(item, ensure_ascii=False, sort_keys=True))
    metrics = result.get("metrics")
    count = metrics.get("warnings") if isinstance(metrics, dict) else 0
    if isinstance(count, int) and count > len(warnings):
        warnings.extend(f"ASMC-WARNING-{index + 1}" for index in range(len(warnings), count))
    return warnings


def copy_if_present(source: Path | None, destination: Path) -> str | None:
    if source is None:
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return sha256_file(destination)


def run_probe(args: argparse.Namespace, payload: dict[str, Any]) -> dict[str, Any]:
    chip = payload.get("chip", "HK64S825")
    with tempfile.TemporaryDirectory(prefix="hk8-compiler-probe-") as temp:
        workspace = Path(temp)
        source = workspace / "probe.asm"
        source.write_text(
            f"; CHIP: {chip}\n; PURPOSE: compiler adapter probe\nORG 0x0000\nNOP\nEND\n",
            encoding="utf-8",
        )
        result = run_asmc_compile(args, source, workspace)
        hex_path = artifact_from_result(result, "hex")
        return {
            "status": "pass",
            "role": ROLE,
            "operation": "probe",
            "tool_version": args.tool_version,
            "toolchain": "hk64s8x-cli asmc source-module",
            "compiler_mcu_type": args.compiler_mcu_type,
            "source_sha256": sha256_file(source),
            "artifact_sha256": sha256_file(hex_path),
            "warnings": warning_messages(result),
            "metrics": result.get("metrics", {}),
        }


def run_compile(args: argparse.Namespace, payload: dict[str, Any], output_path: Path) -> dict[str, Any]:
    source_value = payload.get("source_path")
    artifact_value = payload.get("artifact_path")
    if not isinstance(source_value, str) or not source_value:
        raise AdapterFailure("missing_source_path", "compiler run payload is missing source_path")
    if not isinstance(artifact_value, str) or not artifact_value:
        raise AdapterFailure("missing_artifact_path", "compiler run payload is missing artifact_path")
    source = Path(source_value).resolve()
    if not source.is_file():
        raise AdapterFailure("source_not_found", f"source_path does not exist: {source}")
    artifact_path = Path(artifact_value).resolve()

    workspace = output_path.parent / f"{output_path.stem}.workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    result = run_asmc_compile(args, source, workspace)

    selected = artifact_from_result(result, args.artifact_kind)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(selected, artifact_path)

    bin_hash = copy_if_present(optional_artifact(result, "bin_file"), artifact_path.with_suffix(".bin"))
    map_hash = copy_if_present(optional_artifact(result, "map_file"), artifact_path.with_suffix(".map"))
    log_hash = copy_if_present(optional_artifact(result, "log_file"), artifact_path.with_suffix(".log"))

    return {
        "status": "pass",
        "role": ROLE,
        "operation": "run",
        "tool_version": args.tool_version,
        "toolchain": "hk64s8x-cli asmc source-module",
        "compiler_mcu_type": args.compiler_mcu_type,
        "source_sha256": sha256_file(source),
        "artifact_path": str(artifact_path),
        "artifact_kind": args.artifact_kind,
        "artifact_sha256": sha256_file(artifact_path),
        "warnings": warning_messages(result),
        "metrics": result.get("metrics", {}),
        "artifacts": {
            "hex_path": str(artifact_path) if args.artifact_kind == "hex" else None,
            "bin_path": str(artifact_path.with_suffix(".bin")) if bin_hash else None,
            "map_path": str(artifact_path.with_suffix(".map")) if map_hash else None,
            "log_path": str(artifact_path.with_suffix(".log")) if log_hash else None,
            "bin_sha256": bin_hash,
            "map_sha256": map_hash,
            "log_sha256": log_hash,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Python executable used to run asmc_compile.py")
    parser.add_argument("--asmc-cli", type=Path, help="Path to hk64s8x-cli/asmc/scripts/asmc_compile.py")
    parser.add_argument("--compiler-source-root", type=Path, help="Path to the company HK_ASM_Compiler source root")
    parser.add_argument(
        "--compiler-mcu-type",
        help="MCU type string accepted by the compiler source project file, for example a model listed under mcu/*.json",
    )
    parser.add_argument(
        "--tool-version",
        help="Approved compiler version string. If omitted, a hash-based version is computed from toolchain files.",
    )
    parser.add_argument(
        "--artifact-kind",
        choices=("hex", "bin"),
        default="hex",
        help="Which ASMC artifact to bind to hk8asm artifact_path",
    )
    parser.add_argument("--asmc-timeout-seconds", type=int, default=600)
    parser.add_argument("role")
    parser.add_argument("operation", choices=("probe", "run"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.role != ROLE:
        raise AdapterFailure("wrong_role", f"compiler_adapter.py only supports role={ROLE}")
    if not isinstance(args.asmc_timeout_seconds, int) or args.asmc_timeout_seconds <= 0:
        raise AdapterFailure("invalid_timeout", "--asmc-timeout-seconds must be positive")
    if not args.compiler_mcu_type or not str(args.compiler_mcu_type).strip():
        raise AdapterFailure(
            "missing_compiler_mcu_type",
            "--compiler-mcu-type is required; use the explicit MCU type accepted by HK_ASM_Compiler",
        )
    args.asmc_cli = require_path(args.asmc_cli, "missing_asmc_cli", "--asmc-cli is missing or not a file")
    args.compiler_source_root = require_path(
        args.compiler_source_root,
        "missing_compiler_source_root",
        "--compiler-source-root is missing or not a directory",
        is_dir=True,
    )
    validate_compiler_source_root(args.compiler_source_root)
    args.tool_version = args.tool_version or computed_tool_version(
        args.asmc_cli, args.compiler_source_root, args.compiler_mcu_type
    )


def failure_payload(exc: AdapterFailure) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": "fail", "role": ROLE, "code": exc.code, "error": exc.message}
    if exc.details is not None:
        payload["details"] = exc.details
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate_args(args)
        payload = read_json(args.input)
        if args.operation == "probe":
            result = run_probe(args, payload)
        else:
            result = run_compile(args, payload, args.output.resolve())
    except AdapterFailure as exc:
        write_json(args.output, failure_payload(exc))
        return 20
    except Exception as exc:  # pragma: no cover - final safety net
        write_json(
            args.output,
            {
                "status": "fail",
                "role": ROLE,
                "code": "adapter_internal_error",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return 20
    write_json(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
