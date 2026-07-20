from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from references.spec.tools import asm_static_check


TOOLS = Path(__file__).resolve().parents[1]
CHECKER = TOOLS / "asm_static_check.py"
PROFILE_EXAMPLE = TOOLS.parent.parent / "profiles" / "HK64S825.profile.example.json"


PROBLEM_LED_SOURCE = """; CHIP: HK64S825
; 功能：PA0、PA3、PA5 同步闪烁，高电平点亮
; 时钟：16 MHz
; 延时：亮约 500 ms，灭约 500 ms
; WDT：状态未知，忙等内持续执行 CLRWDT
; 工具链：builtin-hk64s825-assembler-1
; 板级连接：PA0、PA3、PA5 为推挽输出，LED 高电平有效
; 端口策略：仅读改写 PA_PIO 与 PA_POE，保留 PA 口其他位
; SRAM 分配：80H=内层计数，81H=中层计数，82H=外层计数
; 子程序 DELAY_500MS：无输入，无输出，破坏 A、80H、81H、82H 和标志位

LED_MASK        EQU 29H
LED_KEEP_MASK   EQU D6H
DELAY_INNER     EQU 0FAH
DELAY_MIDDLE    EQU 0FAH
DELAY_OUTER     EQU 20H

ORG 000H
RESET:
    JMP INIT

INIT:
    MOV A,PA_PIO
    AND A,#D6H
    MOV PA_PIO,A

    MOV A,PA_POE
    OR A,#29H
    MOV PA_POE,A

MAIN_LOOP:
    MOV A,PA_PIO
    OR A,#29H
    MOV PA_PIO,A
    CALL DELAY_500MS

    MOV A,PA_PIO
    AND A,#D6H
    MOV PA_PIO,A
    CALL DELAY_500MS
    JMP MAIN_LOOP

DELAY_500MS:
    MOV A,#20H
    MOV 82H,A
DELAY_OUTER_LOOP:
    MOV A,#0FAH
    MOV 81H,A
DELAY_MIDDLE_LOOP:
    MOV A,#0FAH
    MOV 80H,A
DELAY_INNER_LOOP:
    CLRWDT
    DECSZ 80H
    JMP DELAY_INNER_LOOP
    DECSZ 81H
    JMP DELAY_MIDDLE_LOOP
    DECSZ 82H
    JMP DELAY_OUTER_LOOP
    RET

END""".replace("\n", "\r\n")


COMPLIANT_LED_SOURCE = """; CHIP: HK64S825
; 功能：PA0、PA3、PA5 同步闪烁，高电平点亮
; 时钟：16 MHz，复位 SCK_PS=34H，系统时钟为 2 MHz
; 延时：按 2 MHz 精确审计，亮约 500 ms，灭约 500 ms
; WDT：状态未知，忙等内持续执行 CLRWDT
; 端口策略：仅以读改写清除目标 PA_POD/PA_PIO 位，再开启目标 PA_POE 位
; SRAM 分配：80H=内层计数，81H=中层计数，82H=外层计数

ORG 000H
RESET:
    JMP INIT

INIT:
    ; 只清 PA0、PA3、PA5 的开漏选择位，配置为推挽并保留其他位
    MOV A,PA_POD
    AND A,#D6H
    MOV PA_POD,A

    ; 在开启输出前写入安全低电平，LED 初始熄灭
    MOV A,PA_PIO
    AND A,#D6H
    MOV PA_PIO,A

    ; 只开启 PA0、PA3、PA5 输出，保留 PA 口其他位
    MOV A,PA_POE
    OR A,#29H
    MOV PA_POE,A

MAIN_LOOP:
    MOV A,PA_PIO
    OR A,#29H
    MOV PA_PIO,A
    CALL DELAY_500MS

    MOV A,PA_PIO
    AND A,#D6H
    MOV PA_PIO,A
    CALL DELAY_500MS
    JMP MAIN_LOOP

DELAY_500MS:
    MOV A,#04H
    MOV 82H,A
DELAY_OUTER_LOOP:
    MOV A,#0FAH
    MOV 81H,A
DELAY_MIDDLE_LOOP:
    MOV A,#0FAH
    MOV 80H,A
DELAY_INNER_LOOP:
    CLRWDT
    DECSZR 80H
    JMP DELAY_INNER_LOOP
    DECSZR 81H
    JMP DELAY_MIDDLE_LOOP
    DECSZR 82H
    JMP DELAY_OUTER_LOOP
    RET

END
"""


BULK_GPIO_WARNING_SOURCE = """; CHIP: HK64S825
; 功能：PA0 LED 输出，保留非目标位
ORG 000H
START:
    MOV A,PA_PPU
    AND A,#0FEH
    MOV PA_PPU,A
    MOV A,PA_PPD
    AND A,#0FEH
    MOV PA_PPD,A
    MOV A,PA_INS
    AND A,#0FEH
    MOV PA_INS,A
    MOV A,PA_IOS
    AND A,#0FEH
    MOV PA_IOS,A
    MOV A,PA_POD
    AND A,#0FEH
    MOV PA_POD,A
    MOV A,PA_PIO
    AND A,#0FEH
    MOV PA_PIO,A
    MOV A,PA_POE
    OR A,#01H
    MOV PA_POE,A
MAIN_LOOP:
    CLRWDT
    JMP MAIN_LOOP
END
"""


OLED_MINIMAL_REALBOARD_SOURCE = """; CHIP: HK64S825
; 功能：OLED 全屏点亮，采用实板最小初始化
; 板级：PB7 为数据线，PB6 为时钟线，七位地址 3CH，写地址 78H
ORG 0000H
  JMP INIT

ORG 0008H
  RETI

INIT:
  MOV A,#0C0H
  MOV PB_PPU,A
  MOV A,#0C0H
  MOV PB_POE,A
  MOV A,#0C0H
  MOV PB_PIO,A
  CALL DELAY_100MS
  CALL OLED_FULL_ON
HOLD:
  CLRWDT
  JMP HOLD

I2C_SEND:
  MOV 80H,A
  MOV A,#8
  MOV 81H,A
I2C_SEND_LOOP:
  BTSZ 80H,7
  JMP I2C_SEND_ONE
  BCLR PB_PIO,7
  JMP I2C_SEND_CLK
I2C_SEND_ONE:
  BSET PB_PIO,7
I2C_SEND_CLK:
  BSET PB_PIO,6
  NOP
  BCLR PB_PIO,6
  RLR 80H
  DECSZR 81H
  JMP I2C_SEND_LOOP
  BCLR PB_POE,7
  NOP
  BSET PB_PIO,6
  NOP
  MOV A,PB_INS
  AND A,#80H
  MOV 80H,A
  BCLR PB_PIO,6
  BSET PB_POE,7
  BSET PB_PIO,7
  RET

OLED_SET_FULL_RANGE:
  MOV A,#021H
  CALL I2C_SEND
  MOV A,#000H
  CALL I2C_SEND
  MOV A,#07FH
  CALL I2C_SEND
  MOV A,#022H
  CALL I2C_SEND
  MOV A,#000H
  CALL I2C_SEND
  MOV A,#007H
  CALL I2C_SEND
  RET

OLED_FULL_ON:
  CALL OLED_SET_FULL_RANGE
  MOV A,#78H
  CALL I2C_SEND
  MOV A,#40H
  CALL I2C_SEND
  MOV A,#00H
  MOV 83H,A
  MOV A,#04H
  MOV 84H,A
OLED_FULL_ON_LOOP:
  MOV A,#0FFH
  CALL I2C_SEND
  DECSZR 83H
  JMP OLED_FULL_ON_LOOP
  DECSZR 84H
  JMP OLED_FULL_ON_LOOP
  RET

DELAY_100MS:
  MOV A,#20
  MOV 85H,A
DELAY_OUTER:
  MOV A,#250
  MOV 86H,A
DELAY_MID:
  MOV A,#20
  MOV 87H,A
DELAY_INNER:
  CLRWDT
  DECSZR 87H
  JMP DELAY_INNER
  DECSZR 86H
  JMP DELAY_MID
  DECSZR 85H
  JMP DELAY_OUTER
  RET

END
"""


OLED_ACK_READS_PIO_SOURCE = OLED_MINIMAL_REALBOARD_SOURCE.replace(
    "  MOV A,PB_INS\n  AND A,#80H",
    "  MOV A,PB_PIO\n  AND A,#80H",
)


OLED_REVERSED_BTSZ_SOURCE = OLED_MINIMAL_REALBOARD_SOURCE.replace(
    "  BTSZ 80H,7\n  JMP I2C_SEND_ONE\n  BCLR PB_PIO,7",
    "  BTSZ 80H,7\n  JMP I2C_SEND_ZERO\n  BSET PB_PIO,7",
).replace(
    "I2C_SEND_ONE:\n  BSET PB_PIO,7",
    "I2C_SEND_ZERO:\n  BCLR PB_PIO,7",
)


OLED_MISSING_POWER_DELAY_SOURCE = OLED_MINIMAL_REALBOARD_SOURCE.replace(
    "  CALL DELAY_100MS\n  CALL OLED_FULL_ON",
    "  CALL OLED_FULL_ON",
)


def gpio_request(*, drive: str = "push_pull", active_level: str = "high") -> dict:
    return {
        "schema_version": 1,
        "chip": "HK64S825",
        "behavior": "PA0 PA3 PA5 LED 输出",
        "clock": {"osc_hz": 16_000_000, "sck_ps": "reset"},
        "pins": {
            "led_outputs": {
                "port": "PA",
                "bits": [0, 3, 5],
                "direction": "output",
                "drive": drive,
                "active_level": active_level,
                "initial_state": "off",
                "preserve_unowned_bits": True,
            }
        },
        "peripherals": [{"name": "gpio"}],
        "timing": {"precision": "approximate"},
        "memory_limits": {"rom_bytes": 2048, "ram_bytes": 64},
        "board": {"id": "HK64S825-DEFAULT"},
        "acceptance": [],
        "allow_nonvolatile_changes": False,
    }


def ready_profile() -> dict:
    return json.loads(PROFILE_EXAMPLE.read_text(encoding="utf-8-sig"))


