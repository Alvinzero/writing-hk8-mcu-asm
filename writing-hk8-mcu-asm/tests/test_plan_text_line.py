from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
SCRIPT = SCRIPTS / "plan_text_line.py"
BDF_CONVERTER = SCRIPTS / "bdf_to_ssd1306.py"
GB2312_FONT = SKILL_ROOT / "references" / "fonts" / "wenquanyi_bitmap_song_16px_gb2312.bdf"

sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("plan_text_line", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
PLAN = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PLAN
SPEC.loader.exec_module(PLAN)


class PlanTextLineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.metrics = PLAN.load_metrics(GB2312_FONT)

    def run_tool(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            env=env,
            capture_output=True,
            check=False,
        )

    def test_even_line_needs_no_padding(self) -> None:
        plan = PLAN.plan_line("你好123hello，", self.metrics)
        self.assertFalse(plan["padded"])
        self.assertEqual(plan["text"], plan["render_text"])
        self.assertEqual(104, plan["width"])
        self.assertEqual(312, plan["byte_count"])
        self.assertEqual(156, plan["words"])

    def test_odd_line_gets_a_one_pixel_blank_column(self) -> None:
        """奇数行宽在行尾补 1 像素空列，且不改动任何字形的格宽。"""
        plan = PLAN.plan_line("王浩宇Whys，", self.metrics)
        self.assertTrue(plan["padded"])
        self.assertEqual(plan["text"] + " ", plan["render_text"])
        self.assertEqual(1, plan["widths"][-1])
        self.assertEqual(102, plan["width"])
        self.assertEqual(0, plan["byte_count"] % 2)
        self.assertEqual(153, plan["words"])
        # 补列之外的宽度必须仍是各字形的原始 DWIDTH
        original = [self.metrics[ord(ch)] for ch in plan["text"]]
        self.assertEqual(original, plan["widths"][:-1])

    def test_window_is_horizontally_centred(self) -> None:
        plan = PLAN.plan_line("在吗", self.metrics)
        self.assertEqual(32, plan["width"])
        self.assertEqual(48, plan["column_start"])
        self.assertEqual(79, plan["column_end"])

    def test_missing_glyph_is_reported(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            PLAN.plan_line("你好\U00030ede", self.metrics)
        self.assertIn("缺字", str(ctx.exception))

    def test_line_wider_than_panel_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            PLAN.plan_line("你好123hello，？？在吗？？", self.metrics)
        self.assertIn("128", str(ctx.exception))

    def test_empty_text_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PLAN.plan_line("", self.metrics)

    def test_cli_exits_non_zero_on_missing_glyph(self) -> None:
        result = self.run_tool("--text", "你好\U00030ede")
        self.assertEqual(1, result.returncode)

    def test_cli_json_output_is_machine_readable(self) -> None:
        result = self.run_tool("--text", "王浩宇Whys，", "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual("16,16,16,14,8,8,7,16,1", plan["widths_arg"])
        self.assertTrue(plan["padded"])

    def test_planned_arguments_produce_an_even_byte_asset(self) -> None:
        """工具给出的参数必须能直接生成偶数字节的资产。"""
        result = self.run_tool("--text", "王浩宇Whys，", "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        plan = json.loads(result.stdout)

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "planned.json"
            generated = subprocess.run(
                [
                    sys.executable, str(BDF_CONVERTER), str(GB2312_FONT),
                    "--text", plan["render_text"],
                    "--widths", plan["widths_arg"],
                    "--cell-height", "24",
                    "--asset-id", "planned",
                    "--output", str(output),
                ],
                cwd=SKILL_ROOT, text=True, encoding="utf-8",
                env=env, capture_output=True, check=False,
            )
            self.assertEqual(0, generated.returncode, generated.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(plan["width"], manifest["width"])
        self.assertEqual(plan["byte_count"], len(manifest["source"]["bytes"]))
        self.assertEqual(0, len(manifest["source"]["bytes"]) % 2)
        # 末项即补列，宽 1 且不带像素
        self.assertEqual(1, manifest["layout"][-1]["width"])

    def test_padding_column_leaves_earlier_columns_untouched(self) -> None:
        """补列只在行尾增加一列全 0，不影响原有列的字节。"""
        import tempfile

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        plan = PLAN.plan_line("王浩宇Whys，", self.metrics)
        unpadded_widths = ",".join(
            str(self.metrics[ord(ch)]) for ch in plan["text"]
        )

        def generate(text: str, widths: str, path: Path) -> dict:
            done = subprocess.run(
                [
                    sys.executable, str(BDF_CONVERTER), str(GB2312_FONT),
                    "--text", text, "--widths", widths,
                    "--cell-height", "24", "--asset-id", "cmp",
                    "--output", str(path),
                ],
                cwd=SKILL_ROOT, text=True, encoding="utf-8",
                env=env, capture_output=True, check=False,
            )
            self.assertEqual(0, done.returncode, done.stderr)
            return json.loads(path.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temp:
            plain = generate(plan["text"], unpadded_widths, Path(temp) / "plain.json")
            padded = generate(
                plan["render_text"], plan["widths_arg"], Path(temp) / "padded.json"
            )

        narrow = plain["width"]
        wide = padded["width"]
        self.assertEqual(narrow + 1, wide)
        plain_bytes = plain["source"]["bytes"]
        padded_bytes = padded["source"]["bytes"]
        for page in range(3):
            kept = padded_bytes[page * wide : page * wide + narrow]
            self.assertEqual(plain_bytes[page * narrow : (page + 1) * narrow], kept)
            self.assertEqual("00H", padded_bytes[page * wide + narrow])


if __name__ == "__main__":
    unittest.main()
