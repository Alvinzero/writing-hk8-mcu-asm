# HK64S825 ASM Semantic Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 HK64S825 Skill 在 release 前自动阻止 GPIO 推挽模式遗漏、无用 `EQU`、`DECSZ/INCSZ` 无写回循环，以及按 OSC 而非实际 SCK 计算的错误延时。

**Architecture:** 保留 `builtin_compiler.py` 作为指令编码器，在 `asm_static_check.py` 前端解析的源码模型之上新增纯 Python 语义门禁模块。`hk8asm.py` 将隔离运行目录中的 request/profile 一并传入静态检查器，使 GPIO、时钟和延时从用户契约一路绑定到 release evidence。

**Tech Stack:** Python 3.7+ 标准库、`unittest`、JSON、Agent Skills Markdown、PowerShell、Git。

---

## Execution environment

在当前 Windows 工作区使用：

```powershell
$PYTHON = 'C:\Users\Admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$REPO = 'C:\Users\Admin\Documents\Skills制作\writing-hk8-mcu-asm'
Set-Location $REPO
```

## File responsibility map

- Create: `references/spec/tools/asm_semantic_gates.py` — request/profile 契约解析、GPIO 位效果、指令副作用、循环进展和延时周期审计。
- Create: `references/spec/tools/tests/test_asm_semantic_gates.py` — 纯函数和周期解释器单元测试。
- Modify: `references/spec/tools/asm_static_check.py` — 源码模型收集、request/profile CLI 参数、调用语义门禁并输出 evidence。
- Modify: `references/spec/tools/tests/test_asm_static_check.py` — 源码级语义门禁回归测试。
- Modify: `scripts/hk8asm.py` — 新契约校验、向静态检查器传递隔离 request/profile、保留审计摘要。
- Modify: `tests/test_cli_contract.py` — `new-run -> close-loop -> release` 集成门禁测试。
- Modify: `references/profiles/HK64S825.profile.example.json` — 内置 SCK_PS 时钟模型。
- Modify: `references/requests/gpio-request.example.json` — 结构化 PinContract、ClockContract、TimingContract。
- Modify: `references/spec/rules/asm-rules.json` — 注册 8 条现有/新增自动规则。
- Modify: `references/spec/rules/asm-rules.schema.json` — 允许 `builtin_compiler` 工具链。
- Modify: `references/spec/tools/validate_spec.py` and `references/spec/tools/tests/test_validate_spec.py` — 规则数量和 checker rule-ID 覆盖校验。
- Modify: `SKILL.md`, `agents/openai.yaml`, `evals/evals.json`, `evals/baseline.json` — 生成行为、前向场景和触发描述。
- Modify: `references/spec/01-HK64S825-ASM编码规范.md`, `02-指令与操作数规范.md`, `03-寄存器与内存使用规范.md`, `05-GPIO-I2C-OLED驱动规范.md`, `08-踩坑案例与症状诊断手册.md`, `09-AI智能体生成与审查协议.md`, `checklists/pre-generation.md`, `checklists/pre-build.md` — 同步规范源。

## Task 1: Register semantic rule IDs and validate checker references

**Files:**
- Modify: `references/spec/rules/asm-rules.json`
- Modify: `references/spec/rules/asm-rules.schema.json`
- Modify: `references/spec/tools/validate_spec.py:214-255`
- Modify: `references/spec/tools/tests/test_validate_spec.py:20-45`

- [ ] **Step 1: Write the failing rule-count and unknown-ID tests**

Change the current rule count assertion and add a copied-package mutation test:

```python
def test_current_spec_package_validates(self):
    completed, payload = self.run_validator(SPEC)
    self.assertEqual(completed.returncode, 0, payload)
    self.assertEqual(payload["summary"]["errors"], 0)
    self.assertEqual(payload["checks"]["rule_count"], 78)

def test_checker_cannot_emit_unregistered_rule_id(self):
    with tempfile.TemporaryDirectory() as tmp:
        copied = self.copy_spec(Path(tmp))
        checker = copied / "tools" / "asm_static_check.py"
        checker.write_text(
            checker.read_text(encoding="utf-8")
            + '\n# make_finding("HK-UNREGISTERED-999", "ERROR", "x", 1, "e", "r", "f")\n',
            encoding="utf-8",
        )
        completed, payload = self.run_validator(copied)
    self.assertEqual(completed.returncode, 2)
    self.assertIn("checker-rule-id", {item["code"] for item in payload["findings"]})
```

- [ ] **Step 2: Run the validator test and verify RED**

Run:

```powershell
& $PYTHON -m unittest references.spec.tools.tests.test_validate_spec -v
```

Expected: FAIL because the package still has 70 rules and `validate_spec.py` does not inspect checker finding IDs.

- [ ] **Step 3: Register all eight rules**

Append complete rule objects using the existing schema for:

```text
HK-WDT-001       delay/wait routine must service WDT or document WDT off
HK-GPIO-INIT-001 simple GPIO must not sweep unrelated configuration registers
HK-GPIO-002      output pins must establish POD, safe PIO, then POE from PinContract
HK-SYN-012       instruction write-back destination must match loop-carried state
HK-SYN-013       business EQU definitions must be referenced or removed
HK-CLOCK-001     precise timing must distinguish OSC, SCK_PS and SCK
HK-TIME-001      audited delay cycles must meet target tolerance
HK-WDT-002       CLRWDT must not conceal a non-progressing delay loop
```

Use these common fields for each new rule, changing title/requirement/examples to the behavior above:

```json
{
  "normative_level": "MUST",
  "severity": "ERROR",
  "status": "active",
  "scope": ["generation", "static_check"],
  "verification": ["asm_static_check.py", "automated regression test"],
  "evidence": [
    {
      "level": "E4",
      "source": "tools/tests/test_asm_static_check.py",
      "note": "自动负例与正例"
    }
  ],
  "confidence": "high",
  "toolchain_applicability": ["company_ide", "python_source_module_cli", "builtin_compiler"],
  "tags": ["ai", "static-check"]
}
```

Use these exact per-rule values:

