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

    def skill_text(self) -> str:
        return (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

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

    def test_skill_and_openai_metadata_are_chinese(self) -> None:
        skill_text = self.skill_text()
        openai_text = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("description: 用于", skill_text)
        for phrase in ("## 第一条回复", "## 必需输入", "## 闭环命令", "## 硬门禁", "## 安装"):
            self.assertIn(phrase, skill_text)
        for phrase in ("## First Response", "## Required Inputs", "This skill writes"):
            self.assertNotIn(phrase, skill_text)
        for phrase in ("生成", "编译", "芯片型号"):
            self.assertIn(phrase, openai_text)
        self.assertIn("内置编译模块", openai_text)
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
            text = path.read_text(encoding="utf-8")
            self.assertIn("HK64S825", text)
            for retired_name in retired_names:
                self.assertNotIn(retired_name, text)

    def test_default_compile_path_is_builtin_and_portable(self) -> None:
        skill_text = self.skill_text()
        for phrase in (
            "默认使用 Skill 内置 HK64S825 编译模块",
            "`scripts/builtin_compiler.py`",
            "`builtin-hk64s825-assembler-1`",
            "`$PYTHON`",
            "`$SKILL_ROOT`",
            "不需要用户提供本机 IDE、外部 ASMC 或 HK_ASM_Compiler 路径",
            "禁止扫盘、遍历本机目录或猜测 IDE/CLI 路径",
            "不得使用 Get-ChildItem、os.walk、rglob、where 或全盘搜索",
        ):
            self.assertIn(phrase, skill_text)
        for stale in (
            "requires-local-toolchain-config",
            "当前电脑缺少真实编译 profile/config",
            "未编译草案模式",
            "PROFILE_NOT_READY 只阻止 release",
            "REPLACE_WITH",
        ):
            self.assertNotIn(stale, skill_text)

    def test_external_asmc_adapter_is_optional_only(self) -> None:
        skill_text = self.skill_text()
        for phrase in (
            "`scripts/compiler_adapter.py`",
            "可选外部 ASMC 适配器",
            "`--asmc-cli`",
            "`--compiler-source-root`",
            "`--compiler-mcu-type`",
            "`--tool-version`",
            "只有用户明确要求使用公司官方 ASMC",
        ):
            self.assertIn(phrase, skill_text)

    def test_required_inputs_only_ask_for_task_gaps_as_letter_choices(self) -> None:
        skill_text = self.skill_text()
        required_section = skill_text.split("## 必需输入", 1)[1].split("## 规则读取策略", 1)[0]
        for phrase in (
            "资料库已知规则",
            "用户任务缺口",
            "资料库已经明确的参数不得重复追问用户",
            "本次要实现的具体功能",
            "无法从 spec 推断",
            "A/B/C/D 选择题",
            "用户只需要回复选项字母",
            "一次最多提出 3 个选择题",
            "不确定/我不知道",
        ):
            self.assertIn(phrase, required_section)
        for phrase in ("烧录器序列号", "逻辑分析仪", "供电电压"):
            self.assertNotIn(phrase, required_section)

    def test_generation_rules_prevent_copying_examples_and_heavy_led_init(self) -> None:
        skill_text = self.skill_text()
        for phrase in (
            "禁止复制 templates、example 或 sample ASM 作为候选源码",
            "必须根据当前需求、规则、寄存器和时序重新撰写候选 ASM",
            "简单 LED/GPIO 不得套用端口全量初始化模板",
            "默认只写当前功能必需的 `PIO` 和 `POE`",
            "不得为了显得完整而批量清写 `PPU/PPD/POD/INS/IOS`",
            "`CLRWDT` 要放在忙等循环内部",
        ):
            self.assertIn(phrase, skill_text)

    def test_release_gate_and_final_output_rules(self) -> None:
        skill_text = self.skill_text()
        for phrase in (
            "只有 `release` 返回 `RELEASED` 后，才输出已编译 ASM",
            "失败时只返回诊断，不展示候选 ASM",
            "烧录、回读、逻辑分析仪或其他实板验证暂不作为输出 ASM 的前置条件",
            "不得把仅编译通过描述为实板验证通过",
            "最终 release 的 ASM 中，说明性注释必须使用中文",
            "不得使用英文句子作为 ASM 注释",
        ):
            self.assertIn(phrase, skill_text)


if __name__ == "__main__":
    unittest.main()
