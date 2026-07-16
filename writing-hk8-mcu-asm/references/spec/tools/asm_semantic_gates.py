from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


GPIO_STATE_REGISTERS = {
    f"{port}_{register}"
    for port in ("PA", "PB")
    for register in ("POD", "PIO", "POE")
}
GPIO_SECOND_OPERAND_WRITE_OPS = {
    "ADDR",
    "ADDCR",
    "SUBR",
    "SUBCR",
    "ANDR",
    "ORR",
    "XORR",
}
GPIO_FIRST_OPERAND_WRITE_OPS = {
    "BSET",
    "BCLR",
    "BCPL",
}
GPIO_UNARY_WRITE_OPS = {
    "CLR",
    "SET",
    "CPLR",
    "INCR",
    "INCSZR",
    "DECR",
    "DECSZR",
    "RLCR",
    "RLR",
    "RRCR",
    "RRR",
    "XCH",
    "SWAPR",
}
SCK_UNARY_WRITE_OPS = GPIO_UNARY_WRITE_OPS | {"SZR"}
SCK_READ_FIRST_OPERAND_OPS = {
    "CPL",
    "INC",
    "DEC",
    "DECSZ",
    "INCSZ",
    "RLC",
    "RL",
    "RRC",
    "RR",
    "SWAP",
    "BTSZ",
    "BTSNZ",
    "SE",
    "SZ",
}
SCK_READ_SECOND_OPERAND_OPS = {
    "ADD",
    "ADDC",
    "SUB",
    "SUBC",
    "AND",
    "OR",
    "XOR",
}
SCK_NO_REGISTER_OPS = {
    "NOP",
    "RET",
    "RETI",
    "CLRWDT",
    "IDLE",
    "STOP",
    "TABL",
    "TABH",
    "JMP",
    "CALL",
}
GPIO_CONTROL_BOUNDARY_OPS = {
    "JMP",
    "CALL",
    "RET",
    "RETI",
    "DECSZ",
    "DECSZR",
    "INCSZ",
    "INCSZR",
    "BTSZ",
    "BTSNZ",
    "SE",
    "SZ",
    "SZR",
}
ACCUMULATOR_ONLY_COUNTER_OPS = {"DECSZ", "INCSZ"}
NONLINEAR_CONTROL_OPS = {"JMP", "CALL", "RET", "RETI"}
PROGRAM_MIN = 0x0000
PROGRAM_MAX = 0x03FF
ALLOWED_COUNTER_SEMANTIC_STATUSES = {
    "compiler_probe_only",
    "hardware_verified_in_project",
}
REQUIRED_COUNTER_EFFECTS = {
    "DECSZ": "A",
    "INCSZ": "A",
    "DECSZR": "R",
    "INCSZR": "R",
}
SUPPORTED_DELAY_OPS = {
    "MOV",
    "NOP",
    "CLRWDT",
    "DECR",
    "INCR",
    "DECSZ",
    "DECSZR",
    "INCSZ",
    "INCSZR",
    "SZ",
    "SZR",
    "JMP",
    "RET",
}
SUPPORTED_DELAY_FORMS = {
    "MOV A,R",
    "MOV R,A",
    "MOV A,#K",
    "RET",
}


@dataclass(frozen=True)
class DelayResult:
    label: str
    cycles: int
    sck_hz: int
    actual_us: float
    clrwdt_count: int
    steps: int


def derive_sck_hz(osc_hz: int, sck_ps: str | int, model: dict[str, Any]) -> int:
    if not isinstance(osc_hz, int) or isinstance(osc_hz, bool) or osc_hz <= 0:
        raise ValueError("OSC frequency must be a positive integer")
    if not isinstance(model, dict):
        raise ValueError("clock model must be an object")
    raw = model.get("sck_ps_reset") if sck_ps == "reset" else sck_ps
    if not isinstance(raw, int) or isinstance(raw, bool) or not 0 <= raw <= 0xFF:
        raise ValueError("SCK_PS must resolve to an 8-bit integer")
    selector = raw & 0x0F
    if selector == 0:
        raise ValueError("SCK_PS selector 0 is prohibited")
    sckhl_bit = model.get("sckhl_bit")
    if (
        not isinstance(sckhl_bit, int)
        or isinstance(sckhl_bit, bool)
        or not 0 <= sckhl_bit <= 7
    ):
        raise ValueError("clock model sckhl_bit must be a bit number 0..7")
    mode = "high" if raw & (1 << sckhl_bit) else "low"
    divider_by_mode = model.get("divider_by_mode")
    if not isinstance(divider_by_mode, dict):
        raise ValueError("clock model divider_by_mode must be an object")
    dividers = divider_by_mode.get(mode)
    if not isinstance(dividers, dict) or str(selector) not in dividers:
        raise ValueError(f"clock model lacks {mode} divider selector {selector}")
    divider = dividers[str(selector)]
    if not isinstance(divider, int) or isinstance(divider, bool) or divider <= 0:
        raise ValueError("clock divider must be a positive integer")
    return round(osc_hz / divider)


def effect_cycles(effect: dict[str, Any], skipped: bool = False) -> int:
    if not isinstance(effect, dict) or "cycles" not in effect:
        raise ValueError("instruction effect is missing cycle metadata")
    value = effect["cycles"]
    if isinstance(value, bool):
        raise ValueError(f"unsupported cycle metadata: {value!r}")
    if isinstance(value, int) and value > 0:
        return value
    if value == "1or2":
        return 2 if skipped else 1
    raise ValueError(f"unsupported cycle metadata: {value!r}")


def _delay_register(
    token: str, equ_symbols: dict[str, dict[str, Any]], line: int | None
) -> int:
    if token.strip().startswith("#"):
        raise ValueError(f"unknown register at line {line}: {token}")
    register = resolve_byte(token, equ_symbols)
    if register is None:
        raise ValueError(f"unknown register at line {line}: {token}")
    return register


