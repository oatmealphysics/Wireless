#!/usr/bin/env python3
"""
Safe Bench Controller — Plan, verify, and gate RF bench tests.

One command creates a bench plan, verifies prerequisites, and stops before
any transmission. Every RF action requires explicit two-step arming:
  1. --arm flag on the command line
  2. Interactive confirmation prompt (unless --yes is also passed)

The controller never transmits, flashes, or modifies hardware. It produces
a validated plan file that a separate TX tool consumes. The absolute
no-flashing rule for the EMAX E8 and TinyHawk III is enforced at every stage.

Usage:
    # Create a bench plan (default: dry-run, no RF)
    python3 tools/bench-controller.py plan --phrase "iloveflying"

    # Verify prerequisites for a plan
    python3 tools/bench-controller.py verify --plan work/bench/plan_20261026.json

    # Review a plan with full parameter dump
    python3 tools/bench-controller.py review --plan work/bench/plan_20261026.json

    # Arm a plan for execution (requires --arm + confirmation)
    python3 tools/bench-controller.py arm --plan work/bench/plan_20261026.json --arm

    # Abort a previously armed plan
    python3 tools/bench-controller.py abort --plan work/bench/plan_20261026.json

For CTF research and authorized security testing only.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from elrs_core.crc import Crc2Byte, ELRS_CRC14_POLY
from elrs_core.v3_compat import (
    v3_uid_crc_init, v3_uid_seed, v3_sync_channel, v3_build_sequence,
    v3_build_sync, uid_crc_init, uid_fhss_seed, sync_channel,
    V3_OTA_VERSION_ID,
)
from elrs_core.fhss import FHSS_SEQUENCE_LEN

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ISM2G4_FREQ_COUNT = 80
ISM2G4_FREQ_START_HZ = 2400400000
ISM2G4_FREQ_STEP_HZ = 1000000
BENCH_DIR_DEFAULT = "work/bench"

BLOCKED_DEVICES = frozenset({"EMAX_E8", "TINYHAWK_III"})

PLAN_VERSION = 1

VALID_TEST_TYPES = frozenset({
    "passive_capture",
    "sync_injection",
    "rc_injection",
    "msp_injection",
    "fhss_verification",
})

VALID_ABORT_CONDITIONS = frozenset({
    "timeout",
    "unexpected_ack",
    "signal_loss",
    "operator_abort",
    "failsafe_triggered",
    "frequency_violation",
    "power_violation",
})

MAX_POWER_DBM = 10
MAX_DURATION_S = 60
DEFAULT_DURATION_S = 5
DEFAULT_POWER_DBM = 0

V3_LORA_RATES = {
    0: {"name": "200Hz", "sf": 6, "bw": 500, "cr": "4/7"},
    1: {"name": "100Hz", "sf": 7, "bw": 500, "cr": "4/7"},
    2: {"name": "150Hz", "sf": 7, "bw": 800, "cr": "4/7"},
    3: {"name": "50Hz", "sf": 8, "bw": 500, "cr": "4/7"},
    4: {"name": "25Hz", "sf": 9, "bw": 500, "cr": "4/7"},
}


def _log(level: str, msg: str, *, file=sys.stderr):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [bench] {level}: {msg}", file=file)


def log_info(msg: str):
    _log("INFO", msg)


def log_warn(msg: str):
    _log("WARN", msg)


def log_error(msg: str):
    _log("ERROR", msg)


def log_safety(msg: str):
    _log("SAFETY", f"\033[1;31m{msg}\033[0m")


# ---------------------------------------------------------------------------
# UID derivation (from binding phrase)
# ---------------------------------------------------------------------------

def phrase_to_uid(phrase: str) -> bytes:
    md5 = hashlib.md5(phrase.encode("utf-8")).digest()
    return md5[:6]


# ---------------------------------------------------------------------------
# Plan creation
# ---------------------------------------------------------------------------

def create_plan(args) -> dict:
    """Build a bench test plan from CLI arguments."""
    uid = phrase_to_uid(args.phrase)
    ota_version = args.ota_version

    crc_init = uid_crc_init(uid, ota_version)
    fhss_seed = uid_fhss_seed(uid, ota_version)
    sync_ch = sync_channel(ISM2G4_FREQ_COUNT, ota_version)
    fhss_seq = v3_build_sequence(uid, ISM2G4_FREQ_COUNT) if ota_version == 3 else []

    freq_hz = ISM2G4_FREQ_START_HZ + sync_ch * ISM2G4_FREQ_STEP_HZ

    plan = {
        "version": PLAN_VERSION,
        "created": datetime.now(timezone.utc).isoformat(),
        "status": "draft",
        "armed": False,
        "armed_at": None,
        "operator": os.environ.get("USER", "unknown"),
        "test_type": args.test_type,
        "target": {
            "device": "TINYHAWK_III",
            "firmware": "Betaflight 4.4.2",
            "ota_version": ota_version,
            "rate_index": args.rate_index,
            "rate_params": V3_LORA_RATES.get(args.rate_index, {}),
            "phy": "LoRa",
            "model_id": 255,
            "model_xor_zero": True,
        },
        "rf_parameters": {
            "frequency_hz": freq_hz,
            "sync_channel": sync_ch,
            "freq_count": ISM2G4_FREQ_COUNT,
            "power_dbm": args.power_dbm,
            "max_power_dbm": MAX_POWER_DBM,
            "duration_s": args.duration_s,
            "max_duration_s": MAX_DURATION_S,
            "modulation": "LoRa",
        },
        "crypto": {
            "uid_hex": uid.hex(),
            "crc_init": f"0x{crc_init:04X}",
            "crc_init_int": crc_init,
            "fhss_seed": fhss_seed,
            "fhss_sequence_len": len(fhss_seq) if fhss_seq else FHSS_SEQUENCE_LEN,
            "fhss_first_10": fhss_seq[:10] if fhss_seq else [],
        },
        "expected_receiver_state": {
            "initial": "disarmed, bound to E8, STABILIZE mode",
            "during_test": args.expected_state or "RC data accepted, no mode change",
            "after_test": "disarmed, failsafe if link lost >1s",
            "failsafe_behavior": "drop to failsafe after ELRS link timeout",
        },
        "abort_conditions": list(args.abort_conditions),
        "evidence_capture": {
            "iq_capture": True,
            "log_file": True,
            "capture_dir": str(Path(BENCH_DIR_DEFAULT) / "captures"),
            "capture_format": ".complex8",
            "sample_rate_hz": 10_000_000,
            "center_freq_hz": freq_hz,
        },
        "safety": {
            "no_flash_devices": list(BLOCKED_DEVICES),
            "no_flash_enforced": True,
            "dry_run_default": True,
            "props_removed": args.props_removed,
            "constrained_rf": True,
            "max_power_dbm": MAX_POWER_DBM,
            "max_duration_s": MAX_DURATION_S,
            "arm_required": True,
            "confirmation_required": True,
        },
    }

    # UID is sensitive — store only under work/
    plan["crypto"]["uid_stored_at"] = "work/bench/ (not in git)"

    return plan


# ---------------------------------------------------------------------------
# Plan verification
# ---------------------------------------------------------------------------

class PrerequisiteError:
    def __init__(self, check: str, message: str, blocking: bool = True):
        self.check = check
        self.message = message
        self.blocking = blocking

    def __repr__(self):
        kind = "BLOCK" if self.blocking else "WARN"
        return f"[{kind}] {self.check}: {self.message}"


def verify_plan(plan: dict) -> list[PrerequisiteError]:
    """Verify a plan's prerequisites. Returns a list of errors/warnings."""
    errors: list[PrerequisiteError] = []

    if plan.get("version") != PLAN_VERSION:
        errors.append(PrerequisiteError(
            "plan_version",
            f"Expected plan version {PLAN_VERSION}, got {plan.get('version')}",
        ))

    # Safety checks
    safety = plan.get("safety", {})
    if not safety.get("no_flash_enforced"):
        errors.append(PrerequisiteError(
            "no_flash",
            "no-flash enforcement is disabled — this violates the absolute constraint",
        ))

    if not safety.get("arm_required"):
        errors.append(PrerequisiteError(
            "arm_gate",
            "arm requirement is disabled — plans must require explicit arming",
        ))

    for device in BLOCKED_DEVICES:
        if device not in safety.get("no_flash_devices", []):
            errors.append(PrerequisiteError(
                "no_flash_device",
                f"{device} missing from no-flash device list",
            ))

    # RF parameter bounds
    rf = plan.get("rf_parameters", {})
    power = rf.get("power_dbm", 0)
    if power > MAX_POWER_DBM:
        errors.append(PrerequisiteError(
            "power_limit",
            f"Power {power} dBm exceeds maximum {MAX_POWER_DBM} dBm",
        ))

    duration = rf.get("duration_s", 0)
    if duration > MAX_DURATION_S:
        errors.append(PrerequisiteError(
            "duration_limit",
            f"Duration {duration}s exceeds maximum {MAX_DURATION_S}s",
        ))

    freq = rf.get("frequency_hz", 0)
    if freq < ISM2G4_FREQ_START_HZ or freq > ISM2G4_FREQ_START_HZ + ISM2G4_FREQ_COUNT * ISM2G4_FREQ_STEP_HZ:
        errors.append(PrerequisiteError(
            "frequency_range",
            f"Frequency {freq} Hz outside 2.4 GHz ISM band",
        ))

    # Test type
    test_type = plan.get("test_type", "")
    if test_type not in VALID_TEST_TYPES:
        errors.append(PrerequisiteError(
            "test_type",
            f"Unknown test type '{test_type}', expected one of {sorted(VALID_TEST_TYPES)}",
        ))

    # Abort conditions
    aborts = set(plan.get("abort_conditions", []))
    required_aborts = {"timeout", "operator_abort"}
    missing = required_aborts - aborts
    if missing:
        errors.append(PrerequisiteError(
            "abort_conditions",
            f"Missing required abort conditions: {sorted(missing)}",
        ))

    invalid_aborts = aborts - VALID_ABORT_CONDITIONS
    if invalid_aborts:
        errors.append(PrerequisiteError(
            "abort_conditions",
            f"Unknown abort conditions: {sorted(invalid_aborts)}",
            blocking=False,
        ))

    # Crypto sanity
    crypto = plan.get("crypto", {})
    uid_hex = crypto.get("uid_hex", "")
    if len(uid_hex) != 12:
        errors.append(PrerequisiteError(
            "uid",
            f"UID hex must be 12 characters (6 bytes), got {len(uid_hex)}",
        ))

    # Props check
    if not safety.get("props_removed"):
        errors.append(PrerequisiteError(
            "props_removed",
            "Props not confirmed removed — bench tests require props removed for safety",
            blocking=False,
        ))

    # Evidence capture
    evidence = plan.get("evidence_capture", {})
    if not evidence.get("iq_capture") and not evidence.get("log_file"):
        errors.append(PrerequisiteError(
            "evidence",
            "No evidence capture configured — at least one method required",
        ))

    return errors


