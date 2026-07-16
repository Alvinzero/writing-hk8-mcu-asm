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

    def test_unresolved_inputs_are_presented_as_letter_choices(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        required_section = skill_text.split("## 必需输入", 1)[1].split("## 资源导航", 1)[0]
        for phrase in (
            "缺口问题必须以 A/B/C/D 选择题呈现",
            "用户只需要回复选项字母",
            "一次最多提出 3 个选择题",
            "默认或推荐选项必须标注“推荐”",
            "必须包含“不确定/我不知道”选项",
            "不得要求用户自由填写一长串板级参数",
        ):
            self.assertIn(phrase, required_section)

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

    def test_skill_forbids_copying_examples_and_uses_fast_relevant_rule_loading(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "禁止复制 templates、example 或 sample ASM 作为候选源码",
            "示例文件只作反例或格式参考，不进入生成上下文",
            "简单 LED/GPIO 任务使用快速路径",
            "只读取当前任务相关规则",
            "不得加载无关 OLED、数码管或分析快照资料",
        ):
            self.assertIn(phrase, skill_text)
        self.assertNotIn("references/spec/templates/", skill_text)

    def test_skill_forbids_local_disk_scans_for_compiler_discovery(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "编译器路径必须来自 profile、config 或 spec 明确配置",
            "禁止扫盘、遍历本机目录或猜测 IDE/CLI 路径",
            "不得使用 Get-ChildItem、os.walk、rglob、where 或全盘搜索寻找编译器",
        ):
            self.assertIn(phrase, skill_text)

    def test_released_asm_comments_must_be_chinese(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "最终 release 的 ASM 中，说明性注释必须使用中文",
            "寄存器名、指令名、标号、宏名、文件名和英文专有名词可以原样保留",
            "不得使用英文句子作为 ASM 注释",
        ):
            self.assertIn(phrase, skill_text)

    def test_oled_tasks_require_project_experience_and_visible_fill_contract(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        oled_spec = (
            SKILL_ROOT / "references" / "spec" / "05-GPIO-I2C-OLED驱动规范.md"
        ).read_text(encoding="utf-8")

        for phrase in (
            "## OLED 任务硬门禁",
            "当用户要求依据 `D:\\hk64s8x-cli` 项目经验",
            "生成候选 ASM 前先写 OLED 契约测试",
            "不得只用 `A5H/AFH` 或裸 `AFH/AEH` 证明亮灭",
            "先写入 1024 字节 `0xFF` 到 GDDRAM",
            "`PB_PPU/PB_POD/PB_INS/PB_PIO/PB_POE`",
            "ACK 后必须立即检查",
            "`I2C_DELAY` 不得退回 2 个 `NOP`",
            "旧芯片型号",
        ):
            self.assertIn(phrase, skill_text)

        for phrase in (
            "PB6/PB7 SSD1306 安全基线",
            "MOV PB_POD,A",
            "MOV PB_INS,A",
            "先预装 `PB_PIO=0xC0`，最后再 `PB_POE=0xC0`",
        ):
            self.assertIn(phrase, oled_spec)

    def test_led_gpio_generation_requires_minimal_init_and_wdt_safe_delays(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        gpio_spec = (
            SKILL_ROOT / "references" / "spec" / "05-GPIO-I2C-OLED驱动规范.md"
        ).read_text(encoding="utf-8")

        for phrase in (
            "LED/GPIO 通用硬门禁",
            "简单 LED/GPIO 不得套用端口全量初始化模板",
            "默认只写当前功能必需的 `PIO` 和 `POE`",
            "不得为了显得完整而批量清写 `PPU/PPD/POD/INS/IOS`",
            "WDT 未明确关闭时，任何可见延时、长忙等或周期循环必须插入 `CLRWDT`",
            "先判断任务需要哪些电气属性，再决定写哪些寄存器",
        ):
            self.assertIn(phrase, skill_text)

        for phrase in (
            "简单 LED/GPIO 最小初始化原则",
            "不要从模板惯性写完整端口初始化序列",
            "只有 board profile 明确要求上拉、下拉、开漏、输入通道或特殊功能选择时",
            "长延时与 WDT",
            "`CLRWDT` 必须放在忙等循环内部",
        ):
            self.assertIn(phrase, gpio_spec)

    def test_skill_explains_real_adapter_wrapper_and_external_toolchain_boundary(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "资料包内置 `scripts/compiler_adapter.py` 作为真实 ASMC wrapper",
            "资料包不内置公司 `HK_ASM_Compiler` 源码、`D:\\hk64s8x-cli` 项目或本机工具链路径",
            "`asm_static_check.py` 只是静态检查器，不是编译器",
            "`fake_adapter.py` 只能用于自动化测试，不能用于 release",
            "配置中出现 `REPLACE_WITH` 占位符时必须停止",
            "`--compiler-mcu-type` 是公司编译器源码实际接受的工程型号",
        ):
            self.assertIn(phrase, skill_text)

    def test_missing_local_toolchain_config_reports_exact_file_locations(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "PROFILE_NOT_READY",
            "`references/profiles/HK64S825.profile.example.json`",
            "`references/configs/local-adapter.example.json`",
            "复制为工作文件，例如 `profiles/local-HK64S825.profile.json` 和 `configs/local-adapter.json`",
            "不得直接把 `.example.json` 当作 release 配置",
            "`<ABSOLUTE_SKILL_ROOT>\\scripts\\compiler_adapter.py`",
            "`--asmc-cli`",
            "`--compiler-source-root`",
            "`--compiler-mcu-type`",
            "`--tool-version`",
            "python scripts/hk8asm.py doctor --profile profiles/local-HK64S825.profile.json --config configs/local-adapter.json",
        ):
            self.assertIn(phrase, skill_text)

    def test_profile_not_ready_allows_uncompiled_draft_without_release_claims(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "PROFILE_NOT_READY 只阻止 release，不阻止未编译草案",
            "用户已明确选择“先给草案”",
            "未编译、未 release、不可烧录、不可量产",
            "不得把草案称为正确 ASM、编译通过、已验证或 release",
            "不得伪造 run ID、source hash、artifact hash 或 evidence hash",
            "真实 profile/config 就绪后，必须重新执行 `doctor -> new-run -> close-loop -> release`",
        ):
            self.assertIn(phrase, skill_text)


if __name__ == "__main__":
    unittest.main()