def _simulate_delay(
    file_model: dict[str, Any],
    label: str,
    sck_hz: int,
    effects: dict[str, dict[str, Any]],
    *,
    max_steps: int,
    accelerate: bool,
) -> DelayResult:
    if not isinstance(sck_hz, int) or isinstance(sck_hz, bool) or sck_hz <= 0:
        raise ValueError("SCK frequency must be a positive integer")
    if (
        not isinstance(max_steps, int)
        or isinstance(max_steps, bool)
        or max_steps <= 0
    ):
        raise ValueError("max_steps must be a positive integer")
    if not isinstance(effects, dict):
        raise ValueError("instruction effects must be an object")

    instructions = file_model.get("_instructions", [])
    labels = file_model.get("labels", {})
    equ_symbols = file_model.get("_equ_symbols", {})
    address_to_index = collect_unique_address_indices(instructions)
    start = labels.get(label.upper()) if isinstance(labels, dict) else None
    start_address = start.get("address") if isinstance(start, dict) else None
    if (
        not isinstance(start_address, int)
        or isinstance(start_address, bool)
        or start_address not in address_to_index
    ):
        raise ValueError(f"delay label cannot be resolved: {label}")

    parsed_args = [split_args(item.get("args", "")) for item in instructions]
    forms: list[str | None] = [None] * len(instructions)
    register_operands: list[int | None] = [None] * len(instructions)
    immediate_operands: list[int | None] = [None] * len(instructions)
    direct_targets: list[int | None] = [None] * len(instructions)
    normal_cycles: list[int | None] = [None] * len(instructions)
    skipped_cycles: list[int | None] = [None] * len(instructions)
    compile_errors: list[str | None] = [None] * len(instructions)
    skip_ops = {"DECSZ", "DECSZR", "INCSZ", "INCSZR", "SZ", "SZR"}
    for index, instruction in enumerate(instructions):
        op = instruction.get("op")
        args = parsed_args[index]
        line = instruction.get("line")
        try:
            if op not in SUPPORTED_DELAY_OPS:
                raise ValueError(
                    f"unsupported delay instruction at line {line}: {op}"
                )
            effect_key = op
            if op == "MOV":
                if len(args) != 2:
                    raise ValueError(f"unsupported MOV at line {line}")
                destination, source = args[0].upper(), args[1]
                if destination == "A" and source.startswith("#"):
                    effect_key = "MOV A,#K"
                    value = resolve_byte(source, equ_symbols)
                    if value is None:
                        raise ValueError(
                            f"unknown MOV immediate at line {line}: {source}"
                        )
                    immediate_operands[index] = value
                    forms[index] = "load-immediate"
                elif destination == "A" and source.upper() != "A":
                    effect_key = "MOV A,R"
                    register_operands[index] = _delay_register(
                        source, equ_symbols, line
                    )
                    forms[index] = "load-register"
                elif source.upper() == "A" and destination != "A":
                    effect_key = "MOV R,A"
                    register_operands[index] = _delay_register(
                        destination, equ_symbols, line
                    )
                    forms[index] = "store-register"
                else:
                    raise ValueError(f"unsupported MOV form at line {line}")
            elif op in {"NOP", "CLRWDT", "RET"}:
                if any(args):
                    raise ValueError(f"unsupported {op} operands at line {line}")
            elif op in {
                "DECR",
                "INCR",
                "DECSZ",
                "DECSZR",
                "INCSZ",
                "INCSZR",
                "SZ",
                "SZR",
            }:
                if len(args) != 1 or not args[0]:
                    raise ValueError(f"unsupported {op} operands at line {line}")
                register_operands[index] = _delay_register(
                    args[0], equ_symbols, line
                )
            elif op == "JMP":
                if len(args) != 1 or not args[0]:
                    raise ValueError(f"unsupported JMP operands at line {line}")
                target = resolve_direct_target_address(file_model, instruction)
                if target is None:
                    raise ValueError(
                        f"jump target cannot be resolved at line {line}: {args[0]}"
                    )
                direct_targets[index] = target

            effect = effects.get(effect_key)
            if effect is None:
                raise ValueError(f"instruction effect is missing for {effect_key}")
            normal_cycles[index] = effect_cycles(effect)
            skipped_cycles[index] = (
                effect_cycles(effect, skipped=True)
                if op in skip_ops
                else normal_cycles[index]
            )
        except ValueError as exc:
            compile_errors[index] = str(exc)

    def fetch(address: int) -> tuple[int, dict[str, Any], list[str]]:
        index = address_to_index.get(address)
        if index is None or not instruction_uniquely_owns_word(
            file_model, address, index
        ):
            owner = file_model.get("_word_owners", {}).get(address)
            if isinstance(owner, dict) and owner.get("instruction_index") is None:
                raise ValueError(
                    f"delay execution reached {owner.get('kind')} word at {address:#06x}"
                )
            raise ValueError(
                f"delay execution reached a gap or ambiguous word at {address:#06x}"
            )
        if compile_errors[index] is not None:
            raise ValueError(compile_errors[index])
        return index, instructions[index], parsed_args[index]

    def next_word(address: int, distance: int = 1) -> int:
        target = address + distance
        fetch(target)
        return target

    cycles = effect_cycles(effects.get("CALL", {}))
    pc = start_address
    accumulator: int | None = None
    registers: dict[int, int] = {}
    clrwdt_count = 0
    steps = 0

    def accelerated_countdown(
        start_address: int,
    ) -> tuple[int, int, int, int, int] | None:
        prefix_cycles = 0
        prefix_steps = 0
        prefix_clrwdt = 0
        address = start_address
        try:
            while True:
                _prefix_index, prefix, _prefix_args = fetch(address)
                prefix_op = prefix.get("op")
                if prefix_op not in {"NOP", "CLRWDT"}:
                    break
                prefix_cycles += normal_cycles[_prefix_index]
                prefix_steps += 1
                prefix_clrwdt += int(prefix_op == "CLRWDT")
                address += 1

            _counter_index, counter, _counter_args = fetch(address)
            counter_op = counter.get("op")
            if counter_op not in {"DECSZR", "INCSZR"}:
                return None
            register = register_operands[_counter_index]
            if register is None:
                return None
            if register not in registers:
                return None
            _jump_index, jump, _jump_args = fetch(address + 1)
            if jump.get("op") != "JMP":
                return None
            target = direct_targets[_jump_index]
            if target != start_address:
                return None
            fetch(address + 2)
        except ValueError:
            return None

        value = registers[register]
        if counter_op == "DECSZR":
            iterations = value if value else 256
        else:
            iterations = 256 - value if value else 256
        cycles_delta = (
            prefix_cycles * iterations
            + normal_cycles[_counter_index] * (iterations - 1)
            + skipped_cycles[_counter_index]
            + normal_cycles[_jump_index] * (iterations - 1)
        )
        steps_delta = (prefix_steps + 1) * iterations + (iterations - 1)
        return (
            address + 2,
            cycles_delta,
            steps_delta,
            prefix_clrwdt * iterations,
            register,
        )

    while True:
        if steps >= max_steps:
            raise ValueError(f"delay routine exceeded {max_steps} interpreter steps")
        _index, instruction, args = fetch(pc)
        op = instruction.get("op")
        line = instruction.get("line")
        cycle = normal_cycles[_index]
        if cycle is None:
            raise ValueError(f"instruction cycle is unavailable at line {line}")

        if accelerate:
            accelerated = accelerated_countdown(pc)
            if accelerated is not None:
                next_pc, cycle_delta, step_delta, wdt_delta, register = accelerated
                if steps + step_delta > max_steps:
                    raise ValueError(
                        f"delay routine exceeded {max_steps} interpreter steps"
                    )
                cycles += cycle_delta
                steps += step_delta
                clrwdt_count += wdt_delta
                registers[register] = 0
                pc = next_pc
                continue
        steps += 1

        if op == "MOV":
            form = forms[_index]
            if form == "load-immediate":
                accumulator = immediate_operands[_index]
            elif form == "load-register":
                register = register_operands[_index]
                if register not in registers:
                    raise ValueError(f"unknown MOV source at line {line}: {args[1]}")
                accumulator = registers[register]
            elif form == "store-register":
                register = register_operands[_index]
                if accumulator is None:
                    raise ValueError(f"A is unknown at line {line}")
                registers[register] = accumulator
            else:
                raise ValueError(f"unsupported MOV form at line {line}")
            cycles += cycle
            pc = next_word(pc)
            continue

        if op in {"NOP", "CLRWDT"}:
            cycles += cycle
            if op == "CLRWDT":
                clrwdt_count += 1
            pc = next_word(pc)
            continue

        if op in {"DECR", "INCR"}:
            register = register_operands[_index]
            if register not in registers:
                raise ValueError(f"counter is unknown at line {line}: {args[0]}")
            delta = -1 if op == "DECR" else 1
            registers[register] = (registers[register] + delta) & 0xFF
            cycles += cycle
            pc = next_word(pc)
            continue

        if op in {"DECSZ", "DECSZR", "INCSZ", "INCSZR"}:
            register = register_operands[_index]
            if register not in registers:
                raise ValueError(f"counter is unknown at line {line}: {args[0]}")
            delta = -1 if op.startswith("DEC") else 1
            result = (registers[register] + delta) & 0xFF
            if op.endswith("R"):
                registers[register] = result
            else:
                accumulator = result
            skipped = result == 0
            cycles += skipped_cycles[_index] if skipped else cycle
            if skipped:
                next_word(pc)
                pc = next_word(pc, 2)
            else:
                pc = next_word(pc)
            continue

        if op in {"SZ", "SZR"}:
            register = register_operands[_index]
            if register not in registers:
                raise ValueError(f"counter is unknown at line {line}: {args[0]}")
            result = registers[register]
            if op == "SZ":
                accumulator = result
            skipped = result == 0
            cycles += skipped_cycles[_index] if skipped else cycle
            if skipped:
                next_word(pc)
                pc = next_word(pc, 2)
            else:
                pc = next_word(pc)
            continue

        if op == "JMP":
            target = direct_targets[_index]
            fetch(target)
            cycles += cycle
            pc = target
            continue

        if op == "RET":
            cycles += cycle
            return DelayResult(
                label=label,
                cycles=cycles,
                sck_hz=sck_hz,
                actual_us=cycles * 1_000_000 / sck_hz,
                clrwdt_count=clrwdt_count,
                steps=steps,
            )

        raise ValueError(f"unhandled delay instruction: {op}")


