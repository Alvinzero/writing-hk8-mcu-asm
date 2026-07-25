from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
SCRIPT = SCRIPTS / "bdf_to_ssd1306.py"
FONT = SKILL_ROOT / "references" / "fonts" / "wenquanyi_bitmap_song_16px_ascii_date_cn.bdf"

sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("bdf_to_ssd1306", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
BDF = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BDF
SPEC.loader.exec_module(BDF)


class BdfToSsd1306Tests(unittest.TestCase):
    def run_converter(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(FONT), *args],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def test_wqy_chinese_glyphs_render_as_16_by_16_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "wqy.json"
            result = self.run_converter(
                "--text",
                "年月号",
                "--widths",
                "16,16,16",
                "--asset-id",
                "wqy-test",
                "--output",
                str(output),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(48, manifest["width"])
        self.assertEqual(16, manifest["height"])
        self.assertEqual(["年", "月", "号"], [item["label"] for item in manifest["layout"]])
        self.assertEqual(False, manifest["transform"]["mirror_x_within_glyphs"])
        self.assertEqual(True, manifest["transform"]["mirror_y"])
        self.assertEqual(96, len(manifest["source"]["bytes"]))
        self.assertEqual(13, manifest["source"]["baseline_row"])
        self.assertIn("#", "".join(manifest["preview_rows"]))

    def test_base_manifest_keeps_unreplaced_digit_bytes(self) -> None:
        base = {
            "schema_version": 1,
            "width": 24,
            "height": 16,
            "layout": [{"label": "2", "width": 8}, {"label": "年", "width": 16}],
            "source": {"format": "ssd1306-page-lsb-top", "bytes": ["01H"] * 48},
            "transform": {"mirror_x_within_glyphs": False, "mirror_y": True},
        }
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            base_path = temp_path / "base.json"
            output = temp_path / "output.json"
            base_path.write_text(json.dumps(base), encoding="utf-8")
            result = self.run_converter(
                "--base-manifest",
                str(base_path),
                "--replace-label",
                "年",
                "--output",
                str(output),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
        values = [int(value[:-1], 16) for value in manifest["source"]["bytes"]]
        self.assertEqual([1] * 8, values[:8])
        self.assertEqual([1] * 8, values[24:32])
        self.assertNotEqual([1] * 16, values[8:24])
        self.assertNotEqual([1] * 16, values[32:48])

    def test_base_manifest_rejects_label_absent_from_layout(self) -> None:
        base = {
            "schema_version": 1,
            "width": 8,
            "height": 16,
            "layout": [{"label": "2", "width": 8}],
            "source": {"format": "ssd1306-page-lsb-top", "bytes": ["00H"] * 16},
            "transform": {"mirror_x_within_glyphs": False, "mirror_y": True},
        }
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            base_path = temp_path / "base.json"
            output = temp_path / "output.json"
            base_path.write_text(json.dumps(base), encoding="utf-8")
            result = self.run_converter(
                "--base-manifest",
                str(base_path),
                "--replace-label",
                "年",
                "--output",
                str(output),
            )
        self.assertEqual(2, result.returncode)
        self.assertIn("replacement labels are absent", result.stderr)

    def test_standard_asm_hex_prefix_round_trips(self) -> None:
        result = BDF.build_result({
            "schema_version": 1,
            "width": 1,
            "height": 8,
            "layout": [{"label": "A", "width": 1}],
            "source": {"format": "ssd1306-page-lsb-top", "bytes": ["0C7H"]},
            "transform": {"mirror_x_within_glyphs": False, "mirror_y": False},
        })
        self.assertEqual(["0C7H"], result["output_bytes_hex"])


if __name__ == "__main__":
    unittest.main()
