from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
CLI = SKILL_ROOT / "scripts" / "hk8asm.py"
FAKE_ADAPTER = Path(__file__).parent / "fixtures" / "fake_adapter.py"


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
            "; CHIP: HK64S825\n; PURPOSE: contract fixture\nORG 0x0000\nSTART:\n    NOP\n    SJMP START\nEND\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def profile(*, status: str = "ready") -> dict:
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
            "acceptance": [
                {
                    "name": "fixture-observable",
                    "observable": "simulated-pin",
                    "expected": "toggles",
                }
            ],
            "allow_nonvolatile_changes": False,
        }

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
    def config_with_command(command: list[str], *, failures: dict[str, str] | None = None) -> dict:
        config = ClosedLoopCliContractTests.config(failures=failures)
        for role in ("compiler", "programmer", "verifier"):
            config["adapters"][role]["command"] = command
        return config

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.assertTrue(CLI.exists(), f"production CLI missing: {CLI}")
        return subprocess.run(
            [sys.executable, str(CLI), *args],
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

    def test_new_run_validates_and_snapshots_inputs(self) -> None:
        run_dir = self.new_run()
        self.assertTrue((run_dir / "run.json").is_file())
        self.assertTrue((run_dir / "profile.json").is_file())
        self.assertTrue((run_dir / "config.json").is_file())
        self.assertTrue((run_dir / "request.json").is_file())
        self.assertTrue((run_dir / "src" / "candidate.asm").is_file())

    def test_new_run_rejects_request_without_machine_observable_acceptance(self) -> None:
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
            str(self.root / "invalid"),
        )
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("INVALID_REQUEST", self.payload(result)["code"])

    def test_new_run_rejects_unstructured_or_human_observable_inputs(self) -> None:
        cases = [
            ("bad-peripheral", {"peripherals": [123]}),
            (
                "human-observable",
                {
                    "acceptance": [
                        {
                            "name": "human-led-check",
                            "observable": "human looks at LED",
                            "expected": "looks blinking",
                        }
                    ]
                },
            ),
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
            "; CHIP: HK64S825\n; PURPOSE: db blocker fixture\nORG 0x0000\nTABLE0:\n    DB 12H,34H\nEND\n",
            encoding="utf-8",
        )
        run_dir = self.new_run("db-blocker")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertNotEqual(0, loop.returncode)
        payload = self.payload(loop)
        self.assertEqual("STATIC_CHECK_FAILED", payload["code"])
        self.assertIn("HK-TOOLCHAIN-DB-001", json.dumps(payload.get("details", {})))

    def test_full_loop_releases_source_and_evidence(self) -> None:
        run_dir = self.new_run()
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertEqual(0, loop.returncode, loop.stderr or loop.stdout)
        self.assertEqual("LOOP_PASSED", self.payload(loop)["code"])
        output = self.root / "released.asm"
        release = self.run_cli("release", "--run-dir", str(run_dir), "--output", str(output))
        self.assertEqual(0, release.returncode, release.stderr or release.stdout)
        receipt = self.payload(release)
        self.assertEqual("RELEASED", receipt["code"])
        self.assertEqual(self.source_path.read_text(encoding="utf-8"), output.read_text(encoding="utf-8"))
        self.assertTrue((run_dir / "evidence.json").is_file())
        self.assertIn("source_sha256", receipt)
        self.assertIn("artifact_sha256", receipt)

    def test_each_failed_hardware_gate_blocks_release_without_source_leakage(self) -> None:
        candidate = self.source_path.read_text(encoding="utf-8")
        for role in ("compiler", "programmer", "verifier"):
            with self.subTest(role=role):
                self._write_json(self.config_path, self.config(failures={role: "fail"}))
                run_dir = self.new_run(f"fail-{role}")
                loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
                self.assertNotEqual(0, loop.returncode)
                self.assertNotIn(candidate, loop.stdout)
                output = self.root / f"leaked-{role}.asm"
                release = self.run_cli(
                    "release", "--run-dir", str(run_dir), "--output", str(output)
                )
                self.assertNotEqual(0, release.returncode)
                self.assertEqual("RELEASE_BLOCKED", self.payload(release)["code"])
                self.assertFalse(output.exists())
                self.assertNotIn(candidate, release.stdout)

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

    def test_program_readback_mismatch_blocks_release(self) -> None:
        self._write_json(self.config_path, self.config(failures={"programmer": "readback-mismatch"}))
        run_dir = self.new_run("readback-mismatch")
        loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertNotEqual(0, loop.returncode)
        self.assertEqual("PROGRAM_FAILED", self.payload(loop)["code"])

    def test_verifier_must_match_acceptance_contract(self) -> None:
        for mode in ("contract-mismatch", "missing-tests"):
            with self.subTest(mode=mode):
                self._write_json(self.config_path, self.config(failures={"verifier": mode}))
                run_dir = self.new_run(f"verify-{mode}")
                loop = self.run_cli("close-loop", "--run-dir", str(run_dir))
                self.assertNotEqual(0, loop.returncode)
                self.assertEqual("VERIFY_FAILED", self.payload(loop)["code"])

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

    def test_flash_attempt_limit_is_enforced_at_three(self) -> None:
        self._write_json(self.config_path, self.config(failures={"programmer": "fail"}))
        run_dir = self.new_run()
        for expected_attempt in range(1, 4):
            result = self.run_cli("close-loop", "--run-dir", str(run_dir))
            self.assertNotEqual(0, result.returncode)
            self.assertEqual("PROGRAM_FAILED", self.payload(result)["code"])
            run_state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(expected_attempt, run_state["flash_attempts"])
        fourth = self.run_cli("close-loop", "--run-dir", str(run_dir))
        self.assertNotEqual(0, fourth.returncode)
        self.assertEqual("FLASH_LIMIT_REACHED", self.payload(fourth)["code"])


if __name__ == "__main__":
    unittest.main()
