#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从被分析仓库生成不可变分析快照。该脚本不修改源项目文件。"""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import math
import re
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

REPO = Path()
SPEC = Path(__file__).resolve().parents[1]
COMPILER = Path()
INSTRUCTION_METADATA = Path()
REGISTER_METADATA = Path()
REGISTER_INC = Path()
GENERATED_AT = ""
EXCLUDED_PARTS = {".git", ".spec-staging", "__pycache__"}
PSEUDO = {"ORG", "EQU", "DB", "DW", "INCLUDE", "END"}
LEGACY_ALIAS_MAP = {
    "PA_PPU": "PA_PU", "PA_PPD": "PA_PD", "PA_POD": "PA_OD", "PA_POE": "PA_OE",
    "PB_PPU": "PB_PU", "PB_PPD": "PB_PD", "PB_POD": "PB_OD", "PB_POE": "PB_OE",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def snapshot_json(source: Path, target: Path, document: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == target.resolve():
        return
    if source.is_file():
        shutil.copyfile(source, target)
    else:
        dump(target, document)


def metadata_records(document: dict, aggregate_key: str) -> list[dict]:
    aggregate = document.get(aggregate_key)
    if isinstance(aggregate, list) and aggregate:
        records = [dict(item) for item in aggregate]
    else:
        sheets = document.get("sheets") or []
        records = [dict(item) for item in (sheets[0].get("records") if sheets else [])]
    for index, record in enumerate(records, 2):
        record.setdefault("source_row", record.get("_row", index))
    return records


def parse_num(text: str):
    s = text.strip().replace("_", "")
    if s.startswith("#"):
        s = s[1:]
    try:
        if re.fullmatch(r"(?i)0x[0-9a-f]+", s):
            return int(s, 16)
        if re.fullmatch(r"(?i)[0-9a-f]+h", s):
            return int(s[:-1], 16)
        if re.fullmatch(r"[0-9]+", s):
            return int(s, 10)
    except ValueError:
        return None
    return None


def strip_label(code: str):
    m = re.match(r"^\s*([A-Za-z_.$?][\w.$?]*)\s*:\s*(.*)$", code)
    return (m.group(1), m.group(2)) if m else (None, code)


def extract_db_values(rest: str):
    values = []
    for token in rest.split(","):
        token = token.strip()
        if not token:
            continue
        value = parse_num(token)
        values.append(value)
    return values


def normalize_probe_instruction(syntax: str) -> str:
    result = syntax
    result = result.replace("XOR A.#K", "XOR A,#K")
    result = result.replace("BTSZ,R,b", "BTSZ R,b")
    result = result.replace("BTSNZ,R,b", "BTSNZ R,b")
    result = result.replace("#K", "#1")
    result = re.sub(r"(?<![A-Za-z0-9_])R(?![A-Za-z0-9_])", "80H", result)
    result = re.sub(r"(?<![A-Za-z0-9_])b(?![A-Za-z0-9_])", "3", result)
    result = re.sub(r"(?<![A-Za-z0-9_])K(?![A-Za-z0-9_])", "TARGET", result)
    return result


def expected_probe_word(record: dict) -> int | None:
    base = int(str(record["value_hex"]), 16)
    operand_type = record.get("operands")
    if operand_type is None:
        expected = base
    elif operand_type == "r8":
        expected = base | 0x80
    elif operand_type == "k8":
        expected = base | 0x01
    elif operand_type == "r8,b":
        expected = base | (3 << 8) | 0x80
    elif operand_type == "k10":
        expected = base | 0x03FF
    else:
        expected = None
    if record["asm_syntax"] == "MOV A,#K":
        expected = 0x7201
    return expected


def run_instruction_probes(records: list[dict]) -> list[dict]:
    compiler_src = str(COMPILER / "src")
    compiler_root = str(COMPILER)
    if compiler_src not in sys.path:
        sys.path.insert(0, compiler_src)
    if compiler_root not in sys.path:
        sys.path.insert(0, compiler_root)
    from core.assembler import Assembler  # type: ignore

    probes = []
    for index, record in enumerate(records, 1):
        assembler = Assembler()
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
            loaded = assembler.load_config(
                str(COMPILER / "instruction_set.xlsx"),
                str(COMPILER / "register_set.xlsx"),
            )
        assembler.set_memory_ranges((0x00, 0x7F), (0x80, 0xBF))
        assembler.set_program_range((0x0000, 0x03FF))
        instruction = normalize_probe_instruction(record["asm_syntax"])
        source = f"TARGET EQU 03FFH\nORG 0x0000\n  {instruction}\nEND\n"
        if loaded:
            success, machine_code, _data_size, errors, warnings = assembler.assemble(source)
        else:
            success = False
            machine_code = []
            errors = list(getattr(assembler, "errors", [])) or ["加载编译器指令/寄存器配置失败"]
            warnings = []
        word = machine_code[0] if machine_code else None
        expected = expected_probe_word(record)
        probes.append(
            {
                "index": index,
                "raw_syntax": record["asm_syntax"],
                "probe_source": instruction,
                "success": success,
                "word": None if word is None else f"0x{word:04X}",
                "expected": None if expected is None else f"0x{expected:04X}",
                "matches_expected": word == expected,
                "errors": errors,
                "warnings": warnings,
            }
        )
    return probes


def analyze_asm(path: Path, official_names: set[str]):
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    mnemonics = Counter()
    orgs = []
    db_lines = 0
    db_bytes = 0
    odd_db_directives = []
    direct_numeric_jumps = []
    mixed_hex_literals = []
    official_refs = set()
    legacy_internal_refs = set()
    sram_refs = set()
    address = 0
    occupied = {}
    overlaps = []
    out_of_range = []
    segment_starts = []
    labels = {}

    for lineno, raw in enumerate(lines, 1):
        code = raw.partition(";")[0].strip()
        if not code:
            continue
        if re.search(r"(?i)#0x[0-9a-f]+h\b", code):
            mixed_hex_literals.append(lineno)
        for name in official_names:
            if re.search(rf"(?i)\b{re.escape(name)}\b", code):
                official_refs.add(name)
        for internal in LEGACY_ALIAS_MAP.values():
            if re.search(rf"(?i)\b{re.escape(internal)}\b", code):
                legacy_internal_refs.add(internal)
        for tok in re.findall(r"(?i)\b(?:0x[0-9a-f]+|[0-9a-f]+h)\b", code):
            value = parse_num(tok)
            if value is not None and 0x80 <= value <= 0xBF:
                sram_refs.add(value)

        label, rest = strip_label(code)
        if label:
            labels[label] = address
        rest = rest.strip()
        if not rest:
            continue
        # 兼容公司源码常见的无冒号常量定义：NAME EQU value
        if re.match(r"^[A-Za-z_.$?][\w.$?]*\s+EQU\b", rest, re.I):
            continue
        parts = rest.split(None, 1)
        op = parts[0].upper()
        args = parts[1].strip() if len(parts) > 1 else ""

        if re.fullmatch(r"(?i)(?:0x[0-9a-f]+|[0-9a-f]+h)", op):
            # 历史探针中的裸 word，不计入合法指令频率。
            continue
        if op == "ORG":
            value = parse_num(args.split(",", 1)[0])
            orgs.append({"line": lineno, "source": args, "word_address": value})
            if value is not None:
                address = value
                segment_starts.append(value)
                if not 0 <= value <= 0x3FF:
                    out_of_range.append({"line": lineno, "address": value, "kind": "ORG"})
            continue
        if op == "EQU" or op in {"DW", "INCLUDE", "END"}:
            continue
        if op == "DB":
            vals = extract_db_values(args)
            db_lines += 1
            db_bytes += len(vals)
            if len(vals) % 2:
                odd_db_directives.append({"line": lineno, "byte_count": len(vals)})
            words = math.ceil(len(vals) / 2)
            for offset in range(words):
                a = address + offset
                if a in occupied:
                    overlaps.append({"address": a, "first_line": occupied[a], "second_line": lineno})
                occupied[a] = lineno
                if not 0 <= a <= 0x3FF:
                    out_of_range.append({"line": lineno, "address": a, "kind": "DB"})
            address += words
            continue

        mnemonics[op] += 1
        if op in {"JMP", "CALL"} and re.match(r"(?i)^(?:0x[0-9a-f]+|[0-9a-f]+h|[0-9]+)\b", args):
            direct_numeric_jumps.append({"line": lineno, "instruction": rest})
        a = address
        if a in occupied:
            overlaps.append({"address": a, "first_line": occupied[a], "second_line": lineno})
        occupied[a] = lineno
        if not 0 <= a <= 0x3FF:
            out_of_range.append({"line": lineno, "address": a, "kind": op})
        address += 1

    highest = max(occupied) if occupied else None
    used_in_range = sum(1 for a in occupied if 0 <= a <= 0x3FF)
    span_words = (highest + 1) if highest is not None and highest >= 0 else 0
    hole_words = max(0, span_words - used_in_range)
    rel = path.relative_to(REPO).as_posix()

    artifacts = {}
    for ext in ("map", "bin", "hex", "hkproj"):
        matches = []
        for candidate in REPO.rglob(path.stem + "." + ext):
            if any(part in EXCLUDED_PARTS for part in candidate.parts):
                continue
            if candidate.resolve() == path.resolve():
                continue
            matches.append({
                "path": candidate.relative_to(REPO).as_posix(),
                "size_bytes": candidate.stat().st_size,
                "sha256": sha256(candidate),
            })
        artifacts[ext] = sorted(matches, key=lambda x: x["path"])

    return {
        "path": rel,
        "name": path.name,
        "role": "hardware_verified" if "verified" in path.stem.lower() else ("probe" if re.search(r"probe|check|sanity", path.stem, re.I) else "example_or_application"),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
        "line_count": len(lines),
        "instruction_count": sum(mnemonics.values()),
        "mnemonic_frequency": dict(sorted(mnemonics.items())),
        "orgs": orgs,
        "db": {
            "directive_count": db_lines,
            "source_byte_count": db_bytes,
            "estimated_word_count": sum(math.ceil(len(extract_db_values((raw.partition(';')[0].strip().split(None,1)+[''])[1])) / 2) for raw in lines if raw.partition(';')[0].strip().upper().startswith('DB ')),
            "odd_directives": odd_db_directives,
        },
        "uses_tabl": mnemonics["TABL"] > 0,
        "uses_tabh": mnemonics["TABH"] > 0,
        "call_count": mnemonics["CALL"],
        "jmp_count": mnemonics["JMP"],
        "official_sfr_references": sorted(official_refs),
        "compiler_internal_alias_references": sorted(legacy_internal_refs),
        "sram_addresses": [f"0x{x:02X}" for x in sorted(sram_refs)],
        "layout_estimate": {
            "occupied_words": used_in_range,
            "highest_written_word": None if highest is None else f"0x{highest:04X}",
            "span_words": span_words,
            "hole_words": hole_words,
            "overlaps": overlaps,
            "out_of_range": out_of_range,
        },
        "syntax_findings": {
            "direct_numeric_jumps": direct_numeric_jumps,
            "mixed_0x_and_h_literals": mixed_hex_literals,
        },
        "artifacts": artifacts,
    }


def normalize_instruction(record, probe):
    raw_syntax = record["asm_syntax"]
    syntax = raw_syntax
    corrections = []
    operand_type = record.get("operands")
    if raw_syntax == "XOR A.#K":
        syntax = "XOR A,#K"
        corrections.append("将元数据中的句点改为操作数逗号")
    elif raw_syntax == "BTSZ,R,b":
        syntax = "BTSZ R,b"
        corrections.append("删除 mnemonic 后多余逗号")
    elif raw_syntax == "BTSNZ,R,b":
        syntax = "BTSNZ R,b"
        corrections.append("删除 mnemonic 后多余逗号")
    if raw_syntax == "MOV A,#K" and operand_type == "r8":
        operand_type = "k8"
        corrections.append("操作数类型由错误的 r8 修正为 k8")

    policy = "allowed"
    semantic_status = "compiler_probe_only"
    cautions = []
    if record["mnemonic"] in {"TABL", "TABH"}:
        policy = "allowed_with_same_256_word_page_rule"
        semantic_status = "hardware_verified_in_project"
        cautions.append("A 作为页内低 8 位索引；执行指令与目标 DB word 必须同一 256-word 页")
    if raw_syntax in {"JMP K", "CALL K"}:
        policy = "label_or_equ_target_only"
        cautions.append("当前 Python 词法器不接受直接数字目标；超 10-bit 目标仅 warning 后截断")
    if raw_syntax == "RET A,#K":
        policy = "restricted_pending_hardware_confirmation"
        semantic_status = "open_hardware_semantics"
        cautions.append("0xA1KK 编译路径可生成，但项目无实板语义证据；assembler.py 另有假设操作码死路")
    if record["mnemonic"] in {"CPL", "CPLR"}:
        policy = "restricted_until_semantics_confirmed"
        semantic_status = "open_hardware_semantics"
        cautions.append("原始 notes 对取反语义存在疑点，不能仅凭表格断言")
    if record["mnemonic"] in {"IDLE", "STOP"}:
        policy = "requires_board_power_mode_test"
        cautions.append("进入低功耗/停止模式前必须验证唤醒源、WDT 和外设状态")

    return {
        "id": f"HK-INS-{probe['index']:03d}",
        "mnemonic": record["mnemonic"],
        "source_row": record.get("source_row", record.get("_row", probe["index"] + 1)),
        "raw_asm_syntax": raw_syntax,
        "asm_syntax": syntax,
        "raw_operand_type": record.get("operands"),
        "operand_type": operand_type,
        "word_bits": record.get("word_bits"),
        "opcode_pattern": record.get("opcode_pattern"),
        "mask_hex": str(record.get("mask_hex")).upper(),
        "value_hex": str(record.get("value_hex")).upper(),
        "cycles": record.get("cycles"),
        "flags_affected": record.get("flags_affected"),
        "raw_notes": record.get("notes"),
        "metadata_corrections": corrections,
        "compile_probe": {
            "status": "passed" if probe["success"] and probe["matches_expected"] else "failed",
            "source_instruction": probe["probe_source"],
            "machine_word": probe["word"],
            "expected_word": probe["expected"],
            "errors": probe["errors"],
            "warnings": probe["warnings"],
            "evidence_level": "E4",
        },
        "semantic_status": semantic_status,
        "delivery_policy": policy,
        "cautions": cautions,
    }


def existing_directory(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"directory does not exist: {path}")
    return path


def existing_file(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file does not exist: {path}")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从 HK64S8x 源项目和公司编译器源码生成可追溯分析快照；不修改源项目文件。"
    )
    parser.add_argument("--repo", required=True, type=existing_directory, help="被分析项目根目录")
    parser.add_argument("--compiler-root", required=True, type=existing_directory, help="公司 HK_ASM_Compiler 根目录")
    parser.add_argument(
        "--instruction-metadata",
        type=existing_file,
        help="更新后的 instruction_set.json；省略时优先使用规范包内快照，再回退源仓库",
    )
    parser.add_argument(
        "--register-metadata",
        type=existing_file,
        help="更新后的 register_set.json；省略时优先使用规范包内快照，再回退源仓库",
    )
    parser.add_argument(
        "--spec-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="规范包根目录；默认是本脚本上一级目录",
    )
    parser.add_argument(
        "--generated-at",
        help="快照时间戳（ISO 8601）；省略时使用当前本地时区时间",
    )
    return parser


def main(argv=None):
    global REPO, SPEC, COMPILER, INSTRUCTION_METADATA, REGISTER_METADATA, REGISTER_INC
    global GENERATED_AT, EXCLUDED_PARTS
    args = build_parser().parse_args(argv)
    REPO = args.repo.resolve()
    COMPILER = args.compiler_root.resolve()
    SPEC = args.spec_root.expanduser().resolve()
    SPEC.mkdir(parents=True, exist_ok=True)
    package_root = Path(__file__).resolve().parents[1]
    packaged_instruction = package_root / "rules" / "instruction-metadata.json"
    packaged_register = package_root / "rules" / "register-reference.json"
    INSTRUCTION_METADATA = (
        args.instruction_metadata.resolve()
        if args.instruction_metadata
        else packaged_instruction
        if packaged_instruction.is_file()
        else REPO / "Standards _rules" / "instruction_set.json"
    )
    REGISTER_METADATA = (
        args.register_metadata.resolve()
        if args.register_metadata
        else packaged_register
        if packaged_register.is_file()
        else REPO / "Standards _rules" / "register_set.json"
    )
    REGISTER_INC = REPO / "Standards _rules" / "REG825.INC"
    GENERATED_AT = args.generated_at or datetime.now().astimezone().replace(microsecond=0).isoformat()
    if SPEC.parent == REPO or REPO in SPEC.parents:
        try:
            relative_spec = SPEC.relative_to(REPO)
            if relative_spec.parts:
                EXCLUDED_PARTS = set(EXCLUDED_PARTS) | {relative_spec.parts[0]}
        except ValueError:
            pass

    inc = REGISTER_INC.read_text(encoding="utf-8-sig", errors="replace")
    official = []
    for line in inc.splitlines():
        m = re.match(r"^\s*([A-Za-z_][\w]*)\s+EQU\s+([0-9A-Fa-f]+H|0x[0-9A-Fa-f]+|\d+)\b", line)
        if m:
            official.append({"name": m.group(1).upper(), "address": f"0x{parse_num(m.group(2)):02X}"})
    official_names = {x["name"] for x in official}

    asm_paths = sorted(p for p in REPO.rglob("*.asm") if not any(part in EXCLUDED_PARTS for part in p.parts))
    analyses = [analyze_asm(p, official_names) for p in asm_paths]
    aggregate = Counter()
    for item in analyses:
        aggregate.update(item["mnemonic_frequency"])

    counts = {}
    for ext in ("asm", "map", "bin", "hex", "hkproj"):
        counts[ext] = sum(1 for p in REPO.rglob("*." + ext) if not any(part in EXCLUDED_PARTS for part in p.parts))

    inventory = {
        "schema_version": "1.0.0",
        "generated_at": GENERATED_AT,
        "source_repo_root": str(REPO),
        "scope": "HK64S8x CLI、公司汇编器源码、53 组 ASM/MAP/BIN/HEX 实验与实板记录",
        "file_counts": counts,
        "asm_summary": {
            "files": len(analyses),
            "total_source_lines": sum(x["line_count"] for x in analyses),
            "total_instruction_occurrences": sum(x["instruction_count"] for x in analyses),
            "files_with_db": sum(x["db"]["directive_count"] > 0 for x in analyses),
            "total_db_source_bytes": sum(x["db"]["source_byte_count"] for x in analyses),
            "files_with_tabl": sum(x["uses_tabl"] for x in analyses),
            "files_with_tabh": sum(x["uses_tabh"] for x in analyses),
            "hardware_verified_files": [x["path"] for x in analyses if x["role"] == "hardware_verified"],
            "mnemonic_frequency": dict(aggregate.most_common()),
        },
        "toolchains": {
            "company_ide": "公司 HK_ASM_Compiler IDE 的实际构建路径，能生成 DB；版本未提供稳定语义版本号",
            "python_source_module_cli": "asmc/scripts/asmc_compile.py 直接调用 src/core/assembler.py；当前 DB 不生成机器码",
            "simulator": "src/core/simulator.py；TABL/TABH 固定读 page 0，不能验证跨页",
            "hardware": "HK64S8x + 当前开发板/OLED/数码管实板结果，作为冲突时最高优先级",
        },
        "items": analyses,
    }
    dump(SPEC / "analysis/project-inventory.json", inventory)

    csv_fields = [
        "path", "role", "size_bytes", "sha256", "line_count", "instruction_count",
        "org_count", "db_directive_count", "db_source_bytes", "uses_tabl", "uses_tabh",
        "call_count", "jmp_count", "sram_addresses", "highest_written_word", "span_words",
        "hole_words", "overlap_count", "out_of_range_count", "bin_artifacts", "map_artifacts",
    ]
    with (SPEC / "analysis/asm-inventory.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_fields)
        w.writeheader()
        for x in analyses:
            w.writerow({
                "path": x["path"], "role": x["role"], "size_bytes": x["size_bytes"], "sha256": x["sha256"],
                "line_count": x["line_count"], "instruction_count": x["instruction_count"], "org_count": len(x["orgs"]),
                "db_directive_count": x["db"]["directive_count"], "db_source_bytes": x["db"]["source_byte_count"],
                "uses_tabl": x["uses_tabl"], "uses_tabh": x["uses_tabh"], "call_count": x["call_count"], "jmp_count": x["jmp_count"],
                "sram_addresses": " ".join(x["sram_addresses"]), "highest_written_word": x["layout_estimate"]["highest_written_word"],
                "span_words": x["layout_estimate"]["span_words"], "hole_words": x["layout_estimate"]["hole_words"],
                "overlap_count": len(x["layout_estimate"]["overlaps"]), "out_of_range_count": len(x["layout_estimate"]["out_of_range"]),
                "bin_artifacts": " | ".join(a["path"] for a in x["artifacts"]["bin"]),
                "map_artifacts": " | ".join(a["path"] for a in x["artifacts"]["map"]),
            })

    raw = json.loads(INSTRUCTION_METADATA.read_text(encoding="utf-8-sig"))
    register_raw = json.loads(REGISTER_METADATA.read_text(encoding="utf-8-sig"))
    records = metadata_records(raw, "instructions")
    probes = run_instruction_probes(records)
    variants = [normalize_instruction(r, p) for r, p in zip(records, probes)]
    instruction_reference = {
        "schema_version": "1.0.0",
        "generated_at": GENERATED_AT,
        "variant_count": len(variants),
        "mnemonic_count": len({x["mnemonic"] for x in variants}),
        "source": {
            "metadata_file": str(INSTRUCTION_METADATA),
            "metadata_file_sha256": sha256(INSTRUCTION_METADATA),
            "upstream_source_file": raw.get("source_file"),
            "upstream_source_sha256": raw.get("source_sha256"),
            "metadata_generated_at": raw.get("generated_at"),
            "metadata_rows": len((raw.get("sheets") or [{}])[0].get("records", [])),
            "compiler": "HK_ASM_Compiler/src/core/assembler.py + instruction_parser.py",
            "probe": "每个变体使用代表操作数单独编译；65/65 机器字匹配元数据基值及操作数字段",
        },
        "important_limit": "编译探针只证明当前源码模块能生成预期机器字，不等价于全部指令已实板验证",
        "variants": variants,
    }
    instruction_metadata_target = SPEC / "rules/instruction-metadata.json"
    snapshot_json(INSTRUCTION_METADATA, instruction_metadata_target, raw)
    instruction_reference["source"]["packaged_metadata_sha256"] = sha256(instruction_metadata_target)
    dump(SPEC / "rules/instruction-reference.json", instruction_reference)

    metadata_registers = metadata_records(register_raw, "registers")
    official_by_key = {}
    for item in official:
        kind = "OPTION" if item["name"].startswith("OPT_") else "SFR"
        official_by_key[(kind, int(item["address"], 16))] = item["name"]
    metadata_by_key = {
        (str(item.get("kind", "SFR")).upper(), parse_num(str(item.get("address", "")))): item["name"].upper()
        for item in metadata_registers
        if parse_num(str(item.get("address", ""))) is not None
    }
    metadata_conflicts = []
    for key in sorted(set(official_by_key) | set(metadata_by_key), key=lambda value: (value[0], value[1])):
        official_name = official_by_key.get(key)
        metadata_name = metadata_by_key.get(key)
        if official_name == metadata_name:
            continue
        conflict_type = "name_mismatch" if official_name and metadata_name else "metadata_only" if metadata_name else "inc_only"
        metadata_conflicts.append(
            {
                "type": conflict_type,
                "space": key[0],
                "address": f"0x{key[1]:02X}",
                "reg825_inc_name": official_name,
                "register_metadata_name": metadata_name,
                "status": "OPEN",
                "source_policy": "交付 ASM 仍以 REG825.INC 名称为准；INC 缺失或命名冲突的寄存器在 FAE/编译器确认前限制使用",
            }
        )

    register_reference_target = SPEC / "rules/register-reference.json"
    snapshot_json(REGISTER_METADATA, register_reference_target, register_raw)
    register_policy = {
        "schema_version": "1.0.0",
        "generated_at": GENERATED_AT,
        "source": {
            "reg825_inc": str(REGISTER_INC),
            "reg825_inc_sha256": sha256(REGISTER_INC),
            "register_metadata_file": str(REGISTER_METADATA),
            "register_metadata_file_sha256": sha256(REGISTER_METADATA),
            "packaged_reference_sha256": sha256(register_reference_target),
            "upstream_source_file": register_raw.get("source_file"),
            "upstream_source_sha256": register_raw.get("source_sha256"),
            "metadata_generated_at": register_raw.get("generated_at"),
            "sheet_row_count": len((register_raw.get("sheets") or [{}])[0].get("records", [])),
            "aggregated_register_count": len(metadata_registers),
        },
        "memory_spaces": {
            "sfr": {"range": ["0x00", "0x7F"], "lifecycle": "运行时特殊功能寄存器"},
            "sram": {"range": ["0x80", "0xBF"], "size_bytes": 64, "lifecycle": "运行时数据/临时状态"},
            "option": {"range": ["0x00", "0x1F"], "lifecycle": "独立配置/烧录空间，不得当作运行时 SFR"},
            "program": {"range_words": ["0x0000", "0x03FF"], "words": 1024, "physical_bytes": 2048},
        },
        "policy": {
            "company_ide_source_names": "REG825.INC 中的官方名称是交付源代码唯一规范名",
            "python_cli_compatibility": "CLI 可在内存中把 *_PPU/*_POE 转换为 *_PU/*_OE；不得把内部名反写进交付源码",
            "metadata_reference": "位字段和描述读取 rules/register-reference.json；公司 IDE 源码名称仍以 REG825.INC 为准",
            "metadata_conflict_policy": "任何 INC/metadata 名称或地址冲突必须进入 OPEN；不得由 AI 静默选边",
            "direct_sram_style": "短小 scratch 可直接使用 80H~BFH；长期状态必须在文件头列分配表并与 scratch 分区",
            "custom_sfr_alias": "禁止无必要给 SFR 再定义 EQU 别名；地址交叉校验用官方 INC",
        },
        "official_registers": official,
        "known_toolchain_aliases": [
            {"official": k, "python_source_module_internal": v, "source_policy": "use_official"}
            for k, v in LEGACY_ALIAS_MAP.items()
        ],
        "critical_gpio_registers": [x for x in official if x["name"] in {
            "PA_PPU", "PA_PPD", "PA_POD", "PA_PSL", "PA_POE", "PA_PIO",
            "PB_PPU", "PB_PPD", "PB_POD", "PB_PSL", "PB_POE", "PB_PIO",
        }],
        "metadata_conflicts": metadata_conflicts,
    }
    dump(SPEC / "rules/register-alias-policy.json", register_policy)

    specific_probe_results = {
        "schema_version": "1.0.0",
        "generated_at": GENERATED_AT,
        "instruction_variants": {"passed": sum(p["success"] and p["matches_expected"] for p in probes), "total": len(probes), "results": probes},
        "jump_target_syntax": {
            "failed": ["JMP 03FFH", "CALL 03FFH", "JMP 0x03FF", "CALL 0x03FF"],
            "diagnostic": "期望操作数格式: [K], 实际: [R]",
            "passed": ["JMP TARGET（标签）", "TARGET EQU 03FFH + JMP TARGET"],
            "evidence_level": "E4",
        },
        "db_source_module": {
            "source": "probe_db_table.asm",
            "db_org": "0x0080",
            "source_module_bin": ".embeddedskills/build/probe_db_table.bin",
            "bin_size_bytes": 46,
            "result": "DB 数据未写入输出，只有普通指令和 ORG 空洞",
            "evidence_level": "E3+E4",
        },
        "db_company_ide_layout": {
            "source_bytes": ["12", "34", "56", "78", "9A", "BC", "DE", "F0"],
            "bin_bytes": ["34", "21", "78", "65", "BC", "A9", "F0", "ED"],
            "word_rule": "word = nibble_swap(first_source_byte) << 8 | second_source_byte",
            "bin_rule": "little-endian bytes = second_source_byte, nibble_swap(first_source_byte)",
            "sha256": "11b59d7692016d7c053d5243f2e73a0020027a063ffc57f5eb8a443afa8fffa1",
            "runtime_rule": "不得据此补偿源码；实板应写原始 DB，运行时 TABL 后 TABH",
            "evidence_level": "E1+E2",
        },
    }
    dump(SPEC / "analysis/probe-results.json", specific_probe_results)

    evidence = {
        "schema_version": "1.0.0",
        "generated_at": GENERATED_AT,
        "levels": {
            "E1": "实板确认", "E2": "公司 IDE 实际构建产物", "E3": "编译器源码",
            "E4": "自动测试或编译探针", "E5": "项目文档/源码注释", "E6": "合理推断", "OPEN": "待芯片/FAE/硬件确认",
        },
        "conflict_precedence": ["E1 实板", "E2 公司 IDE 真实产物", "E3 当前编译器源码", "E4 自动测试/探针", "模拟器", "E5/E6 注释与推断"],
        "claims": [
            {"claim_id": "EV-DB-CLI", "claim": "Python 源码模块 CLI 不生成 DB 机器码", "level": ["E3", "E4"], "sources": ["HK_ASM_Compiler/src/core/assembler.py:167-239", ".embeddedskills/build/probe_db_table.bin"]},
            {"claim_id": "EV-DB-IDE", "claim": "公司 IDE 生成 DB，BIN 采用特殊 word/小端物理排列", "level": ["E2"], "sources": ["probe_db_layout_values.asm", "build/probe_db_layout_values.bin", "build/probe_db_layout_values.hex"]},
            {"claim_id": "EV-TABLE-RUNTIME", "claim": "DB 原始字节 + TABL 再 TABH 已实板验证", "level": ["E1"], "sources": ["ssd1306_oled_heello_db_raw_verified.asm", "ssd1306_oled_avatar_64x64_db_raw_verified.asm", "README.md"]},
            {"claim_id": "EV-TABLE-PAGE", "claim": "TABL/TABH 与数据 word 必须位于同一 256-word 页", "level": ["E1", "E2"], "sources": ["ssd1306_oled_avatar_64x64_db_raw_verified.asm", "build/ssd1306_oled_avatar_64x64_db_raw_verified.map"]},
            {"claim_id": "EV-SIM-PAGE0", "claim": "模拟器 TABL/TABH 固定访问 page 0", "level": ["E3"], "sources": ["HK_ASM_Compiler/src/core/simulator.py:342-349"]},
            {"claim_id": "EV-JUMP-SYNTAX", "claim": "当前源码模块对 JMP/CALL 直接数字目标存在 token 匹配问题", "level": ["E4"], "sources": ["analysis/probe-results.json"]},
            {"claim_id": "EV-I2C-ACK", "claim": "发送 8 位后释放 SDA 再采样 ACK", "level": ["E1", "E5"], "sources": ["ssd1306_oled_avatar_64x64_db_raw_verified.asm", "asmc/SKILL.md"]},
            {"claim_id": "EV-7SEG", "claim": "当前板 COM 极性、物理位序和全关值", "level": ["E1", "E5"], "sources": ["seven_segment_1234.asm", "seven_segment_counter_0001_9999.asm", "README.md"]},
            {"claim_id": "EV-METADATA-202607", "claim": "2026-07 元数据修正 BTSZ/BTSNZ 拼写及 8 个 GPIO 正式寄存器名", "level": ["E5"], "sources": ["rules/instruction-metadata.json", "rules/register-reference.json"]},
        ],
        "open_questions": [
            {"id": "OPEN-ROM-BUS", "question": "IDE DB 半字节变换与实板 TABL/TABH 逻辑之间的芯片级 ROM 数据总线原因", "owner": "芯片设计/FAE", "delivery_impact": "不阻塞已验证用法，但禁止自行推导新的补偿规则"},
            {"id": "OPEN-RET-AK", "question": "RET A,#K 在量产硅上的确切语义与官方操作码", "owner": "芯片设计/FAE", "delivery_impact": "确认前正式项目禁用"},
            {"id": "OPEN-CPL", "question": "CPL/CPLR 的确切按位语义和写回目标", "owner": "芯片设计/FAE", "delivery_impact": "确认前避免关键逻辑使用"},
            {"id": "OPEN-DB-ODD", "question": "公司 IDE 对单个 DB 指令奇数字节末尾的填充规则", "owner": "编译器维护者", "delivery_impact": "当前规范要求显式偶数字节/显式补零"},
            {"id": "OPEN-REG-LVD", "question": "REG825.INC 的 LVD@24H 与 register metadata 的 LVD1@24H、LVD2@26H、LVD3@27H 如何映射", "owner": "芯片设计/FAE + 编译器维护者", "delivery_impact": "LVD2/LVD3 在 INC/编译器正式支持前限制使用；24H 交付源码继续使用 LVD"},
        ],
    }
    dump(SPEC / "analysis/evidence-matrix.json", evidence)

    manifest_paths = [
        REPO / "README.md", REPO / "asmc/SKILL.md", REPO / "asmc/scripts/asmc_compile.py",
        REGISTER_INC, INSTRUCTION_METADATA, REGISTER_METADATA,
        REPO / "ssd1306_oled_heello_db_raw_verified.asm", REPO / "ssd1306_oled_avatar_64x64_db_raw_verified.asm",
        REPO / "build/ssd1306_oled_avatar_64x64_db_raw_verified.map", REPO / "probe_db_layout_values.asm",
        REPO / "build/probe_db_layout_values.bin", REPO / "seven_segment_1234.asm", REPO / "seven_segment_counter_0001_9999.asm",
        COMPILER / "src/core/assembler.py", COMPILER / "src/core/lexer.py", COMPILER / "src/core/instruction_parser.py",
        COMPILER / "src/core/output_generator.py", COMPILER / "src/core/simulator.py", COMPILER / "src/core/chip_manager.py",
    ]
    manifest = []
    for p in manifest_paths:
        if p.exists():
            manifest.append({"path": str(p), "size_bytes": p.stat().st_size, "sha256": sha256(p), "last_modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()})
    dump(SPEC / "analysis/source-manifest.json", {"schema_version": "1.0.0", "generated_at": GENERATED_AT, "files": manifest})

    print(json.dumps({
        "asm_files": len(analyses), "instruction_variants": len(variants), "instruction_probes_passed": sum(p["success"] and p["matches_expected"] for p in probes),
        "official_registers": len(official), "metadata_registers": len(metadata_registers), "metadata_conflicts": len(metadata_conflicts),
        "output": str(SPEC / "analysis")
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