def simulate_delay(
    file_model: dict[str, Any],
    label: str,
    sck_hz: int,
    effects: dict[str, dict[str, Any]],
    max_steps: int = 10_000_000,
) -> DelayResult:
    return _simulate_delay(
        file_model,
        label,
        sck_hz,
        effects,
        max_steps=max_steps,
        accelerate=True,
    )


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


def split_args(args: str) -> list[str]:
    return [part.strip() for part in args.split(",")]


def collect_unique_address_indices(
    instructions: list[dict[str, Any]],
) -> dict[int, int]:
    address_to_index: dict[int, int] = {}
    ambiguous_addresses: set[int] = set()
    for index, instruction in enumerate(instructions):
        address = instruction.get("address")
        if not isinstance(address, int) or isinstance(address, bool):
            continue
        if address in address_to_index:
            ambiguous_addresses.add(address)
        else:
            address_to_index[address] = index
    for address in ambiguous_addresses:
        address_to_index.pop(address, None)
    return address_to_index


def instruction_uniquely_owns_word(
    file_model: dict[str, Any], address: int, instruction_index: int
) -> bool:
    ambiguous_addresses = file_model.get("_ambiguous_word_addresses", set())
    if address in ambiguous_addresses:
        return False
    owners = file_model.get("_word_owners", {})
    owner = owners.get(address) if isinstance(owners, dict) else None
    return (
        isinstance(owner, dict)
        and owner.get("instruction_index") == instruction_index
    )


def resolve_direct_target_address(
    file_model: dict[str, Any], instruction: dict[str, Any]
) -> int | None:
    args = split_args(instruction.get("args", ""))
    if not args or not args[0]:
        return None
    target = args[0].upper()
    if target in file_model.get("_duplicate_label_names", set()):
        return None
    label = file_model.get("labels", {}).get(target)
    if label is not None:
        address = label.get("address")
    else:
        symbol = file_model.get("_equ_symbols", {}).get(target)
        address = symbol.get("value") if symbol is not None else None
    if (
        not isinstance(address, int)
        or isinstance(address, bool)
        or not PROGRAM_MIN <= address <= PROGRAM_MAX
    ):
        return None
    return address


def direct_callee_clears_wdt(
    file_model: dict[str, Any],
    call_instruction: dict[str, Any],
    instructions: list[dict[str, Any]],
    address_to_index: dict[int, int],
    effects: dict[str, dict[str, Any]],
    reachable_indices: set[int],
    caller_indices: set[int],
) -> bool:
    target_address = resolve_direct_target_address(file_model, call_instruction)
    if target_address is None:
        return False
    callee_index = address_to_index.get(target_address)
    if (
        callee_index is None
        or callee_index in caller_indices
        or callee_index not in reachable_indices
    ):
        return False

    saw_clrwdt = False
    current_index = callee_index
    current_address = target_address
    while current_index < len(instructions):
        instruction = instructions[current_index]
        if (
            current_index in caller_indices
            or instruction.get("address") != current_address
            or address_to_index.get(current_address) != current_index
            or not instruction_uniquely_owns_word(
                file_model, current_address, current_index
            )
            or current_index not in reachable_indices
        ):
            return False
        op = instruction["op"]
        if op == "RET":
            return not instruction["args"].strip() and saw_clrwdt
        if (
            op in NONLINEAR_CONTROL_OPS
            or effects.get(op, {}).get("skip") is True
        ):
            return False
        if op == "CLRWDT":
            saw_clrwdt = True
        current_index += 1
        current_address += 1
    return False


def load_instruction_effects(path: Path) -> dict[str, dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"instruction reference cannot be read: {exc}") from exc

    try:
        variants = document["variants"]
        if not isinstance(variants, list):
            raise TypeError("variants must be a list")
        effects: dict[str, dict[str, Any]] = {}
        required_variants = {
            mnemonic: [] for mnemonic in REQUIRED_COUNTER_EFFECTS
        }
        required_delay_effects = (
            SUPPORTED_DELAY_FORMS | (SUPPORTED_DELAY_OPS - {"MOV"}) | {"CALL"}
        )
        required_delay_variant_counts = {
            effect_key: 0 for effect_key in required_delay_effects
        }
        for variant in variants:
            mnemonic = variant["mnemonic"].upper()
            form = " ".join(variant["asm_syntax"].upper().split())
            notes = (variant.get("raw_notes") or "").strip()
            effect = {
                "writes": (
                    "R"
                    if notes.startswith("R ←")
                    else "A"
                    if notes.startswith("A ←")
                    else None
                ),
                "skip": "THEN SKIP" in notes.upper(),
                "cycles": variant["cycles"],
                "notes": notes,
                "semantic_status": variant["semantic_status"],
                "delivery_policy": variant["delivery_policy"],
            }
            if mnemonic == "MOV":
                if form not in SUPPORTED_DELAY_FORMS:
                    continue
                effect_key = form
            elif mnemonic == "RET":
                if form != "RET":
                    continue
                effect_key = "RET"
            else:
                effect_key = mnemonic
            if effect_key in required_delay_variant_counts:
                required_delay_variant_counts[effect_key] += 1
            previous_effect = effects.get(effect_key)
            if (
                previous_effect is not None
                and (
                    previous_effect["skip"] != effect["skip"]
                    or previous_effect["cycles"] != effect["cycles"]
                    or previous_effect["writes"] != effect["writes"]
                    or previous_effect["semantic_status"]
                    != effect["semantic_status"]
                    or previous_effect["delivery_policy"]
                    != effect["delivery_policy"]
                )
            ):
                raise ValueError(
                    f"instruction reference {effect_key} has conflicting semantics or safety metadata"
                )
            effects.setdefault(effect_key, effect)
            if mnemonic in required_variants:
                required_variants[mnemonic].append(effect)
    except (AttributeError, KeyError, TypeError) as exc:
        raise ValueError(f"instruction reference has invalid structure: {exc}") from exc

    for mnemonic, expected_writes in REQUIRED_COUNTER_EFFECTS.items():
        variants_for_mnemonic = required_variants[mnemonic]
        if len(variants_for_mnemonic) != 1:
            raise ValueError(
                f"instruction reference {mnemonic} must have exactly one variant; "
                f"found {len(variants_for_mnemonic)}"
            )
        effect = variants_for_mnemonic[0]
        if effect["skip"] is not True or effect["writes"] != expected_writes:
            raise ValueError(
                f"instruction reference {mnemonic} must have skip=True and "
                f"writes={expected_writes}; got skip={effect['skip']!r}, "
                f"writes={effect['writes']!r}"
            )
        if effect["delivery_policy"] != "allowed":
            raise ValueError(
                f"instruction reference {mnemonic} delivery_policy must be 'allowed'; "
                f"got {effect['delivery_policy']!r}"
            )
        semantic_status = effect["semantic_status"]
        if (
            not isinstance(semantic_status, str)
            or semantic_status not in ALLOWED_COUNTER_SEMANTIC_STATUSES
        ):
            raise ValueError(
                f"instruction reference {mnemonic} semantic_status must be one of "
                f"{sorted(ALLOWED_COUNTER_SEMANTIC_STATUSES)!r}; got {semantic_status!r}"
            )
    for effect_key, count in sorted(required_delay_variant_counts.items()):
        if count != 1:
            raise ValueError(
                f"instruction reference {effect_key} must have exactly one safe variant; "
                f"found {count}"
            )
    skip_effects = {"DECSZ", "DECSZR", "INCSZ", "INCSZR", "SZ", "SZR"}
    for effect_key in sorted(required_delay_effects):
        effect = effects[effect_key]
        expected_skip = effect_key in skip_effects
        if effect.get("skip") is not expected_skip:
            raise ValueError(
                f"instruction reference {effect_key} skip must be {expected_skip}"
            )
        if expected_skip:
            if effect.get("cycles") != "1or2":
                raise ValueError(
                    f"instruction reference {effect_key} cycles must be '1or2'"
                )
            effect_cycles(effect)
            effect_cycles(effect, skipped=True)
        else:
            effect_cycles(effect)
        expected_policy = (
            "label_or_equ_target_only"
            if effect_key in {"JMP", "CALL"}
            else "allowed"
        )
        if effect.get("delivery_policy") != expected_policy:
            raise ValueError(
                f"instruction reference {effect_key} delivery_policy must be "
                f"{expected_policy!r}; got {effect.get('delivery_policy')!r}"
            )
        semantic_status = effect.get("semantic_status")
        if semantic_status not in ALLOWED_COUNTER_SEMANTIC_STATUSES:
            raise ValueError(
                f"instruction reference {effect_key} semantic_status must be one of "
                f"{sorted(ALLOWED_COUNTER_SEMANTIC_STATUSES)!r}; got {semantic_status!r}"
            )
    return effects


