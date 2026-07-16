from __future__ import annotations

import json
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
REQUIRED_COUNTER_EFFECTS = {
    "DECSZ": "A",
    "INCSZ": "A",
    "DECSZR": "R",
    "INCSZR": "R",
}


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


def resolve_direct_target_address(
    file_model: dict[str, Any], instruction: dict[str, Any]
) -> int | None:
    args = split_args(instruction.get("args", ""))
    if not args or not args[0]:
        return None
    target = args[0].upper()
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
        for variant in variants:
            mnemonic = variant["mnemonic"].upper()
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
            effects[mnemonic] = effect
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
            or not semantic_status.strip()
            or semantic_status.strip().lower() in {"open", "restricted"}
        ):
            raise ValueError(
                f"instruction reference {mnemonic} semantic_status must be non-empty and "
                f"not open/restricted; got {semantic_status!r}"
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
            for offset, candidate in enumerate(loop_slice)
        )
        required_reachable = {loop_start, index, index + 1}
        prefix_has_control_boundary = any(
            candidate["op"] in NONLINEAR_CONTROL_OPS
            or effects.get(candidate["op"], {}).get("skip") is True
            for candidate in loop_slice[:-2]
        )
        if (
            not slice_is_contiguous
            or not required_reachable.issubset(reachable_indices)
            or prefix_has_control_boundary
        ):
            continue
        if any(candidate["op"] == "CLRWDT" for candidate in loop_slice):
            issues.append(
                make_issue(
                    "HK-WDT-002",
                    "BLOCKER",
                    file_model["path"],
                    instruction["line"],
                    f"loop ending at JMP {target} contains CLRWDT, but "
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
    symbol = equ_symbols.get(value.upper())
    if symbol is not None:
        resolved = symbol.get("value")
    else:
        try:
            if value.lower().startswith("0x"):
                resolved = int(value, 16)
            elif value.upper().endswith("H"):
                resolved = int(value[:-1], 16)
            elif value.isdecimal():
                resolved = int(value, 10)
            else:
                return None
        except ValueError:
            return None
    if not isinstance(resolved, int) or isinstance(resolved, bool) or not 0 <= resolved <= 0xFF:
        return None
    return resolved


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
