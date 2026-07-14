#!/usr/bin/env python3
"""Deterministic adapter used by the closed-loop CLI contract tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def emit_result(path: Path, payload: dict, *, stdout_only: bool) -> None:
    if stdout_only:
        print(json.dumps(payload, separators=(",", ":")))
    else:
        write_json(path, payload)


def tool_version(simulate: str) -> str:
    if simulate == "unapproved-version":
        return "sim-9.9"
    return "sim-1.0"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdout-only", action="store_true")
    parser.add_argument("role", choices=("compiler", "programmer", "verifier"))
    parser.add_argument("operation", choices=("probe", "run"))
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    request = json.loads(args.input.read_text(encoding="utf-8"))
    simulate = request.get("simulate", {}).get(args.role, "pass")

    if args.operation == "probe":
        payload = {
            "status": "pass" if simulate != "probe-fail" else "fail",
            "role": args.role,
            "tool_version": tool_version(simulate),
        }
        if args.role == "programmer":
            payload.update(
                {
                    "device_id": request["expected_device_id"],
                    "programmer_serial": request["expected_programmer_serial"],
                    "voltage_mv": request["expected_voltage_mv"],
                }
            )
            if simulate == "probe-device-mismatch":
                payload["device_id"] = "WRONG-DEVICE"
            elif simulate == "probe-serial-mismatch":
                payload["programmer_serial"] = "WRONG-SERIAL"
            elif simulate == "probe-voltage-mismatch":
                payload["voltage_mv"] = request["expected_voltage_mv"] + 100
        emit_result(args.output, payload, stdout_only=args.stdout_only)
        return 0 if simulate != "probe-fail" else 20

    if simulate != "pass":
        if args.role == "compiler" and simulate in {"allowed-warning", "unapproved-warning"}:
            pass
        elif args.role == "programmer" and simulate == "readback-mismatch":
            pass
        elif args.role == "verifier" and simulate in {"contract-mismatch", "missing-tests"}:
            pass
        elif simulate == "unapproved-version":
            pass
        else:
            emit_result(
                args.output,
                {"status": "fail", "role": args.role, "error": f"simulated {args.role} failure"},
                stdout_only=args.stdout_only,
            )
            return 21

    if args.role == "compiler":
        source = Path(request["source_path"])
        artifact = Path(request["artifact_path"])
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(("SIMHEX:" + sha256(source)).encode("ascii"))
        warnings = []
        if simulate == "allowed-warning":
            warnings = ["HK-WARN-ALLOWED"]
        elif simulate == "unapproved-warning":
            warnings = ["HK-WARN-UNAPPROVED"]
        payload = {
            "status": "pass",
            "role": args.role,
            "tool_version": tool_version(simulate),
            "source_sha256": sha256(source),
            "artifact_path": str(artifact),
            "artifact_sha256": sha256(artifact),
            "warnings": warnings,
        }
    elif args.role == "programmer":
        artifact = Path(request["artifact_path"])
        artifact_hash = sha256(artifact)
        readback_hash = "mismatched-readback" if simulate == "readback-mismatch" else artifact_hash
        payload = {
            "status": "pass",
            "role": args.role,
            "tool_version": tool_version(simulate),
            "device_id": request["expected_device_id"],
            "programmer_serial": request["expected_programmer_serial"],
            "voltage_mv": request["expected_voltage_mv"],
            "artifact_sha256": artifact_hash,
            "readback_sha256": readback_hash,
        }
    else:
        tests = [
            {
                "name": "fixture-observable",
                "observable": "simulated-pin",
                "expected": "toggles",
                "actual": "toggles",
                "status": "pass",
            }
        ]
        if simulate == "contract-mismatch":
            tests[0]["observable"] = "different-observable"
        elif simulate == "missing-tests":
            tests = []
        payload = {
            "status": "pass",
            "role": args.role,
            "tool_version": tool_version(simulate),
            "tests": tests,
        }

    emit_result(args.output, payload, stdout_only=args.stdout_only)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(1)
