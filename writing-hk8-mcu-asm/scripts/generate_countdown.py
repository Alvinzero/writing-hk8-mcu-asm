from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from hk8asm import (
    GateError,
    normalize_profile_paths,
    read_json,
    validate_clock_contract,
    validate_output_pin_contract,
    validate_profile,
    validate_seven_segment_contract,
    write_json,
)

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT))

from references.spec.tools.asm_semantic_gates import derive_sck_hz


TIME_RE = re.compile(r"^(\d{2}):([0-5]\d)$")
DIGIT_SEGMENTS = {
    0: ("A", "B", "C", "D", "E", "F"),
    1: ("B", "C"),
    2: ("A", "B", "D", "E", "G"),
    3: ("A", "B", "C", "D", "G"),
    4: ("B", "C", "F", "G"),
    5: ("A", "C", "D", "F", "G"),
    6: ("A", "C", "D", "E", "F", "G"),
    7: ("A", "B", "C"),
    8: ("A", "B", "C", "D", "E", "F", "G"),
    9: ("A", "B", "C", "D", "F", "G"),
}
STATE_SYMBOLS = ("MIN_TENS", "MIN_UNITS", "SEC_TENS", "SEC_UNITS")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError("COUNTDOWN_GENERATION_FAILED", message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def finite_positive(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def format_hex_byte(value: int) -> str:
    require(isinstance(value, int) and 0 <= value <= 0xFF, "byte value is out of range")
    return "0%02XH" % value if value >= 0xA0 else "%02XH" % value


def parse_start(value: str) -> Tuple[int, int, List[int]]:
    match = TIME_RE.fullmatch(value)
    require(match is not None, "--start must use MM:SS with MM=00..99 and SS=00..59")
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    return minutes, seconds, [minutes // 10, minutes % 10, seconds // 10, seconds % 10]


def active_high_digit_byte(mapping: Dict[str, int], digit: int) -> int:
    return sum(1 << mapping[name] for name in DIGIT_SEGMENTS[digit])


def physical_digit_byte(
    mapping: Dict[str, int], digit: int, active_level: str, dp_on: bool
) -> int:
    value = active_high_digit_byte(mapping, digit)
    if dp_on:
        value |= 1 << mapping["DP"]
    return value if active_level == "high" else (~value) & 0xFF


def com_instruction(port: str, bit: int, level: str) -> str:
    return "            %s %s_PIO,%d" % ("BSET" if level == "high" else "BCLR", port, bit)


def solve_timing(
    digit_count: int,
    sck_hz: int,
    tick_us: float,
    tolerance_percent: float,
    frame_rate_hz: int,
    slot_min_us: float,
    slot_max_us: float,
    state_immediate_loads: int,
) -> Dict[str, Any]:
    require(digit_count == 4, "the countdown generator requires exactly four digits")
    require(1 <= frame_rate_hz <= 255, "frame_rate_hz must be within 1..255")
    target_cycles = tick_us * sck_hz / 1_000_000
    frame_count = int(round(tick_us * frame_rate_hz / 1_000_000))
    require(1 <= frame_count <= 255, "derived frame count must be within 1..255")
    require(
        1 <= state_immediate_loads <= digit_count,
        "state immediate-load count is invalid",
    )
    first_second_overhead = 22 + state_immediate_loads
    slot_target_us = (slot_min_us + slot_max_us) / 2
    best: Optional[Tuple[Tuple[float, float, int, int, int], Dict[str, Any]]] = None

    # These equations mirror the emitted E1 scan structure. The real semantic
    # cycle audit remains authoritative during quick-release.
    for outer in range(1, 17):
        for inner in range(1, 256):
            hold_cycles = 4 + outer * (4 * inner + 5)
            scan_slot_cycles = hold_cycles + 30
            slot_us = scan_slot_cycles * 1_000_000 / sck_hz
            if not slot_min_us <= slot_us <= slot_max_us:
                continue
            frame_without_pad = digit_count * scan_slot_cycles + 8
            ideal_pad = (
                target_cycles - first_second_overhead
            ) / frame_count - frame_without_pad
            pad_candidates = {
                max(0, min(64, int(math.floor(ideal_pad)))),
                max(0, min(64, int(math.ceil(ideal_pad)))),
            }
            for frame_pad_nops in pad_candidates:
                frame_cycles = frame_without_pad + frame_pad_nops
                total_cycles = frame_count * frame_cycles + first_second_overhead
                actual_us = total_cycles * 1_000_000 / sck_hz
                error_percent = abs(actual_us - tick_us) / tick_us * 100
                score = (
                    abs(total_cycles - target_cycles),
                    abs(slot_us - slot_target_us),
                    outer,
                    inner,
                    frame_pad_nops,
                )
                candidate = {
                    "delay_outer": outer,
                    "delay_inner": inner,
                    "digit_hold_cycles": hold_cycles,
                    "scan_slot_cycles": scan_slot_cycles,
                    "scan_slot_us": slot_us,
                    "frame_count": frame_count,
                    "frame_pad_nops": frame_pad_nops,
                    "frame_cycles": frame_cycles,
                    "first_second_overhead_cycles": first_second_overhead,
                    "predicted_cycles": total_cycles,
                    "predicted_actual_us": actual_us,
                    "predicted_error_percent": error_percent,
                }
                if best is None or score < best[0]:
                    best = (score, candidate)

    require(best is not None, "no scan timing solution satisfies the board limits")
    result = best[1]
    require(
        result["predicted_error_percent"] <= tolerance_percent,
        "no scan timing solution satisfies the tick tolerance",
    )
    return result


def validate_board_profile(board: Dict[str, Any], path: Path) -> None:
    require(board.get("schema_version") == 1, "board profile schema_version must be 1")
    require(board.get("chip") == "HK64S825", "board profile chip must be HK64S825")
    require(board.get("status") == "ready", "board profile must have status=ready")
    require(
        board.get("selection_policy") == "user_must_explicitly_confirm_profile_id",
        "board profile must require explicit user selection",
    )
    require(
        isinstance(board.get("board_profile_id"), str) and bool(board["board_profile_id"]),
        "board_profile_id is required",
    )
    require(path.name == "seven-segment.json", "--board-profile must name seven-segment.json")
    require(board.get("unresolved_inputs") == [], "board profile has unresolved inputs")
    evidence = board.get("evidence")
    require(isinstance(evidence, dict), "board profile E1 evidence is required")
    require(evidence.get("level") == "E1", "board profile evidence must be E1")
    require(
        evidence.get("status") == "user_confirmed_normal_display",
        "board profile must record user-confirmed normal display",
    )
    clock = board.get("clock")
    require(isinstance(clock, dict), "board profile clock is required")
    require(
        isinstance(clock.get("osc_hz"), int)
        and isinstance(clock.get("sck_ps"), int)
        and isinstance(clock.get("effective_sck_hz"), int),
        "board profile clock must be fully numeric",
    )
    pins = board.get("pins")
    require(isinstance(pins, dict), "board profile pins are required")
    for name, pin in pins.items():
        require(isinstance(pin, dict), "pins.%s must be structured" % name)
        validate_output_pin_contract(name, pin)
    seven_segment = board.get("seven_segment")
    require(isinstance(seven_segment, dict), "board profile seven_segment is required")
    request_view = {"seven_segment": seven_segment}
    validate_seven_segment_contract(request_view, pins)
    require(
        seven_segment.get("external_inversion")
        == {"status": "confirmed", "segments": False, "digit_select": False},
        "countdown fast path requires confirmed direct GPIO polarity",
    )
    require(
        seven_segment.get("current_limit", {}).get("status") == "confirmed"
        and seven_segment.get("drive_capability_confirmed") is True,
        "countdown fast path requires confirmed current limiting and drive capability",
    )
    segment_pin = pins.get(seven_segment.get("segment_pin"))
    require(isinstance(segment_pin, dict), "seven_segment.segment_pin is invalid")
    require(
        segment_pin.get("bits") == list(range(8))
        and segment_pin.get("port_ownership") == "exclusive",
        "countdown fast path requires an exclusive eight-bit segment port",
    )
    digits = sorted(seven_segment.get("digits", []), key=lambda item: item["visual_index"])
    require(
        len(digits) == 4 and [item.get("visual_index") for item in digits] == list(range(4)),
        "countdown fast path requires visual digits 0..3",
    )
    digit_ports = {pins[item["pin_contract"]]["port"] for item in digits}
    require(len(digit_ports) == 1, "countdown fast path requires one digit-select port")
    require(
        segment_pin["port"] not in digit_ports,
        "segment and digit-select ports must be distinct",
    )


def resolve_separator(board: Dict[str, Any], selection: str) -> Optional[int]:
    if selection == "none":
        return None
    if selection == "visual-digit-2-dp":
        return 1
    separator = board["seven_segment"].get("separator")
    require(isinstance(separator, dict), "profile separator is not configured")
    require(
        separator.get("type") == "visual_digit_dp"
        and separator.get("mode") == "steady_on"
        and separator.get("visual_index") == 1,
        "profile separator must be the steady DP on visual digit 2",
    )
    return 1


def build_request(
    board: Dict[str, Any],
    board_path: Path,
    source_path: Path,
    start: str,
    separator_index: Optional[int],
    sck_hz: int,
    timing: Dict[str, Any],
) -> Dict[str, Any]:
    seven_segment = clone(board["seven_segment"])
    if separator_index is None:
        seven_segment.pop("separator", None)
        separator_text = "none"
    else:
        seven_segment["separator"] = {
            "type": "visual_digit_dp",
            "visual_index": separator_index,
            "mode": "steady_on",
        }
        separator_text = "visual_index_1_dp_steady"
    defaults = board["countdown_defaults"]
    tolerance = defaults["tolerance_percent"]
    request = {
        "schema_version": 1,
        "chip": "HK64S825",
        "behavior": "四位数码管从%s逐秒倒计时，到00分00秒后保持显示" % start,
        "input_provenance": {
            "board": "user_confirmed_profile",
            "pins": "user_confirmed_profile",
            "clock": "user_confirmed_profile",
        },
        "board": {
            "id": board["board_profile_id"],
            "profile_file": board_path.name,
            "profile_sha256": sha256_file(board_path),
            "evidence_status": board["evidence"]["status"],
        },
        "target_toolchain": "builtin_compiler",
        "source_files": [source_path.name],
        "clock": {
            "osc_hz": board["clock"]["osc_hz"],
            "sck_ps": board["clock"]["sck_ps"],
        },
        "pins": clone(board["pins"]),
        "seven_segment": seven_segment,
        "peripherals": [{"name": "four_digit_seven_segment"}],
        "timing": {
            "precision": "precise",
            "frame_rate_hz": timing["frame_count"],
            "delay_targets": [
                {
                    "label": "FIRST_SECOND_HOLD",
                    "target_us": defaults["tick_us"],
                    "tolerance_percent": tolerance,
                }
            ],
        },
        "memory_limits": {"rom_bytes": 2048, "ram_bytes": 64},
        "functional_requirements": [
            "初始显示%s" % start,
            "每秒减一秒",
            "到00分00秒后保持",
            "每个完整四位扫描帧结束后才更新倒计时状态",
        ],
        "acceptance": [
            {
                "name": "initial_value",
                "observable": "四位数码管",
                "expected": "上电后显示%s" % start,
            },
            {
                "name": "countdown",
                "observable": "显示值",
                "expected": "逐秒递减并正确执行借位",
            },
            {
                "name": "terminal_value",
                "observable": "显示值",
                "expected": "到00分00秒后保持不再递减",
            },
        ],
        "resolved_inputs": {
            "start": start,
            "terminal": "00:00",
            "terminal_behavior": "hold",
            "separator": separator_text,
            "effective_sck_hz": sck_hz,
        },
        "generation": {
            "tool": "generate_countdown.py",
            "board_profile_id": board["board_profile_id"],
            "sck_hz": sck_hz,
            **timing,
        },
        "unresolved_inputs": [],
        "allowed_warnings": [],
        "allow_nonvolatile_changes": False,
    }
    if separator_index is not None:
        request["functional_requirements"].insert(3, "视觉第二位DP常亮")
    return request


def render_source(
    request: Dict[str, Any], start: str, start_digits: List[int], separator_index: Optional[int]
) -> str:
    pins = request["pins"]
    contract = request["seven_segment"]
    mapping = contract["segment_mapping"]
    digits = sorted(contract["digits"], key=lambda item: item["visual_index"])
    segment_pin = pins[contract["segment_pin"]]
    segment_port = segment_pin["port"]
    digit_port = pins[digits[0]["pin_contract"]]["port"]
    sck_ps = request["clock"]["sck_ps"]
    timing = request["generation"]
    separator_text = "视觉第二位的小数点常亮" if separator_index == 1 else "不点亮小数点分隔符"
    initial_segment = 0xFF if segment_pin["initial_level"] == "high" else 0x00
    physical_digits = []
    for digit in digits:
        port = pins[digit["pin_contract"]]["port"]
        active = digit["com_active_level"]
        physical_digits.append(
            (port, digit["bit"], active, "low" if active == "high" else "high")
        )

    initial_bytes = [
        physical_digit_byte(
            mapping,
            value,
            digits[index]["segment_active_level"],
            separator_index == index,
        )
        for index, value in enumerate(start_digits)
    ]
    lines = [
        "; CHIP: HK64S825",
        "; 功能：四位数码管从 %s 逐秒倒计时，到 00 分 00 秒后保持" % start,
        "; 时钟：振荡源为 16 MHz，分频寄存器取 %s，实际指令时钟为 2 MHz"
        % format_hex_byte(sck_ps),
        "; 分隔点：%s" % separator_text,
        "; 驱动：GPIO 推挽直驱，无外部反相，限流和峰值驱动能力已由用户确认",
        "; 位序：视觉从左到右按机器板卡配置的四个位选输出",
        "; 初始化：先设置推挽模式，再预装安全锁存值，最后开启输出使能",
        "; WDT：未修改 OPTION，长循环内部持续执行 CLRWDT",
        ";",
        "; SRAM 分配表：",
        "; 80H MIN_TENS    分钟十位，长期状态",
        "; 81H MIN_UNITS   分钟个位，长期状态",
        "; 82H SEC_TENS    秒十位，长期状态",
        "; 83H SEC_UNITS   秒个位，长期状态",
        "; 84H SEG0        视觉第一位物理段码",
        "; 85H SEG1        视觉第二位物理段码",
        "; 86H SEG2        视觉第三位物理段码",
        "; 87H SEG3        视觉第四位物理段码",
        "; 88H ENC_INPUT   段码转换临时值",
        "; 89H DLY_INNER   位保持内层计数",
        "; 8AH DLY_OUTER   位保持外层计数",
        "; 8BH FRAME_COUNT 完整帧计数",
        ";",
        "; 程序布局：000H 复位入口，008H 中断入口，009H 起为连续代码",
        "",
        "MIN_TENS    EQU 80H",
        "MIN_UNITS   EQU 81H",
        "SEC_TENS    EQU 82H",
        "SEC_UNITS   EQU 83H",
        "SEG0        EQU 84H",
        "SEG1        EQU 85H",
        "SEG2        EQU 86H",
        "SEG3        EQU 87H",
        "ENC_INPUT   EQU 88H",
        "DLY_INNER   EQU 89H",
        "DLY_OUTER   EQU 8AH",
        "FRAME_COUNT EQU 8BH",
        "",
        "            ORG 000H",
        "            JMP RESET",
        "",
        "            ORG 008H",
        "            RETI",
        "",
        "            ORG 009H",
        "RESET:",
        "            MOV A,#%s" % format_hex_byte(sck_ps),
        "            MOV SCK_PS,A",
        "            CALL GPIO_INIT",
        "            CALL FIRST_SECOND_HOLD",
        "",
        "MAIN_LOOP:",
        "            CALL COUNTDOWN_TICK",
        "            CALL BUILD_SEGMENTS",
        "            CALL DISPLAY_ONE_SECOND_CORE",
        "            JMP MAIN_LOOP",
        "",
        "; 输入：无；输出：无；破坏：A；不可重入",
        "GPIO_INIT:",
    ]
    for port, bit, _active, _off in sorted(physical_digits, key=lambda item: item[1]):
        lines.append("            BCLR %s_POD,%d" % (port, bit))
    lines.extend(
        [
            "            MOV A,#00H",
            "            MOV %s_POD,A" % segment_port,
            "",
        ]
    )
    for port, bit, _active, off in sorted(physical_digits, key=lambda item: item[1]):
        lines.append(com_instruction(port, bit, off))
    lines.extend(
        [
            "            MOV A,#%s" % format_hex_byte(initial_segment),
            "            MOV %s_PIO,A" % segment_port,
            "",
        ]
    )
    for port, bit, _active, _off in sorted(physical_digits, key=lambda item: item[1]):
        lines.append("            BSET %s_POE,%d" % (port, bit))
    lines.extend(
        [
            "            MOV A,#0FFH",
            "            MOV %s_POE,A" % segment_port,
            "            RET",
            "",
            "; 输入：无；输出：无；破坏：A、状态区、段码区、延时计数区；不可重入",
            "; 首秒写入已选择的起始时间和物理段码，再走正式四位扫描路径",
            "FIRST_SECOND_HOLD:",
        ]
    )
    previous_value = None
    for symbol, value in zip(STATE_SYMBOLS, start_digits):
        if value != previous_value:
            lines.append("            MOV A,#%s" % format_hex_byte(value))
        lines.append("            MOV %s,A" % symbol)
        previous_value = value
    lines.append("")
    for index, value in enumerate(initial_bytes):
        lines.extend(
            [
                "            MOV A,#%s" % format_hex_byte(value),
                "            MOV SEG%d,A" % index,
            ]
        )
    lines.extend(
        [
            "",
            "            CALL DISPLAY_ONE_SECOND_CORE",
            "            RET",
            "",
            "; 输入：四位段码缓存；输出：无；破坏：A、延时计数区；不可重入",
            "; 每帧 %d 个周期，共 %d 帧"
            % (timing["frame_cycles"], timing["frame_count"]),
            "DISPLAY_ONE_SECOND_CORE:",
            "            MOV A,#%s" % format_hex_byte(timing["frame_count"]),
            "            MOV FRAME_COUNT,A",
            "",
            "DISPLAY_FRAME_LOOP:",
            "            CALL SCAN_VISUAL0",
            "            CALL SCAN_VISUAL1",
            "            CALL SCAN_VISUAL2",
            "            CALL SCAN_VISUAL3",
            "            CLRWDT",
            "            CALL FRAME_PAD",
            "            DECSZR FRAME_COUNT",
            "            JMP DISPLAY_FRAME_LOOP",
            "            NOP",
            "            RET",
            "",
        ]
    )
    for index in range(4):
        lines.extend(
            [
                "; 输入：SEG%d；输出：无；破坏：A、延时计数区；不可重入" % index,
                "SCAN_VISUAL%d:" % index,
                "            CALL ALL_DIGITS_OFF",
                "            MOV A,SEG%d" % index,
                "            NOP",
                "            MOV %s_PIO,A" % segment_port,
                "            CALL SELECT_VISUAL%d" % index,
                "            CALL DIGIT_HOLD",
                "            CALL ALL_DIGITS_OFF",
                "            RET",
                "",
            ]
        )
    lines.extend(
        [
            "; 输入：无；输出：所有位关闭；破坏：无；可重入",
            "ALL_DIGITS_OFF:",
        ]
    )
    for port, bit, _active, off in sorted(physical_digits, key=lambda item: item[1]):
        lines.append(com_instruction(port, bit, off))
    lines.extend(["            RET", ""])
    for index, (port, bit, active, _off) in enumerate(physical_digits):
        lines.extend(
            [
                "; 输入：其余位已关闭；输出：选通视觉第%d位；破坏：无；可重入"
                % (index + 1),
                "SELECT_VISUAL%d:" % index,
                com_instruction(port, bit, active),
                "            RET",
                "",
            ]
        )
    lines.extend(
        [
            "; 输入：无；输出：无；破坏：A、延时计数区；不可重入",
            "; 两层计数共 %d 个周期，内层每次循环清 WDT"
            % timing["digit_hold_cycles"],
            "DIGIT_HOLD:",
            "            MOV A,#%s" % format_hex_byte(timing["delay_outer"]),
            "            MOV DLY_OUTER,A",
            "",
            "DIGIT_HOLD_OUTER:",
            "            MOV A,#%s" % format_hex_byte(timing["delay_inner"]),
            "            MOV DLY_INNER,A",
            "",
            "DIGIT_HOLD_INNER:",
            "            CLRWDT",
            "            DECSZR DLY_INNER",
            "            JMP DIGIT_HOLD_INNER",
            "            NOP",
            "            DECSZR DLY_OUTER",
            "            JMP DIGIT_HOLD_OUTER",
            "            NOP",
            "            RET",
            "",
            "; 输入：无；输出：无；破坏：无；可重入",
            "; 用于补齐每帧周期数",
            "FRAME_PAD:",
        ]
    )
    lines.extend(["            NOP"] * timing["frame_pad_nops"])
    lines.extend(
        [
            "            RET",
            "",
            "; 输入：四位倒计时状态；输出：倒计时状态；破坏：A、倒计时状态区；不可重入",
            "; 零值直接返回；借位顺序为秒个位、秒十位、分钟个位、分钟十位",
            "COUNTDOWN_TICK:",
            "            MOV A,MIN_TENS",
            "            NOP",
            "            OR A,MIN_UNITS",
            "            NOP",
            "            OR A,SEC_TENS",
            "            NOP",
            "            OR A,SEC_UNITS",
            "            SE #00H",
            "            JMP COUNTDOWN_NONZERO",
            "            RET",
            "",
            "COUNTDOWN_NONZERO:",
            "            MOV A,SEC_UNITS",
            "            NOP",
            "            SE #00H",
            "            JMP DEC_SEC_UNITS",
            "            MOV A,#09H",
            "            MOV SEC_UNITS,A",
            "",
            "            MOV A,SEC_TENS",
            "            NOP",
            "            SE #00H",
            "            JMP DEC_SEC_TENS",
            "            MOV A,#05H",
            "            MOV SEC_TENS,A",
            "",
            "            MOV A,MIN_UNITS",
            "            NOP",
            "            SE #00H",
            "            JMP DEC_MIN_UNITS",
            "            MOV A,#09H",
            "            MOV MIN_UNITS,A",
            "",
            "            MOV A,MIN_TENS",
            "            NOP",
            "            SUB A,#01H",
            "            MOV MIN_TENS,A",
            "            RET",
            "",
            "DEC_MIN_UNITS:",
            "            SUB A,#01H",
            "            MOV MIN_UNITS,A",
            "            RET",
            "",
            "DEC_SEC_TENS:",
            "            SUB A,#01H",
            "            MOV SEC_TENS,A",
            "            RET",
            "",
            "DEC_SEC_UNITS:",
            "            SUB A,#01H",
            "            MOV SEC_UNITS,A",
            "            RET",
            "",
            "; 输入：四位倒计时状态；输出：四位段码缓存；破坏：A、段码转换临时值；不可重入",
            "; 按逐位极性生成物理段码，并按选择决定是否点亮分隔点",
            "BUILD_SEGMENTS:",
        ]
    )
    dp_mask = 1 << mapping["DP"]
    for index, (symbol, digit) in enumerate(zip(STATE_SYMBOLS, digits)):
        lines.extend(
            [
                "            MOV A,%s" % symbol,
                "            NOP",
                "            CALL ENCODE_ACTIVE_HIGH",
            ]
        )
        if digit["segment_active_level"] == "low":
            lines.append("            XOR A,#0FFH")
        if separator_index == index:
            if digit["segment_active_level"] == "high":
                lines.append("            OR A,#%s" % format_hex_byte(dp_mask))
            else:
                lines.append("            AND A,#%s" % format_hex_byte((~dp_mask) & 0xFF))
        lines.extend(["            MOV SEG%d,A" % index, ""])
    lines.extend(
        [
            "            RET",
            "",
            "; 输入：A 为零至九；输出：A 为高有效物理段码；破坏：A、ENC_INPUT；不可重入",
            "ENCODE_ACTIVE_HIGH:",
            "            MOV ENC_INPUT,A",
            "",
        ]
    )
    for value in range(10):
        if value:
            lines.append("ENCODE_DIGIT%d:" % value)
        lines.extend(
            [
                "            MOV A,ENC_INPUT",
                "            SE #%s" % format_hex_byte(value),
                "            JMP %s" % ("ENCODE_DEFAULT" if value == 9 else "ENCODE_DIGIT%d" % (value + 1)),
                "            MOV A,#%s" % format_hex_byte(active_high_digit_byte(mapping, value)),
                "            RET",
                "",
            ]
        )
    lines.extend(
        [
            "ENCODE_DEFAULT:",
            "            MOV A,#00H",
            "            RET",
            "",
            "            END",
            "",
        ]
    )
    return "\n".join(lines)


def generate(args: argparse.Namespace) -> Dict[str, Any]:
    board_path = args.board_profile.expanduser()
    board = read_json(board_path, "INVALID_BOARD_PROFILE")
    require(isinstance(board, dict), "board profile must be a JSON object")
    validate_board_profile(board, board_path)
    chip_profile = normalize_profile_paths(
        read_json(args.profile, "INVALID_PROFILE"), args.profile.parent
    )
    validate_profile(chip_profile)
    require(chip_profile.get("chip") == board["chip"], "chip and board profiles disagree")
    _minutes, _seconds, start_digits = parse_start(args.start)
    require(args.terminal_behavior == "hold", "only terminal behavior hold is supported")
    separator_index = resolve_separator(board, args.separator)
    clock = board["clock"]
    sck_hz = derive_sck_hz(
        clock["osc_hz"], clock["sck_ps"], chip_profile["clock_model"]
    )
    require(
        sck_hz == clock["effective_sck_hz"],
        "board effective_sck_hz does not match the chip clock model",
    )
    defaults = board.get("countdown_defaults")
    require(isinstance(defaults, dict), "board countdown_defaults are required")
    for name in ("tick_us", "tolerance_percent", "scan_slot_min_us", "scan_slot_max_us"):
        require(finite_positive(defaults.get(name)), "countdown_defaults.%s must be positive" % name)
    require(
        isinstance(defaults.get("frame_rate_hz"), int),
        "countdown_defaults.frame_rate_hz must be an integer",
    )
    timing = solve_timing(
        4,
        sck_hz,
        float(defaults["tick_us"]),
        float(defaults["tolerance_percent"]),
        defaults["frame_rate_hz"],
        float(defaults["scan_slot_min_us"]),
        float(defaults["scan_slot_max_us"]),
        1
        + sum(
            1
            for index in range(1, len(start_digits))
            if start_digits[index] != start_digits[index - 1]
        ),
    )
    request = build_request(
        board,
        board_path,
        args.source,
        args.start,
        separator_index,
        sck_hz,
        timing,
    )
    validate_clock_contract(request, chip_profile)
    validate_seven_segment_contract(request, request["pins"])
    source = render_source(request, args.start, start_digits, separator_index)
    args.source.parent.mkdir(parents=True, exist_ok=True)
    args.source.write_text(source, encoding="utf-8")
    write_json(args.output_request, request)
    return {
        "status": "ok",
        "code": "COUNTDOWN_GENERATED",
        "board_profile_id": board["board_profile_id"],
        "source": str(args.source.resolve()),
        "request": str(args.output_request.resolve()),
        "start": args.start,
        "terminal_behavior": args.terminal_behavior,
        "separator": request["resolved_inputs"]["separator"],
        "sck_hz": sck_hz,
        **timing,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate an HK64S825 four-digit MM:SS countdown from an explicit board profile"
    )
    parser.add_argument("--profile", required=True, type=Path, help="HK64S825 chip profile")
    parser.add_argument(
        "--board-profile",
        required=True,
        type=Path,
        help="explicitly selected machine-readable seven-segment board profile",
    )
    parser.add_argument("--start", required=True, help="countdown start in MM:SS")
    parser.add_argument(
        "--terminal-behavior", choices=("hold",), default="hold"
    )
    parser.add_argument(
        "--separator",
        choices=("profile", "visual-digit-2-dp", "none"),
        default="profile",
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-request", required=True, type=Path)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    try:
        payload = generate(build_parser().parse_args(argv))
    except GateError as exc:
        payload = {"status": "error", "code": exc.code, "message": exc.message}
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 2
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
