from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from references.spec.tools.tests.test_asm_static_check import (
    BULK_GPIO_WARNING_SOURCE,
    COMPLIANT_LED_SOURCE,
    PROBLEM_LED_SOURCE,
)


SKILL_ROOT = Path(__file__).resolve().parents[1]
CLI = SKILL_ROOT / "scripts" / "hk8asm.py"
COMPILER_ADAPTER = SKILL_ROOT / "scripts" / "compiler_adapter.py"
FAKE_ADAPTER = Path(__file__).parent / "fixtures" / "fake_adapter.py"
FAKE_ASMC_CLI = Path(__file__).parent / "fixtures" / "fake_asmc_cli.py"
EXAMPLE_PROFILE = SKILL_ROOT / "references" / "profiles" / "HK64S825.profile.example.json"
EXAMPLE_CONFIG = SKILL_ROOT / "references" / "configs" / "local-adapter.example.json"
CANONICAL_PROFILE = SKILL_ROOT / "references" / "profiles" / "HK64S825.profile.json"
CANONICAL_CONFIG = SKILL_ROOT / "references" / "configs" / "builtin-config.json"
BUILTIN_COMPILER = SKILL_ROOT / "scripts" / "builtin_compiler.py"
BDF_CONVERTER = SKILL_ROOT / "scripts" / "bdf_to_ssd1306.py"
BITMAP_AUDITOR = SKILL_ROOT / "scripts" / "ssd1306_page_bitmap.py"
STANDARD_TEXT_FONT = (
    SKILL_ROOT
    / "references"
    / "fonts"
    / "wenquanyi_bitmap_song_16px_ascii_date_cn.bdf"
)
INSTRUCTION_REFERENCE = SKILL_ROOT / "references" / "spec" / "rules" / "instruction-reference.json"
OLED_ORIENTATION_PROFILE_ID = "hk64s825-4digit-mixed-pa-pb-e1-a1-c0-page-lsb-top-v1"
REQUIRED_FAKE_COMPILER_FILES = (
    "src/core/assembler.py",
    "src/core/output_generator.py",
    "src/core/chip_manager.py",
    "src/core/online_flasher.py",
    "instruction_set.xlsx",
    "register_set.xlsx",
)


class ClosedLoopCliContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profile_path = self.root / "profile.json"
        self.config_path = self.root / "config.json"
        self.request_path = self.root / "request.json"
        self.source_path = self.root / "candidate.asm"
        self._write_json(self.profile_path, self.profile())
        self._write_json(self.config_path, self.config())
        self._write_json(self.request_path, self.request())
        self.source_path.write_text(
            "; CHIP: HK64S825\n; 用途：闭环测试夹具\nORG 0x0000\nSTART:\n    NOP\n    SJMP START\nEND\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def orientation_profile(board_id: str) -> dict:
        return {
            "board_id": board_id,
            "controller": "SSD1306",
            "segment_remap": "A1H",
            "com_scan_direction": "C0H",
            "source_format": "ssd1306-page-lsb-top",
            "transform": {
                "mirror_x_within_glyphs": False,
                "mirror_y": True,
            },
            "evidence": {
                "level": "E1",
                "status": "hardware_verified",
                "verified_on": "2026-07-25",
                "run_id": "5b3f95decabb44b1b1c396cb871b7882",
                "asset_source_sha256": "d3c2259b0dedfb924f11d7ba57a0b2eb14308d03cbca87866b99503dee9db179",
                "asset_output_sha256": "c1e93c79c3d4f21871ffb881f13ff5404bee912420d2638e1de41ac435a2eb7d",
                "asm_sha256": "6856a0860da8c92e317f3d376e04f985c8b5166e65cdc5e016e61b71ef2b6213",
                "hex_sha256": "72a91967fa990669ea47fcd5af641c5d31b860c9d719ea766d3e3a964ef8b8b8",
            },
        }

    @classmethod
    def profile(cls, *, status: str = "ready") -> dict:
        return {
            "schema_version": 1,
            "chip": "HK64S825",
            "aliases": ["HK64S825"],
            "status": status,
            "expected_device_id": "HK64S825-SIM",
            "approved_tool_versions": {
                "compiler": ["sim-1.0"],
                "programmer": ["sim-1.0"],
                "verifier": ["sim-1.0"],
            },
            "max_flash_attempts": 3,
            "orientation_profiles": {
                OLED_ORIENTATION_PROFILE_ID: cls.orientation_profile(
                    "HK64S825-SIM-BOARD"
                )
            },
            "asm_rules": {
                "required_patterns": ["; CHIP: HK64S825", "ORG", "END"],
                "forbidden_patterns": ["FUSE", "LOCKBIT", "SECURITYBIT"],
                "max_line_length": 100,
            },
        }

    @staticmethod
    def request() -> dict:
        return {
            "schema_version": 1,
            "chip": "HK64S825",
            "behavior": "Toggle the fixture output forever",
            "clock_hz": 8_000_000,
            "pins": {"fixture_output": "SIM.P0"},
            "peripherals": [],
            "timing": {"period_us": 1000},
            "memory_limits": {"rom_bytes": 64, "ram_bytes": 8},
            "board": {"id": "HK64S825-SIM-BOARD"},
            "input_provenance": {
                "board": "user_confirmed_profile",
                "pins": "user_confirmed_profile",
                "clock": "user_confirmed_profile",
            },
            "acceptance": [
                {
                    "name": "fixture-observable",
                    "observable": "simulated-pin",
                    "expected": "toggles",
                }
            ],
            "allow_nonvolatile_changes": False,
        }

    @classmethod
    def structured_gpio_request(cls) -> dict:
        request = cls.request()
        request.pop("clock_hz")
        request["clock"] = {"osc_hz": 16_000_000, "sck_ps": "reset"}
        request["pins"] = {
            "led_outputs": {
                "port": "PA",
                "bits": [0],
                "direction": "output",
                "drive": "push_pull",
                "active_level": "high",
                "initial_state": "off",
                "preserve_unowned_bits": True,
            }
        }
        request["timing"] = {
            "precision": "precise",
            "delay_targets": [
                {
                    "label": "DELAY_500MS",
                    "target_us": 500_000,
                    "tolerance_percent": 1.0,
                }
            ],
        }
        return request

    @classmethod
    def minimal_non_gpio_request(cls) -> dict:
        request = cls.request()
        request.pop("clock_hz")
        request.pop("pins")
        request.pop("timing")
        request["behavior"] = "执行最小空操作循环"
        request["peripherals"] = []
        return request

    @classmethod
    def led_regression_request(cls) -> dict:
        request = cls.structured_gpio_request()
        request["behavior"] = "PA0、PA3、PA5 同步闪烁，高电平点亮"
        request["pins"]["led_outputs"]["bits"] = [0, 3, 5]
        request["peripherals"] = [{"name": "gpio"}]
        request["memory_limits"] = {"rom_bytes": 2048, "ram_bytes": 128}
        request["board"] = {"id": "HK64S825-4DIGIT-MIXED-PA-PB-E1"}
        request["acceptance"] = []
        return request

    @staticmethod
    def clock_model() -> dict:
        profile = json.loads(EXAMPLE_PROFILE.read_text(encoding="utf-8-sig"))
        return profile["clock_model"]

    @staticmethod
    def config(*, failures: dict[str, str] | None = None) -> dict:
        command = [sys.executable, str(FAKE_ADAPTER)]
        return {
            "schema_version": 1,
            "board_id": "HK64S825-SIM-BOARD",
            "programmer_serial": "SIM-PROGRAMMER-001",
            "voltage_mv": 5000,
            "simulate": failures or {},
            "adapters": {
                "compiler": {"command": command},
                "programmer": {"command": command},
                "verifier": {"command": command},
            },
        }

    @staticmethod
    def compile_only_config(*, failures: dict[str, str] | None = None) -> dict:
        command = [sys.executable, str(FAKE_ADAPTER)]
        return {
            "schema_version": 1,
            "board_id": "HK64S825-SIM-BOARD",
            "simulate": failures or {},
            "adapters": {
                "compiler": {"command": command},
            },
        }

    @staticmethod
    def config_with_command(command: list[str], *, failures: dict[str, str] | None = None) -> dict:
        config = ClosedLoopCliContractTests.config(failures=failures)
        for role in ("compiler", "programmer", "verifier"):
            config["adapters"][role]["command"] = command
        return config

    def fake_compiler_source_root(self) -> Path:
        root = self.root / "fake-company-compiler"
        for relative in REQUIRED_FAKE_COMPILER_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"fixture for {relative}\n", encoding="utf-8")
        include = root / "include" / "REG825.INC"
        include.parent.mkdir(parents=True, exist_ok=True)
        include.write_text("; fixture register include\n", encoding="utf-8")
        return root

    def compiler_adapter_command(self, *, tool_version: str = "fixture-compiler-1") -> list[str]:
        return [
            sys.executable,
            str(COMPILER_ADAPTER),
            "--asmc-cli",
            str(FAKE_ASMC_CLI),
            "--compiler-source-root",
            str(self.fake_compiler_source_root()),
            "--compiler-mcu-type",
            "HK64S825",
            "--tool-version",
            tool_version,
        ]

    def context_checker_spec_root(
        self,
        name: str = "context-checker-spec",
        *,
        semantic_audits: dict | None = None,
    ) -> Path:
        spec_root = self.root / name
        checker = spec_root / "tools" / "asm_static_check.py"
        checker.parent.mkdir(parents=True)
        checker_source = """#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("asm", type=Path)
parser.add_argument("--toolchain", required=True)
parser.add_argument("--request", required=True, type=Path)
parser.add_argument("--profile", required=True, type=Path)
parser.add_argument("--json", action="store_true")
args = parser.parse_args()

errors = []
try:
    request = json.loads(args.request.read_text(encoding="utf-8-sig"))
    profile = json.loads(args.profile.read_text(encoding="utf-8-sig"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    errors.append(str(exc))
else:
    if request.get("chip") != "HK64S825" or profile.get("chip") != "HK64S825":
        errors.append("snapshot chip mismatch")

audit_payload = __AUDIT_PAYLOAD__
payload = {
    "contract_context": {
        "request_loaded": True,
        "profile_loaded": True,
        "chip": "HK64S825",
    },
    "files": [],
    "findings": [{"error": error} for error in errors],
    "summary": {
        "blockers": 0,
        "errors": len(errors),
        "warnings": 0,
        "info": 0,
        "exit_code": 2 if errors else 0,
    },
}
if audit_payload is not None:
    payload["semantic_audits"] = audit_payload
print(json.dumps(payload))
raise SystemExit(payload["summary"]["exit_code"])
"""
        checker.write_text(
            checker_source.replace("__AUDIT_PAYLOAD__", repr(semantic_audits)),
            encoding="utf-8",
        )
        return spec_root

    def payload_checker_spec_root(
        self,
        name: str,
        payload: object,
        *,
        exit_code: int = 0,
    ) -> Path:
        spec_root = self.root / name
        checker = spec_root / "tools" / "asm_static_check.py"
        checker.parent.mkdir(parents=True)
        checker_source = """#!/usr/bin/env python3
import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("asm")
parser.add_argument("--toolchain", required=True)
parser.add_argument("--request", required=True)
parser.add_argument("--profile", required=True)
parser.add_argument("--json", action="store_true")
parser.add_argument("--strict-warnings", action="store_true")
parser.parse_args()

print(json.dumps(__PAYLOAD__))
raise SystemExit(__EXIT_CODE__)
"""
        checker.write_text(
            checker_source.replace("__PAYLOAD__", repr(payload)).replace(
                "__EXIT_CODE__", str(exit_code)
            ),
            encoding="utf-8",
        )
        return spec_root

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.assertTrue(CLI.exists(), f"production CLI missing: {CLI}")
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            env=env,
            capture_output=True,
            check=False,
        )

    def payload(self, completed: subprocess.CompletedProcess[str]) -> dict:
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"stdout must contain one JSON document: {exc}: {completed.stdout!r}")

    def new_run(self, name: str = "run") -> Path:
        run_dir = self.root / name
        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertEqual("RUN_CREATED", self.payload(result)["code"])
        return run_dir

    def new_bundled_run(self, source: str, name: str) -> Path:
        self._write_json(self.request_path, self.led_regression_request())
        with self.source_path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(source)
        run_dir = self.root / name
        result = self.run_cli(
            "new-run",
            "--profile",
            str(EXAMPLE_PROFILE),
            "--config",
            str(EXAMPLE_CONFIG),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertEqual("RUN_CREATED", self.payload(result)["code"])
        return run_dir

    def configure_multi_page_display_asset(self) -> tuple[Path, list[int]]:
        manifest_path = self.root / "display-asset.json"
        generated = subprocess.run(
            [
                sys.executable,
                str(BDF_CONVERTER),
                str(STANDARD_TEXT_FONT),
                "--text",
                "12",
                "--widths",
                "8,8",
                "--asset-id",
                "display-asset",
                "--output",
                str(manifest_path),
            ],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, generated.returncode, generated.stderr)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_bytes = [
            int(value[:-1], 16) for value in manifest["source"]["bytes"]
        ]
        source_hash = manifest["expected_source_sha256"]
        output_hash = manifest["expected_output_sha256"]
        output_bytes = [
            int(value[:-1], 16)
            for value in json.loads(
                subprocess.run(
                    [sys.executable, str(BITMAP_AUDITOR), str(manifest_path)],
                    cwd=SKILL_ROOT,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=True,
                ).stdout
            )["output_bytes_hex"]
        ]
        request = self.minimal_non_gpio_request()
        request["display"] = {
            "text": "12",
            "window": {
                "column_start": "00H",
                "column_end": "0FH",
                "page_start": "00H",
                "page_end": "01H",
            },
            "byte_count": 32,
            "format": "SSD1306 page format, bit0 top",
            "asset": {
                "manifest": manifest_path.name,
                "source_encoding": "db",
                "orientation_profile": OLED_ORIENTATION_PROFILE_ID,
                "source_label": "DISPLAY_DATA",
                "table_sender": "SEND_DISPLAY_DATA",
                "byte_count": 32,
                "source_sha256": source_hash,
                "output_sha256": output_hash,
            },
        }
        self._write_json(self.request_path, request)
        literals = ["{:02X}H".format(value) for value in output_bytes]
        lines = [
            "; CHIP: HK64S825",
            "; 用途：多页字模资产闭环测试",
            "ORG 0x0000",
            "START:",
            "    CALL SHOW_BITMAP",
            "    JMP START",
            "SHOW_BITMAP:",
            "    CALL SEND_DISPLAY_DATA",
            "    RET",
            "CONSUME_BYTE:",
            "    RET",
            "; 查表配对 TABLE_PAIR: DISPLAY_DATA,SEND_DISPLAY_DATA",
            "ORG 0x0100",
            "DISPLAY_DATA:",
            "    DB " + ",".join(literals),
            "SEND_DISPLAY_DATA:",
            "    MOV A,#00H",
            "    MOV 88H,A",
            "    MOV A,#10H",
            "    MOV 89H,A",
            "SEND_DISPLAY_DATA_LOOP:",
            "    MOV A,88H",
            "    TABL",
            "    CALL CONSUME_BYTE",
            "    MOV A,88H",
            "    TABH",
            "    CALL CONSUME_BYTE",
            "    INCR 88H",
            "    DECSZR 89H",
            "    JMP SEND_DISPLAY_DATA_LOOP",
            "    RET",
            "END",
        ]
        self.source_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return manifest_path, output_bytes

    def configure_self_consistent_but_semantically_wrong_text_asset(self) -> Path:
        manifest_path = self.root / "wrong-unicode-glyph.json"
        generated = subprocess.run(
            [
                sys.executable,
                str(BDF_CONVERTER),
                str(STANDARD_TEXT_FONT),
                "--text",
                "12",
                "--widths",
                "8,8",
                "--asset-id",
                "wrong-unicode-glyph",
                "--output",
                str(manifest_path),
            ],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, generated.returncode, generated.stderr)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source"]["bytes"][0] = "01H"
        manifest.pop("expected_source_sha256")
        manifest.pop("expected_output_sha256")
        self._write_json(manifest_path, manifest)
        audited = subprocess.run(
            [sys.executable, str(BITMAP_AUDITOR), str(manifest_path)],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, audited.returncode, audited.stderr)
        audit = json.loads(audited.stdout)
        manifest["expected_source_sha256"] = audit["source_sha256"]
        manifest["expected_output_sha256"] = audit["output_sha256"]
        self._write_json(manifest_path, manifest)

        request = self.minimal_non_gpio_request()
        request["display"] = {
            "text": "12",
            "window": {
                "column_start": "00H",
                "column_end": "0FH",
                "page_start": "00H",
                "page_end": "01H",
            },
            "byte_count": 32,
            "asset": {
                "manifest": manifest_path.name,
                "source_encoding": "db",
                "orientation_profile": OLED_ORIENTATION_PROFILE_ID,
                "source_label": "DISPLAY_DATA",
                "table_sender": "SEND_DISPLAY_DATA",
                "byte_count": 32,
                "source_sha256": audit["source_sha256"],
                "output_sha256": audit["output_sha256"],
            },
        }
        self._write_json(self.request_path, request)
        lines = [
            "; CHIP: HK64S825",
            "; 用途：拒绝标签与像素语义不一致的文字资产",
            "ORG 0x0000",
            "START:",
            "    CALL SEND_DISPLAY_DATA",
            "    JMP START",
            "CONSUME_BYTE:",
            "    RET",
            "; 查表配对 TABLE_PAIR: DISPLAY_DATA,SEND_DISPLAY_DATA",
            "ORG 0x0100",
            "DISPLAY_DATA:",
            "    DB " + ",".join(audit["output_bytes_hex"]),
            "SEND_DISPLAY_DATA:",
            "    MOV A,#00H",
            "    MOV 88H,A",
            "    MOV A,#10H",
            "    MOV 89H,A",
            "SEND_DISPLAY_DATA_LOOP:",
            "    MOV A,88H",
            "    TABL",
            "    CALL CONSUME_BYTE",
            "    MOV A,88H",
            "    TABH",
            "    CALL CONSUME_BYTE",
            "    INCR 88H",
            "    DECSZR 89H",
            "    JMP SEND_DISPLAY_DATA_LOOP",
            "    RET",
            "END",
        ]
        self.source_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return manifest_path

    def test_doctor_fails_closed_when_profile_is_not_ready(self) -> None:
        self._write_json(self.profile_path, self.profile(status="requires-vendor-materials"))
        result = self.run_cli(
            "doctor", "--profile", str(self.profile_path), "--config", str(self.config_path)
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("PROFILE_NOT_READY", self.payload(result)["code"])

    def test_profile_cannot_raise_flash_limit_above_three(self) -> None:
        profile = self.profile()
        profile["max_flash_attempts"] = 4
        self._write_json(self.profile_path, profile)
        result = self.run_cli(
            "doctor", "--profile", str(self.profile_path), "--config", str(self.config_path)
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("INVALID_PROFILE", self.payload(result)["code"])

    def test_profile_rejects_invalid_warning_whitelist(self) -> None:
        profile = self.profile()
        profile["allowed_warnings"] = [{}]
        self._write_json(self.profile_path, profile)
        result = self.run_cli(
            "doctor", "--profile", str(self.profile_path), "--config", str(self.config_path)
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("INVALID_PROFILE", self.payload(result)["code"])

    def test_profile_accepts_sh1106_orientation_controller(self) -> None:
        profile = self.profile()
        profile["orientation_profiles"][OLED_ORIENTATION_PROFILE_ID]["controller"] = "SH1106"
        profile["orientation_profiles"][OLED_ORIENTATION_PROFILE_ID]["com_scan_direction"] = "C8H"
        self._write_json(self.profile_path, profile)
        result = self.run_cli(
            "doctor", "--profile", str(self.profile_path), "--config", str(self.config_path)
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_profile_rejects_unknown_orientation_controller(self) -> None:
        profile = self.profile()
        profile["orientation_profiles"][OLED_ORIENTATION_PROFILE_ID]["controller"] = "UNKNOWN"
        self._write_json(self.profile_path, profile)
        result = self.run_cli(
            "doctor", "--profile", str(self.profile_path), "--config", str(self.config_path)
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("INVALID_PROFILE", self.payload(result)["code"])

    def test_profile_rejects_non_finite_clock_divider(self) -> None:
        profile = self.profile()
        profile["clock_model"] = self.clock_model()
        profile["clock_model"]["divider_by_mode"]["high"]["1"] = math.inf
        self._write_json(self.profile_path, profile)
        result = self.run_cli(
            "doctor", "--profile", str(self.profile_path), "--config", str(self.config_path)
        )
        self.assertNotEqual(0, result.returncode)
        payload = self.payload(result)
        self.assertEqual("INVALID_PROFILE", payload["code"])
        self.assertIn("divider_by_mode", payload["message"])

    def test_profile_rejects_reset_clock_selector_zero(self) -> None:
        profile = self.profile()
        profile["clock_model"] = self.clock_model()
        profile["clock_model"]["sck_ps_reset"] = 0x30
        self._write_json(self.profile_path, profile)

        result = self.run_cli(
            "doctor", "--profile", str(self.profile_path), "--config", str(self.config_path)
        )

        self.assertNotEqual(0, result.returncode)
        payload = self.payload(result)
        self.assertEqual("INVALID_PROFILE", payload["code"])
        self.assertIn("selector", payload["message"])

    def test_profile_clock_model_rejects_extra_modes_and_selectors(self) -> None:
        for name in ("extra-mode", "extra-selector"):
            with self.subTest(name=name):
                profile = self.profile()
                profile["clock_model"] = self.clock_model()
                if name == "extra-mode":
                    profile["clock_model"]["divider_by_mode"]["turbo"] = {}
                else:
                    profile["clock_model"]["divider_by_mode"]["high"]["16"] = 1
                self._write_json(self.profile_path, profile)
                result = self.run_cli(
                    "doctor",
                    "--profile",
                    str(self.profile_path),
                    "--config",
                    str(self.config_path),
                )
                self.assertNotEqual(0, result.returncode)
                self.assertEqual("INVALID_PROFILE", self.payload(result)["code"])

    def test_new_run_validates_and_snapshots_inputs(self) -> None:
        run_dir = self.new_run()
        self.assertTrue((run_dir / "run.json").is_file())
        self.assertTrue((run_dir / "profile.json").is_file())
        self.assertTrue((run_dir / "config.json").is_file())
        self.assertTrue((run_dir / "request.json").is_file())
        self.assertTrue((run_dir / "src" / "candidate.asm").is_file())

    def test_hardware_request_requires_confirmed_board_provenance(self) -> None:
        request = self.structured_gpio_request()
        del request["input_provenance"]["board"]
        self._write_json(self.request_path, request)

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "unconfirmed-board"),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("BOARD_PROFILE_UNCONFIRMED", self.payload(result)["code"])

    def test_gpio_request_requires_confirmed_pin_provenance(self) -> None:
        request = self.structured_gpio_request()
        del request["input_provenance"]["pins"]
        self._write_json(self.request_path, request)

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "unconfirmed-pins"),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("BOARD_INPUT_UNCONFIRMED", self.payload(result)["code"])

    def test_timed_request_requires_confirmed_clock_provenance(self) -> None:
        request = self.structured_gpio_request()
        del request["input_provenance"]["clock"]
        self._write_json(self.request_path, request)

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "unconfirmed-clock"),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("BOARD_INPUT_UNCONFIRMED", self.payload(result)["code"])

    def test_compile_only_config_is_board_agnostic(self) -> None:
        config = self.compile_only_config()
        del config["board_id"]
        self._write_json(self.config_path, config)
        request = self.structured_gpio_request()
        request["board"] = {"id": "CUSTOM-HK64S825-BOARD"}
        request["input_provenance"] = {
            "board": "user_provided",
            "pins": "user_provided",
            "clock": "user_provided",
        }
        self._write_json(self.request_path, request)

        run_dir = self.root / "board-agnostic-compile"
        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )

        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertEqual("RUN_CREATED", self.payload(result)["code"])

    def test_new_run_accepts_non_gpio_non_timing_request_without_pins_or_clock(self) -> None:
        self._write_json(self.request_path, self.minimal_non_gpio_request())

        run_dir = self.root / "minimal-non-gpio"
        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )

        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertEqual("RUN_CREATED", self.payload(result)["code"])

    def test_multi_page_display_requires_asset_contract(self) -> None:
        request = self.minimal_non_gpio_request()
        request["display"] = {
            "text": "左右",
            "window": {
                "column_start": 0,
                "column_end": 3,
                "page_start": 0,
                "page_end": 1,
            },
            "byte_count": 8,
        }
        self._write_json(self.request_path, request)
        run_dir = self.root / "missing-display-asset"

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("INVALID_REQUEST", self.payload(result)["code"])
        self.assertIn("display.asset", self.payload(result)["message"])
        self.assertFalse(run_dir.exists())

    def test_multi_page_asset_is_audited_and_bound_to_evidence(self) -> None:
        manifest_path, _output_bytes = self.configure_multi_page_display_asset()
        request = json.loads(self.request_path.read_text(encoding="utf-8"))
        request["board"] = {"id": "HK64S825-4DIGIT-MIXED-PA-PB-E1"}
        request["memory_limits"] = {"rom_bytes": 2048, "ram_bytes": 64}
        self._write_json(self.request_path, request)
        run_dir = self.root / "display-asset-pass"

        created = self.run_cli(
            "new-run",
            "--profile",
            str(CANONICAL_PROFILE),
            "--config",
            str(CANONICAL_CONFIG),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(0, created.returncode, created.stderr or created.stdout)
        self.assertTrue((run_dir / "assets" / "display-asset.json").is_file())

        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
        audit = evidence["gates"]["static"]["display_asset_audit"]
        self.assertEqual("pass", audit["status"])
        self.assertEqual(32, audit["output_byte_count"])
        self.assertEqual(["1", "2"], audit["text_order"])
        self.assertEqual(OLED_ORIENTATION_PROFILE_ID, audit["orientation_profile"])
        self.assertEqual(
            {"mirror_x_within_glyphs": False, "mirror_y": True},
            audit["transform"],
        )
        self.assertEqual("A1H", audit["orientation"]["segment_remap"])
        self.assertEqual("C0H", audit["orientation"]["com_scan_direction"])
        self.assertIn("display_asset_sha256", evidence["snapshots"])

        snapshot = run_dir / "assets" / "display-asset.json"
        snapshot.write_text(manifest_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        release = self.run_cli(
            "release",
            "--run-dir",
            str(run_dir),
            "--output",
            str(self.root / "display-asset-released.asm"),
        )
        self.assertNotEqual(0, release.returncode)
        self.assertEqual("RELEASE_BLOCKED", self.payload(release)["code"])

    def test_db_display_close_loop_rechecks_table_pair_with_final_map(self) -> None:
        self.configure_multi_page_display_asset()
        request = json.loads(self.request_path.read_text(encoding="utf-8"))
        request["board"] = {"id": "HK64S825-4DIGIT-MIXED-PA-PB-E1"}
        request["memory_limits"] = {"rom_bytes": 2048, "ram_bytes": 64}
        self._write_json(self.request_path, request)
        run_dir = self.root / "display-asset-db-final-map"

        created = self.run_cli(
            "new-run",
            "--profile",
            str(CANONICAL_PROFILE),
            "--config",
            str(CANONICAL_CONFIG),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(0, created.returncode, created.stderr or created.stdout)

        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
        static = evidence["gates"]["static"]
        self.assertEqual(0, static["summary"]["warnings"])
        self.assertNotIn("deferred_table_map", static)
        self.assertEqual("db", static["display_asset_audit"]["source_encoding"])
        self.assertEqual("SEND_DISPLAY_DATA", static["display_asset_audit"]["table_sender"])
        self.assertEqual("map", static["table_pairs"][0]["evidence"])
        self.assertTrue(static["table_pairs"][0]["same_256_word_page"])

    def test_db_display_close_loop_requires_packaged_map_checker(self) -> None:
        self.configure_multi_page_display_asset()
        run_dir = self.new_run("display-asset-db-no-map-checker")

        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))

        self.assertNotEqual(0, loop.returncode)
        self.assertEqual("STATIC_CHECK_FAILED", self.payload(loop)["code"])
        self.assertIn("final MAP proof", self.payload(loop)["message"])

    def test_multi_page_asset_rejects_asm_byte_drift_before_run_creation(self) -> None:
        _manifest_path, output_bytes = self.configure_multi_page_display_asset()
        source = self.source_path.read_text(encoding="utf-8")
        first = "{:02X}H".format(output_bytes[0])
        changed = "{:02X}H".format(output_bytes[0] ^ 0x01)
        self.source_path.write_text(
            source.replace("DB " + first, "DB " + changed, 1),
            encoding="utf-8",
        )
        run_dir = self.root / "display-asset-byte-drift"

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("DISPLAY_ASSET_MISMATCH", self.payload(result)["code"])
        self.assertIn("SHA256", self.payload(result)["message"])
        self.assertFalse(run_dir.exists())

    def test_text_asset_rejects_self_consistent_bytes_that_do_not_match_unicode_glyphs(self) -> None:
        self.configure_self_consistent_but_semantically_wrong_text_asset()
        run_dir = self.root / "wrong-unicode-glyph"

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual(
            "DISPLAY_GLYPH_PROVENANCE_MISMATCH", self.payload(result)["code"]
        )
        self.assertIn("Unicode glyph", self.payload(result)["message"])
        self.assertFalse(run_dir.exists())

    def test_db_display_asset_requires_table_sender(self) -> None:
        self.configure_multi_page_display_asset()
        request = json.loads(self.request_path.read_text(encoding="utf-8"))
        request["display"]["asset"].pop("table_sender")
        self._write_json(self.request_path, request)
        run_dir = self.root / "display-asset-db-missing-sender"

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("INVALID_REQUEST", self.payload(result)["code"])
        self.assertIn("table_sender", self.payload(result)["message"])
        self.assertFalse(run_dir.exists())

    def test_db_display_asset_requires_orientation_profile(self) -> None:
        self.configure_multi_page_display_asset()
        request = json.loads(self.request_path.read_text(encoding="utf-8"))
        request["display"]["asset"].pop("orientation_profile")
        self._write_json(self.request_path, request)

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "display-asset-missing-orientation-profile"),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("INVALID_REQUEST", self.payload(result)["code"])
        self.assertIn("orientation_profile", self.payload(result)["message"])

    def test_db_display_asset_rejects_transform_outside_orientation_profile(self) -> None:
        manifest_path, _output_bytes = self.configure_multi_page_display_asset()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["transform"]["mirror_x_within_glyphs"] = True
        self._write_json(manifest_path, manifest)

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "display-asset-wrong-orientation-transform"),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual(
            "DISPLAY_ASSET_ORIENTATION_MISMATCH", self.payload(result)["code"]
        )
        self.assertIn("mirror transform", self.payload(result)["message"])

    def test_db_display_asset_rejects_source_format_outside_orientation_profile(self) -> None:
        manifest_path, _output_bytes = self.configure_multi_page_display_asset()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source"]["format"] = "ssd1306-page-msb-top"
        self._write_json(manifest_path, manifest)

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "display-asset-wrong-orientation-source-format"),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual(
            "DISPLAY_ASSET_ORIENTATION_MISMATCH", self.payload(result)["code"]
        )
        self.assertIn("source format", self.payload(result)["message"])

    def test_db_display_asset_rejects_orientation_profile_for_other_board(self) -> None:
        self.configure_multi_page_display_asset()
        profile = self.profile()
        profile["orientation_profiles"][OLED_ORIENTATION_PROFILE_ID]["board_id"] = (
            "ANOTHER-BOARD"
        )
        self._write_json(self.profile_path, profile)

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "display-asset-wrong-orientation-board"),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("INVALID_REQUEST", self.payload(result)["code"])
        self.assertIn("request board", self.payload(result)["message"])

    def test_db_display_asset_requires_exact_table_pair(self) -> None:
        self.configure_multi_page_display_asset()
        source = self.source_path.read_text(encoding="utf-8")
        self.source_path.write_text(
            source.replace(
                "; 查表配对 TABLE_PAIR: DISPLAY_DATA,SEND_DISPLAY_DATA",
                "; 查表配对 TABLE_PAIR: DISPLAY_DATA,WRONG_SENDER",
            ),
            encoding="utf-8",
        )
        run_dir = self.root / "display-asset-db-wrong-pair"

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("DISPLAY_TABLE_INVALID", self.payload(result)["code"])
        self.assertIn("TABLE_PAIR", self.payload(result)["message"])
        self.assertFalse(run_dir.exists())

    def test_db_display_asset_rejects_sender_with_wrong_table_read_order(self) -> None:
        self.configure_multi_page_display_asset()
        source = self.source_path.read_text(encoding="utf-8")
        self.source_path.write_text(
            source.replace(
                "    TABL\n    CALL CONSUME_BYTE\n    MOV A,88H\n    TABH",
                "    TABH\n    CALL CONSUME_BYTE\n    MOV A,88H\n    TABL",
            ),
            encoding="utf-8",
        )
        run_dir = self.root / "display-asset-db-wrong-table-read-order"

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("DISPLAY_TABLE_INVALID", self.payload(result)["code"])
        self.assertIn("TABL followed by one TABH", self.payload(result)["message"])
        self.assertFalse(run_dir.exists())

    def test_text_display_asset_rejects_inline_i2c_send(self) -> None:
        self.configure_multi_page_display_asset()
        request = json.loads(self.request_path.read_text(encoding="utf-8"))
        asset = request["display"]["asset"]
        asset["source_encoding"] = "inline_i2c_send"
        asset["source_label"] = "OLED_SHOW_BITMAP"
        asset.pop("table_sender")
        self._write_json(self.request_path, request)
        run_dir = self.root / "display-asset-inline-text"

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )

        self.assertNotEqual(0, result.returncode)
        self.assertEqual("INVALID_REQUEST", self.payload(result)["code"])
        self.assertIn("use db for text and images", self.payload(result)["message"])
        self.assertFalse(run_dir.exists())

    def test_explicit_eight_byte_non_text_probe_can_use_inline_i2c_send(self) -> None:
        source_bytes = list(range(1, 9))
        source_hash = hashlib.sha256(bytes(source_bytes)).hexdigest()
        manifest_path = self.root / "inline-probe.json"
        self._write_json(
            manifest_path,
            {
                "schema_version": 1,
                "width": 8,
                "height": 8,
                "layout": [{"label": "probe", "width": 8}],
                "source": {
                    "format": "ssd1306-page-lsb-top",
                    "bytes": ["{:02X}H".format(value) for value in source_bytes],
                },
                "transform": {
                    "mirror_x_within_glyphs": False,
                    "mirror_y": False,
                },
                "expected_source_sha256": source_hash,
                "expected_output_sha256": source_hash,
            },
        )
        request = self.minimal_non_gpio_request()
        request["behavior"] = "执行八字节总线方向探针"
        request["display"] = {
            "window": {
                "column_start": 0,
                "column_end": 7,
                "page_start": 0,
                "page_end": 0,
            },
            "byte_count": 8,
            "asset": {
                "manifest": manifest_path.name,
                "source_encoding": "inline_i2c_send",
                "source_label": "SHOW_PROBE",
                "role": "probe",
                "byte_count": 8,
                "source_sha256": source_hash,
                "output_sha256": source_hash,
            },
        }
        self._write_json(self.request_path, request)
        lines = ["; CHIP: HK64S825", "ORG 0", "SHOW_PROBE:"]
        for value in source_bytes:
            lines.extend((f"    MOV A,#{value:02X}H", "    CALL I2C_SEND"))
        lines.extend(("    RET", "I2C_SEND:", "    RET", "END"))
        self.source_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        run_dir = self.root / "display-inline-probe"

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )

        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertEqual("RUN_CREATED", self.payload(result)["code"])

    def test_new_run_accepts_approximate_timing_without_clock(self) -> None:
        request = self.minimal_non_gpio_request()
        request["timing"] = {"precision": "approximate"}
        self._write_json(self.request_path, request)

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "approximate-no-clock"),
        )

        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_approximate_numeric_timing_requires_clock_evidence(self) -> None:
        cases = {
            "suffix": {"precision": "approximate", "period_ms": 500},
            "value-unit": {"precision": "approximate", "period": 500, "unit": "ms"},
        }
        for name, timing in cases.items():
            with self.subTest(name=name):
                request = self.minimal_non_gpio_request()
                request["timing"] = timing
                self._write_json(self.request_path, request)
                run_dir = self.root / f"approximate-period-no-clock-{name}"

                result = self.run_cli(
                    "new-run",
                    "--profile",
                    str(self.profile_path),
                    "--config",
                    str(self.config_path),
                    "--request",
                    str(self.request_path),
                    "--source",
                    str(self.source_path),
                    "--run-dir",
                    str(run_dir),
                )

                self.assertNotEqual(0, result.returncode)
                payload = self.payload(result)
                self.assertEqual("INVALID_REQUEST", payload["code"])
                self.assertIn("clock", payload["message"])
                self.assertFalse(run_dir.exists())

    def test_approximate_numeric_timing_accepts_structured_clock(self) -> None:
        request = self.minimal_non_gpio_request()
        request["timing"] = {"precision": "approximate", "period_ms": 500}
        request["clock"] = {"osc_hz": 16_000_000, "sck_ps": "reset"}
        self._write_json(self.request_path, request)

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "approximate-period-with-clock"),
        )

        self.assertEqual(0, result.returncode, result.stderr or result.stdout)

    def test_gpio_request_without_pins_is_rejected(self) -> None:
        request = self.minimal_non_gpio_request()
        request["peripherals"] = [{"name": "gpio"}]
        self._write_json(self.request_path, request)

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "gpio-without-pins"),
        )

        self.assertNotEqual(0, result.returncode)
        payload = self.payload(result)
        self.assertEqual("INVALID_REQUEST", payload["code"])
        self.assertIn("pins", payload["message"])

    def test_gpio_backed_peripherals_require_pin_contracts(self) -> None:
        for peripheral in (
            "led",
            "oled",
            "i2c",
            "ssd1306",
            "seven-segment",
            "数码管",
            "gpio_led",
            "i2c_bus",
            "ssd1306_oled",
        ):
            with self.subTest(peripheral=peripheral):
                request = self.minimal_non_gpio_request()
                request["peripherals"] = [{"name": peripheral}]
                self._write_json(self.request_path, request)

                result = self.run_cli(
                    "new-run",
                    "--profile",
                    str(self.profile_path),
                    "--config",
                    str(self.config_path),
                    "--request",
                    str(self.request_path),
                    "--source",
                    str(self.source_path),
                    "--run-dir",
                    str(self.root / f"missing-pins-{peripheral}"),
                )

                self.assertNotEqual(0, result.returncode)
                payload = self.payload(result)
                self.assertEqual("INVALID_REQUEST", payload["code"])
                self.assertIn("pins", payload["message"])

    def test_gpio_behavior_keywords_require_pin_contracts(self) -> None:
        request = self.minimal_non_gpio_request()
        request["behavior"] = "让 PA0 LED 快速闪烁"
        self._write_json(self.request_path, request)

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "missing-pins-led-behavior"),
        )

        self.assertNotEqual(0, result.returncode)
        payload = self.payload(result)
        self.assertEqual("INVALID_REQUEST", payload["code"])
        self.assertIn("pins", payload["message"])

    def test_gpio_output_devices_reject_legacy_or_input_only_pins(self) -> None:
        cases = {
            "legacy-string": {"led": "PA0"},
            "input-only": {
                "led": {
                    "port": "PA",
                    "bits": [0],
                    "direction": "input",
                }
            },
        }
        for name, pins in cases.items():
            with self.subTest(name=name):
                request = self.minimal_non_gpio_request()
                request["behavior"] = "让 PA0 LED 点亮"
                request["peripherals"] = [{"name": "led"}]
                request["pins"] = pins
                self._write_json(self.request_path, request)

                result = self.run_cli(
                    "new-run",
                    "--profile",
                    str(self.profile_path),
                    "--config",
                    str(self.config_path),
                    "--request",
                    str(self.request_path),
                    "--source",
                    str(self.source_path),
                    "--run-dir",
                    str(self.root / f"invalid-led-pin-contract-{name}"),
                )

                self.assertNotEqual(0, result.returncode)
                payload = self.payload(result)
                self.assertEqual("INVALID_REQUEST", payload["code"])
                self.assertIn("output", payload["message"].lower())

    def test_precise_timing_requires_clock_and_delay_targets(self) -> None:
        cases = (
            (
                "missing-clock",
                {"precision": "precise", "delay_targets": [{"label": "WAIT", "target_us": 10, "tolerance_percent": 1}]},
                False,
                "clock",
            ),
            ("missing-targets", {"precision": "precise"}, True, "delay_targets"),
        )
        for name, timing, add_clock, expected_message in cases:
            with self.subTest(name=name):
                request = self.minimal_non_gpio_request()
                request["timing"] = timing
                if add_clock:
                    request["clock"] = {"osc_hz": 16_000_000, "sck_ps": "reset"}
                self._write_json(self.request_path, request)
                result = self.run_cli(
                    "new-run",
                    "--profile",
                    str(self.profile_path),
                    "--config",
                    str(self.config_path),
                    "--request",
                    str(self.request_path),
                    "--source",
                    str(self.source_path),
                    "--run-dir",
                    str(self.root / f"precise-{name}"),
                )
                self.assertNotEqual(0, result.returncode)
                payload = self.payload(result)
                self.assertEqual("INVALID_REQUEST", payload["code"])
                self.assertIn(expected_message, payload["message"])

    def test_delay_targets_require_explicit_precise_timing(self) -> None:
        request = self.minimal_non_gpio_request()
        request["clock"] = {"osc_hz": 16_000_000, "sck_ps": "reset"}
        request["timing"] = {
            "delay_targets": [
                {"label": "WAIT", "target_us": 10, "tolerance_percent": 1}
            ]
        }
        self._write_json(self.request_path, request)

        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "delay-targets-without-precision"),
        )

        self.assertNotEqual(0, result.returncode)
        payload = self.payload(result)
        self.assertEqual("INVALID_REQUEST", payload["code"])
        self.assertIn("precision", payload["message"])

    def test_precise_timing_rejects_invalid_structured_clock_values(self) -> None:
        cases = (
            ("bad-osc", {"osc_hz": 0, "sck_ps": "reset"}, "osc_hz"),
            ("bad-divider", {"osc_hz": 16_000_000, "sck_ps": 256}, "sck_ps"),
            ("selector-zero", {"osc_hz": 16_000_000, "sck_ps": 0x30}, "selector"),
        )
        for name, clock, expected_message in cases:
            with self.subTest(name=name):
                request = self.minimal_non_gpio_request()
                request["clock"] = clock
                request["timing"] = {
                    "precision": "precise",
                    "delay_targets": [
                        {"label": "WAIT", "target_us": 10, "tolerance_percent": 1}
                    ],
                }
                self._write_json(self.request_path, request)
                result = self.run_cli(
                    "new-run",
                    "--profile",
                    str(self.profile_path),
                    "--config",
                    str(self.config_path),
                    "--request",
                    str(self.request_path),
                    "--source",
                    str(self.source_path),
                    "--run-dir",
                    str(self.root / f"invalid-clock-{name}"),
                )
                self.assertNotEqual(0, result.returncode)
                payload = self.payload(result)
                self.assertEqual("INVALID_REQUEST", payload["code"])
                self.assertIn(expected_message, payload["message"])

    def test_json_inputs_accept_utf8_bom_from_windows_tools(self) -> None:
        self.profile_path.write_text(
            "\ufeff" + json.dumps(self.profile(), indent=2),
            encoding="utf-8",
        )
        self.config_path.write_text(
            "\ufeff" + json.dumps(self.compile_only_config(), indent=2),
            encoding="utf-8",
        )
        self.request_path.write_text(
            "\ufeff" + json.dumps(self.request(), indent=2),
            encoding="utf-8",
        )
        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "bom-json"),
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertEqual("RUN_CREATED", self.payload(result)["code"])

    def test_new_run_allows_request_without_hardware_acceptance(self) -> None:
        request = self.request()
        request["acceptance"] = []
        self._write_json(self.request_path, request)
        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "no-hardware-acceptance"),
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertEqual("RUN_CREATED", self.payload(result)["code"])

    def test_new_run_accepts_structured_gpio_output_contract(self) -> None:
        self._write_json(self.request_path, self.structured_gpio_request())
        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "structured-gpio"),
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertEqual("RUN_CREATED", self.payload(result)["code"])

    def test_new_run_accepts_structured_gpio_input_contract(self) -> None:
        request = self.request()
        request["pins"] = {
            "button": {
                "direction": "input",
                "port": "PA",
                "bits": [1],
            }
        }
        self._write_json(self.request_path, request)
        result = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(self.root / "structured-input"),
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertEqual("RUN_CREATED", self.payload(result)["code"])

    def test_new_run_rejects_missing_or_invalid_structured_pin_direction(self) -> None:
        for name, direction in (("missing", None), ("misspelled", "ouput")):
            with self.subTest(name=name):
                request = self.structured_gpio_request()
                if direction is None:
                    del request["pins"]["led_outputs"]["direction"]
                else:
                    request["pins"]["led_outputs"]["direction"] = direction
                self._write_json(self.request_path, request)
                result = self.run_cli(
                    "new-run",
                    "--profile",
                    str(self.profile_path),
                    "--config",
                    str(self.config_path),
                    "--request",
                    str(self.request_path),
                    "--source",
                    str(self.source_path),
                    "--run-dir",
                    str(self.root / f"invalid-direction-{name}"),
                )
                self.assertNotEqual(0, result.returncode)
                payload = self.payload(result)
                self.assertEqual("INVALID_REQUEST", payload["code"])
                self.assertIn("direction", payload["message"])

    def test_output_pin_contract_requires_drive_active_level_and_initial_state(self) -> None:
        for field in ("drive", "active_level", "initial_state"):
            with self.subTest(field=field):
                request = self.structured_gpio_request()
                del request["pins"]["led_outputs"][field]
                self._write_json(self.request_path, request)
                result = self.run_cli(
                    "new-run",
                    "--profile",
                    str(self.profile_path),
                    "--config",
                    str(self.config_path),
                    "--request",
                    str(self.request_path),
                    "--source",
                    str(self.source_path),
                    "--run-dir",
                    str(self.root / f"missing-{field}"),
                )
                self.assertNotEqual(0, result.returncode)
                payload = self.payload(result)
                self.assertEqual("INVALID_REQUEST", payload["code"])
                self.assertIn(field, payload["message"])

    def test_new_run_rejects_non_finite_legacy_timing_numbers(self) -> None:
        for name, value in (
            ("nan", math.nan),
            ("negative-infinity", -math.inf),
            ("infinity", math.inf),
        ):
            with self.subTest(name=name):
                request = self.request()
                request["timing"]["period_us"] = value
                self._write_json(self.request_path, request)
                result = self.run_cli(
                    "new-run",
                    "--profile",
                    str(self.profile_path),
                    "--config",
                    str(self.config_path),
                    "--request",
                    str(self.request_path),
                    "--source",
                    str(self.source_path),
                    "--run-dir",
                    str(self.root / f"non-finite-{name}"),
                )
                self.assertNotEqual(0, result.returncode)
                payload = self.payload(result)
                self.assertEqual("INVALID_REQUEST", payload["code"])
                self.assertIn("timing", payload["message"])

    def test_new_run_rejects_non_finite_structured_timing_targets(self) -> None:
        for field in ("target_us", "tolerance_percent"):
            with self.subTest(field=field):
                request = self.structured_gpio_request()
                request["timing"]["delay_targets"][0][field] = math.inf
                self._write_json(self.request_path, request)
                result = self.run_cli(
                    "new-run",
                    "--profile",
                    str(self.profile_path),
                    "--config",
                    str(self.config_path),
                    "--request",
                    str(self.request_path),
                    "--source",
                    str(self.source_path),
                    "--run-dir",
                    str(self.root / f"non-finite-{field}"),
                )
                self.assertNotEqual(0, result.returncode)
                payload = self.payload(result)
                self.assertEqual("INVALID_REQUEST", payload["code"])
                self.assertIn(field, payload["message"])

    def test_new_run_rejects_unstructured_or_forbidden_inputs(self) -> None:
        cases = [
            ("bad-peripheral", {"peripherals": [123]}),
            ("nonvolatile", {"allow_nonvolatile_changes": True}),
        ]
        for name, patch in cases:
            with self.subTest(name=name):
                request = self.request()
                request.update(patch)
                self._write_json(self.request_path, request)
                result = self.run_cli(
                    "new-run",
                    "--profile",
                    str(self.profile_path),
                    "--config",
                    str(self.config_path),
                    "--request",
                    str(self.request_path),
                    "--source",
                    str(self.source_path),
                    "--run-dir",
                    str(self.root / name),
                )
                self.assertNotEqual(0, result.returncode)
                self.assertEqual("INVALID_REQUEST", self.payload(result)["code"])

    def test_doctor_rejects_programmer_probe_identity_mismatch(self) -> None:
        for mode in ("probe-device-mismatch", "probe-serial-mismatch", "probe-voltage-mismatch"):
            with self.subTest(mode=mode):
                self._write_json(self.config_path, self.config(failures={"programmer": mode}))
                result = self.run_cli(
                    "doctor", "--profile", str(self.profile_path), "--config", str(self.config_path)
                )
                self.assertNotEqual(0, result.returncode)
                self.assertEqual("DOCTOR_FAILED", self.payload(result)["code"])

    def test_unapproved_tool_version_is_rejected(self) -> None:
        self._write_json(self.config_path, self.config(failures={"compiler": "unapproved-version"}))
        result = self.run_cli(
            "doctor", "--profile", str(self.profile_path), "--config", str(self.config_path)
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("DOCTOR_FAILED", self.payload(result)["code"])

    def test_stdout_only_adapter_result_is_accepted(self) -> None:
        command = [sys.executable, str(FAKE_ADAPTER), "--stdout-only"]
        self._write_json(self.config_path, self.config_with_command(command))
        result = self.run_cli(
            "doctor", "--profile", str(self.profile_path), "--config", str(self.config_path)
        )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertEqual("READY", self.payload(result)["code"])

    def test_profile_static_checker_blocks_db_with_python_cli_toolchain(self) -> None:
        profile = self.profile()
        profile["spec_root"] = str(SKILL_ROOT / "references" / "spec")
        profile["static_check"] = {
            "toolchain": "python_source_module_cli",
            "strict_warnings": True,
        }
        self._write_json(self.profile_path, profile)
        self.source_path.write_text(
            "; CHIP: HK64S825\n; 用途：验证 DB 工具链阻断\nORG 0x0000\nTABLE0:\n    DB 12H,34H\nEND\n",
            encoding="utf-8",
        )
        run_dir = self.new_run("db-blocker")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertNotEqual(0, loop.returncode)
        payload = self.payload(loop)
        self.assertEqual("STATIC_CHECK_FAILED", payload["code"])
        self.assertIn("HK-TOOLCHAIN-DB-001", json.dumps(payload.get("details", {})))

    def test_close_loop_passes_request_and_profile_snapshots_to_static_checker(self) -> None:
        profile = self.profile()
        profile["spec_root"] = str(self.context_checker_spec_root())
        profile["static_check"] = {
            "toolchain": "company_ide",
            "strict_warnings": False,
        }
        self._write_json(self.profile_path, profile)

        run_dir = self.new_run("context-plumbing")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        self.assertEqual("COMPILE_PASSED", self.payload(loop)["code"])
        evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
        self.assertNotIn("semantic_audits", evidence["gates"]["static"])

    def test_custom_checker_invalid_semantic_audits_are_not_promoted_to_pass(self) -> None:
        valid_gpio_rules = ["HK-GPIO-002", "HK-GPIO-INIT-001"]
        valid_loop_rules = ["HK-SYN-012", "HK-WDT-001", "HK-WDT-002"]
        valid_oled_rules = ["HK-I2C-005", "HK-I2C-006", "HK-OLED-005"]
        cases = {
            "missing_sections": {"timing": []},
            "audited_false_pass": {
                "gpio_contract": {
                    "audited": False,
                    "status": "pass",
                    "rule_ids": valid_gpio_rules,
                    "finding_rule_ids": [],
                },
                "loop_semantics": {
                    "audited": False,
                    "status": "pass",
                    "rule_ids": valid_loop_rules,
                    "finding_rule_ids": [],
                },
                "oled_i2c": {
                    "audited": False,
                    "status": "not_applicable",
                    "rule_ids": valid_oled_rules,
                    "finding_rule_ids": [],
                },
                "timing": [],
            },
            "invalid_structure": {
                "gpio_contract": {
                    "audited": True,
                    "status": "pass",
                    "rule_ids": "HK-GPIO-002",
                    "finding_rule_ids": [],
                },
                "loop_semantics": {
                    "audited": True,
                    "status": "pass",
                    "rule_ids": valid_loop_rules,
                    "finding_rule_ids": [],
                },
                "oled_i2c": {
                    "audited": False,
                    "status": "not_applicable",
                    "rule_ids": valid_oled_rules,
                    "finding_rule_ids": [],
                },
                "timing": [],
            },
        }
        for case_name, audits in cases.items():
            with self.subTest(case_name=case_name):
                profile = self.profile()
                profile["spec_root"] = str(
                    self.context_checker_spec_root(
                        f"context-checker-{case_name}", semantic_audits=audits
                    )
                )
                profile["static_check"] = {
                    "toolchain": "company_ide",
                    "strict_warnings": False,
                }
                self._write_json(self.profile_path, profile)
                run_dir = self.new_run(f"invalid-audit-{case_name}")
                loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
                self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
                evidence = json.loads(
                    (run_dir / "evidence.json").read_text(encoding="utf-8")
                )
                self.assertNotIn("semantic_audits", evidence["gates"]["static"])

    def test_static_checker_payload_must_cross_validate_findings_summary_and_audits(
        self,
    ) -> None:
        valid_audits = {
            "gpio_contract": {
                "audited": False,
                "status": "not_applicable",
                "rule_ids": ["HK-GPIO-002", "HK-GPIO-INIT-001"],
                "finding_rule_ids": [],
            },
            "loop_semantics": {
                "audited": True,
                "status": "pass",
                "rule_ids": ["HK-SYN-012", "HK-WDT-001", "HK-WDT-002"],
                "finding_rule_ids": [],
            },
            "oled_i2c": {
                "audited": False,
                "status": "not_applicable",
                "rule_ids": ["HK-I2C-005", "HK-I2C-006", "HK-OLED-005"],
                "finding_rule_ids": [],
            },
            "timing": [],
        }

        def finding(rule_id: str, severity: str) -> dict:
            return {
                "rule_id": rule_id,
                "severity": severity,
                "file": "candidate.asm",
                "line": 1,
                "evidence": "fixture finding",
                "risk": "fixture risk",
                "required_fix": "fixture fix",
            }

        zero_summary = {
            "blockers": 0,
            "errors": 0,
            "warnings": 0,
            "info": 0,
            "exit_code": 0,
        }
        warning = finding("HK-GPIO-INIT-001", "WARNING")
        warning_summary = {**zero_summary, "warnings": 1}
        cases = {
            "hidden_blocker": {
                "findings": [finding("HK-GPIO-002", "BLOCKER")],
                "summary": zero_summary,
                "semantic_audits": valid_audits,
                "process_exit": 0,
            },
            "hidden_warning": {
                "findings": [warning],
                "summary": zero_summary,
                "semantic_audits": valid_audits,
                "process_exit": 0,
            },
            "invalid_severity": {
                "findings": [finding("HK-GPIO-002", "WARN")],
                "summary": zero_summary,
                "semantic_audits": valid_audits,
                "process_exit": 0,
            },
            "missing_finding_fields": {
                "findings": [{"rule_id": "HK-GPIO-002", "severity": "INFO"}],
                "summary": {**zero_summary, "info": 1},
                "semantic_audits": valid_audits,
                "process_exit": 0,
            },
            "findings_not_array": {
                "findings": {},
                "summary": zero_summary,
                "semantic_audits": valid_audits,
                "process_exit": 0,
            },
            "summary_count_mismatch": {
                "findings": [finding("HK-GOV-003", "INFO")],
                "summary": zero_summary,
                "semantic_audits": valid_audits,
                "process_exit": 0,
            },
            "summary_exit_mismatch": {
                "findings": [],
                "summary": {**zero_summary, "exit_code": 2},
                "semantic_audits": valid_audits,
                "process_exit": 0,
            },
            "process_exit_mismatch": {
                "findings": [],
                "summary": zero_summary,
                "semantic_audits": valid_audits,
                "process_exit": 1,
            },
            "audit_finding_ids_mismatch": {
                "findings": [warning],
                "summary": warning_summary,
                "semantic_audits": valid_audits,
                "process_exit": 0,
            },
            "audit_status_mismatch": {
                "findings": [warning],
                "summary": warning_summary,
                "semantic_audits": {
                    **valid_audits,
                    "gpio_contract": {
                        **valid_audits["gpio_contract"],
                        "audited": True,
                        "status": "pass",
                        "finding_rule_ids": ["HK-GPIO-INIT-001"],
                    },
                },
                "process_exit": 0,
            },
        }
        for case_name, case in cases.items():
            with self.subTest(case_name=case_name):
                profile = self.profile()
                checker_payload = {
                    "schema_version": "1.0.0",
                    "toolchain": "company_ide",
                    "contract_context": {},
                    "files": [],
                    "findings": case["findings"],
                    "summary": case["summary"],
                    "semantic_audits": case["semantic_audits"],
                }
                profile["spec_root"] = str(
                    self.payload_checker_spec_root(
                        f"payload-checker-{case_name}",
                        checker_payload,
                        exit_code=case["process_exit"],
                    )
                )
                profile["static_check"] = {
                    "toolchain": "company_ide",
                    "strict_warnings": False,
                }
                self._write_json(self.profile_path, profile)
                run_dir = self.new_run(f"payload-mismatch-{case_name}")
                loop = self.run_cli("close-loop", "--run-dir", str(run_dir))

                self.assertNotEqual(0, loop.returncode)
                self.assertEqual("STATIC_CHECK_FAILED", self.payload(loop)["code"])
                self.assertFalse((run_dir / "build").exists())

    def test_non_strict_gpio_warning_is_not_reported_as_semantic_pass(self) -> None:
        profile = json.loads(EXAMPLE_PROFILE.read_text(encoding="utf-8-sig"))
        profile["spec_root"] = str(SKILL_ROOT / "references" / "spec")
        profile["static_check"]["strict_warnings"] = False
        self._write_json(self.profile_path, profile)
        request = self.structured_gpio_request()
        request["timing"] = {"precision": "approximate"}
        request["board"] = {"id": "HK64S825-4DIGIT-MIXED-PA-PB-E1"}
        request["memory_limits"] = {"rom_bytes": 2048, "ram_bytes": 128}
        self._write_json(self.request_path, request)
        self.source_path.write_text(BULK_GPIO_WARNING_SOURCE, encoding="utf-8")
        run_dir = self.root / "non-strict-gpio-warning"
        new_run = self.run_cli(
            "new-run",
            "--profile",
            str(self.profile_path),
            "--config",
            str(EXAMPLE_CONFIG),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(0, new_run.returncode, new_run.stderr or new_run.stdout)

        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
        audit = evidence["gates"]["static"]["semantic_audits"]["gpio_contract"]
        self.assertTrue(audit["audited"])
        self.assertEqual("warning", audit["status"])
        self.assertEqual(["HK-GPIO-INIT-001"], audit["finding_rule_ids"])

    def test_full_loop_releases_source_and_evidence(self) -> None:
        run_dir = self.new_run()
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        self.assertEqual("COMPILE_PASSED", self.payload(loop)["code"])
        output = self.root / "released.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
        self.assertEqual(0, release.returncode, release.stderr or release.stdout)
        receipt = self.payload(release)
        self.assertEqual("RELEASED", receipt["code"])
        self.assertEqual(self.source_path.read_text(encoding="utf-8"), output.read_text(encoding="utf-8"))
        self.assertTrue((run_dir / "evidence.json").is_file())
        self.assertIn("source_sha256", receipt)
        self.assertIn("artifact_sha256", receipt)

    def test_compile_pass_releases_without_hardware_adapters(self) -> None:
        self._write_json(self.config_path, self.compile_only_config())
        run_dir = self.new_run("compile-only")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        self.assertEqual("COMPILE_PASSED", self.payload(loop)["code"])
        output = self.root / "compile-only-released.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
        self.assertEqual(0, release.returncode, release.stderr or release.stdout)
        self.assertEqual("RELEASED", self.payload(release)["code"])
        self.assertEqual(self.source_path.read_text(encoding="utf-8"), output.read_text(encoding="utf-8"))
        run_state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(0, run_state["flash_attempts"])

    def test_compile_only_profile_does_not_require_hardware_fields(self) -> None:
        profile = self.profile()
        profile.pop("expected_device_id")
        profile.pop("max_flash_attempts")
        profile["approved_tool_versions"] = {"compiler": ["sim-1.0"]}
        self._write_json(self.profile_path, profile)
        self._write_json(self.config_path, self.compile_only_config())

        run_dir = self.new_run("minimal-compile-only-profile")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        self.assertEqual("COMPILE_PASSED", self.payload(loop)["code"])
        run_state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(0, run_state["max_flash_attempts"])

    def test_bundled_canonical_config_uses_builtin_compiler_without_local_toolchain_config(self) -> None:
        request = self.request()
        request["board"] = {"id": "HK64S825-4DIGIT-MIXED-PA-PB-E1"}
        request["memory_limits"] = {"rom_bytes": 2048, "ram_bytes": 128}
        self._write_json(self.request_path, request)
        self.source_path.write_text(
            "; CHIP: HK64S825\n"
            "; 目的：验证内置编译器默认可用\n"
            "ORG 0x0000\n"
            "START:\n"
            "    NOP\n"
            "    CLRWDT\n"
            "    JMP START\n"
            "END\n",
            encoding="utf-8",
        )

        profile_text = CANONICAL_PROFILE.read_text(encoding="utf-8-sig")
        config_text = CANONICAL_CONFIG.read_text(encoding="utf-8-sig")
        self.assertNotIn("REPLACE_WITH", profile_text + config_text)
        self.assertEqual("ready", json.loads(profile_text)["status"])

        doctor = self.run_cli("doctor", "--profile", str(CANONICAL_PROFILE), "--config", str(CANONICAL_CONFIG))
        self.assertEqual(0, doctor.returncode, doctor.stderr or doctor.stdout)
        doctor_payload = self.payload(doctor)
        self.assertEqual("READY", doctor_payload["code"])
        self.assertEqual("builtin-hk64s825-assembler-2", doctor_payload["tools"]["compiler"])

        run_dir = self.root / "builtin-example"
        new_run = self.run_cli(
            "new-run",
            "--profile",
            str(CANONICAL_PROFILE),
            "--config",
            str(CANONICAL_CONFIG),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
        )
        self.assertEqual(0, new_run.returncode, new_run.stderr or new_run.stdout)
        self.assertEqual("RUN_CREATED", self.payload(new_run)["code"])

        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        self.assertEqual("COMPILE_PASSED", self.payload(loop)["code"])

        output = self.root / "builtin-released.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
        self.assertEqual(0, release.returncode, release.stderr or release.stdout)
        self.assertEqual("RELEASED", self.payload(release)["code"])
        self.assertEqual(self.source_path.read_text(encoding="utf-8"), output.read_text(encoding="utf-8"))
        self.assertTrue((run_dir / "build" / "firmware.hex").is_file())
        self.assertTrue((run_dir / "build" / "firmware.bin").is_file())
        self.assertTrue((run_dir / "build" / "firmware.map").is_file())
        evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual("hk64s825-builtin-assembler", evidence["gates"]["compile"]["toolchain"])
        self.assertEqual(
            "not_applicable",
            evidence["gates"]["static"]["semantic_audits"]["gpio_contract"]["status"],
        )
        self.assertFalse(
            evidence["gates"]["static"]["semantic_audits"]["gpio_contract"]["audited"]
        )

    def test_documented_relative_run_dir_works_with_builtin_compiler(self) -> None:
        request = self.minimal_non_gpio_request()
        request["board"] = {"id": "HK64S825-4DIGIT-MIXED-PA-PB-E1"}
        self._write_json(self.request_path, request)
        self.source_path.write_text(
            "; CHIP: HK64S825\n"
            "; 用途：验证相对运行目录可用于内置编译\n"
            "ORG 0x0000\n"
            "START:\n"
            "    NOP\n"
            "    CLRWDT\n"
            "    JMP START\n"
            "END\n",
            encoding="utf-8",
        )
        run_dir = self.root / "relative-run"
        relative_run_dir = os.path.relpath(run_dir, SKILL_ROOT)
        new_run = self.run_cli(
            "new-run",
            "--profile",
            str(CANONICAL_PROFILE),
            "--config",
            str(CANONICAL_CONFIG),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            relative_run_dir,
        )
        self.assertEqual(0, new_run.returncode, new_run.stderr or new_run.stdout)
        self.assertEqual("RUN_CREATED", self.payload(new_run)["code"])

        loop = self.run_cli("close-loop", "--run-dir", relative_run_dir)
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        self.assertEqual("COMPILE_PASSED", self.payload(loop)["code"])
        output = self.root / "relative-run-release.asm"
        release = self.run_cli("release", "--run-dir", relative_run_dir, "--output", str(output))
        self.assertEqual(0, release.returncode, release.stderr or release.stdout)
        self.assertEqual("RELEASED", self.payload(release)["code"])
        self.assertTrue((run_dir / "build" / "firmware.hex").is_file())

    def test_problem_led_source_static_failure_blocks_compile_and_release(self) -> None:
        candidate = PROBLEM_LED_SOURCE
        run_dir = self.new_bundled_run(candidate, "problem-led")

        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertNotEqual(0, loop.returncode)
        loop_payload = self.payload(loop)
        self.assertEqual("STATIC_CHECK_FAILED", loop_payload["code"])
        finding_rule_ids = {
            finding["rule_id"]
            for finding in loop_payload["details"]["spec_static_check"]["findings"]
        }
        self.assertTrue(
            {"HK-GPIO-002", "HK-SYN-012", "HK-SYN-013", "HK-WDT-002"}
            <= finding_rule_ids
        )
        self.assertFalse((run_dir / "build").exists())
        self.assertFalse((run_dir / "build" / "firmware.hex").exists())
        self.assertNotIn(candidate, loop.stdout)
        for source_line in ("LED_MASK        EQU 29H", "DELAY_INNER_LOOP:"):
            self.assertNotIn(source_line, loop.stdout)

        evidence_text = (run_dir / "evidence.json").read_text(encoding="utf-8")
        self.assertNotIn(candidate, evidence_text)
        for source_line in ("LED_MASK        EQU 29H", "DELAY_INNER_LOOP:"):
            self.assertNotIn(source_line, evidence_text)
        failure_evidence = json.loads(evidence_text)
        self.assertEqual("STATIC_CHECK_FAILED", failure_evidence["failure"]["code"])

        output = self.root / "problem-led-released.asm"
        release = self.run_cli(
            "release", "--run-dir", str(run_dir), "--output", str(output)
        )
        self.assertNotEqual(0, release.returncode)
        self.assertEqual("RELEASE_BLOCKED", self.payload(release)["code"])
        self.assertFalse(output.exists())
        self.assertNotIn(candidate, release.stdout)
        for source_line in ("LED_MASK        EQU 29H", "DELAY_INNER_LOOP:"):
            self.assertNotIn(source_line, release.stdout)

    def test_static_failure_diagnostics_redact_checker_source_evidence(self) -> None:
        candidate = (
            "; CHIP: HK64S825\n"
            "; 用途：验证失败诊断不会泄露候选源码标号\n"
            "ORG 000H\n"
            "START:\n"
            "    MOV A,#03H\n"
            "    MOV 80H,A\n"
            "SECRET_LOOP_LABEL:\n"
            "    CLRWDT\n"
            "    DECSZ 80H\n"
            "    JMP SECRET_LOOP_LABEL\n"
            "END\n"
        )
        run_dir = self.new_bundled_run(candidate, "redacted-static-failure")

        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))

        self.assertNotEqual(0, loop.returncode)
        payload = self.payload(loop)
        self.assertEqual("STATIC_CHECK_FAILED", payload["code"])
        self.assertNotIn("SECRET_LOOP_LABEL", loop.stdout)
        self.assertNotIn("DECSZ 80H", loop.stdout)
        details_text = json.dumps(payload.get("details", {}), ensure_ascii=False)
        self.assertNotIn("evidence", details_text)
        self.assertNotIn("candidate.asm", details_text)
        evidence_text = (run_dir / "evidence.json").read_text(encoding="utf-8")
        self.assertNotIn("SECRET_LOOP_LABEL", evidence_text)
        self.assertNotIn("DECSZ 80H", evidence_text)

    def test_compliant_led_source_compiles_releases_and_hash_binds_semantic_audits(self) -> None:
        doctor = self.run_cli(
            "doctor", "--profile", str(EXAMPLE_PROFILE), "--config", str(EXAMPLE_CONFIG)
        )
        self.assertEqual(0, doctor.returncode, doctor.stderr or doctor.stdout)
        self.assertEqual("READY", self.payload(doctor)["code"])

        run_dir = self.new_bundled_run(COMPLIANT_LED_SOURCE, "compliant-led")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        self.assertEqual("COMPILE_PASSED", self.payload(loop)["code"])

        evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
        audits = evidence["gates"]["static"]["semantic_audits"]
        self.assertEqual(
            set(audits), {"gpio_contract", "loop_semantics", "oled_i2c", "timing"}
        )
        self.assertEqual(audits["gpio_contract"]["status"], "pass")
        self.assertTrue(audits["gpio_contract"]["audited"])
        self.assertIn("HK-GPIO-002", audits["gpio_contract"]["rule_ids"])
        self.assertIn("HK-GPIO-INIT-001", audits["gpio_contract"]["rule_ids"])
        self.assertEqual(audits["loop_semantics"]["status"], "pass")
        self.assertTrue(audits["loop_semantics"]["audited"])
        self.assertEqual(
            set(audits["loop_semantics"]["rule_ids"]),
            {"HK-SYN-012", "HK-WDT-001", "HK-WDT-002"},
        )
        self.assertEqual(audits["oled_i2c"]["status"], "not_applicable")
        self.assertFalse(audits["oled_i2c"]["audited"])
        self.assertEqual(
            set(audits["oled_i2c"]["rule_ids"]),
            {"HK-I2C-005", "HK-I2C-006", "HK-OLED-005"},
        )
        self.assertEqual(len(audits["timing"]), 1)
        self.assertEqual(audits["timing"][0]["status"], "pass")
        self.assertEqual(audits["timing"][0]["actual_us"], 502_010.5)
        self.assertEqual(audits["timing"][0]["error_percent"], 0.4021)

        output = self.root / "compliant-led-released.asm"
        release = self.run_cli(
            "release", "--run-dir", str(run_dir), "--output", str(output)
        )
        self.assertEqual(0, release.returncode, release.stderr or release.stdout)
        receipt = self.payload(release)
        self.assertEqual("RELEASED", receipt["code"])
        expected_hash = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        self.assertEqual(receipt["source_sha256"], expected_hash)
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), expected_hash)

    def test_builtin_compiler_matches_all_instruction_probe_words(self) -> None:
        reference = json.loads(INSTRUCTION_REFERENCE.read_text(encoding="utf-8-sig"))
        for variant in reference["variants"]:
            probe = variant["compile_probe"]
            instruction = probe["source_instruction"]
            expected = int(probe["expected_word"], 16)
            with self.subTest(variant=variant["id"], instruction=instruction):
                case_dir = self.root / f"probe-{variant['id']}"
                case_dir.mkdir()
                source = case_dir / "case.asm"
                if "TARGET" in instruction:
                    source.write_text(
                        "; CHIP: HK64S825\n"
                        "ORG 0x0000\n"
                        f"    {instruction}\n"
                        "ORG 0x03FF\n"
                        "TARGET:\n"
                        "    NOP\n"
                        "END\n",
                        encoding="utf-8",
                    )
                else:
                    source.write_text(
                        "; CHIP: HK64S825\n"
                        "ORG 0x0000\n"
                        f"    {instruction}\n"
                        "END\n",
                        encoding="utf-8",
                    )
                input_path = case_dir / "input.json"
                output_path = case_dir / "output.json"
                artifact = case_dir / "firmware.hex"
                self._write_json(
                    input_path,
                    {
                        "schema_version": 1,
                        "chip": "HK64S825",
                        "source_path": str(source),
                        "artifact_path": str(artifact),
                    },
                )
                result = subprocess.run(
                    [
                        sys.executable,
                        str(BUILTIN_COMPILER),
                        "compiler",
                        "run",
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                    ],
                    cwd=SKILL_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr or output_path.read_text(encoding="utf-8"))
                firmware = artifact.with_suffix(".bin").read_bytes()
                actual = firmware[0] | (firmware[1] << 8)
                self.assertEqual(expected, actual)

    def test_builtin_compiler_resolves_equ_symbols_in_immediate_operands(self) -> None:
        case_dir = self.root / "equ-immediates"
        case_dir.mkdir()
        source = case_dir / "case.asm"
        source.write_text(
            "; CHIP: HK64S825\n"
            "MOV_VALUE EQU 12H\n"
            "AND_VALUE EQU 34H\n"
            "OR_VALUE EQU 56H\n"
            "ORG 000H\n"
            "    MOV A,#MOV_VALUE\n"
            "    AND A,#AND_VALUE\n"
            "    OR A,#OR_VALUE\n"
            "END\n",
            encoding="utf-8",
        )
        input_path = case_dir / "input.json"
        output_path = case_dir / "output.json"
        artifact = case_dir / "firmware.hex"
        self._write_json(
            input_path,
            {
                "schema_version": 1,
                "chip": "HK64S825",
                "source_path": str(source),
                "artifact_path": str(artifact),
            },
        )

        result = subprocess.run(
            [
                sys.executable,
                str(BUILTIN_COMPILER),
                "compiler",
                "run",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ],
            cwd=SKILL_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, output_path.read_text(encoding="utf-8"))
        firmware = artifact.with_suffix(".bin").read_bytes()
        words = [
            firmware[index] | (firmware[index + 1] << 8)
            for index in range(0, len(firmware), 2)
        ]
        self.assertEqual([0x7212, 0x3034, 0x4056], words)

    def test_builtin_compiler_rejects_hash_equ_outside_immediate_operands(self) -> None:
        cases = {
            "jump": "BASE EQU 000H\nORG 000H\nSTART:\n    JMP #START\nEND\n",
            "bit": "BITNO EQU 0\nORG 000H\n    BCLR PA_PIO,#BITNO\nEND\n",
            "org": "BASE EQU 000H\nORG #BASE\n    NOP\nEND\n",
            "db": "VAL EQU 12H\nORG 000H\n    DB #VAL\nEND\n",
            "include": "VAL EQU 12H\nINCLUDE #VAL\nORG 000H\n    NOP\nEND\n",
            "end": "ORG 000H\n    NOP\nEND #1\n",
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                case_dir = self.root / f"hash-equ-{name}"
                case_dir.mkdir()
                source = case_dir / "case.asm"
                source.write_text("; CHIP: HK64S825\n" + body, encoding="utf-8")
                input_path = case_dir / "input.json"
                output_path = case_dir / "output.json"
                self._write_json(
                    input_path,
                    {
                        "schema_version": 1,
                        "chip": "HK64S825",
                        "source_path": str(source),
                        "artifact_path": str(case_dir / "firmware.hex"),
                    },
                )

                result = subprocess.run(
                    [
                        sys.executable,
                        str(BUILTIN_COMPILER),
                        "compiler",
                        "run",
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                    ],
                    cwd=SKILL_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertNotEqual(0, result.returncode, output_path.read_text(encoding="utf-8"))
                self.assertEqual("fail", json.loads(output_path.read_text(encoding="utf-8"))["status"])

    def test_builtin_compiler_rejects_empty_operands(self) -> None:
        cases = {
            "mov-double-comma": "ORG 000H\n    MOV A,,#12H\nEND\n",
            "mov-trailing-comma": "ORG 000H\n    MOV A,#12H,\nEND\n",
            "bit-double-comma": "ORG 000H\n    BCLR PA_PIO,,0\nEND\n",
            "db-double-comma": "ORG 000H\n    DB 12H,,34H\nEND\n",
            "db-trailing-comma": "ORG 000H\n    DB 12H,\nEND\n",
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                case_dir = self.root / f"empty-operand-{name}"
                case_dir.mkdir()
                source = case_dir / "case.asm"
                source.write_text("; CHIP: HK64S825\n" + body, encoding="utf-8")
                input_path = case_dir / "input.json"
                output_path = case_dir / "output.json"
                artifact_path = case_dir / "firmware.hex"
                self._write_json(
                    input_path,
                    {
                        "schema_version": 1,
                        "chip": "HK64S825",
                        "source_path": str(source),
                        "artifact_path": str(artifact_path),
                    },
                )

                result = subprocess.run(
                    [
                        sys.executable,
                        str(BUILTIN_COMPILER),
                        "compiler",
                        "run",
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                    ],
                    cwd=SKILL_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertNotEqual(0, result.returncode, output_path.read_text(encoding="utf-8"))
                self.assertEqual("fail", json.loads(output_path.read_text(encoding="utf-8"))["status"])
                self.assertFalse(artifact_path.exists())

    def test_builtin_compiler_rejects_duplicate_symbols(self) -> None:
        cases = {
            "duplicate-equ": "COUNT EQU 01H\nCOUNT EQU 02H\nORG 000H\n    NOP\nEND\n",
            "label-then-equ": (
                "ORG 000H\nTARGET:\n    NOP\nTARGET EQU 003H\n    JMP TARGET\nEND\n"
            ),
            "equ-register-name": "PA_PIO EQU 01H\nORG 000H\n    NOP\nEND\n",
            "label-register-name": "ORG 000H\nPA_PIO:\n    NOP\nEND\n",
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                case_dir = self.root / f"duplicate-symbol-{name}"
                case_dir.mkdir()
                source = case_dir / "case.asm"
                source.write_text("; CHIP: HK64S825\n" + body, encoding="utf-8")
                input_path = case_dir / "input.json"
                output_path = case_dir / "output.json"
                artifact_path = case_dir / "firmware.hex"
                self._write_json(
                    input_path,
                    {
                        "schema_version": 1,
                        "chip": "HK64S825",
                        "source_path": str(source),
                        "artifact_path": str(artifact_path),
                    },
                )

                result = subprocess.run(
                    [
                        sys.executable,
                        str(BUILTIN_COMPILER),
                        "compiler",
                        "run",
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                    ],
                    cwd=SKILL_ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertNotEqual(0, result.returncode, output_path.read_text(encoding="utf-8"))
                self.assertEqual("fail", json.loads(output_path.read_text(encoding="utf-8"))["status"])
                self.assertFalse(artifact_path.exists())

    def test_english_explanatory_comment_blocks_compile_and_release(self) -> None:
        self.source_path.write_text(
            "; CHIP: HK64S825\n"
            "; Initialize the output before entering the loop.\n"
            "ORG 000H\nSTART:\n    NOP\n    JMP START\nEND\n",
            encoding="utf-8",
        )
        candidate = self.source_path.read_text(encoding="utf-8")
        run_dir = self.new_run("english-comment")

        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))

        self.assertNotEqual(0, loop.returncode)
        self.assertEqual("STATIC_CHECK_FAILED", self.payload(loop)["code"])
        self.assertNotIn(candidate, loop.stdout)
        self.assertFalse((run_dir / "build").exists())
        output = self.root / "english-comment.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
        self.assertNotEqual(0, release.returncode)
        self.assertEqual("RELEASE_BLOCKED", self.payload(release)["code"])
        self.assertFalse(output.exists())
        self.assertNotIn(candidate, release.stdout)

    def test_comment_gate_rejects_short_uppercase_and_mixed_english_explanations(self) -> None:
        comments = {
            "single-uppercase": "INITIALIZE",
            "trailing-underscore": "INITIALIZE_",
            "snake-case-english": "INITIALIZE_OUTPUT",
            "single-mixed": "说明 Initialize",
            "short": "Initialize output",
            "uppercase": "INITIALIZE THE OUTPUT BEFORE ENTERING THE LOOP",
            "mixed": "说明 Initialize the output before entering the loop",
        }
        for name, comment in comments.items():
            with self.subTest(name=name):
                self.source_path.write_text(
                    "; CHIP: HK64S825\n"
                    f"; {comment}\n"
                    "ORG 000H\nSTART:\n    NOP\n    JMP START\nEND\n",
                    encoding="utf-8",
                )
                run_dir = self.new_run(f"comment-{name}")

                loop = self.run_cli("close-loop", "--run-dir", str(run_dir))

                self.assertNotEqual(0, loop.returncode)
                payload = self.payload(loop)
                self.assertEqual("STATIC_CHECK_FAILED", payload["code"])
                self.assertEqual(
                    "chinese_explanatory_comment",
                    payload["details"]["comment_language"]["issues"][0]["rule"],
                )

    def test_comment_gate_rejects_english_sentence_made_from_source_labels(self) -> None:
        self.source_path.write_text(
            "; CHIP: HK64S825\n"
            "; Enter main loop\n"
            "ORG 000H\nENTER:\nMAIN:\nLOOP:\n    NOP\nEND\n",
            encoding="utf-8",
        )
        run_dir = self.new_run("comment-source-label-sentence")

        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))

        self.assertNotEqual(0, loop.returncode)
        payload = self.payload(loop)
        self.assertEqual("STATIC_CHECK_FAILED", payload["code"])
        self.assertEqual(
            "chinese_explanatory_comment",
            payload["details"]["comment_language"]["issues"][0]["rule"],
        )

    def test_chinese_comment_allows_bundled_technical_identifiers(self) -> None:
        self.source_path.write_text(
            "; CHIP: HK64S825\n"
            "; 初始化 SSD1306，并按 REG825.INC 设置 PA_POD\n"
            "ORG 000H\n    NOP\nEND\n",
            encoding="utf-8",
        )
        run_dir = self.new_run("comment-bundled-technical-identifiers")

        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))

        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        output = self.root / "comment-bundled-technical-identifiers.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
        self.assertEqual(0, release.returncode, release.stderr or release.stdout)
        self.assertEqual(self.source_path.read_bytes(), output.read_bytes())

    def test_release_rechecks_english_explanatory_comment_gate(self) -> None:
        self.source_path.write_text(
            "; CHIP: HK64S825\n; 初始源码使用中文说明\nORG 000H\nSTART:\n    NOP\n    JMP START\nEND\n",
            encoding="utf-8",
        )
        run_dir = self.new_run("release-comment-recheck")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)

        run_source = run_dir / "src" / "candidate.asm"
        run_source.write_text(
            "; CHIP: HK64S825\n; Initialize the output before entering the loop.\nORG 000H\nSTART:\n    NOP\n    JMP START\nEND\n",
            encoding="utf-8",
        )
        source_hash = hashlib.sha256(run_source.read_bytes()).hexdigest()
        evidence_path = run_dir / "evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["source_sha256"] = source_hash
        evidence["gates"]["compile"]["source_sha256"] = source_hash
        self._write_json(evidence_path, evidence)
        run_path = run_dir / "run.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["source_sha256"] = source_hash
        run["verified_source_sha256"] = source_hash
        run["evidence_sha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        self._write_json(run_path, run)

        output = self.root / "release-comment-recheck.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))

        self.assertNotEqual(0, release.returncode)
        self.assertEqual("RELEASE_BLOCKED", self.payload(release)["code"])
        self.assertFalse(output.exists())

    def test_chinese_explanatory_comment_passes_release_gate(self) -> None:
        self.source_path.write_text(
            "; CHIP: HK64S825\n"
            "; 进入主循环前先完成初始化\n"
            "ORG 000H\nSTART:\n    NOP\n    JMP START\nEND\n",
            encoding="utf-8",
        )
        run_dir = self.new_run("chinese-comment")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        output = self.root / "chinese-comment.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
        self.assertEqual(0, release.returncode, release.stderr or release.stdout)
        self.assertEqual(self.source_path.read_bytes(), output.read_bytes())

    def test_minimal_source_without_comments_passes_release_gate(self) -> None:
        profile = self.profile()
        profile["asm_rules"]["required_patterns"] = ["ORG", "END"]
        self._write_json(self.profile_path, profile)
        self.source_path.write_text("ORG 000H\n    NOP\nEND\n", encoding="utf-8")
        run_dir = self.new_run("no-comments")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        output = self.root / "no-comments.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
        self.assertEqual(0, release.returncode, release.stderr or release.stdout)
        self.assertEqual(self.source_path.read_bytes(), output.read_bytes())

    def test_compile_failure_blocks_release_without_source_leakage(self) -> None:
        candidate = self.source_path.read_text(encoding="utf-8")
        self._write_json(self.config_path, self.config(failures={"compiler": "fail"}))
        run_dir = self.new_run("fail-compiler")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertNotEqual(0, loop.returncode)
        self.assertNotIn(candidate, loop.stdout)
        output = self.root / "leaked-compiler.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
        self.assertNotEqual(0, release.returncode)
        self.assertEqual("RELEASE_BLOCKED", self.payload(release)["code"])
        self.assertFalse(output.exists())
        self.assertNotIn(candidate, release.stdout)

    def test_hardware_run_failures_are_deferred_after_compile_release(self) -> None:
        for role in ("programmer", "verifier"):
            with self.subTest(role=role):
                self._write_json(self.config_path, self.config(failures={role: "fail"}))
                run_dir = self.new_run(f"defer-{role}")
                loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
                self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
                self.assertEqual("COMPILE_PASSED", self.payload(loop)["code"])
                output = self.root / f"released-with-{role}-failure-deferred.asm"
                release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
                self.assertEqual(0, release.returncode, release.stderr or release.stdout)
                self.assertEqual("RELEASED", self.payload(release)["code"])

    def test_failed_loop_writes_failure_evidence_without_source_leakage(self) -> None:
        candidate = self.source_path.read_text(encoding="utf-8")
        self._write_json(self.config_path, self.config(failures={"compiler": "fail"}))
        run_dir = self.new_run("failure-evidence")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertNotEqual(0, loop.returncode)
        evidence_path = run_dir / "evidence.json"
        self.assertTrue(evidence_path.is_file())
        evidence_text = evidence_path.read_text(encoding="utf-8")
        self.assertNotIn(candidate, evidence_text)
        evidence = json.loads(evidence_text)
        self.assertEqual("FAILED", evidence["state"])
        self.assertEqual("COMPILE_FAILED", evidence["failure"]["code"])

    def test_allowed_compile_warnings_pass_and_unapproved_warnings_fail(self) -> None:
        profile = self.profile()
        profile["allowed_warnings"] = ["HK-WARN-ALLOWED"]
        self._write_json(self.profile_path, profile)

        self._write_json(self.config_path, self.config(failures={"compiler": "allowed-warning"}))
        allowed_run = self.new_run("allowed-warning")
        allowed_loop = self.run_cli("close-loop", "--run-dir", str(allowed_run))
        self.assertEqual(0, allowed_loop.returncode, allowed_loop.stderr or allowed_loop.stdout)

        self._write_json(self.config_path, self.config(failures={"compiler": "unapproved-warning"}))
        unapproved_run = self.new_run("unapproved-warning")
        unapproved_loop = self.run_cli("close-loop", "--run-dir", str(unapproved_run))
        self.assertNotEqual(0, unapproved_loop.returncode)
        self.assertEqual("COMPILE_FAILED", self.payload(unapproved_loop)["code"])

    def test_program_readback_mismatch_is_deferred_after_compile_release(self) -> None:
        self._write_json(self.config_path, self.config(failures={"programmer": "readback-mismatch"}))
        run_dir = self.new_run("readback-mismatch")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        self.assertEqual("COMPILE_PASSED", self.payload(loop)["code"])
        output = self.root / "readback-deferred.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
        self.assertEqual(0, release.returncode, release.stderr or release.stdout)
        self.assertEqual("RELEASED", self.payload(release)["code"])

    def test_verifier_contract_checks_are_deferred_after_compile_release(self) -> None:
        for mode in ("contract-mismatch", "missing-tests"):
            with self.subTest(mode=mode):
                self._write_json(self.config_path, self.config(failures={"verifier": mode}))
                run_dir = self.new_run(f"verify-{mode}")
                loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
                self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
                self.assertEqual("COMPILE_PASSED", self.payload(loop)["code"])
                output = self.root / f"verify-{mode}-deferred.asm"
                release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
                self.assertEqual(0, release.returncode, release.stderr or release.stdout)
                self.assertEqual("RELEASED", self.payload(release)["code"])

    def test_release_detects_evidence_tampering(self) -> None:
        run_dir = self.new_run()
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        evidence_path = run_dir / "evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["device_id"] = "TAMPERED"
        self._write_json(evidence_path, evidence)
        output = self.root / "tampered.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
        self.assertNotEqual(0, release.returncode)
        self.assertEqual("RELEASE_BLOCKED", self.payload(release)["code"])
        self.assertFalse(output.exists())

    def test_release_detects_declared_bin_and_map_tampering(self) -> None:
        for filename in ("firmware.bin", "firmware.map"):
            with self.subTest(filename=filename):
                run_dir = self.new_bundled_run(
                    COMPLIANT_LED_SOURCE,
                    f"artifact-tamper-{filename.replace('.', '-')}",
                )
                loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
                self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
                (run_dir / "build" / filename).write_bytes(b"TAMPERED")
                output = self.root / f"blocked-{filename}.asm"

                release = self.run_cli(
                    "release", "--run-dir", str(run_dir), "--output", str(output)
                )

                self.assertNotEqual(0, release.returncode)
                self.assertEqual("RELEASE_BLOCKED", self.payload(release)["code"])
                self.assertFalse(output.exists())

    def test_release_detects_request_profile_and_config_snapshot_tampering(self) -> None:
        cases = {
            "request.json": lambda payload: payload["timing"]["delay_targets"][0].update(
                {"target_us": 100_000}
            ),
            "profile.json": lambda payload: payload.update({"allowed_warnings": ["NEW_WARNING"]}),
            "config.json": lambda payload: payload.update({"simulate": {"changed": True}}),
        }
        for filename, mutate in cases.items():
            with self.subTest(filename=filename):
                run_dir = self.new_bundled_run(
                    COMPLIANT_LED_SOURCE,
                    f"snapshot-tamper-{filename.replace('.', '-')}",
                )
                loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
                self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
                snapshot_path = run_dir / filename
                payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
                mutate(payload)
                self._write_json(snapshot_path, payload)
                output = self.root / f"blocked-{filename}.asm"

                release = self.run_cli(
                    "release", "--run-dir", str(run_dir), "--output", str(output)
                )

                self.assertNotEqual(0, release.returncode)
                self.assertEqual("RELEASE_BLOCKED", self.payload(release)["code"])
                self.assertFalse(output.exists())

    def test_release_rejects_outputs_inside_run_directory(self) -> None:
        for relative in ("run.json", "released.asm"):
            with self.subTest(relative=relative):
                run_dir = self.new_run(f"release-output-collision-{relative.replace('.', '-')}")
                loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
                self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
                output = run_dir / relative
                original = output.read_bytes() if output.exists() else None

                release = self.run_cli(
                    "release", "--run-dir", str(run_dir), "--output", str(output)
                )

                self.assertNotEqual(0, release.returncode)
                self.assertEqual("RELEASE_BLOCKED", self.payload(release)["code"])
                if original is None:
                    self.assertFalse(output.exists())
                else:
                    self.assertEqual(original, output.read_bytes())

    def test_source_change_after_verification_blocks_release(self) -> None:
        run_dir = self.new_run()
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        run_source = run_dir / "src" / "candidate.asm"
        run_source.write_text(run_source.read_text(encoding="utf-8") + "; changed\n", encoding="utf-8")
        output = self.root / "changed.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
        self.assertNotEqual(0, release.returncode)
        self.assertEqual("SOURCE_CHANGED", self.payload(release)["code"])
        self.assertFalse(output.exists())

    def test_string_adapter_command_is_rejected_without_shell_execution(self) -> None:
        sentinel = self.root / "command-injection.txt"
        config = self.config()
        config["adapters"]["compiler"]["command"] = (
            f'ignored; echo injected > "{sentinel}"'
        )
        self._write_json(self.config_path, config)
        result = self.run_cli(
            "doctor", "--profile", str(self.profile_path), "--config", str(self.config_path)
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("INVALID_CONFIG", self.payload(result)["code"])
        self.assertFalse(sentinel.exists())

    def test_placeholder_compiler_adapter_is_rejected_before_probe(self) -> None:
        config = self.compile_only_config()
        config["adapters"]["compiler"]["command"] = [
            "python",
            "REPLACE_WITH_EXPLICIT_COMPILER_ADAPTER.py",
        ]
        self._write_json(self.config_path, config)
        result = self.run_cli(
            "doctor", "--profile", str(self.profile_path), "--config", str(self.config_path)
        )
        self.assertNotEqual(0, result.returncode)
        payload = self.payload(result)
        self.assertEqual("INVALID_CONFIG", payload["code"])
        self.assertIn("placeholder", payload["message"])

    def test_portable_skill_path_tokens_are_expanded_for_adapter_commands(self) -> None:
        profile = self.profile()
        profile["approved_tool_versions"] = {"compiler": ["builtin-hk64s825-assembler-2"]}
        profile["static_check"] = {
            "toolchain": "builtin_compiler",
            "strict_warnings": True,
        }
        self._write_json(self.profile_path, profile)
        self._write_json(
            self.config_path,
            {
                "schema_version": 1,
                "board_id": "HK64S825-SIM-BOARD",
                "simulate": {},
                "adapters": {
                    "compiler": {
                        "command": [
                            "$PYTHON",
                            "$SKILL_ROOT/scripts/builtin_compiler.py",
                        ]
                    },
                },
            },
        )
        self.source_path.write_text(
            "; CHIP: HK64S825\n"
            "; 目的：验证可移植路径占位符\n"
            "ORG 0x0000\n"
            "START:\n"
            "    NOP\n"
            "    CLRWDT\n"
            "    JMP START\n"
            "END\n",
            encoding="utf-8",
        )
        doctor = self.run_cli("doctor", "--profile", str(self.profile_path), "--config", str(self.config_path))
        self.assertEqual(0, doctor.returncode, doctor.stderr or doctor.stdout)
        self.assertEqual("READY", self.payload(doctor)["code"])

    def test_compiler_adapter_wraps_asmc_cli_for_full_loop(self) -> None:
        profile = self.profile()
        profile["approved_tool_versions"]["compiler"] = ["fixture-compiler-1"]
        self._write_json(self.profile_path, profile)
        self._write_json(
            self.config_path,
            {
                "schema_version": 1,
                "board_id": "HK64S825-SIM-BOARD",
                "simulate": {},
                "adapters": {
                    "compiler": {"command": self.compiler_adapter_command()},
                },
            },
        )

        doctor = self.run_cli("doctor", "--profile", str(self.profile_path), "--config", str(self.config_path))
        self.assertEqual(0, doctor.returncode, doctor.stderr or doctor.stdout)
        self.assertEqual("READY", self.payload(doctor)["code"])

        run_dir = self.new_run("real-compiler-adapter")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        self.assertEqual("COMPILE_PASSED", self.payload(loop)["code"])
        self.assertTrue((run_dir / "build" / "firmware.hex").is_file())
        self.assertTrue((run_dir / "build" / "firmware.bin").is_file())
        self.assertTrue((run_dir / "build" / "firmware.map").is_file())
        evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual("fixture-compiler-1", evidence["gates"]["compile"]["tool_version"])
        self.assertEqual("hk64s8x-cli asmc source-module", evidence["gates"]["compile"]["toolchain"])

    def test_compiler_adapter_failure_blocks_release_without_source_leakage(self) -> None:
        profile = self.profile()
        profile["approved_tool_versions"]["compiler"] = ["fixture-compiler-1"]
        self._write_json(self.profile_path, profile)
        self._write_json(
            self.config_path,
            {
                "schema_version": 1,
                "board_id": "HK64S825-SIM-BOARD",
                "simulate": {},
                "adapters": {
                    "compiler": {"command": self.compiler_adapter_command()},
                },
            },
        )
        self.source_path.write_text(
            "; CHIP: HK64S825\n; 用途：验证编译失败关闭\nORG 0x0000\nFORCE_ERROR:\nNOP\nEND\n",
            encoding="utf-8",
        )
        candidate = self.source_path.read_text(encoding="utf-8")

        run_dir = self.new_run("real-compiler-adapter-failure")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertNotEqual(0, loop.returncode)
        self.assertEqual("COMPILE_FAILED", self.payload(loop)["code"])
        self.assertNotIn(candidate, loop.stdout)
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(self.root / "blocked.asm"))
        self.assertNotEqual(0, release.returncode)
        self.assertEqual("RELEASE_BLOCKED", self.payload(release)["code"])

    def test_compile_gate_does_not_increment_flash_attempts(self) -> None:
        self._write_json(self.config_path, self.config(failures={"programmer": "fail"}))
        run_dir = self.new_run()
        for _ in range(3):
            result = self.run_cli("close-loop", "--run-dir", str(run_dir))
            self.assertEqual(0, result.returncode, result.stderr or result.stdout)
            self.assertEqual("COMPILE_PASSED", self.payload(result)["code"])
            run_state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(0, run_state["flash_attempts"])

    def test_lint_aggregates_comment_and_profile_findings_without_run(self) -> None:
        self.source_path.write_text(
            "; English prose is rejected\nORG 0x0000\nSTART:\nNOP\nEND\n",
            encoding="utf-8",
        )

        result = self.run_cli(
            "lint",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
        )

        self.assertEqual(2, result.returncode)
        payload = self.payload(result)
        self.assertEqual("STATIC_CHECK_FAILED", payload["code"])
        details = payload["details"]
        self.assertEqual("fail", details["comment_language"]["status"])
        self.assertTrue(details["comment_language"]["issues"][0]["tokens"])
        self.assertTrue(details["profile_checks"])
        self.assertFalse(any(self.root.glob("hk8asm-lint-*")))

    def test_quick_release_runs_the_full_compile_release_workflow(self) -> None:
        run_dir = self.root / "quick-release-run"
        output = self.root / "quick-release-verified.asm"

        result = self.run_cli(
            "quick-release",
            "--profile",
            str(self.profile_path),
            "--config",
            str(self.config_path),
            "--request",
            str(self.request_path),
            "--source",
            str(self.source_path),
            "--run-dir",
            str(run_dir),
            "--output",
            str(output),
        )

        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        payload = self.payload(result)
        self.assertEqual("RELEASED", payload["code"])
        self.assertEqual("quick-release", payload["workflow"])
        evidence_path = run_dir / "evidence.json"
        self.assertEqual(
            hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
            payload["evidence_sha256"],
        )
        self.assertEqual("sim-1.0", payload["compiler"]["tool_version"])
        self.assertEqual([], payload["warnings"])
        self.assertIsInstance(payload["artifacts"], dict)
        self.assertIsInstance(payload["metrics"], dict)
        self.assertIsInstance(payload["static_summary"], dict)
        self.assertIsInstance(payload["timing_audit"], list)
        self.assertTrue(output.is_file())
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual("RELEASED", run["state"])

    def test_close_loop_failure_evidence_keeps_aggregated_details(self) -> None:
        self.source_path.write_text(
            "; English prose is rejected\nORG 0x0000\nSTART:\nNOP\nEND\n",
            encoding="utf-8",
        )
        run_dir = self.new_run("aggregated-failure")

        result = self.run_cli("close-loop", "--run-dir", str(run_dir))

        self.assertEqual(2, result.returncode)
        evidence = json.loads(
            (run_dir / "evidence.json").read_text(encoding="utf-8")
        )
        details = evidence["failure"]["details"]
        self.assertEqual("fail", details["comment_language"]["status"])
        self.assertTrue(details["profile_checks"])


if __name__ == "__main__":
    unittest.main()
