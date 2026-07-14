from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
CHECKER = TOOLS / "asm_static_check.py"


class AsmStaticCheckCliTests(unittest.TestCase):
    def run_checker(self, source: str, *args: str, map_text: str | None = None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asm = root / "main.asm"
            asm.write_text(source, encoding="utf-8", newline="\n")
            command = [sys.executable, str(CHECKER), str(asm), *args, "--json"]
            if map_text is not None:
                map_path = root / "main.map"
                map_path.write_text(map_text, encoding="utf-8", newline="\n")
                command.extend(["--map", str(map_path)])
            completed = subprocess.run(command, text=True, encoding="utf-8", capture_output=True)
            payload = json.loads(completed.stdout)
            return completed, payload

    @staticmethod
    def rule_ids(payload: dict) -> set[str]:
        return {finding["rule_id"] for finding in payload["findings"]}

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


if __name__ == "__main__":
    unittest.main()
