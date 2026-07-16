from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SPEC = Path(__file__).resolve().parents[2]
VALIDATOR = SPEC / "tools" / "validate_spec.py"


class ValidateSpecCliTests(unittest.TestCase):
    def run_validator_process(self, root: Path, *, force_fallback: bool = False):
        command = [sys.executable]
        if force_fallback:
            command.append("-S")
        command.extend([str(VALIDATOR), str(root), "--json"])
        return subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            capture_output=True,
        )

    def run_validator(self, root: Path):
        completed = self.run_validator_process(root)
        payload = json.loads(completed.stdout)
        return completed, payload

    def copy_spec(self, destination: Path) -> Path:
        copied = destination / "spec"
        shutil.copytree(SPEC, copied)
        return copied

    def assert_missing_automated_test_is_rejected(self, spoof: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = self.copy_spec(Path(tmp))
            tests = copied / "tools" / "tests" / "test_asm_static_check.py"
            source = tests.read_text(encoding="utf-8")
            signature = "    def test_delay_loop_without_clrwdt_is_blocked(self):"
            self.assertIn(signature, source)
            tests.write_text(
                source.replace(
                    signature,
                    "    def disabled_delay_loop_without_clrwdt_is_blocked(self):",
                    1,
                )
                + spoof,
                encoding="utf-8",
            )
            completed = self.run_validator_process(copied)

        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        payload = json.loads(completed.stdout)
        findings = [
            item for item in payload["findings"] if item["code"] == "checker-rule-test"
        ]
        self.assertTrue(findings, payload["findings"])
        self.assertIn("test_delay_loop_without_clrwdt_is_blocked", findings[0]["message"])

    def test_current_spec_package_validates(self):
        completed, payload = self.run_validator(SPEC)
        self.assertEqual(completed.returncode, 0, payload)
        self.assertEqual(payload["summary"]["errors"], 0)
        self.assertEqual(payload["checks"]["rule_count"], 78)
        self.assertEqual(payload["checks"]["instruction_variant_count"], 65)
        self.assertEqual(payload["checks"]["instruction_metadata_count"], 65)
        self.assertEqual(payload["checks"]["register_reference_count"], 96)
        self.assertEqual(payload["checks"]["register_sheet_row_count"], 407)
        self.assertTrue(payload["checks"]["instruction_metadata_exact_snapshot"])
        self.assertTrue(payload["checks"]["register_metadata_exact_snapshot"])

    def test_automated_checker_rules_are_bound_to_exact_test_methods(self):
        completed, payload = self.run_validator(SPEC)
        self.assertEqual(completed.returncode, 0, payload)
        self.assertEqual(
            payload["checks"]["automated_rule_tests"],
            {
                "HK-WDT-001": "test_delay_loop_without_clrwdt_is_blocked",
                "HK-GPIO-INIT-001": (
                    "test_simple_led_bulk_gpio_initialization_warns_under_strict_warnings"
                ),
                "HK-GPIO-002": "test_problem_led_source_is_rejected_by_semantic_gates",
                "HK-SYN-012": (
                    "test_decsz_backward_counter_loop_is_blocked_and_clrwdt_masking_is_reported"
                ),
                "HK-SYN-013": "test_unused_business_equ_warns_and_strict_mode_fails",
                "HK-CLOCK-001": "test_reset_0x34_derives_2mhz_from_16mhz_osc",
                "HK-TIME-001": (
                    "test_original_three_level_counts_are_about_4_seconds_at_2mhz"
                ),
                "HK-WDT-002": (
                    "test_decsz_backward_counter_loop_is_blocked_and_clrwdt_masking_is_reported"
                ),
            },
        )

    def test_missing_automated_rule_test_method_fails_without_comment_spoofing(self):
        self.assert_missing_automated_test_is_rejected(
            "\n# def test_delay_loop_without_clrwdt_is_blocked(self):\n"
        )

    def test_nested_local_test_definition_cannot_spoof_automation_binding(self):
        self.assert_missing_automated_test_is_rejected(
            "\nclass NestedSpoof(unittest.TestCase):\n"
            "    def helper(self):\n"
            "        def test_delay_loop_without_clrwdt_is_blocked(self):\n"
            "            pass\n"
            "        return test_delay_loop_without_clrwdt_is_blocked\n"
        )

    def test_non_testcase_class_cannot_spoof_automation_binding(self):
        self.assert_missing_automated_test_is_rejected(
            "\nclass FakeTests:\n"
            "    def test_delay_loop_without_clrwdt_is_blocked(self):\n"
            "        pass\n"
        )

    def test_top_level_async_test_cannot_spoof_automation_binding(self):
        self.assert_missing_automated_test_is_rejected(
            "\nasync def test_delay_loop_without_clrwdt_is_blocked(self):\n"
            "    pass\n"
        )

    def test_direct_testcase_import_is_a_valid_automation_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = self.copy_spec(Path(tmp))
            tests = copied / "tools" / "tests" / "test_asm_static_check.py"
            source = tests.read_text(encoding="utf-8")
            source = source.replace("import unittest\n", "from unittest import TestCase\n", 1)
            source = source.replace(
                "class AsmStaticCheckCliTests(unittest.TestCase):",
                "class AsmStaticCheckCliTests(TestCase):",
                1,
            )
            tests.write_text(source, encoding="utf-8")
            completed, payload = self.run_validator(copied)

        self.assertEqual(completed.returncode, 0, payload)

    def test_fallback_rejects_invalid_toolchain_applicability(self):
        invalid_values = {
            "unknown": ["bogus_compiler"],
            "empty": [],
            "duplicate": ["company_ide", "company_ide"],
            "non-string": [42],
        }
        for name, invalid_value in invalid_values.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                copied = self.copy_spec(Path(tmp))
                path = copied / "rules" / "asm-rules.json"
                data = json.loads(path.read_text(encoding="utf-8"))
                data["rules"][0]["toolchain_applicability"] = invalid_value
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                completed = self.run_validator_process(copied, force_fallback=True)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["checks"]["rule_schema_engine"], "fallback")
                self.assertIn(
                    "rule-schema",
                    {item["code"] for item in payload["findings"]},
                )

    def test_checker_cannot_emit_unregistered_rule_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = self.copy_spec(Path(tmp))
            checker = copied / "tools" / "asm_static_check.py"
            checker.write_text(
                checker.read_text(encoding="utf-8")
                + '\n# make_finding("HK-UNREGISTERED-999", "ERROR", "x", 1, "e", "r", "f")\n',
                encoding="utf-8",
            )
            completed, payload = self.run_validator(copied)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("checker-rule-id", {item["code"] for item in payload["findings"]})

    def test_checker_invalid_utf8_preserves_structured_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = self.copy_spec(Path(tmp))
            checker = copied / "tools" / "asm_static_check.py"
            checker.write_bytes(checker.read_bytes() + b"\xff")
            completed = self.run_validator_process(copied)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIn("invalid-utf8", {item["code"] for item in payload["findings"]})

    def test_missing_required_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = self.copy_spec(Path(tmp))
            (copied / "AGENTS.md").unlink()
            completed, payload = self.run_validator(copied)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("missing-file", {item["code"] for item in payload["findings"]})

    def test_duplicate_rule_id_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = self.copy_spec(Path(tmp))
            path = copied / "rules" / "asm-rules.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["rules"][1]["rule_id"] = data["rules"][0]["rule_id"]
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            completed, payload = self.run_validator(copied)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("duplicate-rule-id", {item["code"] for item in payload["findings"]})

    def test_missing_rule_id_reports_schema_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = self.copy_spec(Path(tmp))
            path = copied / "rules" / "asm-rules.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["rules"][0].pop("rule_id")
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            completed = self.run_validator_process(copied)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIn("rule-schema", {item["code"] for item in payload["findings"]})

    def test_fallback_rejects_invalid_rule_ids_without_crashing(self):
        invalid_values = {
            "integer": 123,
            "array": [{"nested": "id"}],
            "invalid-pattern": "not-a-rule-id",
        }
        for name, invalid_value in invalid_values.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                copied = self.copy_spec(Path(tmp))
                path = copied / "rules" / "asm-rules.json"
                data = json.loads(path.read_text(encoding="utf-8"))
                data["rules"][0]["rule_id"] = invalid_value
                path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                completed = self.run_validator_process(copied, force_fallback=True)
                self.assertEqual(completed.returncode, 2, completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["checks"]["rule_schema_engine"], "fallback")
                self.assertIn(
                    "rule-schema",
                    {item["code"] for item in payload["findings"]},
                )

    def test_broken_relative_markdown_link_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = self.copy_spec(Path(tmp))
            readme = copied / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8") + "\n[broken](missing-file.md)\n",
                encoding="utf-8",
            )
            completed, payload = self.run_validator(copied)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("broken-link", {item["code"] for item in payload["findings"]})

    def test_template_validation_hash_must_track_template_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = self.copy_spec(Path(tmp))
            template = copied / "templates" / "minimal-main.asm"
            template.write_text(
                template.read_text(encoding="utf-8") + "; changed after validation\n",
                encoding="utf-8",
            )
            completed, payload = self.run_validator(copied)
        self.assertEqual(completed.returncode, 2)
        self.assertIn(
            "template-validation-stale",
            {item["code"] for item in payload["findings"]},
        )

    def test_instruction_metadata_regression_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = self.copy_spec(Path(tmp))
            path = copied / "rules" / "instruction-metadata.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["instructions"][49]["asm_syntax"] = "BTSZ,R,b"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            completed, payload = self.run_validator(copied)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("instruction-metadata", {item["code"] for item in payload["findings"]})

    def test_register_metadata_internal_gpio_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            copied = self.copy_spec(Path(tmp))
            path = copied / "rules" / "register-reference.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            target = next(item for item in data["registers"] if item["name"] == "PA_PPU")
            target["name"] = "PA_PU"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            completed, payload = self.run_validator(copied)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("register-metadata-name", {item["code"] for item in payload["findings"]})


if __name__ == "__main__":
    unittest.main()