| rule_id | title | severity | requirement | bad_example | good_example |
|---|---|---|---|---|---|
| `HK-WDT-001` | 延时循环必须处理看门狗 | BLOCKER | WDT 未明确关闭时，DELAY/WAIT 忙等必须在循环内部按有界间隔执行 CLRWDT；若 WDT 已关闭，文件头必须给出配置依据。 | 延时函数没有 CLRWDT 且只写“WDT 未知”。 | 在最内层忙等中 CLRWDT，或声明经批准的 WDT-off 配置。 |
| `HK-GPIO-INIT-001` | 简单 GPIO 禁止全量初始化惯性 | WARNING | 简单 LED/GPIO 只能配置 PinContract 需要的寄存器，不得无依据遍历 PPU/PPD/POD/INS/IOS/PSL。 | 为单个 LED 批量清写六类端口配置寄存器。 | 只配置目标 POD/PIO/POE 及契约明确要求的附加模式。 |
| `HK-GPIO-002` | 输出 pin 必须显式建立电气模式 | BLOCKER | 每个输出 pin 必须按 PinContract 显式设置 POD，预装安全 PIO，并最后开启 POE；共享端口必须保留非本任务位。 | 声明推挽输出但完全不写 POD，或先开 POE 后写 PIO。 | 清目标 POD 位，预装 off 电平，再置目标 POE 位。 |
| `HK-SYN-012` | 循环状态必须写回正确目标 | BLOCKER | 用作 loop-carried 状态的指令结果必须写回该状态；不得把只写 A 的 DECSZ/INCSZ 当作 SRAM 原位计数器。 | `DECSZ 80H` 后向后跳转并期待 80H 自减。 | 使用 `DECSZR 80H` 并重新核算周期。 |
| `HK-SYN-013` | 业务 EQU 必须成为单一来源 | WARNING | 业务掩码、计数、尺寸或枚举 EQU 定义后必须被代码引用，否则删除。 | 定义 `LED_MASK EQU 29H`，代码继续使用 `#29H`。 | 代码引用 `#LED_MASK`，或删除多余定义。 |
| `HK-CLOCK-001` | 精确时序必须区分 OSC 与 SCK | BLOCKER | 精确时序必须记录实际 OSC、SCK_PS 和派生 SCK；单独声明 16 MHz 不得作为指令时钟。 | 按 16 MHz 直接计算循环，忽略 SCK_PS=34H。 | 从 16 MHz OSC 和 /8 分频得到 2 MHz SCK。 |
| `HK-TIME-001` | 延时周期必须满足目标容差 | BLOCKER | 精确延时的静态 cycle audit 必须落入 TimingContract 的 target_us 与 tolerance_percent。 | 目标 500 ms，审计结果 4.016 s。 | 审计结果 502 ms 且容差为 1%。 |
| `HK-WDT-002` | 喂狗不得掩盖死循环 | BLOCKER | 含 CLRWDT 的延时循环仍必须证明 loop-carried 状态会更新并能到达退出路径。 | DECSZ 不写回计数器，但循环每次都 CLRWDT。 | 使用写回计数器并证明有限次迭代后退出。 |

Set `HK-GPIO-002`, `HK-SYN-012`, `HK-CLOCK-001`, `HK-TIME-001`, and `HK-WDT-002` to `BLOCKER`; keep unused `EQU` and bulk GPIO initialization as `WARNING` so the default strict-warning profile still blocks release.

Add `builtin_compiler` to `toolchain_ids` and to the schema enum under `toolchain_applicability`.

- [ ] **Step 4: Add checker rule-ID validation**

Add to `validate_spec.py`:

```python
CHECKER_RULE_RE = re.compile(r'make_finding\(\s*["\'](HK-[A-Z0-9-]+)["\']')


def check_checker_rule_ids(
    root: Path,
    rules: list[dict[str, Any]],
    checks: dict[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    registered = {item["rule_id"] for item in rules if isinstance(item, dict)}
    emitted: set[str] = set()
    for relative in ("tools/asm_static_check.py", "tools/asm_semantic_gates.py"):
        path = root / relative
        if path.is_file():
            emitted.update(CHECKER_RULE_RE.findall(path.read_text(encoding="utf-8")))
    unknown = sorted(emitted - registered)
    checks["checker_rule_ids"] = sorted(emitted)
    checks["checker_unknown_rule_ids"] = unknown
    for rule_id in unknown:
        add_finding(findings, "checker-rule-id", root / "tools", f"unregistered finding ID: {rule_id}")
```

Call it from `validate()` after `check_rules()`. Change the fixed rule-count check from 70 to 78.

- [ ] **Step 5: Run GREEN verification**

Run:

```powershell
& $PYTHON -m unittest references.spec.tools.tests.test_validate_spec -v
& $PYTHON references/spec/tools/validate_spec.py references/spec --json
```

Expected: all validator tests PASS; JSON reports `rule_count: 78` and no unknown checker IDs.

- [ ] **Step 6: Commit**

```powershell
git add references/spec/rules/asm-rules.json references/spec/rules/asm-rules.schema.json references/spec/tools/validate_spec.py references/spec/tools/tests/test_validate_spec.py
git commit -m "Register HK64S825 semantic gate rules"
```

## Task 2: Add structured request/profile contracts and checker context plumbing

**Files:**
- Modify: `references/profiles/HK64S825.profile.example.json`
- Modify: `references/requests/gpio-request.example.json`
- Modify: `scripts/hk8asm.py:120-220,308-370,568-618,682-705`
- Modify: `references/spec/tools/asm_static_check.py:746-840`
- Modify: `references/spec/tools/tests/test_asm_static_check.py:14-30`
- Modify: `tests/test_cli_contract.py:70-115`

- [ ] **Step 1: Write failing contract-loading tests**

Extend the checker test helper:

```python
def run_checker(
    self,
    source: str,
    *args: str,
    map_text: str | None = None,
    request: dict | None = None,
    profile: dict | None = None,
):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        asm = root / "main.asm"
        asm.write_text(source, encoding="utf-8", newline="\n")
        command = [sys.executable, str(CHECKER), str(asm), *args, "--json"]
        if request is not None:
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            command.extend(["--request", str(request_path)])
        if profile is not None:
            profile_path = root / "profile.json"
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            command.extend(["--profile", str(profile_path)])
        completed = subprocess.run(command, text=True, encoding="utf-8", capture_output=True)
        return completed, json.loads(completed.stdout)
```

Add a test that expects:

```python
self.assertEqual(payload["contract_context"], {
    "request_loaded": True,
    "profile_loaded": True,
    "chip": "HK64S825",
})
```

Add CLI validation tests for a structured GPIO output and for rejecting `direction="output"` without `drive`, `active_level`, or `initial_state`.

- [ ] **Step 2: Run RED**

Run:

```powershell
& $PYTHON -m unittest references.spec.tools.tests.test_asm_static_check tests.test_cli_contract -v
```

Expected: FAIL because `--request/--profile` are unknown and the runner only accepts legacy scalar clock/timing fields.

- [ ] **Step 3: Add the full clock model to the example profile**

Insert:

```json
"clock_model": {
  "sck_ps_register": "SCK_PS",
  "sck_ps_reset": 52,
  "sckhl_bit": 5,
  "divider_by_mode": {
    "high": {"1": 1, "2": 2, "3": 4, "4": 8, "5": 16, "6": 32, "7": 64, "8": 128, "9": 256, "10": 512, "11": 1024, "12": 2048, "13": 5096, "14": 10192, "15": 20384},
    "low": {"1": 1, "2": 2, "3": 4, "4": 8, "5": 16, "6": 32, "7": 64, "8": 1, "9": 2, "10": 4, "11": 8, "12": 16, "13": 32, "14": 64, "15": 128}
  }
}
```

