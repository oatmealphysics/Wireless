"""Regression test for the legacy ELRS chain's TinyHawk raw-channel order."""

import importlib.util
from pathlib import Path
import unittest


TOOL = Path(__file__).resolve().parents[1] / "exploit_chains" / "elrs_chain.py"
SPEC = importlib.util.spec_from_file_location("elrs_chain_target_mapping", TOOL)
chain = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(chain)


class QuietLogger:
    def step(self, *_args, **_kwargs):
        pass

    def success(self, *_args, **_kwargs):
        pass


@unittest.skip("QUARANTINE: uses fabricated UID/takeover packets; needs real chain takeover capture")
class TargetMappingTests(unittest.TestCase):
    def test_preview_places_minimum_throttle_in_raw_slot_zero(self):
        packets = chain.forge_takeover_packets(
            bytes.fromhex("112233445566"), 3, QuietLogger())
        raw = packets[0]["data"]
        values = chain.build_rcdata_std.__globals__["unpack_channels_4x10"](
            raw[1:6])
        # The core unpacker expands each stored 10-bit value to its 11-bit
        # representation, so centered 512 appears as 1024 here.
        self.assertEqual(values, [0, 1024, 1024, 1024])
        self.assertEqual((raw[6] >> 7) & 1, 0)


if __name__ == "__main__":
    unittest.main()
