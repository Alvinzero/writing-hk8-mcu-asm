from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from hk8asm import GateError, validate_seven_segment_contract


def pins() -> dict:
    return {
        "segments": {
            "port": "PB",
            "bits": list(range(8)),
            "direction": "output",
            "drive": "push_pull",
            "active_level": "dynamic",
            "initial_level": "high",
            "preserve_unowned_bits": False,
            "port_ownership": "exclusive",
        },
        "digits_cc": {
            "port": "PA",
            "bits": [5, 6],
            "direction": "output",
            "drive": "push_pull",
            "active_level": "low",
            "initial_state": "off",
            "preserve_unowned_bits": True,
            "port_ownership": "shared",
        },
        "digits_ca": {
            "port": "PA",
            "bits": [2, 3],
            "direction": "output",
            "drive": "push_pull",
            "active_level": "high",
            "initial_state": "off",
            "preserve_unowned_bits": True,
            "port_ownership": "shared",
        },
    }


def request() -> dict:
    return {
        "seven_segment": {
            "driver": "gpio_dynamic_scan",
            "segment_pin": "segments",
            "segment_mapping": {
                "A": 7,
                "B": 6,
                "C": 5,
                "D": 4,
                "E": 3,
                "F": 2,
                "G": 1,
                "DP": 0,
            },
            "external_inversion": {
                "status": "confirmed",
                "segments": False,
                "digit_select": False,
            },
            "current_limit": {
                "status": "confirmed",
                "description": "每段串联限流电阻",
            },
            "drive_capability_confirmed": True,
            "digits": [
                {
                    "visual_index": 0,
                    "pin_contract": "digits_cc",
                    "bit": 5,
                    "topology": "common_cathode",
                    "com_active_level": "low",
                    "segment_active_level": "high",
                },
                {
                    "visual_index": 1,
                    "pin_contract": "digits_cc",
                    "bit": 6,
                    "topology": "common_cathode",
                    "com_active_level": "low",
                    "segment_active_level": "high",
                },
                {
                    "visual_index": 2,
                    "pin_contract": "digits_ca",
                    "bit": 2,
                    "topology": "common_anode",
                    "com_active_level": "high",
                    "segment_active_level": "low",
                },
                {
                    "visual_index": 3,
                    "pin_contract": "digits_ca",
                    "bit": 3,
                    "topology": "common_anode",
                    "com_active_level": "high",
                    "segment_active_level": "low",
                },
            ],
        }
    }


class SevenSegmentContractTests(unittest.TestCase):
    def test_mixed_common_contract_is_accepted(self):
        validate_seven_segment_contract(request(), pins())

    def test_segment_level_must_match_topology(self):
        payload = request()
        payload["seven_segment"]["digits"][0]["segment_active_level"] = "low"

        with self.assertRaises(GateError) as raised:
            validate_seven_segment_contract(payload, pins())

        self.assertEqual(raised.exception.code, "INVALID_REQUEST")

    def test_external_inversion_must_be_confirmed(self):
        payload = request()
        payload["seven_segment"]["external_inversion"]["status"] = "unconfirmed"

        with self.assertRaises(GateError) as raised:
            validate_seven_segment_contract(payload, pins())

        self.assertEqual(raised.exception.code, "BOARD_INPUT_UNCONFIRMED")

    def test_duplicate_com_pin_is_rejected(self):
        payload = request()
        duplicate = copy.deepcopy(payload["seven_segment"]["digits"][0])
        duplicate["visual_index"] = 1
        payload["seven_segment"]["digits"] = [
            payload["seven_segment"]["digits"][0],
            duplicate,
        ]

        with self.assertRaises(GateError) as raised:
            validate_seven_segment_contract(payload, pins())

        self.assertEqual(raised.exception.code, "INVALID_REQUEST")


if __name__ == "__main__":
    unittest.main()
