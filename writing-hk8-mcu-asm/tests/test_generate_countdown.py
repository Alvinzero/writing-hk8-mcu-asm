from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = SKILL_ROOT / "scripts" / "generate_countdown.py"
CLI = SKILL_ROOT / "scripts" / "hk8asm.py"
PROFILE = SKILL_ROOT / "references" / "profiles" / "HK64S825.profile.json"
CONFIG = SKILL_ROOT / "references" / "configs" / "builtin-config.json"
BOARD_PROFILE = (
    SKILL_ROOT
    / "references"
    / "boards"
    / "HK64S825-DEFAULT"
    / "seven-segment.json"
)


class GenerateCountdownTests(unittest.TestCase):
    def run_generator(
        self, root: Path, *, start: str = "11:11", separator: str = "profile", board: Path = BOARD_PROFILE
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "--profile",
                str(PROFILE),
                "--board-profile",
                str(board),
                "--start",
                start,
                "--separator",
                separator,
                "--source",
                str(root / "candidate.asm"),
                "--output-request",
                str(root / "request.json"),
            ],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def quick_release(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(CLI),
                "quick-release",
                "--profile",
                str(PROFILE),
                "--config",
                str(CONFIG),
                "--request",
                str(root / "request.json"),
                "--source",
                str(root / "candidate.asm"),
                "--run-dir",
                str(root / "run"),
                "--output",
                str(root / "verified.asm"),
            ],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def test_e1_profile_generates_and_releases_11m11s_countdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated = self.run_generator(root)
            self.assertEqual(0, generated.returncode, generated.stderr or generated.stdout)
            generation = json.loads(generated.stdout)
            self.assertEqual("COUNTDOWN_GENERATED", generation["code"])
            self.assertEqual("HK64S825-DEFAULT", generation["board_profile_id"])
            self.assertEqual(2_000_023, generation["predicted_cycles"])
            self.assertEqual(0.00115, generation["predicted_error_percent"])

            request = json.loads((root / "request.json").read_text(encoding="utf-8"))
            self.assertEqual("user_confirmed_profile", request["input_provenance"]["board"])
            self.assertEqual(
                "user_confirmed_normal_display", request["board"]["evidence_status"]
            )
            self.assertEqual("11:11", request["resolved_inputs"]["start"])
            self.assertEqual([], request["unresolved_inputs"])

            released = self.quick_release(root)
            self.assertEqual(0, released.returncode, released.stderr or released.stdout)
            receipt = json.loads(released.stdout)
            self.assertEqual("RELEASED", receipt["code"])
            self.assertEqual(
                "builtin-hk64s825-assembler-2",
                receipt["compiler"]["tool_version"],
            )
            self.assertEqual([], receipt["warnings"])
            for kind in ("hex", "bin", "map"):
                self.assertIn(kind + "_path", receipt["artifacts"])
                self.assertIn(kind + "_sha256", receipt["artifacts"])
            self.assertGreater(receipt["metrics"]["words"], 0)
            self.assertIn("highest_word", receipt["metrics"])
            self.assertEqual(0, receipt["static_summary"]["warnings"])
            self.assertEqual("FIRST_SECOND_HOLD", receipt["timing_audit"][0]["label"])
            evidence = json.loads(
                (root / "run" / "evidence.json").read_text(encoding="utf-8")
            )
            audit = evidence["gates"]["static"]["semantic_audits"]["timing"][0]
            self.assertEqual(2_000_023, audit["cycles"])
            self.assertEqual("pass", audit["status"])
            self.assertEqual([], evidence["gates"]["compile"]["warnings"])

    def test_arbitrary_start_and_no_separator_remain_releasable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated = self.run_generator(root, start="02:03", separator="none")
            self.assertEqual(0, generated.returncode, generated.stderr or generated.stdout)
            request = json.loads((root / "request.json").read_text(encoding="utf-8"))
            self.assertEqual("02:03", request["resolved_inputs"]["start"])
            self.assertEqual("none", request["resolved_inputs"]["separator"])
            self.assertNotIn("separator", request["seven_segment"])
            source = (root / "candidate.asm").read_text(encoding="utf-8")
            self.assertNotIn("            OR A,#01H", source)
            released = self.quick_release(root)
            self.assertEqual(0, released.returncode, released.stderr or released.stdout)

    def test_draft_board_profile_is_rejected_before_writing_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            board = json.loads(BOARD_PROFILE.read_text(encoding="utf-8"))
            board["status"] = "draft"
            draft = root / "seven-segment.json"
            draft.write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")
            generated = self.run_generator(root, board=draft)
            self.assertEqual(2, generated.returncode)
            self.assertEqual("COUNTDOWN_GENERATION_FAILED", json.loads(generated.stdout)["code"])
            self.assertFalse((root / "candidate.asm").exists())


if __name__ == "__main__":
    unittest.main()