def audit_counter_loops(
    file_model: dict[str, Any], effects: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    instructions = file_model.get("_instructions", [])
    address_to_index = collect_unique_address_indices(instructions)
    reachable_indices = collect_reachable_instruction_indices(file_model)

    issues: list[dict[str, Any]] = []
    for index, instruction in enumerate(instructions[:-1]):
        if instruction["op"] not in ACCUMULATOR_ONLY_COUNTER_OPS:
            continue
        effect = effects.get(instruction["op"])
        if effect is None or effect.get("skip") is not True:
            continue
        jump = instructions[index + 1]
        if jump["op"] != "JMP":
            continue
        instruction_address = instruction.get("address")
        jump_address = jump.get("address")
        if (
            not isinstance(instruction_address, int)
            or isinstance(instruction_address, bool)
            or not isinstance(jump_address, int)
            or isinstance(jump_address, bool)
            or jump_address != instruction_address + 1
            or address_to_index.get(instruction_address) != index
            or address_to_index.get(jump_address) != index + 1
            or not instruction_uniquely_owns_word(
                file_model, instruction_address, index
            )
            or not instruction_uniquely_owns_word(
                file_model, jump_address, index + 1
            )
        ):
            continue
        jump_args = split_args(jump["args"])
        if not jump_args or not jump_args[0]:
            continue
        target = jump_args[0].upper()
        target_address = resolve_direct_target_address(file_model, jump)
        if target_address is None or target_address > instruction_address:
            continue
        loop_start = address_to_index.get(target_address)
        if loop_start is None or effect.get("writes") != "A":
            continue

        op = instruction["op"]
        operand_args = split_args(instruction["args"])
        operand = operand_args[0] if operand_args and operand_args[0] else "<operand>"
        issues.append(
            make_issue(
                "HK-SYN-012",
                "BLOCKER",
                file_model["path"],
                instruction["line"],
                f"{op} writes A instead of operand {operand}; following JMP {target} "
                f"is a backward/same target ({target_address:#06x} <= "
                f"{instruction_address:#06x})",
                "The loop-carried counter is not written back, so the loop may never progress "
                "to its exit path.",
                f"Replace {op} with {op}R for operand write-back and recalculate loop timing.",
            )
        )

        if loop_start > index:
            continue
        loop_slice = instructions[loop_start : index + 2]
        slice_is_contiguous = all(
            candidate.get("address") == target_address + offset
            and address_to_index.get(target_address + offset) == loop_start + offset
            and instruction_uniquely_owns_word(
                file_model, target_address + offset, loop_start + offset
            )
            for offset, candidate in enumerate(loop_slice)
        )
        required_reachable = {loop_start, index, index + 1}
        if (
            not slice_is_contiguous
            or not required_reachable.issubset(reachable_indices)
        ):
            continue
        caller_indices = set(range(loop_start, index + 2))
        prefix_is_proven = True
        wdt_clear_is_proven = False
        for candidate in loop_slice[:-2]:
            if candidate["op"] == "CLRWDT":
                wdt_clear_is_proven = True
                continue
            if candidate["op"] == "CALL":
                if not direct_callee_clears_wdt(
                    file_model,
                    candidate,
                    instructions,
                    address_to_index,
                    effects,
                    reachable_indices,
                    caller_indices,
                ):
                    prefix_is_proven = False
                    break
                wdt_clear_is_proven = True
                continue
            if (
                candidate["op"] in NONLINEAR_CONTROL_OPS
                or effects.get(candidate["op"], {}).get("skip") is True
            ):
                prefix_is_proven = False
                break
        if prefix_is_proven and wdt_clear_is_proven:
            issues.append(
                make_issue(
                    "HK-WDT-002",
                    "BLOCKER",
                    file_model["path"],
                    instruction["line"],
                    f"loop ending at JMP {target} executes CLRWDT inline or through a "
                    f"proven direct callee, but "
                    f"{op} writes A and counter operand {operand} is not written back",
                    "The watchdog is continuously cleared while a non-progressing loop can "
                    "run forever.",
                    f"Replace {op} with {op}R, recalculate timing, and prove the loop reaches "
                    "its exit after a finite number of iterations.",
                )
            )
    return issues


def resolve_byte(token: str, equ_symbols: dict[str, dict[str, Any]]) -> int | None:
    value = token.strip()
    if value.startswith("#"):
        value = value[1:].strip()

    def resolve(candidate: str, seen: set[str]) -> int | None:
        key = candidate.upper()
        if key == "SCK_PS":
            return 0x10
        symbol = equ_symbols.get(key)
        if symbol is not None:
            if key in seen:
                return None
            resolved = symbol.get("value")
            if (
                isinstance(resolved, int)
                and not isinstance(resolved, bool)
                and 0 <= resolved <= 0xFF
            ):
                return resolved
            alias = symbol.get("alias")
            if isinstance(alias, str) and alias:
                return resolve(alias, seen | {key})
            return None
        try:
            if candidate.lower().startswith("0x"):
                resolved = int(candidate, 16)
            elif candidate.upper().endswith("H"):
                resolved = int(candidate[:-1], 16)
            elif candidate.isdecimal():
                resolved = int(candidate, 10)
            else:
                return None
        except ValueError:
            return None
        return resolved if 0 <= resolved <= 0xFF else None

    return resolve(value, set())


def gpio_written_register(instruction: dict[str, Any]) -> str | None:
    op = instruction["op"]
    args = split_args(instruction["args"])
    if op == "MOV" and len(args) == 2 and args[0].upper() != "A":
        return args[0].upper()
    if op in GPIO_SECOND_OPERAND_WRITE_OPS and len(args) == 2:
        return args[1].upper()
    if op in GPIO_FIRST_OPERAND_WRITE_OPS and len(args) == 2:
        return args[0].upper()
    if op in GPIO_UNARY_WRITE_OPS and len(args) == 1:
        return args[0].upper()
    return None


def collect_gpio_effects(file_model: dict[str, Any]) -> list[dict[str, Any]]:
    instructions = file_model.get("_instructions", [])
    equ_symbols = file_model.get("_equ_symbols", {})
    effects: list[dict[str, Any]] = []
    safe_write_indices: set[int] = set()
    for index, instruction in enumerate(instructions):
        args = split_args(instruction["args"])
        if instruction["op"] in {"BSET", "BCLR"} and len(args) == 2:
            bit = resolve_byte(args[1], equ_symbols)
            if bit is not None and 0 <= bit <= 7:
                safe_write_indices.add(index)
                effects.append(
                    {
                        "register": args[0].upper(),
                        "set_bits": {bit} if instruction["op"] == "BSET" else set(),
                        "clear_bits": {bit} if instruction["op"] == "BCLR" else set(),
                        "line": instruction["line"],
                        "index": index,
                        "source": instruction["source"],
                        "kind": "bit",
                    }
                )
        if index + 2 >= len(instructions):
            continue
        load, logic, store = instructions[index : index + 3]
        load_args = split_args(load["args"])
        logic_args = split_args(logic["args"])
        store_args = split_args(store["args"])
        if not (
            load["op"] == "MOV"
            and len(load_args) == 2
            and load_args[0].upper() == "A"
            and logic["op"] in {"AND", "OR"}
            and len(logic_args) == 2
            and logic_args[0].upper() == "A"
            and logic_args[1].startswith("#")
            and store["op"] == "MOV"
            and len(store_args) == 2
            and store_args[1].upper() == "A"
            and load_args[1].upper() == store_args[0].upper()
        ):
            continue
        mask = resolve_byte(logic_args[1], equ_symbols)
        if mask is None:
            continue
        safe_write_indices.add(index + 2)
        effects.append(
            {
                "register": store_args[0].upper(),
                "set_bits": {
                    bit for bit in range(8) if logic["op"] == "OR" and mask & (1 << bit)
                },
                "clear_bits": {
                    bit for bit in range(8) if logic["op"] == "AND" and not mask & (1 << bit)
                },
                "line": store["line"],
                "index": index + 2,
                "source": store["source"],
                "kind": "rmw",
            }
        )
    for index, instruction in enumerate(instructions):
        register = gpio_written_register(instruction)
        if register not in GPIO_STATE_REGISTERS or index in safe_write_indices:
            continue
        effects.append(
            {
                "register": register,
                "set_bits": set(),
                "clear_bits": set(),
                "line": instruction["line"],
                "index": index,
                "source": instruction["source"],
                "kind": "unknown",
            }
        )
    return sorted(effects, key=lambda effect: (effect["line"], effect["index"]))


def collect_reachable_instruction_indices(file_model: dict[str, Any]) -> set[int]:
    instructions = file_model.get("_instructions", [])
    if not instructions:
        return set()
    address_to_index = collect_unique_address_indices(instructions)
    source_addresses = [
        instruction.get("address")
        for instruction in instructions
        if isinstance(instruction.get("address"), int)
        and not isinstance(instruction.get("address"), bool)
    ]
    if not source_addresses:
        return set()
    entry_address = 0 if 0 in source_addresses else min(source_addresses)
    entry_index = address_to_index.get(entry_address)
    if entry_index is None:
        return set()

    def sequential_index(index: int, distance: int = 1) -> int | None:
        candidate = index + distance
        if candidate >= len(instructions):
            return None
        address = instructions[index]["address"]
        if address_to_index.get(address) != index:
            return None
        if any(
            instructions[index + offset]["address"] != address + offset
            or address_to_index.get(address + offset) != index + offset
            for offset in range(1, distance + 1)
        ):
            return None
        return candidate

    def direct_target_index(instruction: dict[str, Any]) -> int | None:
        target_address = resolve_direct_target_address(file_model, instruction)
        return (
            None if target_address is None else address_to_index.get(target_address)
        )

    reachable: set[int] = set()
    pending = [entry_index]
    while pending:
        index = pending.pop()
        if index in reachable:
            continue
        reachable.add(index)
        instruction = instructions[index]
        op = instruction["op"]
        successors: list[int | None]
        if op in {"RET", "RETI"}:
            successors = []
        elif op == "JMP":
            successors = [direct_target_index(instruction)]
        elif op == "CALL":
            successors = [direct_target_index(instruction), sequential_index(index)]
        elif op in GPIO_CONTROL_BOUNDARY_OPS:
            successors = [sequential_index(index), sequential_index(index, 2)]
        else:
            successors = [sequential_index(index)]
        pending.extend(successor for successor in successors if successor is not None)
    return reachable


def audit_gpio_contract(
    file_model: dict[str, Any], request: dict[str, Any]
) -> list[dict[str, Any]]:
    if "pins" not in request:
        return []
    pins = request["pins"]
    if not isinstance(pins, dict):
        return [
            make_issue(
                "HK-AI-003",
                "ERROR",
                "<request>",
                None,
                "request pins must be an object when present",
                "Malformed pin contracts cannot be bound to source-level GPIO safety checks.",
                "Provide pins as an object containing string mappings or structured pin contracts.",
            )
        ]

    instructions = file_model.get("_instructions", [])
    effects = collect_gpio_effects(file_model)
    reachable_indices = collect_reachable_instruction_indices(file_model)
    issues: list[dict[str, Any]] = []
    contracts: list[dict[str, Any]] = []
    owned_bits_by_port: dict[str, set[int]] = {}
    for pin_name in sorted(pins):
        pin = pins[pin_name]
        if isinstance(pin, str):
            continue
        if not isinstance(pin, dict):
            issues.append(
                make_issue(
                    "HK-AI-003",
                    "ERROR",
                    "<request>",
                    None,
                    f"pins.{pin_name} must be a string or object",
                    "The checker cannot classify this pin entry as legacy or structured.",
                    f"Replace pins.{pin_name} with a string mapping or structured pin object.",
                )
            )
            continue
        direction = pin.get("direction")
        if direction not in {"input", "output"}:
            issues.append(
                make_issue(
                    "HK-AI-003",
                    "ERROR",
                    "<request>",
                    None,
                    f"pins.{pin_name}.direction must be input or output",
                    "A missing or invalid direction can bypass output-specific GPIO safety checks.",
                    f"Set pins.{pin_name}.direction explicitly to input or output.",
                )
            )
            continue
        if direction == "input":
            continue
        port = pin.get("port")
        bits = pin.get("bits")
        drive = pin.get("drive")
        active_level = pin.get("active_level")
        initial_state = pin.get("initial_state")
        contract_errors: list[str] = []
        if not isinstance(port, str) or port.upper() not in {"PA", "PB"}:
            contract_errors.append(f"pins.{pin_name}.port must be PA or PB")
        bits_are_valid = (
            isinstance(bits, list)
            and bool(bits)
            and all(
                isinstance(bit, int) and not isinstance(bit, bool) and 0 <= bit <= 7
                for bit in bits
            )
            and len(set(bits)) == len(bits)
        )
        if not bits_are_valid:
            contract_errors.append(
                f"pins.{pin_name}.bits must be a non-empty unique list of bit numbers 0..7"
            )
        if drive not in {"push_pull", "open_drain"}:
            contract_errors.append(
                f"pins.{pin_name}.drive must be push_pull or open_drain"
            )
        if active_level not in {"high", "low"}:
            contract_errors.append(f"pins.{pin_name}.active_level must be high or low")
        if initial_state not in {"on", "off"}:
            contract_errors.append(f"pins.{pin_name}.initial_state must be on or off")
        if not isinstance(pin.get("preserve_unowned_bits"), bool):
            contract_errors.append(
                f"pins.{pin_name}.preserve_unowned_bits must be boolean"
            )
        if contract_errors:
            issues.append(
                make_issue(
                    "HK-AI-003",
                    "ERROR",
                    "<request>",
                    None,
                    "; ".join(contract_errors),
                    "Malformed output contracts make GPIO mode, polarity, ownership, "
                    "or safety ambiguous.",
                    f"Correct the structured fields for pins.{pin_name} before source audit.",
                )
            )
            continue
        port = port.upper()
        owned_bits = set(bits)
        contract = {
            "name": pin_name,
            "pin": pin,
            "port": port,
            "owned_bits": owned_bits,
        }
        contracts.append(contract)
        owned_bits_by_port.setdefault(port, set()).update(owned_bits)

    contract_ports = set(owned_bits_by_port)
    for effect in effects:
        if effect["kind"] != "unknown":
            continue
        port = effect["register"].split("_", 1)[0]
        if port not in contract_ports:
            continue
        issues.append(
            make_issue(
                "HK-GPIO-002",
                "BLOCKER",
                file_model["path"],
                effect["line"],
                f"unknown GPIO write {effect['source']} targets {effect['register']} "
                f"at instruction index {effect['index']}",
                "The checker cannot prove which GPIO bits or electrical state this write changes.",
                "Replace the write with BSET/BCLR on owned bits or an exact MOV/AND-or-OR/MOV "
                "read-modify-write sequence.",
            )
        )

    preserve_ports = {
        contract["port"]
        for contract in contracts
        if contract["pin"].get("preserve_unowned_bits") is True
    }
    for port in sorted(preserve_ports):
        task_owned_bits = owned_bits_by_port[port]
        port_registers = {f"{port}_POD", f"{port}_PIO", f"{port}_POE"}
        for effect in effects:
            if effect["register"] not in port_registers:
                continue
            touched_bits = effect["set_bits"] | effect["clear_bits"]
            unowned_bits = touched_bits - task_owned_bits
            if not unowned_bits:
                continue
            issues.append(
                make_issue(
                    "HK-GPIO-002",
                    "BLOCKER",
                    file_model["path"],
                    effect["line"],
                    f"{port} {effect['register']} {effect['kind']} effect touches task-"
                    f"unowned bits {sorted(unowned_bits)}; task-owned union is "
                    f"{sorted(task_owned_bits)}",
                    "The source changes GPIO bits outside every output PinContract on the port.",
                    f"Limit {port} POD/PIO/POE bit and RMW effects to task-owned bits "
                    f"{sorted(task_owned_bits)}.",
                )
            )

    for contract in contracts:
        pin_name = contract["name"]
        pin = contract["pin"]
        port = contract["port"]
        owned_bits = contract["owned_bits"]
        drive = pin["drive"]
        active_level = pin["active_level"]
        initial_state = pin["initial_state"]

        mode_register = f"{port}_POD"
        data_register = f"{port}_PIO"
        enable_register = f"{port}_POE"
        mode_action = "clear_bits" if drive == "push_pull" else "set_bits"
        initial_high = (initial_state == "on") == (active_level == "high")
        data_action = "set_bits" if initial_high else "clear_bits"

        for bit in sorted(owned_bits):
            enable_effect = next(
                (
                    effect
                    for effect in effects
                    if effect["register"] == enable_register and bit in effect["set_bits"]
                ),
                None,
            )
            enable_line = enable_effect["line"] if enable_effect is not None else None
            effects_before_enable = [
                effect
                for effect in effects
                if enable_line is None or effect["line"] < enable_line
            ]
            mode_effects = [
                effect
                for effect in effects_before_enable
                if effect["register"] == mode_register
                and bit in effect["set_bits"] | effect["clear_bits"]
            ]
            data_effects = [
                effect
                for effect in effects_before_enable
                if effect["register"] == data_register
                and bit in effect["set_bits"] | effect["clear_bits"]
            ]
            mode_effect = mode_effects[-1] if mode_effects else None
            data_effect = data_effects[-1] if data_effects else None

            def effect_action(effect: dict[str, Any] | None) -> str | None:
                if effect is None:
                    return None
                if bit in effect["set_bits"]:
                    return "set_bits"
                if bit in effect["clear_bits"]:
                    return "clear_bits"
                return None

            final_mode_action = effect_action(mode_effect)
            final_data_action = effect_action(data_effect)
            state_errors: list[str] = []
            if mode_effect is None:
                state_errors.append(
                    f"lacks {mode_register} {mode_action} before first {enable_register} set"
                )
            elif final_mode_action != mode_action:
                state_errors.append(
                    f"final {mode_register} action before enable is "
                    f"{final_mode_action}@{mode_effect['line']}, required {mode_action}"
                )
            if data_effect is None:
                state_errors.append(
                    f"lacks {data_register} {data_action} before first {enable_register} set"
                )
            elif final_data_action != data_action:
                state_errors.append(
                    f"final {data_register} action before enable is "
                    f"{final_data_action}@{data_effect['line']}, required {data_action}"
                )
            if enable_effect is None:
                state_errors.append(f"lacks first {enable_register} set")

            unreachable_effects = [
                (name, effect)
                for name, effect in (
                    (mode_register, mode_effect),
                    (data_register, data_effect),
                    (enable_register, enable_effect),
                )
                if effect is not None and effect["index"] not in reachable_indices
            ]
            if unreachable_effects:
                unreachable_evidence = ", ".join(
                    f"{name}@{effect['line']}"
                    for name, effect in unreachable_effects
                )
                state_errors.append(
                    f"unreachable GPIO effect(s) {unreachable_evidence} cannot prove initialization"
                )

            control_boundaries: list[dict[str, Any]] = []
            if mode_effect is not None and enable_effect is not None:
                control_boundaries = [
                    instruction
                    for index, instruction in enumerate(instructions)
                    if mode_effect["index"] < index < enable_effect["index"]
                    and instruction["op"] in GPIO_CONTROL_BOUNDARY_OPS
                ]
            if control_boundaries:
                boundary_evidence = ", ".join(
                    f"{instruction['op']}@{instruction['line']}"
                    for instruction in control_boundaries
                )
                state_errors.append(
                    f"control-flow boundary {boundary_evidence} lies between final "
                    f"{mode_register} and first {enable_register} set"
                )

            ordered = (
                mode_effect is not None
                and data_effect is not None
                and enable_effect is not None
                and final_mode_action == mode_action
                and final_data_action == data_action
                and mode_effect["line"] < data_effect["line"] < enable_effect["line"]
            )
            if not state_errors and ordered:
                continue

            if not ordered:
                state_errors.append(
                    "required POD < PIO < POE using final POD/PIO state before the first "
                    "POE set"
                )
            relevant = [
                effect
                for effect in (mode_effect, data_effect, enable_effect)
                if effect is not None
            ]
            line = min((effect["line"] for effect in relevant), default=None)
            evidence = f"PinContract {pin_name} {port}{bit}: {'; '.join(state_errors)}"
            issues.append(
                make_issue(
                    "HK-GPIO-002",
                    "BLOCKER",
                    file_model["path"],
                    line,
                    evidence,
                    "The output can use the wrong drive mode, glitch while enabling, "
                    "or corrupt unowned port bits.",
                    f"For {port}{bit}, use ownership-preserving bit/RMW operations to "
                    f"{mode_action.removesuffix('_bits')} {mode_register}, "
                    f"{data_action.removesuffix('_bits')} {data_register} for the safe "
                    "initial state, "
                    f"then set {enable_register}.",
                )
            )
    return issues


def _sck_ps_token_status(
    token: str, file_model: dict[str, Any]
) -> str | None:
    value = token.strip()
    if not value or value.startswith("#"):
        return None
    if value.upper() == "SCK_PS":
        return "sck"
    equ_symbols = file_model.get("_equ_symbols", {})
    resolved = resolve_byte(value, equ_symbols)
    if resolved == 0x10:
        return "sck"
    if value.upper() in equ_symbols and resolved is None:
        return "unresolved-equ"
    return None


def _token_is_sck_ps(token: str, file_model: dict[str, Any]) -> bool:
    return _sck_ps_token_status(token, file_model) == "sck"


def _classify_sck_ps_reference(
    file_model: dict[str, Any], instruction: dict[str, Any]
) -> str | None:
    op = instruction.get("op")
    args = split_args(instruction.get("args", ""))
    if op in SCK_NO_REGISTER_OPS:
        return None
    references = {
        index: status
        for index, argument in enumerate(args)
        if (status := _sck_ps_token_status(argument, file_model)) is not None
    }
    referenced = list(references)
    if not referenced:
        return None

    def write_classification() -> str:
        return (
            "potential-write"
            if "unresolved-equ" in references.values()
            else "write"
        )

    if op == "MOV" and len(args) == 2:
        if referenced == [0] and args[0].upper() != "A":
            return write_classification()
        if referenced == [1] and args[0].upper() == "A":
            return "read"
        return "potential-write"
    if op in GPIO_SECOND_OPERAND_WRITE_OPS and len(args) == 2:
        return write_classification() if referenced == [1] else "potential-write"
    if op in GPIO_FIRST_OPERAND_WRITE_OPS and len(args) == 2:
        return write_classification() if referenced == [0] else "potential-write"
    if op in SCK_UNARY_WRITE_OPS and len(args) == 1:
        return write_classification() if referenced == [0] else "potential-write"
    if op in SCK_READ_FIRST_OPERAND_OPS:
        return "read" if referenced == [0] else "potential-write"
    if op in SCK_READ_SECOND_OPERAND_OPS and len(args) == 2:
        return (
            "read"
            if referenced == [1] and args[0].upper() == "A"
            else "potential-write"
        )
    return "potential-write"


def _clock_control_flow_dominators(
    file_model: dict[str, Any],
) -> tuple[set[int], dict[int, set[int]]]:
    instructions = file_model.get("_instructions", [])
    address_to_index = {
        address: index
        for address, index in collect_unique_address_indices(instructions).items()
        if instruction_uniquely_owns_word(file_model, address, index)
    }
    nodes = set(address_to_index.values())
    entry_index = address_to_index.get(0)
    if entry_index is None:
        raise ValueError("SCK_PS control-flow proof lacks a unique address-0 entry")

    successors = {index: set() for index in nodes}
    unresolved_edges: list[str] = []

    def sequential_index(index: int, distance: int = 1) -> int | None:
        address = instructions[index].get("address")
        if not isinstance(address, int) or isinstance(address, bool):
            return None
        return address_to_index.get(address + distance)

    def direct_target_index(index: int) -> int | None:
        target_address = resolve_direct_target_address(
            file_model, instructions[index]
        )
        return (
            None
            if target_address is None
            else address_to_index.get(target_address)
        )

    def add_required_edge(index: int, successor: int | None, kind: str) -> None:
        if successor is None:
            instruction = instructions[index]
            unresolved_edges.append(
                f"{instruction.get('op')} at line {instruction.get('line')} "
                f"has unresolved {kind} control-flow"
            )
        else:
            successors[index].add(successor)

    skip_ops = GPIO_CONTROL_BOUNDARY_OPS - NONLINEAR_CONTROL_OPS
    for index in sorted(nodes):
        instruction = instructions[index]
        op = instruction.get("op")
        if op in {"RET", "RETI"}:
            continue
        if op == "JMP":
            add_required_edge(index, direct_target_index(index), "jump target")
            continue
        if op == "CALL":
            add_required_edge(index, direct_target_index(index), "call target")
            add_required_edge(index, sequential_index(index), "call fallthrough")
            continue
        if op in skip_ops:
            add_required_edge(index, sequential_index(index), "skip fallthrough")
            add_required_edge(index, sequential_index(index, 2), "skip target")
            continue
        add_required_edge(index, sequential_index(index), "fallthrough")

    if unresolved_edges:
        raise ValueError("; ".join(unresolved_edges))

    def reachable_from(starts: set[int]) -> set[int]:
        reached: set[int] = set()
        pending = list(starts)
        while pending:
            index = pending.pop()
            if index in reached:
                continue
            reached.add(index)
            pending.extend(successors[index] - reached)
        return reached

    true_reachable = reachable_from({entry_index})

    # Only address 0 proves real program reachability. Disconnected instructions are
    # also attached to the virtual root for dominance, so dead/vector-like paths can
    # invalidate a proof but can never make an unreachable SCK store look reachable.
    virtual_root = -1
    virtual_entries = {entry_index} | (nodes - true_reachable)
    predecessors = {index: set() for index in nodes}
    for index, next_indices in successors.items():
        for successor in next_indices:
            predecessors[successor].add(index)
    for index in virtual_entries:
        predecessors[index].add(virtual_root)

    all_nodes = nodes | {virtual_root}
    dominators: dict[int, set[int]] = {virtual_root: {virtual_root}}
    dominators.update({index: set(all_nodes) for index in nodes})
    changed = True
    while changed:
        changed = False
        for index in sorted(nodes):
            incoming = predecessors[index]
            common = set(all_nodes)
            for predecessor in incoming:
                common &= dominators[predecessor]
            updated = {index} | common
            if updated != dominators[index]:
                dominators[index] = updated
                changed = True
    return true_reachable, dominators


def _source_sck_ps_value(
    files: list[dict[str, Any]],
    delay_entries: list[tuple[dict[str, Any], int, str]],
) -> int | None:
    writes: list[tuple[dict[str, Any], int, dict[str, Any], str]] = []
    for file_model in files:
        official_alias = file_model.get("_equ_symbols", {}).get("SCK_PS")
        if official_alias is not None and official_alias.get("value") != 0x10:
            raise ValueError(
                f"{file_model.get('path', '<source>')} redefines SCK_PS to a conflicting address"
            )
        for index, instruction in enumerate(file_model.get("_instructions", [])):
            classification = _classify_sck_ps_reference(file_model, instruction)
            if classification in {"write", "potential-write"}:
                writes.append((file_model, index, instruction, classification))

    if not writes:
        return None
    if len(writes) != 1:
        raise ValueError(
            f"source contains {len(writes)} possible SCK_PS writes; exactly one static write is required"
        )

    file_model, store_index, store, classification = writes[0]
    store_args = split_args(store.get("args", ""))
    if (
        classification != "write"
        or store.get("op") != "MOV"
        or len(store_args) != 2
        or not _token_is_sck_ps(store_args[0], file_model)
        or store_args[1].upper() != "A"
    ):
        raise ValueError(
            f"SCK_PS write at line {store.get('line')} is not MOV SCK_PS,A"
        )
    store_address = store.get("address")
    if (
        not isinstance(store_address, int)
        or isinstance(store_address, bool)
        or not instruction_uniquely_owns_word(file_model, store_address, store_index)
    ):
        raise ValueError("SCK_PS store does not uniquely own its machine word")

    address_to_index = collect_unique_address_indices(file_model.get("_instructions", []))
    load_address = store_address - 1
    load_index = address_to_index.get(load_address)
    if load_index is None or not instruction_uniquely_owns_word(
        file_model, load_address, load_index
    ):
        raise ValueError("SCK_PS store lacks an adjacent unique immediate load")
    load = file_model["_instructions"][load_index]
    load_args = split_args(load.get("args", ""))
    if (
        load.get("op") != "MOV"
        or len(load_args) != 2
        or load_args[0].upper() != "A"
        or not load_args[1].startswith("#")
    ):
        raise ValueError("SCK_PS store is not preceded by MOV A,#K")
    value = resolve_byte(load_args[1], file_model.get("_equ_symbols", {}))
    if value is None:
        raise ValueError("SCK_PS immediate value cannot be resolved")

    instruction_files = [
        candidate for candidate in files if candidate.get("_instructions")
    ]
    if len(instruction_files) != 1 or instruction_files[0] is not file_model:
        raise ValueError(
            "SCK_PS control-flow proof cannot span multiple source files"
        )
    if any(entry_file is not file_model for entry_file, _, _ in delay_entries):
        raise ValueError(
            "SCK_PS store and every audited delay entry must be in one source file"
        )
    true_reachable, dominators = _clock_control_flow_dominators(file_model)
    if store_index not in true_reachable:
        raise ValueError("SCK_PS store is not reachable from address 0")
    if load_index not in dominators.get(store_index, set()):
        raise ValueError("MOV A,#K does not dominate the SCK_PS store")
    for _, entry_index, label in delay_entries:
        if store_index not in dominators.get(entry_index, set()):
            raise ValueError(
                f"SCK_PS store does not dominate delay entry {label}"
            )
    return value


def _prove_effective_clock(
    files: list[dict[str, Any]],
    request: dict[str, Any],
    profile: dict[str, Any] | None,
    delay_entries: list[tuple[dict[str, Any], int, str]],
) -> tuple[int, int, int]:
    if not isinstance(request, dict):
        raise ValueError("precise timing requires a request object")
    if not isinstance(profile, dict):
        raise ValueError("precise timing requires a profile object")
    clock = request.get("clock")
    if clock is None:
        osc_hz = request.get("clock_hz")
        requested_sck_ps: str | int = "reset"
    elif isinstance(clock, dict):
        osc_hz = clock.get("osc_hz")
        requested_sck_ps = clock.get("sck_ps", "reset")
    else:
        raise ValueError("request clock must be an object")
    model = profile.get("clock_model")
    if not isinstance(model, dict):
        raise ValueError("profile clock_model is missing or invalid")
    if model.get("sck_ps_register") != "SCK_PS":
        raise ValueError("profile clock_model.sck_ps_register must be SCK_PS")

    source_sck_ps = _source_sck_ps_value(files, delay_entries)
    effective_sck_ps: str | int = (
        requested_sck_ps if source_sck_ps is None else source_sck_ps
    )
    sck_hz = derive_sck_hz(osc_hz, effective_sck_ps, model)
    raw_sck_ps = (
        model.get("sck_ps_reset")
        if effective_sck_ps == "reset"
        else effective_sck_ps
    )
    if (
        not isinstance(raw_sck_ps, int)
        or isinstance(raw_sck_ps, bool)
        or not 0 <= raw_sck_ps <= 0xFF
    ):
        raise ValueError("effective SCK_PS is not an 8-bit integer")
    return osc_hz, raw_sck_ps, sck_hz


def _resolve_global_delay_label(
    files: list[dict[str, Any]], label: str
) -> tuple[dict[str, Any], int]:
    definitions: list[tuple[dict[str, Any], dict[str, Any]]] = []
    key = label.upper()
    for file_model in files:
        if key in file_model.get("_duplicate_label_names", set()):
            raise ValueError(
                f"delay label {label} is defined more than once in "
                f"{file_model.get('path', '<source>')}"
            )
        candidate = file_model.get("labels", {}).get(key)
        if isinstance(candidate, dict):
            definitions.append((file_model, candidate))
    if len(definitions) != 1:
        raise ValueError(
            f"delay label {label} must resolve once across all files; found {len(definitions)}"
        )
    file_model, definition = definitions[0]
    address = definition.get("address")
    address_to_index = collect_unique_address_indices(file_model.get("_instructions", []))
    index = address_to_index.get(address)
    if (
        index is None
        or not isinstance(address, int)
        or isinstance(address, bool)
        or not instruction_uniquely_owns_word(file_model, address, index)
    ):
        raise ValueError(
            f"delay label {label} does not uniquely name an instruction word"
        )
    return file_model, index


def audit_timing_contract(
    files: list[dict[str, Any]],
    request: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    effects: dict[str, dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(request, dict):
        return [], []
    timing = request.get("timing")
    if not isinstance(timing, dict) or timing.get("precision") != "precise":
        return [], []
    targets = timing.get("delay_targets")
    if not isinstance(targets, list) or not targets:
        return [], []

    prepared_targets: list[dict[str, Any]] = []
    delay_entries: list[tuple[dict[str, Any], int, str]] = []
    for position, target in enumerate(targets):
        label = target.get("label") if isinstance(target, dict) else None
        target_us = target.get("target_us") if isinstance(target, dict) else None
        tolerance = (
            target.get("tolerance_percent") if isinstance(target, dict) else None
        )
        file_model = None
        entry_index = None
        validation_reason = None
        if not isinstance(label, str) or not label.strip():
            validation_reason = f"delay target {position} has no valid label"
        elif (
            not isinstance(target_us, (int, float))
            or isinstance(target_us, bool)
            or target_us <= 0
            or not isinstance(tolerance, (int, float))
            or isinstance(tolerance, bool)
            or tolerance < 0
        ):
            validation_reason = "target_us/tolerance_percent is invalid"
        else:
            try:
                file_model, entry_index = _resolve_global_delay_label(files, label)
            except ValueError as exc:
                validation_reason = str(exc)
        if file_model is not None and entry_index is not None:
            delay_entries.append((file_model, entry_index, label))
        prepared_targets.append(
            {
                "label": label,
                "target_us": target_us,
                "tolerance": tolerance,
                "file_model": file_model,
                "validation_reason": validation_reason,
            }
        )

    osc_hz = None
    sck_ps = None
    sck_hz = None
    clock_reason = None
    if delay_entries:
        try:
            osc_hz, sck_ps, sck_hz = _prove_effective_clock(
                files, request, profile, delay_entries
            )
        except ValueError as exc:
            clock_reason = str(exc)

    audits: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for prepared in prepared_targets:
        label = prepared["label"]
        target_us = prepared["target_us"]
        tolerance = prepared["tolerance"]
        file_model = prepared["file_model"]
        audit: dict[str, Any] = {
            "label": label,
            "osc_hz": osc_hz,
            "sck_ps": sck_ps,
            "sck_hz": sck_hz,
            "cycles": None,
            "actual_us": None,
            "target_us": target_us,
            "error_percent": None,
        }

        def unproven(rule_id: str, reason: str, file: str = "<request>") -> None:
            audit["status"] = "unproven"
            audit["reason"] = reason
            issues.append(
                make_issue(
                    rule_id,
                    "BLOCKER",
                    file,
                    None,
                    f"timing target {label!r} is unproven: {reason}",
                    "Precise delay timing cannot be proven before compilation.",
                    "Provide a complete clock contract and a uniquely auditable delay routine.",
                )
            )

        validation_reason = prepared["validation_reason"]
        if validation_reason is not None:
            unproven("HK-TIME-001", validation_reason)
            audits.append(audit)
            continue
        if clock_reason is not None:
            unproven("HK-CLOCK-001", clock_reason)
            audits.append(audit)
            continue
        if effects is None:
            unproven(
                "HK-TIME-001",
                "instruction cycle metadata is unavailable",
                file_model.get("path", "<source>"),
            )
            audits.append(audit)
            continue
        try:
            result = simulate_delay(file_model, label, sck_hz, effects)
        except ValueError as exc:
            unproven(
                "HK-TIME-001", str(exc), file_model.get("path", "<source>")
            )
            audits.append(audit)
            continue

        error_percent = abs(result.actual_us - target_us) / target_us * 100
        audit.update(
            {
                "cycles": result.cycles,
                "actual_us": result.actual_us,
                "error_percent": error_percent,
            }
        )
        if error_percent <= tolerance:
            audit["status"] = "pass"
        else:
            reason = (
                f"error {error_percent}% exceeds tolerance {tolerance}%"
            )
            audit["status"] = "fail"
            audit["reason"] = reason
            issues.append(
                make_issue(
                    "HK-TIME-001",
                    "BLOCKER",
                    file_model.get("path", "<source>"),
                    file_model.get("labels", {}).get(label.upper(), {}).get("line"),
                    f"{label}: {result.cycles} cycles at {sck_hz} Hz gives "
                    f"{result.actual_us} us; target {target_us} us, {reason}",
                    "The generated delay is outside its declared TimingContract.",
                    "Recalculate loop counts from the proven SCK and rerun the cycle audit.",
                )
            )
        audits.append(audit)
    return audits, issues


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