# ---------------------------------------------------------------------------
# Plan arming
# ---------------------------------------------------------------------------

def arm_plan(plan: dict, confirmed: bool = False) -> tuple[dict, bool]:
    """Arm a plan for execution. Returns (updated_plan, success)."""
    if plan.get("armed"):
        log_warn("Plan is already armed")
        return plan, True

    if plan.get("status") == "aborted":
        log_error("Cannot arm an aborted plan — create a new one")
        return plan, False

    errors = verify_plan(plan)
    blockers = [e for e in errors if e.blocking]
    if blockers:
        log_error("Cannot arm plan — blocking prerequisites:")
        for e in blockers:
            log_error(f"  {e}")
        return plan, False

    warnings = [e for e in errors if not e.blocking]
    if warnings:
        log_warn("Plan has warnings:")
        for w in warnings:
            log_warn(f"  {w}")

    if not confirmed:
        log_safety("ARMING GATE — this plan authorizes RF transmission")
        _print_plan_summary(plan)
        print("\nType 'ARM' to confirm: ", end="", file=sys.stderr)
        try:
            response = input()
        except (EOFError, KeyboardInterrupt):
            log_info("Arming cancelled")
            return plan, False
        if response.strip() != "ARM":
            log_info("Arming cancelled — expected 'ARM'")
            return plan, False

    plan["armed"] = True
    plan["armed_at"] = datetime.now(timezone.utc).isoformat()
    plan["status"] = "armed"
    log_info("Plan ARMED — ready for execution by TX tool")
    log_safety("No transmission occurs from this controller. "
               "The armed plan file is consumed by the TX tool.")
    return plan, True


