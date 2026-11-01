#!/usr/bin/env python3
"""Offline model of a Betaflight 4.4.2 ELRS v3 rate-switch latch.

This model records a source-derived state transition; it does not build a
packet, use a UID, access a serial port, open an SDR, or transmit.  It models
three properties of the pinned 4.4.2 source:

* a CRC-valid, UID-matching SYNC can select a different supported air rate;
* the subsequent forced disconnect keeps ``lockRFmode`` set and retunes the
  SX1280 to that rate's common initial/sync channel; and
* a CRC-valid type-3 TLM packet is reported as new RX data without replacing
  the retained RC payload, even while the ELRS connection is disconnected.

The output is a host-only hypothesis record, not evidence that a waveform is
accepted by the owned receiver or that failsafe is suppressed on hardware.
Python 3.10+, standard library only.
"""

from __future__ import annotations

import argparse
import copy
import json

SCHEMA = "elrs-v3-rate-switch-latch-model-v1"
INITIAL_CHANNEL = 41
GENERIC_RX_DEADLINE_MS = 100
SLOW_CYCLE_MULTIPLIER = 10

# OTA rate code -> source table values from Betaflight 4.4.2
# expresslrs_common.c::airRateConfig and airRateIndexToIndex24.
RATE_TABLE = {
    4: {"index": 0, "label": "LoRa 500 Hz", "interval_us": 2000,
        "hop_interval": 4},
    6: {"index": 1, "label": "LoRa 250 Hz", "interval_us": 4000,
        "hop_interval": 4},
    7: {"index": 2, "label": "LoRa 150 Hz", "interval_us": 6666,
        "hop_interval": 4},
    9: {"index": 3, "label": "LoRa 50 Hz", "interval_us": 20000,
        "hop_interval": 2},
}


def cycle_interval_ms(rate_code: int) -> int:
    """Port the integer expression used by 4.4.2 ``setRfLinkRate``."""
    rate = RATE_TABLE[rate_code]
    return (11 * 80 * rate["hop_interval"] * rate["interval_us"]) // 10_000


def connected_state(*, retained_rc: bool = True) -> dict:
    """Return the relevant state after a legitimate 150 Hz connection."""
    return {
        "connection": "connected",
        # receiver.rateIndex: the configured/search-rate fallback used when
        # a SYNC carries a reserved OTA rate code.  Forced lostConnection()
        # retunes modParams but does not update this field.
        "scan_rate_code": 7,
        "current_rate_code": 7,
        "next_rate_code": 7,
        "lock_rf_mode": True,
        "timer_running": True,
        "radio_channel": "fhss",
        "switch_mode": "hybrid",
        "config_changed": False,
        "retained_rc_payload": retained_rc,
        "generic_frame_complete": False,
        "retained_rc_replayed": False,
        "generic_signal_deadline_ms": None,
        "rf_mode_cycle_multiplier": 1,
    }


def _event_state(state: dict) -> dict:
    """Copy persistent state while clearing the previous packet's outcome."""
    result = copy.deepcopy(state)
    result["sync_accepted"] = False
    result["identity_rejected_after_crc"] = False
    result["forced_rate_change"] = False
    result["tlm_accepted"] = False
    result["generic_frame_complete"] = False
    result["retained_rc_replayed"] = False
    return result


def resolve_rate_code(raw_code: int, fallback_code: int) -> int:
    """Port airRateIndexToIndex24's reserved-code fallback."""
    if not isinstance(raw_code, int) or isinstance(raw_code, bool) or not 0 <= raw_code <= 15:
        raise ValueError("OTA rate code must be a four-bit integer")
    return raw_code if raw_code in RATE_TABLE else fallback_code


def process_sync(state: dict, *, rate_code: int, crc_valid: bool = True,
                 identity_match: bool = True,
                 sync_state_matches: bool = True,
                 switch_mode: str = "hybrid") -> dict:
    """Apply the relevant SYNC and connection-update transitions.

    ``sync_state_matches`` combines the nonce, FHSS-index, and model-match
    predicates used by the pinned source after its partial UID check.
    """
    result = _event_state(state)
    if not crc_valid:
        result["sync_accepted"] = False
        return result

    # lastValidPacketMs is updated after the CRC check, and the outer packet
    # switch returns DATA even if processRFSyncPacket rejects the identity
    # fields.  A rejected SYNC is therefore a second fixed-channel carrier for
    # the retained-payload issue without altering nonce, rate, or FHSS state.
    result["generic_frame_complete"] = True
    result["retained_rc_replayed"] = bool(result["retained_rc_payload"])
    result["generic_signal_deadline_ms"] = GENERIC_RX_DEADLINE_MS
    result["rf_mode_cycle_multiplier"] = SLOW_CYCLE_MULTIPLIER
    if not identity_match:
        result["sync_accepted"] = False
        result["identity_rejected_after_crc"] = True
        return result

    result["sync_accepted"] = True
    result["identity_rejected_after_crc"] = False
    result["next_rate_code"] = resolve_rate_code(
        rate_code, result["scan_rate_code"])
    if switch_mode != result["switch_mode"]:
        result["switch_mode"] = switch_mode
        result["config_changed"] = True

    # A valid SYNC received while disconnected, or one whose nonce, FHSS
    # index, or model-match state differs, enters tentative state.  A stopped
    # timer is resumed before the main-loop rate-mismatch check.
    if result["connection"] == "disconnected" or not sync_state_matches:
        result["connection"] = "tentative"
        result["timer_running"] = True

    # handleConnectionStateUpdate() immediately forces loss when the selected
    # rate differs. lostConnection() stops the timer and calls setRfLinkRate(),
    # but deliberately does not clear lockRFmode.
    if (result["connection"] != "disconnected" and
            result["current_rate_code"] != result["next_rate_code"]):
        result["connection"] = "disconnected"
        result["timer_running"] = False
        result["current_rate_code"] = result["next_rate_code"]
        result["radio_channel"] = INITIAL_CHANNEL
        result["forced_rate_change"] = True
        # lostConnection() resets the slow-search multiplier even though the
        # packet handler set it to ten earlier in the same loop iteration.
        result["rf_mode_cycle_multiplier"] = 1
    else:
        result["forced_rate_change"] = False
    return result


