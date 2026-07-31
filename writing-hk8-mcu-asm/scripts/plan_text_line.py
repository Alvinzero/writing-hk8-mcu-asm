#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为一行显示文本规划字宽、偶数补列与居中窗口。

字模按 word 存放，发送器每轮发一次 TABL 再一次 TABH，因此每行字节数必须
为偶数。行宽为奇数时不得加宽字形凑数：bdf_to_ssd1306.py 的
crop_glyph_cell 会拒绝格宽超过字形 DWIDTH 的请求，release 门禁也会用同一
函数逐字形重建比对。本工具改为在行尾追加一列 1 像素空白（U+0020 裁到 1
列，该字形本身无像素），字形零改动。

用法:
    python scripts/plan_text_line.py --text "王浩宇Whys，"
    python scripts/plan_text_line.py --text "你好" --font <bdf路径> --json

输出可直接抄用的 --text 与 --widths 参数、字节数、word 数和居中列范围。
缺字或行宽超过 128 像素时以非零码退出。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = SKILL_ROOT / "references" / "fonts"
DEFAULT_FONTS = [
    FONT_DIR / "wenquanyi_bitmap_song_16px_gb2312.bdf",
    FONT_DIR / "wenquanyi_bitmap_song_16px_ascii_date_cn.bdf",
]
PANEL_COLUMNS = 128
PAD_LABEL = " "
PAD_WIDTH = 1


def load_metrics(path: Path) -> dict:
    """返回 {码位: DWIDTH}，只解析必要字段，不载入点阵。"""
    metrics: dict = {}
    enc = None
    with io.open(path, encoding="latin-1") as fh:
        for raw in fh:
            line = raw.rstrip()
            if line.startswith("ENCODING"):
                parts = line.split()
                enc = int(parts[1]) if len(parts) > 1 else None
            elif line.startswith("DWIDTH") and enc is not None:
                parts = line.split()
                if len(parts) > 1:
                    metrics[enc] = int(parts[1])
                enc = None
    return metrics


def plan_line(text: str, metrics: dict) -> dict:
    """规划一行。返回送给 bdf_to_ssd1306.py 的文本、宽度串与窗口信息。"""
    if not text:
        raise ValueError("行文本不能为空")
    missing = [ch for ch in text if ord(ch) not in metrics]
    if missing:
        raise ValueError(
            "字库缺字: %s\n需从上游 bdf/wenquanyi_12pt.bdf 按 ENCODING 机械提取后并入子集。"
            % "".join(missing)
        )

    widths = [metrics[ord(ch)] for ch in text]
    render_text = text
    padded = False
    if sum(widths) % 2 == 1:
        # 追加 1 像素空列凑偶数，不改任何字形的格宽
        render_text = text + PAD_LABEL
        widths = widths + [PAD_WIDTH]
        padded = True

    total = sum(widths)
    if total > PANEL_COLUMNS:
        raise ValueError(
            "行宽 %d 像素超过 %d，需缩短本行文本或改分行" % (total, PANEL_COLUMNS)
        )

    byte_count = total * 3  # 24 像素高合 3 页
    column_start = (PANEL_COLUMNS - total) // 2
    return {
        "text": text,
        "render_text": render_text,
        "widths": widths,
        "widths_arg": ",".join(str(w) for w in widths),
        "width": total,
        "byte_count": byte_count,
        "words": byte_count // 2,
        "padded": padded,
        "column_start": column_start,
        "column_end": column_start + total - 1,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="规划一行显示文本的字宽、偶数补列与居中窗口。",
    )
    parser.add_argument("--text", required=True, help="本行要显示的文本")
    parser.add_argument(
        "--font", type=Path, action="append",
        help="指定 BDF；可重复。缺省依次查 GB2312 子集与 103 字子集",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    fonts = args.font if args.font else DEFAULT_FONTS

    metrics: dict = {}
    for path in reversed(fonts):
        if not path.is_file():
            print("字库不存在: %s" % path)
            return 2
        metrics.update(load_metrics(path))

    try:
        plan = plan_line(args.text, metrics)
    except ValueError as exc:
        print(str(exc))
        return 1

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=1))
        return 0

    print("文本      %s" % plan["text"])
    print("行宽      %d 像素" % plan["width"])
    print("字节      %d，合 %d 个 word" % (plan["byte_count"], plan["words"]))
    if plan["padded"]:
        print("偶数补列  行尾追加 1 像素空列，字形未改动")
    else:
        print("偶数补列  不需要")
    print("居中窗口  列 %d..%d" % (plan["column_start"], plan["column_end"]))
    print()
    print("传给 bdf_to_ssd1306.py：")
    print('  --text "%s" --widths "%s" --cell-height 24'
          % (plan["render_text"], plan["widths_arg"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