def timing_request(
    *,
    label: str = "DELAY_500MS",
    target_us: float = 500_000,
    tolerance_percent: float = 1.0,
    osc_hz: int = 16_000_000,
    sck_ps: str | int = "reset",
) -> dict:
    return {
        "chip": "HK64S825",
        "clock": {"osc_hz": osc_hz, "sck_ps": sck_ps},
        "timing": {
            "precision": "precise",
            "delay_targets": [
                {
                    "label": label,
                    "target_us": target_us,
                    "tolerance_percent": tolerance_percent,
                }
            ],
        },
    }


def delay_source(outer: int = 4, *, prefix: str = "") -> str:
    return (
        prefix
        + "ORG 0x0000\n"
        + "DELAY_500MS:\n"
        + f"  MOV A,#{outer}\n"
        + "  MOV 82H,A\n"
        + "DELAY_OUTER_LOOP:\n"
        + "  MOV A,#250\n"
        + "  MOV 81H,A\n"
        + "DELAY_MIDDLE_LOOP:\n"
        + "  MOV A,#250\n"
        + "  MOV 80H,A\n"
        + "DELAY_INNER_LOOP:\n"
        + "  CLRWDT\n"
        + "  DECSZR 80H\n"
        + "  JMP DELAY_INNER_LOOP\n"
        + "  DECSZR 81H\n"
        + "  JMP DELAY_MIDDLE_LOOP\n"
        + "  DECSZR 82H\n"
        + "  JMP DELAY_OUTER_LOOP\n"
        + "  RET\n"
        + "END\n"
    )


