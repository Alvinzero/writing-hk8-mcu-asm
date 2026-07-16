from __future__ import annotations

from typing import Any


def make_issue(
    rule_id: str,
    severity: str,
    file: str,
    line: int | None,
    evidence: str,
    risk: str,
    required_fix: str,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "file": file,
        "line": line,
        "evidence": evidence,
        "risk": risk,
        "required_fix": required_fix,
    }


def audit_unused_equ(file_model: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for symbol in file_model.get("_equ_symbols", {}).values():
        if symbol["uses"] == 0:
            issues.append(
                make_issue(
                    "HK-SYN-013",
                    "WARNING",
                    file_model["path"],
                    symbol["line"],
                    f"EQU {symbol['name']} defined but never referenced",
                    "declared constant is not source of truth and can drift",
                    "Use symbol or remove definition",
                )
            )
    return issues
