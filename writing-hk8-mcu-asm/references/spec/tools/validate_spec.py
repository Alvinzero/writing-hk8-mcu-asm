#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate the HK64S825 ASM corporate specification package."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import unquote

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


EXPECTED_FILES = [
    "README.md",
    "AGENTS.md",
    "00-规范适用范围与证据等级.md",
    "01-HK64S825-ASM编码规范.md",
    "02-指令与操作数规范.md",
    "03-寄存器与内存使用规范.md",
    "04-程序布局-ORG-查表规范.md",
    "05-GPIO-I2C-OLED驱动规范.md",
    "06-数码管动态扫描规范.md",
    "07-构建-烧录-验收规范.md",
    "08-踩坑案例与症状诊断手册.md",
    "09-AI智能体生成与审查协议.md",
    "10-证据索引与待确认事项.md",
    "rules/asm-rules.json",
    "rules/asm-rules.schema.json",
    "rules/instruction-metadata.json",
    "rules/instruction-reference.json",
    "rules/register-alias-policy.json",
    "rules/register-reference.json",
    "analysis/project-inventory.json",
    "analysis/asm-inventory.csv",
    "analysis/probe-results.json",
    "analysis/evidence-matrix.json",
    "analysis/source-manifest.json",
    "analysis/template-validation.json",
    "checklists/pre-generation.md",
    "checklists/pre-build.md",
    "checklists/pre-flash.md",
    "checklists/hardware-acceptance.md",
    "templates/README.md",
    "templates/minimal-main.asm",
    "templates/gpio-driver.asm",
    "templates/i2c-bitbang.asm",
    "templates/ssd1306-table-paged.asm",
    "templates/seven-segment-scan.asm",
    "templates/hkproj.example",
    "templates/board-profile.example.json",
    "templates/ai-task-request.example.json",
    "templates/ai-review-output.example.json",
    "tools/asm_static_check.py",
    "tools/build_analysis_snapshot.py",
    "tools/validate_spec.py",
    "tools/README.md",
    "tools/tests/test_asm_static_check.py",
    "tools/tests/test_build_analysis_snapshot_cli.py",
    "tools/tests/test_validate_spec.py",
]

TEXT_SUFFIXES = {".md", ".json", ".csv", ".py", ".asm", ".example"}
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
PLACEHOLDER_RE = re.compile(r"\b(?:TODO|TBD)\b", re.IGNORECASE)
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
CHECKER_RULE_RE = re.compile(r'make_finding\(\s*["\'](HK-[A-Z0-9-]+)["\']')
AUTOMATED_RULE_TESTS = {
    "HK-WDT-001": "test_delay_loop_without_clrwdt_is_blocked",
    "HK-GPIO-INIT-001": (
        "test_simple_led_bulk_gpio_initialization_warns_under_strict_warnings"
    ),
    "HK-GPIO-002": "test_problem_led_source_is_rejected_by_semantic_gates",
    "HK-SYN-012": (
        "test_decsz_backward_counter_loop_is_blocked_and_clrwdt_masking_is_reported"
    ),
    "HK-SYN-013": "test_unused_business_equ_warns_and_strict_mode_fails",
    "HK-CLOCK-001": "test_reset_0x34_derives_2mhz_from_16mhz_osc",
    "HK-TIME-001": "test_original_three_level_counts_are_about_4_seconds_at_2mhz",
    "HK-WDT-002": (
        "test_decsz_backward_counter_loop_is_blocked_and_clrwdt_masking_is_reported"
    ),
}


def add_finding(
    findings: list[dict[str, Any]],
    code: str,
    path: Path | str,
    message: str,
    *,
    line: int | None = None,
) -> None:
    findings.append(
        {
            "severity": "ERROR",
            "code": code,
            "path": str(path),
            "line": line,
            "message": message,
        }
    )


def load_json(path: Path, findings: list[dict[str, Any]]) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig", errors="strict"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        add_finding(findings, "invalid-json", path, str(exc))
        return None


def check_required_files(root: Path, findings: list[dict[str, Any]]) -> None:
    for relative in EXPECTED_FILES:
        path = root / relative
        if not path.is_file():
            add_finding(findings, "missing-file", path, f"required package file is missing: {relative}")


