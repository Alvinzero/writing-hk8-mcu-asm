#!/usr/bin/env python3
"""Install the HK8 ASM skill into Codex or Claude Code skill directories."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


SKILL_NAME = "writing-hk8-mcu-asm"
EXCLUDED_DIRS = {".git", ".pytest_cache", "__pycache__", "evals", "tests"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


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


def ignore_names(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in EXCLUDED_DIRS or Path(name).suffix in EXCLUDED_SUFFIXES:
            ignored.add(name)
    return ignored


def remove_existing(destination: Path) -> None:
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)


def install(source: Path, destination: Path, mode: str, force: bool) -> None:
    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise InstallError("SAME_PATH", "Source and destination are the same directory")
    if destination.exists() or destination.is_symlink():
        if not force:
            raise InstallError("TARGET_EXISTS", f"Destination already exists: {destination}")
        remove_existing(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if mode == "copy":
        shutil.copytree(source, destination, ignore=ignore_names)
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
    parser.add_argument("--mode", choices=("copy", "symlink"), default="copy")
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
