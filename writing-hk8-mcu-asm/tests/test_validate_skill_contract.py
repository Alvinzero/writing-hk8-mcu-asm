from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL_ROOT / "scripts" / "validate_skill.py"


class ValidateSkillContractTests(unittest.TestCase):
    def run_validator(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.assertTrue(VALIDATOR.exists(), f"validator missing: {VALIDATOR}")
        return subprocess.run(
            [sys.executable, str(VALIDATOR), *args],
            cwd=SKILL_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def payload(self, completed: subprocess.CompletedProcess[str]) -> dict:
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"stdout must contain one JSON document: {exc}: {completed.stdout!r}")

    def test_current_skill_structure_is_valid(self) -> None:
        result = self.run_validator(str(SKILL_ROOT))
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertEqual("SKILL_VALID", self.payload(result)["code"])

    def test_missing_description_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "SKILL.md").write_text("---\nname: bad-skill\n---\n# Bad\n", encoding="utf-8")
            result = self.run_validator(str(root))
            self.assertNotEqual(0, result.returncode)
            self.assertEqual("SKILL_INVALID", self.payload(result)["code"])


if __name__ == "__main__":
    unittest.main()
