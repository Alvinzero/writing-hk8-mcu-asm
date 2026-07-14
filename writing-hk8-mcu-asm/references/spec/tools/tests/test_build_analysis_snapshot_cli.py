from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "build_analysis_snapshot.py"


class BuildAnalysisSnapshotCliTests(unittest.TestCase):
    def test_builder_does_not_depend_on_temporary_probe_file(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn(".spec_instruction_probe_results.json", source)

    def test_help_documents_required_paths(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("--repo", completed.stdout)
        self.assertIn("--compiler-root", completed.stdout)
        self.assertIn("--spec-root", completed.stdout)
        self.assertIn("--generated-at", completed.stdout)
        self.assertIn("--instruction-metadata", completed.stdout)
        self.assertIn("--register-metadata", completed.stdout)

    def test_repo_and_compiler_root_are_required(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            text=True,
            encoding="utf-8",
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("--repo", completed.stderr)
        self.assertIn("--compiler-root", completed.stderr)


if __name__ == "__main__":
    unittest.main()
