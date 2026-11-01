"""Synthetic timing tests; these do not validate an IQ byte decoder."""

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "elrs-v3-hop-timing-check.py"
SPEC = importlib.util.spec_from_file_location("hop_timing", TOOL)
assert SPEC is not None and SPEC.loader is not None
hop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hop)

SYNTHETIC_UID = bytes.fromhex("010203040506")
PERIOD = 1 / 150
ANCHOR = 0.5
DURATION = 8.5
TRUE_PHASE = 80


def candidate(channel: int, stamp: float) -> dict:
    return {
        "channel_hz": hop.fhss.FIRST_HZ + channel * hop.fhss.STEP_HZ,
        "preamble_start_seconds": stamp,
        "phy_candidate": True,
        "packet_bytes_decoded": False,
    }


@unittest.skip("QUARANTINE: uses fabricated timing data; needs real hop timing observations from IQ capture")
class HopTimingTests(unittest.TestCase):
    def make_times(self):
        sequence = hop.fhss.build_v3_sequence(SYNTHETIC_UID)
        times = {40: [], 41: [], 42: []}
        for relative in range(-100, 400):
            channel = sequence[(TRUE_PHASE + relative) % len(sequence)]
            if channel not in times:
                continue
            for packet in range(hop.HOP_PACKETS):
                stamp = ANCHOR + (relative * hop.HOP_PACKETS + packet) * PERIOD
                if 0 <= stamp < DURATION:
                    times[channel].append(stamp)
        return {channel: sorted(values) for channel, values in times.items()}

    def test_correct_uid_and_phase_match_held_out_channels(self):
        times = self.make_times()
        self.assertGreaterEqual(len(times[41]), 12)
        anchor, period, visits = hop.fit_packet_clock(times[41])
        self.assertGreaterEqual(visits, 3)
        self.assertAlmostEqual(anchor, ANCHOR, places=7)
        self.assertAlmostEqual(period, PERIOD, places=9)
        scores = hop.compare_phases(SYNTHETIC_UID,
                                    {40: times[40], 42: times[42]},
                                    anchor, period, DURATION, 0.00065)
        winner = max(scores, key=lambda row: row["matched"])
        self.assertEqual(winner["phase"], TRUE_PHASE)
        self.assertEqual(winner["matched"], winner["expected"])
        self.assertGreater(winner["matched"], 20)
        self.assertLess(max(row["matched"] for row in scores
                            if row["phase"] != TRUE_PHASE), winner["matched"] // 2)

    def test_reject_incomplete_sync_and_bad_candidates(self):
        times = self.make_times()
        with self.assertRaisesRegex(ValueError, "three distinct complete"):
            hop.fit_packet_clock(times[41][:8])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text(json.dumps({**candidate(40, 1.0),
                                        "packet_bytes_decoded": True}) + "\n",
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid candidate"):
                hop.read_candidate_times(path, 40)
            path.write_text(json.dumps(candidate(42, 1.0)) + "\n",
                            encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid candidate"):
                hop.read_candidate_times(path, 40)

    def test_cli_output_contains_counts_not_uid_or_paths(self):
        times = self.make_times()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uid_path = root / "private-uid.hex"
            uid_path.write_text(SYNTHETIC_UID.hex(), encoding="ascii")
            paths = {}
            for channel, stamps in times.items():
                path = root / f"channel-{channel}.jsonl"
                path.write_text("".join(json.dumps(candidate(channel, t)) + "\n"
                                        for t in stamps), encoding="utf-8")
                paths[channel] = path
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = hop.main([
                    "--uid-file", str(uid_path),
                    "--sync-candidates", str(paths[41]),
                    "--channel", f"40={paths[40]}",
                    "--channel", f"42={paths[42]}",
                    "--duration-seconds", str(DURATION),
                ])
            self.assertEqual(code, 0)
            result = json.loads(stdout.getvalue())
            self.assertFalse(result["packet_bytes_decoded"])
            self.assertEqual(result["best_phase"], TRUE_PHASE)
            self.assertEqual(stderr.getvalue(), "")
            combined = stdout.getvalue() + stderr.getvalue()
            self.assertNotIn(SYNTHETIC_UID.hex(), combined)
            self.assertNotIn(str(root), combined)


if __name__ == "__main__":
    unittest.main()
