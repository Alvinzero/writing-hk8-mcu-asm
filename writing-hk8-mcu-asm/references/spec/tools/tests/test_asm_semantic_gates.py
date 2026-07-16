from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from references.spec.tools.asm_semantic_gates import (
    DelayResult,
    _simulate_delay,
    derive_sck_hz,
    effect_cycles,
    load_instruction_effects,
    simulate_delay,
)


SPEC = Path(__file__).resolve().parents[2]
RULES = SPEC / "rules"
PROFILE = SPEC.parent / "profiles" / "HK64S825.profile.example.json"


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
        {
            "address": address,
            "op": op,
            "args": args,
            "line": address + 1,
            "source": f"{op} {args}".strip(),
        }
        for address, op, args in rows
    ]
    return {
        "path": "delay.asm",
        "_instructions": instructions,
        "_equ_symbols": {},
        "_word_owners": {
            address: {
                "line": address + 1,
                "kind": "instruction",
                "instruction_index": index,
            }
            for index, (address, _op, _args) in enumerate(rows)
        },
        "_ambiguous_word_addresses": set(),
        "labels": {
            "DELAY_500MS": {"address": 0, "line": 1},
            "DELAY_OUTER_LOOP": {"address": 2, "line": 3},
            "DELAY_MIDDLE_LOOP": {"address": 4, "line": 5},
            "DELAY_INNER_LOOP": {"address": 6, "line": 7},
        },
    }


def file_model(
    rows: list[tuple[int, str, str]],
    *,
    labels: dict[str, int] | None = None,
    equ: dict[str, int] | None = None,
    owner_kinds: dict[int, str] | None = None,
    ambiguous: set[int] | None = None,
    duplicate_labels: set[str] | None = None,
) -> dict:
    instructions = [
        {
            "address": address,
            "op": op,
            "args": args,
            "line": index + 1,
            "source": f"{op} {args}".strip(),
        }
        for index, (address, op, args) in enumerate(rows)
    ]
    owners: dict[int, dict] = {}
    for index, (address, _op, _args) in enumerate(rows):
        owners.setdefault(
            address,
            {
                "line": index + 1,
                "kind": (owner_kinds or {}).get(address, "instruction"),
                "instruction_index": (
                    index
                    if (owner_kinds or {}).get(address, "instruction")
                    == "instruction"
                    else None
                ),
            },
        )
    return {
        "path": "delay.asm",
        "_instructions": instructions,
        "_equ_symbols": {
            name.upper(): {"name": name, "value": value, "line": 1, "uses": 1}
            for name, value in (equ or {}).items()
        },
        "_word_owners": owners,
        "_ambiguous_word_addresses": set(ambiguous or set()),
        "_duplicate_label_names": {
            name.upper() for name in (duplicate_labels or set())
        },
        "labels": {
            name.upper(): {"name": name, "address": address, "line": 1}
            for name, address in (labels or {"DELAY": rows[0][0]}).items()
        },
    }


class ClockAndDelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        profile = json.loads(PROFILE.read_text(encoding="utf-8-sig"))
        cls.clock_model = profile["clock_model"]
        cls.effects = load_instruction_effects(
            RULES / "instruction-reference.json"
        )

    def test_reset_0x34_derives_2mhz_from_16mhz_osc(self):
        self.assertEqual(
            derive_sck_hz(16_000_000, "reset", self.clock_model),
            2_000_000,
        )

    def test_original_three_level_counts_are_about_4_seconds_at_2mhz(self):
        result = simulate_delay(
            delay_model(32), "DELAY_500MS", 2_000_000, self.effects
        )

        self.assertIsInstance(result, DelayResult)
        self.assertEqual(result.cycles, 8_032_133)
        self.assertEqual(result.actual_us, 4_016_066.5)
        self.assertEqual(result.clrwdt_count, 2_000_000)
        self.assertEqual(result.steps, 6_024_098)

    def test_outer_four_is_within_one_percent_of_500ms(self):
        result = simulate_delay(
            delay_model(4), "DELAY_500MS", 2_000_000, self.effects
        )

        self.assertEqual(result.cycles, 1_004_021)
        self.assertEqual(result.actual_us, 502_010.5)
        error_percent = abs(result.actual_us - 500_000) / 500_000 * 100
        self.assertAlmostEqual(error_percent, 0.4021, places=10)
        self.assertLessEqual(error_percent, 1.0)

    def test_effect_cycles_rejects_missing_bool_and_unknown_metadata(self):
        for effect in ({}, {"cycles": True}, {"cycles": "sometimes"}):
            with self.subTest(effect=effect):
                with self.assertRaises(ValueError):
                    effect_cycles(effect)

    def test_clock_derivation_rejects_malformed_inputs(self):
        cases = [
            (True, "reset", self.clock_model),
            (16_000_000, 0x30, self.clock_model),
            (16_000_000, "reset", {}),
            (
                16_000_000,
                "reset",
                {**self.clock_model, "sckhl_bit": True},
            ),
            (
                16_000_000,
                "reset",
                {
                    **self.clock_model,
                    "divider_by_mode": {
                        **self.clock_model["divider_by_mode"],
                        "high": {
                            **self.clock_model["divider_by_mode"]["high"],
                            "4": True,
                        },
                    },
                },
            ),
        ]
        for osc_hz, sck_ps, model in cases:
            with self.subTest(osc_hz=osc_hz, sck_ps=sck_ps, model=model):
                with self.assertRaises(ValueError):
                    derive_sck_hz(osc_hz, sck_ps, model)

    def test_accelerated_countdown_is_equivalent_to_step_interpreter(self):
        model = delay_model(1)
        model["_instructions"][2]["args"] = "A,#3"
        model["_instructions"][4]["args"] = "A,#4"

        accelerated = _simulate_delay(
            model,
            "DELAY_500MS",
            2_000_000,
            self.effects,
            max_steps=10_000_000,
            accelerate=True,
        )
        stepped = _simulate_delay(
            model,
            "DELAY_500MS",
            2_000_000,
            self.effects,
            max_steps=10_000_000,
            accelerate=False,
        )

        self.assertEqual(accelerated, stepped)

    def test_instruction_forms_select_their_own_cycle_metadata(self):
        document = json.loads(
            (RULES / "instruction-reference.json").read_text(encoding="utf-8-sig")
        )
        cycle_by_form = {
            "MOV A,R": 2,
            "MOV R,A": 3,
            "MOV A,#K": 4,
            "CALL K": 5,
            "RET": 6,
        }
        document["variants"] = [
            {
                **variant,
                "cycles": cycle_by_form.get(variant["asm_syntax"], variant["cycles"]),
            }
            for variant in document["variants"]
        ]
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "instruction-reference.json"
            reference.write_text(json.dumps(document), encoding="utf-8")
            effects = load_instruction_effects(reference)

        model = file_model(
            [
                (0, "MOV", "A,#1"),
                (1, "MOV", "80H,A"),
                (2, "MOV", "A,80H"),
                (3, "RET", ""),
            ]
        )
        result = simulate_delay(model, "DELAY", 1_000_000, effects)
        self.assertEqual(result.cycles, 20)

    def test_conflicting_supported_form_metadata_is_rejected(self):
        document = json.loads(
            (RULES / "instruction-reference.json").read_text(encoding="utf-8-sig")
        )
        immediate_mov = next(
            variant
            for variant in document["variants"]
            if variant["asm_syntax"] == "MOV A,#K"
        )
        document["variants"].append({**immediate_mov, "cycles": 2})
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "instruction-reference.json"
            reference.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_instruction_effects(reference)

    def test_duplicate_supported_form_cannot_hide_an_unsafe_variant(self):
        document = json.loads(
            (RULES / "instruction-reference.json").read_text(encoding="utf-8-sig")
        )
        immediate_mov = next(
            variant
            for variant in document["variants"]
            if variant["asm_syntax"] == "MOV A,#K"
        )
        document["variants"].append(
            {**immediate_mov, "delivery_policy": "restricted"}
        )
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "instruction-reference.json"
            reference.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_instruction_effects(reference)

    def test_every_required_delay_form_must_have_exactly_one_variant(self):
        base = json.loads(
            (RULES / "instruction-reference.json").read_text(encoding="utf-8-sig")
        )
        for form in ("MOV A,#K", "RET", "NOP", "CALL K"):
            with self.subTest(form=form):
                duplicate = next(
                    variant
                    for variant in base["variants"]
                    if variant["asm_syntax"] == form
                )
                document = {
                    **base,
                    "variants": [*base["variants"], dict(duplicate)],
                }
                with tempfile.TemporaryDirectory() as tmp:
                    reference = Path(tmp) / "instruction-reference.json"
                    reference.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_instruction_effects(reference)

    def test_supported_forms_require_safe_policy_and_valid_cycle_metadata(self):
        base = json.loads(
            (RULES / "instruction-reference.json").read_text(encoding="utf-8-sig")
        )
        mutations = [
            (
                "restricted_mov",
                "MOV A,#K",
                {"delivery_policy": "restricted"},
            ),
            (
                "open_ret",
                "RET",
                {"semantic_status": "open_hardware_semantics"},
            ),
            ("bool_cycle", "MOV A,R", {"cycles": True}),
            ("unknown_cycle", "CALL K", {"cycles": "sometimes"}),
        ]
        for name, form, changes in mutations:
            with self.subTest(name=name):
                document = {**base, "variants": [dict(item) for item in base["variants"]]}
                document["variants"] = [
                    {**variant, **changes}
                    if variant["asm_syntax"] == form
                    else variant
                    for variant in document["variants"]
                ]
                with tempfile.TemporaryDirectory() as tmp:
                    reference = Path(tmp) / "instruction-reference.json"
                    reference.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_instruction_effects(reference)

    def test_equ_jump_target_is_supported(self):
        model = file_model(
            [(0, "JMP", "DONE_ADDR"), (1, "NOP", ""), (2, "RET", "")],
            equ={"DONE_ADDR": 2},
        )
        result = simulate_delay(model, "DELAY", 1_000_000, self.effects)
        self.assertEqual(result.cycles, 6)

    def test_jump_target_with_duplicate_label_is_rejected(self):
        model = file_model(
            [(0, "JMP", "LOOP"), (1, "NOP", ""), (2, "RET", "")],
            labels={"DELAY": 0, "LOOP": 2},
            duplicate_labels={"LOOP"},
        )
        with self.assertRaises(ValueError):
            simulate_delay(model, "DELAY", 1_000_000, self.effects)

    def test_execution_rejects_gaps_data_and_ambiguous_words(self):
        cases = {
            "gap": file_model([(0, "NOP", ""), (2, "RET", "")]),
            "data": file_model(
                [(0, "NOP", ""), (1, "RET", "")],
                owner_kinds={1: "DB"},
            ),
            "dw": file_model(
                [(0, "NOP", ""), (1, "RET", "")],
                owner_kinds={1: "DW"},
            ),
            "raw_word": file_model(
                [(0, "NOP", ""), (1, "RET", "")],
                owner_kinds={1: "raw-word"},
            ),
            "ambiguous": file_model(
                [(0, "NOP", ""), (1, "NOP", ""), (1, "RET", "")],
                ambiguous={1},
            ),
        }
        for name, model in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    simulate_delay(model, "DELAY", 1_000_000, self.effects)

    def test_skip_requires_real_instruction_words(self):
        model = file_model(
            [
                (0, "MOV", "A,#1"),
                (1, "MOV", "80H,A"),
                (2, "DECSZR", "80H"),
                (4, "RET", ""),
            ]
        )
        with self.assertRaises(ValueError):
            simulate_delay(model, "DELAY", 1_000_000, self.effects)

    def test_equ_jump_to_data_or_ambiguous_word_is_rejected(self):
        cases = [
            file_model(
                [(0, "JMP", "DONE"), (2, "RET", "")],
                equ={"DONE": 2},
                owner_kinds={2: "DW"},
            ),
            file_model(
                [(0, "JMP", "DONE"), (2, "RET", "")],
                equ={"DONE": 2},
                ambiguous={2},
            ),
        ]
        for model in cases:
            with self.subTest(model=model):
                with self.assertRaises(ValueError):
                    simulate_delay(model, "DELAY", 1_000_000, self.effects)

    def test_unsupported_forms_missing_effect_step_cap_and_no_ret_fail_closed(self):
        cases = [
            (file_model([(0, "MOV", "80H,#1"), (1, "RET", "")]), self.effects, 20),
            (
                file_model(
                    [
                        (0, "MOV", "A,#1"),
                        (1, "MOV", "#80H,A"),
                        (2, "RET", ""),
                    ]
                ),
                self.effects,
                20,
            ),
            (file_model([(0, "RET", "A,#1")]), self.effects, 20),
            (file_model([(0, "CALL", "OTHER"), (1, "RET", "")]), self.effects, 20),
            (
                file_model([(0, "NOP", ""), (1, "RET", "")]),
                {key: value for key, value in self.effects.items() if key != "NOP"},
                20,
            ),
            (
                file_model([(0, "JMP", "DELAY")], labels={"DELAY": 0}),
                self.effects,
                2,
            ),
            (file_model([(0, "NOP", "")]), self.effects, 20),
        ]
        for model, effects, max_steps in cases:
            with self.subTest(model=model, max_steps=max_steps):
                with self.assertRaises(ValueError):
                    simulate_delay(
                        model,
                        "DELAY",
                        1_000_000,
                        effects,
                        max_steps=max_steps,
                    )


if __name__ == "__main__":
    unittest.main()
