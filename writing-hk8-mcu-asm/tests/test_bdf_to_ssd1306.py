from __future__ import annotations

import importlib.util
import hashlib
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
        self.assertEqual(
            BDF.CANONICAL_FONT_SHA256,
            hashlib.sha256(FONT.read_bytes()).hexdigest(),
        )
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

    def test_mixed_text_glyphs_are_reproducible_across_independent_runs(self) -> None:
        text = "123ABC中国%￥#"
        widths = "8,8,8,8,8,8,16,16,8,16,8"
        manifests = []
        with tempfile.TemporaryDirectory() as temp:
            for index in range(3):
                output = Path(temp) / f"mixed-{index}.json"
                result = self.run_converter(
                    "--text",
                    text,
                    "--widths",
                    widths,
                    "--asset-id",
                    f"mixed-{index}",
                    "--output",
                    str(output),
                )
                self.assertEqual(0, result.returncode, result.stderr)
                manifests.append(json.loads(output.read_text(encoding="utf-8")))

        signatures = [
            {
                "source": manifest["expected_source_sha256"],
                "output": manifest["expected_output_sha256"],
                "encodings": manifest["source"]["glyph_encodings"],
                "glyphs": manifest["source"]["glyph_source_sha256"],
                "provenance": manifest["source"]["glyph_provenance"],
                "bytes": manifest["source"]["bytes"],
            }
            for manifest in manifests
        ]
        self.assertEqual(signatures[0], signatures[1])
        self.assertEqual(signatures[0], signatures[2])
        self.assertEqual(list(text), [item["label"] for item in manifests[0]["layout"]])
        glyph_hashes = manifests[0]["source"]["glyph_source_sha256"]
        self.assertNotEqual(glyph_hashes["中"], glyph_hashes["国"])
        self.assertEqual(ord("中"), manifests[0]["source"]["glyph_encodings"]["中"])
        self.assertEqual(ord("国"), manifests[0]["source"]["glyph_encodings"]["国"])

    def test_base_manifest_keeps_unreplaced_digit_bytes(self) -> None:
        base = {
            "schema_version": 1,
            "width": 24,
            "height": 16,
            "layout": [
                {"label": "digit-image", "width": 8, "kind": "image"},
                {"label": "年", "width": 16, "kind": "text"},
            ],
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

    def test_descender_glyphs_lower_the_baseline_within_the_cell(self) -> None:
        """含 3 行下伸部的小写字母必须能渲染，且基线自动下移一行。

        只用 cell_height - 3 会让 y_offset 为 -3 的字形恰好溢出一行，
        与 cell_height 取值无关。基线须按本 layout 实际字形推导。
        """
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "descender.json"
            result = self.run_converter(
                "--text", "hy",
                "--widths", "8,8",
                "--cell-height", "24",
                "--asset-id", "descender",
                "--output", str(output),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(20, manifest["source"]["baseline_row"])
        self.assertEqual(48, len(manifest["source"]["bytes"]))
        self.assertIn("#", "".join(manifest["preview_rows"]))

    def test_ascender_only_layout_keeps_the_default_baseline(self) -> None:
        """无下伸部时基线仍为 cell_height - 3，既有资产哈希不受影响。"""
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "plain.json"
            result = self.run_converter(
                "--text", "12",
                "--widths", "8,8",
                "--cell-height", "16",
                "--asset-id", "plain",
                "--output", str(output),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(13, manifest["source"]["baseline_row"])

    def test_layout_baseline_matches_default_without_descenders(self) -> None:
        _properties, glyphs = BDF.parse_bdf(FONT)
        layout = [{"label": "1", "width": 8}]
        self.assertEqual(
            BDF.default_baseline(16),
            BDF.layout_baseline(layout, glyphs, 16),
        )

    def test_layout_baseline_drops_for_descenders(self) -> None:
        _properties, glyphs = BDF.parse_bdf(FONT)
        layout = [{"label": "y", "width": 8}]
        self.assertEqual(20, BDF.layout_baseline(layout, glyphs, 24))
        self.assertLess(
            BDF.layout_baseline(layout, glyphs, 24),
            BDF.default_baseline(24),
        )

    def test_approved_font_list_covers_bundled_fonts(self) -> None:
        """白名单登记的 SHA256 必须与随包字库实际内容一致。"""
        fonts_dir = SKILL_ROOT / "references" / "fonts"
        self.assertIn("wenquanyi-bitmap-song-16px-canonical-v1", BDF.APPROVED_FONTS)
        self.assertIn("wenquanyi-bitmap-song-16px-gb2312-v1", BDF.APPROVED_FONTS)
        for font_id, entry in BDF.APPROVED_FONTS.items():
            path = fonts_dir / entry["filename"]
            self.assertTrue(path.is_file(), f"{font_id} 的字库文件缺失: {path}")
            self.assertEqual(
                entry["sha256"],
                hashlib.sha256(path.read_bytes()).hexdigest(),
                f"{font_id} 的登记 SHA256 与实际内容不一致",
            )
            self.assertEqual(font_id, BDF.font_identity(path))

    def test_unapproved_font_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / "fake.bdf"
            fake.write_bytes(FONT.read_bytes() + b"\nCOMMENT tampered\n")
            with self.assertRaises(BDF.BdfError):
                BDF.font_identity(fake)

    def test_gb2312_font_renders_chinese_glyphs(self) -> None:
        """GB2312 字库须覆盖常用汉字与全角标点，并记录对应 font_id。"""
        gb2312 = SKILL_ROOT / "references" / "fonts" / "wenquanyi_bitmap_song_16px_gb2312.bdf"
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "cn.json"
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT), str(gb2312),
                    "--text", "你好，",
                    "--widths", "16,16,16",
                    "--cell-height", "24",
                    "--asset-id", "cn",
                    "--output", str(output),
                ],
                cwd=SKILL_ROOT, text=True, encoding="utf-8",
                env=env, capture_output=True, check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(
            "wenquanyi-bitmap-song-16px-gb2312-v1",
            manifest["source"]["font_id"],
        )
        self.assertEqual(["你", "好", "，"], [i["label"] for i in manifest["layout"]])
        self.assertIn("#", "".join(manifest["preview_rows"]))

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
