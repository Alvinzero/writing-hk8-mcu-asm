from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL_ROOT / "scripts" / "validate_skill.py"


class ValidateSkillContractTests(unittest.TestCase):
    def run_validator(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.assertTrue(VALIDATOR.exists(), f"validator missing: {VALIDATOR}")
        return subprocess.run(
            [sys.executable, str(VALIDATOR), *args],
            cwd=SKILL_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def payload(self, completed: subprocess.CompletedProcess[str]) -> dict:
        self.assertTrue(completed.stdout.strip(), completed.stderr)
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"stdout must contain one JSON document: {exc}: {completed.stdout!r}")

    def test_current_skill_structure_is_valid(self) -> None:
        result = self.run_validator(str(SKILL_ROOT))
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertEqual("SKILL_VALID", self.payload(result)["code"])

    def test_missing_description_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "SKILL.md").write_text("---\nname: bad-skill\n---\n# Bad\n", encoding="utf-8")
            result = self.run_validator(str(root))
            self.assertNotEqual(0, result.returncode)
            self.assertEqual("SKILL_INVALID", self.payload(result)["code"])

    def test_skill_instructions_and_openai_metadata_are_chinese(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        openai_text = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("description: 用于", skill_text)
        for phrase in ("## 第一条回复", "## 必需输入", "## 闭环命令", "## 硬门禁", "## 安装"):
            self.assertIn(phrase, skill_text)
        for phrase in ("## First Response", "## Required Inputs", "This skill writes"):
            self.assertNotIn(phrase, skill_text)
        for phrase in ("生成", "编译", "芯片型号"):
            self.assertIn(phrase, openai_text)
        self.assertNotIn("Generate HK8 ASM", openai_text)

    def test_public_skill_surfaces_name_only_hk64s825(self) -> None:
        retired_names = ["HK64S8" + suffix for suffix in ("X", "x", "101")]
        public_paths = [
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "agents" / "openai.yaml",
            SKILL_ROOT / "references" / "profiles" / "HK64S825.profile.example.json",
            SKILL_ROOT / "references" / "requests" / "gpio-request.example.json",
        ]
        for path in public_paths:
            self.assertTrue(path.is_file(), f"missing public surface: {path}")
            text = path.read_text(encoding="utf-8")
            self.assertIn("HK64S825", text)
            for retired_name in retired_names:
                self.assertNotIn(retired_name, text)
        retired_profile = SKILL_ROOT / "references" / "profiles" / ("HK64S8" + "X.profile.example.json")
        self.assertFalse(retired_profile.exists())

    def test_skill_uses_hk64s825_rules_without_unrelated_questions(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("选择 `HK64S825` 后", skill_text)
        self.assertIn("LED、OLED、数码管", skill_text)
        self.assertIn("不得追问与当前功能无关的输入", skill_text)

    def test_required_inputs_only_ask_for_task_gaps_not_known_spec_defaults(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        required_section = skill_text.split("## 必需输入", 1)[1].split("## 资源导航", 1)[0]
        for phrase in (
            "资料库已知规则",
            "不得重复追问用户",
            "本次要实现的具体功能",
            "无法从 spec 推断",
            "普通代码生成阶段",
            "open_items",
        ):
            self.assertIn(phrase, required_section)
        for phrase in ("板卡 ID", "烧录器序列号", "可机器观测的验收条件"):
            self.assertNotIn(phrase, required_section)

    def test_release_gate_is_compile_only_with_hardware_verification_deferred(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "静态检查和目标编译通过后即可 release",
            "烧录、回读、逻辑分析仪或其他实板验证暂不作为输出 ASM 的前置条件",
            "close-loop` 只执行静态检查和目标编译",
            "release` 是唯一允许释放已编译 ASM 的命令",
        ):
            self.assertIn(phrase, skill_text)
        self.assertNotIn("受控烧录、回读校验和功能验证全部通过", skill_text)
        release_section = skill_text.split("## Release 后最终回复", 1)[1].split("## 安装", 1)[0]
        self.assertIn("编译器版本", release_section)
        self.assertNotIn("烧录器序列号", release_section)
        self.assertNotIn("回读 hash", release_section)


if __name__ == "__main__":
    unittest.main()
