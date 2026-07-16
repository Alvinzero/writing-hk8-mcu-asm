#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HK64S825 ASM source/layout static checker.

This checker is deliberately conservative: it proves a bounded set of source and
artifact invariants from the corporate rule package. It is not a replacement for
the company assembler, MAP/BIN audit, controlled flash, or hardware acceptance.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

try:
    from .asm_semantic_gates import (
        audit_counter_loops,
        audit_gpio_contract,
        audit_timing_contract,
        audit_unused_equ,
        load_instruction_effects,
    )
except ImportError:
    from asm_semantic_gates import (
        audit_counter_loops,
        audit_gpio_contract,
        audit_timing_contract,
        audit_unused_equ,
        load_instruction_effects,
    )

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROGRAM_MIN = 0x0000
PROGRAM_MAX = 0x03FF
INTERNAL_SFR_ALIASES = {
    "PA_PU": "PA_PPU",
    "PA_PD": "PA_PPD",
    "PA_OD": "PA_POD",
    "PA_OE": "PA_POE",
    "PB_PU": "PB_PPU",
    "PB_PD": "PB_PPD",
    "PB_OD": "PB_POD",
    "PB_OE": "PB_POE",
}
NO_WORD_DIRECTIVES = {"EQU", "INCLUDE", "END"}
TABLE_PAIR_RE = re.compile(
    r"\bTABLE_PAIR\s*:\s*([A-Za-z_.$?][\w.$?]*)\s*[, :]\s*([A-Za-z_.$?][\w.$?]*)",
    re.IGNORECASE,
)
LABEL_RE = re.compile(r"^\s*([A-Za-z_.$?][\w.$?]*)\s*:\s*(.*)$")
MAP_SYMBOL_RE = re.compile(r"^\s*(\S+)\s+0x([0-9A-Fa-f]+)\s+", re.MULTILINE)
NUMERIC_TARGET_RE = re.compile(r"^(?:0x[0-9A-Fa-f]+|[0-9A-Fa-f]+H|[0-9]+)\b", re.IGNORECASE)
MIXED_HEX_RE = re.compile(r"\b0x[0-9A-Fa-f]+H\b", re.IGNORECASE)
NUMBER_TOKEN_RE = re.compile(r"(?<![\w])(?:0x[0-9A-Fa-f]+|[0-9A-Fa-f]+H|[0-9]+)(?![\w])", re.IGNORECASE)
SYMBOL_TOKEN_RE = re.compile(r"(?<![\w.$?])([A-Za-z_.$?][\w.$?]*)(?![\w.$?])")
GPIO_CONFIG_REGISTER_RE = re.compile(r"\b(P[AB])_(PPU|PPD|POD|INS|IOS|PSL)\b", re.IGNORECASE)
GPIO_COMPLEX_CONTEXT_RE = re.compile(
    r"(I2C|OLED|SSD1306|数码|七段|7SEG|BOARD[_ -]?PROFILE|板级|经验证)",
    re.IGNORECASE,
)
DELAY_LABEL_RE = re.compile(r"(DELAY|WAIT)", re.IGNORECASE)
WDT_OFF_RE = re.compile(r"(WDT|看门狗).{0,20}(OFF|DISABLE|DISABLED|关闭|禁用|已关)", re.IGNORECASE)
WRITE_FIRST_OPERAND_OPS = {"MOV", "BSET", "BCLR", "BCPL"}


def load_context_json(path: Path | None, label: str) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} cannot be read: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def parse_number(text: str) -> int | None:
    value = text.strip().replace("_", "")
    if value.startswith("#"):
        value = value[1:].lstrip()
    try:
        if re.fullmatch(r"0x[0-9A-Fa-f]+", value, re.IGNORECASE):
            return int(value, 16)
        if re.fullmatch(r"[0-9A-Fa-f]+H", value, re.IGNORECASE):
            return int(value[:-1], 16)
        if re.fullmatch(r"[0-9]+", value):
            return int(value, 10)
    except ValueError:
        return None
    return None


def hexadecimal(value: int | None, width: int = 4) -> str | None:
    return None if value is None else f"0x{value:0{width}X}"


