#!/usr/bin/env python3
"""Fail-closed orchestration for HK8 ASM static check, compile, and release."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANDATORY_ROLES = ("compiler",)
OPTIONAL_HARDWARE_ROLES = ("programmer", "verifier")
ROLES = (*MANDATORY_ROLES, *OPTIONAL_HARDWARE_ROLES)
RUN_SCHEMA_VERSION = 1
MAX_FLASH_ATTEMPTS = 3
PLACEHOLDER_MARKERS = ("REPLACE_WITH", "实际路径")
SKILL_ROOT = Path(__file__).resolve().parents[1]


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


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise GateError(code, message)


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


def validate_clock_contract(request: dict[str, Any]) -> None:
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


def validate_request(request: dict[str, Any], profile: dict[str, Any], config: dict[str, Any]) -> None:
    require(request.get("schema_version") == 1, "INVALID_REQUEST", "Unsupported request schema")
    supported = {profile["chip"], *profile.get("aliases", [])}
    require(request.get("chip") in supported, "INVALID_REQUEST", "Request chip is not supported")
    behavior = request.get("behavior")
    require(isinstance(behavior, str) and bool(behavior.strip()), "INVALID_REQUEST", "behavior is required")
    validate_clock_contract(request)
    pins = request.get("pins")
    require(isinstance(pins, dict), "INVALID_REQUEST", "pins must be an object")
    require(not contains_unresolved(pins), "INVALID_REQUEST", "pins contain unresolved values")
    for key, value in pins.items():
        require(is_non_empty_string(key), "INVALID_REQUEST", "pin names must be non-empty strings")
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
    timing = request.get("timing")
    require(isinstance(timing, dict), "INVALID_REQUEST", "timing must be an object")
    require(not contains_unresolved(timing), "INVALID_REQUEST", "timing contains unresolved values")
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
    output_path.unlink(missing_ok=True)
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
        }
        if (
            isinstance(checker_audits, dict)
            and isinstance(checker_audits.get("timing"), list)
            and all(audit_sections_valid.values())
        ):
            static_result["semantic_audits"] = checker_audits
        return static_result

    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise GateError("STATIC_CHECK_FAILED", f"Cannot read candidate source: {exc}") from exc
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
    return {"status": "pass", "checks": 1 + len(rules["required_patterns"]) + len(rules["forbidden_patterns"])}


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
    require(not args.run_dir.exists(), "RUN_EXISTS", f"Run directory already exists: {args.run_dir}")
    args.run_dir.mkdir(parents=True)
    source_copy = args.run_dir / "src" / "candidate.asm"
    source_copy.parent.mkdir(parents=True)
    write_json(args.run_dir / "profile.json", profile)
    shutil.copy2(args.config, args.run_dir / "config.json")
    shutil.copy2(args.request, args.run_dir / "request.json")
    shutil.copy2(args.source, source_copy)
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
    append_history(run, "CREATED")
    write_json(args.run_dir / "run.json", run)
    return {"code": "RUN_CREATED", "run_id": run["run_id"], "run_dir": str(args.run_dir)}


def command_close_loop(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = args.run_dir
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
    (run_dir / "evidence.json").unlink(missing_ok=True)
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
    run_dir = args.run_dir
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
    if current_hash != run.get("verified_source_sha256") or current_hash != evidence.get("source_sha256"):
        run["state"] = "CREATED"
        run["verified_source_sha256"] = None
        append_history(run, "SOURCE_CHANGED_RESET")
        write_json(run_dir / "run.json", run)
        evidence_path.unlink(missing_ok=True)
        raise GateError("SOURCE_CHANGED", "Candidate source changed after compile")
    artifact = run_dir / "build" / "firmware.hex"
    require(artifact.is_file(), "RELEASE_BLOCKED", "Compiled artifact is missing")
    artifact_hash = sha256_file(artifact)
    require(
        artifact_hash == evidence.get("artifact_sha256") == run.get("artifact_sha256"),
        "RELEASE_BLOCKED",
        "Compiled artifact hash changed",
    )
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
            payload["details"] = exc.details
        emit(payload)
        return 2
    emit({"status": "ok", **payload})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
