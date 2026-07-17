#!/usr/bin/env python3
"""Builtin HK64S825 assembler adapter for the hk8asm closed-loop runner.

Adapter contract:

    builtin_compiler.py compiler <probe|run> --input input.json --output output.json

The assembler is intentionally conservative. It encodes only the instruction
forms present in references/spec/rules/instruction-reference.json plus the
basic ORG/EQU/DB/DW/END directives needed by the rule package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROLE = "compiler"
TOOL_VERSION = "builtin-hk64s825-assembler-2"
TOOLCHAIN = "hk64s825-builtin-assembler"
PROGRAM_MIN = 0x0000
PROGRAM_MAX = 0x03FF
LABEL_RE = re.compile(r"^\s*([A-Za-z_.$?][\w.$?]*)\s*:\s*(.*)$")
EQU_RE = re.compile(r"^\s*([A-Za-z_.$?][\w.$?]*)\s+EQU\s+(.+?)\s*$", re.IGNORECASE)
NUMERIC_RE = re.compile(
    r"^(?:0x[0-9A-Fa-f]+|[0-9A-Fa-f]+H|[0-9]+)$",
    re.IGNORECASE,
)
RAW_WORD_RE = re.compile(r"^(?:0x[0-9A-Fa-f]+|[0-9A-Fa-f]+H)$", re.IGNORECASE)


class CompileFailure(Exception):
    def __init__(self, code: str, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class Statement:
    line: int
    address: int
    op: str
    args: str
    kind: str


@dataclass(frozen=True)
class Assembly:
    words: dict[int, int]
    labels: dict[str, int]
    source_sha256: str


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


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
        raise CompileFailure("invalid_input", f"cannot read adapter input JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CompileFailure("invalid_input", "adapter input must be a JSON object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def parse_int(text: str) -> int:
    value = text.strip().replace("_", "")
    try:
        if re.fullmatch(r"0x[0-9A-Fa-f]+", value, re.IGNORECASE):
            return int(value, 16)
        if re.fullmatch(r"[0-9A-Fa-f]+H", value, re.IGNORECASE):
            return int(value[:-1], 16)
        if re.fullmatch(r"[0-9]+", value):
            return int(value, 10)
    except ValueError as exc:
        raise CompileFailure("invalid_number", f"invalid numeric literal: {safe_token(text)}") from exc
    raise CompileFailure("invalid_number", f"invalid numeric literal: {safe_token(text)}")


def safe_token(text: str) -> str:
    token = text.strip()
    if len(token) <= 48:
        return token
    return token[:45] + "..."


def split_args(args: str) -> list[str]:
    if not args.strip():
        return []
    parts = [part.strip() for part in args.split(",")]
    if any(not part for part in parts):
        raise CompileFailure("empty_operand", "operand list contains an empty item")
    return parts


def load_instruction_variants() -> list[dict[str, Any]]:
    path = skill_root() / "references" / "spec" / "rules" / "instruction-reference.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompileFailure("rules_unreadable", f"cannot read instruction reference: {exc}") from exc
    variants = payload.get("variants")
    if not isinstance(variants, list) or not variants:
        raise CompileFailure("rules_invalid", "instruction reference has no variants")
    return variants


def load_registers() -> dict[str, int]:
    registers: dict[str, int] = {}
    for relative in (
        ("register-reference.json", "registers"),
        ("register-alias-policy.json", "official_registers"),
    ):
        filename, key = relative
        path = skill_root() / "references" / "spec" / "rules" / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            address = item.get("address")
            if isinstance(name, str) and isinstance(address, str):
                try:
                    registers[name.upper()] = parse_int(address)
                except CompileFailure:
                    continue
    if not registers:
        raise CompileFailure("rules_invalid", "register reference has no usable registers")
    return registers


def normalize_code(raw: str) -> str:
    return raw.partition(";")[0].strip()


def resolve_value(token: str, symbols: dict[str, int], registers: dict[str, int]) -> int:
    stripped = token.strip()
    if not stripped:
        raise CompileFailure("missing_operand", "operand is missing")
    if stripped.startswith("#"):
        raise CompileFailure(
            "invalid_immediate_position",
            f"immediate prefix is not allowed here: {safe_token(stripped)}",
        )
    if NUMERIC_RE.fullmatch(stripped):
        return parse_int(stripped)
    key = stripped.upper()
    if key in registers:
        return registers[key]
    if key in symbols:
        return symbols[key]
    raise CompileFailure("unknown_symbol", f"unknown symbol or register: {safe_token(stripped)}")


def resolve_immediate(token: str, symbols: dict[str, int], registers: dict[str, int]) -> int:
    stripped = token.strip()
    if not stripped.startswith("#"):
        raise CompileFailure("missing_immediate_prefix", "immediate operand must start with #")
    value = stripped[1:].strip()
    if not value:
        raise CompileFailure("missing_operand", "immediate operand is missing")
    return resolve_value(value, symbols, registers)


def range_check(value: int, low: int, high: int, name: str, line: int) -> int:
    if not low <= value <= high:
        raise CompileFailure("operand_out_of_range", f"{name} out of range at line {line}")
    return value


def define_symbol(
    name: str,
    value: int,
    labels: dict[str, int],
    registers: dict[str, int],
    line: int,
) -> None:
    if name in labels or name in registers:
        raise CompileFailure("duplicate_symbol", f"duplicate or reserved symbol at line {line}: {name}")
    labels[name] = value


def first_pass(text: str, registers: dict[str, int]) -> tuple[list[Statement], dict[str, int]]:
    address = 0
    labels: dict[str, int] = {}
    statements: list[Statement] = []
    ended = False
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if ended:
            break
        code = normalize_code(raw)
        if not code:
            continue

        label_match = LABEL_RE.match(code)
        if label_match:
            label = label_match.group(1).upper()
            define_symbol(label, address, labels, registers, line_number)
            code = label_match.group(2).strip()
            if not code:
                continue

        equ_match = EQU_RE.match(code)
        if equ_match:
            name = equ_match.group(1).upper()
            value = resolve_value(equ_match.group(2), labels, registers)
            define_symbol(name, value, labels, registers, line_number)
            continue

        parts = code.split(None, 1)
        op = parts[0].upper()
        args = parts[1].strip() if len(parts) > 1 else ""

        if op == "ORG":
            address = range_check(resolve_value(args, labels, registers), PROGRAM_MIN, PROGRAM_MAX, "ORG", line_number)
            statements.append(Statement(line_number, address, op, args, "directive"))
            continue
        if op == "END":
            if args:
                raise CompileFailure(
                    "invalid_directive_arguments",
                    f"END does not accept operands at line {line_number}",
                )
            statements.append(Statement(line_number, address, op, args, "directive"))
            ended = True
            continue
        if op == "INCLUDE":
            raise CompileFailure(
                "unsupported_directive",
                f"INCLUDE is not supported by the built-in compiler at line {line_number}",
            )
        if op == "DB":
            values = split_args(args)
            if not values:
                raise CompileFailure("invalid_db", f"DB has no bytes at line {line_number}")
            statements.append(Statement(line_number, address, op, args, "data"))
            address += math.ceil(len(values) / 2)
            continue
        if op == "DW":
            values = split_args(args)
            if not values:
                raise CompileFailure("invalid_dw", f"DW has no words at line {line_number}")
            statements.append(Statement(line_number, address, op, args, "data"))
            address += len(values)
            continue
        if RAW_WORD_RE.fullmatch(op) and not args:
            statements.append(Statement(line_number, address, op, args, "raw"))
            address += 1
            continue

        statements.append(Statement(line_number, address, op, args, "instruction"))
        address += 1
        if address > PROGRAM_MAX + 1:
            raise CompileFailure("program_out_of_range", f"program address exceeds 0x03FF near line {line_number}")
    return statements, labels


def add_word(words: dict[int, int], address: int, value: int, line: int) -> None:
    if not PROGRAM_MIN <= address <= PROGRAM_MAX:
        raise CompileFailure("program_out_of_range", f"word address out of range at line {line}")
    if address in words:
        raise CompileFailure("program_overlap", f"ORG segments overlap at word 0x{address:04X}")
    words[address] = value & 0xFFFF


def assemble_data(statement: Statement, symbols: dict[str, int], registers: dict[str, int], words: dict[int, int]) -> None:
    if statement.op == "DB":
        bytes_out: list[int] = []
        for token in split_args(statement.args):
            value = range_check(resolve_value(token, symbols, registers), 0, 0xFF, "DB byte", statement.line)
            bytes_out.append(value)
        if len(bytes_out) % 2:
            bytes_out.append(0)
        for index in range(0, len(bytes_out), 2):
            word = (bytes_out[index] << 8) | bytes_out[index + 1]
            add_word(words, statement.address + index // 2, word, statement.line)
        return
    if statement.op == "DW":
        for index, token in enumerate(split_args(statement.args)):
            value = range_check(resolve_value(token, symbols, registers), 0, 0xFFFF, "DW word", statement.line)
            add_word(words, statement.address + index, value, statement.line)
        return
    add_word(words, statement.address, range_check(parse_int(statement.op), 0, 0xFFFF, "raw word", statement.line), statement.line)


def match_a_immediate(args: str, symbols: dict[str, int], registers: dict[str, int], line: int) -> int | None:
    parts = split_args(args)
    if len(parts) == 2 and parts[0].upper() == "A" and parts[1].lstrip().startswith("#"):
        return range_check(resolve_immediate(parts[1], symbols, registers), 0, 0xFF, "k8", line)
    return None


def match_immediate(args: str, symbols: dict[str, int], registers: dict[str, int], line: int) -> int | None:
    parts = split_args(args)
    if len(parts) == 1 and parts[0].lstrip().startswith("#"):
        return range_check(resolve_immediate(parts[0], symbols, registers), 0, 0xFF, "k8", line)
    return None


def match_a_register(args: str, symbols: dict[str, int], registers: dict[str, int], line: int) -> int | None:
    parts = split_args(args)
    if len(parts) == 2 and parts[0].upper() == "A" and not parts[1].lstrip().startswith("#"):
        return range_check(resolve_value(parts[1], symbols, registers), 0, 0xFF, "r8", line)
    return None


def match_register_a(args: str, symbols: dict[str, int], registers: dict[str, int], line: int) -> int | None:
    parts = split_args(args)
    if len(parts) == 2 and parts[1].upper() == "A":
        return range_check(resolve_value(parts[0], symbols, registers), 0, 0xFF, "r8", line)
    return None


def match_register(args: str, symbols: dict[str, int], registers: dict[str, int], line: int) -> int | None:
    parts = split_args(args)
    if len(parts) == 1 and not parts[0].lstrip().startswith("#"):
        return range_check(resolve_value(parts[0], symbols, registers), 0, 0xFF, "r8", line)
    return None


def encode_instruction(
    statement: Statement,
    variants: list[dict[str, Any]],
    symbols: dict[str, int],
    registers: dict[str, int],
) -> int:
    candidates = [item for item in variants if item.get("mnemonic", "").upper() == statement.op]
    for variant in candidates:
        operand_type = variant.get("operand_type")
        syntax = str(variant.get("asm_syntax", "")).upper()
        base = parse_int(str(variant.get("value_hex")))
        try:
            if operand_type is None:
                if not statement.args.strip():
                    return base
                continue
            if operand_type == "k8":
                value = match_a_immediate(statement.args, symbols, registers, statement.line)
                if value is None and "," not in syntax:
                    value = match_immediate(statement.args, symbols, registers, statement.line)
                if value is not None:
                    return base | value
                continue
            if operand_type == "r8":
                value = None
                if syntax.endswith(" A,R"):
                    value = match_a_register(statement.args, symbols, registers, statement.line)
                elif syntax.endswith(" R,A"):
                    value = match_register_a(statement.args, symbols, registers, statement.line)
                elif syntax.endswith(" R"):
                    value = match_register(statement.args, symbols, registers, statement.line)
                if value is not None:
                    return base | value
                continue
            if operand_type == "r8,b":
                parts = split_args(statement.args)
                if len(parts) != 2:
                    continue
                register = range_check(resolve_value(parts[0], symbols, registers), 0, 0xFF, "r8", statement.line)
                bit = range_check(resolve_value(parts[1], symbols, registers), 0, 7, "bit", statement.line)
                return base | (bit << 8) | register
            if operand_type == "k10":
                parts = split_args(statement.args)
                if len(parts) != 1:
                    continue
                target = range_check(resolve_value(parts[0], symbols, registers), 0, 0x03FF, "k10", statement.line)
                return base | target
        except CompileFailure:
            raise
    raise CompileFailure("unsupported_instruction", f"unsupported instruction form at line {statement.line}: {statement.op}")


def assemble_source(source: Path) -> Assembly:
    try:
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise CompileFailure("source_unreadable", f"cannot read ASM source: {exc}") from exc
    registers = load_registers()
    variants = load_instruction_variants()
    statements, symbols = first_pass(text, registers)
    words: dict[int, int] = {}
    for statement in statements:
        if statement.op in {"ORG", "END", "INCLUDE"}:
            continue
        if statement.kind in {"data", "raw"}:
            assemble_data(statement, symbols, registers, words)
            continue
        add_word(words, statement.address, encode_instruction(statement, variants, symbols, registers), statement.line)
    return Assembly(words=words, labels=symbols, source_sha256=sha256_file(source))


def image_words(words: dict[int, int]) -> list[int]:
    if not words:
        return []
    highest = max(words)
    return [words.get(address, 0xFFFF) for address in range(highest + 1)]


def intel_hex_record(address: int, record_type: int, data: bytes) -> str:
    count = len(data)
    payload = bytes([count, (address >> 8) & 0xFF, address & 0xFF, record_type, *data])
    checksum = ((~sum(payload) + 1) & 0xFF)
    return ":" + payload.hex().upper() + f"{checksum:02X}"


def write_artifacts(assembly: Assembly, artifact_path: Path) -> dict[str, Any]:
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    words = image_words(assembly.words)

    hex_bytes = bytearray()
    for word in words:
        hex_bytes.append((word >> 8) & 0xFF)
        hex_bytes.append(word & 0xFF)
    records = [
        intel_hex_record(offset, 0, bytes(hex_bytes[offset : offset + 16]))
        for offset in range(0, len(hex_bytes), 16)
    ]
    records.append(":00000001FF")
    artifact_path.write_text("\n".join(records) + "\n", encoding="ascii")

    bin_path = artifact_path.with_suffix(".bin")
    bin_bytes = bytearray()
    for word in words:
        bin_bytes.append(word & 0xFF)
        bin_bytes.append((word >> 8) & 0xFF)
    bin_path.write_bytes(bytes(bin_bytes))

    map_path = artifact_path.with_suffix(".map")
    map_lines = ["; HK64S825 builtin assembler map", "; symbol address"]
    for name, address in sorted(assembly.labels.items(), key=lambda item: (item[1], item[0])):
        map_lines.append(f"{name} 0x{address:04X}")
    map_path.write_text("\n".join(map_lines) + "\n", encoding="utf-8")

    return {
        "hex_path": str(artifact_path),
        "bin_path": str(bin_path),
        "map_path": str(map_path),
        "hex_sha256": sha256_file(artifact_path),
        "bin_sha256": sha256_file(bin_path),
        "map_sha256": sha256_file(map_path),
        "word_count": len(words),
        "highest_word": f"0x{max(assembly.words):04X}" if assembly.words else None,
    }


def compile_to_artifacts(source: Path, artifact_path: Path) -> dict[str, Any]:
    assembly = assemble_source(source)
    artifacts = write_artifacts(assembly, artifact_path)
    return {
        "status": "pass",
        "role": ROLE,
        "operation": "run",
        "tool_version": TOOL_VERSION,
        "toolchain": TOOLCHAIN,
        "source_sha256": assembly.source_sha256,
        "artifact_path": str(artifact_path),
        "artifact_sha256": sha256_file(artifact_path),
        "warnings": [],
        "artifacts": artifacts,
        "metrics": {
            "words": artifacts["word_count"],
            "highest_word": artifacts["highest_word"],
        },
    }


def run_probe(payload: dict[str, Any]) -> dict[str, Any]:
    chip = payload.get("chip", "HK64S825")
    if chip != "HK64S825":
        raise CompileFailure("unsupported_chip", f"unsupported chip: {chip}")
    with tempfile.TemporaryDirectory(prefix="hk64s825-builtin-probe-") as temp:
        root = Path(temp)
        source = root / "probe.asm"
        source.write_text(
            "; CHIP: HK64S825\n"
            "; 目的：内置编译器探测\n"
            "ORG 0x0000\n"
            "START:\n"
            "    NOP\n"
            "    CLRWDT\n"
            "    JMP START\n"
            "END\n",
            encoding="utf-8",
        )
        result = compile_to_artifacts(source, root / "firmware.hex")
    result["operation"] = "probe"
    return result


def run_compile(payload: dict[str, Any]) -> dict[str, Any]:
    source_value = payload.get("source_path")
    artifact_value = payload.get("artifact_path")
    if not isinstance(source_value, str) or not source_value:
        raise CompileFailure("missing_source_path", "compiler run payload is missing source_path")
    if not isinstance(artifact_value, str) or not artifact_value:
        raise CompileFailure("missing_artifact_path", "compiler run payload is missing artifact_path")
    source = Path(source_value).resolve()
    artifact_path = Path(artifact_value).resolve()
    if not source.is_file():
        raise CompileFailure("source_not_found", f"source_path does not exist: {source}")
    return compile_to_artifacts(source, artifact_path)


def failure_payload(exc: CompileFailure) -> dict[str, Any]:
    payload: dict[str, Any] = {"status": "fail", "role": ROLE, "code": exc.code, "error": exc.message}
    if exc.details is not None:
        payload["details"] = exc.details
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role")
    parser.add_argument("operation", choices=("probe", "run"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.role != ROLE:
            raise CompileFailure("wrong_role", f"builtin compiler only supports role={ROLE}")
        payload = read_json(args.input)
        result = run_probe(payload) if args.operation == "probe" else run_compile(payload)
    except CompileFailure as exc:
        write_json(args.output, failure_payload(exc))
        return 20
    except Exception as exc:  # pragma: no cover - final safety net
        write_json(
            args.output,
            {
                "status": "fail",
                "role": ROLE,
                "code": "builtin_internal_error",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return 20
    write_json(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
