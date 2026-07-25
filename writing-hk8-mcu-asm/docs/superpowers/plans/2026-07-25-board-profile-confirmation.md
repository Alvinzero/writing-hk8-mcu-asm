# Board Profile Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent HK64S825 ASM generation and run creation from silently adopting unconfirmed development-board parameters.

**Architecture:** Add explicit input provenance validation to `hk8asm.py`, make the bundled compiler config board-agnostic, and separate generic seven-segment rules from the registered board profile. Skill instructions and evals enforce the same pre-generation pause as the machine gate.

**Tech Stack:** Python 3.7+, `unittest`, JSON request/config contracts, Markdown Skill instructions.

---

### Task 1: Request provenance RED tests

**Files:**
- Modify: `tests/test_cli_contract.py`

- [ ] Add a test removing `input_provenance.board` and expect `BOARD_PROFILE_UNCONFIRMED`.
- [ ] Add tests removing GPIO and clock provenance and expect `BOARD_INPUT_UNCONFIRMED`.
- [ ] Add provenance to shared valid request fixtures.
- [ ] Run the focused tests and confirm they fail because validation is absent.

### Task 2: Request provenance validation

**Files:**
- Modify: `scripts/hk8asm.py`
- Modify: `references/requests/gpio-request.example.json`

- [ ] Validate provenance before accepting board, GPIO and clock contracts.
- [ ] Restrict board-level sources to `user_provided` and `user_confirmed_profile`.
- [ ] Run focused CLI tests and confirm they pass.

### Task 3: Board-agnostic compiler config

**Files:**
- Modify: `tests/test_cli_contract.py`
- Modify: `tests/test_validate_skill_contract.py`
- Modify: `scripts/hk8asm.py`
- Modify: `references/configs/builtin-config.json`

- [ ] Add failing tests showing compile-only config works without `board_id` and accepts different confirmed boards.
- [ ] Make `board_id` optional for compile-only adapters and mandatory for hardware adapters.
- [ ] Match config/request board ids only when config declares one.
- [ ] Run the focused tests and confirm they pass.

### Task 4: Skill and seven-segment isolation

**Files:**
- Modify: `tests/test_validate_skill_contract.py`
- Modify: `SKILL.md`
- Modify: `agents/openai.yaml`
- Modify: `references/spec/AGENTS.md`
- Modify: `references/spec/06-数码管动态扫描规范.md`
- Create: `references/boards/HK64S825-DEFAULT/seven-segment.md`
- Delete: `references/spec/templates/seven-segment-scan.asm`
- Modify: `evals/evals.json`

- [ ] Add failing contract tests for board confirmation, generic spec isolation and template removal.
- [ ] Rewrite required-input rules so an absent board selection always pauses before candidate generation.
- [ ] Move fixed wiring and polarity into the explicitly selected board profile.
- [ ] Add unknown-board and confirmed-profile forward eval cases.
- [ ] Run Skill contract tests and confirm they pass.

### Task 5: Verification and release

**Files:**
- Modify only files required by failing regression tests.

- [ ] Run all unit tests.
- [ ] Run `scripts/validate_skill.py` against the Skill root.
- [ ] Run `git diff --check` and inspect the final diff.
- [ ] Install the verified copy into the local Codex Skill locations.
- [ ] Commit the feature and push `codex/board-profile-confirmation` to GitHub.