- [ ] **Step 4: Replace the GPIO request example with structured fields**

Use:

```json
"clock": {"osc_hz": 16000000, "sck_ps": "reset"},
"pins": {
  "led_outputs": {
    "port": "PA",
    "bits": [0],
    "direction": "output",
    "drive": "push_pull",
    "active_level": "high",
    "initial_state": "off",
    "preserve_unowned_bits": true
  }
},
"timing": {
  "precision": "precise",
  "delay_targets": [
    {"label": "DELAY_500MS", "target_us": 500000, "tolerance_percent": 1.0}
  ]
}
```

- [ ] **Step 5: Implement request/profile validation**

Add helpers to `hk8asm.py` with these exact contracts:

```python
def validate_clock_contract(request: dict[str, Any]) -> None:
    clock = request.get("clock")
    legacy = request.get("clock_hz")
    require(clock is not None or legacy is not None, "INVALID_REQUEST", "clock or clock_hz is required")
    if clock is None:
        require(isinstance(legacy, int) and not isinstance(legacy, bool) and legacy > 0,
                "INVALID_REQUEST", "clock_hz must be a positive OSC frequency")
        return
    require(isinstance(clock, dict), "INVALID_REQUEST", "clock must be an object")
    osc_hz = clock.get("osc_hz")
    require(isinstance(osc_hz, int) and not isinstance(osc_hz, bool) and osc_hz > 0,
            "INVALID_REQUEST", "clock.osc_hz must be positive")
    sck_ps = clock.get("sck_ps", "reset")
    require(sck_ps == "reset" or (isinstance(sck_ps, int) and not isinstance(sck_ps, bool) and 0 <= sck_ps <= 255),
            "INVALID_REQUEST", "clock.sck_ps must be reset or an 8-bit integer")


def validate_output_pin_contract(name: str, pin: dict[str, Any]) -> None:
    require(pin.get("port") in {"PA", "PB"}, "INVALID_REQUEST", f"pins.{name}.port must be PA or PB")
    bits = pin.get("bits")
    require(isinstance(bits, list) and bool(bits) and all(isinstance(bit, int) and 0 <= bit <= 7 for bit in bits),
            "INVALID_REQUEST", f"pins.{name}.bits must contain bit numbers 0..7")
    require(len(set(bits)) == len(bits), "INVALID_REQUEST", f"pins.{name}.bits must be unique")
    require(pin.get("drive") in {"push_pull", "open_drain"}, "INVALID_REQUEST", f"pins.{name}.drive is invalid")
    require(pin.get("active_level") in {"high", "low"}, "INVALID_REQUEST", f"pins.{name}.active_level is invalid")
    require(pin.get("initial_state") in {"on", "off"}, "INVALID_REQUEST", f"pins.{name}.initial_state is invalid")
    require(isinstance(pin.get("preserve_unowned_bits"), bool), "INVALID_REQUEST",
            f"pins.{name}.preserve_unowned_bits must be boolean")
```

Allow existing string pins for non-GPIO compatibility. If a pin object declares `direction="output"`, call `validate_output_pin_contract`. Accept legacy scalar timing, but validate new `delay_targets` entries as label + positive target + positive tolerance.

Validate the profile clock model keys, reset byte, SCKHL bit, and both divider maps.

- [ ] **Step 6: Load request/profile in the checker and pass them from close-loop**

Add CLI arguments:

```python
parser.add_argument("--request", type=Path)
parser.add_argument("--profile", type=Path)
```

Add:

```python
def load_context_json(path: Path | None, label: str) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} cannot be read: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value
```

Include `contract_context` in checker JSON. Change runner plumbing to:

```python
def static_check(source: Path, profile: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(checker),
        str(source),
        "--toolchain", static_config["toolchain"],
        "--request", str(run_dir / "request.json"),
        "--profile", str(run_dir / "profile.json"),
        "--json",
    ]
```

After constructing this base command, preserve the existing `--map`, `--table-pair`, and `--strict-warnings` appends exactly as they work today. In direct checker mode, malformed context JSON must be converted into a normal ERROR finding and JSON payload rather than an argparse traceback.

Call `static_check(source, profile, run_dir)` from `command_close_loop`.

- [ ] **Step 7: Run GREEN and commit**

```powershell
& $PYTHON -m unittest references.spec.tools.tests.test_asm_static_check tests.test_cli_contract -v
git add references/profiles/HK64S825.profile.example.json references/requests/gpio-request.example.json scripts/hk8asm.py references/spec/tools/asm_static_check.py references/spec/tools/tests/test_asm_static_check.py tests/test_cli_contract.py
git commit -m "Add structured HK64S825 request contracts"
```

Expected: contract-loading and validation tests PASS; legacy non-GPIO requests remain accepted.

## Task 3: Enforce EQU single-source usage

**Files:**
- Create: `references/spec/tools/asm_semantic_gates.py`
- Modify: `references/spec/tools/asm_static_check.py:168-550`
- Modify: `references/spec/tools/tests/test_asm_static_check.py`

- [ ] **Step 1: Write failing unused/used EQU tests**

```python
def test_unused_business_equ_warns_and_strict_mode_fails(self):
    source = "LED_MASK EQU 29H\nORG 0\nSTART:\n  MOV A,#29H\nEND\n"
    completed, payload = self.run_checker(source, "--toolchain", "builtin_compiler", "--strict-warnings")
    self.assertEqual(completed.returncode, 1)
    self.assertIn("HK-SYN-013", self.rule_ids(payload))

def test_referenced_business_equ_passes(self):
    source = "LED_MASK EQU 29H\nORG 0\nSTART:\n  MOV A,#LED_MASK\nEND\n"
    completed, payload = self.run_checker(source, "--toolchain", "builtin_compiler", "--strict-warnings")
    self.assertEqual(completed.returncode, 0, payload["findings"])
```

- [ ] **Step 2: Run RED**

```powershell
& $PYTHON -m unittest references.spec.tools.tests.test_asm_static_check.AsmStaticCheckCliTests.test_unused_business_equ_warns_and_strict_mode_fails references.spec.tools.tests.test_asm_static_check.AsmStaticCheckCliTests.test_referenced_business_equ_passes -v
```

Expected: the unused case incorrectly passes today.

- [ ] **Step 3: Create the semantic helper and collect EQU symbols**

Create the module with:

```python
from __future__ import annotations

from typing import Any


def make_issue(
    rule_id: str,
    severity: str,
    file: str,
    line: int | None,
    evidence: str,
    risk: str,
    required_fix: str,
) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "file": file,
        "line": line,
        "evidence": evidence,
        "risk": risk,
        "required_fix": required_fix,
    }


def audit_unused_equ(file_model: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for symbol in file_model.get("_equ_symbols", {}).values():
        if symbol["uses"] == 0:
            issues.append(make_issue(
                "HK-SYN-013",
                "WARNING",
                file_model["path"],
                symbol["line"],
                f"EQU {symbol['name']} is defined but never referenced by code",
                "The declared constant is not the source of truth and can silently drift from inline literals.",
                "Use the EQU symbol in code or remove the unused definition.",
            ))
    return issues
```

In `analyze_file`, collect every `NAME EQU value`, collect symbol tokens from all other code statements, then compute `uses` after the full file is parsed. Store this as `_equ_symbols` until semantic gates complete.

- [ ] **Step 4: Call the gate and run GREEN**

Import and call `audit_unused_equ(file_result)` before private fields are removed. Run:

```powershell
& $PYTHON -m unittest references.spec.tools.tests.test_asm_static_check -v
```

Expected: both new tests PASS and existing direct SRAM-address tests remain green.

- [ ] **Step 5: Commit**

```powershell
git add references/spec/tools/asm_semantic_gates.py references/spec/tools/asm_static_check.py references/spec/tools/tests/test_asm_static_check.py
git commit -m "Reject unused HK64S825 EQU constants"
```

## Task 4: Enforce GPIO drive, safe latch and output-enable order

**Files:**
- Modify: `references/spec/tools/asm_semantic_gates.py`
- Modify: `references/spec/tools/asm_static_check.py`
- Modify: `references/spec/tools/tests/test_asm_static_check.py`

- [ ] **Step 1: Write four failing GPIO tests**

Add tests for:

```text
push_pull + missing PA_POD clear -> HK-GPIO-002 BLOCKER
push_pull + RMW clear PA_POD, safe PA_PIO, then set PA_POE -> PASS
open_drain + missing PA_POD set -> HK-GPIO-002 BLOCKER
correct values but PA_POE occurs before PA_POD/PA_PIO -> HK-GPIO-002 BLOCKER
```

Use this request factory:

```python
def gpio_request(*, drive: str = "push_pull", active_level: str = "high") -> dict:
    return {
        "schema_version": 1,
        "chip": "HK64S825",
        "behavior": "PA0 PA3 PA5 LED 输出",
        "clock": {"osc_hz": 16_000_000, "sck_ps": "reset"},
        "pins": {
            "led_outputs": {
                "port": "PA",
                "bits": [0, 3, 5],
                "direction": "output",
                "drive": drive,
                "active_level": active_level,
                "initial_state": "off",
                "preserve_unowned_bits": True,
            }
        },
        "peripherals": [{"name": "gpio"}],
        "timing": {"precision": "approximate"},
        "memory_limits": {"rom_bytes": 2048, "ram_bytes": 64},
        "board": {"id": "HK64S825-DEFAULT"},
        "acceptance": [],
        "allow_nonvolatile_changes": False,
    }
```

- [ ] **Step 2: Run RED**

```powershell
& $PYTHON -m unittest references.spec.tools.tests.test_asm_static_check -v
```

Expected: all new GPIO contract tests fail because the checker only warns about writing too many registers.

- [ ] **Step 3: Implement canonical GPIO bit effects**

Add to `asm_semantic_gates.py`:

```python
def split_args(args: str) -> list[str]:
    return [part.strip() for part in args.split(",")]


def resolve_byte(token: str, equ_symbols: dict[str, dict[str, Any]]) -> int | None:
    value = token.strip()
    if value.startswith("#"):
        value = value[1:].strip()
    symbol = equ_symbols.get(value.upper())
    if symbol is not None:
        return symbol.get("value")
    try:
        if value.lower().startswith("0x"):
            return int(value, 16)
        if value.upper().endswith("H"):
            return int(value[:-1], 16)
        if value.isdecimal():
            return int(value, 10)
    except ValueError:
        return None
    return None


def collect_gpio_effects(file_model: dict[str, Any]) -> list[dict[str, Any]]:
    instructions = file_model.get("_instructions", [])
    equ_symbols = file_model.get("_equ_symbols", {})
    effects: list[dict[str, Any]] = []
    for index, instruction in enumerate(instructions):
        args = split_args(instruction["args"])
        if instruction["op"] in {"BSET", "BCLR"} and len(args) == 2:
            bit = resolve_byte(args[1], equ_symbols)
            if bit is not None and 0 <= bit <= 7:
                effects.append({
                    "register": args[0].upper(),
                    "set_bits": {bit} if instruction["op"] == "BSET" else set(),
                    "clear_bits": {bit} if instruction["op"] == "BCLR" else set(),
                    "line": instruction["line"],
                    "kind": "bit",
                })
        if index + 2 >= len(instructions):
            continue
        load, logic, store = instructions[index:index + 3]
        load_args = split_args(load["args"])
        logic_args = split_args(logic["args"])
        store_args = split_args(store["args"])
        if not (
            load["op"] == "MOV" and len(load_args) == 2 and load_args[0].upper() == "A"
            and logic["op"] in {"AND", "OR"} and len(logic_args) == 2 and logic_args[0].upper() == "A"
            and store["op"] == "MOV" and len(store_args) == 2 and store_args[1].upper() == "A"
            and load_args[1].upper() == store_args[0].upper()
        ):
            continue
        mask = resolve_byte(logic_args[1], equ_symbols)
        if mask is None:
            continue
        effects.append({
            "register": store_args[0].upper(),
            "set_bits": {bit for bit in range(8) if logic["op"] == "OR" and mask & (1 << bit)},
            "clear_bits": {bit for bit in range(8) if logic["op"] == "AND" and not mask & (1 << bit)},
            "line": store["line"],
            "kind": "rmw",
        })
    return effects
```

- [ ] **Step 4: Audit each PinContract**

Implement `audit_gpio_contract(file_model, request)` so each bit requires:

```python
mode_register = f"{port}_POD"
data_register = f"{port}_PIO"
enable_register = f"{port}_POE"
mode_action = "clear_bits" if drive == "push_pull" else "set_bits"
initial_high = (initial_state == "on") == (active_level == "high")
data_action = "set_bits" if initial_high else "clear_bits"
```

Find the first matching mode, data and enable effects. Emit `HK-GPIO-002` if any is absent or if the source order is not `POD < PIO < POE`. If `preserve_unowned_bits=true`, accept only `kind` values `bit` and `rmw`.

- [ ] **Step 5: Run GREEN and commit**

```powershell
& $PYTHON -m unittest references.spec.tools.tests.test_asm_static_check -v
git add references/spec/tools/asm_semantic_gates.py references/spec/tools/asm_static_check.py references/spec/tools/tests/test_asm_static_check.py
git commit -m "Enforce HK64S825 GPIO output contracts"
```

