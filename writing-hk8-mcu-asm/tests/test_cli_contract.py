from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
CLI = SKILL_ROOT / "scripts" / "hk8asm.py"
COMPILER_ADAPTER = SKILL_ROOT / "scripts" / "compiler_adapter.py"
FAKE_ADAPTER = Path(__file__).parent / "fixtures" / "fake_adapter.py"
FAKE_ASMC_CLI = Path(__file__).parent / "fixtures" / "fake_asmc_cli.py"
EXAMPLE_PROFILE = SKILL_ROOT / "references" / "profiles" / "HK64S825.profile.example.json"
EXAMPLE_CONFIG = SKILL_ROOT / "references" / "configs" / "local-adapter.example.json"
BUILTIN_COMPILER = SKILL_ROOT / "scripts" / "builtin_compiler.py"
INSTRUCTION_REFERENCE = SKILL_ROOT / "references" / "spec" / "rules" / "instruction-reference.json"
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
            "HK64S8101",
            "--tool-version",
            tool_version,
        ]

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

    def test_bundled_examples_use_builtin_compiler_without_local_toolchain_config(self) -> None:
        request = self.request()
        request["board"] = {"id": "HK64S825-DEFAULT"}
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

        doctor = self.run_cli("doctor", "--profile", str(EXAMPLE_PROFILE), "--config", str(EXAMPLE_CONFIG))
        self.assertEqual(0, doctor.returncode, doctor.stderr or doctor.stdout)
        doctor_payload = self.payload(doctor)
        self.assertEqual("READY", doctor_payload["code"])
        self.assertEqual("builtin-hk64s825-assembler-1", doctor_payload["tools"]["compiler"])

        run_dir = self.root / "builtin-example"
        new_run = self.run_cli(
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
        profile["approved_tool_versions"] = {"compiler": ["builtin-hk64s825-assembler-1"]}
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
            "; CHIP: HK64S825\n; PURPOSE: fail fixture\n; FORCE_ERROR\nORG 0x0000\nNOP\nEND\n",
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


if __name__ == "__main__":
    unittest.main()
