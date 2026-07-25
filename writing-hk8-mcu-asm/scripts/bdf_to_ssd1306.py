#!/usr/bin/env python3
"""Render selected BDF glyphs into an audited SSD1306 page manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ssd1306_page_bitmap import (
    PAGE_FORMAT,
    build_result,
    format_hex_byte,
    pack_page_bytes,
    parse_byte,
    unpack_page_bytes,
    split_glyph_bytes,
)


GENERATOR_VERSION = "bdf-to-ssd1306-2"
CANONICAL_FONT_ID = "wenquanyi-bitmap-song-16px-canonical-v1"
CANONICAL_FONT_SHA256 = "7f4e5ee2d26faef12ccb90e77532346a0cfbd2a8335535907825b8f498629f65"


class BdfError(ValueError):
    pass


@dataclass(frozen=True)
class Glyph:
    codepoint: int
    width: int
    height: int
    x_offset: int
    y_offset: int
    dwidth: int
    bitmap: Tuple[int, ...]


def parse_int(value: str, field: str) -> int:
    try:
        return int(value, 10)
    except ValueError as exc:
        raise BdfError("invalid {}: {}".format(field, value)) from exc


def parse_bdf(path: Path) -> Tuple[Dict[str, str], Dict[int, Glyph]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise BdfError("cannot read BDF {}: {}".format(path, exc)) from exc

    properties: Dict[str, str] = {}
    glyphs: Dict[int, Glyph] = {}
    current: Dict[str, object] = {}
    bitmap: List[int] = []
    in_bitmap = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("STARTPROPERTIES"):
            continue
        if line == "ENDPROPERTIES":
            continue
        if not current and " " in line:
            key, value = line.split(" ", 1)
            if key in {"FONT_ASCENT", "FONT_DESCENT", "PIXEL_SIZE"}:
                properties[key] = value.strip()
        if line == "STARTCHAR":
            current = {}
            bitmap = []
            in_bitmap = False
            continue
        if line.startswith("STARTCHAR "):
            current = {}
            bitmap = []
            in_bitmap = False
            continue
        if line == "BITMAP":
            in_bitmap = True
            continue
        if line == "ENDCHAR":
            in_bitmap = False
            encoding = current.get("encoding")
            if isinstance(encoding, int) and encoding >= 0:
                required_fields = {"width", "height", "x_offset", "y_offset", "dwidth"}
                missing_fields = sorted(required_fields.difference(current))
                if missing_fields:
                    raise BdfError(
                        "glyph U+{:04X} is missing {}".format(
                            encoding, ", ".join(missing_fields)
                        )
                    )
                required_rows = int(current["height"])
                if required_rows != len(bitmap):
                    raise BdfError(
                        "glyph U+{:04X} has {} bitmap rows, expected {}".format(
                            encoding, len(bitmap), required_rows
                        )
                    )
                glyphs.setdefault(
                    encoding,
                    Glyph(
                        codepoint=encoding,
                        width=int(current["width"]),
                        height=required_rows,
                        x_offset=int(current["x_offset"]),
                        y_offset=int(current["y_offset"]),
                        dwidth=int(current["dwidth"]),
                        bitmap=tuple(bitmap),
                    ),
                )
            current = {}
            bitmap = []
            continue
        if in_bitmap:
            if not re.fullmatch(r"[0-9A-Fa-f]+", line):
                raise BdfError("invalid bitmap row: {}".format(line))
            bitmap.append(int(line, 16))
            continue
        parts = line.split()
        if not parts:
            continue
        key = parts[0]
        if key == "ENCODING" and len(parts) >= 2:
            current["encoding"] = parse_int(parts[1], "encoding")
        elif key == "DWIDTH" and len(parts) >= 2:
            current["dwidth"] = parse_int(parts[1], "DWIDTH")
        elif key == "BBX" and len(parts) >= 5:
            current["width"] = parse_int(parts[1], "BBX width")
            current["height"] = parse_int(parts[2], "BBX height")
            current["x_offset"] = parse_int(parts[3], "BBX x offset")
            current["y_offset"] = parse_int(parts[4], "BBX y offset")

    if not glyphs:
        raise BdfError("BDF contains no encoded glyphs: {}".format(path))
    return properties, glyphs


def parse_widths(value: Optional[str], count: int) -> Optional[List[int]]:
    if value is None:
        return None
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) != count:
        raise BdfError("--widths must contain {} values".format(count))
    widths: List[int] = []
    for part in parts:
        number = parse_int(part, "cell width")
        if number <= 0:
            raise BdfError("cell widths must be positive")
        widths.append(number)
    return widths


def glyph_pixels(glyph: Glyph) -> List[List[int]]:
    rows: List[List[int]] = []
    row_bytes = (glyph.width + 7) // 8
    for value in glyph.bitmap:
        row = []
        for x in range(glyph.width):
            shift = row_bytes * 8 - 1 - x
            row.append((value >> shift) & 1)
        rows.append(row)
    return rows


def render_glyph(
    glyph: Glyph, cell_width: int, cell_height: int, baseline_row: int
) -> List[List[int]]:
    if cell_width <= 0 or cell_height <= 0:
        raise BdfError("cell dimensions must be positive")
    canvas = [[0 for _ in range(cell_width)] for _ in range(cell_height)]
    for source_y, source_row in enumerate(glyph_pixels(glyph)):
        font_y = glyph.y_offset + glyph.height - 1 - source_y
        target_y = baseline_row - font_y
        if not 0 <= target_y < cell_height:
            raise BdfError(
                "glyph U+{:04X} does not fit cell: target row {}".format(
                    glyph.codepoint, target_y
                )
            )
        for source_x, pixel in enumerate(source_row):
            target_x = glyph.x_offset + source_x
            if not 0 <= target_x < cell_width:
                if pixel:
                    raise BdfError(
                        "glyph U+{:04X} does not fit cell: target column {}".format(
                            glyph.codepoint, target_x
                        )
                    )
                continue
            canvas[target_y][target_x] = pixel
    return canvas


def crop_glyph_cell(
    rendered: List[List[int]], cell_width: int
) -> List[List[int]]:
    if cell_width <= 0:
        raise BdfError("cell width must be positive")
    source_width = len(rendered[0])
    if cell_width > source_width:
        raise BdfError(
            "cell width {} exceeds canonical glyph advance {}".format(
                cell_width, source_width
            )
        )
    left = (source_width - cell_width) // 2
    return [row[left : left + cell_width] for row in rendered]


def glyph_by_label(glyphs: Dict[int, Glyph], label: str) -> Glyph:
    if len(label) != 1:
        raise BdfError("layout labels must contain one Unicode character: {!r}".format(label))
    codepoint = ord(label)
    glyph = glyphs.get(codepoint)
    if glyph is None:
        raise BdfError("BDF has no glyph for U+{:04X} ({})".format(codepoint, label))
    return glyph


def load_base_manifest(path: Path) -> Tuple[dict, List[List[int]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BdfError("cannot read base manifest {}: {}".format(path, exc)) from exc
    if not isinstance(payload, dict):
        raise BdfError("base manifest root must be an object")
    width = payload.get("width")
    height = payload.get("height")
    source = payload.get("source")
    if not isinstance(width, int) or not isinstance(height, int):
        raise BdfError("base manifest needs integer width and height")
    if not isinstance(source, dict) or source.get("format") != PAGE_FORMAT:
        raise BdfError("base manifest source must use {}".format(PAGE_FORMAT))
    raw_bytes = source.get("bytes")
    if not isinstance(raw_bytes, list):
        raise BdfError("base manifest source.bytes must be a list")
    try:
        source_bytes = [parse_byte(value) for value in raw_bytes]
    except (TypeError, ValueError) as exc:
        raise BdfError("base manifest contains invalid source bytes") from exc
    return payload, unpack_page_bytes(source_bytes, width, height)


def clone_layout(payload: dict) -> List[dict]:
    raw_layout = payload.get("layout")
    if not isinstance(raw_layout, list) or not raw_layout:
        raise BdfError("base manifest layout must be a non-empty list")
    layout: List[dict] = []
    for item in raw_layout:
        if not isinstance(item, dict) or not isinstance(item.get("label"), str):
            raise BdfError("base manifest layout item is invalid")
        if not isinstance(item.get("width"), int) or item["width"] <= 0:
            raise BdfError("base manifest layout width is invalid")
        cloned = {"label": item["label"], "width": item["width"]}
        if "kind" in item:
            if item["kind"] not in {"text", "image"}:
                raise BdfError("base manifest layout kind is invalid")
            cloned["kind"] = item["kind"]
        layout.append(cloned)
    return layout


def default_baseline(cell_height: int) -> int:
    if cell_height < 3:
        raise BdfError("cell height is too small for a baseline")
    return cell_height - 3


def make_manifest(
    font_path: Path,
    layout: Sequence[dict],
    source_rows: List[List[int]],
    cell_height: int,
    baseline_row: int,
    mirror_x: bool,
    mirror_y: bool,
    replaced_labels: Sequence[str],
    properties: Dict[str, str],
    base_manifest: Optional[Path],
    asset_id: str,
) -> dict:
    width = len(source_rows[0])
    source_bytes = pack_page_bytes(source_rows, width, cell_height)
    source_glyphs = split_glyph_bytes(source_bytes, layout, cell_height)
    rendered_labels = [
        item["label"]
        for item in layout
        if item.get("kind") == "text"
    ]
    glyph_provenance = [
        {
            "label": item["label"],
            "codepoint": ord(item["label"]),
            "width": item["width"],
            "source_sha256": hashlib.sha256(bytes(source_glyphs[index])).hexdigest(),
        }
        for index, item in enumerate(layout)
        if item.get("kind") == "text"
    ]
    manifest = {
        "schema_version": 1,
        "asset_id": asset_id,
        "width": width,
        "height": cell_height,
        "layout": list(layout),
        "source": {
            "format": PAGE_FORMAT,
            "bytes": [format_hex_byte(value) for value in source_bytes],
            "generator": "bdf_to_ssd1306.py",
            "generator_version": GENERATOR_VERSION,
            "font_id": CANONICAL_FONT_ID,
            "font_source": str(font_path),
            "font_sha256": hashlib.sha256(font_path.read_bytes()).hexdigest(),
            "font_properties": properties,
            "baseline_row": baseline_row,
            "replaced_labels": list(replaced_labels),
            "base_manifest": str(base_manifest) if base_manifest is not None else None,
            "glyph_encodings": {
                label: ord(label) for label in sorted(set(rendered_labels))
            },
            "glyph_source_sha256": {
                item["label"]: hashlib.sha256(bytes(source_glyphs[index])).hexdigest()
                for index, item in enumerate(layout)
                if item.get("kind") == "text"
            },
            "glyph_provenance": glyph_provenance,
        },
        "transform": {
            "mirror_x_within_glyphs": mirror_x,
            "mirror_y": mirror_y,
        },
        "expected_source_sha256": hashlib.sha256(bytes(source_bytes)).hexdigest(),
    }
    result = build_result(manifest)
    manifest["expected_output_sha256"] = result["output_sha256"]
    manifest["preview_rows"] = result["preview_rows"]
    return manifest


def build_manifest(args: argparse.Namespace) -> dict:
    font_hash = hashlib.sha256(args.font.read_bytes()).hexdigest()
    if args.font.resolve() == (Path(__file__).resolve().parents[1] / "references" / "fonts" / "wenquanyi_bitmap_song_16px_ascii_date_cn.bdf").resolve():
        if font_hash != CANONICAL_FONT_SHA256:
            raise BdfError(
                "canonical font SHA256 mismatch: expected {}, got {}".format(
                    CANONICAL_FONT_SHA256, font_hash
                )
            )
    properties, glyphs = parse_bdf(args.font)
    if args.base_manifest is not None:
        if args.text is not None or args.widths is not None:
            raise BdfError("--text and --widths cannot be used with --base-manifest")
        base_payload, rows = load_base_manifest(args.base_manifest)
        layout = clone_layout(base_payload)
        cell_height = int(base_payload["height"])
        replace_labels = set(args.replace_label or [])
        if not replace_labels:
            raise BdfError("--replace-label is required with --base-manifest")
        unknown_labels = replace_labels.difference(item["label"] for item in layout)
        if unknown_labels:
            raise BdfError(
                "replacement labels are absent from base layout: {}".format(
                    ", ".join(sorted(unknown_labels))
                )
            )
        missing_text_labels = {
            item["label"]
            for item in layout
            if item.get("kind") != "image" and item["label"] not in replace_labels
        }
        if missing_text_labels:
            raise BdfError(
                "base manifest text labels must all be replaced: {}".format(
                    ", ".join(sorted(missing_text_labels))
                )
            )
        for item in layout:
            if item["label"] in replace_labels:
                item["kind"] = "text"
    else:
        if args.replace_label:
            raise BdfError("--replace-label requires --base-manifest")
        if not args.text:
            raise BdfError("--text is required without --base-manifest")
        labels = list(args.text)
        parsed_widths = parse_widths(args.widths, len(labels))
        layout = []
        for index, label in enumerate(labels):
            glyph = glyph_by_label(glyphs, label)
            width = parsed_widths[index] if parsed_widths is not None else glyph.dwidth
            layout.append({"label": label, "width": width, "kind": "text"})
        cell_height = args.cell_height
        rows = [[0 for _ in range(sum(item["width"] for item in layout))] for _ in range(cell_height)]
        replace_labels = set(labels)

    width = sum(item["width"] for item in layout)
    if len(rows) != cell_height or any(len(row) != width for row in rows):
        raise BdfError("base manifest dimensions do not match its layout")
    baseline_row = args.baseline_row
    if baseline_row is None:
        baseline_row = default_baseline(cell_height)
    if not 0 <= baseline_row < cell_height:
        raise BdfError("baseline row must be inside the cell")

    offset = 0
    for item in layout:
        label = item["label"]
        if label in replace_labels:
            glyph = glyph_by_label(glyphs, label)
            rendered = crop_glyph_cell(
                render_glyph(glyph, glyph.dwidth, cell_height, baseline_row),
                item["width"],
            )
            for row_index in range(cell_height):
                rows[row_index][offset : offset + item["width"]] = rendered[row_index]
        offset += item["width"]

    return make_manifest(
        args.font,
        layout,
        rows,
        cell_height,
        baseline_row,
        args.mirror_x,
        args.mirror_y,
        sorted(replace_labels),
        properties,
        args.base_manifest,
        args.asset_id,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("font", type=Path, help="input BDF font")
    parser.add_argument("--output", type=Path, required=True, help="output JSON manifest")
    parser.add_argument("--text", help="Unicode text to render")
    parser.add_argument(
        "--base-manifest",
        type=Path,
        help="existing manifest whose non-replaced glyphs and layout are preserved",
    )
    parser.add_argument(
        "--replace-label",
        action="append",
        help="glyph label to replace; repeat for multiple labels",
    )
    parser.add_argument("--widths", help="comma-separated cell widths for --text")
    parser.add_argument("--cell-height", type=int, default=16)
    parser.add_argument("--baseline-row", type=int)
    parser.add_argument("--mirror-x", action="store_true", default=False)
    parser.add_argument("--no-mirror-y", action="store_true", default=False)
    parser.add_argument("--asset-id", default="bdf-ssd1306-asset")
    parser.add_argument("--preview", action="store_true", help="also print transformed preview")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    args.mirror_y = not args.no_mirror_y
    try:
        manifest = build_manifest(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if args.preview:
            print("\n".join(manifest["preview_rows"]))
    except (BdfError, OSError, UnicodeError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "error", "code": "BDF_ASSET_INVALID", "message": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