def make_finding(
    rule_id: str,
    severity: str,
    file: Path | str,
    line: int | None,
    evidence: str,
    risk: str,
    required_fix: str,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "file": str(file),
        "line": line,
        "evidence": evidence,
        "risk": risk,
        "required_fix": required_fix,
    }


def split_label(code: str) -> tuple[str | None, str]:
    match = LABEL_RE.match(code)
    return (match.group(1), match.group(2)) if match else (None, code)


def split_values(args: str) -> list[str]:
    return [part.strip() for part in args.split(",") if part.strip()]


def instruction_writes_a(instruction: dict[str, Any]) -> bool:
    if instruction["op"] != "MOV":
        return False
    return re.match(r"^\s*A\s*,", instruction["args"], re.IGNORECASE) is not None


def collect_sram_addresses(args: str) -> set[int]:
    addresses: set[int] = set()
    for match in NUMBER_TOKEN_RE.finditer(args):
        prefix = args[: match.start()].rstrip()
        if prefix.endswith("#"):
            continue
        value = parse_number(match.group(0))
        if value is not None and 0x80 <= value <= 0xBF:
            addresses.add(value)
    return addresses


def first_operand(args: str) -> str:
    return args.split(",", 1)[0].strip()


def occupy_words(
    occupied: dict[int, dict[str, Any]],
    ambiguous_addresses: set[int],
    start: int,
    words: int,
    path: Path,
    line: int,
    kind: str,
    findings: list[dict[str, Any]],
    instruction_index: int | None = None,
) -> None:
    for offset in range(words):
        address = start + offset
        previous = occupied.get(address)
        if previous is not None:
            ambiguous_addresses.add(address)
            findings.append(
                make_finding(
                    "HK-LAYOUT-004",
                    "BLOCKER",
                    path,
                    line,
                    f"word {hexadecimal(address)} is written by line {previous['line']} and line {line}",
                    "Overlapping ORG segments can silently replace code or table data.",
                    "Move or resize the segment so every program word has one owner, then rebuild and inspect MAP.",
                )
            )
        else:
            occupied[address] = {
                "line": line,
                "kind": kind,
                "instruction_index": instruction_index,
            }
        if not PROGRAM_MIN <= address <= PROGRAM_MAX:
            findings.append(
                make_finding(
                    "HK-LAYOUT-002",
                    "BLOCKER",
                    path,
                    line,
                    f"{kind} occupies out-of-range word {hexadecimal(address)}",
                    "HK64S825 program words are limited to 0x0000..0x03FF.",
                    "Reduce the image or correct ORG so the highest occupied word is at most 0x03FF.",
                )
            )