Expected: targeted `POD/PIO/POE` initialization passes; missing or misordered mode setup fails; existing bulk-initialization warning remains.

## Task 5: Detect accumulator-only skip counters and WDT-masked dead loops

**Files:**
- Modify: `references/spec/tools/asm_semantic_gates.py`
- Modify: `references/spec/tools/asm_static_check.py`
- Modify: `references/spec/tools/tests/test_asm_static_check.py`

- [ ] **Step 1: Write failing loop tests**

Add separate tests for:

```asm
LOOP:
  CLRWDT
  DECSZ 80H
  JMP LOOP
```

and the `INCSZ` equivalent. Expect `HK-SYN-012`; when `CLRWDT` is present also expect `HK-WDT-002`. Change the instruction to `DECSZR` or `INCSZR` and expect those findings to disappear.

- [ ] **Step 2: Run RED**

```powershell
& $PYTHON -m unittest references.spec.tools.tests.test_asm_static_check -v
```

Expected: the current checker accepts both non-progressing loops.

- [ ] **Step 3: Load instruction effects from the packaged reference**

Add:

```python
def load_instruction_effects(path: Path) -> dict[str, dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    effects: dict[str, dict[str, Any]] = {}
    for variant in document["variants"]:
        mnemonic = variant["mnemonic"].upper()
        notes = (variant.get("raw_notes") or "").strip()
        writes = "R" if notes.startswith("R ←") else "A" if notes.startswith("A ←") else None
        effects[mnemonic] = {
            "writes": writes,
            "skip": "THEN SKIP" in notes.upper(),
            "cycles": variant["cycles"],
            "notes": notes,
            "semantic_status": variant.get("semantic_status"),
            "delivery_policy": variant.get("delivery_policy"),
        }
    return effects
```

Import `json` and `Path` in the helper. Resolve the reference path from the checker as `spec_root / "rules" / "instruction-reference.json"`.

- [ ] **Step 4: Implement backward skip-loop audit**

```python
def audit_counter_loops(
    file_model: dict[str, Any],
    effects: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    instructions = file_model.get("_instructions", [])
    labels = file_model.get("labels", {})
    address_to_index = {item["address"]: position for position, item in enumerate(instructions)}
    for index, instruction in enumerate(instructions[:-1]):
        effect = effects.get(instruction["op"], {})
        jump = instructions[index + 1]
        if not effect.get("skip") or jump["op"] != "JMP":
            continue
        target = jump["args"].split(",", 1)[0].strip().upper()
        label = labels.get(target)
        if label is None or label["address"] > instruction["address"]:
            continue
        start_index = address_to_index.get(label["address"])
        if start_index is None:
            continue
        if effect.get("writes") == "A":
            replacement = instruction["op"] + "R"
            issues.append(make_issue(
                "HK-SYN-012", "BLOCKER", file_model["path"], instruction["line"],
                f"{instruction['op']} writes the result to A, not {instruction['args']}; backward loop target is {target}",
                "The loop-carried counter does not change, so the loop cannot reach its exit condition.",
                f"Use {replacement} when the counter must be updated in place, then recalculate timing.",
            ))
            loop_slice = instructions[start_index:index + 2]
            if any(item["op"] == "CLRWDT" for item in loop_slice):
                issues.append(make_issue(
                    "HK-WDT-002", "BLOCKER", file_model["path"], instruction["line"],
                    "CLRWDT executes inside a loop whose counter is not written back",
                    "The watchdog is continually cleared while the program is permanently stuck.",
                    "Restore provable loop progress before retaining CLRWDT.",
                ))
    return issues
```

- [ ] **Step 5: Run GREEN and commit**

```powershell
& $PYTHON -m unittest references.spec.tools.tests.test_asm_static_check -v
git add references/spec/tools/asm_semantic_gates.py references/spec/tools/asm_static_check.py references/spec/tools/tests/test_asm_static_check.py
git commit -m "Detect non-progressing HK64S825 counter loops"
```

## Task 6: Derive SCK and audit precise delay cycles

**Files:**
- Modify: `references/spec/tools/asm_semantic_gates.py`
- Create: `references/spec/tools/tests/test_asm_semantic_gates.py`
- Modify: `references/spec/tools/asm_static_check.py`
- Modify: `references/spec/tools/tests/test_asm_static_check.py`

- [ ] **Step 1: Write failing pure timing tests**

Create tests for:

```python
def delay_model(outer: int) -> dict:
    rows = [
        (0, "MOV", f"A,#{outer}"),
        (1, "MOV", "82H,A"),
        (2, "MOV", "A,#250"),
        (3, "MOV", "81H,A"),
        (4, "MOV", "A,#250"),
        (5, "MOV", "80H,A"),
        (6, "CLRWDT", ""),
        (7, "DECSZR", "80H"),
        (8, "JMP", "DELAY_INNER_LOOP"),
        (9, "DECSZR", "81H"),
        (10, "JMP", "DELAY_MIDDLE_LOOP"),
        (11, "DECSZR", "82H"),
        (12, "JMP", "DELAY_OUTER_LOOP"),
        (13, "RET", ""),
    ]
    instructions = [
        {"address": address, "op": op, "args": args, "line": address + 1, "source": f"{op} {args}".strip()}
        for address, op, args in rows
    ]
    return {
        "path": "delay.asm",
        "_instructions": instructions,
        "_equ_symbols": {},
        "labels": {
            "DELAY_500MS": {"address": 0, "line": 1},
            "DELAY_OUTER_LOOP": {"address": 2, "line": 3},
            "DELAY_MIDDLE_LOOP": {"address": 4, "line": 5},
            "DELAY_INNER_LOOP": {"address": 6, "line": 7},
        },
    }


def test_reset_0x34_derives_2mhz_from_16mhz_osc(self):
    self.assertEqual(derive_sck_hz(16_000_000, "reset", PROFILE_CLOCK_MODEL), 2_000_000)

def test_original_three_level_counts_are_about_4_seconds_at_2mhz(self):
    result = simulate_delay(delay_model(32), "DELAY_500MS", 2_000_000, EFFECTS)
    self.assertEqual(result.cycles, 8_032_133)
    self.assertAlmostEqual(result.actual_us, 4_016_066.5, places=1)

def test_outer_four_is_within_one_percent_of_500ms(self):
    result = simulate_delay(delay_model(4), "DELAY_500MS", 2_000_000, EFFECTS)
    self.assertLessEqual(abs(result.actual_us - 500_000) / 500_000 * 100, 1.0)
```

The fixed cycle totals include the 2-cycle `CALL` overhead and 2-cycle `RET` and must be loaded from the packaged instruction reference.

- [ ] **Step 2: Run RED**

```powershell
& $PYTHON -m unittest references.spec.tools.tests.test_asm_semantic_gates -v
```

Expected: import failure because the clock and simulator functions do not exist.

- [ ] **Step 3: Implement SCK derivation**

