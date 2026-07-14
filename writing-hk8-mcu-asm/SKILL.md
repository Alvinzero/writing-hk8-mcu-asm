---
name: writing-hk8-mcu-asm
description: Use when generating, modifying, reviewing, compiling, flashing, or hardware-verifying ASM for the company's HK64S8X/HK64S8x 8-bit MCU family, especially when the user asks for chip-specific assembly, HK64S8101 evidence-backed code, or a self-checking compile/flash/verify loop.
---

# HK64S8X ASM Closed Loop

This skill writes company-compliant HK8 MCU ASM only through an evidence-bound loop. It must fail closed: no candidate ASM may be shown to the user until static checks, real build, controlled flash, readback, and functional verification all pass and `release` succeeds.

## First Response

On every invocation, the first assistant response must ask the user to confirm the chip model. Do not infer it from context.

Use a short question such as:

```text
请先确认目标芯片型号：HK64S8X / HK64S8101，还是其他型号？
```

If the user names anything outside the approved aliases in the selected profile, stop and say the skill does not support that chip yet. `HK64S8X` is the skill entry alias; the bundled evidence baseline currently names `HK64S8101` under the HK64S8x family, so require a board/profile confirmation before treating those as the same physical target.

## Required Inputs

Before creating any candidate source, collect and validate:

- chip model and revision;
- board profile, board ID, programmer serial, supply voltage, clock, OPTION/WDT policy;
- pin ownership, active polarity, pullups, current limits, and forbidden contention;
- required peripherals, timing tolerance, ROM/RAM limits, interrupt/SRAM constraints;
- target toolchain and approved versions;
- machine-observable acceptance criteria, such as logic analyzer, serial fixture, current/voltage measurement, readback/CRC, or automated test jig evidence.

If any safety, timing, addressing, memory, toolchain, or hardware-observation input is unresolved, return a diagnostic with `unresolved_inputs`. Do not generate or reveal ASM.

## Resources

Load only what the task needs, but always treat these as authoritative:

- `references/spec/AGENTS.md`
- `references/spec/rules/asm-rules.json`
- `references/spec/rules/instruction-reference.json`
- `references/spec/rules/register-reference.json`
- `references/spec/rules/register-alias-policy.json`
- `references/spec/09-AI智能体生成与审查协议.md`
- task-specific docs/checklists under `references/spec/`

Useful examples:

- `references/profiles/HK64S8X.profile.example.json`
- `references/configs/local-adapter.example.json`
- `references/requests/gpio-request.example.json`
- `references/spec/templates/`

Do not copy a template directly into production. Treat templates as auditable skeletons that still require board profile, toolchain, build, flash, and E1 hardware evidence.

## Closed Loop Commands

Use Python 3.10+ and standard library only. The stable command entry point is:

```powershell
python scripts/hk8asm.py doctor --profile profile.json --config local-config.json
python scripts/hk8asm.py new-run --profile profile.json --config local-config.json --request request.json --source candidate.asm --run-dir .hk8asm/run-id
python scripts/hk8asm.py close-loop --run-dir .hk8asm/run-id
python scripts/hk8asm.py release --run-dir .hk8asm/run-id --output verified.asm
```

`doctor` checks local adapters, approved tool versions, programmer serial, device ID, and voltage. `new-run` snapshots inputs into an isolated run directory. `close-loop` runs static checks, compiler, programmer, readback, and functional verifier. `release` is the only command allowed to expose the final ASM.

Adapters must be configured as string arrays and are invoked with:

```text
<command...> <role> <probe|run> --input input.json --output output.json
```

They may either write the JSON result to `--output` or emit one JSON object on stdout. Never use shell strings for adapter commands.

## Hard Gates

- Candidate ASM lives only in the isolated run directory before release.
- Static checking must use the bundled spec checker when the profile provides `spec_root` and `static_check`.
- Compiler warnings are failures unless listed in `allowed_warnings`.
- Default and maximum automatic flash attempts is three.
- Readback/CRC only proves transfer. Functional verification must also satisfy the request acceptance contract.
- Default policy forbids fuse, lock, security bit, OPTION, protection, and other nonvolatile changes unless a separate approved flow is provided.
- Any source or evidence change after verification invalidates release.
- If any gate fails, return diagnosis and evidence paths only; do not reveal candidate ASM.

## Final Response After Release

Only after `release` returns `RELEASED`, provide:

- the verified ASM content or file path requested by the user;
- chip/model and run ID;
- source, artifact, and evidence hashes;
- compact verification credentials: static check, compiler version, programmer serial/device ID/voltage, readback hash, and functional test names.

If release did not succeed, state the failed gate and next required input/action. Do not include unreleased source code.

## Installation

Install copies of this skill with:

```powershell
python scripts/install.py --target codex-user --mode copy
python scripts/install.py --target claude-user --mode copy
python scripts/install.py --target codex-project --project-dir <project> --mode copy
python scripts/install.py --target claude-project --project-dir <project> --mode copy
```

Codex can invoke it as `$writing-hk8-mcu-asm`; Claude Code can invoke it as `/writing-hk8-mcu-asm`. Description matching may also trigger it implicitly.
