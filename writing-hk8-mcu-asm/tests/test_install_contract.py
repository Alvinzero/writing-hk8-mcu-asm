from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = SKILL_ROOT / "scripts" / "install.py"


class InstallContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_install(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.assertTrue(INSTALLER.exists(), f"installer missing: {INSTALLER}")
        return subprocess.run(
            [sys.executable, str(INSTALLER), *args],
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

    def test_codex_project_copy_install_excludes_development_artifacts(self) -> None:
        result = self.run_install(
            "--target",
            "codex-project",
            "--project-dir",
            str(self.project),
            "--mode",
            "copy",
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        payload = self.payload(result)
        self.assertEqual("INSTALLED", payload["code"])
        destination = self.project / ".agents" / "skills" / "writing-hk8-mcu-asm"
        self.assertEqual(str(destination), payload["destination"])
        self.assertTrue((destination / "SKILL.md").is_file())
        self.assertTrue((destination / "scripts" / "hk8asm.py").is_file())
        self.assertTrue((destination / "references").is_dir())
        self.assertFalse((destination / "tests").exists())
        self.assertFalse((destination / "evals").exists())

    def test_existing_target_requires_force(self) -> None:
        first = self.run_install(
            "--target", "claude-project", "--project-dir", str(self.project), "--mode", "copy"
        )
        self.assertEqual(0, first.returncode, first.stderr or first.stdout)
        second = self.run_install(
            "--target", "claude-project", "--project-dir", str(self.project), "--mode", "copy"
        )
        self.assertNotEqual(0, second.returncode)
        self.assertEqual("TARGET_EXISTS", self.payload(second)["code"])


if __name__ == "__main__":
    unittest.main()