```python
def derive_sck_hz(osc_hz: int, sck_ps: str | int, model: dict[str, Any]) -> int:
    raw = model["sck_ps_reset"] if sck_ps == "reset" else sck_ps
    if not isinstance(raw, int) or not 0 <= raw <= 0xFF:
        raise ValueError("SCK_PS must resolve to an 8-bit integer")
    selector = raw & 0x0F
    if selector == 0:
        raise ValueError("SCKPS=0000 is prohibited")
    high = bool(raw & (1 << model["sckhl_bit"]))
    mode = "high" if high else "low"
    divider = model["divider_by_mode"][mode][str(selector)]
    if osc_hz % divider:
        return round(osc_hz / divider)
    return osc_hz // divider
```

- [ ] **Step 4: Implement the bounded delay interpreter**

Add a `DelayResult` dataclass and an interpreter supporting only the audited subset:

```python
@dataclass(frozen=True)
class DelayResult:
    label: str
    cycles: int
    sck_hz: int
    actual_us: float
    clrwdt_count: int
    steps: int


SUPPORTED_DELAY_OPS = {
    "MOV", "NOP", "CLRWDT", "DECR", "INCR", "DECSZ", "DECSZR",
    "INCSZ", "INCSZR", "SZ", "SZR", "JMP", "RET",
}


def effect_cycles(effect: dict[str, Any], *, skipped: bool = False) -> int:
    value = effect.get("cycles")
    if isinstance(value, int):
        return value
    if str(value).lower() == "1or2":
        return 2 if skipped else 1
    raise ValueError(f"unsupported cycle metadata: {value}")


def simulate_delay(
    file_model: dict[str, Any],
    label: str,
    sck_hz: int,
    effects: dict[str, dict[str, Any]],
    *,
    max_steps: int = 10_000_000,
) -> DelayResult:
    """Interpret one auditable delay routine from CALL entry through RET."""
    instructions = file_model.get("_instructions", [])
    labels = file_model.get("labels", {})
    equ_symbols = file_model.get("_equ_symbols", {})
    address_to_index = {item["address"]: position for position, item in enumerate(instructions)}
    start = labels.get(label.upper())
    if start is None or start["address"] not in address_to_index:
        raise ValueError(f"delay label cannot be resolved: {label}")
    pc = address_to_index[start["address"]]
    accumulator: int | None = None
    registers: dict[str, int] = {}
    cycles = effect_cycles(effects["CALL"])
    clrwdt_count = 0
    steps = 0
    while True:
        if steps >= max_steps:
            raise ValueError(f"delay routine exceeded {max_steps} interpreter steps")
        if not 0 <= pc < len(instructions):
            raise ValueError("delay routine left the source model without RET")
        instruction = instructions[pc]
        op = instruction["op"]
        if op not in SUPPORTED_DELAY_OPS:
            raise ValueError(f"unsupported delay instruction at line {instruction['line']}: {op}")
        args = split_args(instruction["args"])
        effect = effects[op]
        steps += 1

        if op == "MOV":
            if len(args) != 2:
                raise ValueError(f"unsupported MOV at line {instruction['line']}")
            destination, source = args[0].upper(), args[1]
            if destination == "A":
                if source.startswith("#"):
                    value = resolve_byte(source, equ_symbols)
                else:
                    value = registers.get(source.upper())
                if value is None:
                    raise ValueError(f"unknown MOV source at line {instruction['line']}: {source}")
                accumulator = value & 0xFF
            elif source.upper() == "A":
                if accumulator is None:
                    raise ValueError(f"A is unknown at line {instruction['line']}")
                registers[destination] = accumulator
            else:
                raise ValueError(f"unsupported MOV form at line {instruction['line']}")
            cycles += effect_cycles(effect)
            pc += 1
            continue

        if op in {"NOP", "CLRWDT"}:
            cycles += effect_cycles(effect)
            clrwdt_count += int(op == "CLRWDT")
            pc += 1
            continue

        if op in {"DECR", "INCR"}:
            register = args[0].upper()
            if register not in registers:
                raise ValueError(f"counter is unknown at line {instruction['line']}: {register}")
            delta = -1 if op == "DECR" else 1
            registers[register] = (registers[register] + delta) & 0xFF
            cycles += effect_cycles(effect)
            pc += 1
            continue

        if op in {"DECSZ", "DECSZR", "INCSZ", "INCSZR"}:
            register = args[0].upper()
            if register not in registers:
                raise ValueError(f"counter is unknown at line {instruction['line']}: {register}")
            delta = -1 if op.startswith("DEC") else 1
            result = (registers[register] + delta) & 0xFF
            if op.endswith("R"):
                registers[register] = result
            else:
                accumulator = result
            skipped = result == 0
            cycles += effect_cycles(effect, skipped=skipped)
            pc += 2 if skipped else 1
            continue

        if op in {"SZ", "SZR"}:
            register = args[0].upper()
            if register not in registers:
                raise ValueError(f"counter is unknown at line {instruction['line']}: {register}")
            result = registers[register]
            if op == "SZ":
                accumulator = result
            skipped = result == 0
            cycles += effect_cycles(effect, skipped=skipped)
            pc += 2 if skipped else 1
            continue

        if op == "JMP":
            target = labels.get(args[0].upper())
            if target is None or target["address"] not in address_to_index:
                raise ValueError(f"jump target cannot be resolved at line {instruction['line']}: {args[0]}")
            cycles += effect_cycles(effect)
            pc = address_to_index[target["address"]]
            continue

        if op == "RET":
            cycles += effect_cycles(effect)
            return DelayResult(
                label=label,
                cycles=cycles,
                sck_hz=sck_hz,
                actual_us=cycles * 1_000_000 / sck_hz,
                clrwdt_count=clrwdt_count,
                steps=steps,
            )

        raise ValueError(f"unhandled delay instruction: {op}")
```

Interpreter state must contain `A`, an 8-bit register dictionary, current instruction index, cycles, steps, and `skip_next`. Resolve labels and numeric/`EQU` constants from the file model. Implement these state transitions:

```text
MOV A,#K      A=K, +1 cycle
MOV R,A       R=A, +1 cycle
MOV A,R       A=R, +1 cycle
DECSZR R      R=(R-1)&FF; zero => +2 cycles and skip next, else +1
DECSZ R       A=(R-1)&FF; zero => +2 cycles and skip next, else +1
INCSZR/INCSZ  equivalent increment behavior
DECR/INCR     write back R, +1 cycle
SZR/SZ        apply documented destination and 1/2-cycle skip behavior
JMP LABEL     jump to label, +2 cycles
NOP/CLRWDT    +1 cycle
RET           +2 cycles and finish
```

Start with 2 cycles for the caller's `CALL`. Reject unsupported instructions, unknown register values, unresolved targets, more than 10,000,000 steps, or reaching code outside the routine without `RET`.