def analyze_file(path: Path, toolchain: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        findings.append(
            make_finding(
                "HK-SYN-001",
                "ERROR",
                path,
                None,
                f"source is not strict UTF-8: {exc}",
                "Encoding-dependent source can be parsed differently by tools and agents.",
                "Convert the source to UTF-8 and keep END explicit.",
            )
        )
        text = path.read_text(encoding="utf-8-sig", errors="replace")

    lines = text.splitlines()
    address = 0
    occupied: dict[int, dict[str, Any]] = {}
    ambiguous_word_addresses: set[int] = set()
    labels: dict[str, dict[str, Any]] = {}
    duplicate_label_names: set[str] = set()
    table_pairs: list[dict[str, str]] = []
    instructions: list[dict[str, Any]] = []
    equ_symbols: dict[str, dict[str, Any]] = {}
    symbol_references: Counter[str] = Counter()
    db_directives = 0
    db_source_bytes = 0
    sram_addresses: set[int] = set()
    gpio_config_writes: dict[str, dict[str, int]] = {}

    for line_number, raw in enumerate(lines, 1):
        for match in TABLE_PAIR_RE.finditer(raw):
            table_pairs.append(
                {
                    "table": match.group(1),
                    "sender": match.group(2),
                    "source": "comment",
                    "file": str(path),
                    "line": line_number,
                }
            )

        code = raw.partition(";")[0].strip()
        if not code:
            continue

        if MIXED_HEX_RE.search(code):
            findings.append(
                make_finding(
                    "HK-SYN-002",
                    "ERROR",
                    path,
                    line_number,
                    f"mixed hexadecimal literal in: {code}",
                    "Combining 0x prefix and H suffix is ambiguous and rejected by parts of the toolchain.",
                    "Use either 0x12 or 12H, never 0x12H.",
                )
            )

        for internal, official in INTERNAL_SFR_ALIASES.items():
            if re.search(rf"\b{re.escape(internal)}\b", code, re.IGNORECASE):
                findings.append(
                    make_finding(
                        "HK-MEM-001",
                        "ERROR",
                        path,
                        line_number,
                        f"compiler-internal SFR alias {internal} is used",
                        "Internal Python aliases are not the company REG825.INC source contract.",
                        f"Replace {internal} with official name {official}.",
                    )
                )

        label, rest = split_label(code)
        if label:
            label_key = label.upper()
            if label_key in labels:
                duplicate_label_names.add(label_key)
            labels[label_key] = {"name": label, "address": address, "line": line_number, "file": str(path)}
        rest = rest.strip()
        if not rest:
            continue

        equ_match = re.match(
            r"^([A-Za-z_.$?][\w.$?]*)\s+EQU\b\s*(.*)$",
            rest,
            re.IGNORECASE,
        )
        if equ_match:
            name = equ_match.group(1)
            equ_symbols[name.upper()] = {
                "name": name,
                "value": parse_number(equ_match.group(2)),
                "line": line_number,
                "uses": 0,
            }
            continue

        parts = rest.split(None, 1)
        op = parts[0].upper()
        args = parts[1].strip() if len(parts) > 1 else ""

        if op in NO_WORD_DIRECTIVES:
            continue

        symbol_references.update(match.group(1).upper() for match in SYMBOL_TOKEN_RE.finditer(args))

        if op == "ORG":
            value = parse_number(args.split(",", 1)[0])
            if value is None:
                findings.append(
                    make_finding(
                        "HK-SYN-005",
                        "ERROR",
                        path,
                        line_number,
                        f"ORG operand cannot be resolved: {args}",
                        "Layout and capacity cannot be proven from an unresolved ORG.",
                        "Use an explicit in-range word address or a resolvable EQU symbol.",
                    )
                )
            else:
                address = value
                if not PROGRAM_MIN <= value <= PROGRAM_MAX:
                    findings.append(
                        make_finding(
                            "HK-LAYOUT-002",
                            "BLOCKER",
                            path,
                            line_number,
                            f"ORG sets out-of-range word address {hexadecimal(value)}",
                            "HK64S825 program words are limited to 0x0000..0x03FF.",
                            "Correct ORG to an in-range word address and re-audit the complete image.",
                        )
                    )
            continue

        if op == "DB":
            db_directives += 1
            tokens = split_values(args)
            db_source_bytes += len(tokens)
            if len(tokens) % 2:
                findings.append(
                    make_finding(
                        "HK-TABLE-007",
                        "ERROR",
                        path,
                        line_number,
                        f"DB directive contains {len(tokens)} source byte(s)",
                        "The company IDE odd-byte padding rule is still OPEN.",
                        "Use an even number of bytes; add explicit 00H padding and keep logical length separate if needed.",
                    )
                )
            for token in tokens:
                value = parse_number(token)
                if value is None or not 0 <= value <= 0xFF:
                    findings.append(
                        make_finding(
                            "HK-SYN-005",
                            "ERROR",
                            path,
                            line_number,
                            f"DB byte is invalid or unresolved: {token}",
                            "The emitted table size and bytes cannot be audited.",
                            "Use explicit byte literals in range 0x00..0xFF.",
                        )
                    )
            word_count = math.ceil(len(tokens) / 2)
            occupy_words(
                occupied,
                ambiguous_word_addresses,
                address,
                word_count,
                path,
                line_number,
                "DB",
                findings,
            )
            address += word_count
            continue

        if op == "DW":
            tokens = split_values(args)
            for token in tokens:
                value = parse_number(token)
                if value is None or not 0 <= value <= 0xFFFF:
                    findings.append(
                        make_finding(
                            "HK-SYN-005",
                            "ERROR",
                            path,
                            line_number,
                            f"DW word is invalid or unresolved: {token}",
                            "The emitted program word cannot be audited.",
                            "Use an explicit word literal in range 0x0000..0xFFFF.",
                        )
                    )
            occupy_words(
                occupied,
                ambiguous_word_addresses,
                address,
                len(tokens),
                path,
                line_number,
                "DW",
                findings,
            )
            address += len(tokens)
            continue

        if re.fullmatch(r"(?:0x[0-9A-Fa-f]+|[0-9A-Fa-f]+H)", op, re.IGNORECASE):
            occupy_words(
                occupied,
                ambiguous_word_addresses,
                address,
                1,
                path,
                line_number,
                "raw-word",
                findings,
            )
            address += 1
            continue

        instruction = {
            "op": op,
            "args": args,
            "line": line_number,
            "address": address,
            "file": str(path),
            "source": rest,
        }
        instructions.append(instruction)
        sram_addresses.update(collect_sram_addresses(args))

        if op in WRITE_FIRST_OPERAND_OPS:
            destination = first_operand(args)
            match = GPIO_CONFIG_REGISTER_RE.fullmatch(destination)
            if match:
                port = match.group(1).upper()
                register = f"{port}_{match.group(2).upper()}"
                gpio_config_writes.setdefault(port, {}).setdefault(register, line_number)

        if op in {"JMP", "CALL"} and NUMERIC_TARGET_RE.match(args):
            findings.append(
                make_finding(
                    "HK-SYN-004",
                    "ERROR",
                    path,
                    line_number,
                    f"direct numeric {op} target: {args}",
                    "The current Python lexer rejects some direct numeric targets and jump range warnings can truncate addresses.",
                    "Use a label or an EQU symbol and treat any jump warning as an error.",
                )
            )

        if op == "RET" and re.match(r"^\s*A\s*,\s*#", args, re.IGNORECASE):
            findings.append(
                make_finding(
                    "HK-SYN-008",
                    "BLOCKER",
                    path,
                    line_number,
                    f"restricted instruction form: {rest}",
                    "RET A,#K compiles on one path but its production-silicon semantics are OPEN.",
                    "Replace it with verified instructions or obtain a versioned hardware/FAE confirmation.",
                )
            )

        if op in {"CPL", "CPLR"}:
            findings.append(
                make_finding(
                    "HK-SYN-009",
                    "ERROR",
                    path,
                    line_number,
                    f"restricted semantic instruction: {rest}",
                    "The exact complement/write-back semantics are not hardware-confirmed in this baseline.",
                    "Avoid it in critical logic or attach new versioned E1 evidence.",
                )
            )

        if op in {"IDLE", "STOP"}:
            findings.append(
                make_finding(
                    "HK-SYN-010",
                    "ERROR",
                    path,
                    line_number,
                    f"power-mode instruction requires board proof: {op}",
                    "Wake sources, WDT, clock and peripheral states are board-specific.",
                    "Add a board-specific power-mode test and acceptance evidence before delivery.",
                )
            )

        occupy_words(
            occupied,
            ambiguous_word_addresses,
            address,
            1,
            path,
            line_number,
            op,
            findings,
            instruction_index=len(instructions) - 1,
        )
        address += 1

    has_clrwdt = any(instruction["op"] == "CLRWDT" for instruction in instructions)
    wdt_declared_off = WDT_OFF_RE.search(text) is not None
    delay_labels = [
        info
        for key, info in labels.items()
        if DELAY_LABEL_RE.search(key) is not None
    ]
    if delay_labels and not has_clrwdt and not wdt_declared_off:
        first_delay = min(delay_labels, key=lambda item: item["line"])
        findings.append(
            make_finding(
                "HK-WDT-001",
                "BLOCKER",
                path,
                first_delay["line"],
                f"delay/wait routine {first_delay['name']} has no CLRWDT in source",
                "When WDT is enabled or unknown, long busy-wait loops can reset the MCU before the visible GPIO state stabilizes.",
                "Insert CLRWDT inside the busy-wait cadence, or document an explicit board/profile WDT-off configuration.",
            )
        )

    if not GPIO_COMPLEX_CONTEXT_RE.search(text):
        for port, writes in sorted(gpio_config_writes.items()):
            if len(writes) < 4:
                continue
            first_line = min(writes.values())
            findings.append(
                make_finding(
                    "HK-GPIO-INIT-001",
                    "WARNING",
                    path,
                    first_line,
                    f"{port} writes {len(writes)} GPIO configuration registers: {', '.join(sorted(writes))}",
                    "Simple LED/GPIO code that sweeps pull-up, pull-down, open-drain, input and special-function registers is harder to audit and can disturb unrelated pins.",
                    "For simple LED/GPIO, write only the task-required PIO/POE bits; configure PPU/PPD/POD/INS/IOS/PSL only when the board profile requires that electrical property.",
                )
            )

    if db_directives and toolchain == "python_source_module_cli":
        first_db_line = next(
            (
                index
                for index, raw in enumerate(lines, 1)
                if re.match(r"^\s*(?:[A-Za-z_.$?][\w.$?]*\s*:\s*)?DB\b", raw.partition(";")[0], re.IGNORECASE)
            ),
            None,
        )
        findings.append(
            make_finding(
                "HK-TOOLCHAIN-DB-001",
                "BLOCKER",
                path,
                first_db_line,
                f"source contains {db_directives} DB directive(s) but toolchain is python_source_module_cli",
                "This CLI can report success while omitting DB words from BIN/HEX/MAP.",
                "Build DB sources with the verified company IDE and audit MAP/BIN/HEX, or fix and re-qualify the CLI first.",
            )
        )

    for index, instruction in enumerate(instructions):
        if instruction["op"] != "TABH":
            continue
        previous = instructions[index - 1] if index > 0 else None
        if previous is None or not instruction_writes_a(previous):
            findings.append(
                make_finding(
                    "HK-TABLE-004",
                    "BLOCKER",
                    path,
                    instruction["line"],
                    "TABH is not immediately preceded by MOV A,<same table index>",
                    "TABL and intervening consumer calls can clobber A, causing TABH to read a different word.",
                    "Reload A from the table index immediately before TABH.",
                )
            )
        seen_tabl = False
        for candidate in reversed(instructions[:index]):
            if candidate["op"] in {"RET", "RETI"}:
                break
            if candidate["op"] == "TABL":
                seen_tabl = True
                break
            if candidate["op"] == "TABH":
                break
        if not seen_tabl:
            findings.append(
                make_finding(
                    "HK-TABLE-004",
                    "BLOCKER",
                    path,
                    instruction["line"],
                    "TABH has no preceding TABL in the current routine",
                    "DB word bytes must be consumed in TABL then TABH order.",
                    "Read and consume TABL first, reload A, then read and consume TABH.",
                )
            )

    for name, symbol in equ_symbols.items():
        symbol["uses"] = symbol_references[name]

    highest = max(occupied) if occupied else None
    span_words = highest + 1 if highest is not None and highest >= 0 else 0
    occupied_nonnegative = sum(1 for value in occupied if 0 <= value <= (highest if highest is not None else -1))
    layout = {
        "program_range_words": ["0x0000", "0x03FF"],
        "occupied_words": len(occupied),
        "highest_written_word": hexadecimal(highest),
        "span_words": span_words,
        "image_bytes": span_words * 2,
        "hole_words": max(0, span_words - occupied_nonnegative),
        "overlap_findings": sum(1 for item in findings if item["rule_id"] == "HK-LAYOUT-004"),
        "out_of_range_findings": sum(1 for item in findings if item["rule_id"] == "HK-LAYOUT-002"),
    }
    result = {
        "path": str(path),
        "line_count": len(lines),
        "db": {
            "directive_count": db_directives,
            "source_byte_count": db_source_bytes,
            "estimated_word_count": sum(1 for info in occupied.values() if info["kind"] == "DB"),
        },
        "uses_tabl": any(item["op"] == "TABL" for item in instructions),
        "uses_tabh": any(item["op"] == "TABH" for item in instructions),
        "sram_addresses": [f"0x{value:02X}" for value in sorted(sram_addresses)],
        "labels": labels,
        "_duplicate_label_names": duplicate_label_names,
        "table_pair_declarations": table_pairs,
        "_instructions": instructions,
        "_equ_symbols": equ_symbols,
        "_word_owners": occupied,
        "_ambiguous_word_addresses": ambiguous_word_addresses,
        "layout": layout,
    }
    return result, findings


def parse_map(paths: Iterable[Path]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    symbols: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="strict")
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(
                make_finding(
                    "HK-LAYOUT-008",
                    "ERROR",
                    path,
                    None,
                    f"MAP cannot be read: {exc}",
                    "Final label addresses and table pages cannot be audited.",
                    "Provide a readable MAP generated by the target company IDE build.",
                )
            )
            continue
        for match in MAP_SYMBOL_RE.finditer(text):
            name = match.group(1)
            symbols[name.upper()] = {
                "name": name,
                "address": int(match.group(2), 16),
                "map": str(path),
            }
    return symbols, findings


def parse_cli_pair(value: str) -> tuple[str, str]:
    match = re.fullmatch(
        r"\s*([A-Za-z_.$?][\w.$?]*)\s*[: ,]\s*([A-Za-z_.$?][\w.$?]*)\s*",
        value,
    )
    if not match:
        raise argparse.ArgumentTypeError("table pair must be TABLE:SENDER")
    return match.group(1), match.group(2)


def audit_table_pairs(
    files: list[dict[str, Any]],
    cli_pairs: list[tuple[str, str]],
    map_paths: list[Path],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    declarations: list[dict[str, Any]] = []
    labels: dict[str, dict[str, Any]] = {}
    for file_result in files:
        labels.update(file_result["labels"])
        declarations.extend(file_result["table_pair_declarations"])
    for table, sender in cli_pairs:
        declarations.append({"table": table, "sender": sender, "source": "cli", "file": None, "line": None})

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for declaration in declarations:
        key = (declaration["table"].upper(), declaration["sender"].upper())
        if key not in seen:
            seen.add(key)
            unique.append(declaration)

    has_table_instructions = any(item["uses_tabl"] or item["uses_tabh"] for item in files)
    has_db = any(item["db"]["directive_count"] for item in files)
    if has_db and has_table_instructions and not unique:
        first = next(item for item in files if item["db"]["directive_count"])
        findings.append(
            make_finding(
                "HK-TABLE-005",
                "BLOCKER",
                first["path"],
                None,
                "DB and TABL/TABH are present but no TABLE_PAIR declaration was supplied",
                "The table word and executing table-read routine cannot be paired for same-page proof.",
                "Add '; TABLE_PAIR: TABLE,SENDER' or --table-pair TABLE:SENDER, then provide the final MAP.",
            )
        )

    map_symbols, map_findings = parse_map(map_paths)
    findings.extend(map_findings)
    pair_results: list[dict[str, Any]] = []
    for declaration in unique:
        table_key = declaration["table"].upper()
        sender_key = declaration["sender"].upper()
        evidence = "map" if map_paths else "source-estimate"
        source = map_symbols if map_paths else labels
        table_symbol = source.get(table_key)
        sender_symbol = source.get(sender_key)
        source_sender_symbol = labels.get(sender_key)
        sender_file = next(
            (
                item
                for item in files
                if source_sender_symbol is not None
                and item["path"] == source_sender_symbol["file"]
            ),
            None,
        )
        source_table_reads: list[dict[str, Any]] = []
        if sender_file is not None and source_sender_symbol is not None:
            for instruction in sender_file["_instructions"]:
                if instruction["address"] < source_sender_symbol["address"]:
                    continue
                if instruction["op"] in {"TABL", "TABH"}:
                    source_table_reads.append(instruction)
                if instruction["op"] in {"RET", "RETI"}:
                    break
        if map_paths and sender_symbol is not None and source_sender_symbol is not None:
            table_read_addresses = [
                sender_symbol["address"]
                + instruction["address"]
                - source_sender_symbol["address"]
                for instruction in source_table_reads
            ]
        else:
            table_read_addresses = [instruction["address"] for instruction in source_table_reads]
        pair_result = {
            "table": declaration["table"],
            "sender": declaration["sender"],
            "declaration_source": declaration["source"],
            "evidence": evidence,
            "table_address": hexadecimal(table_symbol["address"]) if table_symbol else None,
            "sender_address": hexadecimal(sender_symbol["address"]) if sender_symbol else None,
            "table_read_addresses": [hexadecimal(address) for address in table_read_addresses],
            "same_256_word_page": None,
        }
        if table_symbol is None or sender_symbol is None or source_sender_symbol is None:
            missing = [
                name
                for name, value in (
                    (declaration["table"], table_symbol),
                    (declaration["sender"], sender_symbol),
                    (f"source:{declaration['sender']}", source_sender_symbol),
                )
                if value is None
            ]
            findings.append(
                make_finding(
                    "HK-LAYOUT-008",
                    "ERROR",
                    declaration.get("file") or (map_paths[0] if map_paths else "<inputs>"),
                    declaration.get("line"),
                    f"table pair symbol(s) not found in {evidence}: {', '.join(missing)}",
                    "Same-page table access cannot be proven without both final addresses.",
                    "Fix label names and regenerate/provide the target MAP.",
                )
            )
        elif not table_read_addresses:
            findings.append(
                make_finding(
                    "HK-TABLE-005",
                    "BLOCKER",
                    declaration.get("file") or (map_paths[0] if map_paths else "<inputs>"),
                    declaration.get("line"),
                    f"sender {declaration['sender']} contains no TABL/TABH before RET",
                    "A sender label alone does not identify the executing table-read instruction page.",
                    "Pair the table with the routine that actually executes TABL/TABH.",
                )
            )
        else:
            same_page = all(
                (table_symbol["address"] >> 8) == (address >> 8)
                for address in table_read_addresses
            )
            pair_result["same_256_word_page"] = same_page
            if not same_page:
                findings.append(
                    make_finding(
                        "HK-TABLE-005",
                        "BLOCKER",
                        declaration.get("file") or (map_paths[0] if map_paths else "<inputs>"),
                        declaration.get("line"),
                        f"{declaration['table']}={hexadecimal(table_symbol['address'])}, "
                        f"TABL/TABH={','.join(hexadecimal(address) for address in table_read_addresses)} "
                        f"are on different 256-word pages ({evidence})",
                        "TABL/TABH use the executing instruction page and will read the wrong ROM word.",
                        "Move/split the table and sender so address>>8 matches, then verify the final MAP.",
                    )
                )
        pair_results.append(pair_result)

    if unique and not map_paths:
        findings.append(
            make_finding(
                "HK-LAYOUT-008",
                "WARNING",
                unique[0].get("file") or "<inputs>",
                unique[0].get("line"),
                "table pairs were checked only from source-estimated addresses; no final MAP was supplied",
                "Assembler layout changes can invalidate source-only page estimates.",
                "After the target build, rerun with --map and retain the audited MAP/hash.",
            )
        )
    return pair_results, findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check HK64S825 ASM source/layout rules; this does not replace compiler or hardware acceptance."
    )
    parser.add_argument("asm", nargs="+", type=Path, help="ASM source file(s) to check")
    parser.add_argument(
        "--toolchain",
        required=True,
        choices=["company_ide", "python_source_module_cli", "simulator", "builtin_compiler"],
        help="target build/execution toolchain",
    )
    parser.add_argument("--map", dest="maps", action="append", type=Path, default=[], help="final MAP file; repeat if needed")
    parser.add_argument(
        "--table-pair",
        action="append",
        type=parse_cli_pair,
        default=[],
        metavar="TABLE:SENDER",
        help="declare a table and its same-page sender; repeat for each pair",
    )
    parser.add_argument("--request", type=Path, help="structured generation request JSON")
    parser.add_argument("--profile", type=Path, help="structured chip profile JSON")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--strict-warnings", action="store_true", help="return exit code 1 when warnings exist")
    return parser


def render_text(payload: dict[str, Any]) -> str:
    lines = [
        "HK64S825 ASM static check",
        f"toolchain: {payload['toolchain']}",
        f"files: {len(payload['files'])}",
    ]
    for finding in payload["findings"]:
        location = finding["file"]
        if finding["line"] is not None:
            location += f":{finding['line']}"
        lines.append(f"[{finding['severity']}] {finding['rule_id']} {location} - {finding['evidence']}")
    summary = payload["summary"]
    lines.append(
        f"summary: {summary['blockers']} blocker(s), {summary['errors']} error(s), "
        f"{summary['warnings']} warning(s); exit={summary['exit_code']}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    files: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    spec_root = Path(__file__).resolve().parent.parent
    instruction_reference = spec_root / "rules" / "instruction-reference.json"
    try:
        instruction_effects = load_instruction_effects(instruction_reference)
    except ValueError as exc:
        findings.append(
            make_finding(
                "HK-AI-003",
                "ERROR",
                instruction_reference,
                None,
                str(exc),
                "The checker cannot audit instruction write-back or skip semantics without "
                "the packaged instruction reference.",
                "Restore a valid packaged rules/instruction-reference.json and rerun the checker.",
            )
        )
        instruction_effects = None
    request_context = None
    profile_context = None
    for path, label in ((args.request, "request"), (args.profile, "profile")):
        try:
            context = load_context_json(path, label)
        except ValueError as exc:
            findings.append(
                make_finding(
                    "HK-AI-003",
                    "ERROR",
                    path or f"<{label}>",
                    None,
                    str(exc),
                    "The checker cannot bind source findings to the structured generation contract.",
                    f"Provide a readable {label} JSON file whose root is an object.",
                )
            )
            context = None
        if label == "request":
            request_context = context
        else:
            profile_context = context
    if request_context is not None and profile_context is not None:
        request_chip = request_context.get("chip")
        profile_chip = profile_context.get("chip")
        aliases = profile_context.get("aliases", [])
        supported_aliases = (
            {alias for alias in aliases if isinstance(alias, str)}
            if isinstance(aliases, list)
            else set()
        )
        supported_chips = set(supported_aliases)
        if isinstance(profile_chip, str):
            supported_chips.add(profile_chip)
        if not isinstance(request_chip, str) or request_chip not in supported_chips:
            findings.append(
                make_finding(
                    "HK-AI-003",
                    "ERROR",
                    args.request or "<request>",
                    None,
                    f"request chip {request_chip!r} does not match profile chip {profile_chip!r} "
                    f"or aliases {sorted(supported_aliases)!r}",
                    "The checker could apply chip-specific rules to a request for a different target.",
                    "Use a request chip that matches the profile chip or one of its declared aliases.",
                )
            )
    for path in args.asm:
        if not path.is_file():
            findings.append(
                make_finding(
                    "HK-GOV-003",
                    "ERROR",
                    path,
                    None,
                    "ASM input does not exist or is not a file",
                    "No source was available for review.",
                    "Provide an existing ASM path.",
                )
            )
            continue
        file_result, file_findings = analyze_file(path.resolve(), args.toolchain)
        files.append(file_result)
        findings.extend(file_findings)

    table_pairs, pair_findings = audit_table_pairs(files, args.table_pair, [path.resolve() for path in args.maps])
    findings.extend(pair_findings)
    for file_result in files:
        if instruction_effects is not None:
            findings.extend(audit_counter_loops(file_result, instruction_effects))
        if request_context is not None:
            findings.extend(audit_gpio_contract(file_result, request_context))
        findings.extend(audit_unused_equ(file_result))
    timing_audits, timing_findings = audit_timing_contract(
        files,
        request_context,
        profile_context,
        instruction_effects,
    )
    findings.extend(timing_findings)
    for file_result in files:
        file_result.pop("_instructions", None)
        file_result.pop("_equ_symbols", None)
        file_result.pop("_word_owners", None)
        file_result.pop("_ambiguous_word_addresses", None)
        file_result.pop("_duplicate_label_names", None)
    severity_counts = Counter(item["severity"] for item in findings)
    if severity_counts["BLOCKER"] or severity_counts["ERROR"]:
        exit_code = 2
    elif severity_counts["WARNING"] and args.strict_warnings:
        exit_code = 1
    else:
        exit_code = 0
    chip = None
    for context in (profile_context, request_context):
        if context is not None and isinstance(context.get("chip"), str):
            chip = context["chip"]
            break
    payload = {
        "schema_version": "1.0.0",
        "toolchain": args.toolchain,
        "contract_context": {
            "request_loaded": request_context is not None,
            "profile_loaded": profile_context is not None,
            "chip": chip,
        },
        "inputs": [str(path.resolve()) for path in args.asm],
        "map_files": [str(path.resolve()) for path in args.maps],
        "files": files,
        "table_pairs": table_pairs,
        "semantic_audits": {"timing": timing_audits},
        "findings": findings,
        "summary": {
            "blockers": severity_counts["BLOCKER"],
            "errors": severity_counts["ERROR"],
            "warnings": severity_counts["WARNING"],
            "info": severity_counts["INFO"],
            "exit_code": exit_code,
        },
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(payload))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
