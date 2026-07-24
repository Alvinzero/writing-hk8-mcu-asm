#!/usr/bin/env python3
"""Validate the portable structure of the HK8 ASM skill without third-party packages."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")
REQUIRED_SPEC_FILES = (
    "AGENTS.md",
    "README.md",
    "09-AI智能体生成与审查协议.md",
    "07-构建-烧录-验收规范.md",
    "rules/asm-rules.json",
    "rules/instruction-reference.json",
    "rules/register-reference.json",
    "rules/register-alias-policy.json",
    "tools/asm_static_check.py",
    "tools/validate_spec.py",
)


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def parse_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter")
    try:
        end = lines[1:].index("---") + 1
    except ValueError as exc:
        raise ValueError("SKILL.md frontmatter must end with ---") from exc
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError(f"Invalid frontmatter line: {line}")
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"').strip("'")
    body = "\n".join(lines[end + 1 :])
    return fields, body


def validate(root: Path) -> list[str]:
    findings: list[str] = []
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        return ["SKILL.md is missing"]
    try:
        fields, body = parse_frontmatter(skill_md)
    except (OSError, UnicodeError, ValueError) as exc:
        return [str(exc)]
    allowed_fields = {"name", "description"}
    extra_fields = set(fields) - allowed_fields
    if extra_fields:
        findings.append(f"Unexpected frontmatter fields: {sorted(extra_fields)}")
    name = fields.get("name", "")
    description = fields.get("description", "")
    if not NAME_RE.match(name):
        findings.append("name must be lowercase letters, digits, and hyphens")
    if not description:
        findings.append("description is required")
    if "TODO" in description or "[TODO" in description:
        findings.append("description still contains TODO text")
    if not description.startswith(("Use when", "用于", "当")):
        findings.append('description must start with "Use when", "用于", or "当"')
    if "TODO" in body or "[TODO" in body:
        findings.append("SKILL.md body still contains TODO text")
    for relative in (
        "agents/openai.yaml",
        "scripts/hk8asm.py",
        "scripts/builtin_compiler.py",
        "scripts/compiler_adapter.py",
        "scripts/ssd1306_page_bitmap.py",
        "scripts/install.py",
        "scripts/validate_skill.py",
        "references/spec",
    ):
        if not (root / relative).exists():
            findings.append(f"Required resource missing: {relative}")
    spec_root = root / "references" / "spec"
    if spec_root.exists():
        for relative in REQUIRED_SPEC_FILES:
            if not (spec_root / relative).is_file():
                findings.append(f"Spec resource missing: references/spec/{relative}")
    return findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.skill_root.resolve()
    findings = validate(root)
    if findings:
        emit({"status": "error", "code": "SKILL_INVALID", "findings": findings})
        return 2
    emit({"status": "ok", "code": "SKILL_VALID", "skill_root": str(root)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