- [ ] **Step 5: Audit TimingContract in the checker**

For every `timing.delay_targets[]` entry:

1. derive effective SCK from request clock and profile model;
2. detect any source write to `SCK_PS`; accept only a statically resolvable `MOV A,#K` followed by `MOV SCK_PS,A` and use that value;
3. run the delay interpreter;
4. calculate `error_percent`;
5. emit `HK-CLOCK-001` when SCK cannot be proven and `HK-TIME-001` when error exceeds tolerance;
6. append this evidence to checker JSON:

```json
{
  "label": "DELAY_500MS",
  "osc_hz": 16000000,
  "sck_ps": 52,
  "sck_hz": 2000000,
  "cycles": 1004021,
  "actual_us": 502010.5,
  "target_us": 500000,
  "error_percent": 0.4021,
  "status": "pass"
}
```

- [ ] **Step 6: Run GREEN and performance check**

```powershell
Measure-Command { & $PYTHON -m unittest references.spec.tools.tests.test_asm_semantic_gates references.spec.tools.tests.test_asm_static_check -v }
```

Expected: tests PASS and the full semantic test pair completes in under 10 seconds on the current machine.

- [ ] **Step 7: Commit**

```powershell
git add references/spec/tools/asm_semantic_gates.py references/spec/tools/tests/test_asm_semantic_gates.py references/spec/tools/asm_static_check.py references/spec/tools/tests/test_asm_static_check.py
git commit -m "Audit HK64S825 delay timing from SCK cycles"
```

## Task 7: Block the real regression through close-loop and bind evidence

**Files:**
- Modify: `references/spec/tools/tests/test_asm_static_check.py`
- Modify: `tests/test_cli_contract.py`
- Modify: `scripts/hk8asm.py:699-760`
- Modify: `references/spec/tools/validate_spec.py`
- Modify: `references/spec/tools/tests/test_validate_spec.py`

- [ ] **Step 1: Add the complete problem-source regression test**

Embed the provided source as `PROBLEM_LED_SOURCE` in the test file. Do not create a reusable ASM template. Assert:

```python
completed, payload = self.run_checker(
    PROBLEM_LED_SOURCE,
    "--toolchain", "builtin_compiler",
    "--strict-warnings",
    request=gpio_request(),
    profile=ready_profile(),
)
self.assertEqual(completed.returncode, 2)
self.assertTrue({
    "HK-GPIO-002",
    "HK-SYN-012",
    "HK-SYN-013",
    "HK-WDT-002",
}.issubset(self.rule_ids(payload)))
```

Add a second source that uses `DECSZR`, references all constants, explicitly clears `PA_POD`, and uses an outer count derived for 2 MHz; expect zero findings.

- [ ] **Step 2: Add close-loop/release integration tests**

Create a compile-only run using the bundled profile/config. For the bad source:

```python
self.assertNotEqual(loop.returncode, 0)
self.assertEqual("STATIC_CHECK_FAILED", self.payload(loop)["code"])
self.assertFalse((run_dir / "build" / "firmware.hex").exists())
```

For the good source, assert `COMPILE_PASSED`, `RELEASED`, and evidence contains `gpio_contract`, `loop_semantics`, and `timing` sections.

- [ ] **Step 3: Run RED, then update evidence propagation**

Run the two new integration tests and confirm the good evidence assertion fails. Update `static_check()` to return the checker payload's semantic audit summaries rather than only its numeric summary:

```python
return {
    "status": "pass",
    "checker": "asm_static_check.py",
    "toolchain": static_config["toolchain"],
    "summary": summary,
    "semantic_audits": result.get("semantic_audits", {}),
}
```

The existing evidence writer will then hash-bind the audit result through `run["gates"]["static"]`.

- [ ] **Step 4: Bind automated rule IDs to tests**

Add to `validate_spec.py`:

```python
AUTOMATED_RULE_TESTS = {
    "HK-WDT-001": "test_delay_loop_without_clrwdt_is_blocked",
    "HK-GPIO-INIT-001": "test_simple_led_bulk_gpio_initialization_warns_under_strict_warnings",
    "HK-GPIO-002": "test_problem_led_source_is_rejected_by_semantic_gates",
    "HK-SYN-012": "test_decsz_backward_counter_loop_is_blocked",
    "HK-SYN-013": "test_unused_business_equ_warns_and_strict_mode_fails",
    "HK-CLOCK-001": "test_reset_0x34_derives_2mhz_from_16mhz_osc",
    "HK-TIME-001": "test_original_three_level_counts_are_about_4_seconds_at_2mhz",
    "HK-WDT-002": "test_decsz_loop_with_clrwdt_is_blocked",
}
```

Check that each rule exists and each method name occurs in `tools/tests/*.py`. Add a negative validator test that deletes one method name from a copied test file and expects `checker-rule-test`.

- [ ] **Step 5: Run GREEN and commit**

```powershell
& $PYTHON -m unittest references.spec.tools.tests.test_asm_static_check references.spec.tools.tests.test_asm_semantic_gates tests.test_cli_contract references.spec.tools.tests.test_validate_spec -v
git add references/spec/tools/tests/test_asm_static_check.py tests/test_cli_contract.py scripts/hk8asm.py references/spec/tools/validate_spec.py references/spec/tools/tests/test_validate_spec.py
git commit -m "Block invalid LED ASM before HK64S825 release"
```

## Task 8: Update Skill instructions, company rules and evaluations

**Files:**
- Modify: `tests/test_validate_skill_contract.py`
- Modify: `SKILL.md`
- Modify: `agents/openai.yaml`
- Modify: `evals/evals.json`
- Modify: `evals/baseline.json`
- Modify: relevant spec Markdown and checklists listed in the file map

- [ ] **Step 1: Write failing Skill contract assertions**

Replace the outdated `默认只写当前功能必需的 PIO 和 POE` assertion with required phrases:

```python
for phrase in (
    "最小初始化是最少但足以建立确定电气状态的操作",
    "推挽输出必须显式清除目标 `POD` 位",
    "先预装安全 `PIO`，最后开启 `POE`",
    "不得把 `DECSZ` 当作写回计数寄存器的倒计数指令",
    "精确延时必须从 OSC、SCK_PS 和实际 SCK 推导",
    "未使用的业务 `EQU` 必须删除或真正引用",
):
    self.assertIn(phrase, skill_text)
self.assertNotIn("默认只写当前功能必需的 `PIO` 和 `POE`", skill_text)
```

- [ ] **Step 2: Run RED**

```powershell
& $PYTHON -m unittest tests.test_validate_skill_contract -v
```

Expected: new phrases are missing and the retired phrase is still present.

- [ ] **Step 3: Replace the LED/GPIO hard-gate text**

Use this concise rule in `SKILL.md`:

