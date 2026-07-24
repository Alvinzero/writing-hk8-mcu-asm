#!/usr/bin/env python3
"""Transform and audit SSD1306 page-format glyph rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


PAGE_FORMAT = "ssd1306-page-lsb-top"
HEX_BYTE_RE = re.compile(r"^(?:0x)?([0-9a-fA-F]{1,2})(?:H)?$")


class AssetError(ValueError):
    pass


def parse_byte(value: Any) -> int:
    if isinstance(value, bool):
        raise AssetError("Boolean values are not valid bytes")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        match = HEX_BYTE_RE.fullmatch(value.strip())
        if match is None:
            raise AssetError("Invalid hexadecimal byte: {!r}".format(value))
        number = int(match.group(1), 16)
    else:
        raise AssetError("Byte values must be integers or hexadecimal strings")
    if not 0 <= number <= 0xFF:
        raise AssetError("Byte is outside 0x00..0xFF: {!r}".format(value))
    return number


def normalize_layout(raw_layout: Any, width: int) -> List[Dict[str, Any]]:
    if not isinstance(raw_layout, list) or not raw_layout:
        raise AssetError("layout must be a non-empty list")
    layout: List[Dict[str, Any]] = []
    total_width = 0
    for index, raw_item in enumerate(raw_layout):
        if not isinstance(raw_item, dict):
            raise AssetError("layout item {} must be an object".format(index))
        label = raw_item.get("label")
        glyph_width = raw_item.get("width")
        if not isinstance(label, str) or not label:
            raise AssetError("layout item {} needs a non-empty label".format(index))
        if isinstance(glyph_width, bool) or not isinstance(glyph_width, int) or glyph_width <= 0:
            raise AssetError("layout item {} needs a positive integer width".format(index))
        layout.append({"label": label, "width": glyph_width})
        total_width += glyph_width
    if total_width != width:
        raise AssetError(
            "layout width {} does not match image width {}".format(total_width, width)
        )
    return layout


def unpack_page_bytes(data: Sequence[int], width: int, height: int) -> List[List[int]]:
    pages = height // 8
    expected = width * pages
    if len(data) != expected:
        raise AssetError("byte count {} does not match expected {}".format(len(data), expected))
    rows = [[0 for _ in range(width)] for _ in range(height)]
    for page in range(pages):
        for column in range(width):
            value = data[page * width + column]
            for bit in range(8):
                rows[page * 8 + bit][column] = (value >> bit) & 1
    return rows


def pack_page_bytes(rows: Sequence[Sequence[int]], width: int, height: int) -> List[int]:
    if len(rows) != height or any(len(row) != width for row in rows):
        raise AssetError("pixel matrix dimensions do not match width and height")
    output: List[int] = []
    for page in range(height // 8):
        for column in range(width):
            value = 0
            for bit in range(8):
                pixel = rows[page * 8 + bit][column]
                if pixel not in (0, 1):
                    raise AssetError("pixels must be 0 or 1")
                value |= pixel << bit
            output.append(value)
    return output


def transform_rows(
    rows: Sequence[Sequence[int]],
    layout: Sequence[Dict[str, Any]],
    mirror_x_within_glyphs: bool,
    mirror_y: bool,
) -> List[List[int]]:
    transformed = [list(row) for row in rows]
    if mirror_y:
        transformed.reverse()
    if mirror_x_within_glyphs:
        for row_index, row in enumerate(transformed):
            output_row: List[int] = []
            offset = 0
            for item in layout:
                glyph_width = int(item["width"])
                output_row.extend(reversed(row[offset : offset + glyph_width]))
                offset += glyph_width
            transformed[row_index] = output_row
    return transformed


def sha256_bytes(data: Sequence[int]) -> str:
    return hashlib.sha256(bytes(bytearray(data))).hexdigest()


def format_hex_byte(value: int) -> str:
    digits = "{:02X}".format(value)
    if digits[0] in "ABCDEF":
        digits = "0" + digits
    return digits + "H"


def preview_rows(rows: Sequence[Sequence[int]], layout: Sequence[Dict[str, Any]]) -> List[str]:
    boundaries = set()
    offset = 0
    for item in layout[:-1]:
        offset += int(item["width"])
        boundaries.add(offset)
    output: List[str] = []
    for row in rows:
        rendered: List[str] = []
        for column, pixel in enumerate(row):
            if column in boundaries:
                rendered.append("|")
            rendered.append("#" if pixel else ".")
        output.append("".join(rendered))
    return output


def require_dimensions(payload: Dict[str, Any]) -> Tuple[int, int]:
    width = payload.get("width")
    height = payload.get("height")
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise AssetError("width must be a positive integer")
    if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
        raise AssetError("height must be a positive integer")
    if height % 8 != 0:
        raise AssetError("height must be a multiple of 8 for SSD1306 page format")
    return width, height


def build_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    if payload.get("schema_version") != 1:
        raise AssetError("schema_version must be 1")
    width, height = require_dimensions(payload)
    layout = normalize_layout(payload.get("layout"), width)

    source = payload.get("source")
    if not isinstance(source, dict):
        raise AssetError("source must be an object")
    if source.get("format") != PAGE_FORMAT:
        raise AssetError("source.format must be {}".format(PAGE_FORMAT))
    raw_bytes = source.get("bytes")
    if not isinstance(raw_bytes, list):
        raise AssetError("source.bytes must be a list")
    source_bytes = [parse_byte(value) for value in raw_bytes]
    source_rows = unpack_page_bytes(source_bytes, width, height)

    transform = payload.get("transform")
    if not isinstance(transform, dict):
        raise AssetError("transform must be an object")
    mirror_x = transform.get("mirror_x_within_glyphs")
    mirror_y = transform.get("mirror_y")
    if not isinstance(mirror_x, bool) or not isinstance(mirror_y, bool):
        raise AssetError("transform mirror flags must be booleans")

    output_rows = transform_rows(source_rows, layout, mirror_x, mirror_y)
    output_bytes = pack_page_bytes(output_rows, width, height)
    source_hash = sha256_bytes(source_bytes)
    output_hash = sha256_bytes(output_bytes)

    expected_source_hash = payload.get("expected_source_sha256")
    if expected_source_hash is not None and expected_source_hash != source_hash:
        raise AssetError(
            "source SHA256 mismatch: expected {}, got {}".format(
                expected_source_hash, source_hash
            )
        )
    expected_output_hash = payload.get("expected_output_sha256")
    if expected_output_hash is not None and expected_output_hash != output_hash:
        raise AssetError(
            "output SHA256 mismatch: expected {}, got {}".format(
                expected_output_hash, output_hash
            )
        )

    return {
        "status": "ok",
        "format": PAGE_FORMAT,
        "width": width,
        "height": height,
        "page_count": height // 8,
        "layout": layout,
        "text_order": [item["label"] for item in layout],
        "transform": {
            "mirror_x_within_glyphs": mirror_x,
            "mirror_y": mirror_y,
        },
        "source_byte_count": len(source_bytes),
        "source_sha256": source_hash,
        "output_byte_count": len(output_bytes),
        "output_sha256": output_hash,
        "output_bytes_hex": [format_hex_byte(value) for value in output_bytes],
        "preview_rows": preview_rows(output_rows, layout),
    }


def emit_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--format",
        choices=("json", "hex", "asm", "preview"),
        default="json",
        help="select the stdout representation",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise AssetError("manifest root must be an object")
        result = build_result(payload)
    except (AssetError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {"status": "error", "code": "SSD1306_ASSET_INVALID", "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    if args.format == "json":
        emit_json(result)
    elif args.format == "hex":
        print(",".join(result["output_bytes_hex"]))
    elif args.format == "asm":
        for value in result["output_bytes_hex"]:
            print("    MOV A,#{}".format(value))
            print("    CALL I2C_SEND")
    else:
        print("\n".join(result["preview_rows"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
