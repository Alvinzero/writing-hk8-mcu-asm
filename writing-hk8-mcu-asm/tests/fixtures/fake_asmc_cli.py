#!/usr/bin/env python3
"""Small ASMC-compatible fixture used by compiler_adapter.py tests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def write_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("compile",))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--compiler-source-root", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    source = args.source
    text = source.read_text(encoding="utf-8")
    build_dir = args.workspace / ".embeddedskills" / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    stem = source.stem
    log_file = build_dir / f"{stem}-compile.log"

    if "FORCE_ERROR" in text:
        log_file.write_text("1 error(s), 0 warning(s)\n", encoding="utf-8")
        write_json(
            {
                "status": "error",
                "action": "compile",
                "summary": "compile 失败，errors=1 warnings=0",
                "details": {
                    "source": str(source),
                    "log_file": str(log_file),
                    "returncode": 2,
                    "diagnostics": [
                        {
                            "file": str(source),
                            "line": 1,
                            "level": "error",
                            "message": "forced fixture error",
                        }
                    ],
                },
                "metrics": {"errors": 1, "warnings": 0},
                "error": {"code": "compile_failed", "message": "forced fixture error"},
            }
        )
        return 1

    bin_file = build_dir / f"{stem}.bin"
    hex_file = build_dir / f"{stem}.hex"
    map_file = build_dir / f"{stem}.map"
    bin_file.write_bytes(b"\x00\x00")
    hex_file.write_text(":020000000000FE\n:00000001FF\n", encoding="ascii")
    map_file.write_text("START 0x0000\n", encoding="utf-8")
    log_file.write_text("0 error(s), 0 warning(s)\n", encoding="utf-8")
    write_json(
        {
            "status": "ok",
            "action": "compile",
            "summary": "compile 成功，errors=0 warnings=0",
            "details": {
                "source": str(source),
                "log_file": str(log_file),
                "returncode": 0,
                "diagnostics": [],
                "bin_file": str(bin_file),
                "hex_file": str(hex_file),
                "map_file": str(map_file),
                "mcu_type": "HK64S8101",
            },
            "artifacts": {
                "bin_file": str(bin_file),
                "hex_file": str(hex_file),
                "map_file": str(map_file),
                "log_file": str(log_file),
            },
            "metrics": {"errors": 0, "warnings": 0, "code_words": 1, "code_bytes": 2},
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
