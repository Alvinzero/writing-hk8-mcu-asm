from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "ssd1306_page_bitmap.py"
SPEC = importlib.util.spec_from_file_location("ssd1306_page_bitmap", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BITMAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BITMAP)


class Ssd1306PageBitmapTests(unittest.TestCase):
    def test_page_pack_round_trip_preserves_lsb_top_bytes(self) -> None:
        source = [0x01, 0x80, 0x55, 0xAA]
        rows = BITMAP.unpack_page_bytes(source, width=2, height=16)
        self.assertEqual(source, BITMAP.pack_page_bytes(rows, width=2, height=16))

    def test_horizontal_mirror_stays_inside_each_glyph(self) -> None:
        rows = BITMAP.unpack_page_bytes([0x01, 0x02, 0x04, 0x08, 0x10], 5, 8)
        layout = [{"label": "A", "width": 2}, {"label": "B", "width": 3}]
        output_rows = BITMAP.transform_rows(rows, layout, True, False)
        output = BITMAP.pack_page_bytes(output_rows, 5, 8)
        self.assertEqual([0x02, 0x01, 0x10, 0x08, 0x04], output)

    def test_vertical_mirror_swaps_pages_and_reverses_bits(self) -> None:
        rows = BITMAP.unpack_page_bytes([0x01, 0x04], width=1, height=16)
        layout = [{"label": "A", "width": 1}]
        output_rows = BITMAP.transform_rows(rows, layout, False, True)
        output = BITMAP.pack_page_bytes(output_rows, width=1, height=16)
        self.assertEqual([0x20, 0x80], output)

    def test_combined_transform_preserves_text_block_order(self) -> None:
        payload = {
            "schema_version": 1,
            "width": 4,
            "height": 16,
            "layout": [
                {"label": "left", "width": 2},
                {"label": "right", "width": 2},
            ],
            "source": {
                "format": "ssd1306-page-lsb-top",
                "bytes": ["01H", "02H", "04H", "08H", "10H", "20H", "40H", "80H"],
            },
            "transform": {
                "mirror_x_within_glyphs": True,
                "mirror_y": True,
            },
        }
        result = BITMAP.build_result(payload)
        self.assertEqual(["left", "right"], result["text_order"])
        self.assertEqual(
            ["04H", "08H", "01H", "02H", "40H", "80H", "10H", "20H"],
            result["output_bytes_hex"],
        )

    def test_asm_hex_literals_prefix_values_that_start_with_a_letter(self) -> None:
        self.assertEqual("0FCH", BITMAP.format_hex_byte(0xFC))
        self.assertEqual("80H", BITMAP.format_hex_byte(0x80))

    def test_cli_rejects_hash_mismatch(self) -> None:
        payload = {
            "schema_version": 1,
            "width": 1,
            "height": 8,
            "layout": [{"label": "A", "width": 1}],
            "source": {"format": "ssd1306-page-lsb-top", "bytes": ["01H"]},
            "transform": {"mirror_x_within_glyphs": False, "mirror_y": False},
            "expected_output_sha256": "0" * 64,
        }
        with tempfile.TemporaryDirectory() as temp:
            manifest = Path(temp) / "manifest.json"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(manifest)],
                cwd=SKILL_ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
        self.assertEqual(2, result.returncode)
        self.assertIn("SSD1306_ASSET_INVALID", result.stderr)
        self.assertIn("output SHA256 mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
