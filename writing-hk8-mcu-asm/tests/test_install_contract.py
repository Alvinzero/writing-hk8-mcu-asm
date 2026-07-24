from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from scripts import install as installer


SKILL_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = SKILL_ROOT / "scripts" / "install.py"
VALIDATOR = SKILL_ROOT / "scripts" / "validate_skill.py"


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
        self.assertEqual(destination.resolve(), Path(payload["destination"]).resolve())
        self.assertTrue((destination / "SKILL.md").is_file())
        self.assertTrue((destination / "scripts" / "hk8asm.py").is_file())
        self.assertTrue((destination / "scripts" / "builtin_compiler.py").is_file())
        self.assertTrue((destination / "scripts" / "ssd1306_page_bitmap.py").is_file())
        self.assertTrue((destination / "references" / "profiles" / "HK64S825.profile.json").is_file())
        self.assertTrue((destination / "references" / "configs" / "builtin-config.json").is_file())
        self.assertTrue((destination / "references").is_dir())
        self.assertTrue((destination / "references" / "spec" / "rules" / "asm-rules.json").is_file())
        self.assertTrue((destination / "references" / "spec" / "tools" / "asm_static_check.py").is_file())
        self.assertTrue((destination / "references" / "spec" / "tools" / "validate_spec.py").is_file())
        self.assertFalse((destination / "tests").exists())
        self.assertFalse((destination / "evals").exists())
        self.assertFalse((destination / "docs").exists())
        self.assertFalse((destination / ".smoke").exists())
        self.assertFalse((destination / ".hk8asm").exists())
        self.assertFalse((destination / "references" / "spec" / "templates").exists())
        self.assertFalse((destination / "references" / "spec" / "analysis").exists())
        self.assertFalse((destination / "references" / "spec" / "tools" / "tests").exists())
        self.assertFalse(
            (destination / "references" / "spec" / "tools" / "build_analysis_snapshot.py").exists()
        )
        for path in destination.rglob("*"):
            if not path.is_file():
                continue
            text = installer.read_utf8_text(path)
            if text is None:
                continue
            self.assertIsNone(installer.find_nonportable_absolute_path(text), path)
        validation = subprocess.run(
            [sys.executable, str(VALIDATOR), str(destination)],
            cwd=SKILL_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, validation.returncode, validation.stderr or validation.stdout)

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

    def test_force_copy_replaces_symlink_without_deleting_its_target(self) -> None:
        destination = self.project / ".agents" / "skills" / "writing-hk8-mcu-asm"
        destination.parent.mkdir(parents=True)
        target = self.root / "symlink-target"
        target.mkdir()
        sentinel = target / "keep.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        try:
            os.symlink(target, destination, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"当前环境不能创建目录符号链接: {exc}")

        result = self.run_install(
            "--target",
            "codex-project",
            "--project-dir",
            str(self.project),
            "--mode",
            "copy",
            "--force",
        )

        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertFalse(destination.is_symlink())
        self.assertTrue((destination / "SKILL.md").is_file())
        self.assertTrue(sentinel.is_file())
        self.assertEqual("keep\n", sentinel.read_text(encoding="utf-8"))

    def test_copy_rejects_cross_platform_absolute_paths_and_rolls_back(self) -> None:
        cases = {
            "windows.csv": "tool,C:\\Users\\alvin\\compiler.exe\n",
            "unc.asm": "; tool=\\\\build-server\\share\\compiler.exe\n",
            "linux.example": "tool=/home/alvin/compiler\n",
            "macos.yml": "tool: /Users/alvin/compiler\n",
            "opt.txt": "tool=/opt/compiler\n",
            "var.csv": "tool=/var/lib/compiler\n",
            "windows.ps1": "$tool = 'C:\\Users\\alvin\\compiler.exe'\n",
            "escaped-unc.json": json.dumps(
                {"custom_path": "\\\\build-server\\share\\compiler.exe"}
            ),
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                source = self.root / f"source-{name.replace('.', '-')}"
                source.mkdir()
                (source / "SKILL.md").write_text(
                    "---\nname: writing-hk8-mcu-asm\ndescription: fixture\n---\n",
                    encoding="utf-8",
                )
                (source / name).write_text(content, encoding="utf-8")
                destination = self.root / f"destination-{name.replace('.', '-')}"

                with mock.patch.object(installer, "skill_root", return_value=source):
                    with self.assertRaises(installer.InstallError) as raised:
                        installer.install(source, destination, "copy", False)

                self.assertEqual("PORTABILITY_VIOLATION", raised.exception.code)
                self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
