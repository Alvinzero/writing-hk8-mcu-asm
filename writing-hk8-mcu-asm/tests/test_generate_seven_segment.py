from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = SKILL_ROOT / "scripts" / "generate_seven_segment.py"
CLI = SKILL_ROOT / "scripts" / "hk8asm.py"
PROFILE = SKILL_ROOT / "references" / "profiles" / "HK64S825.profile.json"
CONFIG = SKILL_ROOT / "references" / "configs" / "builtin-config.json"


def request() -> dict:
    return {
        "schema_version": 1,
        "chip": "HK64S825",
        "behavior": "四位数码管每一秒在全部段亮和全灭之间切换",
        "clock": {"osc_hz": 16_000_000, "sck_ps": 0x34},
        "pins": {
            "segments": {
                "port": "PB",
                "bits": list(range(8)),
                "direction": "output",
                "drive": "push_pull",
                "active_level": "dynamic",
                "initial_level": "high",
                "preserve_unowned_bits": False,
                "port_ownership": "exclusive",
            },
            "digits_cc": {
                "port": "PA",
                "bits": [5, 6],
                "direction": "output",
                "drive": "push_pull",
                "active_level": "low",
                "initial_state": "off",
                "preserve_unowned_bits": True,
                "port_ownership": "shared",
            },
            "digits_ca": {
                "port": "PA",
                "bits": [2, 3],
                "direction": "output",
                "drive": "push_pull",
                "active_level": "high",
                "initial_state": "off",
                "preserve_unowned_bits": True,
                "port_ownership": "shared",
            },
        },
        "seven_segment": {
            "driver": "gpio_dynamic_scan",
            "segment_pin": "segments",
            "segment_mapping": {
                "A": 7,
                "B": 6,
                "C": 5,
                "D": 4,
                "E": 3,
                "F": 2,
                "G": 1,
                "DP": 0,
            },
            "external_inversion": {
                "status": "confirmed",
                "segments": False,
                "digit_select": False,
            },
            "current_limit": {
                "status": "confirmed",
                "description": "每段串联限流电阻且峰值电流已确认",
            },
            "drive_capability_confirmed": True,
            "digits": [
                {
                    "visual_index": 0,
                    "pin_contract": "digits_cc",
                    "bit": 5,
                    "topology": "common_cathode",
                    "com_active_level": "low",
                    "segment_active_level": "high",
                },
                {
                    "visual_index": 1,
                    "pin_contract": "digits_cc",
                    "bit": 6,
                    "topology": "common_cathode",
                    "com_active_level": "low",
                    "segment_active_level": "high",
                },
                {
                    "visual_index": 2,
                    "pin_contract": "digits_ca",
                    "bit": 2,
                    "topology": "common_anode",
                    "com_active_level": "high",
                    "segment_active_level": "low",
                },
                {
                    "visual_index": 3,
                    "pin_contract": "digits_ca",
                    "bit": 3,
                    "topology": "common_anode",
                    "com_active_level": "high",
                    "segment_active_level": "low",
                },
            ],
            "operation": {
                "type": "toggle_all_segments",
                "include_dp": True,
                "state_duration_us": 1_000_000,
                "tolerance_percent": 1.0,
            },
        },
        "peripherals": [{"name": "seven_segment"}],
        "memory_limits": {"rom_bytes": 2048, "ram_bytes": 64},
        "board": {"id": "MIXED-4DIGIT-TEST"},
        "input_provenance": {
            "board": "user_provided",
            "pins": "user_provided",
            "clock": "user_provided",
        },
        "acceptance": [],
        "allow_nonvolatile_changes": False,
    }


class GenerateSevenSegmentTests(unittest.TestCase):
    def test_generated_source_passes_quick_release_with_full_period_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_request = root / "input.json"
            resolved_request = root / "request.json"
            source = root / "candidate.asm"
            input_request.write_text(
                json.dumps(request(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            generated = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR),
                    "--profile",
                    str(PROFILE),
                    "--request",
                    str(input_request),
                    "--source",
                    str(source),
                    "--output-request",
                    str(resolved_request),
                ],
                cwd=SKILL_ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, generated.returncode, generated.stderr or generated.stdout)
            generation = json.loads(generated.stdout)
            self.assertLessEqual(generation["error_percent"], 1.0)
            source_text = source.read_text(encoding="utf-8")
            self.assertIn("MOV PB_PIO,A", source_text)
            self.assertNotIn("WRITE_SEGMENT_BITS", source_text)

            run_dir = root / "run"
            output = root / "verified.asm"
            released = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "quick-release",
                    "--profile",
                    str(PROFILE),
                    "--config",
                    str(CONFIG),
                    "--request",
                    str(resolved_request),
                    "--source",
                    str(source),
                    "--run-dir",
                    str(run_dir),
                    "--output",
                    str(output),
                ],
                cwd=SKILL_ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, released.returncode, released.stderr or released.stdout)
            evidence = json.loads(
                (run_dir / "evidence.json").read_text(encoding="utf-8")
            )
            timing = evidence["gates"]["static"]["semantic_audits"]["timing"]
            self.assertEqual([item["label"] for item in timing], ["HOLD_ON", "HOLD_OFF"])
            self.assertTrue(all(item["status"] == "pass" for item in timing))


if __name__ == "__main__":
    unittest.main()
