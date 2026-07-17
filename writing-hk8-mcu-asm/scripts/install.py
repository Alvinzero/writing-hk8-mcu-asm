#!/usr/bin/env python3
"""Install the HK8 ASM skill into Codex or Claude Code skill directories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path, PureWindowsPath
from typing import Any


SKILL_NAME = "writing-hk8-mcu-asm"
EXCLUDED_DIRS = {
    ".git",
    ".hk8asm",
    ".pytest_cache",
    ".smoke",
    "__pycache__",
    "docs",
    "evals",
    "tests",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
EXCLUDED_RELATIVE_DIRS = {
    "references/spec/analysis",
    "references/spec/templates",
    "references/spec/tools/tests",
}
EXCLUDED_RELATIVE_FILES = {
    "references/spec/tools/build_analysis_snapshot.py",
}
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]+")
UNC_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![\\])\\\\[A-Za-z0-9._$ -]+[\\/][A-Za-z0-9._$ -]+"
)
POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?:^|[\s=:'\",(\[])(?P<path>/(?!/)(?:[A-Za-z0-9._~+@%=-]+/)+[A-Za-z0-9._~+@%=-]+)"
)
PROVENANCE_PATH_KEYS = {
    "metadata_file",
    "reg825_inc",
    "register_metadata_file",
    "source_file",
    "upstream_source_file",
}
DOCUMENTATION_PATH_REPLACEMENTS = {
    "D:" + "/" + "path/to/register_set.json": "<register-metadata.json>",
    "D:" + "/spec": "<spec-root>",
}


class InstallError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def destination_for(target: str, project_dir: Path | None) -> Path:
    home = Path.home()
    if target == "codex-user":
        return home / ".agents" / "skills" / SKILL_NAME
    if target == "claude-user":
        return home / ".claude" / "skills" / SKILL_NAME
    if project_dir is None:
        raise InstallError("PROJECT_DIR_REQUIRED", "--project-dir is required for project targets")
    project = project_dir.resolve()
    if target == "codex-project":
        return project / ".agents" / "skills" / SKILL_NAME
    if target == "claude-project":
        return project / ".claude" / "skills" / SKILL_NAME
    raise InstallError("INVALID_TARGET", f"Unknown target: {target}")


def should_exclude_relative(path: Path) -> bool:
    relative = path.as_posix()
    return relative in EXCLUDED_RELATIVE_DIRS or relative in EXCLUDED_RELATIVE_FILES


def ignore_names(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    root = skill_root().resolve()
    base = Path(directory).resolve()
    for name in names:
        if name in EXCLUDED_DIRS or Path(name).suffix in EXCLUDED_SUFFIXES:
            ignored.add(name)
            continue
        candidate = base / name
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if should_exclude_relative(relative):
            ignored.add(name)
    return ignored


def remove_existing(destination: Path) -> None:
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def portable_path_name(value: str) -> str:
    return PureWindowsPath(value.replace("/", "\\")).name


def find_nonportable_absolute_path(text: str) -> str | None:
    for line in text.splitlines() or [text]:
        if line.startswith("#!/usr/bin/env "):
            continue
        for pattern in (
            WINDOWS_ABSOLUTE_PATH_RE,
            UNC_ABSOLUTE_PATH_RE,
            POSIX_ABSOLUTE_PATH_RE,
        ):
            match = pattern.search(line)
            if match:
                return match.groupdict().get("path") or match.group(0)
    return None


def find_nonportable_json_path(value: Any) -> str | None:
    if isinstance(value, dict):
        for item in value.values():
            match = find_nonportable_json_path(item)
            if match:
                return match
        return None
    if isinstance(value, list):
        for item in value:
            match = find_nonportable_json_path(item)
            if match:
                return match
        return None
    if isinstance(value, str):
        return find_nonportable_absolute_path(value)
    return None


def read_utf8_text(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def sanitize_json_paths(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {item_key: sanitize_json_paths(item, item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_json_paths(item, key) for item in value]
    if (
        isinstance(value, str)
        and key in PROVENANCE_PATH_KEYS
        and find_nonportable_absolute_path(value)
    ):
        return portable_path_name(value)
    return value


def rewrite_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sanitize_portable_copy(destination: Path) -> None:
    rules = destination / "references" / "spec" / "rules"
    json_paths = sorted(destination.rglob("*.json"))
    for path in json_paths:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        sanitized = sanitize_json_paths(payload)
        if sanitized != payload:
            rewrite_json(path, sanitized)
        local_path = find_nonportable_json_path(sanitized)
        if local_path:
            relative = path.relative_to(destination).as_posix()
            raise InstallError(
                "PORTABILITY_VIOLATION",
                f"Portable copy contains a machine-local absolute path: {relative}",
            )

    instruction_metadata = rules / "instruction-metadata.json"
    instruction_reference = rules / "instruction-reference.json"
    if instruction_metadata.is_file() and instruction_reference.is_file():
        payload = json.loads(instruction_reference.read_text(encoding="utf-8-sig"))
        source = payload.get("source")
        if isinstance(source, dict):
            metadata_hash = sha256_file(instruction_metadata)
            source["metadata_file_sha256"] = metadata_hash
            source["packaged_metadata_sha256"] = metadata_hash
            rewrite_json(instruction_reference, payload)

    register_reference = rules / "register-reference.json"
    register_policy = rules / "register-alias-policy.json"
    if register_reference.is_file() and register_policy.is_file():
        payload = json.loads(register_policy.read_text(encoding="utf-8-sig"))
        source = payload.get("source")
        if isinstance(source, dict):
            reference_hash = sha256_file(register_reference)
            source["register_metadata_file_sha256"] = reference_hash
            source["packaged_reference_sha256"] = reference_hash
            rewrite_json(register_policy, payload)

    for path in destination.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        sanitized = text
        for original, replacement in DOCUMENTATION_PATH_REPLACEMENTS.items():
            sanitized = sanitized.replace(original, replacement)
        if sanitized != text:
            path.write_text(sanitized, encoding="utf-8")

    for path in destination.rglob("*"):
        if not path.is_file():
            continue
        text = read_utf8_text(path)
        if text is None:
            continue
        if find_nonportable_absolute_path(text):
            relative = path.relative_to(destination).as_posix()
            raise InstallError(
                "PORTABILITY_VIOLATION",
                f"Portable copy contains a machine-local absolute path: {relative}",
            )


def install(source: Path, destination: Path, mode: str, force: bool) -> None:
    source = source.resolve()
    destination = Path(os.path.abspath(os.fspath(destination)))
    if source == destination:
        raise InstallError("SAME_PATH", "Source and destination are the same directory")
    if destination.exists() or destination.is_symlink():
        if not force:
            raise InstallError("TARGET_EXISTS", f"Destination already exists: {destination}")
        remove_existing(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if mode == "copy":
        try:
            shutil.copytree(source, destination, ignore=ignore_names)
            sanitize_portable_copy(destination)
        except Exception:
            remove_existing(destination)
            raise
    elif mode == "symlink":
        os.symlink(source, destination, target_is_directory=True)
    else:
        raise InstallError("INVALID_MODE", f"Unknown install mode: {mode}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        required=True,
        choices=("codex-user", "codex-project", "claude-user", "claude-project"),
    )
    parser.add_argument("--project-dir", type=Path)
    parser.add_argument(
        "--mode",
        choices=("copy", "symlink"),
        default="copy",
        help="copy creates a portable release; symlink exposes the full checkout for development",
    )
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        source = skill_root()
        destination = destination_for(args.target, args.project_dir)
        install(source, destination, args.mode, args.force)
    except InstallError as exc:
        emit({"status": "error", "code": exc.code, "message": exc.message})
        return 2
    except OSError as exc:
        emit({"status": "error", "code": "INSTALL_FAILED", "message": str(exc)})
        return 2
    emit(
        {
            "status": "ok",
            "code": "INSTALLED",
            "skill": SKILL_NAME,
            "target": args.target,
            "mode": args.mode,
            "destination": str(destination),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
