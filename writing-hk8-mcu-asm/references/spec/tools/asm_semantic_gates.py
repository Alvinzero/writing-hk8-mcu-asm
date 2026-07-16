from __future__ import annotations

from typing import Any


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


def collect_gpio_effects(file_model: dict[str, Any]) -> list[dict[str, Any]]:
    instructions = file_model.get("_instructions", [])
    equ_symbols = file_model.get("_equ_symbols", {})
    effects: list[dict[str, Any]] = []
    for index, instruction in enumerate(instructions):
        args = split_args(instruction["args"])
        if instruction["op"] in {"BSET", "BCLR"} and len(args) == 2:
            bit = resolve_byte(args[1], equ_symbols)
            if bit is not None and 0 <= bit <= 7:
                effects.append(
                    {
                        "register": args[0].upper(),
                        "set_bits": {bit} if instruction["op"] == "BSET" else set(),
                        "clear_bits": {bit} if instruction["op"] == "BCLR" else set(),
                        "line": instruction["line"],
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
                "kind": "rmw",
            }
        )
    return effects


def audit_gpio_contract(
    file_model: dict[str, Any], request: dict[str, Any]
) -> list[dict[str, Any]]:
    pins = request.get("pins")
    if not isinstance(pins, dict):
        return []

    effects = collect_gpio_effects(file_model)
    issues: list[dict[str, Any]] = []
    for pin_name in sorted(pins):
        pin = pins[pin_name]
        if not isinstance(pin, dict) or pin.get("direction") != "output":
            continue
        port = pin.get("port")
        bits = pin.get("bits")
        drive = pin.get("drive")
        active_level = pin.get("active_level")
        initial_state = pin.get("initial_state")
        if (
            not isinstance(port, str)
            or port.upper() not in {"PA", "PB"}
            or not isinstance(bits, list)
            or drive not in {"push_pull", "open_drain"}
            or active_level not in {"high", "low"}
            or initial_state not in {"on", "off"}
        ):
            continue
        port = port.upper()
        owned_bits = {
            bit
            for bit in bits
            if isinstance(bit, int) and not isinstance(bit, bool) and 0 <= bit <= 7
        }
        if not owned_bits:
            continue

        mode_register = f"{port}_POD"
        data_register = f"{port}_PIO"
        enable_register = f"{port}_POE"
        mode_action = "clear_bits" if drive == "push_pull" else "set_bits"
        initial_high = (initial_state == "on") == (active_level == "high")
        data_action = "set_bits" if initial_high else "clear_bits"
        preserve_unowned_bits = pin.get("preserve_unowned_bits") is True

        def preserves_ownership(effect: dict[str, Any]) -> bool:
            if not preserve_unowned_bits:
                return True
            touched_bits = effect["set_bits"] | effect["clear_bits"]
            return effect["kind"] in {"bit", "rmw"} and touched_bits <= owned_bits

        def candidates(register: str, action: str, bit: int) -> list[dict[str, Any]]:
            return [
                effect
                for effect in effects
                if effect["register"] == register and bit in effect[action]
            ]

        for bit in sorted(owned_bits):
            required = (
                (mode_register, mode_action),
                (data_register, data_action),
                (enable_register, "set_bits"),
            )
            matched: list[dict[str, Any] | None] = []
            rejected: list[dict[str, Any]] = []
            for register, action in required:
                action_candidates = candidates(register, action, bit)
                first_effect = action_candidates[0] if action_candidates else None
                accepted = (
                    first_effect
                    if first_effect is not None and preserves_ownership(first_effect)
                    else None
                )
                matched.append(accepted)
                if first_effect is not None and accepted is None:
                    rejected.append(first_effect)

            mode_effect, data_effect, enable_effect = matched
            missing = [
                f"{register} {action}"
                for (register, action), effect in zip(required, matched)
                if effect is None
            ]
            ordered = (
                not missing
                and mode_effect["line"] < data_effect["line"] < enable_effect["line"]
            )
            if not missing and ordered:
                continue

            present = [effect for effect in matched if effect is not None]
            relevant = present + rejected
            line = min((effect["line"] for effect in relevant), default=None)
            if missing:
                evidence = (
                    f"PinContract {pin_name} {port}{bit} lacks proof for "
                    f"{', '.join(missing)}"
                )
                if rejected:
                    rejected_details = "; ".join(
                        f"line {effect['line']} {effect['register']} {effect['kind']} touches "
                        f"unowned bits "
                        f"{sorted((effect['set_bits'] | effect['clear_bits']) - owned_bits)}; "
                        f"owned bits are {sorted(owned_bits)}"
                        for effect in rejected
                    )
                    evidence += f"; rejected ownership-unsafe effect(s): {rejected_details}"
            else:
                evidence = (
                    f"PinContract {pin_name} {port}{bit} order is "
                    f"{mode_register}@{mode_effect['line']}, "
                    f"{data_register}@{data_effect['line']}, "
                    f"{enable_register}@{enable_effect['line']}; required POD < PIO < POE"
                )
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
