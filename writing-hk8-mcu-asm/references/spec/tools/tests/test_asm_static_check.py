from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
CHECKER = TOOLS / "asm_static_check.py"


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


class AsmStaticCheckCliTests(unittest.TestCase):
    def run_checker(
        self,
        source: str,
        *args: str,
        map_text: str | None = None,
        request: dict | None = None,
        profile: dict | None = None,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asm = root / "main.asm"
            asm.write_text(source, encoding="utf-8", newline="\n")
            command = [sys.executable, str(CHECKER), str(asm), *args, "--json"]
            if map_text is not None:
                map_path = root / "main.map"
                map_path.write_text(map_text, encoding="utf-8", newline="\n")
                command.extend(["--map", str(map_path)])
            if request is not None:
                request_path = root / "request.json"
                request_path.write_text(json.dumps(request), encoding="utf-8")
                command.extend(["--request", str(request_path)])
            if profile is not None:
                profile_path = root / "profile.json"
                profile_path.write_text(json.dumps(profile), encoding="utf-8")
                command.extend(["--profile", str(profile_path)])
            completed = subprocess.run(command, text=True, encoding="utf-8", capture_output=True)
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
            asm.write_text("ORG 0x0000\n  NOP\nEND\n", encoding="utf-8", newline="\n")
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
        for reference_text in (None, "{not-json"):
            with self.subTest(reference_text=reference_text):
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
                self.assertIn("instruction reference", reference_findings[0]["evidence"])

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

    def test_python_cli_blocks_sources_containing_db(self):
        completed, payload = self.run_checker(
            "ORG 0x0000\nTABLE:\n  DB 12H,34H\nEND\n",
            "--toolchain",
            "python_source_module_cli",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("HK-TOOLCHAIN-DB-001", self.rule_ids(payload))
        self.assertEqual(payload["summary"]["blockers"], 1)

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


if __name__ == "__main__":
    unittest.main()
