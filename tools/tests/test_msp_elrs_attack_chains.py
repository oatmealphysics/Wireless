"""Offline tests for msp-elrs-attack-chains.py."""

import importlib.util
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "msp-elrs-attack-chains.py"


def _load():
    import sys
    name = "msp_elrs_attack_chains"
    spec = importlib.util.spec_from_file_location(name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@unittest.skip("QUARANTINE: uses fabricated attack chain steps; needs real attack chain execution data")
class StepBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_force_disarm_step(self):
        step = self.mod.step_force_disarm()
        self.assertEqual(step.msp_cmd, 251)
        self.assertGreater(step.elrs_packets, 0)
        self.assertGreater(step.inject_time_ms, 0)

    def test_reboot_step(self):
        step = self.mod.step_reboot(0)
        self.assertEqual(step.msp_cmd, 68)
        self.assertIn("firmware", step.name)

    def test_reboot_bootloader(self):
        step = self.mod.step_reboot(1)
        self.assertIn("bootloader", step.name)

    def test_motor_override_step(self):
        step = self.mod.step_motor_override()
        self.assertEqual(step.msp_cmd, 214)
        self.assertEqual(len(step.msp_payload), 16)

    def test_motor_override_custom(self):
        values = [2000, 1500, 1000, 1000, 0, 0, 0, 0]
        step = self.mod.step_motor_override(values)
        self.assertEqual(len(step.msp_payload), 16)

    def test_failsafe_poison_step(self):
        step = self.mod.step_failsafe_poison(delay=0, procedure=0)
        self.assertEqual(step.msp_cmd, 76)

    def test_gps_poison_step(self):
        step = self.mod.step_gps_poison(lat=40.0, lon=-74.0)
        self.assertEqual(step.msp_cmd, 201)

    def test_rx_config_rewrite_step(self):
        step = self.mod.step_rx_config_rewrite()
        self.assertEqual(step.msp_cmd, 45)

    def test_eeprom_write_requires_disarmed(self):
        step = self.mod.step_eeprom_write()
        self.assertTrue(step.requires_disarmed)

    def test_zero_pids_step(self):
        step = self.mod.step_zero_pids()
        self.assertEqual(step.msp_cmd, 0x92)
        self.assertEqual(len(step.msp_payload), 30)

    def test_flip_alignment_step(self):
        step = self.mod.step_flip_alignment()
        self.assertEqual(step.msp_cmd, 0x26)

    def test_rc_override_step(self):
        step = self.mod.step_rc_override()
        self.assertEqual(step.msp_cmd, 200)
        self.assertEqual(len(step.msp_payload), 16)


@unittest.skip("QUARANTINE: uses fabricated attack chain steps; needs real attack chain execution data")
class ChainBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_all_chains_build(self):
        chains = self.mod.build_chains()
        self.assertGreater(len(chains), 3)
        for name, chain in chains.items():
            self.assertGreater(len(chain.steps), 0, f"{name} has no steps")
            self.assertGreater(chain.total_packets, 0, f"{name} has zero packets")
            self.assertGreater(chain.total_inject_ms, 0)

    def test_expected_chains_present(self):
        chains = self.mod.build_chains()
        expected = ["drop", "hijack", "failsafe-ambush", "gps-redirect",
                    "destabilize", "full-kill"]
        for name in expected:
            self.assertIn(name, chains, f"missing chain: {name}")

    def test_drop_chain_structure(self):
        chains = self.mod.build_chains()
        drop = chains["drop"]
        self.assertEqual(len(drop.steps), 2)
        self.assertEqual(drop.steps[0].msp_cmd, 251)
        self.assertEqual(drop.steps[1].msp_cmd, 214)

    def test_hijack_chain_has_eeprom(self):
        chains = self.mod.build_chains()
        hijack = chains["hijack"]
        eeprom_steps = [s for s in hijack.steps if s.msp_cmd == 250]
        self.assertEqual(len(eeprom_steps), 1)
        self.assertTrue(eeprom_steps[0].requires_disarmed)

    def test_full_kill_most_packets(self):
        chains = self.mod.build_chains()
        full_kill = chains["full-kill"]
        for name, chain in chains.items():
            if name != "full-kill" and name != "hijack":
                self.assertGreaterEqual(full_kill.total_packets,
                                        chain.total_packets,
                                        f"full-kill should have >= packets than {name}")


@unittest.skip("QUARANTINE: uses fabricated attack chain steps; needs real attack chain execution data")
class MspFramingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_msp_command_format(self):
        frame = self.mod.build_msp_command(68, b"\x00")
        self.assertTrue(frame.startswith(b"$M<"))

    def test_crsf_frame_format(self):
        frame = self.mod.build_crsf_msp_frame(68, b"\x00")
        self.assertEqual(frame[0], 0xC8)

    def test_elrs_packet_count_positive(self):
        count = self.mod.elrs_packet_count(251, bytes([1, 0, 0, 0]))
        self.assertGreater(count, 0)

    def test_elrs_split_all_correct_size(self):
        crsf = self.mod.build_crsf_msp_frame(214, bytes(16))
        chunks = self.mod.split_crsf_to_elrs(crsf)
        for c in chunks:
            self.assertEqual(len(c), 5)


@unittest.skip("QUARANTINE: uses fabricated attack chain steps; needs real attack chain execution data")
class SimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load()

    def test_simulate_drop_chain(self):
        chains = self.mod.build_chains()
        result = self.mod.simulate_chain(chains["drop"])
        self.assertEqual(len(result.step_details), 2)
        self.assertGreater(result.total_packets, 0)
        self.assertGreater(result.total_time_ms, 0)
        self.assertGreater(result.connection_overhead_ms, 0)

    def test_simulate_with_ota(self):
        import hashlib
        uid = hashlib.md5(b"test").digest()[:6]
        chains = self.mod.build_chains()
        result = self.mod.simulate_chain(chains["drop"], uid=uid, show_ota=True)
        for detail in result.step_details:
            self.assertIn("ota_frames", detail)
            for frame_hex in detail["ota_frames"]:
                self.assertEqual(len(bytes.fromhex(frame_hex)), 8)

    def test_simulate_all_chains(self):
        chains = self.mod.build_chains()
        for name, chain in chains.items():
            result = self.mod.simulate_chain(chain)
            self.assertEqual(result.chain.name, name)
            self.assertGreater(result.total_time_ms, 0,
                               f"{name} has zero total time")

    def test_step_timing_increases(self):
        chains = self.mod.build_chains()
        result = self.mod.simulate_chain(chains["full-kill"])
        times = [d["start_time_ms"] for d in result.step_details]
        for i in range(1, len(times)):
            self.assertGreater(times[i], times[i - 1],
                               "step start times must increase")


if __name__ == "__main__":
    unittest.main()