def abort_plan(plan: dict) -> dict:
    """Abort a plan, disarming it."""
    plan["armed"] = False
    plan["status"] = "aborted"
    plan["aborted_at"] = datetime.now(timezone.utc).isoformat()
    log_info("Plan ABORTED")
    return plan


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _print_plan_summary(plan: dict):
    rf = plan.get("rf_parameters", {})
    target = plan.get("target", {})
    safety = plan.get("safety", {})
    crypto = plan.get("crypto", {})

    print("\n=== BENCH TEST PLAN ===", file=sys.stderr)
    print(f"  Test type:    {plan.get('test_type')}", file=sys.stderr)
    print(f"  Status:       {plan.get('status')}", file=sys.stderr)
    print(f"  Armed:        {plan.get('armed')}", file=sys.stderr)
    print(f"  Created:      {plan.get('created')}", file=sys.stderr)
    print(f"\n  Target:", file=sys.stderr)
    print(f"    Device:     {target.get('device')}", file=sys.stderr)
    print(f"    Firmware:   {target.get('firmware')}", file=sys.stderr)
    print(f"    OTA:        v{target.get('ota_version')}", file=sys.stderr)
    print(f"    Rate:       {target.get('rate_params', {}).get('name', 'unknown')}", file=sys.stderr)
    print(f"    PHY:        {target.get('phy')}", file=sys.stderr)
    print(f"\n  RF Parameters:", file=sys.stderr)
    print(f"    Frequency:  {rf.get('frequency_hz', 0) / 1e6:.1f} MHz", file=sys.stderr)
    print(f"    Sync ch:    {rf.get('sync_channel')}", file=sys.stderr)
    print(f"    Power:      {rf.get('power_dbm')} dBm (max {rf.get('max_power_dbm')})", file=sys.stderr)
    print(f"    Duration:   {rf.get('duration_s')}s (max {rf.get('max_duration_s')})", file=sys.stderr)
    print(f"    Modulation: {rf.get('modulation')}", file=sys.stderr)
    print(f"\n  Crypto:", file=sys.stderr)
    print(f"    CRC init:   {crypto.get('crc_init')}", file=sys.stderr)
    print(f"    FHSS seed:  {crypto.get('fhss_seed')}", file=sys.stderr)
    print(f"    Seq len:    {crypto.get('fhss_sequence_len')}", file=sys.stderr)
    print(f"\n  Safety:", file=sys.stderr)
    print(f"    No-flash:   {safety.get('no_flash_devices')}", file=sys.stderr)
    print(f"    Props off:  {safety.get('props_removed')}", file=sys.stderr)
    print(f"    Dry-run:    {safety.get('dry_run_default')}", file=sys.stderr)
    print(f"    Max power:  {safety.get('max_power_dbm')} dBm", file=sys.stderr)
    print(f"    Max time:   {safety.get('max_duration_s')}s", file=sys.stderr)
    print(f"\n  Expected receiver state:", file=sys.stderr)
    for k, v in plan.get("expected_receiver_state", {}).items():
        print(f"    {k}: {v}", file=sys.stderr)
    print(f"\n  Abort conditions: {', '.join(plan.get('abort_conditions', []))}", file=sys.stderr)
    print("========================", file=sys.stderr)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def save_plan(plan: dict, output_dir: str) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filepath = out / f"plan_{ts}.json"
    with open(filepath, "w") as f:
        json.dump(plan, f, indent=2)
    log_info(f"Plan saved to {filepath}")
    return filepath