```markdown
简单 LED/GPIO 使用“最小充分初始化”：只配置当前 PinContract 真正需要的寄存器，但每个输出 pin 的电气模式必须被显式建立。推挽输出必须清目标 `POD` 位，开漏输出必须置目标 `POD` 位；随后预装安全 `PIO`，最后开启 `POE`。不得依赖 `POD` 复位值代替正式初始化，也不得批量清写无关 `PPU/PPD/INS/IOS/PSL`。

所有循环计数指令先核对 `instruction-reference.json.raw_notes` 的写回目标。`DECSZ/INCSZ` 结果进入 A，不能作为原位更新的 SRAM 计数器；需要写回时使用经规则允许的 `DECSZR/INCSZR`。含 `CLRWDT` 的延时循环仍必须证明会退出。

精确延时必须从 `clock.osc_hz`、`SCK_PS` 和实际 SCK 推导，并通过 cycle audit；只写“16 MHz”不构成时序依据。业务掩码和延时常量的 `EQU` 必须被源码引用，否则删除。
```

Keep the existing no-disk-scan, Chinese-comment, builtin-compiler, no-example-reuse and no-hardware-prerequisite rules.

- [ ] **Step 4: Synchronize source-of-truth documents**

Make these focused edits:

```text
01: add unused EQU and one-source-of-truth review item
02: explicitly contrast DECSZ/DECSZR and INCSZ/INCSZR write destinations
03: state push-pull requires target POD bits explicitly cleared without full-port reset
05: replace PIO/POE-only wording with POD/PIO/POE minimal-sufficient sequence
08: add “LED constant on: DECSZ does not write R” and “500 ms becomes 4 s: OSC/SCK conflation” cases
09: update request examples and generation self-check questions
pre-generation: require PinContract and ClockContract only when task uses them
pre-build: add unused EQU, loop progress, SCK and cycle audit checks
```

Do not add or modify any template/sample ASM.

- [ ] **Step 5: Add forward-evaluation cases**

Add cases asserting the agent:

```text
does not omit POD for push-pull output
does not use DECSZ/INCSZ as persistent counters
derives 2 MHz SCK from 16 MHz OSC and reset 0x34
does not define unused EQU
still avoids full PPU/PPD/INS/IOS initialization
```

Record the supplied problem output in `baseline.json` as observed failure evidence without storing it as a generation template.

- [ ] **Step 6: Run GREEN and commit**

```powershell
& $PYTHON -m unittest tests.test_validate_skill_contract -v
& $PYTHON scripts/validate_skill.py .
git add SKILL.md agents/openai.yaml evals references/spec tests/test_validate_skill_contract.py references/profiles/HK64S825.profile.example.json references/requests/gpio-request.example.json
git commit -m "Teach HK64S825 semantic GPIO and timing rules"
```

## Task 9: Full verification, installation sync, independent review and push

**Files:**
- Verify all changed files
- Update installed copies only after repository verification

- [ ] **Step 1: Run the complete automated suite**

```powershell
& $PYTHON -m unittest discover -s tests -v
& $PYTHON -m unittest discover -s references/spec/tools/tests -v
& $PYTHON scripts/validate_skill.py .
& $PYTHON references/spec/tools/validate_spec.py references/spec --json
git diff --check
```

Expected: zero test failures, `SKILL_VALID`, spec summary 0 errors/0 warnings, and no diff whitespace errors.

- [ ] **Step 2: Run the bundled closed-loop smoke test**

Use the good LED source from the integration test in a temporary directory and run:

```powershell
$SMOKE = Join-Path $env:TEMP 'hk64s825-semantic-smoke'
& $PYTHON scripts/hk8asm.py doctor --profile references/profiles/HK64S825.profile.example.json --config references/configs/local-adapter.example.json
& $PYTHON scripts/hk8asm.py new-run --profile references/profiles/HK64S825.profile.example.json --config references/configs/local-adapter.example.json --request (Join-Path $SMOKE 'request.json') --source (Join-Path $SMOKE 'candidate.asm') --run-dir (Join-Path $SMOKE 'run')
& $PYTHON scripts/hk8asm.py close-loop --run-dir (Join-Path $SMOKE 'run')
& $PYTHON scripts/hk8asm.py release --run-dir (Join-Path $SMOKE 'run') --output (Join-Path $SMOKE 'verified.asm')
```

Expected: `READY`, `RUN_CREATED`, `COMPILE_PASSED`, `RELEASED`; evidence reports SCK 2 MHz and delay within tolerance.

- [ ] **Step 3: Re-run the supplied bad source as a negative smoke test**

Use `C:\Users\Admin\Desktop\有问题led亮灯.txt` with the structured PA0/PA3/PA5 request. Expected: `close-loop` returns `STATIC_CHECK_FAILED`, no firmware artifacts are created, and findings include GPIO, EQU, loop and WDT rule IDs.

- [ ] **Step 4: Request an independent code review**

Dispatch a fresh reviewer with the design document, implementation plan, base SHA `d4a76b7`, current HEAD, and these review questions:

```text
Does the implementation reject the real bad source for transferable reasons?
Can valid push-pull/open-drain and approximate/precise timing tasks still pass?
Are there false positives caused by ORG holes, shared ports or legacy non-GPIO requests?
Does any path release source after a semantic failure?
```

Fix all Critical and Important findings, then rerun Step 1.

- [ ] **Step 5: Forward-test the Skill with fresh agents**

Run at least four isolated scenarios without telling the agents the expected code:

```text
HK64S825 PA1 high-active push-pull LED, 250 ms on/off, 16 MHz OSC
HK64S825 PA2 low-active push-pull LED, approximate visible blink
HK64S825 PB0 open-drain heartbeat with external pull-up
HK64S825 LED task where the user explicitly asks to use DECSZ as the counter
```

Inspect outputs for explicit POD mode, no unused EQU, correct writeback variant, OSC/SCK distinction, Chinese comments, internal compilation and no disk scan.

- [ ] **Step 6: Sync installed Skill copies**

```powershell
& $PYTHON scripts/install.py --target codex-user --mode copy
$CODEX_SKILL = 'C:\Users\Admin\.codex\skills\writing-hk8-mcu-asm'
New-Item -ItemType Directory -Force -Path $CODEX_SKILL | Out-Null
Copy-Item -Path (Join-Path $REPO '*') -Destination $CODEX_SKILL -Recurse -Force
```

Verify hashes of repository and installed `SKILL.md`, `asm_static_check.py`, `asm_semantic_gates.py`, profile and request example match.

- [ ] **Step 7: Final commit and push**

```powershell
git status --short
git add -A
git commit -m "Enforce HK64S825 ASM semantic and timing gates"
git push origin main
```

If earlier task commits already contain every file and the worktree is clean, do not create an empty final commit. Push the verified HEAD and confirm `HEAD == origin/main`.