class AsmStaticCheckCliTests(unittest.TestCase):
    def run_checker(
        self,
        source: str | list[tuple[str, str]],
        *args: str,
        map_text: str | None = None,
        request: dict | None = None,
        profile: dict | None = None,
        subprocess_mode: bool = False,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = [("main.asm", source)] if isinstance(source, str) else source
            asm_paths: list[Path] = []
            for name, text in sources:
                asm = root / name
                with asm.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
                asm_paths.append(asm)
            command = [
                sys.executable,
                str(CHECKER),
                *(str(path) for path in asm_paths),
                *args,
                "--json",
            ]
            if map_text is not None:
                map_path = root / "main.map"
                with map_path.open("w", encoding="utf-8", newline="\n") as handle:
                    handle.write(map_text)
                command.extend(["--map", str(map_path)])
            if request is not None:
                request_path = root / "request.json"
                request_path.write_text(json.dumps(request), encoding="utf-8")
                command.extend(["--request", str(request_path)])
            if profile is not None:
                profile_path = root / "profile.json"
                profile_path.write_text(json.dumps(profile), encoding="utf-8")
                command.extend(["--profile", str(profile_path)])
            if subprocess_mode:
                completed = subprocess.run(
                    command,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                )
            else:
                stdout = io.StringIO()
                stderr = io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    try:
                        returncode = asm_static_check.main(command[2:])
                    except SystemExit as exc:
                        returncode = int(exc.code or 0)
                completed = SimpleNamespace(
                    returncode=returncode,
                    stdout=stdout.getvalue(),
                    stderr=stderr.getvalue(),
                )
            payload = json.loads(completed.stdout)
            return completed, payload

    @staticmethod
    def rule_ids(payload: dict) -> set[str]:
        return {finding["rule_id"] for finding in payload["findings"]}

    def assert_gpio_blocker(self, completed, payload: dict) -> list[dict]:
        self.assertEqual(completed.returncode, 2)
        findings = [
            finding for finding in payload["findings"] if finding["rule_id"] == "HK-GPIO-002"
        ]
        self.assertTrue(findings, payload["findings"])
        self.assertTrue(all(finding["severity"] == "BLOCKER" for finding in findings))
        return findings

    def assert_ai_error(self, completed, payload: dict) -> list[dict]:
        self.assertEqual(completed.returncode, 2)
        findings = [
            finding for finding in payload["findings"] if finding["rule_id"] == "HK-AI-003"
        ]
        self.assertTrue(findings, payload["findings"])
        self.assertTrue(all(finding["severity"] == "ERROR" for finding in findings))
        return findings

    def test_rejects_status_bit_tests_not_portable_to_company_assembler(self):
        for instruction in ("BTSZ STATUS,0", "BTSNZ STATUS,0"):
            with self.subTest(instruction=instruction):
                completed, payload = self.run_checker(
                    f"ORG 0000H\n  {instruction}\n  NOP\nEND\n",
                    "--toolchain",
                    "builtin_compiler",
                )
                self.assertEqual(completed.returncode, 2)
                findings = [
                    finding
                    for finding in payload["findings"]
                    if finding["rule_id"] == "HK-SYN-014"
                ]
                self.assertEqual(1, len(findings), payload["findings"])
                self.assertEqual("BLOCKER", findings[0]["severity"])
                self.assertIn("STATUS", findings[0]["evidence"])

    def test_reports_loaded_request_and_profile_context(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n  NOP\nEND\n",
            "--toolchain",
            "company_ide",
            request={"chip": "HK64S825"},
            profile={"chip": "HK64S825"},
        )
        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertEqual(
            payload["contract_context"],
            {
                "request_loaded": True,
                "profile_loaded": True,
                "chip": "HK64S825",
            },
        )

    def test_request_chip_may_match_a_profile_alias(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n  NOP\nEND\n",
            "--toolchain",
            "company_ide",
            request={"chip": "HK825"},
            profile={"chip": "HK64S825", "aliases": ["HK825"]},
        )
        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertEqual(
            payload["contract_context"],
            {
                "request_loaded": True,
                "profile_loaded": True,
                "chip": "HK64S825",
            },
        )

    def test_request_chip_mismatch_is_reported_as_an_error_finding(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n  NOP\nEND\n",
            "--toolchain",
            "company_ide",
            request={"chip": "OTHER_CHIP"},
            profile={"chip": "HK64S825", "aliases": ["HK825"]},
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["summary"]["errors"], 1)
        self.assertIn("HK-AI-003", self.rule_ids(payload))
        self.assertIn("OTHER_CHIP", payload["findings"][0]["evidence"])
        self.assertEqual(
            payload["contract_context"],
            {
                "request_loaded": True,
                "profile_loaded": True,
                "chip": "HK64S825",
            },
        )

    def test_malformed_context_json_is_reported_as_an_error_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asm = root / "main.asm"
            request_path = root / "request.json"
            with asm.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write("ORG 0x0000\n  NOP\nEND\n")
            request_path.write_text("{not-json", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(CHECKER),
                    str(asm),
                    "--toolchain",
                    "company_ide",
                    "--request",
                    str(request_path),
                    "--json",
                ],
                text=True,
                encoding="utf-8",
                capture_output=True,
            )
            payload = json.loads(completed.stdout)

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["summary"]["errors"], 1)
        self.assertIn("request cannot be read", payload["findings"][0]["evidence"])

    def test_instruction_reference_failure_is_reported_without_a_traceback(self):
        reference_document = json.loads(
            (TOOLS.parent / "rules" / "instruction-reference.json").read_text(
                encoding="utf-8-sig"
            )
        )
        reference_cases = [
            ("missing_file", None, "instruction reference"),
            ("invalid_json", "{not-json", "instruction reference"),
        ]
        empty_document = dict(reference_document)
        empty_document["variants"] = []
        reference_cases.append(
            ("empty_variants", json.dumps(empty_document, ensure_ascii=False), "DECSZ")
        )
        for mnemonic in ("DECSZ", "INCSZ", "DECSZR", "INCSZR"):
            missing_document = dict(reference_document)
            missing_document["variants"] = [
                variant
                for variant in reference_document["variants"]
                if variant["mnemonic"].upper() != mnemonic
            ]
            reference_cases.append(
                (
                    f"missing_{mnemonic}",
                    json.dumps(missing_document, ensure_ascii=False),
                    mnemonic,
                )
            )
        decsz_variant = next(
            variant
            for variant in reference_document["variants"]
            if variant["mnemonic"].upper() == "DECSZ"
        )
        duplicate_document = dict(reference_document)
        duplicate_document["variants"] = [
            *reference_document["variants"],
            dict(decsz_variant),
        ]
        reference_cases.append(
            (
                "duplicate_DECSZ",
                json.dumps(duplicate_document, ensure_ascii=False),
                "DECSZ",
            )
        )
        restricted_document = dict(reference_document)
        restricted_document["variants"] = [
            {**variant, "delivery_policy": "restricted"}
            if variant["mnemonic"].upper() == "DECSZ"
            else variant
            for variant in reference_document["variants"]
        ]
        reference_cases.append(
            (
                "restricted_DECSZ",
                json.dumps(restricted_document, ensure_ascii=False),
                "delivery_policy",
            )
        )
        for semantic_status in (
            "open_hardware_semantics",
            "restricted_pending_hardware_confirmation",
        ):
            status_document = dict(reference_document)
            status_document["variants"] = [
                {**variant, "semantic_status": semantic_status}
                if variant["mnemonic"].upper() == "DECSZ"
                else variant
                for variant in reference_document["variants"]
            ]
            reference_cases.append(
                (
                    f"status_{semantic_status}",
                    json.dumps(status_document, ensure_ascii=False),
                    "semantic_status",
                )
            )
        sz_variant = next(
            variant
            for variant in reference_document["variants"]
            if variant["mnemonic"].upper() == "SZ"
        )
        conflicting_skip_document = dict(reference_document)
        conflicting_skip_document["variants"] = [
            *reference_document["variants"],
            {**sz_variant, "raw_notes": "A ← R"},
        ]
        reference_cases.append(
            (
                "conflicting_SZ_skip",
                json.dumps(conflicting_skip_document, ensure_ascii=False),
                "SZ",
            )
        )

        for case_name, reference_text, expected_evidence in reference_cases:
            with self.subTest(case_name=case_name):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    copied_tools = root / "spec" / "tools"
                    copied_tools.mkdir(parents=True)
                    copied_checker = copied_tools / CHECKER.name
                    shutil.copy2(CHECKER, copied_checker)
                    shutil.copy2(TOOLS / "asm_semantic_gates.py", copied_tools)
                    if reference_text is not None:
                        rules = root / "spec" / "rules"
                        rules.mkdir()
                        (rules / "instruction-reference.json").write_text(
                            reference_text,
                            encoding="utf-8",
                        )
                    asm = root / "main.asm"
                    asm.write_text("ORG 0x0000\n  NOP\nEND\n", encoding="utf-8")
                    completed = subprocess.run(
                        [
                            sys.executable,
                            str(copied_checker),
                            str(asm),
                            "--toolchain",
                            "company_ide",
                            "--json",
                        ],
                        text=True,
                        encoding="utf-8",
                        capture_output=True,
                    )

                self.assertEqual(completed.returncode, 2)
                self.assertNotIn("Traceback", completed.stderr)
                payload = json.loads(completed.stdout)
                reference_findings = [
                    finding
                    for finding in payload["findings"]
                    if finding["rule_id"] == "HK-AI-003"
                ]
                self.assertEqual(len(reference_findings), 1)
                self.assertEqual(reference_findings[0]["severity"], "ERROR")
                self.assertIn(expected_evidence, reference_findings[0]["evidence"])
                loop_audit = payload["semantic_audits"]["loop_semantics"]
                self.assertFalse(loop_audit["audited"])
                self.assertEqual(loop_audit["status"], "unavailable")

    def test_request_pins_must_be_an_object_when_present(self):
        request = gpio_request()
        request["pins"] = ["PA0", "PA3", "PA5"]
        completed, payload = self.run_checker(
            "ORG 0x0000\n  NOP\nEND\n",
            "--toolchain",
            "company_ide",
            request=request,
        )
        findings = self.assert_ai_error(completed, payload)
        self.assertIn("request pins must be an object", findings[0]["evidence"])

    def test_output_pin_contract_missing_drive_is_an_error(self):
        request = gpio_request()
        del request["pins"]["led_outputs"]["drive"]
        completed, payload = self.run_checker(
            "ORG 0x0000\n  NOP\nEND\n",
            "--toolchain",
            "company_ide",
            request=request,
        )
        findings = self.assert_ai_error(completed, payload)
        self.assertIn("pins.led_outputs.drive", findings[0]["evidence"])
        gpio_audit = payload["semantic_audits"]["gpio_contract"]
        self.assertTrue(gpio_audit["audited"])
        self.assertEqual(gpio_audit["status"], "fail")
        self.assertEqual(gpio_audit["finding_rule_ids"], ["HK-AI-003"])

    def test_structured_pin_direction_must_be_explicit_and_valid(self):
        for name, direction in (("missing", None), ("misspelled", "ouput")):
            with self.subTest(name=name):
                request = gpio_request()
                if direction is None:
                    del request["pins"]["led_outputs"]["direction"]
                else:
                    request["pins"]["led_outputs"]["direction"] = direction
                completed, payload = self.run_checker(
                    "ORG 0x0000\n  NOP\nEND\n",
                    "--toolchain",
                    "company_ide",
                    request=request,
                )
                findings = self.assert_ai_error(completed, payload)
                self.assertIn("pins.led_outputs.direction", findings[0]["evidence"])

    def test_string_and_non_output_pin_entries_remain_compatible(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n  NOP\nEND\n",
            "--toolchain",
            "company_ide",
            "--strict-warnings",
            request={
                "chip": "HK64S825",
                "pins": {
                    "fixture_output": "SIM.P0",
                    "button": {"direction": "input", "port": "PA", "bits": [1]},
                },
            },
        )
        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-AI-003", self.rule_ids(payload))
        gpio_audit = payload["semantic_audits"]["gpio_contract"]
        self.assertFalse(gpio_audit["audited"])
        self.assertEqual(gpio_audit["status"], "not_applicable")

    def test_python_cli_blocks_sources_containing_db(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\nTABLE:\n  DB 12H,34H\nEND\n",
            "--toolchain",
            "python_source_module_cli",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("HK-TOOLCHAIN-DB-001", self.rule_ids(payload))
        self.assertEqual(payload["summary"]["blockers"], 1)
        finding = next(item for item in payload["findings"] if item["rule_id"] == "HK-TOOLCHAIN-DB-001")
        self.assertIn("builtin_compiler", finding["required_fix"])
        self.assertIn("explicitly requested", finding["required_fix"])
        self.assertNotIn("Build DB sources with the verified company IDE", finding["required_fix"])

    def test_reports_each_forbidden_source_form(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "  MOV A,#0x12H\n"
            "  MOV PA_PU,A\n"
            "  JMP 0x0010\n"
            "  RET A,#01H\n"
            "END\n",
            "--toolchain",
            "company_ide",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertTrue(
            {"HK-SYN-002", "HK-MEM-001", "HK-SYN-004", "HK-SYN-008"}.issubset(self.rule_ids(payload))
        )

    def test_odd_db_and_layout_overlap_are_errors(self):
        completed, payload = self.run_checker(
            "ORG 0x0010\n"
            "  DB 12H,34H,56H\n"
            "ORG 0x0010\n"
            "  NOP\n"
            "END\n",
            "--toolchain",
            "company_ide",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("HK-TABLE-007", self.rule_ids(payload))
        self.assertIn("HK-LAYOUT-004", self.rule_ids(payload))
        layout = payload["files"][0]["layout"]
        self.assertEqual(layout["highest_written_word"], "0x0011")
        self.assertEqual(layout["image_bytes"], 36)
        self.assertEqual(layout["hole_words"], 16)

    def test_program_space_overflow_is_blocking(self):
        completed, payload = self.run_checker(
            "ORG 0x0400\n  NOP\nEND\n",
            "--toolchain",
            "company_ide",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("HK-LAYOUT-002", self.rule_ids(payload))

    def test_collects_sram_addresses(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n  MOV 80H,A\n  MOV A,0BFH\nEND\n",
            "--toolchain",
            "company_ide",
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(payload["files"][0]["sram_addresses"], ["0x80", "0xBF"])

    def test_unused_business_equ_warns_and_strict_mode_fails(self):
        source = "LED_MASK EQU 29H\nORG 0\nSTART:\n  MOV A,#29H\nEND\n"
        completed, payload = self.run_checker(
            source,
            "--toolchain",
            "builtin_compiler",
            "--strict-warnings",
        )
        self.assertEqual(completed.returncode, 1, payload["findings"])
        self.assertIn("HK-SYN-013", self.rule_ids(payload))

    def test_problem_led_source_is_rejected_by_semantic_gates(self):
        self.assertEqual(
            hashlib.sha256(PROBLEM_LED_SOURCE.encode("utf-8")).hexdigest(),
            "0144cd6f746bbbf393de4e79bc74b46194f5e7685db744ad6f4e81a832697409",
        )
        request = gpio_request()
        request["timing"] = timing_request()["timing"]
        completed, payload = self.run_checker(
            PROBLEM_LED_SOURCE,
            "--toolchain",
            "builtin_compiler",
            "--strict-warnings",
            request=request,
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 2, payload["findings"])
        self.assertTrue(
            {"HK-GPIO-002", "HK-SYN-012", "HK-SYN-013", "HK-WDT-002"}
            <= self.rule_ids(payload),
            payload["findings"],
        )
        gpio_audit = payload["semantic_audits"]["gpio_contract"]
        self.assertTrue(gpio_audit["audited"])
        self.assertEqual(gpio_audit["status"], "fail")
        self.assertEqual(gpio_audit["finding_rule_ids"], ["HK-GPIO-002"])
        loop_audit = payload["semantic_audits"]["loop_semantics"]
        self.assertTrue(loop_audit["audited"])
        self.assertEqual(loop_audit["status"], "fail")
        self.assertEqual(
            loop_audit["finding_rule_ids"], ["HK-SYN-012", "HK-WDT-002"]
        )

    def test_compliant_led_source_passes_semantic_gates_and_timing_audit(self):
        request = gpio_request()
        request["timing"] = timing_request()["timing"]
        completed, payload = self.run_checker(
            COMPLIANT_LED_SOURCE,
            "--toolchain",
            "builtin_compiler",
            "--strict-warnings",
            request=request,
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertEqual(payload["findings"], [])
        audits = payload["semantic_audits"]
        self.assertEqual(set(audits), {"gpio_contract", "loop_semantics", "oled_i2c", "timing"})
        self.assertEqual(
            audits["gpio_contract"],
            {
                "audited": True,
                "status": "pass",
                "structured_output_contract": True,
                "rule_ids": ["HK-GPIO-002", "HK-GPIO-INIT-001"],
                "finding_rule_ids": [],
            },
        )
        self.assertEqual(
            audits["loop_semantics"],
            {
                "audited": True,
                "status": "pass",
                "rule_ids": ["HK-SYN-012", "HK-WDT-001", "HK-WDT-002"],
                "finding_rule_ids": [],
            },
        )
        self.assertEqual(
            audits["oled_i2c"],
            {
                "audited": False,
                "status": "not_applicable",
                "rule_ids": ["HK-I2C-005", "HK-I2C-006", "HK-OLED-005"],
                "finding_rule_ids": [],
            },
        )
        self.assertEqual(len(audits["timing"]), 1)
        audit = audits["timing"][0]
        self.assertEqual(audit["status"], "pass")
        self.assertEqual(audit["actual_us"], 502_010.5)
        self.assertEqual(audit["error_percent"], 0.4021)
        for register in ("PA_PPU", "PA_PPD", "PA_INS", "PA_IOS"):
            self.assertNotIn(register, COMPLIANT_LED_SOURCE)

    def test_gpio_warning_is_explicit_in_checker_semantic_audit(self):
        request = gpio_request()
        request["pins"]["led_outputs"]["bits"] = [0]
        completed, payload = self.run_checker(
            BULK_GPIO_WARNING_SOURCE,
            "--toolchain",
            "builtin_compiler",
            request=request,
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        gpio_audit = payload["semantic_audits"]["gpio_contract"]
        self.assertTrue(gpio_audit["audited"])
        self.assertEqual(gpio_audit["status"], "warning")
        self.assertEqual(gpio_audit["finding_rule_ids"], ["HK-GPIO-INIT-001"])

    def test_oled_minimal_realboard_source_passes_i2c_semantic_gates(self):
        completed, payload = self.run_checker(
            OLED_MINIMAL_REALBOARD_SOURCE,
            "--toolchain",
            "builtin_compiler",
            "--strict-warnings",
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-I2C-005", self.rule_ids(payload))
        self.assertNotIn("HK-I2C-006", self.rule_ids(payload))
        self.assertNotIn("HK-OLED-005", self.rule_ids(payload))

    def test_oled_ack_must_read_input_sense_not_output_latch(self):
        completed, payload = self.run_checker(
            OLED_ACK_READS_PIO_SOURCE,
            "--toolchain",
            "builtin_compiler",
        )

        self.assertEqual(completed.returncode, 2)
        findings = [
            finding
            for finding in payload["findings"]
            if finding["rule_id"] == "HK-I2C-005"
        ]
        self.assertEqual(1, len(findings), payload["findings"])
        self.assertIn("PB_PIO", findings[0]["evidence"])
        self.assertIn("PB_INS", findings[0]["required_fix"])

    def test_oled_btsz_send_bit_branch_must_preserve_msb_order(self):
        completed, payload = self.run_checker(
            OLED_REVERSED_BTSZ_SOURCE,
            "--toolchain",
            "builtin_compiler",
        )

        self.assertEqual(completed.returncode, 2)
        findings = [
            finding
            for finding in payload["findings"]
            if finding["rule_id"] == "HK-I2C-006"
        ]
        self.assertEqual(1, len(findings), payload["findings"])
        self.assertIn("BTSZ", findings[0]["evidence"])
        self.assertIn("JMP I2C_SEND_ONE", findings[0]["required_fix"])

    def test_oled_initialization_requires_power_settle_delay_before_commands(self):
        completed, payload = self.run_checker(
            OLED_MISSING_POWER_DELAY_SOURCE,
            "--toolchain",
            "builtin_compiler",
        )

        self.assertEqual(completed.returncode, 2)
        findings = [
            finding
            for finding in payload["findings"]
            if finding["rule_id"] == "HK-OLED-005"
        ]
        self.assertEqual(1, len(findings), payload["findings"])
        self.assertIn("OLED", findings[0]["evidence"])
        self.assertIn("DELAY", findings[0]["required_fix"])

    def test_include_operand_does_not_count_as_business_equ_use(self):
        source = 'LED_MASK EQU 29H\nINCLUDE "LED_MASK"\nORG 0\nSTART:\n  NOP\nEND\n'
        completed, payload = self.run_checker(
            source,
            "--toolchain",
            "builtin_compiler",
            "--strict-warnings",
        )
        self.assertEqual(completed.returncode, 1, payload["findings"])
        self.assertIn("HK-SYN-013", self.rule_ids(payload))

    def test_referenced_business_equ_passes(self):
        source = "LED_MASK EQU 29H\nORG 0\nSTART:\n  MOV A,#LED_MASK\nEND\n"
        completed, payload = self.run_checker(
            source,
            "--toolchain",
            "builtin_compiler",
            "--strict-warnings",
        )
        self.assertEqual(completed.returncode, 0, payload["findings"])

    def test_tabh_requires_a_reload_after_tabl(self):
        completed, payload = self.run_checker(
            "; TABLE_PAIR: TABLE0,SEND0\n"
            "ORG 0x0020\nTABLE0:\n  DB 12H,34H\n"
            "ORG 0x0040\nSEND0:\n"
            "  MOV A,#20H\n  TABL\n  CALL CONSUME\n  TABH\n  RET\n"
            "CONSUME:\n  RET\nEND\n",
            "--toolchain",
            "company_ide",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("HK-TABLE-004", self.rule_ids(payload))

    def test_map_table_pair_must_be_in_same_256_word_page(self):
        completed, payload = self.run_checker(
            "; TABLE_PAIR: TABLE0,SEND0\n"
            "ORG 0x0020\nTABLE0:\n  DB 12H,34H\n"
            "ORG 0x0040\nSEND0:\n"
            "  MOV A,#20H\n  TABL\n  CALL CONSUME\n"
            "  MOV A,#20H\n  TABH\n  CALL CONSUME\n  RET\n"
            "CONSUME:\n  RET\nEND\n",
            "--toolchain",
            "company_ide",
            map_text="TABLE0 0x00F0 240\nSEND0 0x0100 256\n",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("HK-TABLE-005", self.rule_ids(payload))
        self.assertEqual(payload["table_pairs"][0]["evidence"], "map")

    def test_sender_start_same_page_does_not_hide_cross_page_table_instruction(self):
        completed, payload = self.run_checker(
            "; TABLE_PAIR: TABLE0,SEND0\n"
            "ORG 0x00F0\nTABLE0:\n  DB 12H,34H\n"
            "ORG 0x00FE\nSEND0:\n"
            "  MOV A,#F0H\n  NOP\n  TABL\n  CALL CONSUME\n"
            "  MOV A,#F0H\n  TABH\n  CALL CONSUME\n  RET\n"
            "CONSUME:\n  RET\nEND\n",
            "--toolchain",
            "company_ide",
            map_text="TABLE0 0x00F0 240\nSEND0 0x00FE 254\n",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("HK-TABLE-005", self.rule_ids(payload))
        self.assertEqual(
            payload["table_pairs"][0]["table_read_addresses"],
            ["0x0100", "0x0103"],
        )

    def test_warnings_only_fail_under_strict_warnings(self):
        source = (
            "; TABLE_PAIR: TABLE0,SEND0\n"
            "ORG 0x0020\nTABLE0:\n  DB 12H,34H\n"
            "ORG 0x0040\nSEND0:\n"
            "  MOV A,#20H\n  TABL\n  CALL CONSUME\n"
            "  MOV A,#20H\n  TABH\n  CALL CONSUME\n  RET\n"
            "CONSUME:\n  RET\nEND\n"
        )
        normal, normal_payload = self.run_checker(source, "--toolchain", "company_ide")
        strict, strict_payload = self.run_checker(
            source, "--toolchain", "company_ide", "--strict-warnings"
        )
        self.assertEqual(normal.returncode, 0)
        self.assertGreater(normal_payload["summary"]["warnings"], 0)
        self.assertEqual(strict.returncode, 1)
        self.assertEqual(strict_payload["summary"]["errors"], 0)

    def test_delay_loop_without_clrwdt_is_blocked(self):
        completed, payload = self.run_checker(
            "; CHIP: HK64S825\n"
            "; 功能：PA0 LED 长延时闪烁\n"
            "ORG 0x0000\n"
            "START:\n"
            "  BSET PA_POE,0\n"
            "MAIN_LOOP:\n"
            "  BCPL PA_PIO,0\n"
            "  CALL DELAY_2S\n"
            "  JMP MAIN_LOOP\n"
            "DELAY_2S:\n"
            "  MOV A,#0FFH\n"
            "  MOV 80H,A\n"
            "DELAY_LOOP:\n"
            "  DECR 80H\n"
            "  SZR 80H\n"
            "  JMP DELAY_LOOP\n"
            "  RET\n"
            "END\n",
            "--toolchain",
            "company_ide",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("HK-WDT-001", self.rule_ids(payload))

    def test_decsz_backward_counter_loop_is_blocked_and_clrwdt_masking_is_reported(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "LOOP:\n"
            "  CLRWDT\n"
            "  DECSZ 80H\n"
            "  JMP LOOP\n"
            "END\n",
            "--toolchain",
            "company_ide",
        )

        self.assertEqual(completed.returncode, 2)
        findings = {
            finding["rule_id"]: finding
            for finding in payload["findings"]
            if finding["rule_id"] in {"HK-SYN-012", "HK-WDT-002"}
        }
        self.assertEqual(set(findings), {"HK-SYN-012", "HK-WDT-002"})
        self.assertEqual(findings["HK-SYN-012"]["severity"], "BLOCKER")
        self.assertIn("DECSZ", findings["HK-SYN-012"]["evidence"])
        self.assertIn("writes A", findings["HK-SYN-012"]["evidence"])
        self.assertIn("backward", findings["HK-SYN-012"]["evidence"])
        self.assertIn("DECSZR", findings["HK-SYN-012"]["required_fix"])
        self.assertEqual(findings["HK-WDT-002"]["severity"], "BLOCKER")
        self.assertIn("CLRWDT", findings["HK-WDT-002"]["evidence"])
        self.assertIn("not written back", findings["HK-WDT-002"]["evidence"])

    def test_incsz_backward_counter_loop_is_blocked_without_delay_wdt_findings(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "LOOP:\n"
            "  INCSZ 80H\n"
            "  JMP LOOP\n"
            "END\n",
            "--toolchain",
            "company_ide",
        )

        self.assertEqual(completed.returncode, 2)
        rule_ids = self.rule_ids(payload)
        self.assertIn("HK-SYN-012", rule_ids)
        self.assertNotIn("HK-WDT-001", rule_ids)
        self.assertNotIn("HK-WDT-002", rule_ids)

    def test_decr_sz_backward_counter_loop_is_not_an_accumulator_only_counter(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "LOOP:\n"
            "  CLRWDT\n"
            "  DECR 80H\n"
            "  SZ 80H\n"
            "  JMP LOOP\n"
            "END\n",
            "--toolchain",
            "company_ide",
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-SYN-012", self.rule_ids(payload))
        self.assertNotIn("HK-WDT-002", self.rule_ids(payload))

    def test_counter_skip_and_jump_must_be_adjacent_machine_words(self):
        separators = {
            "db_word": "  DB 00H,00H\n",
            "org_gap": "ORG 0x0010\n",
        }
        for case_name, separator in separators.items():
            with self.subTest(case_name=case_name):
                completed, payload = self.run_checker(
                    "ORG 0x0000\n"
                    "LOOP:\n"
                    "  CLRWDT\n"
                    "  DECSZ 80H\n"
                    f"{separator}"
                    "  JMP LOOP\n"
                    "END\n",
                    "--toolchain",
                    "company_ide",
                )

                self.assertEqual(completed.returncode, 0, payload["findings"])
                self.assertNotIn("HK-SYN-012", self.rule_ids(payload))
                self.assertNotIn("HK-WDT-002", self.rule_ids(payload))

    def test_overlapping_counter_skip_and_jump_do_not_feed_semantic_audit(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "LOOP:\n"
            "  CLRWDT\n"
            "  DECSZ 80H\n"
            "ORG 0x0001\n"
            "  JMP LOOP\n"
            "END\n",
            "--toolchain",
            "company_ide",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("HK-LAYOUT-004", self.rule_ids(payload))
        self.assertNotIn("HK-SYN-012", self.rule_ids(payload))
        self.assertNotIn("HK-WDT-002", self.rule_ids(payload))

    def test_data_word_overlapping_jump_does_not_feed_semantic_audit(self):
        data_words = {
            "db": "  DB 00H,00H\n",
            "dw": "  DW 0000H\n",
            "raw_word": "  0000H\n",
        }
        for case_name, data_word in data_words.items():
            with self.subTest(case_name=case_name):
                completed, payload = self.run_checker(
                    "ORG 0x0000\n"
                    "LOOP:\n"
                    "  CLRWDT\n"
                    "  DECSZ 80H\n"
                    f"{data_word}"
                    "ORG 0x0002\n"
                    "  JMP LOOP\n"
                    "END\n",
                    "--toolchain",
                    "company_ide",
                )

                self.assertEqual(completed.returncode, 2)
                self.assertIn("HK-LAYOUT-004", self.rule_ids(payload))
                self.assertNotIn("HK-SYN-012", self.rule_ids(payload))
                self.assertNotIn("HK-WDT-002", self.rule_ids(payload))
                self.assertNotIn("_word_owners", payload["files"][0])
                self.assertNotIn("_ambiguous_word_addresses", payload["files"][0])

    def test_equ_counter_loop_target_is_resolved(self):
        for op in ("DECSZ", "INCSZ"):
            with self.subTest(op=op):
                completed, payload = self.run_checker(
                    "LOOP_ADDR EQU 00H\n"
                    "ORG 0x0000\n"
                    "START:\n"
                    f"  {op} 80H\n"
                    "  JMP LOOP_ADDR\n"
                    "END\n",
                    "--toolchain",
                    "company_ide",
                )

                self.assertEqual(completed.returncode, 2)
                self.assertIn("HK-SYN-012", self.rule_ids(payload))
                self.assertNotIn("HK-WDT-002", self.rule_ids(payload))

    def test_unreachable_clrwdt_does_not_mask_a_non_progressing_counter_loop(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "LOOP:\n"
            "  JMP CHECK\n"
            "  CLRWDT\n"
            "CHECK:\n"
            "  DECSZ 80H\n"
            "  JMP LOOP\n"
            "END\n",
            "--toolchain",
            "company_ide",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("HK-SYN-012", self.rule_ids(payload))
        self.assertNotIn("HK-WDT-002", self.rule_ids(payload))

    def test_direct_clrwdt_callee_masks_a_non_progressing_counter_loop(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "LOOP:\n"
            "  CALL KICK\n"
            "  DECSZ 80H\n"
            "  JMP LOOP\n"
            "KICK:\n"
            "  CLRWDT\n"
            "  RET\n"
            "END\n",
            "--toolchain",
            "company_ide",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("HK-SYN-012", self.rule_ids(payload))
        self.assertIn("HK-WDT-002", self.rule_ids(payload))

    def test_unreachable_clrwdt_in_complex_callee_does_not_support_wdt_finding(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "LOOP:\n"
            "  CALL KICK\n"
            "  DECSZ 80H\n"
            "  JMP LOOP\n"
            "KICK:\n"
            "  JMP RETURN_FROM_KICK\n"
            "  CLRWDT\n"
            "RETURN_FROM_KICK:\n"
            "  RET\n"
            "END\n",
            "--toolchain",
            "company_ide",
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("HK-SYN-012", self.rule_ids(payload))
        self.assertNotIn("HK-WDT-002", self.rule_ids(payload))

    def test_decszr_backward_counter_loop_has_no_constructive_finding(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "LOOP:\n"
            "  CLRWDT\n"
            "  DECSZR 80H\n"
            "  JMP LOOP\n"
            "END\n",
            "--toolchain",
            "company_ide",
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertEqual(payload["findings"], [])

    def test_incszr_backward_counter_loop_has_no_constructive_finding(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "LOOP:\n"
            "  INCSZR 80H\n"
            "  JMP LOOP\n"
            "END\n",
            "--toolchain",
            "company_ide",
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertEqual(payload["findings"], [])

    def test_simple_led_bulk_gpio_initialization_warns_under_strict_warnings(self):
        completed, payload = self.run_checker(
            "; CHIP: HK64S825\n"
            "; 功能：PA0 LED 输出\n"
            "ORG 0x0000\n"
            "START:\n"
            "  MOV A,PA_PPU\n"
            "  AND A,#0FEH\n"
            "  MOV PA_PPU,A\n"
            "  MOV A,PA_PPD\n"
            "  AND A,#0FEH\n"
            "  MOV PA_PPD,A\n"
            "  MOV A,PA_POD\n"
            "  AND A,#0FEH\n"
            "  MOV PA_POD,A\n"
            "  MOV A,PA_INS\n"
            "  AND A,#0FEH\n"
            "  MOV PA_INS,A\n"
            "  MOV A,PA_IOS\n"
            "  AND A,#0FEH\n"
            "  MOV PA_IOS,A\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_PIO,0\n"
            "END\n",
            "--toolchain",
            "company_ide",
            "--strict-warnings",
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("HK-GPIO-INIT-001", self.rule_ids(payload))

    def test_push_pull_output_requires_explicit_pod_clear(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("PA_POD clear_bits", findings[0]["evidence"])

    def test_push_pull_rmw_gpio_contract_passes(self):
        completed, payload = self.run_checker(
            "GPIO_CLEAR_MASK EQU 0D6H\n"
            "GPIO_SET_MASK EQU 29H\n"
            "ORG 0x0000\n"
            "START:\n"
            "  MOV A,PA_POD\n"
            "  AND A,#GPIO_CLEAR_MASK\n"
            "  MOV PA_POD,A\n"
            "  MOV A,PA_PIO\n"
            "  AND A,#GPIO_CLEAR_MASK\n"
            "  MOV PA_PIO,A\n"
            "  MOV A,PA_POE\n"
            "  OR A,#GPIO_SET_MASK\n"
            "  MOV PA_POE,A\n"
            "END\n",
            "--toolchain",
            "company_ide",
            "--strict-warnings",
            request=gpio_request(),
        )
        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-GPIO-002", self.rule_ids(payload))

    def test_multifile_gpio_ignores_helper_without_port_effects(self):
        completed, payload = self.run_checker(
            [
                (
                    "main.asm",
                    "ORG 0x0000\n"
                    "START:\n"
                    "  BCLR PA_POD,0\n"
                    "  BCLR PA_POD,3\n"
                    "  BCLR PA_POD,5\n"
                    "  BCLR PA_PIO,0\n"
                    "  BCLR PA_PIO,3\n"
                    "  BCLR PA_PIO,5\n"
                    "  BSET PA_POE,0\n"
                    "  BSET PA_POE,3\n"
                    "  BSET PA_POE,5\n"
                    "END\n",
                ),
                ("helper.asm", "ORG 0x0020\nHELPER:\n  NOP\n  RET\nEND\n"),
            ],
            "--toolchain",
            "company_ide",
            "--strict-warnings",
            request=gpio_request(),
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-GPIO-002", self.rule_ids(payload))
        self.assertEqual(payload["semantic_audits"]["gpio_contract"]["status"], "pass")

    def test_multifile_gpio_rejects_same_port_split_across_owners_once(self):
        completed, payload = self.run_checker(
            [
                (
                    "mode.asm",
                    "ORG 0x0000\n"
                    "MODE_INIT:\n"
                    "  BCLR PA_POD,0\n"
                    "  BCLR PA_POD,3\n"
                    "  BCLR PA_POD,5\n"
                    "END\n",
                ),
                (
                    "io.asm",
                    "ORG 0x0020\n"
                    "IO_INIT:\n"
                    "  BCLR PA_PIO,0\n"
                    "  BCLR PA_PIO,3\n"
                    "  BCLR PA_PIO,5\n"
                    "  BSET PA_POE,0\n"
                    "  BSET PA_POE,3\n"
                    "  BSET PA_POE,5\n"
                    "END\n",
                ),
            ],
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )

        findings = self.assert_gpio_blocker(completed, payload)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("multiple source owners", findings[0]["evidence"])

    def test_multifile_gpio_allows_distinct_owner_per_port(self):
        request = gpio_request()
        base_contract = request["pins"]["led_outputs"]
        request["pins"] = {
            "pa_output": {**base_contract, "bits": [0]},
            "pb_output": {**base_contract, "port": "PB", "bits": [1]},
        }
        completed, payload = self.run_checker(
            [
                (
                    "pa.asm",
                    "ORG 0x0000\n"
                    "PA_INIT:\n"
                    "  BCLR PA_POD,0\n"
                    "  BCLR PA_PIO,0\n"
                    "  BSET PA_POE,0\n"
                    "END\n",
                ),
                (
                    "pb.asm",
                    "ORG 0x0020\n"
                    "PB_INIT:\n"
                    "  BCLR PB_POD,1\n"
                    "  BCLR PB_PIO,1\n"
                    "  BSET PB_POE,1\n"
                    "END\n",
                ),
            ],
            "--toolchain",
            "company_ide",
            "--strict-warnings",
            request=request,
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-GPIO-002", self.rule_ids(payload))
        self.assertEqual(payload["semantic_audits"]["gpio_contract"]["status"], "pass")

    def test_multifile_gpio_requires_owner_for_each_output_port(self):
        completed, payload = self.run_checker(
            [("helper.asm", "ORG 0x0000\nHELPER:\n  NOP\n  RET\nEND\n")],
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )

        findings = self.assert_gpio_blocker(completed, payload)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("has no source owner", findings[0]["evidence"])

    def test_multifile_gpio_keeps_same_port_contracts_on_one_owner(self):
        request = gpio_request()
        base_contract = request["pins"]["led_outputs"]
        request["pins"] = {
            "status_outputs": {**base_contract, "bits": [0, 3]},
            "alarm_output": {**base_contract, "bits": [5]},
        }
        completed, payload = self.run_checker(
            [
                (
                    "main.asm",
                    "GPIO_CLEAR_MASK EQU 0D6H\n"
                    "GPIO_SET_MASK EQU 29H\n"
                    "ORG 0x0000\n"
                    "START:\n"
                    "  MOV A,PA_POD\n"
                    "  AND A,#GPIO_CLEAR_MASK\n"
                    "  MOV PA_POD,A\n"
                    "  MOV A,PA_PIO\n"
                    "  AND A,#GPIO_CLEAR_MASK\n"
                    "  MOV PA_PIO,A\n"
                    "  MOV A,PA_POE\n"
                    "  OR A,#GPIO_SET_MASK\n"
                    "  MOV PA_POE,A\n"
                    "END\n",
                ),
                ("helper.asm", "ORG 0x0020\nHELPER:\n  NOP\n  RET\nEND\n"),
            ],
            "--toolchain",
            "company_ide",
            "--strict-warnings",
            request=request,
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-GPIO-002", self.rule_ids(payload))
        self.assertEqual(payload["semantic_audits"]["gpio_contract"]["status"], "pass")

    def test_open_drain_output_requires_explicit_pod_set(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(drive="open_drain"),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("PA_POD set_bits", findings[0]["evidence"])

    def test_explicit_board_exception_skips_only_pod_requirement(self):
        request = gpio_request(drive="open_drain")
        request["pins"]["led_outputs"]["configure_drive_mode"] = False
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=request,
        )
        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-GPIO-002", self.rule_ids(payload))

    def test_gpio_output_enable_must_follow_mode_and_safe_latch(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("required POD < PIO < POE", findings[0]["evidence"])

    def test_gpio_rejects_mode_reversal_before_first_output_enable(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BSET PA_POD,0\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("final PA_POD action before enable", findings[0]["evidence"])

    def test_gpio_requires_final_mode_before_final_safe_latch(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("required POD < PIO < POE", findings[0]["evidence"])

    def test_gpio_rmw_rejects_changes_to_unowned_bits(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  MOV A,PA_POD\n"
            "  AND A,#00H\n"
            "  MOV PA_POD,A\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("unowned bits", findings[0]["evidence"])

    def test_gpio_rejects_any_port_effect_on_a_task_unowned_bit(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  BSET PA_POD,7\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("unowned bits [7]", findings[0]["evidence"])

    def test_gpio_combined_rmw_may_cover_two_contracts_on_the_same_port(self):
        request = gpio_request()
        base_contract = request["pins"]["led_outputs"]
        request["pins"] = {
            "status_outputs": {**base_contract, "bits": [0, 3]},
            "alarm_output": {**base_contract, "bits": [5]},
        }
        completed, payload = self.run_checker(
            "GPIO_CLEAR_MASK EQU 0D6H\n"
            "GPIO_SET_MASK EQU 29H\n"
            "ORG 0x0000\n"
            "START:\n"
            "  MOV A,PA_POD\n"
            "  AND A,#GPIO_CLEAR_MASK\n"
            "  MOV PA_POD,A\n"
            "  MOV A,PA_PIO\n"
            "  AND A,#GPIO_CLEAR_MASK\n"
            "  MOV PA_PIO,A\n"
            "  MOV A,PA_POE\n"
            "  OR A,#GPIO_SET_MASK\n"
            "  MOV PA_POE,A\n"
            "END\n",
            "--toolchain",
            "company_ide",
            "--strict-warnings",
            request=request,
        )
        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-GPIO-002", self.rule_ids(payload))

    def test_gpio_same_port_contracts_keep_their_own_drive_modes(self):
        request = gpio_request()
        base_contract = request["pins"]["led_outputs"]
        request["pins"] = {
            "drain_output": {**base_contract, "bits": [3], "drive": "open_drain"},
            "push_output": {**base_contract, "bits": [0], "drive": "push_pull"},
        }
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  BCLR PA_POD,0\n"
            "  BSET PA_POD,3\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "END\n",
            "--toolchain",
            "company_ide",
            "--strict-warnings",
            request=request,
        )
        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-GPIO-002", self.rule_ids(payload))

    def test_gpio_rejects_direct_register_enable_before_a_valid_sequence(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  MOV A,#01H\n"
            "  MOV PA_POE,A\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("unknown GPIO write", findings[0]["evidence"])

    def test_gpio_rejects_unknown_register_write_after_a_valid_sequence(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "  MOV PA_PIO,A\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("unknown GPIO write", findings[0]["evidence"])

    def test_gpio_rejects_unmodeled_writeback_instruction_families(self):
        for writeback in (
            "ADDR A,PA_PIO",
            "CLR PA_PIO",
            "BCPL PA_PIO,0",
            "XCH PA_PIO",
        ):
            with self.subTest(writeback=writeback):
                completed, payload = self.run_checker(
                    "ORG 0x0000\n"
                    "START:\n"
                    "  BCLR PA_POD,0\n"
                    "  BCLR PA_POD,3\n"
                    "  BCLR PA_POD,5\n"
                    "  BCLR PA_PIO,0\n"
                    "  BCLR PA_PIO,3\n"
                    "  BCLR PA_PIO,5\n"
                    "  BSET PA_POE,0\n"
                    "  BSET PA_POE,3\n"
                    "  BSET PA_POE,5\n"
                    f"  {writeback}\n"
                    "END\n",
                    "--toolchain",
                    "company_ide",
                    request=gpio_request(),
                )
                findings = self.assert_gpio_blocker(completed, payload)
                self.assertIn("unknown GPIO write", findings[0]["evidence"])

    def test_gpio_read_only_register_forms_are_not_unknown_writes(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "  INC PA_PIO\n"
            "  DECSZ PA_PIO\n"
            "  SZ PA_PIO\n"
            "  BTSZ PA_PIO,0\n"
            "  NOP\n"
            "END\n",
            "--toolchain",
            "company_ide",
            "--strict-warnings",
            request=gpio_request(),
        )
        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-GPIO-002", self.rule_ids(payload))

    def test_gpio_contract_does_not_join_effects_across_routines(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "UNUSED_INIT:\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  RET\n"
            "ENABLE_OUTPUTS:\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "  RET\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("control-flow boundary", findings[0]["evidence"])

    def test_gpio_contract_rejects_skip_between_mode_and_enable(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BTSZ 80H,0\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("control-flow boundary", findings[0]["evidence"])

    def test_gpio_contract_rejects_complete_but_unreachable_init_routine(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "RESET:\n"
            "  JMP MAIN\n"
            "UNUSED_INIT:\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "  RET\n"
            "MAIN:\n"
            "  NOP\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("unreachable GPIO effect", findings[0]["evidence"])

    def test_gpio_contract_accepts_init_routine_reached_by_direct_call(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  CALL INIT_GPIO\n"
            "MAIN:\n"
            "  NOP\n"
            "  JMP MAIN\n"
            "INIT_GPIO:\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "  RET\n"
            "END\n",
            "--toolchain",
            "company_ide",
            "--strict-warnings",
            request=gpio_request(),
        )
        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-GPIO-002", self.rule_ids(payload))

    def test_gpio_contract_accepts_init_routine_reached_by_equ_call(self):
        completed, payload = self.run_checker(
            "INIT_GPIO_ADDR EQU 03H\n"
            "ORG 0x0000\n"
            "START:\n"
            "  CALL INIT_GPIO_ADDR\n"
            "MAIN:\n"
            "  NOP\n"
            "  JMP MAIN\n"
            "INIT_GPIO:\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "  RET\n"
            "END\n",
            "--toolchain",
            "company_ide",
            "--strict-warnings",
            request=gpio_request(),
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertNotIn("HK-GPIO-002", self.rule_ids(payload))

    def test_gpio_contract_does_not_fall_through_an_org_gap(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\n"
            "START:\n"
            "  NOP\n"
            "ORG 0x0010\n"
            "INIT_GPIO:\n"
            "  BCLR PA_POD,0\n"
            "  BCLR PA_POD,3\n"
            "  BCLR PA_POD,5\n"
            "  BCLR PA_PIO,0\n"
            "  BCLR PA_PIO,3\n"
            "  BCLR PA_PIO,5\n"
            "  BSET PA_POE,0\n"
            "  BSET PA_POE,3\n"
            "  BSET PA_POE,5\n"
            "  RET\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("unreachable GPIO effect", findings[0]["evidence"])

    def test_gpio_does_not_skip_unsafe_first_effect_for_a_later_safe_effect(self):
        completed, payload = self.run_checker(
            "GPIO_CLEAR_MASK EQU 0D6H\n"
            "GPIO_SET_MASK EQU 29H\n"
            "ORG 0x0000\n"
            "START:\n"
            "  MOV A,PA_POD\n"
            "  AND A,#00H\n"
            "  MOV PA_POD,A\n"
            "  MOV A,PA_POD\n"
            "  AND A,#GPIO_CLEAR_MASK\n"
            "  MOV PA_POD,A\n"
            "  MOV A,PA_PIO\n"
            "  AND A,#GPIO_CLEAR_MASK\n"
            "  MOV PA_PIO,A\n"
            "  MOV A,PA_POE\n"
            "  OR A,#GPIO_SET_MASK\n"
            "  MOV PA_POE,A\n"
            "END\n",
            "--toolchain",
            "company_ide",
            request=gpio_request(),
        )
        findings = self.assert_gpio_blocker(completed, payload)
        self.assertIn("unowned bits", findings[0]["evidence"])

    def test_minimal_led_init_with_clrwdt_delay_passes(self):
        completed, payload = self.run_checker(
            "; CHIP: HK64S825\n"
            "; 功能：PA0 LED 闪烁，WDT 未确认关闭\n"
            "ORG 0x0000\n"
            "START:\n"
            "  BCLR PA_PIO,0\n"
            "  BSET PA_POE,0\n"
            "MAIN_LOOP:\n"
            "  BCPL PA_PIO,0\n"
            "  CALL DELAY_VISIBLE\n"
            "  JMP MAIN_LOOP\n"
            "DELAY_VISIBLE:\n"
            "  MOV A,#20H\n"
            "  MOV 80H,A\n"
            "DELAY_LOOP:\n"
            "  CLRWDT\n"
            "  DECR 80H\n"
            "  SZR 80H\n"
            "  JMP DELAY_LOOP\n"
            "  RET\n"
            "END\n",
            "--toolchain",
            "company_ide",
            "--strict-warnings",
        )
        self.assertEqual(completed.returncode, 0, payload["findings"])

    def test_precise_delay_uses_reset_sck_and_emits_exact_timing_audit(self):
        completed, payload = self.run_checker(
            delay_source(),
            "--toolchain",
            "company_ide",
            request=timing_request(),
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertEqual(len(payload["semantic_audits"]["timing"]), 1)
        audit = payload["semantic_audits"]["timing"][0]
        self.assertEqual(
            {
                key: audit[key]
                for key in (
                    "label",
                    "osc_hz",
                    "sck_ps",
                    "sck_hz",
                    "cycles",
                    "actual_us",
                    "target_us",
                    "error_percent",
                    "status",
                )
            },
            {
                "label": "DELAY_500MS",
                "osc_hz": 16_000_000,
                "sck_ps": 52,
                "sck_hz": 2_000_000,
                "cycles": 1_004_021,
                "actual_us": 502_010.5,
                "target_us": 500_000,
                "error_percent": 0.4021,
                "status": "pass",
            },
        )
        for internal in (
            "_instructions",
            "_equ_symbols",
            "_word_owners",
            "_ambiguous_word_addresses",
            "_duplicate_label_names",
        ):
            self.assertNotIn(internal, payload["files"][0])

    def test_static_immediate_sck_alias_write_overrides_reset(self):
        source = delay_source(
            prefix=(
                "SCK_ALIAS EQU 10H\n"
                "ORG 0x0000\n"
                "  MOV A,#32H\n"
                "  MOV SCK_ALIAS,A\n"
            )
        ).replace("ORG 0x0000\nDELAY_500MS:", "DELAY_500MS:", 1)
        completed, payload = self.run_checker(
            source,
            "--toolchain",
            "company_ide",
            request=timing_request(target_us=125_502.625, tolerance_percent=0.001),
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        audit = payload["semantic_audits"]["timing"][0]
        self.assertEqual(audit["sck_ps"], 0x32)
        self.assertEqual(audit["sck_hz"], 8_000_000)
        self.assertEqual(audit["status"], "pass")

    def test_sck_equ_alias_chain_cycle_and_unresolved_write_fail_closed(self):
        prefixes = {
            "direct": "SCK_ALIAS EQU SCK_PS\nORG 0\n  MOV SCK_ALIAS,A\n",
            "chain": (
                "SCK_BASE EQU SCK_PS\n"
                "SCK_ALIAS EQU SCK_BASE\n"
                "ORG 0\n"
                "  MOV SCK_ALIAS,A\n"
            ),
            "cycle": (
                "SCK_ALIAS EQU SCK_OTHER\n"
                "SCK_OTHER EQU SCK_ALIAS\n"
                "ORG 0\n"
                "  MOV SCK_ALIAS,A\n"
            ),
            "unresolved": (
                "SCK_ALIAS EQU UNKNOWN_REGISTER\n"
                "ORG 0\n"
                "  MOV SCK_ALIAS,A\n"
            ),
        }
        for name, prefix in prefixes.items():
            with self.subTest(name=name):
                source = delay_source(prefix=prefix).replace(
                    "ORG 0x0000\nDELAY_500MS:", "DELAY_500MS:", 1
                )
                completed, payload = self.run_checker(
                    source,
                    "--toolchain",
                    "company_ide",
                    request=timing_request(),
                    profile=ready_profile(),
                )
                self.assertEqual(completed.returncode, 2)
                clock_findings = [
                    finding
                    for finding in payload["findings"]
                    if finding["rule_id"] == "HK-CLOCK-001"
                ]
                self.assertEqual(len(clock_findings), 1, payload["findings"])
                self.assertEqual(
                    payload["semantic_audits"]["timing"][0]["status"],
                    "unproven",
                )

    def test_static_immediate_sck_equ_alias_chain_overrides_reset(self):
        source = delay_source(
            prefix=(
                "SCK_BASE EQU SCK_PS\n"
                "SCK_ALIAS EQU SCK_BASE\n"
                "ORG 0\n"
                "  MOV A,#32H\n"
                "  MOV SCK_ALIAS,A\n"
            )
        ).replace("ORG 0x0000\nDELAY_500MS:", "DELAY_500MS:", 1)
        completed, payload = self.run_checker(
            source,
            "--toolchain",
            "company_ide",
            request=timing_request(target_us=125_502.625, tolerance_percent=0.001),
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        audit = payload["semantic_audits"]["timing"][0]
        self.assertEqual(audit["sck_ps"], 0x32)
        self.assertEqual(audit["sck_hz"], 8_000_000)
        self.assertEqual(audit["status"], "pass")

    def test_dead_sck_write_cannot_override_reset_clock(self):
        body = delay_source().replace(
            "ORG 0x0000\nDELAY_500MS:", "DELAY_500MS:", 1
        )
        source = (
            "ORG 0\n"
            "  JMP DELAY_500MS\n"
            "DEAD:\n"
            "  MOV A,#31H\n"
            "  MOV SCK_PS,A\n"
            + body
        )
        completed, payload = self.run_checker(
            source,
            "--toolchain",
            "company_ide",
            request=timing_request(
                target_us=62_751.3125,
                tolerance_percent=0.001,
            ),
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 2)
        clock_findings = [
            finding
            for finding in payload["findings"]
            if finding["rule_id"] == "HK-CLOCK-001"
        ]
        self.assertEqual(len(clock_findings), 1, payload["findings"])
        audit = payload["semantic_audits"]["timing"][0]
        self.assertEqual(audit["status"], "unproven")
        self.assertIsNone(audit["sck_ps"])
        self.assertIsNone(audit["sck_hz"])

    def test_sck_write_must_precede_every_audited_delay_entry(self):
        request = timing_request(target_us=1, tolerance_percent=1_000)
        request["timing"]["delay_targets"] = [
            {
                "label": label,
                "target_us": 1,
                "tolerance_percent": 1_000,
            }
            for label in ("DELAY_BEFORE", "DELAY_AFTER")
        ]
        source = (
            "ORG 0\n"
            "  CALL DELAY_BEFORE\n"
            "  MOV A,#31H\n"
            "  MOV SCK_PS,A\n"
            "  CALL DELAY_AFTER\n"
            "HANG:\n"
            "  JMP HANG\n"
            "DELAY_BEFORE:\n"
            "  CLRWDT\n"
            "  RET\n"
            "DELAY_AFTER:\n"
            "  CLRWDT\n"
            "  RET\n"
            "END\n"
        )
        completed, payload = self.run_checker(
            source,
            "--toolchain",
            "company_ide",
            request=request,
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 2)
        clock_findings = [
            finding
            for finding in payload["findings"]
            if finding["rule_id"] == "HK-CLOCK-001"
        ]
        self.assertEqual(len(clock_findings), 2, payload["findings"])
        self.assertTrue(
            all(
                audit["status"] == "unproven"
                for audit in payload["semantic_audits"]["timing"]
            )
        )

    def test_jump_cannot_bypass_sck_immediate_load(self):
        source = (
            "ORG 0\n"
            "  CALL BYPASS\n"
            "  MOV A,#31H\n"
            "STORE:\n"
            "  MOV SCK_PS,A\n"
            "  CALL DELAY_500MS\n"
            "HANG:\n"
            "  JMP HANG\n"
            "BYPASS:\n"
            "  JMP STORE\n"
            "DELAY_500MS:\n"
            "  CLRWDT\n"
            "  RET\n"
            "END\n"
        )
        completed, payload = self.run_checker(
            source,
            "--toolchain",
            "company_ide",
            request=timing_request(target_us=1, tolerance_percent=1_000),
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            len(
                [
                    finding
                    for finding in payload["findings"]
                    if finding["rule_id"] == "HK-CLOCK-001"
                ]
            ),
            1,
            payload["findings"],
        )
        self.assertEqual(
            payload["semantic_audits"]["timing"][0]["status"], "unproven"
        )

    def test_sck_store_cannot_have_back_edge_bypassing_immediate_load(self):
        source = (
            "ORG 0\n"
            "  MOV A,#2\n"
            "  MOV 80H,A\n"
            "  MOV A,#31H\n"
            "STORE:\n"
            "  MOV SCK_PS,A\n"
            "  MOV A,#32H\n"
            "  CLRWDT\n"
            "  DECSZR 80H\n"
            "  JMP STORE\n"
            "  CALL DELAY_500MS\n"
            "HANG:\n"
            "  JMP HANG\n"
            "DELAY_500MS:\n"
            "  CLRWDT\n"
            "  RET\n"
            "END\n"
        )
        completed, payload = self.run_checker(
            source,
            "--toolchain",
            "company_ide",
            request=timing_request(target_us=1, tolerance_percent=1_000),
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            len(
                [
                    finding
                    for finding in payload["findings"]
                    if finding["rule_id"] == "HK-CLOCK-001"
                ]
            ),
            1,
            payload["findings"],
        )
        audit = payload["semantic_audits"]["timing"][0]
        self.assertEqual(audit["status"], "unproven")
        self.assertIsNone(audit["sck_ps"])
        self.assertIsNone(audit["sck_hz"])

    def test_cross_file_path_cannot_bypass_sck_control_flow_proof(self):
        main = (
            "ORG 0\n"
            "  MOV A,#31H\n"
            "  MOV SCK_PS,A\n"
            "  CALL DELAY_500MS\n"
            "HANG:\n"
            "  JMP HANG\n"
            "DELAY_500MS:\n"
            "  CLRWDT\n"
            "  RET\n"
            "END\n"
        )
        vector = (
            "ORG 100H\n"
            "VECTOR:\n"
            "  JMP DELAY_500MS\n"
            "END\n"
        )
        completed, payload = self.run_checker(
            [("main.asm", main), ("vector.asm", vector)],
            "--toolchain",
            "company_ide",
            request=timing_request(target_us=1, tolerance_percent=1_000),
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            len(
                [
                    finding
                    for finding in payload["findings"]
                    if finding["rule_id"] == "HK-CLOCK-001"
                ]
            ),
            1,
            payload["findings"],
        )
        self.assertEqual(
            payload["semantic_audits"]["timing"][0]["status"], "unproven"
        )

    def test_reachable_sck_store_must_dominate_every_delay_target(self):
        request = timing_request(target_us=1, tolerance_percent=1_000)
        request["timing"]["delay_targets"] = [
            {
                "label": label,
                "target_us": 1,
                "tolerance_percent": 1_000,
            }
            for label in ("DELAY_A", "DELAY_B")
        ]
        source = (
            "ORG 0\n"
            "RESET:\n"
            "  JMP INIT\n"
            "INIT:\n"
            "  MOV A,#31H\n"
            "  MOV SCK_PS,A\n"
            "  CALL DELAY_A\n"
            "  CALL DELAY_B\n"
            "HANG:\n"
            "  JMP HANG\n"
            "DELAY_A:\n"
            "  CLRWDT\n"
            "  RET\n"
            "DELAY_B:\n"
            "  CLRWDT\n"
            "  RET\n"
            "END\n"
        )
        completed, payload = self.run_checker(
            source,
            "--toolchain",
            "company_ide",
            request=request,
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        audits = payload["semantic_audits"]["timing"]
        self.assertEqual(len(audits), 2)
        self.assertEqual({audit["label"] for audit in audits}, {"DELAY_A", "DELAY_B"})
        self.assertTrue(all(audit["sck_ps"] == 0x31 for audit in audits))
        self.assertTrue(all(audit["sck_hz"] == 16_000_000 for audit in audits))
        self.assertTrue(all(audit["status"] == "pass" for audit in audits))

    def test_unknown_non_equ_destination_does_not_block_reset_clock(self):
        source = delay_source(prefix="ORG 0\n  MOV UNKNOWN_REGISTER,A\n").replace(
            "ORG 0x0000\nDELAY_500MS:", "DELAY_500MS:", 1
        )
        completed, payload = self.run_checker(
            source,
            "--toolchain",
            "company_ide",
            request=timing_request(),
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 0, payload["findings"])
        self.assertEqual(
            payload["semantic_audits"]["timing"][0]["sck_hz"], 2_000_000
        )

    def test_dynamic_raw_and_multiple_sck_writes_do_not_fall_back_to_reset(self):
        prefixes = {
            "dynamic": "ORG 0\n  NOP\n  MOV SCK_PS,A\n",
            "raw": "ORG 0\n  BCLR 10H,0\n",
            "multiple": (
                "ORG 0\n"
                "  MOV A,#34H\n"
                "  MOV SCK_PS,A\n"
                "  MOV A,#32H\n"
                "  MOV 10H,A\n"
            ),
        }
        for name, prefix in prefixes.items():
            with self.subTest(name=name):
                source = delay_source(prefix=prefix).replace(
                    "ORG 0x0000\nDELAY_500MS:", "DELAY_500MS:", 1
                )
                completed, payload = self.run_checker(
                    source,
                    "--toolchain",
                    "company_ide",
                    request=timing_request(),
                    profile=ready_profile(),
                )
                clock_findings = [
                    finding
                    for finding in payload["findings"]
                    if finding["rule_id"] == "HK-CLOCK-001"
                ]
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(len(clock_findings), 1, payload["findings"])
                audit = payload["semantic_audits"]["timing"][0]
                self.assertEqual(audit["status"], "unproven")
                self.assertIn("reason", audit)
                self.assertNotEqual(audit.get("sck_hz"), 2_000_000)

    def test_bad_clock_contracts_are_findings_not_tracebacks(self):
        cases = {
            "bool_osc": (timing_request(osc_hz=True), ready_profile()),
            "selector_zero": (timing_request(sck_ps=0x30), ready_profile()),
            "missing_model": (timing_request(), {"chip": "HK64S825"}),
        }
        for name, (request, profile) in cases.items():
            with self.subTest(name=name):
                completed, payload = self.run_checker(
                    delay_source(),
                    "--toolchain",
                    "company_ide",
                    request=request,
                    profile=profile,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertNotIn("Traceback", completed.stderr)
                self.assertEqual(
                    len(
                        [
                            finding
                            for finding in payload["findings"]
                            if finding["rule_id"] == "HK-CLOCK-001"
                        ]
                    ),
                    1,
                )
                self.assertEqual(
                    payload["semantic_audits"]["timing"][0]["status"],
                    "unproven",
                )

    def test_precise_label_missing_or_duplicate_is_reported_once_globally(self):
        cases = {
            "missing": "ORG 0\n  NOP\nEND\n",
            "duplicate_same_file": (
                "ORG 0\n"
                "DELAY_500MS:\n"
                "  CLRWDT\n"
                "  RET\n"
                "ORG 10H\n"
                "DELAY_500MS:\n"
                "  CLRWDT\n"
                "  RET\n"
                "END\n"
            ),
            "duplicate": [
                ("one.asm", delay_source()),
                ("two.asm", delay_source()),
            ],
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                completed, payload = self.run_checker(
                    source,
                    "--toolchain",
                    "company_ide",
                    request=timing_request(),
                    profile=ready_profile(),
                )
                timing_findings = [
                    finding
                    for finding in payload["findings"]
                    if finding["rule_id"] == "HK-TIME-001"
                ]
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(len(timing_findings), 1, payload["findings"])
                self.assertEqual(
                    payload["semantic_audits"]["timing"][0]["status"],
                    "unproven",
                )

    def test_timing_rejects_real_gap_data_overlap_duplicate_and_skip_hole(self):
        cases = {
            "gap": (
                "ORG 0\n"
                "DELAY_500MS:\n"
                "  CLRWDT\n"
                "  NOP\n"
                "ORG 4\n"
                "  RET\n"
                "END\n"
            ),
            "data_overlap": (
                "ORG 0\n"
                "DELAY_500MS:\n"
                "  CLRWDT\n"
                "  NOP\n"
                "ORG 2\n"
                "  DB 00H,00H\n"
                "ORG 2\n"
                "  RET\n"
                "END\n"
            ),
            "duplicate": (
                "ORG 0\n"
                "DELAY_500MS:\n"
                "  CLRWDT\n"
                "  NOP\n"
                "ORG 2\n"
                "  NOP\n"
                "ORG 2\n"
                "  RET\n"
                "END\n"
            ),
            "skip_hole": (
                "ORG 0\n"
                "DELAY_500MS:\n"
                "  MOV A,#1\n"
                "  MOV 80H,A\n"
                "  CLRWDT\n"
                "  DECSZR 80H\n"
                "ORG 5\n"
                "  RET\n"
                "END\n"
            ),
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                completed, payload = self.run_checker(
                    source,
                    "--toolchain",
                    "company_ide",
                    request=timing_request(),
                    profile=ready_profile(),
                )
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(
                    payload["semantic_audits"]["timing"][0]["status"],
                    "unproven",
                )
                self.assertEqual(
                    len(
                        [
                            finding
                            for finding in payload["findings"]
                            if finding["rule_id"] == "HK-TIME-001"
                        ]
                    ),
                    1,
                )

    def test_unsupported_step_capped_and_unterminated_delays_are_unproven(self):
        cases = {
            "unsupported": (
                "ORG 0\nDELAY_500MS:\n  CLRWDT\n  ADD A,#1\n  RET\nEND\n"
            ),
            "step_cap": delay_source(64),
            "no_ret": "ORG 0\nDELAY_500MS:\n  CLRWDT\n  NOP\nEND\n",
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                completed, payload = self.run_checker(
                    source,
                    "--toolchain",
                    "company_ide",
                    request=timing_request(),
                    profile=ready_profile(),
                )
                timing_findings = [
                    finding
                    for finding in payload["findings"]
                    if finding["rule_id"] == "HK-TIME-001"
                ]
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(len(timing_findings), 1, payload["findings"])
                audit = payload["semantic_audits"]["timing"][0]
                self.assertEqual(audit["status"], "unproven")
                self.assertIn("reason", audit)

    def test_tolerance_uses_unrounded_error_for_boundary_decision(self):
        target_us = 502_010.5 / 1.0100004
        completed, payload = self.run_checker(
            delay_source(),
            "--toolchain",
            "company_ide",
            request=timing_request(
                target_us=target_us,
                tolerance_percent=1.0,
            ),
            profile=ready_profile(),
        )

        self.assertEqual(completed.returncode, 2)
        audit = payload["semantic_audits"]["timing"][0]
        self.assertGreater(audit["error_percent"], 1.0)
        self.assertEqual(audit["status"], "fail")
        self.assertEqual(
            len(
                [
                    finding
                    for finding in payload["findings"]
                    if finding["rule_id"] == "HK-TIME-001"
                ]
            ),
            1,
        )

    def test_approximate_or_targetless_timing_remains_compatible(self):
        requests = [
            {
                "chip": "HK64S825",
                "timing": {
                    "precision": "approximate",
                    "delay_targets": [{"label": "MISSING"}],
                },
            },
            {"chip": "HK64S825", "timing": {"precision": "precise"}},
        ]
        for request in requests:
            with self.subTest(request=request):
                completed, payload = self.run_checker(
                    "ORG 0\n  NOP\nEND\n",
                    "--toolchain",
                    "company_ide",
                    request=request,
                )
                self.assertEqual(completed.returncode, 0, payload["findings"])
                self.assertEqual(payload["semantic_audits"]["timing"], [])

    def test_timing_real_subprocess_pass_and_fail_smoke(self):
        cases = [(4, 0, "pass"), (32, 2, "fail")]
        for outer, returncode, status in cases:
            with self.subTest(outer=outer):
                completed, payload = self.run_checker(
                    delay_source(outer),
                    "--toolchain",
                    "company_ide",
                    request=timing_request(),
                    profile=ready_profile(),
                    subprocess_mode=True,
                )
                self.assertEqual(completed.returncode, returncode, payload["findings"])
                self.assertEqual(
                    payload["semantic_audits"]["timing"][0]["status"], status
                )


if __name__ == "__main__":
    unittest.main()