def load_plan(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safe Bench Controller — plan, verify, and gate RF bench tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # plan
    p_plan = sub.add_parser("plan", help="Create a new bench test plan")
    p_plan.add_argument("--phrase", required=True,
                        help="ELRS binding phrase (derives UID)")
    p_plan.add_argument("--ota-version", type=int, default=3, choices=[3, 4],
                        help="OTA version (default: 3 for Betaflight 4.4.2)")
    p_plan.add_argument("--rate-index", type=int, default=2, choices=range(5),
                        help="ELRS rate index (default: 2 = 150Hz SF7 BW800)")
    p_plan.add_argument("--test-type", default="passive_capture",
                        choices=sorted(VALID_TEST_TYPES),
                        help="Type of bench test (default: passive_capture)")
    p_plan.add_argument("--power-dbm", type=int, default=DEFAULT_POWER_DBM,
                        help=f"TX power in dBm (default: {DEFAULT_POWER_DBM}, max: {MAX_POWER_DBM})")
    p_plan.add_argument("--duration-s", type=int, default=DEFAULT_DURATION_S,
                        help=f"Test duration in seconds (default: {DEFAULT_DURATION_S}, max: {MAX_DURATION_S})")
    p_plan.add_argument("--expected-state", default=None,
                        help="Expected receiver state during test")
    p_plan.add_argument("--props-removed", action="store_true", default=False,
                        help="Confirm props are removed from the drone")
    p_plan.add_argument("--abort-conditions", nargs="+",
                        default=["timeout", "operator_abort", "signal_loss",
                                 "failsafe_triggered"],
                        help="Abort conditions for the test")
    p_plan.add_argument("--output-dir", default=BENCH_DIR_DEFAULT,
                        help=f"Output directory for plan file (default: {BENCH_DIR_DEFAULT})")

    # verify
    p_verify = sub.add_parser("verify", help="Verify prerequisites for a plan")
    p_verify.add_argument("--plan", required=True, help="Path to plan JSON file")

    # review
    p_review = sub.add_parser("review", help="Review a plan with full details")
    p_review.add_argument("--plan", required=True, help="Path to plan JSON file")

    # arm
    p_arm = sub.add_parser("arm", help="Arm a plan for execution")
    p_arm.add_argument("--plan", required=True, help="Path to plan JSON file")
    p_arm.add_argument("--arm", action="store_true", required=True,
                       help="Required flag to confirm intent to arm")
    p_arm.add_argument("--yes", action="store_true", default=False,
                       help="Skip interactive confirmation (use with caution)")

    # abort
    p_abort = sub.add_parser("abort", help="Abort an armed plan")
    p_abort.add_argument("--plan", required=True, help="Path to plan JSON file")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "plan":
        if args.power_dbm > MAX_POWER_DBM:
            log_error(f"Power {args.power_dbm} dBm exceeds maximum {MAX_POWER_DBM} dBm")
            sys.exit(1)
        if args.duration_s > MAX_DURATION_S:
            log_error(f"Duration {args.duration_s}s exceeds maximum {MAX_DURATION_S}s")
            sys.exit(1)

        plan = create_plan(args)
        errors = verify_plan(plan)

        _print_plan_summary(plan)

        blockers = [e for e in errors if e.blocking]
        warnings = [e for e in errors if not e.blocking]

        if warnings:
            print("\nWarnings:", file=sys.stderr)
            for w in warnings:
                log_warn(f"  {w}")

        if blockers:
            print("\nBlockers:", file=sys.stderr)
            for e in blockers:
                log_error(f"  {e}")
            log_error("Plan has blocking issues — fix before arming")

        filepath = save_plan(plan, args.output_dir)
        log_info("Plan created. Next: verify, review, then arm.")
        log_safety("No RF transmission occurs from this tool.")

    elif args.command == "verify":
        plan = load_plan(args.plan)
        errors = verify_plan(plan)

        if not errors:
            log_info("All prerequisites PASS")
            sys.exit(0)

        blockers = [e for e in errors if e.blocking]
        warnings = [e for e in errors if not e.blocking]

        if warnings:
            for w in warnings:
                log_warn(str(w))

        if blockers:
            for e in blockers:
                log_error(str(e))
            sys.exit(1)

    elif args.command == "review":
        plan = load_plan(args.plan)
        _print_plan_summary(plan)

        # Also dump the full JSON for inspection
        print("\n--- Full plan JSON ---", file=sys.stderr)
        json.dump(plan, sys.stderr, indent=2)
        print(file=sys.stderr)

    elif args.command == "arm":
        plan = load_plan(args.plan)
        plan, success = arm_plan(plan, confirmed=args.yes)
        if success:
            with open(args.plan, "w") as f:
                json.dump(plan, f, indent=2)
            log_info(f"Armed plan saved to {args.plan}")
        else:
            sys.exit(1)

    elif args.command == "abort":
        plan = load_plan(args.plan)
        plan = abort_plan(plan)
        with open(args.plan, "w") as f:
            json.dump(plan, f, indent=2)
        log_info(f"Aborted plan saved to {args.plan}")


if __name__ == "__main__":
    main()
