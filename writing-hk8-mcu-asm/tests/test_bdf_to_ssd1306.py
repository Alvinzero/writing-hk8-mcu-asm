from __future__ import annotations

import importlib.util
import json
import os
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
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(FONT), *args],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            env=env,
            capture_output=True,
            check=False,
        )

    def test_wqy_chinese_glyphs_render_as_16_by_16_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "wqy.json"
            result = self.run_converter(
                "--text",
                "中国￥",
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
        self.assertEqual(["中", "国", "￥"], [item["label"] for item in manifest["layout"]])
        self.assertEqual(False, manifest["transform"]["mirror_x_within_glyphs"])
        self.assertEqual(True, manifest["transform"]["mirror_y"])
        self.assertEqual(96, len(manifest["source"]["bytes"]))
        self.assertEqual(13, manifest["source"]["baseline_row"])
        self.assertIn("#", "".join(manifest["preview_rows"]))

    def test_canonical_font_covers_china_and_fullwidth_yen(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "china-yen.json"
            result = self.run_converter(
                "--text",
                "中国￥",
                "--widths",
                "16,16,16",
                "--asset-id",
                "china-yen-test",
                "--output",
                str(output),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(
            {"中": ord("中"), "国": ord("国"), "￥": ord("￥")},
            manifest["source"]["glyph_encodings"],
        )
        self.assertEqual(96, len(manifest["source"]["bytes"]))
        self.assertEqual(
            ["中", "国", "￥"],
            [item["label"] for item in manifest["source"]["glyph_provenance"]],
        )
        self.assertTrue(
            all(
                len(item["source_sha256"]) == 64
                for item in manifest["source"]["glyph_provenance"]
            )
        )
        self.assertIn("#", "".join(manifest["preview_rows"]))

    def test_base_manifest_rejects_unreplaced_digit_text(self) -> None:
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
        self.assertEqual(2, result.returncode)
        self.assertIn("text labels must all be replaced", result.stderr)

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

    def test_base_manifest_rejects_unreplaced_text_labels(self) -> None:
        base = {
            "schema_version": 1,
            "width": 16,
            "height": 16,
            "layout": [
                {"label": "1", "width": 8},
                {"label": "2", "width": 8},
            ],
            "source": {
                "format": "ssd1306-page-lsb-top",
                "bytes": ["00H"] * 32,
            },
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
                "1",
                "--output",
                str(output),
            )
        self.assertEqual(2, result.returncode)
        self.assertIn("text labels must all be replaced", result.stderr)

    def test_base_manifest_may_preserve_explicit_image_blocks(self) -> None:
        base = {
            "schema_version": 1,
            "width": 16,
            "height": 16,
            "layout": [
                {"label": "1", "width": 8, "kind": "text"},
                {"label": "logo", "width": 8, "kind": "image"},
            ],
            "source": {
                "format": "ssd1306-page-lsb-top",
                "bytes": ["00H"] * 16 + ["01H"] * 16,
            },
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
                "1",
                "--output",
                str(output),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(["1"], [item["label"] for item in manifest["source"]["glyph_provenance"]])

    def test_explicit_narrow_cell_is_cropped_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "narrow-ascii.json"
            result = self.run_converter(
                "--text",
                "AC",
                "--widths",
                "8,8",
                "--output",
                str(output),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual([8, 8], [item["width"] for item in manifest["layout"]])
        self.assertEqual(32, len(manifest["source"]["bytes"]))
        self.assertEqual(
            ["A", "C"],
            [item["label"] for item in manifest["source"]["glyph_provenance"]],
        )

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
