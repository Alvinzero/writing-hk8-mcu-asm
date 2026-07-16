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
    def run_validator(self, root: Path):
        completed = subprocess.run(
            [sys.executable, str(VALIDATOR), str(root), "--json"],
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        payload = json.loads(completed.stdout)
        return completed, payload

    def copy_spec(self, destination: Path) -> Path:
        copied = destination / "spec"
        shutil.copytree(SPEC, copied)
        return copied

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
            completed = subprocess.run(
                [sys.executable, str(VALIDATOR), str(copied), "--json"],
                text=True,
                encoding="utf-8",
                capture_output=True,
            )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIn("rule-schema", {item["code"] for item in payload["findings"]})

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