def check_utf8(root: Path, findings: list[dict[str, Any]]) -> int:
    paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in TEXT_SUFFIXES
        and "__pycache__" not in path.parts
    )
    for path in paths:
        try:
            path.read_text(encoding="utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            add_finding(findings, "invalid-utf8", path, str(exc))
    return len(paths)


def check_all_json(
    root: Path, findings: list[dict[str, Any]]
) -> tuple[int, dict[Path, Any]]:
    paths = sorted(root.rglob("*.json"))
    hkproj = root / "templates" / "hkproj.example"
    if hkproj.is_file():
        paths.append(hkproj)
    loaded: dict[Path, Any] = {}
    for path in paths:
        value = load_json(path, findings)
        if value is not None:
            loaded[path.resolve()] = value
    return len(paths), loaded


def collect_valid_rule_ids(rules: Any, schema: dict[str, Any]) -> list[str]:
    rule_id_schema = (
        schema.get("$defs", {}).get("rule", {}).get("properties", {}).get("rule_id", {})
    )
    pattern = rule_id_schema.get("pattern")
    if not isinstance(rules, list):
        return []
    return [
        value
        for rule in rules
        if isinstance(rule, dict)
        and isinstance((value := rule.get("rule_id")), str)
        and (not isinstance(pattern, str) or re.fullmatch(pattern, value) is not None)
    ]


def fallback_rule_schema_check(
    document: dict[str, Any],
    schema: dict[str, Any],
    path: Path,
    findings: list[dict[str, Any]],
) -> None:
    root_required = set(schema.get("required", []))
    root_properties = set(schema.get("properties", {}))
    missing_root = root_required - set(document)
    extra_root = set(document) - root_properties
    if missing_root:
        add_finding(findings, "rule-schema", path, f"missing root fields: {sorted(missing_root)}")
    if extra_root:
        add_finding(findings, "rule-schema", path, f"unexpected root fields: {sorted(extra_root)}")
    if document.get("rule_set_id") != "HK64S825-ASM-CORPORATE":
        add_finding(findings, "rule-schema", path, "invalid rule_set_id")
    for key in ("schema_version", "rule_set_version"):
        value = document.get(key)
        if not isinstance(value, str) or not SEMVER_RE.fullmatch(value):
            add_finding(findings, "rule-schema", path, f"{key} must be x.y.z")

    rule_schema = schema.get("$defs", {}).get("rule", {})
    rule_required = set(rule_schema.get("required", []))
    rule_properties = rule_schema.get("properties", {})
    evidence_schema = schema.get("$defs", {}).get("evidence", {})
    evidence_required = set(evidence_schema.get("required", []))
    evidence_properties = evidence_schema.get("properties", {})
    rules = document.get("rules")
    if not isinstance(rules, list) or not rules:
        add_finding(findings, "rule-schema", path, "rules must be a non-empty array")
        return

    for index, rule in enumerate(rules):
        where = f"rules[{index}]"
        if not isinstance(rule, dict):
            add_finding(findings, "rule-schema", path, f"{where} must be an object")
            continue
        missing = rule_required - set(rule)
        extras = set(rule) - set(rule_properties)
        if missing:
            add_finding(findings, "rule-schema", path, f"{where} missing fields: {sorted(missing)}")
        if extras:
            add_finding(findings, "rule-schema", path, f"{where} unexpected fields: {sorted(extras)}")
        if "rule_id" in rule:
            rule_id = rule["rule_id"]
            rule_id_schema = rule_properties.get("rule_id", {})
            pattern = rule_id_schema.get("pattern")
            if not isinstance(rule_id, str):
                add_finding(findings, "rule-schema", path, f"{where}.rule_id must be a string")
            elif isinstance(pattern, str) and re.fullmatch(pattern, rule_id) is None:
                add_finding(
                    findings,
                    "rule-schema",
                    path,
                    f"{where}.rule_id does not match schema pattern",
                )
        for field in ("normative_level", "severity", "status", "confidence"):
            allowed = rule_properties.get(field, {}).get("enum", [])
            if allowed and rule.get(field) not in allowed:
                add_finding(findings, "rule-schema", path, f"{where}.{field} has invalid value")
        for field in ("scope", "verification", "evidence", "toolchain_applicability", "tags"):
            if field in rule and not isinstance(rule[field], list):
                add_finding(findings, "rule-schema", path, f"{where}.{field} must be an array")
        toolchain_schema = rule_properties.get("toolchain_applicability", {})
        toolchains = rule.get("toolchain_applicability")
        if isinstance(toolchains, list):
            minimum = toolchain_schema.get("minItems", 0)
            if isinstance(minimum, int) and len(toolchains) < minimum:
                add_finding(
                    findings,
                    "rule-schema",
                    path,
                    f"{where}.toolchain_applicability must contain at least {minimum} item(s)",
                )
            if toolchain_schema.get("uniqueItems") and any(
                item in toolchains[:index] for index, item in enumerate(toolchains)
            ):
                add_finding(
                    findings,
                    "rule-schema",
                    path,
                    f"{where}.toolchain_applicability must contain unique items",
                )
            allowed_toolchains = toolchain_schema.get("items", {}).get("enum", [])
            for item in toolchains:
                if allowed_toolchains and item not in allowed_toolchains:
                    add_finding(
                        findings,
                        "rule-schema",
                        path,
                        f"{where}.toolchain_applicability has invalid value: {item!r}",
                    )
        evidence = rule.get("evidence", [])
        if isinstance(evidence, list):
            if not evidence:
                add_finding(findings, "rule-schema", path, f"{where}.evidence cannot be empty")
            for evidence_index, item in enumerate(evidence):
                evidence_where = f"{where}.evidence[{evidence_index}]"
                if not isinstance(item, dict):
                    add_finding(findings, "rule-schema", path, f"{evidence_where} must be an object")
                    continue
                if evidence_required - set(item):
                    add_finding(findings, "rule-schema", path, f"{evidence_where} is incomplete")
                if set(item) - set(evidence_properties):
                    add_finding(findings, "rule-schema", path, f"{evidence_where} has extra fields")
                levels = evidence_properties.get("level", {}).get("enum", [])
                if item.get("level") not in levels:
                    add_finding(findings, "rule-schema", path, f"{evidence_where}.level is invalid")
                for field in ("source", "note"):
                    if not isinstance(item.get(field), str) or not item[field].strip():
                        add_finding(findings, "rule-schema", path, f"{evidence_where}.{field} is empty")


def check_rules(
    root: Path,
    loaded: dict[Path, Any],
    findings: list[dict[str, Any]],
    checks: dict[str, Any],
) -> None:
    rules_path = (root / "rules" / "asm-rules.json").resolve()
    schema_path = (root / "rules" / "asm-rules.schema.json").resolve()
    document = loaded.get(rules_path)
    schema = loaded.get(schema_path)
    if not isinstance(document, dict) or not isinstance(schema, dict):
        return

    checks["rule_schema_engine"] = "fallback"
    try:
        import jsonschema  # type: ignore

        jsonschema.validate(document, schema)
        checks["rule_schema_engine"] = "jsonschema"
    except ImportError:
        fallback_rule_schema_check(document, schema, rules_path, findings)
    except Exception as exc:
        add_finding(findings, "rule-schema", rules_path, str(exc))

    rules = document.get("rules", [])
    checks["rule_count"] = len(rules) if isinstance(rules, list) else 0
    if checks["rule_count"] != 78:
        add_finding(findings, "rule-count", rules_path, f"expected 78 rules, found {checks['rule_count']}")
    ids = collect_valid_rule_ids(rules, schema)
    duplicate_ids = sorted(
        value for value, count in Counter(ids).items() if count > 1
    )
    if duplicate_ids:
        add_finding(findings, "duplicate-rule-id", rules_path, f"duplicate rule IDs: {duplicate_ids}")
    db_rule = next(
        (rule for rule in rules if isinstance(rule, dict) and rule.get("rule_id") == "HK-TOOLCHAIN-DB-001"),
        None,
    )
    if not db_rule or db_rule.get("severity") != "BLOCKER":
        add_finding(findings, "missing-db-blocker", rules_path, "HK-TOOLCHAIN-DB-001 must be BLOCKER")


def check_checker_rule_ids(
    root: Path,
    rules: list[dict[str, Any]],
    schema: dict[str, Any],
    checks: dict[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    registered = set(collect_valid_rule_ids(rules, schema))
    emitted: set[str] = set()
    for relative in ("tools/asm_static_check.py", "tools/asm_semantic_gates.py"):
        path = root / relative
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            emitted.update(CHECKER_RULE_RE.findall(text))
    unknown = sorted(emitted - registered)
    checks["checker_rule_ids"] = sorted(emitted)
    checks["checker_unknown_rule_ids"] = unknown
    for rule_id in unknown:
        add_finding(
            findings,
            "checker-rule-id",
            root / "tools",
            f"unregistered finding ID: {rule_id}",
        )


def check_automated_rule_tests(
    root: Path,
    rules: list[dict[str, Any]],
    schema: dict[str, Any],
    checks: dict[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    registered = set(collect_valid_rule_ids(rules, schema))
    test_methods: set[str] = set()
    test_paths = sorted((root / "tools" / "tests").glob("*.py"))
    for path in test_paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            add_finding(
                findings,
                "checker-rule-test",
                path,
                f"cannot inspect automated test definitions: {exc}",
            )
            continue
        unittest_module_imported = any(
            isinstance(node, ast.Import)
            and any(alias.name == "unittest" and alias.asname is None for alias in node.names)
            for node in tree.body
        )
        testcase_directly_imported = any(
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module == "unittest"
            and any(alias.name == "TestCase" and alias.asname is None for alias in node.names)
            for node in tree.body
        )
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            is_testcase = any(
                (
                    unittest_module_imported
                    and isinstance(base, ast.Attribute)
                    and isinstance(base.value, ast.Name)
                    and base.value.id == "unittest"
                    and base.attr == "TestCase"
                )
                or (
                    testcase_directly_imported
                    and isinstance(base, ast.Name)
                    and base.id == "TestCase"
                )
                for base in node.bases
            )
            if not is_testcase or node.decorator_list:
                continue
            for member in node.body:
                if (
                    not isinstance(member, ast.FunctionDef)
                    or not member.name.startswith("test_")
                    or member.decorator_list
                ):
                    continue
                statements = member.body
                if (
                    statements
                    and isinstance(statements[0], ast.Expr)
                    and isinstance(statements[0].value, ast.Constant)
                    and isinstance(statements[0].value.value, str)
                ):
                    statements = statements[1:]
                is_empty = not statements or all(
                    isinstance(statement, ast.Pass)
                    or (
                        isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                        and statement.value.value is Ellipsis
                    )
                    for statement in statements
                )
                if not is_empty:
                    test_methods.add(member.name)

    checks["automated_rule_tests"] = dict(AUTOMATED_RULE_TESTS)
    checks["automated_rule_test_files"] = [
        str(path.relative_to(root)).replace("\\", "/") for path in test_paths
    ]
    for rule_id, test_name in AUTOMATED_RULE_TESTS.items():
        if rule_id not in registered:
            add_finding(
                findings,
                "checker-rule-test",
                root / "rules" / "asm-rules.json",
                f"automated checker rule is not registered: {rule_id}",
            )
        if test_name not in test_methods:
            add_finding(
                findings,
                "checker-rule-test",
                root / "tools" / "tests",
                f"{rule_id} requires exact test method def {test_name}(",
            )


def check_instruction_reference(
    root: Path,
    loaded: dict[Path, Any],
    findings: list[dict[str, Any]],
    checks: dict[str, Any],
) -> None:
    path = (root / "rules" / "instruction-reference.json").resolve()
    document = loaded.get(path)
    if not isinstance(document, dict):
        return
    variants = document.get("variants")
    if not isinstance(variants, list):
        add_finding(findings, "instruction-reference", path, "variants must be an array")
        return
    checks["instruction_variant_count"] = len(variants)
    if len(variants) != 65 or document.get("variant_count") != 65:
        add_finding(findings, "instruction-count", path, "instruction variant count must be 65")
    ids = [item.get("id") for item in variants if isinstance(item, dict)]
    duplicates = sorted(
        value for value, count in Counter(ids).items() if value is not None and count > 1
    )
    if duplicates:
        add_finding(findings, "duplicate-instruction-id", path, f"duplicate IDs: {duplicates}")
    failed: list[str] = []
    for item in variants:
        if not isinstance(item, dict):
            failed.append("<non-object>")
            continue
        probe = item.get("compile_probe", {})
        if (
            probe.get("status") != "passed"
            or str(probe.get("machine_word", "")).lower()
            != str(probe.get("expected_word", "")).lower()
        ):
            failed.append(str(item.get("id")))
    checks["instruction_compile_probes_passed"] = len(variants) - len(failed)
    if failed:
        add_finding(findings, "instruction-probe", path, f"failed/mismatched probes: {failed}")


def normalized_instruction_syntax(raw: str) -> str:
    return (
        raw.replace("XOR A.#K", "XOR A,#K")
        .replace("BTSZ,R,b", "BTSZ R,b")
        .replace("BTSNZ,R,b", "BTSNZ R,b")
    )


def check_metadata_references(
    root: Path,
    loaded: dict[Path, Any],
    findings: list[dict[str, Any]],
    checks: dict[str, Any],
) -> None:
    instruction_path = (root / "rules" / "instruction-metadata.json").resolve()
    reference_path = (root / "rules" / "instruction-reference.json").resolve()
    register_path = (root / "rules" / "register-reference.json").resolve()
    policy_path = (root / "rules" / "register-alias-policy.json").resolve()
    instruction_document = loaded.get(instruction_path)
    reference_document = loaded.get(reference_path)
    register_document = loaded.get(register_path)
    policy_document = loaded.get(policy_path)

    if isinstance(instruction_document, dict):
        instructions = instruction_document.get("instructions")
        sheet_rows = (instruction_document.get("sheets") or [{}])[0].get("records", [])
        if not isinstance(instructions, list):
            add_finding(findings, "instruction-metadata", instruction_path, "instructions must be an array")
            instructions = []
        checks["instruction_metadata_count"] = len(instructions)
        checks["instruction_metadata_sheet_rows"] = len(sheet_rows) if isinstance(sheet_rows, list) else 0
        if len(instructions) != 65 or checks["instruction_metadata_sheet_rows"] != 65:
            add_finding(findings, "instruction-metadata", instruction_path, "expected 65 aggregate instructions and 65 sheet rows")
        by_syntax = {item.get("asm_syntax") for item in instructions if isinstance(item, dict)}
        if "BTSZ R,b" not in by_syntax or "BTSNZ R,b" not in by_syntax:
            add_finding(findings, "instruction-metadata", instruction_path, "2026-07 BTSZ/BTSNZ syntax corrections are missing")
        if "BTSZ,R,b" in by_syntax or "BTSNZ,R,b" in by_syntax:
            add_finding(findings, "instruction-metadata", instruction_path, "historical BTSZ/BTSNZ comma syntax reappeared")

        variants = reference_document.get("variants", []) if isinstance(reference_document, dict) else []
        if len(variants) == len(instructions):
            for index, (metadata, variant) in enumerate(zip(instructions, variants), 1):
                if not isinstance(metadata, dict) or not isinstance(variant, dict):
                    continue
                expected_operand = "k8" if metadata.get("asm_syntax") == "MOV A,#K" else metadata.get("operands")
                if (
                    variant.get("raw_asm_syntax") != metadata.get("asm_syntax")
                    or variant.get("asm_syntax") != normalized_instruction_syntax(str(metadata.get("asm_syntax", "")))
                    or variant.get("raw_operand_type") != metadata.get("operands")
                    or variant.get("operand_type") != expected_operand
                ):
                    add_finding(findings, "instruction-metadata", reference_path, f"variant {index} is out of sync with instruction-metadata.json")
        if isinstance(reference_document, dict):
            reference_source = reference_document.get("source", {})
            expected_hash = reference_source.get("packaged_metadata_sha256")
            if expected_hash != sha256(instruction_path):
                add_finding(findings, "instruction-metadata", instruction_path, "packaged instruction metadata hash is stale")
            exact_snapshot = reference_source.get("metadata_file_sha256") == sha256(instruction_path)
            checks["instruction_metadata_exact_snapshot"] = exact_snapshot
            if not exact_snapshot:
                add_finding(findings, "instruction-metadata", instruction_path, "packaged instruction metadata is not an exact input JSON snapshot")

    if isinstance(register_document, dict):
        registers = register_document.get("registers")
        sheet_rows = (register_document.get("sheets") or [{}])[0].get("records", [])
        if not isinstance(registers, list):
            add_finding(findings, "register-reference", register_path, "registers must be an array")
            registers = []
        checks["register_reference_count"] = len(registers)
        checks["register_sheet_row_count"] = len(sheet_rows) if isinstance(sheet_rows, list) else 0
        if len(registers) != 96 or checks["register_sheet_row_count"] != 407:
            add_finding(findings, "register-reference", register_path, "expected 96 aggregate registers and 407 sheet rows")
        internal_names = {"PA_PU", "PA_PD", "PA_OD", "PA_OE", "PB_PU", "PB_PD", "PB_OD", "PB_OE"}
        names = {
            str(item.get("name", "")).upper()
            for item in list(registers) + (list(sheet_rows) if isinstance(sheet_rows, list) else [])
            if isinstance(item, dict)
        }
        invalid_names = sorted(names & internal_names)
        if invalid_names:
            add_finding(findings, "register-metadata-name", register_path, f"compiler-internal GPIO names found: {invalid_names}")
        required_names = {"PA_PPU", "PA_PPD", "PA_POD", "PA_POE", "PB_PPU", "PB_PPD", "PB_POD", "PB_POE"}
        if not required_names.issubset(names):
            add_finding(findings, "register-metadata-name", register_path, "official GPIO register names are incomplete")

        if isinstance(policy_document, dict):
            source = policy_document.get("source", {})
            if source.get("packaged_reference_sha256") != sha256(register_path):
                add_finding(findings, "register-reference", register_path, "packaged register reference hash is stale")
            exact_snapshot = source.get("register_metadata_file_sha256") == sha256(register_path)
            checks["register_metadata_exact_snapshot"] = exact_snapshot
            if not exact_snapshot:
                add_finding(findings, "register-reference", register_path, "packaged register reference is not an exact input JSON snapshot")
            conflicts = policy_document.get("metadata_conflicts", [])
            signatures = {
                (
                    item.get("type"),
                    item.get("space"),
                    item.get("address"),
                    item.get("reg825_inc_name"),
                    item.get("register_metadata_name"),
                    item.get("status"),
                )
                for item in conflicts
                if isinstance(item, dict)
            }
            expected_conflicts = {
                ("inc_only", "SFR", "0x04", "STATUS", None, "OPEN"),
                ("name_mismatch", "SFR", "0x24", "LVD", "LVD1", "OPEN"),
                ("metadata_only", "SFR", "0x26", None, "LVD2", "OPEN"),
                ("metadata_only", "SFR", "0x27", None, "LVD3", "OPEN"),
            }
            if signatures != expected_conflicts:
                add_finding(findings, "register-reference", policy_path, "REG825.INC/register metadata OPEN conflicts changed without review")


def extract_link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        return target[1 : target.index(">")]
    title_match = re.match(r"^(\S+)(?:\s+[\"'].*[\"'])?$", target)
    return title_match.group(1) if title_match else target


def check_markdown_links(root: Path, findings: list[dict[str, Any]]) -> int:
    checked = 0
    for path in sorted(root.rglob("*.md")):
        text = path.read_text(encoding="utf-8-sig", errors="strict")
        for match in MARKDOWN_LINK_RE.finditer(text):
            target = extract_link_target(match.group(1))
            lowered = target.lower()
            if (
                not target
                or target.startswith("#")
                or lowered.startswith(("http://", "https://", "mailto:", "data:"))
                or re.match(r"^[A-Za-z]:[\\/]", target)
            ):
                continue
            relative = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not relative:
                continue
            checked += 1
            if not (path.parent / relative).resolve().exists():
                line = text.count("\n", 0, match.start()) + 1
                add_finding(findings, "broken-link", path, f"link target does not exist: {target}", line=line)
    return checked


def check_placeholders(root: Path, findings: list[dict[str, Any]]) -> int:
    paths = sorted(root.rglob("*.md")) + sorted(root.rglob("*.asm"))
    for path in paths:
        text = path.read_text(encoding="utf-8-sig", errors="strict")
        for line_number, line in enumerate(text.splitlines(), 1):
            if PLACEHOLDER_RE.search(line):
                add_finding(
                    findings,
                    "scattered-placeholder",
                    path,
                    "replace TODO/TBD with an explicit OPEN/UNRESOLVED item",
                    line=line_number,
                )
    return len(paths)


def run_static_checker(
    checker: Path, asm: Path, toolchain: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None]:
    completed = subprocess.run(
        [sys.executable, str(checker), str(asm), "--toolchain", toolchain, "--json"],
        text=True,
        encoding="utf-8",
        capture_output=True,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = None
    return completed, payload


def check_templates(
    root: Path, findings: list[dict[str, Any]], checks: dict[str, Any]
) -> None:
    checker = root / "tools" / "asm_static_check.py"
    if not checker.is_file():
        return
    results: dict[str, Any] = {}
    for name in (
        "minimal-main.asm",
        "gpio-driver.asm",
        "i2c-bitbang.asm",
        "seven-segment-scan.asm",
    ):
        asm = root / "templates" / name
        if not asm.is_file():
            continue
        completed, payload = run_static_checker(checker, asm, "company_ide")
        results[name] = {"company_ide_exit": completed.returncode}
        if completed.returncode != 0 or payload is None:
            add_finding(findings, "template-static-check", asm, f"static check exit={completed.returncode}")

    table = root / "templates" / "ssd1306-table-paged.asm"
    if table.is_file():
        company, company_payload = run_static_checker(checker, table, "company_ide")
        cli, cli_payload = run_static_checker(checker, table, "python_source_module_cli")
        results[table.name] = {
            "company_ide_exit": company.returncode,
            "python_source_module_cli_exit": cli.returncode,
        }
        if company.returncode != 0 or company_payload is None:
            add_finding(findings, "template-static-check", table, "company_ide path must have no ERROR/BLOCKER")
        cli_rule_ids = {
            item.get("rule_id")
            for item in (cli_payload or {}).get("findings", [])
            if isinstance(item, dict)
        }
        if cli.returncode != 2 or "HK-TOOLCHAIN-DB-001" not in cli_rule_ids:
            add_finding(findings, "template-db-blocker", table, "Python CLI path must trigger DB BLOCKER")
    checks["template_static_checks"] = results


def check_examples(
    root: Path, loaded: dict[Path, Any], findings: list[dict[str, Any]]
) -> None:
    board_path = (root / "templates" / "board-profile.example.json").resolve()
    board = loaded.get(board_path)
    if isinstance(board, dict):
        if board.get("ready_for_flash") is not False:
            add_finding(findings, "unsafe-board-example", board_path, "ready_for_flash must be false")
        if "UNRESOLVED" not in json.dumps(board, ensure_ascii=False):
            add_finding(findings, "unsafe-board-example", board_path, "explicit UNRESOLVED fields are required")
    review_path = (root / "templates" / "ai-review-output.example.json").resolve()
    review = loaded.get(review_path)
    if isinstance(review, dict) and review.get("status") != "draft":
        add_finding(findings, "unsafe-ai-example", review_path, "example status must be draft")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_template_validation(
    root: Path,
    loaded: dict[Path, Any],
    findings: list[dict[str, Any]],
    checks: dict[str, Any],
) -> None:
    evidence_path = (root / "analysis" / "template-validation.json").resolve()
    document = loaded.get(evidence_path)
    if not isinstance(document, dict):
        return
    records = document.get("no_db_templates")
    if not isinstance(records, list) or len(records) != 4:
        add_finding(
            findings,
            "template-validation",
            evidence_path,
            "no_db_templates must contain exactly four validated templates",
        )
        return
    checks["validated_no_db_templates"] = len(records)
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("template"), str):
            add_finding(findings, "template-validation", evidence_path, "invalid template validation record")
            continue
        template = root / record["template"]
        if not template.is_file():
            add_finding(findings, "template-validation-stale", template, "validated template is missing")
            continue
        actual_hash = sha256(template)
        actual_size = template.stat().st_size
        if record.get("source_sha256") != actual_hash or record.get("source_bytes") != actual_size:
            add_finding(
                findings,
                "template-validation-stale",
                template,
                "template source no longer matches analysis/template-validation.json",
            )
        if record.get("errors") != 0 or record.get("warnings") != 0:
            add_finding(
                findings,
                "template-validation",
                evidence_path,
                f"{record['template']} must retain 0 errors and 0 warnings",
            )
        if record.get("bin_bytes") != record.get("code_words", -1) * 2:
            add_finding(
                findings,
                "template-validation",
                evidence_path,
                f"{record['template']} bin_bytes must equal code_words * 2",
            )
    db_template = document.get("db_template", {})
    cli_check = db_template.get("python_source_module_cli_static_check", {})
    if (
        cli_check.get("exit_code") != 2
        or cli_check.get("required_blocker") != "HK-TOOLCHAIN-DB-001"
        or db_template.get("compile_attempted_with_python_source_module_cli") is not False
    ):
        add_finding(
            findings,
            "template-validation",
            evidence_path,
            "DB template evidence must preserve the Python CLI DB blocker and no-compile decision",
        )


def validate(root: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    checks: dict[str, Any] = {}
    if not root.is_dir():
        add_finding(findings, "missing-root", root, "spec root is not a directory")
    else:
        check_required_files(root, findings)
        checks["utf8_text_files"] = check_utf8(root, findings)
        checks["json_files"], loaded = check_all_json(root, findings)
        check_rules(root, loaded, findings, checks)
        rules_document = loaded.get((root / "rules" / "asm-rules.json").resolve())
        rules_schema = loaded.get((root / "rules" / "asm-rules.schema.json").resolve())
        rules = rules_document.get("rules", []) if isinstance(rules_document, dict) else []
        check_checker_rule_ids(
            root,
            rules if isinstance(rules, list) else [],
            rules_schema if isinstance(rules_schema, dict) else {},
            checks,
            findings,
        )
        check_automated_rule_tests(
            root,
            rules if isinstance(rules, list) else [],
            rules_schema if isinstance(rules_schema, dict) else {},
            checks,
            findings,
        )
        check_instruction_reference(root, loaded, findings, checks)
        check_metadata_references(root, loaded, findings, checks)
        checks["relative_markdown_links_checked"] = check_markdown_links(root, findings)
        checks["placeholder_files_checked"] = check_placeholders(root, findings)
        check_examples(root, loaded, findings)
        check_template_validation(root, loaded, findings, checks)
        check_templates(root, findings, checks)

    errors = sum(1 for item in findings if item["severity"] == "ERROR")
    return {
        "schema_version": "1.0.0",
        "root": str(root.resolve()),
        "checks": checks,
        "findings": findings,
        "summary": {"errors": errors, "warnings": 0, "exit_code": 2 if errors else 0},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an HK64S825 ASM corporate specification package."
    )
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd(), help="spec package root")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def render_text(payload: dict[str, Any]) -> str:
    lines = [f"HK64S825 spec validation: {payload['root']}"]
    for finding in payload["findings"]:
        location = finding["path"]
        if finding["line"] is not None:
            location += f":{finding['line']}"
        lines.append(f"[ERROR] {finding['code']} {location} - {finding['message']}")
    lines.append(
        f"summary: {payload['summary']['errors']} error(s); exit={payload['summary']['exit_code']}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = validate(args.root.expanduser())
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render_text(payload))
    return int(payload["summary"]["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
