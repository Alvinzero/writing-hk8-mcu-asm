from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = SKILL_ROOT / "scripts" / "validate_skill.py"
SPEC_ROOT = SKILL_ROOT / "references" / "spec"
PORTABLE_RUNTIME_SCRIPTS = [
    SKILL_ROOT / "scripts" / "hk8asm.py",
    SKILL_ROOT / "scripts" / "builtin_compiler.py",
    SKILL_ROOT / "scripts" / "compiler_adapter.py",
    SKILL_ROOT / "scripts" / "install.py",
    SKILL_ROOT / "scripts" / "validate_skill.py",
    SPEC_ROOT / "tools" / "asm_static_check.py",
    SPEC_ROOT / "tools" / "asm_semantic_gates.py",
    SPEC_ROOT / "tools" / "validate_spec.py",
]


class ValidateSkillContractTests(unittest.TestCase):
    def run_validator(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.assertTrue(VALIDATOR.exists(), f"validator missing: {VALIDATOR}")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, str(VALIDATOR), *args],
            cwd=SKILL_ROOT,
            text=True,
            encoding="utf-8",
            env=env,
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

    def spec_text(self, relative_path: str) -> str:
        return (SPEC_ROOT / relative_path).read_text(encoding="utf-8")

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
            SKILL_ROOT / "references" / "profiles" / "HK64S825.profile.json",
            SKILL_ROOT / "references" / "profiles" / "HK64S825.profile.example.json",
            SKILL_ROOT / "references" / "requests" / "gpio-request.example.json",
        ]
        for path in public_paths:
            text = path.read_text(encoding="utf-8")
            self.assertIn("HK64S825", text)
            for retired_name in retired_names:
                self.assertNotIn(retired_name, text)
        for profile_path in (
            SKILL_ROOT / "references" / "profiles" / "HK64S825.profile.json",
            SKILL_ROOT / "references" / "profiles" / "HK64S825.profile.example.json",
        ):
            with self.subTest(profile=profile_path.name):
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                self.assertEqual([], profile["aliases"])

    def test_packaged_text_uses_only_hk64s825_model_name(self) -> None:
        retired_chip = "HK64S8" + "101"
        retired_short = "S" + "8101"
        duplicate_name = "HK64S825/" + "HK64S825"
        for path in SKILL_ROOT.rglob("*"):
            if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            with self.subTest(path=path.relative_to(SKILL_ROOT).as_posix()):
                self.assertNotIn(retired_chip, text)
                self.assertNotIn(retired_short, text)
                self.assertNotIn(duplicate_name, text)

    def test_validator_json_remains_utf8_under_windows_locale(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "SKILL.md").write_text(
                "---\nname: bad-skill\n---\n# Bad\n", encoding="utf-8"
            )
            result = self.run_validator(str(root))
            payload = self.payload(result)
            self.assertEqual("SKILL_INVALID", payload["code"])
            self.assertTrue(
                any("用于" in finding for finding in payload["findings"]), payload
            )

    def test_public_documentation_has_no_retired_local_windows_paths(self) -> None:
        paths = (SKILL_ROOT / "SKILL.md", *SPEC_ROOT.rglob("*.md"))
        for path in paths:
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                for stale in ("D:/spec", "D:\\spec", "D:/path", "D:\\path", "D:/hk64s8x"):
                    self.assertNotIn(stale, text)

    def test_portable_runtime_supports_python37_without_newer_path_apis(self) -> None:
        skill_text = self.skill_text()
        self.assertIn("Python 3.7+", skill_text)
        self.assertNotIn("Python 3.8+", skill_text)
        self.assertNotIn("Python 3.10+", skill_text)
        for path in PORTABLE_RUNTIME_SCRIPTS:
            with self.subTest(path=path.relative_to(SKILL_ROOT).as_posix()):
                source = path.read_text(encoding="utf-8")
                ast.parse(source, filename=str(path), feature_version=(3, 7))
                self.assertNotIn(":=", source)
                self.assertNotIn(".is_relative_to(", source)
                self.assertNotIn("missing_ok=", source)

    def test_default_compile_path_is_builtin_and_portable(self) -> None:
        skill_text = self.skill_text()
        for phrase in (
            "默认使用 Skill 内置 HK64S825 编译模块",
            "`scripts/builtin_compiler.py`",
            "`builtin-hk64s825-assembler-2`",
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

    def test_release_claims_distinguish_builtin_from_company_assembler(self) -> None:
        skill_text = self.skill_text()
        for phrase in (
            "内置编译 release 只证明源码通过当前 Skill 内置编译器",
            "不得宣称公司编译器兼容",
            "不得使用 `company compatible`",
            "公司编译器交叉验证",
        ):
            self.assertIn(phrase, skill_text)

    def test_default_profile_and_config_use_canonical_non_example_paths(self) -> None:
        skill_text = self.skill_text()
        profile = SKILL_ROOT / "references" / "profiles" / "HK64S825.profile.json"
        config = SKILL_ROOT / "references" / "configs" / "builtin-config.json"
        self.assertTrue(profile.is_file(), profile)
        self.assertTrue(config.is_file(), config)
        self.assertIn("references/profiles/HK64S825.profile.json", skill_text)
        self.assertIn("references/configs/builtin-config.json", skill_text)
        self.assertNotIn("profile.example.json", skill_text)
        self.assertNotIn("local-adapter.example.json", skill_text)

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

    def test_explicit_hk64s825_requests_skip_confirmation_only_reply(self) -> None:
        skill_text = self.skill_text()
        first_reply_section = skill_text.split("## 第一条回复", 1)[1].split("## 必需输入", 1)[0]
        openai_text = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        evals = json.loads((SKILL_ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))
        cases = {case["id"]: case for case in evals["cases"]}

        for phrase in (
            "若用户请求已经明确包含 `HK64S825`",
            "不得再要求用户回复“是/否”或重复确认型号",
            "直接进入需求解析、规则读取、候选生成、静态检查、编译和 release",
        ):
            self.assertIn(phrase, first_reply_section)
        self.assertNotIn("每次调用本 Skill 后，第一条回复必须先询问", first_reply_section)
        self.assertNotIn("第一条回复先确认芯片型号", openai_text)

        case = cases["explicit-hk64s825-oled-direct-run"]
        self.assertIn("HK64S825 ASM 闭环 写一个 OLED 亮屏代码", case["query"])
        behavior = "\n".join(case["expected_behavior"])
        self.assertIn("不要求用户回复是/否", behavior)
        self.assertIn("直接进入 OLED 亮屏生成、静态检查、编译和 release", behavior)

    def test_oled_i2c_electrical_questions_are_resolved_before_generation(self) -> None:
        skill_text = self.skill_text()
        required_section = skill_text.split("## 必需输入", 1)[1].split(
            "## 规则读取策略", 1
        )[0]
        for phrase in (
            "创建候选源码前必须确认 SDA、SCL 各自是否配置 `POD`",
            "候选源码前必须确认 I2C 上拉来源",
            "PB7（SDA）是否设置 POD",
            "PB6（SCL）是否设置 POD",
            "I2C 上拉来源是什么",
            "不得先生成候选、运行静态检查或编译后，再以 POD 或上拉缺口为由中止",
        ):
            self.assertIn(phrase, required_section)

    def test_generation_rules_prevent_copying_examples_and_heavy_led_init(self) -> None:
        skill_text = self.skill_text()
        for phrase in (
            "禁止复制 templates、example 或 sample ASM 作为候选源码",
            "必须根据当前需求、规则、寄存器和时序重新撰写候选 ASM",
            "简单 LED/GPIO 不得套用端口全量初始化模板",
            "最小初始化是最少但足以建立确定电气状态的操作",
            "推挽输出必须显式清除目标 `POD` 位",
            "开漏输出必须显式置位目标 `POD` 位",
            "先预装安全 `PIO`，最后开启 `POE`",
            "不得把 `DECSZ` 当作写回计数寄存器的倒计数指令",
            "精确延时必须从 OSC、SCK_PS 和实际 SCK 推导",
            "未使用的业务 `EQU` 必须删除或真正引用",
            "不得批量清写无关 `PPU/PPD/INS/IOS/PSL`",
            "`CLRWDT` 要放在忙等循环内部",
        ):
            self.assertIn(phrase, skill_text)
        self.assertNotIn("默认只写当前功能必需的 `PIO` 和 `POE`", skill_text)

    def test_oled_realboard_bringup_lessons_are_generalized(self) -> None:
        skill_text = self.skill_text()
        oled_spec = self.spec_text("05-GPIO-I2C-OLED驱动规范.md")
        pitfall_spec = self.spec_text("08-踩坑案例与症状诊断手册.md")
        agent_spec = self.spec_text("AGENTS.md")
        combined = "\n".join((skill_text, oled_spec, pitfall_spec, agent_spec))
        for phrase in (
            "已验证最小初始化",
            "`PB_PPU`、`PB_POE`、`PB_PIO`",
            "`PB_POD/PB_INS/PB_PPD/PB_PSL`",
            "ACK 采样必须读 `PB_INS`",
            "不得读 `PB_PIO`",
            "PB_PIO 可能是输出锁存",
            "上电稳定延时",
            "`BTSZ R,b` 是 bit=0 跳过下一条",
            "低字节 `00H` 配合高计数 `04H`",
        ):
            self.assertIn(phrase, combined)
        self.assertNotIn(
            "PB6/PB7 初始化必须覆盖 `PB_PPU/PB_POD/PB_INS/PB_PIO/PB_POE`",
            skill_text,
        )
        self.assertIn("MOV A,PB_INS", oled_spec)
        self.assertNotIn("MOV A,PB_PIO\nAND A,#80H", oled_spec)
        self.assertIn("MOV A,PB_INS", pitfall_spec)
        self.assertNotIn("MOV A,PB_PIO\nAND A,#80H", pitfall_spec)

    def test_oled_page_table_order_is_an_explicit_hard_gate(self) -> None:
        skill_text = self.skill_text()
        oled_spec = self.spec_text("05-GPIO-I2C-OLED驱动规范.md")
        combined = "\n".join((skill_text, oled_spec))
        for phrase in (
            "SSD1306 128x64",
            "7-bit 地址 `3CH`",
            "写地址 `78H`",
            "命令模式控制字节 `00H`",
            "数据模式控制字节 `40H`",
            "正常显示命令 `A6H`",
            "bit0 是该 page 顶部像素",
            "bit7 是该 page 底部像素",
            "禁止把字模按普通横向行扫描直接发送",
            "先发送 page0 的第 1 个字 16 列",
            "再发送 page0 的第 2 个字 16 列",
            "再发送 page1 的第 1 个字 16 列",
            "再发送 page1 的第 2 个字 16 列",
            "for page in pages",
            "for glyph_or_image_block in row",
            "for col in width",
        ):
            self.assertIn(phrase, combined)

    def test_current_oled_board_uses_realboard_corrected_orientation(self) -> None:
        skill_text = self.skill_text()
        oled_spec = self.spec_text("05-GPIO-I2C-OLED驱动规范.md")
        combined = "\n".join((skill_text, oled_spec))
        self.assertIn("当前板验证方向为 `A0H + C0H`", combined)
        self.assertIn("修正上下左右镜像", combined)
        self.assertIn("换板时必须重新确认显示方向", combined)

    def test_oled_machine_rules_cover_realboard_regressions(self) -> None:
        rules = json.loads(
            (SPEC_ROOT / "rules" / "asm-rules.json").read_text(encoding="utf-8")
        )
        by_id = {item["rule_id"]: item for item in rules["rules"]}
        for rule_id in ("HK-I2C-005", "HK-I2C-006", "HK-OLED-005"):
            self.assertIn(rule_id, by_id)
            self.assertEqual("BLOCKER", by_id[rule_id]["severity"])
        self.assertIn("PB_INS", by_id["HK-I2C-002"]["good_example"])
        self.assertIn("PB_PIO", by_id["HK-I2C-005"]["bad_example"])
        self.assertIn("BTSZ", by_id["HK-I2C-006"]["requirement"])
        self.assertIn("上电稳定延时", by_id["HK-OLED-005"]["requirement"])

    def test_simple_tasks_use_targeted_reference_lookup_without_extra_artifacts(self) -> None:
        skill_text = self.skill_text()
        for phrase in (
            "不得把大型规则 JSON 整份载入上下文",
            "只检索候选源码实际使用的 mnemonic、SFR、rule ID 和当前功能章节",
            "简单任务不创建设计文档、计划文档、probe 工程或额外说明文件",
            "一次完成需求解析、候选生成、静态检查、编译和 release",
        ):
            self.assertIn(phrase, skill_text)

    def test_forward_evaluations_cover_semantic_gpio_loop_clock_and_equ_rules(self) -> None:
        evals = json.loads((SKILL_ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))
        cases = {case["id"]: case for case in evals["cases"]}
        expected_by_case = {
            "push-pull-explicit-pod": ("PA_POD", "安全 PA_PIO", "PA_POE", "保留"),
            "persistent-counter-writeback": ("DECSZR", "DECSZ", "写回", "退出"),
            "derive-sck-from-osc-and-divider": ("16 MHz", "SCK_PS=34H", "2 MHz", "cycles"),
            "remove-unused-business-equ": ("EQU", "真实引用", "删除", "魔数"),
            "avoid-unrelated-port-initialization": ("PA2", "POD", "PIO", "POE", "不破坏"),
            "compile-release-without-hardware": (
                "内置编译器",
                "release",
                "烧录",
                "不把它们作为交付前置条件",
            ),
            "oled-full-on-realboard-minimal": (
                "HK64S825",
                "PB_INS",
                "PB_PPU/PB_POE/PB_PIO",
                "DELAY_100MS",
                "BTSZ",
                "1024",
            ),
        }
        for case_id, expected_phrases in expected_by_case.items():
            with self.subTest(case_id=case_id):
                self.assertIn(case_id, cases)
                behavior = "\n".join(cases[case_id]["expected_behavior"])
                for phrase in expected_phrases:
                    self.assertIn(phrase, behavior)
                self.assertNotIn("先完成烧录、回读和功能验证", behavior)

    def test_baseline_records_supplied_led_failure_without_becoming_a_template(self) -> None:
        baseline = json.loads(
            (SKILL_ROOT / "evals" / "baseline.json").read_text(encoding="utf-8")
        )
        cases = {case["id"]: case for case in baseline["cases"]}
        case = cases["supplied-led-semantic-failures"]
        self.assertTrue(case["failure_observed"])
        self.assertIn("PA_POD", case["failure_reason"])
        self.assertIn("DECSZ", case["failure_reason"])
        self.assertIn("SCK_PS", case["failure_reason"])
        self.assertNotIn("source", case)

    def test_reference_workflow_keeps_builtin_compile_release_self_contained(self) -> None:
        paths = (
            SKILL_ROOT / "references" / "spec" / "07-构建-烧录-验收规范.md",
            SKILL_ROOT / "references" / "spec" / "09-AI智能体生成与审查协议.md",
            SKILL_ROOT / "references" / "spec" / "AGENTS.md",
            SKILL_ROOT / "references" / "spec" / "checklists" / "pre-generation.md",
        )
        text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for phrase in (
            "默认使用 Skill 内置编译器",
            "编译 release 不要求烧录、回读或实板验收",
            "PinContract 只在任务使用 GPIO 时要求",
            "ClockContract 只在任务依赖时序时要求",
        ):
            self.assertIn(phrase, text)
        for stale in ("D:/hk64s8x-cli", "D:\\hk64s8x-cli", "先完成烧录、回读和功能验证"):
            self.assertNotIn(stale, text)

    def test_db_workflow_blocks_only_the_retired_python_cli(self) -> None:
        document_paths = (
            "04-程序布局-ORG-查表规范.md",
            "07-构建-烧录-验收规范.md",
            "09-AI智能体生成与审查协议.md",
            "checklists/pre-generation.md",
            "checklists/pre-build.md",
        )
        for relative_path in document_paths:
            with self.subTest(relative_path=relative_path):
                text = self.spec_text(relative_path)
                self.assertIn("`python_source_module_cli`", text)
                self.assertIn("`builtin_compiler`", text)
                self.assertIn("可完成编译 release", text)
                for stale in (
                    "若会使用 `DB`，toolchain 为 `company_ide`",
                    "含 DB 时 target toolchain 为 `company_ide`",
                    "DB 项目使用 company IDE",
                    "含 DB 时使用 company IDE",
                    "含 DB 的交付件必须由已证明支持 DB 的 company IDE 构建",
                ):
                    self.assertNotIn(stale, text)

        for relative_path in document_paths[:3]:
            with self.subTest(company_ide_is_optional_in=relative_path):
                text = self.spec_text(relative_path)
                self.assertIn("用户明确要求", text)
                self.assertTrue("company IDE" in text or "`company_ide`" in text, text)

        for relative_path in document_paths[3:]:
            with self.subTest(company_ide_is_optional_in=relative_path):
                text = self.spec_text(relative_path)
                self.assertIn("company IDE", text)
                self.assertIn("用户明确要求", text)

        rules = json.loads(
            (SPEC_ROOT / "rules" / "asm-rules.json").read_text(encoding="utf-8")
        )
        rule = next(item for item in rules["rules"] if item["rule_id"] == "HK-TOOLCHAIN-DB-001")
        self.assertEqual(["python_source_module_cli"], rule["toolchain_applicability"])
        self.assertIn("builtin_compiler", rule["requirement"])

    def test_seven_segment_initialization_sets_drive_then_safe_latch_then_output_enable(self) -> None:
        text = self.spec_text("06-数码管动态扫描规范.md")
        initialization = text.split("## 2. 初始化", 1)[1].split("## 3.", 1)[0]
        for port in ("PA", "PB"):
            with self.subTest(port=port):
                pod = initialization.index(f"{port}_POD")
                pio = initialization.index(f"{port}_PIO")
                poe = initialization.index(f"{port}_POE")
                self.assertLess(pod, pio)
                self.assertLess(pio, poe)
        self.assertIn("目标 `POD` -> 安全 `PIO` -> 最后开启 `POE`", initialization)
        self.assertIn("保留非本任务位", initialization)

    def test_oled_and_seven_segment_compile_release_checklists_keep_hardware_optional(self) -> None:
        documents = (
            ("05-GPIO-I2C-OLED驱动规范.md", "## 15. 交付清单"),
            ("06-数码管动态扫描规范.md", "## 13. 交付清单"),
        )
        for relative_path, delivery_heading in documents:
            with self.subTest(relative_path=relative_path):
                delivery = self.spec_text(relative_path).split(delivery_heading, 1)[1]
                optional_heading = "### 后续硬件验收（仅用户明确要求时）"
                self.assertIn(optional_heading, delivery)
                compile_release, optional_hardware = delivery.split(
                    optional_heading, 1
                )
                self.assertIn("### 普通编译 release（必需）", compile_release)
                for hardware_term in ("逻辑分析仪", "示波器", "烧录", "实板"):
                    self.assertNotIn(hardware_term, compile_release)
                self.assertIn("烧录", optional_hardware)
                self.assertIn("实板", optional_hardware)
                self.assertIn("镜像 hash 与 release 证据一致", optional_hardware)
                self.assertTrue(
                    "逻辑分析仪" in optional_hardware or "示波器" in optional_hardware,
                    optional_hardware,
                )

    def test_fast_path_uses_targeted_queries_instead_of_loading_large_rule_files(self) -> None:
        document_paths = (
            "AGENTS.md",
            "09-AI智能体生成与审查协议.md",
            "checklists/pre-generation.md",
        )
        for relative_path in document_paths:
            with self.subTest(relative_path=relative_path):
                text = self.spec_text(relative_path)
                self.assertIn("mnemonic、SFR、rule ID 和当前功能章节", text)
                self.assertIn("不得整份加载约 892 KB 的 `register-reference.json`", text)
                self.assertNotIn("读取 `asm-rules.json` 全部", text)

    def test_representative_gpio_eval_cannot_satisfy_other_semantic_cases(self) -> None:
        evals = json.loads((SKILL_ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))
        cases = {case["id"]: case for case in evals["cases"]}
        push_pull = "\n".join(cases["push-pull-explicit-pod"]["expected_behavior"])
        for unrelated in ("DECSZR", "SCK_PS=34H", "EQU"):
            self.assertNotIn(unrelated, push_pull)

    def test_release_state_is_distinct_from_optional_hardware_states(self) -> None:
        build_spec = self.spec_text("07-构建-烧录-验收规范.md")
        agent_spec = self.spec_text("09-AI智能体生成与审查协议.md")
        for relative_path, text in (
            ("07-构建-烧录-验收规范.md", build_spec),
            ("09-AI智能体生成与审查协议.md", agent_spec),
        ):
            with self.subTest(relative_path=relative_path):
                self.assertIn("`released`", text)
                self.assertIn("用户明确要求", text)
                self.assertIn("hardware_verified", text)
        self.assertIn("buildable -> released", build_spec)
        self.assertIn('"status": "draft|buildable|released|flash_candidate|hardware_verified"', agent_spec)
        coding_spec = self.spec_text("01-HK64S825-ASM编码规范.md")
        self.assertNotIn("hardware acceptance required", coding_spec)

    def test_spec_surfaces_report_82_machine_rules(self) -> None:
        document_paths = (
            "README.md",
            "09-AI智能体生成与审查协议.md",
            "tools/README.md",
        )
        for relative_path in document_paths:
            with self.subTest(relative_path=relative_path):
                text = self.spec_text(relative_path)
                self.assertIn("82 条", text)
                self.assertNotIn("79 条", text)
                self.assertNotIn("78 条", text)
                self.assertNotIn("70 条", text)
        evidence_index = self.spec_text("10-证据索引与待确认事项.md")
        self.assertIn("| 规则数 | 82 |", evidence_index)
        self.assertNotIn("| 规则数 | 79 |", evidence_index)
        self.assertNotIn("| 规则数 | 78 |", evidence_index)
        self.assertNotIn("| 规则数 | 70 |", evidence_index)

    def test_all_spec_docs_avoid_retired_db_toolchain_requirements(self) -> None:
        stale_phrases = (
            "DB 项目使用 company IDE",
            "含 DB 时使用 company IDE",
            "有 DB 时公司 IDE 构建",
            "含 DB 时 target toolchain 为 `company_ide`",
            "若会使用 `DB`，toolchain 为 `company_ide`",
        )
        for path in SPEC_ROOT.rglob("*.md"):
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                for stale in stale_phrases:
                    self.assertNotIn(stale, text)

    def test_bundled_gpio_request_is_ready_for_compile_only_default(self) -> None:
        request = json.loads(
            (SKILL_ROOT / "references" / "requests" / "gpio-request.example.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual({"id": "HK64S825-DEFAULT"}, request["board"])
        self.assertEqual([], request["acceptance"])
        self.assertNotIn("REPLACE_WITH", json.dumps(request, ensure_ascii=False))

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