def process_tlm(state: dict, *, crc_valid: bool = True,
                recognized_type: bool = True) -> dict:
    """Model the 4.4.2 type-3 no-op followed by the generic RX status."""
    result = _event_state(state)
    if not crc_valid or not recognized_type:
        result["tlm_accepted"] = False
        return result
    result["tlm_accepted"] = True
    result["generic_frame_complete"] = True
    result["retained_rc_replayed"] = bool(result["retained_rc_payload"])
    result["generic_signal_deadline_ms"] = GENERIC_RX_DEADLINE_MS
    result["rf_mode_cycle_multiplier"] = SLOW_CYCLE_MULTIPLIER
    return result


def can_cycle_rate(state: dict) -> bool:
    """Mirror the state guards at the start of ``cycleRfMode``."""
    return state["connection"] != "connected" and not state["lock_rf_mode"]


def build_report() -> dict:
    paths = []
    for target_code in (4, 6, 9):
        after_sync = process_sync(connected_state(), rate_code=target_code)
        after_tlm = process_tlm(after_sync)
        after_rejected_sync = process_sync(
            after_sync, rate_code=target_code, identity_match=False)
        paths.append({
            "target_rate_code": target_code,
            "target_rate": RATE_TABLE[target_code]["label"],
            "normal_cycle_interval_ms": cycle_interval_ms(target_code),
            "slow_cycle_interval_ms": (
                cycle_interval_ms(target_code) * SLOW_CYCLE_MULTIPLIER),
            "post_forced_loss_cycle_multiplier": (
                after_sync["rf_mode_cycle_multiplier"]),
            "post_keepalive_cycle_multiplier": (
                after_rejected_sync["rf_mode_cycle_multiplier"]),
            "forced_disconnect": after_sync["connection"] == "disconnected",
            "timer_stopped": not after_sync["timer_running"],
            "initial_channel": after_sync["radio_channel"],
            "lock_survives": after_sync["lock_rf_mode"],
            "automatic_rate_cycle_possible": can_cycle_rate(after_sync),
            "persistent_config_change_requested": after_sync["config_changed"],
            "recognized_tlm_reports_complete_frame": (
                after_tlm["generic_frame_complete"]),
            "rejected_sync_reports_complete_frame": (
                after_rejected_sync["generic_frame_complete"]),
            "retained_rc_replayed": after_tlm["retained_rc_replayed"],
            "generic_rx_deadline_ms": after_tlm["generic_signal_deadline_ms"],
        })
    return {
        "schema": SCHEMA,
        "source_version": "Betaflight 4.4.2",
        "transmit_enabled": False,
        "live_validated": False,
        "starting_state": "legitimately connected at LoRa 150 Hz",
        "paths": paths,
        "limits": [
            "The model assumes a CRC-valid UID-matching SYNC reaches the receiver.",
            "Same-rate connected-state retention assumes matching nonce, FHSS index, and model-match state.",
            "It does not establish SDR waveform acceptance or precise RF timing.",
            "Retained payload reuse does not prove the live failsafe result.",
            "Unsafe retained arm or throttle state could create physical risk.",
            "Changing switch mode is excluded because it requests a config save.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true",
                        help="Emit the host-only model as JSON")
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print("Betaflight 4.4.2 ELRS v3 rate-switch latch (offline model)")
        for path in report["paths"]:
            print(
                f"rate {path['target_rate_code']}: forced_disconnect="
                f"{str(path['forced_disconnect']).lower()}, fixed_channel="
                f"{path['initial_channel']}, auto_cycle="
                f"{str(path['automatic_rate_cycle_possible']).lower()}, "
                f"retained_rc={str(path['retained_rc_replayed']).lower()}"
            )
        print("No RF, serial, UID, or device access is implemented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
