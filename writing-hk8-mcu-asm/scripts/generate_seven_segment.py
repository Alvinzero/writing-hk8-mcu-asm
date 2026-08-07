from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

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


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError("SEVEN_SEGMENT_GENERATION_FAILED", message)


def finite_positive(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def format_hex_byte(value: int) -> str:
    return f"0{value:02X}H" if value >= 0xA0 else f"{value:02X}H"


def solve_timing(
    *,
    digit_count: int,
    sck_hz: int,
    target_us: float,
    tolerance_percent: float,
    frame_min: int,
    frame_max: int,
    slot_min_us: float,
    slot_max_us: float,
) -> dict[str, Any]:
    target_cycles = target_us * sck_hz / 1_000_000
    best: tuple[tuple[float, float, int], dict[str, Any]] | None = None
    slot_target_us = (slot_min_us + slot_max_us) / 2
    for outer in range(1, 17):
        for inner in range(1, 256):
            delay_cycles = 6 + outer * (6 * inner + 5)
            slot_us = delay_cycles * 1_000_000 / sck_hz
            if not slot_min_us <= slot_us <= slot_max_us:
                continue
            frame_cycles = 4 + digit_count * (
                delay_cycles + 2 * digit_count + 15
            )
            for frames in range(frame_min, frame_max + 1):
                hold_cycles = 6 + frames * (frame_cycles + 3)
                state_error = abs(hold_cycles - target_cycles)
                score = (state_error, abs(slot_us - slot_target_us), -frames)
                candidate = {
                    "delay_outer": outer,
                    "delay_inner": inner,
                    "frames_per_state": frames,
                    "delay_cycles": delay_cycles,
                    "frame_cycles": frame_cycles,
                    "hold_cycles": hold_cycles,
                    "scan_slot_us": slot_us,
                    "actual_state_us": hold_cycles * 1_000_000 / sck_hz,
                }
                if best is None or score < best[0]:
                    best = (score, candidate)
    require(best is not None, "No timing solution satisfies the scan-slot limits")
    result = best[1]
    result["error_percent"] = (
        abs(result["actual_state_us"] - target_us) / target_us * 100
    )
    require(
        result["error_percent"] <= tolerance_percent,
        "No timing solution satisfies the requested state-duration tolerance",
    )
    return result


def segment_byte(mapping: dict[str, int], active_level: str, include_dp: bool) -> int:
    active_segments = set(mapping) if include_dp else set(mapping) - {"DP"}
    mask = sum(1 << mapping[name] for name in active_segments)
    return mask if active_level == "high" else (~mask) & 0xFF


def inactive_segment_byte(active_level: str) -> int:
    return 0x00 if active_level == "high" else 0xFF


def com_instruction(port: str, bit: int, level: str) -> str:
    op = "BSET" if level == "high" else "BCLR"
    return f"    {op} {port}_PIO,{bit}"


def render_source(request: dict[str, Any], timing: dict[str, Any]) -> str:
    pins = request["pins"]
    contract = request["seven_segment"]
    operation = contract["operation"]
    segment_pin = pins[contract["segment_pin"]]
    segment_port = segment_pin["port"]
    digits = sorted(contract["digits"], key=lambda item: item["visual_index"])
    digit_ports = {pins[digit["pin_contract"]]["port"] for digit in digits}
    require(
        segment_port not in digit_ports,
        "The deterministic generator requires segment and COM ports to be distinct",
    )
    include_dp = operation["include_dp"]
    mapping = contract["segment_mapping"]
    initial_segment = 0xFF if segment_pin["initial_level"] == "high" else 0x00
    sck_ps = request["clock"]["sck_ps"]
    require(isinstance(sck_ps, int), "The generator requires an explicit numeric SCK_PS")

    lines = [
        "; CHIP: HK64S825",
        "; 功能：数码管全部段亮与全灭按精确周期循环",
        "; 生成方式：确定性数码管生成器",
        "; 非易失配置：不修改保护位、锁定位和选项配置",
        ";",
        "; SRAM 分配：",
        "; 81H：扫描延时内层计数，临时变量",
        "; 82H：扫描延时外层计数，临时变量",
        "; 91H：状态保持帧计数，临时变量",
        "",
        "ORG 000H",
        "RESET:",
        "    JMP INIT",
        "",
        "ORG 008H",
        "    RETI",
        "",
        "ORG 010H",
        "INIT:",
        f"    MOV A,#{format_hex_byte(sck_ps)}",
        "    MOV SCK_PS,A",
        "",
        "    ; 段线整口独占，先建立推挽和安全电平，再开启输出",
        "    MOV A,#00H",
        f"    MOV {segment_port}_POD,A",
        f"    MOV A,#{format_hex_byte(initial_segment)}",
        f"    MOV {segment_port}_PIO,A",
        "    MOV A,#0FFH",
        f"    MOV {segment_port}_POE,A",
        "",
        "    ; 位选按逐位契约建立关闭电平，最后开启输出",
    ]
    physical_digits: list[tuple[str, int, str, str]] = []
    for digit in digits:
        pin = pins[digit["pin_contract"]]
        physical_digits.append(
            (
                pin["port"],
                digit["bit"],
                digit["com_active_level"],
                "low" if digit["com_active_level"] == "high" else "high",
            )
        )
    for port, bit, _active, _off in physical_digits:
        lines.append(f"    BCLR {port}_POD,{bit}")
    for port, bit, _active, off in physical_digits:
        lines.append(com_instruction(port, bit, off))
    for port, bit, _active, _off in physical_digits:
        lines.append(f"    BSET {port}_POE,{bit}")
    lines.extend(
        [
            "",
            "MAIN_LOOP:",
            "    CALL HOLD_ON",
            "    CALL HOLD_OFF",
            "    JMP MAIN_LOOP",
            "",
        ]
    )

    for state in ("ON", "OFF"):
        lines.extend(
            [
                "; 输入：无",
                f"; 输出：保持{'全亮' if state == 'ON' else '全灭'}一个状态周期",
                "; 破坏：A,81H,82H,91H",
                f"HOLD_{state}:",
                f"    MOV A,#{format_hex_byte(timing['frames_per_state'])}",
                "    MOV 91H,A",
                f"HOLD_{state}_LOOP:",
                f"    CALL SCAN_FRAME_{state}",
                "    DECSZR 91H",
                f"    JMP HOLD_{state}_LOOP",
                "    NOP",
                "    RET",
                "",
                "; 输入：无",
                "; 输出：按视觉顺序完成一帧扫描",
                "; 破坏：A,81H,82H",
                f"SCAN_FRAME_{state}:",
            ]
        )
        for index in range(len(digits)):
            lines.append(f"    CALL SCAN_{state}_{index + 1}")
        lines.extend(["    RET", ""])
        for index, digit in enumerate(digits, start=1):
            port, bit, active, _off = physical_digits[index - 1]
            active_level = digit["segment_active_level"]
            value = (
                segment_byte(mapping, active_level, include_dp)
                if state == "ON"
                else inactive_segment_byte(active_level)
            )
            lines.extend(
                [
                    "; 输入：无",
                    f"; 输出：扫描视觉第 {index} 位",
                    "; 破坏：A,81H,82H",
                    f"SCAN_{state}_{index}:",
                    "    CALL ALL_DIGITS_OFF",
                    f"    MOV A,#{format_hex_byte(value)}",
                    f"    MOV {segment_port}_PIO,A",
                    com_instruction(port, bit, active),
                    "    CALL SCAN_DELAY",
                    "    CALL ALL_DIGITS_OFF",
                    "    RET",
                    "",
                ]
            )

    lines.extend(
        [
            "; 输入：无",
            "; 输出：关闭全部位选",
            "; 破坏：位选端口锁存",
            "ALL_DIGITS_OFF:",
        ]
    )
    for port, bit, _active, off in physical_digits:
        lines.append(com_instruction(port, bit, off))
    lines.extend(
        [
            "    RET",
            "",
            "; 输入：无",
            f"; 输出：扫描槽延时约 {timing['scan_slot_us']:.3f} 微秒",
            "; 破坏：A,81H,82H",
            "SCAN_DELAY:",
            f"    MOV A,#{format_hex_byte(timing['delay_outer'])}",
            "    MOV 82H,A",
            "SCAN_DELAY_OUTER:",
            f"    MOV A,#{format_hex_byte(timing['delay_inner'])}",
            "    MOV 81H,A",
            "SCAN_DELAY_INNER:",
            "    CLRWDT",
            "    NOP",
            "    NOP",
            "    DECSZR 81H",
            "    JMP SCAN_DELAY_INNER",
            "    NOP",
            "    DECSZR 82H",
            "    JMP SCAN_DELAY_OUTER",
            "    NOP",
            "    RET",
            "",
            "END",
            "",
        ]
    )
    return "\n".join(lines)


def generate(args: argparse.Namespace) -> dict[str, Any]:
    request = read_json(args.request, "INVALID_REQUEST")
    profile = normalize_profile_paths(
        read_json(args.profile, "INVALID_PROFILE"), args.profile.parent
    )
    validate_profile(profile)
    pins = request.get("pins")
    require(isinstance(pins, dict), "pins are required")
    for name, pin in pins.items():
        require(isinstance(pin, dict), f"pins.{name} must be structured")
        if pin.get("direction") == "output":
            validate_output_pin_contract(name, pin)
    validate_seven_segment_contract(request, pins)
    validate_clock_contract(request, profile)
    operation = request["seven_segment"].get("operation")
    require(isinstance(operation, dict), "seven_segment.operation is required")
    require(
        operation.get("type") == "toggle_all_segments",
        "Only toggle_all_segments is supported",
    )
    require(isinstance(operation.get("include_dp"), bool), "operation.include_dp is required")
    target_us = operation.get("state_duration_us")
    tolerance = operation.get("tolerance_percent", 1.0)
    require(finite_positive(target_us), "operation.state_duration_us must be positive")
    require(finite_positive(tolerance), "operation.tolerance_percent must be positive")
    frame_min = operation.get("frame_count_min", 200)
    frame_max = operation.get("frame_count_max", 255)
    require(
        isinstance(frame_min, int)
        and isinstance(frame_max, int)
        and 1 <= frame_min <= frame_max <= 255,
        "frame count range must be within 1..255",
    )
    slot_min_us = operation.get("scan_slot_min_us", 700.0)
    slot_max_us = operation.get("scan_slot_max_us", 1300.0)
    require(
        finite_positive(slot_min_us)
        and finite_positive(slot_max_us)
        and slot_min_us <= slot_max_us,
        "scan slot limits are invalid",
    )
    sck_hz = derive_sck_hz(
        request["clock"]["osc_hz"],
        request["clock"]["sck_ps"],
        profile["clock_model"],
    )
    timing = solve_timing(
        digit_count=len(request["seven_segment"]["digits"]),
        sck_hz=sck_hz,
        target_us=float(target_us),
        tolerance_percent=float(tolerance),
        frame_min=frame_min,
        frame_max=frame_max,
        slot_min_us=float(slot_min_us),
        slot_max_us=float(slot_max_us),
    )
    resolved_request = json.loads(json.dumps(request))
    resolved_request["timing"] = {
        "precision": "precise",
        "delay_targets": [
            {
                "label": "HOLD_ON",
                "target_us": target_us,
                "tolerance_percent": tolerance,
            },
            {
                "label": "HOLD_OFF",
                "target_us": target_us,
                "tolerance_percent": tolerance,
            },
        ],
    }
    resolved_request["generation"] = {
        "tool": "generate_seven_segment.py",
        "sck_hz": sck_hz,
        **timing,
    }
    source = render_source(resolved_request, timing)
    args.source.parent.mkdir(parents=True, exist_ok=True)
    args.source.write_text(source, encoding="utf-8")
    write_json(args.output_request, resolved_request)
    return {
        "status": "ok",
        "code": "SEVEN_SEGMENT_GENERATED",
        "source": str(args.source.resolve()),
        "request": str(args.output_request.resolve()),
        "sck_hz": sck_hz,
        **timing,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic HK64S825 all-segments toggle scanner"
    )
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-request", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
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
