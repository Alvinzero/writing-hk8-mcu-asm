#!/usr/bin/env python3
"""Fail-closed orchestration for HK8 ASM static check, compile, and release."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ssd1306_page_bitmap import AssetError as DisplayAssetError
from ssd1306_page_bitmap import build_result as build_display_asset_result


MANDATORY_ROLES = ("compiler",)
OPTIONAL_HARDWARE_ROLES = ("programmer", "verifier")
ROLES = (*MANDATORY_ROLES, *OPTIONAL_HARDWARE_ROLES)
RUN_SCHEMA_VERSION = 1
MAX_FLASH_ATTEMPTS = 3
PLACEHOLDER_MARKERS = ("REPLACE_WITH", "实际路径")
SKILL_ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER_RE = re.compile(r"\b[A-Za-z_.$?][A-Za-z0-9_.$?]*\b")
CHINESE_TEXT_RE = re.compile(r"[\u3400-\u9fff]")
TECHNICAL_FILENAME_RE = re.compile(
    r"^[A-Za-z0-9_.$?-]+\.(?:ASM|BIN|HEX|INC|JSON|MAP)$", re.IGNORECASE
)
GPIO_DEPENDENCY_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:gpio|led|oled|i2c|ssd1306|seven[-_ ]?segment|7[-_ ]?segment|p[ab][0-7])(?![a-z0-9])"
)
GPIO_OUTPUT_DEPENDENCY_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:led|oled|i2c|ssd1306|seven[-_ ]?segment|7[-_ ]?segment)(?![a-z0-9])"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ASM_LABEL_RE = re.compile(r"^[A-Za-z_.$?][A-Za-z0-9_.$?]*$")
DISPLAY_ASSET_SNAPSHOT = Path("assets") / "display-asset.json"
APPROVED_TECHNICAL_TOKENS = {
    "A", "ACK", "ASM", "ASSEMBLER", "BIN", "BUILTIN", "CHIP", "CLOBBERS",
    "COMPILER", "CRC", "GPIO", "HEX", "HK64S825", "HZ", "I2C", "IN", "KHZ",
    "LED", "MCU", "MHZ", "MS", "MV", "NACK", "NS", "OLED", "OSC", "OUT", "PA",
    "PB", "RAM", "REENTRANT", "ROM", "RULE", "RULES", "SCK_PS", "SRAM", "TABLE_PAIR",
    "SSD1306", "TOOLCHAIN", "US", "V", "WDT",
}


def load_bundled_technical_tokens() -> set[str]:
    rules = SKILL_ROOT / "references" / "spec" / "rules"
    tokens: set[str] = set()
    try:
        instructions = json.loads(
            (rules / "instruction-reference.json").read_text(encoding="utf-8-sig")
        )
        for variant in instructions.get("variants", []):
            if (
                isinstance(variant, dict)
                and isinstance(variant.get("mnemonic"), str)
                and bool(variant["mnemonic"].strip())
            ):
                tokens.add(variant["mnemonic"].upper())
        registers = json.loads(
            (rules / "register-reference.json").read_text(encoding="utf-8-sig")
        )
        for register in registers.get("registers", []):
            if not isinstance(register, dict):
                continue
            for key in ("name", "kind"):
                if isinstance(register.get(key), str) and bool(register[key].strip()):
                    tokens.add(register[key].upper())
            for field in register.get("bit_fields", []):
                if (
                    not isinstance(field, dict)
                    or not isinstance(field.get("name"), str)
                    or not field["name"].strip()
                ):
                    continue
                tokens.update(token.upper() for token in IDENTIFIER_RE.findall(field["name"]))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return set()
    return tokens


BUNDLED_TECHNICAL_TOKENS = load_bundled_technical_tokens()


class GateError(Exception):
    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class AdapterError(Exception):
    def __init__(self, role: str, message: str) -> None:
        super().__init__(message)
        self.role = role
        self.message = message


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(code, f"Cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise GateError(code, f"Expected a JSON object in {path}")
    return payload


def read_json_text(text: str, code: str, source: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateError(code, f"Cannot read valid JSON from {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise GateError(code, f"Expected a JSON object from {source}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp_path, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def path_is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise GateError(code, message)


SENSITIVE_DETAIL_KEYS = {
    "evidence",
    "file",
    "files",
    "inputs",
    "labels",
    "line_text",
    "path",
    "snippet",
    "source",
    "source_line",
}


def sanitize_diagnostic_details(details: Any) -> Any:
    if isinstance(details, dict):
        return {
            key: sanitize_diagnostic_details(value)
            for key, value in details.items()
            if key not in SENSITIVE_DETAIL_KEYS
        }
    if isinstance(details, list):
        return [sanitize_diagnostic_details(item) for item in details]
    return details


def is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_scalar(value: Any) -> bool:
    return isinstance(value, str) or is_finite_number(value)


def is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value))
    )


def is_finite_positive_number(value: Any) -> bool:
    return is_finite_number(value) and value > 0


def contains_placeholder(value: str) -> bool:
    stripped = value.strip()
    return any(marker in stripped for marker in PLACEHOLDER_MARKERS) or (
        stripped.startswith("<") and stripped.endswith(">")
    )


def contains_unresolved(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().upper() in {"UNRESOLVED", "TBD", "UNKNOWN"}
    if isinstance(value, dict):
        return any(contains_unresolved(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_unresolved(item) for item in value)
    return False


def is_bundled_technical_comment_token(token: str) -> bool:
    normalized = token.upper()
    return (
        normalized in APPROVED_TECHNICAL_TOKENS
        or normalized in BUNDLED_TECHNICAL_TOKENS
        or re.fullmatch(r"P[AB][0-7]", normalized) is not None
        or TECHNICAL_FILENAME_RE.fullmatch(token) is not None
    )


def is_technical_comment_token(token: str, code_identifiers: set[str]) -> bool:
    return is_bundled_technical_comment_token(token) or token.upper() in code_identifiers


def validate_chinese_explanatory_comments(source: Path) -> dict[str, Any]:
    try:
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise GateError("STATIC_CHECK_FAILED", f"Cannot read candidate source: {exc}") from exc
    code_identifiers: set[str] = set()
    for line in text.splitlines():
        code = line.partition(";")[0]
        code_identifiers.update(token.upper() for token in IDENTIFIER_RE.findall(code))
    checked = 0
    issues: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        _code, marker, comment = line.partition(";")
        if not marker:
            continue
        content = comment.strip()
        if not content:
            continue
        checked += 1
        latin_tokens = [
            token
            for token in IDENTIFIER_RE.findall(content)
            if re.search(r"[A-Za-z]", token)
        ]
        if not all(
            is_technical_comment_token(token, code_identifiers)
            for token in latin_tokens
        ):
            issues.append({"line": line_number, "rule": "chinese_explanatory_comment"})
            continue
        if CHINESE_TEXT_RE.search(content):
            continue
        if all(is_bundled_technical_comment_token(token) for token in latin_tokens):
            continue
        if len(latin_tokens) == 1 and latin_tokens[0].upper() in code_identifiers:
            continue
        issues.append({"line": line_number, "rule": "chinese_explanatory_comment"})
    if issues:
        raise GateError(
            "STATIC_CHECK_FAILED",
            "ASM explanatory comments must use Chinese",
            details=issues,
        )
    return {"status": "pass", "comments_checked": checked}


def validate_clock_contract(request: dict[str, Any], profile: dict[str, Any]) -> None:
    clock = request.get("clock")
    legacy = request.get("clock_hz")
    require(
        clock is not None or legacy is not None,
        "INVALID_REQUEST",
        "clock or clock_hz is required",
    )
    if clock is None:
        require(
            isinstance(legacy, int) and not isinstance(legacy, bool) and legacy > 0,
            "INVALID_REQUEST",
            "clock_hz must be a positive OSC frequency",
        )
        return
    require(isinstance(clock, dict), "INVALID_REQUEST", "clock must be an object")
    osc_hz = clock.get("osc_hz")
    require(
        isinstance(osc_hz, int) and not isinstance(osc_hz, bool) and osc_hz > 0,
        "INVALID_REQUEST",
        "clock.osc_hz must be positive",
    )
    sck_ps = clock.get("sck_ps", "reset")
    require(
        sck_ps == "reset"
        or (
            isinstance(sck_ps, int)
            and not isinstance(sck_ps, bool)
            and 0 <= sck_ps <= 255
        ),
        "INVALID_REQUEST",
        "clock.sck_ps must be reset or an 8-bit integer",
    )
    clock_model = profile.get("clock_model")
    resolved_sck_ps = (
        clock_model.get("sck_ps_reset")
        if sck_ps == "reset" and isinstance(clock_model, dict)
        else sck_ps
    )
    if isinstance(resolved_sck_ps, int) and not isinstance(resolved_sck_ps, bool):
        selector = resolved_sck_ps & 0x0F
        require(
            selector != 0,
            "INVALID_REQUEST",
            "clock.sck_ps selector 0 is prohibited",
        )
        if isinstance(clock_model, dict):
            sckhl_bit = clock_model["sckhl_bit"]
            mode = "high" if resolved_sck_ps & (1 << sckhl_bit) else "low"
            divider_map = clock_model["divider_by_mode"][mode]
            require(
                str(selector) in divider_map,
                "INVALID_REQUEST",
                f"clock.sck_ps selector {selector} is not defined for {mode} mode",
            )


def request_uses_gpio(request: dict[str, Any]) -> bool:
    pins = request.get("pins")
    if isinstance(pins, dict) and any(
        isinstance(pin, dict) and pin.get("port") in {"PA", "PB"}
        for pin in pins.values()
    ):
        return True
    behavior = request.get("behavior")
    if isinstance(behavior, str) and (
        GPIO_DEPENDENCY_RE.search(behavior) or "数码管" in behavior or "引脚" in behavior
    ):
        return True
    peripherals = request.get("peripherals")
    if not isinstance(peripherals, list):
        return False
    for peripheral in peripherals:
        name = peripheral.get("name") if isinstance(peripheral, dict) else peripheral
        if isinstance(name, str) and (
            GPIO_DEPENDENCY_RE.search(name) or "数码管" in name or "引脚" in name
        ):
            return True
    return False


def request_requires_gpio_output(request: dict[str, Any]) -> bool:
    behavior = request.get("behavior")
    if isinstance(behavior, str) and (
        GPIO_OUTPUT_DEPENDENCY_RE.search(behavior) or "数码管" in behavior
    ):
        return True
    peripherals = request.get("peripherals")
    if not isinstance(peripherals, list):
        return False
    for peripheral in peripherals:
        name = peripheral.get("name") if isinstance(peripheral, dict) else peripheral
        if isinstance(name, str) and (
            GPIO_OUTPUT_DEPENDENCY_RE.search(name) or "数码管" in name
        ):
            return True
    return False


def timing_requires_clock(timing: Any) -> bool:
    if not isinstance(timing, dict):
        return False
    return timing.get("precision") == "precise" or any(key != "precision" for key in timing)


def validate_output_pin_contract(name: str, pin: dict[str, Any]) -> None:
    require(
        pin.get("port") in {"PA", "PB"},
        "INVALID_REQUEST",
        f"pins.{name}.port must be PA or PB",
    )
    bits = pin.get("bits")
    require(
        isinstance(bits, list)
        and bool(bits)
        and all(
            isinstance(bit, int)
            and not isinstance(bit, bool)
            and 0 <= bit <= 7
            for bit in bits
        ),
        "INVALID_REQUEST",
        f"pins.{name}.bits must contain bit numbers 0..7",
    )
    require(
        len(set(bits)) == len(bits),
        "INVALID_REQUEST",
        f"pins.{name}.bits must be unique",
    )
    require(
        pin.get("drive") in {"push_pull", "open_drain"},
        "INVALID_REQUEST",
        f"pins.{name}.drive is invalid",
    )
    require(
        pin.get("active_level") in {"high", "low"},
        "INVALID_REQUEST",
        f"pins.{name}.active_level is invalid",
    )
    require(
        pin.get("initial_state") in {"on", "off"},
        "INVALID_REQUEST",
        f"pins.{name}.initial_state is invalid",
    )
    require(
        isinstance(pin.get("preserve_unowned_bits"), bool),
        "INVALID_REQUEST",
        f"pins.{name}.preserve_unowned_bits must be boolean",
    )


def validate_profile(profile: dict[str, Any], *, require_ready: bool = True) -> None:
    require(profile.get("schema_version") == 1, "INVALID_PROFILE", "Unsupported profile schema")
    chip = profile.get("chip")
    require(isinstance(chip, str) and bool(chip), "INVALID_PROFILE", "Profile chip is required")
    aliases = profile.get("aliases")
    require(
        isinstance(aliases, list) and all(isinstance(item, str) for item in aliases),
        "INVALID_PROFILE",
        "Profile aliases must be an array of strings",
    )
    if require_ready and profile.get("status") != "ready":
        raise GateError(
            "PROFILE_NOT_READY",
            f"Profile for {chip} is not ready",
            details={"status": profile.get("status", "missing")},
        )
    expected_device_id = profile.get("expected_device_id")
    require(
        expected_device_id is None or isinstance(expected_device_id, str),
        "INVALID_PROFILE",
        "Profile expected_device_id must be a string when provided",
    )
    versions = profile.get("approved_tool_versions")
    require(isinstance(versions, dict), "INVALID_PROFILE", "Approved tool versions are required")
    for role in MANDATORY_ROLES:
        approved = versions.get(role)
        require(
            isinstance(approved, list)
            and bool(approved)
            and all(isinstance(item, str) for item in approved),
            "INVALID_PROFILE",
            f"Approved versions for {role} must be a non-empty string array",
        )
    for role in OPTIONAL_HARDWARE_ROLES:
        approved = versions.get(role)
        require(
            approved is None
            or (
                isinstance(approved, list)
                and bool(approved)
                and all(isinstance(item, str) for item in approved)
            ),
            "INVALID_PROFILE",
            f"Approved versions for optional {role} must be a non-empty string array when provided",
        )
    attempts = profile.get("max_flash_attempts", 0)
    require(
        isinstance(attempts, int)
        and not isinstance(attempts, bool)
        and 0 <= attempts <= MAX_FLASH_ATTEMPTS,
        "INVALID_PROFILE",
        f"max_flash_attempts must be between 0 and {MAX_FLASH_ATTEMPTS}",
    )
    allowed_warnings = profile.get("allowed_warnings", [])
    require(
        isinstance(allowed_warnings, list) and all(isinstance(item, str) for item in allowed_warnings),
        "INVALID_PROFILE",
        "allowed_warnings must be a string array",
    )
    spec_root = profile.get("spec_root")
    require(
        spec_root is None or (isinstance(spec_root, str) and bool(spec_root.strip())),
        "INVALID_PROFILE",
        "spec_root must be a non-empty string when provided",
    )
    clock_model = profile.get("clock_model")
    if clock_model is not None:
        require(
            isinstance(clock_model, dict),
            "INVALID_PROFILE",
            "clock_model must be an object",
        )
        for key in ("sck_ps_register", "sck_ps_reset", "sckhl_bit", "divider_by_mode"):
            require(
                key in clock_model,
                "INVALID_PROFILE",
                f"clock_model.{key} is required",
            )
        require(
            is_non_empty_string(clock_model.get("sck_ps_register")),
            "INVALID_PROFILE",
            "clock_model.sck_ps_register must be a non-empty string",
        )
        sck_ps_reset = clock_model.get("sck_ps_reset")
        require(
            isinstance(sck_ps_reset, int)
            and not isinstance(sck_ps_reset, bool)
            and 0 <= sck_ps_reset <= 255,
            "INVALID_PROFILE",
            "clock_model.sck_ps_reset must be an 8-bit integer",
        )
        require(
            sck_ps_reset & 0x0F != 0,
            "INVALID_PROFILE",
            "clock_model.sck_ps_reset selector 0 is prohibited",
        )
        sckhl_bit = clock_model.get("sckhl_bit")
        require(
            isinstance(sckhl_bit, int)
            and not isinstance(sckhl_bit, bool)
            and 0 <= sckhl_bit <= 7,
            "INVALID_PROFILE",
            "clock_model.sckhl_bit must be a bit number 0..7",
        )
        divider_by_mode = clock_model.get("divider_by_mode")
        require(
            isinstance(divider_by_mode, dict),
            "INVALID_PROFILE",
            "clock_model.divider_by_mode must be an object",
        )
        required_selectors = {str(selector) for selector in range(1, 16)}
        require(
            set(divider_by_mode) == {"high", "low"},
            "INVALID_PROFILE",
            "clock_model.divider_by_mode must contain exactly high and low",
        )
        for mode in ("high", "low"):
            divider_map = divider_by_mode.get(mode)
            require(
                isinstance(divider_map, dict),
                "INVALID_PROFILE",
                f"clock_model.divider_by_mode.{mode} must be an object",
            )
            require(
                set(divider_map) == required_selectors,
                "INVALID_PROFILE",
                f"clock_model.divider_by_mode.{mode} must contain exactly selectors 1..15",
            )
            for selector in required_selectors:
                divider = divider_map[selector]
                require(
                    is_finite_positive_number(divider),
                    "INVALID_PROFILE",
                    f"clock_model.divider_by_mode.{mode}.{selector} must be positive",
                )
    static_config = profile.get("static_check", {})
    require(isinstance(static_config, dict), "INVALID_PROFILE", "static_check must be an object")
    if static_config:
        toolchain = static_config.get("toolchain")
        require(
            toolchain in {"company_ide", "python_source_module_cli", "simulator", "builtin_compiler"},
            "INVALID_PROFILE",
            "static_check.toolchain is invalid",
        )
        table_pairs = static_config.get("table_pairs", [])
        require(
            isinstance(table_pairs, list) and all(isinstance(item, str) for item in table_pairs),
            "INVALID_PROFILE",
            "static_check.table_pairs must be a string array",
        )
        map_files = static_config.get("map_files", [])
        require(
            isinstance(map_files, list) and all(isinstance(item, str) for item in map_files),
            "INVALID_PROFILE",
            "static_check.map_files must be a string array",
        )
        strict = static_config.get("strict_warnings", False)
        require(isinstance(strict, bool), "INVALID_PROFILE", "static_check.strict_warnings must be boolean")
    rules = profile.get("asm_rules")
    require(isinstance(rules, dict), "INVALID_PROFILE", "asm_rules are required")
    for key in ("required_patterns", "forbidden_patterns"):
        values = rules.get(key)
        require(
            isinstance(values, list) and all(isinstance(item, str) for item in values),
            "INVALID_PROFILE",
            f"asm_rules.{key} must be a string array",
        )
    limit = rules.get("max_line_length")
    require(
        isinstance(limit, int) and not isinstance(limit, bool) and limit > 0,
        "INVALID_PROFILE",
        "asm_rules.max_line_length must be positive",
    )


def validate_config(config: dict[str, Any]) -> None:
    require(config.get("schema_version") == 1, "INVALID_CONFIG", "Unsupported config schema")
    require(
        isinstance(config.get("board_id"), str) and bool(config["board_id"]),
        "INVALID_CONFIG",
        "Config board_id is required",
    )
    adapters = config.get("adapters")
    require(isinstance(adapters, dict), "INVALID_CONFIG", "Config adapters are required")
    for role in MANDATORY_ROLES:
        adapter = adapters.get(role)
        require(isinstance(adapter, dict), "INVALID_CONFIG", f"Missing {role} adapter")
        command = adapter.get("command")
        require(
            isinstance(command, list)
            and bool(command)
            and all(isinstance(item, str) and bool(item) for item in command),
            "INVALID_CONFIG",
            f"{role} adapter command must be a non-empty string array",
        )
        for item in command:
            require(
                not contains_placeholder(item),
                "INVALID_CONFIG",
                f"{role} adapter command contains placeholder instead of a real compiler adapter or toolchain path: {item}",
            )
        timeout = adapter.get("timeout_seconds", 60)
        require(
            isinstance(timeout, int) and not isinstance(timeout, bool) and 1 <= timeout <= 3600,
            "INVALID_CONFIG",
            f"{role} timeout_seconds must be between 1 and 3600",
        )
    for role in OPTIONAL_HARDWARE_ROLES:
        adapter = adapters.get(role)
        if adapter is None:
            continue
        require(isinstance(adapter, dict), "INVALID_CONFIG", f"{role} adapter must be an object")
        command = adapter.get("command")
        require(
            isinstance(command, list)
            and bool(command)
            and all(isinstance(item, str) and bool(item) for item in command),
            "INVALID_CONFIG",
            f"{role} adapter command must be a non-empty string array",
        )
        for item in command:
            require(
                not contains_placeholder(item),
                "INVALID_CONFIG",
                f"{role} adapter command contains placeholder instead of a real adapter path: {item}",
            )
        timeout = adapter.get("timeout_seconds", 60)
        require(
            isinstance(timeout, int) and not isinstance(timeout, bool) and 1 <= timeout <= 3600,
            "INVALID_CONFIG",
            f"{role} timeout_seconds must be between 1 and 3600",
        )
    if "programmer" in adapters:
        require(
            isinstance(config.get("programmer_serial"), str) and bool(config["programmer_serial"]),
            "INVALID_CONFIG",
            "Config programmer_serial is required when programmer adapter is configured",
        )
        voltage = config.get("voltage_mv")
        require(
            isinstance(voltage, int) and not isinstance(voltage, bool) and voltage > 0,
            "INVALID_CONFIG",
            "Config voltage_mv must be a positive integer when programmer adapter is configured",
        )
    simulate = config.get("simulate", {})
    require(isinstance(simulate, dict), "INVALID_CONFIG", "simulate must be an object")


def normalize_profile_paths(profile: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    normalized = dict(profile)
    spec_root = normalized.get("spec_root")
    if isinstance(spec_root, str) and spec_root:
        path = Path(spec_root)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        normalized["spec_root"] = str(path)
    return normalized


def parse_display_coordinate(value: Any, field: str) -> int:
    require(
        (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, str),
        "INVALID_REQUEST",
        f"{field} must be an integer or hexadecimal string",
    )
    if isinstance(value, int):
        number = value
    else:
        token = value.strip()
        try:
            if re.fullmatch(r"[0-9A-Fa-f]+H", token):
                number = int(token[:-1], 16)
            elif re.fullmatch(r"0x[0-9A-Fa-f]+", token):
                number = int(token[2:], 16)
            else:
                number = int(token, 10)
        except ValueError as exc:
            raise GateError("INVALID_REQUEST", f"{field} is not numeric") from exc
    require(0 <= number <= 0xFF, "INVALID_REQUEST", f"{field} must fit in one byte")
    return number


def display_geometry(display: dict[str, Any]) -> tuple[int, int, int]:
    window = display.get("window")
    require(isinstance(window, dict), "INVALID_REQUEST", "display.window must be an object")
    column_start = parse_display_coordinate(
        window.get("column_start"), "display.window.column_start"
    )
    column_end = parse_display_coordinate(
        window.get("column_end"), "display.window.column_end"
    )
    page_start = parse_display_coordinate(window.get("page_start"), "display.window.page_start")
    page_end = parse_display_coordinate(window.get("page_end"), "display.window.page_end")
    require(column_start <= column_end, "INVALID_REQUEST", "display column range is reversed")
    require(page_start <= page_end, "INVALID_REQUEST", "display page range is reversed")
    width = column_end - column_start + 1
    pages = page_end - page_start + 1
    return width, pages, width * pages


def validate_display_contract(request: dict[str, Any]) -> None:
    display = request.get("display")
    if display is None:
        return
    require(isinstance(display, dict), "INVALID_REQUEST", "display must be an object")
    require(not contains_unresolved(display), "INVALID_REQUEST", "display contains unresolved values")
    text = display.get("text")
    require(
        text is None or (isinstance(text, str) and bool(text)),
        "INVALID_REQUEST",
        "display.text must be a non-empty string when provided",
    )
    _width, pages, expected_byte_count = display_geometry(display)
    byte_count = display.get("byte_count")
    require(
        isinstance(byte_count, int) and not isinstance(byte_count, bool) and byte_count > 0,
        "INVALID_REQUEST",
        "display.byte_count must be a positive integer",
    )
    require(
        byte_count == expected_byte_count,
        "INVALID_REQUEST",
        "display.byte_count does not match the address window",
    )
    asset = display.get("asset")
    if pages > 1:
        require(
            isinstance(asset, dict),
            "INVALID_REQUEST",
            "multi-page display assets require display.asset",
        )
    if asset is None:
        return
    require(isinstance(asset, dict), "INVALID_REQUEST", "display.asset must be an object")
    manifest = asset.get("manifest")
    require(
        isinstance(manifest, str) and bool(manifest.strip()),
        "INVALID_REQUEST",
        "display.asset.manifest is required",
    )
    manifest_path = Path(manifest)
    require(
        not manifest_path.is_absolute() and ".." not in manifest_path.parts,
        "INVALID_REQUEST",
        "display.asset.manifest must be a safe relative path",
    )
    require(
        manifest_path.suffix.lower() == ".json",
        "INVALID_REQUEST",
        "display.asset.manifest must be a JSON file",
    )
    require(
        asset.get("source_encoding") in {"inline_i2c_send", "db"},
        "INVALID_REQUEST",
        "display.asset.source_encoding must be inline_i2c_send or db",
    )
    source_label = asset.get("source_label")
    require(
        isinstance(source_label, str) and ASM_LABEL_RE.fullmatch(source_label) is not None,
        "INVALID_REQUEST",
        "display.asset.source_label must be an ASM label",
    )
    for key in ("source_sha256", "output_sha256"):
        value = asset.get(key)
        require(
            isinstance(value, str) and SHA256_RE.fullmatch(value) is not None,
            "INVALID_REQUEST",
            f"display.asset.{key} must be a lowercase SHA256",
        )
    require(
        asset.get("byte_count") == byte_count,
        "INVALID_REQUEST",
        "display.asset.byte_count must equal display.byte_count",
    )


def parse_asm_byte_token(token: str) -> int:
    normalized = token.strip()
    if re.fullmatch(r"[0-9A-Fa-f]+H", normalized):
        number = int(normalized[:-1], 16)
    elif re.fullmatch(r"0x[0-9A-Fa-f]+", normalized):
        number = int(normalized[2:], 16)
    elif re.fullmatch(r"[0-9]+", normalized):
        number = int(normalized, 10)
    else:
        raise GateError("DISPLAY_ASSET_MISMATCH", f"Invalid ASM byte literal: {token}")
    require(0 <= number <= 0xFF, "DISPLAY_ASSET_MISMATCH", "ASM asset byte is out of range")
    return number


def source_region_after_label(source_text: str, label: str) -> str:
    label_match = re.search(
        r"(?mi)^[ \t]*" + re.escape(label) + r":[ \t]*(?:;.*)?$",
        source_text,
    )
    require(
        label_match is not None,
        "DISPLAY_ASSET_MISMATCH",
        f"Display asset source label was not found: {label}",
    )
    return source_text[label_match.end() :]


def extract_inline_i2c_asset_bytes(source_text: str, label: str) -> list[int]:
    region = source_region_after_label(source_text, label)
    end = re.search(r"(?mi)^[ \t]*RET[ \t]*(?:;.*)?$", region)
    require(
        end is not None,
        "DISPLAY_ASSET_MISMATCH",
        f"Display asset routine has no RET: {label}",
    )
    routine = region[: end.start()]
    pair_re = re.compile(
        r"(?mi)^[ \t]*MOV[ \t]+A[ \t]*,[ \t]*#"
        r"(0x[0-9A-Fa-f]+|[0-9A-Fa-f]+H|[0-9]+)"
        r"[ \t]*(?:;.*)?\r?\n[ \t]*CALL[ \t]+I2C_SEND\b"
    )
    return [parse_asm_byte_token(match.group(1)) for match in pair_re.finditer(routine)]


def extract_db_asset_bytes(source_text: str, label: str) -> list[int]:
    region = source_region_after_label(source_text, label)
    end = re.search(
        r"(?mi)^(?:[ \t]*[A-Za-z_.$?][A-Za-z0-9_.$?]*:[ \t]*(?:;.*)?|[ \t]*END\b)",
        region,
    )
    table = region[: end.start()] if end is not None else region
    output: list[int] = []
    for match in re.finditer(r"(?mi)^[ \t]*DB[ \t]+([^;\r\n]+)", table):
        for token in match.group(1).split(","):
            output.append(parse_asm_byte_token(token))
    return output


def audit_display_asset(
    request: dict[str, Any], source: Path, manifest_path: Path
) -> dict[str, Any] | None:
    display = request.get("display")
    if not isinstance(display, dict) or not isinstance(display.get("asset"), dict):
        return None
    asset = display["asset"]
    require(
        manifest_path.is_file(),
        "DISPLAY_ASSET_MISSING",
        f"Display asset manifest does not exist: {manifest_path}",
    )
    manifest = read_json(manifest_path, "DISPLAY_ASSET_INVALID")
    require(
        manifest.get("expected_source_sha256") == asset["source_sha256"],
        "DISPLAY_ASSET_MISMATCH",
        "Manifest source SHA256 does not match request",
    )
    require(
        manifest.get("expected_output_sha256") == asset["output_sha256"],
        "DISPLAY_ASSET_MISMATCH",
        "Manifest output SHA256 does not match request",
    )
    try:
        result = build_display_asset_result(manifest)
    except DisplayAssetError as exc:
        raise GateError("DISPLAY_ASSET_INVALID", str(exc)) from exc

    width, pages, expected_byte_count = display_geometry(display)
    expected_text_order = "".join(result["text_order"])
    require(result["width"] == width, "DISPLAY_ASSET_MISMATCH", "Asset width does not match window")
    require(
        result["height"] == pages * 8,
        "DISPLAY_ASSET_MISMATCH",
        "Asset height does not match page range",
    )
    require(
        result["output_byte_count"] == expected_byte_count == asset["byte_count"],
        "DISPLAY_ASSET_MISMATCH",
        "Asset byte count does not match display contract",
    )
    if isinstance(display.get("text"), str):
        require(
            expected_text_order == display["text"],
            "DISPLAY_ASSET_MISMATCH",
            "Asset layout labels do not preserve display text order",
        )
    require(
        result["source_sha256"] == asset["source_sha256"],
        "DISPLAY_ASSET_MISMATCH",
        "Asset source SHA256 does not match request",
    )
    require(
        result["output_sha256"] == asset["output_sha256"],
        "DISPLAY_ASSET_MISMATCH",
        "Asset output SHA256 does not match request",
    )

    try:
        source_text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise GateError("DISPLAY_ASSET_MISMATCH", f"Cannot read ASM display source: {exc}") from exc
    if asset["source_encoding"] == "inline_i2c_send":
        source_bytes = extract_inline_i2c_asset_bytes(source_text, asset["source_label"])
    else:
        source_bytes = extract_db_asset_bytes(source_text, asset["source_label"])
    require(
        len(source_bytes) == result["output_byte_count"],
        "DISPLAY_ASSET_MISMATCH",
        "ASM display byte count does not match transformed asset",
    )
    source_output_hash = hashlib.sha256(bytes(bytearray(source_bytes))).hexdigest()
    require(
        source_output_hash == result["output_sha256"],
        "DISPLAY_ASSET_MISMATCH",
        "ASM display bytes do not match transformed asset SHA256",
    )
    return {
        "status": "pass",
        "rule_ids": ["HK-OLED-003", "HK-OLED-004", "HK-OLED-006"],
        "manifest_sha256": sha256_file(manifest_path),
        "source_byte_count": result["source_byte_count"],
        "source_sha256": result["source_sha256"],
        "output_byte_count": result["output_byte_count"],
        "output_sha256": result["output_sha256"],
        "text_order": result["text_order"],
        "transform": result["transform"],
    }


def validate_request(request: dict[str, Any], profile: dict[str, Any], config: dict[str, Any]) -> None:
    require(request.get("schema_version") == 1, "INVALID_REQUEST", "Unsupported request schema")
    supported = {profile["chip"], *profile.get("aliases", [])}
    require(request.get("chip") in supported, "INVALID_REQUEST", "Request chip is not supported")
    behavior = request.get("behavior")
    require(isinstance(behavior, str) and bool(behavior.strip()), "INVALID_REQUEST", "behavior is required")
    timing = request.get("timing")
    clock_required = timing_requires_clock(timing)
    if clock_required or "clock" in request or "clock_hz" in request:
        validate_clock_contract(request, profile)
    pins = request.get("pins")
    uses_gpio = request_uses_gpio(request)
    requires_gpio_output = request_requires_gpio_output(request)
    if uses_gpio:
        require(isinstance(pins, dict) and bool(pins), "INVALID_REQUEST", "pins are required for GPIO tasks")
    if pins is not None:
        require(isinstance(pins, dict), "INVALID_REQUEST", "pins must be an object")
        require(not contains_unresolved(pins), "INVALID_REQUEST", "pins contain unresolved values")
        for key, value in pins.items():
            require(is_non_empty_string(key), "INVALID_REQUEST", "pin names must be non-empty strings")
            if uses_gpio:
                require(
                    isinstance(value, dict),
                    "INVALID_REQUEST",
                    "GPIO output tasks require structured output pin contracts"
                    if requires_gpio_output
                    else "GPIO tasks require structured pin contracts",
                )
            require(
                is_non_empty_string(value) or isinstance(value, dict),
                "INVALID_REQUEST",
                "pin values must be non-empty strings or objects",
            )
            if isinstance(value, dict):
                direction = value.get("direction")
                require(
                    direction in {"input", "output"},
                    "INVALID_REQUEST",
                    f"pins.{key}.direction must be input or output",
                )
                if direction == "output":
                    validate_output_pin_contract(key, value)
        if requires_gpio_output:
            require(
                any(
                    isinstance(value, dict) and value.get("direction") == "output"
                    for value in pins.values()
                ),
                "INVALID_REQUEST",
                "GPIO output tasks require at least one structured output pin contract",
            )
    peripherals = request.get("peripherals")
    require(isinstance(peripherals, list), "INVALID_REQUEST", "peripherals must be an array")
    require(not contains_unresolved(peripherals), "INVALID_REQUEST", "peripherals contain unresolved values")
    for item in peripherals:
        require(
            is_non_empty_string(item) or isinstance(item, dict),
            "INVALID_REQUEST",
            "Each peripheral must be a string or object",
        )
        if isinstance(item, dict):
            require(is_non_empty_string(item.get("name")), "INVALID_REQUEST", "Peripheral name is required")
    validate_display_contract(request)
    if timing is not None:
        require(isinstance(timing, dict), "INVALID_REQUEST", "timing must be an object")
        require(not contains_unresolved(timing), "INVALID_REQUEST", "timing contains unresolved values")
        precision = timing.get("precision")
        require(
            precision is None or precision in {"approximate", "precise"},
            "INVALID_REQUEST",
            "timing.precision must be approximate or precise",
        )
        delay_targets = timing.get("delay_targets")
        if "delay_targets" in timing:
            require(
                precision == "precise",
                "INVALID_REQUEST",
                "timing.precision must be precise when delay_targets are provided",
            )
        if precision == "precise":
            require(
                isinstance(delay_targets, list) and bool(delay_targets),
                "INVALID_REQUEST",
                "timing.delay_targets are required for precise timing",
            )
        for key, value in timing.items():
            require(is_non_empty_string(key), "INVALID_REQUEST", "timing keys must be non-empty strings")
            if key != "delay_targets":
                require(is_scalar(value), "INVALID_REQUEST", "timing values must be scalar")
                continue
            require(
                isinstance(value, list),
                "INVALID_REQUEST",
                "timing.delay_targets must be an array",
            )
            for item in value:
                require(
                    isinstance(item, dict),
                    "INVALID_REQUEST",
                    "Each timing.delay_targets item must be an object",
                )
                require(
                    is_non_empty_string(item.get("label")),
                    "INVALID_REQUEST",
                    "timing.delay_targets label is required",
                )
                target_us = item.get("target_us")
                require(
                    is_finite_positive_number(target_us),
                    "INVALID_REQUEST",
                    "timing.delay_targets target_us must be positive",
                )
                tolerance_percent = item.get("tolerance_percent")
                require(
                    is_finite_positive_number(tolerance_percent),
                    "INVALID_REQUEST",
                    "timing.delay_targets tolerance_percent must be positive",
                )
    memory_limits = request.get("memory_limits")
    require(
        isinstance(memory_limits, dict),
        "INVALID_REQUEST",
        "memory_limits must be an object",
    )
    require(not contains_unresolved(memory_limits), "INVALID_REQUEST", "memory_limits contain unresolved values")
    for key in ("rom_bytes", "ram_bytes"):
        value = memory_limits.get(key)
        require(
            isinstance(value, int) and not isinstance(value, bool) and value > 0,
            "INVALID_REQUEST",
            f"memory_limits.{key} must be a positive integer",
        )
    board = request.get("board")
    require(isinstance(board, dict), "INVALID_REQUEST", "board must be an object")
    require(not contains_unresolved(board), "INVALID_REQUEST", "board contains unresolved values")
    require(board.get("id") == config["board_id"], "INVALID_REQUEST", "Request board does not match config")
    acceptance = request.get("acceptance", [])
    require(isinstance(acceptance, list), "INVALID_REQUEST", "acceptance must be an array when provided")
    for item in acceptance:
        require(isinstance(item, dict), "INVALID_REQUEST", "Each acceptance item must be an object")
        for key in ("name", "observable", "expected"):
            require(
                isinstance(item.get(key), str) and bool(item[key]),
                "INVALID_REQUEST",
                f"Acceptance {key} is required",
            )
    require(
        request.get("allow_nonvolatile_changes") is False,
        "INVALID_REQUEST",
        "Nonvolatile configuration changes are not permitted",
    )


def adapter_payload(
    profile: dict[str, Any], config: dict[str, Any], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "chip": profile["chip"],
        "simulate": config.get("simulate", {}),
        "expected_device_id": profile.get("expected_device_id"),
        "expected_programmer_serial": config.get("programmer_serial"),
        "expected_voltage_mv": config.get("voltage_mv"),
        "board_id": config["board_id"],
    }
    if extra:
        payload.update(extra)
    return payload


def invoke_adapter(
    role: str,
    operation: str,
    payload: dict[str, Any],
    config: dict[str, Any],
    work_dir: Path,
    label: str,
) -> dict[str, Any]:
    adapter = config["adapters"][role]
    input_path = work_dir / "adapter" / f"{label}.input.json"
    output_path = work_dir / "adapter" / f"{label}.output.json"
    stdout_path = work_dir / "logs" / f"{label}.stdout.txt"
    stderr_path = work_dir / "logs" / f"{label}.stderr.txt"
    write_json(input_path, payload)
    unlink_if_exists(output_path)
    command = [
        *expand_adapter_command(adapter["command"]),
        role,
        operation,
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=work_dir,
            text=True,
            capture_output=True,
            timeout=adapter.get("timeout_seconds", 60),
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdapterError(role, f"Adapter could not run: {exc}") from exc
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    if not output_path.is_file():
        try:
            result = read_json_text(completed.stdout.strip(), "ADAPTER_RESULT_INVALID", f"{role} stdout")
        except GateError as exc:
            raise AdapterError(role, "Adapter did not return a JSON result") from exc
    else:
        try:
            result = read_json(output_path, "ADAPTER_RESULT_INVALID")
        except GateError as exc:
            raise AdapterError(role, exc.message) from exc
    if completed.returncode != 0 or result.get("status") != "pass":
        message = result.get("error")
        if not isinstance(message, str) or not message:
            message = f"Adapter returned exit code {completed.returncode}"
        raise AdapterError(role, message)
    return result


def expand_adapter_command(command: list[str]) -> list[str]:
    expanded: list[str] = []
    for token in command:
        if token == "$PYTHON":
            expanded.append(sys.executable)
            continue
        if token == "$SKILL_ROOT":
            expanded.append(str(SKILL_ROOT))
            continue
        if token.startswith("$SKILL_ROOT/") or token.startswith("$SKILL_ROOT\\"):
            suffix = token[len("$SKILL_ROOT") + 1 :]
            expanded.append(str(SKILL_ROOT / Path(suffix)))
            continue
        expanded.append(token)
    return expanded


def check_version(role: str, result: dict[str, Any], profile: dict[str, Any]) -> None:
    version = result.get("tool_version")
    approved = profile["approved_tool_versions"].get(role)
    if not isinstance(approved, list) or version not in approved:
        raise AdapterError(role, f"Unapproved {role} version: {version!r}")


def check_probe_identity(role: str, result: dict[str, Any], profile: dict[str, Any], config: dict[str, Any]) -> None:
    if role != "programmer":
        return
    expected = {
        "device_id": profile["expected_device_id"],
        "programmer_serial": config["programmer_serial"],
        "voltage_mv": config["voltage_mv"],
    }
    for key, value in expected.items():
        if result.get(key) != value:
            raise AdapterError(role, f"Programmer probe {key} mismatch")


def run_doctor(profile: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    validate_profile(profile)
    validate_config(config)
    if "programmer" in config["adapters"]:
        require(
            isinstance(profile.get("expected_device_id"), str) and bool(profile["expected_device_id"]),
            "INVALID_PROFILE",
            "Profile expected_device_id is required when programmer adapter is configured",
        )
    tools: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="hk8asm-doctor-") as temp:
        work_dir = Path(temp)
        roles_to_probe = [
            role for role in ROLES if role in MANDATORY_ROLES or role in config["adapters"]
        ]
        for role in roles_to_probe:
            try:
                result = invoke_adapter(
                    role,
                    "probe",
                    adapter_payload(profile, config),
                    config,
                    work_dir,
                    f"{role}-probe",
                )
                check_version(role, result, profile)
                check_probe_identity(role, result, profile, config)
            except AdapterError as exc:
                raise GateError("DOCTOR_FAILED", f"{role} preflight failed: {exc.message}") from exc
            tools[role] = result["tool_version"]
    return {"code": "READY", "chip": profile["chip"], "tools": tools}


def load_run(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    require(run_dir.is_dir(), "RUN_NOT_FOUND", f"Run directory does not exist: {run_dir}")
    run = read_json(run_dir / "run.json", "RUN_INVALID")
    profile = read_json(run_dir / "profile.json", "RUN_INVALID")
    config = read_json(run_dir / "config.json", "RUN_INVALID")
    request = read_json(run_dir / "request.json", "RUN_INVALID")
    validate_profile(profile)
    validate_config(config)
    validate_request(request, profile, config)
    return run, profile, config, request


def append_history(run: dict[str, Any], state: str, **fields: Any) -> None:
    entry = {"state": state, "at": now_utc(), **fields}
    run.setdefault("history", []).append(entry)


def write_evidence(run_dir: Path, payload: dict[str, Any]) -> str:
    evidence_path = run_dir / "evidence.json"
    write_json(evidence_path, payload)
    return sha256_file(evidence_path)


def snapshot_hashes(run_dir: Path) -> dict[str, str]:
    snapshots = {
        "request_sha256": sha256_file(run_dir / "request.json"),
        "profile_sha256": sha256_file(run_dir / "profile.json"),
        "config_sha256": sha256_file(run_dir / "config.json"),
    }
    display_asset = run_dir / DISPLAY_ASSET_SNAPSHOT
    if display_asset.is_file():
        snapshots["display_asset_sha256"] = sha256_file(display_asset)
    return snapshots


def require_snapshot_hashes(run_dir: Path, evidence: dict[str, Any]) -> None:
    expected = evidence.get("snapshots")
    require(
        isinstance(expected, dict),
        "RELEASE_BLOCKED",
        "Compile evidence snapshot hashes are missing",
    )
    current = snapshot_hashes(run_dir)
    require(
        set(expected) == set(current),
        "RELEASE_BLOCKED",
        "Run snapshot file set changed after build",
    )
    for key, value in current.items():
        require(
            expected.get(key) == value,
            "RELEASE_BLOCKED",
            f"Run snapshot changed after build: {key}",
        )


def resolve_run_artifact_path(run_dir: Path, value: Any) -> Path | None:
    if value is None:
        return None
    require(isinstance(value, str) and bool(value), "RELEASE_BLOCKED", "Declared artifact path is invalid")
    path = Path(value)
    if not path.is_absolute():
        path = run_dir / path
    return path.resolve(strict=False)


def require_declared_compile_artifacts(run_dir: Path, compile_result: dict[str, Any]) -> None:
    artifacts = compile_result.get("artifacts", {})
    require(isinstance(artifacts, dict), "RELEASE_BLOCKED", "Compile artifact manifest is invalid")
    for path_key, path_value in artifacts.items():
        if not path_key.endswith("_path") or path_value is None:
            continue
        artifact_path = resolve_run_artifact_path(run_dir, path_value)
        require(artifact_path is not None and artifact_path.is_file(), "RELEASE_BLOCKED", f"Declared artifact is missing: {path_key}")
        hash_key = f"{path_key[:-5]}_sha256"
        expected_hash = artifacts.get(hash_key)
        if expected_hash is None and path_key == "hex_path":
            expected_hash = compile_result.get("artifact_sha256")
        require(
            isinstance(expected_hash, str) and bool(expected_hash),
            "RELEASE_BLOCKED",
            f"Declared artifact hash is missing: {hash_key}",
        )
        require(
            sha256_file(artifact_path) == expected_hash,
            "RELEASE_BLOCKED",
            f"Declared artifact hash changed: {path_key}",
        )


def save_failure(run_dir: Path, run: dict[str, Any], stage: str, code: str, message: str) -> None:
    run["state"] = "FAILED"
    run["failure"] = {"stage": stage, "code": code, "message": message, "at": now_utc()}
    append_history(run, "FAILED", stage=stage, code=code)
    evidence = {
        "schema_version": 1,
        "run_id": run.get("run_id"),
        "chip": run.get("chip"),
        "state": "FAILED",
        "updated_at": now_utc(),
        "source_sha256": run.get("source_sha256"),
        "artifact_sha256": run.get("artifact_sha256"),
        "flash_attempts": run.get("flash_attempts", 0),
        "failure": run["failure"],
    }
    run["evidence_sha256"] = write_evidence(run_dir, evidence)
    write_json(run_dir / "run.json", run)


def static_check(source: Path, profile: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    comment_language = validate_chinese_explanatory_comments(source)
    request = read_json(run_dir / "request.json", "RUN_INVALID")
    display_asset_audit = audit_display_asset(
        request, source, run_dir / DISPLAY_ASSET_SNAPSHOT
    )
    static_config = profile.get("static_check", {})
    spec_root_value = profile.get("spec_root")
    if static_config and spec_root_value:
        spec_root = Path(spec_root_value)
        checker = spec_root / "tools" / "asm_static_check.py"
        require(checker.is_file(), "STATIC_CHECK_FAILED", f"Spec static checker is missing: {checker}")
        command = [
            sys.executable,
            str(checker),
            str(source),
            "--toolchain",
            static_config["toolchain"],
            "--request",
            str(run_dir / "request.json"),
            "--profile",
            str(run_dir / "profile.json"),
            "--json",
        ]
        for map_file in static_config.get("map_files", []):
            command.extend(["--map", map_file])
        for table_pair in static_config.get("table_pairs", []):
            command.extend(["--table-pair", table_pair])
        strict_warnings = static_config.get("strict_warnings", False)
        if strict_warnings:
            command.append("--strict-warnings")
        completed = subprocess.run(
            command,
            cwd=source.parent,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
            shell=False,
        )
        try:
            result = read_json_text(completed.stdout.strip(), "STATIC_CHECK_FAILED", "asm_static_check stdout")
        except GateError as exc:
            raise GateError(
                "STATIC_CHECK_FAILED",
                "Spec static checker did not return valid JSON",
                details={"stderr": completed.stderr[-2000:]},
            ) from exc
        finding_fields = {
            "rule_id",
            "severity",
            "file",
            "line",
            "evidence",
            "risk",
            "required_fix",
        }
        severity_summary_keys = {
            "BLOCKER": "blockers",
            "ERROR": "errors",
            "WARNING": "warnings",
            "INFO": "info",
        }
        checker_findings = result.get("findings")

        def reject_checker_payload() -> None:
            raise GateError(
                "STATIC_CHECK_FAILED",
                "Spec static checker returned inconsistent evidence",
                details=result,
            )

        if not isinstance(checker_findings, list):
            reject_checker_payload()
        severity_counts = {key: 0 for key in severity_summary_keys.values()}
        for finding in checker_findings:
            if (
                not isinstance(finding, dict)
                or set(finding) != finding_fields
                or finding.get("severity") not in severity_summary_keys
            ):
                reject_checker_payload()
            severity_counts[severity_summary_keys[finding["severity"]]] += 1
        if severity_counts["blockers"] or severity_counts["errors"]:
            expected_exit_code = 2
        elif strict_warnings and severity_counts["warnings"]:
            expected_exit_code = 1
        else:
            expected_exit_code = 0
        summary = {**severity_counts, "exit_code": expected_exit_code}
        if result.get("summary") != summary or completed.returncode != expected_exit_code:
            reject_checker_payload()

        checker_audits = result.get("semantic_audits")
        expected_audit_rules = {
            "gpio_contract": ["HK-GPIO-002", "HK-GPIO-INIT-001"],
            "loop_semantics": ["HK-SYN-012", "HK-WDT-001", "HK-WDT-002"],
            "oled_i2c": ["HK-I2C-005", "HK-I2C-006", "HK-OLED-005"],
        }

        def valid_audit_section(name: str) -> bool:
            if not isinstance(checker_audits, dict):
                return False
            section = checker_audits.get(name)
            if not isinstance(section, dict):
                return False
            audited = section.get("audited")
            status = section.get("status")
            rule_ids = section.get("rule_ids")
            finding_rule_ids = section.get("finding_rule_ids")
            if (
                not isinstance(audited, bool)
                or status
                not in {"pass", "warning", "fail", "info", "not_applicable", "unavailable"}
                or rule_ids != expected_audit_rules[name]
                or not isinstance(finding_rule_ids, list)
                or not all(isinstance(rule_id, str) for rule_id in finding_rule_ids)
                or not set(finding_rule_ids).issubset(rule_ids)
            ):
                return False
            if status == "pass":
                return audited and not finding_rule_ids
            if status in {"warning", "fail", "info"}:
                return audited and bool(finding_rule_ids)
            return not audited and not finding_rule_ids

        audit_sections_valid: dict[str, bool] = {}
        for name, rule_ids in expected_audit_rules.items():
            relevant_findings = [
                finding for finding in checker_findings if finding["rule_id"] in rule_ids
            ]
            audit_sections_valid[name] = valid_audit_section(name)
            if not audit_sections_valid[name]:
                if relevant_findings:
                    reject_checker_payload()
                continue
            section = checker_audits[name]
            expected_finding_rule_ids = sorted(
                {finding["rule_id"] for finding in relevant_findings}
            )
            if section["finding_rule_ids"] != expected_finding_rule_ids:
                reject_checker_payload()
            severities = {finding["severity"] for finding in relevant_findings}
            if severities & {"BLOCKER", "ERROR"}:
                expected_status = "fail"
            elif "WARNING" in severities:
                expected_status = "warning"
            elif "INFO" in severities:
                expected_status = "info"
            elif section["audited"]:
                expected_status = "pass"
            else:
                expected_status = section["status"]
            if section["status"] != expected_status:
                reject_checker_payload()

        if expected_exit_code != 0:
            raise GateError("STATIC_CHECK_FAILED", "Spec static checker failed", details=result)
        static_result = {
            "status": "pass",
            "checker": "asm_static_check.py",
            "toolchain": static_config["toolchain"],
            "summary": summary,
            "comment_language": comment_language,
        }
        if (
            isinstance(checker_audits, dict)
            and isinstance(checker_audits.get("timing"), list)
            and all(audit_sections_valid.values())
        ):
            static_result["semantic_audits"] = checker_audits
        if display_asset_audit is not None:
            static_result["display_asset_audit"] = display_asset_audit
        return static_result

    text = source.read_text(encoding="utf-8-sig")
    rules = profile["asm_rules"]
    issues: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if len(line) > rules["max_line_length"]:
            issues.append({"line": line_number, "rule": "max_line_length"})
    upper_text = text.upper()
    for pattern in rules["required_patterns"]:
        if pattern.upper() not in upper_text:
            issues.append({"rule": "required_pattern", "pattern": pattern})
    for pattern in rules["forbidden_patterns"]:
        if pattern.upper() in upper_text:
            issues.append({"rule": "forbidden_pattern", "pattern": pattern})
    if issues:
        raise GateError("STATIC_CHECK_FAILED", "Candidate failed static checks", details=issues)
    static_result = {
        "status": "pass",
        "checks": 1 + len(rules["required_patterns"]) + len(rules["forbidden_patterns"]),
        "comment_language": comment_language,
    }
    if display_asset_audit is not None:
        static_result["display_asset_audit"] = display_asset_audit
    return static_result


def command_doctor(args: argparse.Namespace) -> dict[str, Any]:
    profile = normalize_profile_paths(read_json(args.profile, "INVALID_PROFILE"), args.profile.parent)
    config = read_json(args.config, "INVALID_CONFIG")
    return run_doctor(profile, config)


def command_new_run(args: argparse.Namespace) -> dict[str, Any]:
    profile = normalize_profile_paths(read_json(args.profile, "INVALID_PROFILE"), args.profile.parent)
    config = read_json(args.config, "INVALID_CONFIG")
    request = read_json(args.request, "INVALID_REQUEST")
    run_doctor(profile, config)
    validate_request(request, profile, config)
    require(args.source.is_file(), "SOURCE_NOT_FOUND", f"Candidate source does not exist: {args.source}")
    display = request.get("display")
    asset = display.get("asset") if isinstance(display, dict) else None
    display_asset_source: Path | None = None
    display_asset_audit = None
    if isinstance(asset, dict):
        display_asset_source = (args.request.resolve().parent / asset["manifest"]).resolve()
        display_asset_audit = audit_display_asset(request, args.source, display_asset_source)
    require(not args.run_dir.exists(), "RUN_EXISTS", f"Run directory already exists: {args.run_dir}")
    args.run_dir.mkdir(parents=True)
    source_copy = args.run_dir / "src" / "candidate.asm"
    source_copy.parent.mkdir(parents=True)
    write_json(args.run_dir / "profile.json", profile)
    shutil.copy2(args.config, args.run_dir / "config.json")
    shutil.copy2(args.request, args.run_dir / "request.json")
    shutil.copy2(args.source, source_copy)
    if display_asset_source is not None:
        display_asset_snapshot = args.run_dir / DISPLAY_ASSET_SNAPSHOT
        display_asset_snapshot.parent.mkdir(parents=True)
        shutil.copy2(display_asset_source, display_asset_snapshot)
    source_hash = sha256_file(source_copy)
    run = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": uuid.uuid4().hex,
        "chip": profile["chip"],
        "state": "CREATED",
        "created_at": now_utc(),
        "source_sha256": source_hash,
        "verified_source_sha256": None,
        "artifact_sha256": None,
        "evidence_sha256": None,
        "flash_attempts": 0,
        "max_flash_attempts": profile.get("max_flash_attempts", 0),
        "history": [],
    }
    if display_asset_audit is not None:
        run["display_asset_sha256"] = display_asset_audit["manifest_sha256"]
    append_history(run, "CREATED")
    write_json(args.run_dir / "run.json", run)
    return {"code": "RUN_CREATED", "run_id": run["run_id"], "run_dir": str(args.run_dir)}


def command_close_loop(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = args.run_dir.resolve()
    run, profile, config, request = load_run(run_dir)
    source = run_dir / "src" / "candidate.asm"
    require(source.is_file(), "SOURCE_NOT_FOUND", "Run candidate source is missing")
    current_hash = sha256_file(source)
    if current_hash != run.get("source_sha256"):
        run["source_sha256"] = current_hash
        append_history(run, "SOURCE_CHANGED_RESET")
    run["state"] = "CREATED"
    run["verified_source_sha256"] = None
    run["artifact_sha256"] = None
    run["evidence_sha256"] = None
    run.pop("failure", None)
    unlink_if_exists(run_dir / "evidence.json")
    write_json(run_dir / "run.json", run)

    try:
        static_result = static_check(source, profile, run_dir)
        run_doctor(profile, config)
    except GateError as exc:
        save_failure(run_dir, run, "preflight", exc.code, exc.message)
        raise

    artifact = run_dir / "build" / "firmware.hex"
    try:
        compile_result = invoke_adapter(
            "compiler",
            "run",
            adapter_payload(
                profile,
                config,
                {"source_path": str(source), "artifact_path": str(artifact), "request": request},
            ),
            config,
            run_dir,
            f"compiler-run-{run['flash_attempts'] + 1}",
        )
        check_version("compiler", compile_result, profile)
        require(artifact.is_file(), "COMPILE_FAILED", "Compiler artifact is missing")
        require(
            compile_result.get("source_sha256") == current_hash,
            "COMPILE_FAILED",
            "Compiler source hash does not match candidate",
        )
        artifact_hash = sha256_file(artifact)
        require(
            compile_result.get("artifact_sha256") == artifact_hash,
            "COMPILE_FAILED",
            "Compiler artifact hash does not match",
        )
        allowed_warnings = set(profile.get("allowed_warnings", []))
        warnings = compile_result.get("warnings", [])
        require(isinstance(warnings, list), "COMPILE_FAILED", "Compiler warnings must be an array")
        unexpected_warnings = [warning for warning in warnings if warning not in allowed_warnings]
        require(not unexpected_warnings, "COMPILE_FAILED", "Compiler emitted unapproved warnings")
    except AdapterError as exc:
        save_failure(run_dir, run, "compile", "COMPILE_FAILED", exc.message)
        raise GateError("COMPILE_FAILED", "Compiler gate failed") from exc
    except GateError as exc:
        save_failure(run_dir, run, "compile", exc.code, exc.message)
        raise

    run["state"] = "BUILT"
    run["artifact_sha256"] = artifact_hash
    run["verified_source_sha256"] = current_hash
    append_history(run, "BUILT")
    evidence = {
        "schema_version": 1,
        "run_id": run["run_id"],
        "chip": profile["chip"],
        "state": "BUILT",
        "compiled_at": now_utc(),
        "source_sha256": current_hash,
        "artifact_sha256": artifact_hash,
        "snapshots": snapshot_hashes(run_dir),
        "flash_attempts": run["flash_attempts"],
        "gates": {
            "static": static_result,
            "compile": compile_result,
        },
        "deferred_gates": ["program", "readback", "hardware_verify"],
    }
    run["evidence_sha256"] = write_evidence(run_dir, evidence)
    write_json(run_dir / "run.json", run)
    return {
        "code": "COMPILE_PASSED",
        "run_id": run["run_id"],
        "state": "BUILT",
        "source_sha256": current_hash,
        "artifact_sha256": artifact_hash,
    }


def command_release(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = args.run_dir.resolve()
    resolved_run_dir = run_dir.resolve()
    resolved_output = args.output.resolve(strict=False)
    require(
        not path_is_relative_to(resolved_output, resolved_run_dir),
        "RELEASE_BLOCKED",
        "Release output must be outside the run directory",
    )
    run, profile, _config, _request = load_run(run_dir)
    source = run_dir / "src" / "candidate.asm"
    if run.get("state") not in {"BUILT", "VERIFIED", "RELEASED"}:
        raise GateError("RELEASE_BLOCKED", "Run has not passed the static check and compile gates")
    require(source.is_file(), "SOURCE_CHANGED", "Verified source is missing")
    current_hash = sha256_file(source)
    evidence_path = run_dir / "evidence.json"
    require(evidence_path.is_file(), "RELEASE_BLOCKED", "Compile evidence is missing")
    expected_evidence_hash = run.get("evidence_sha256")
    require(
        isinstance(expected_evidence_hash, str) and bool(expected_evidence_hash),
        "RELEASE_BLOCKED",
        "Compile evidence hash is missing",
    )
    require(
        sha256_file(evidence_path) == expected_evidence_hash,
        "RELEASE_BLOCKED",
        "Compile evidence changed after build",
    )
    evidence = read_json(evidence_path, "RELEASE_BLOCKED")
    require_snapshot_hashes(run_dir, evidence)
    if current_hash != run.get("verified_source_sha256") or current_hash != evidence.get("source_sha256"):
        run["state"] = "CREATED"
        run["verified_source_sha256"] = None
        append_history(run, "SOURCE_CHANGED_RESET")
        write_json(run_dir / "run.json", run)
        unlink_if_exists(evidence_path)
        raise GateError("SOURCE_CHANGED", "Candidate source changed after compile")
    artifact = run_dir / "build" / "firmware.hex"
    require(artifact.is_file(), "RELEASE_BLOCKED", "Compiled artifact is missing")
    artifact_hash = sha256_file(artifact)
    require(
        artifact_hash == evidence.get("artifact_sha256") == run.get("artifact_sha256"),
        "RELEASE_BLOCKED",
        "Compiled artifact hash changed",
    )
    compile_result = evidence.get("gates", {}).get("compile")
    require(isinstance(compile_result, dict), "RELEASE_BLOCKED", "Compile evidence is invalid")
    require_declared_compile_artifacts(run_dir, compile_result)
    try:
        validate_chinese_explanatory_comments(source)
    except GateError as exc:
        raise GateError("RELEASE_BLOCKED", exc.message, details=exc.details) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = args.output.with_name(f".{args.output.name}.{uuid.uuid4().hex}.tmp")
    shutil.copy2(source, temp_output)
    os.replace(temp_output, args.output)
    require(sha256_file(args.output) == current_hash, "RELEASE_BLOCKED", "Released source hash mismatch")
    run["state"] = "RELEASED"
    append_history(run, "RELEASED", output=str(args.output))
    write_json(run_dir / "run.json", run)
    return {
        "code": "RELEASED",
        "run_id": run["run_id"],
        "chip": profile["chip"],
        "output": str(args.output),
        "source_sha256": current_hash,
        "artifact_sha256": artifact_hash,
        "evidence": str(evidence_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hk8asm", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Probe the configured compiler and optional hardware adapters")
    doctor.add_argument("--profile", required=True, type=Path)
    doctor.add_argument("--config", required=True, type=Path)
    doctor.set_defaults(handler=command_doctor)

    new_run = subparsers.add_parser("new-run", help="Validate and snapshot a candidate run")
    new_run.add_argument("--profile", required=True, type=Path)
    new_run.add_argument("--config", required=True, type=Path)
    new_run.add_argument("--request", required=True, type=Path)
    new_run.add_argument("--source", required=True, type=Path)
    new_run.add_argument("--run-dir", required=True, type=Path)
    new_run.set_defaults(handler=command_new_run)

    close_loop = subparsers.add_parser("close-loop", help="Run static checks and compile the candidate")
    close_loop.add_argument("--run-dir", required=True, type=Path)
    close_loop.set_defaults(handler=command_close_loop)

    release = subparsers.add_parser("release", help="Release only a compiled source")
    release.add_argument("--run-dir", required=True, type=Path)
    release.add_argument("--output", required=True, type=Path)
    release.set_defaults(handler=command_release)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        payload = args.handler(args)
    except GateError as exc:
        payload = {"code": exc.code, "status": "error", "message": exc.message}
        if exc.details is not None:
            payload["details"] = sanitize_diagnostic_details(exc.details)
        emit(payload)
        return 2
    emit({"status": "ok", **payload})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
