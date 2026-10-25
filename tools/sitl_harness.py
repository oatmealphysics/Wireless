#!/usr/bin/env python3
"""
ArduPilot SITL test harness for Chain 2 MAVLink integration testing.

Manages the lifecycle of an ArduPilot SITL process for offline validation
of MAVLink discovery, interception, signing policy, and command injection.
All network activity is localhost-only. No hardware, no RF, no external targets.

Usage:
    # As a library (from tests):
    from sitl_harness import SITLHarness
    with SITLHarness() as sitl:
        conn = sitl.connect()
        ...

    # Standalone smoke test:
    python3 tools/sitl_harness.py

Dependencies:
    pymavlink (pip install pymavlink)
    ArduPilot SITL binary at firmware/ardupilot/arducopter_SITL_x86_64_4.7.2

For authorized CTF research and offline testing only.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

try:
    from pymavlink import mavutil
except ImportError:
    print("[!] pymavlink not installed. Run: pip install pymavlink",
          file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
SITL_BINARY = REPO_ROOT / "firmware" / "ardupilot" / "arducopter_SITL_x86_64_4.7.2"

DEFAULT_HOME = "-35.363261,149.165230,584,353"
DEFAULT_SPEEDUP = 10
SITL_STARTUP_TIMEOUT = 30
HEARTBEAT_TIMEOUT = 15
DEFAULT_INSTANCE = 0


def _find_free_port() -> int:
    """Find an available TCP/UDP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SITLHarness:
    """Manages an ArduPilot SITL process for deterministic testing.

    The harness:
    - Starts SITL with --wipe (clean EEPROM) for deterministic state
    - Uses a unique instance ID to avoid port collisions
    - Binds all network to localhost
    - Provides pymavlink connection to the simulated vehicle
    - Kills SITL on teardown (context manager or explicit close)
    """

    def __init__(self, *, instance: int = DEFAULT_INSTANCE,
                 home: str = DEFAULT_HOME, speedup: int = DEFAULT_SPEEDUP,
                 model: str = "quad", extra_args: list[str] | None = None,
                 sitl_binary: Path | None = None):
        self.instance = instance
        self.home = home
        self.speedup = speedup
        self.model = model
        self.extra_args = extra_args or []
        self.sitl_binary = sitl_binary or SITL_BINARY
        self._proc: subprocess.Popen | None = None
        self._workdir: Path | None = None

        self.master_port = 5760 + 10 * instance
        self.sitl_output_port = 5501 + 10 * instance

    def _compute_ports(self):
        """ArduPilot SITL uses base + 10*instance for port numbering."""
        self.master_port = 5760 + 10 * self.instance
        self.sitl_output_port = 5501 + 10 * self.instance

    @property
    def connection_string(self) -> str:
        """pymavlink connection string for the SITL TCP master port."""
        return f"tcp:127.0.0.1:{self.master_port}"

    @property
    def udp_connection_string(self) -> str:
        """UDP output string for listening to SITL broadcast."""
        return f"udpin:127.0.0.1:{self.sitl_output_port}"

    def start(self, *, timeout: float = SITL_STARTUP_TIMEOUT) -> None:
        """Start the SITL process and wait for it to be ready."""
        if self._proc is not None:
            raise RuntimeError("SITL already running")

        if not self.sitl_binary.exists():
            raise FileNotFoundError(
                f"SITL binary not found: {self.sitl_binary}\n"
                f"Expected at: firmware/ardupilot/arducopter_SITL_x86_64_4.7.2"
            )

        self.sitl_binary.chmod(0o755)

        self._workdir = Path(f"/tmp/sitl_test_{self.instance}_{os.getpid()}")
        self._workdir.mkdir(parents=True, exist_ok=True)

        self._compute_ports()

        cmd = [
            str(self.sitl_binary),
            "--model", self.model,
            "--home", self.home,
            "--speedup", str(self.speedup),
            "--instance", str(self.instance),
            "--wipe",
            "--synthetic-clock",
        ] + self.extra_args

        env = os.environ.copy()
        env["HOME"] = str(self._workdir)

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(self._workdir),
            env=env,
            preexec_fn=os.setsid,
        )

        self._wait_for_ready(timeout)

    def _wait_for_ready(self, timeout: float) -> None:
        """Wait for SITL to start accepting MAVLink connections."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                stdout = self._proc.stdout.read().decode(errors="replace")
                raise RuntimeError(
                    f"SITL exited with code {self._proc.returncode}.\n"
                    f"Output:\n{stdout[:2000]}"
                )
            try:
                with socket.create_connection(
                    ("127.0.0.1", self.master_port), timeout=1.0
                ):
                    return
            except (ConnectionRefusedError, OSError, TimeoutError):
                time.sleep(0.5)

        self.stop()
        raise TimeoutError(
            f"SITL did not start within {timeout}s on port {self.master_port}"
        )

    def connect(self, *, timeout: float = HEARTBEAT_TIMEOUT) -> "mavutil.mavlink_connection":
        """Create a pymavlink connection to the running SITL and wait for heartbeat."""
        if self._proc is None or self._proc.poll() is not None:
            raise RuntimeError("SITL is not running")

        conn = mavutil.mavlink_connection(
            self.connection_string,
            source_system=255,
            source_component=190,
            dialect="common",
        )
        hb = conn.wait_heartbeat(timeout=timeout)
        if hb is None:
            raise TimeoutError(
                f"No heartbeat from SITL within {timeout}s"
            )
        return conn

    def stop(self) -> None:
        """Kill the SITL process and clean up."""
        if self._proc is not None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                self._proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                    self._proc.wait(timeout=3)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
            finally:
                self._proc = None

        if self._workdir and self._workdir.exists():
            import shutil
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


def wait_for_mode(conn, mode_name: str, timeout: float = 10.0) -> bool:
    """Wait until the vehicle reports a specific flight mode."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hb = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
        if hb is None:
            continue
        current = mavutil.mode_string_v10(hb)
        if current.upper() == mode_name.upper():
            return True
    return False


def wait_armed(conn, armed: bool, timeout: float = 10.0) -> bool:
    """Wait until the vehicle reports armed/disarmed state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hb = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=1.0)
        if hb is None:
            continue
        is_armed = bool(hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        if is_armed == armed:
            return True
    return False


def get_vehicle_state(conn, timeout: float = 5.0) -> dict:
    """Capture current vehicle state from heartbeat and other messages."""
    state = {}
    hb = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=timeout)
    if hb:
        state["mode"] = mavutil.mode_string_v10(hb)
        state["armed"] = bool(hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        state["system_status"] = hb.system_status
        state["mav_type"] = hb.type
        state["autopilot"] = hb.autopilot
        state["base_mode"] = hb.base_mode
        state["custom_mode"] = hb.custom_mode
    return state


def smoke_test():
    """Run a basic smoke test: start SITL, connect, read heartbeat, stop."""
    print("[*] SITL Harness Smoke Test", file=sys.stderr)
    print(f"    Binary: {SITL_BINARY}", file=sys.stderr)
    print(f"    Exists: {SITL_BINARY.exists()}", file=sys.stderr)

    if not SITL_BINARY.exists():
        print("[!] SITL binary not found — skipping smoke test", file=sys.stderr)
        return False

    try:
        with SITLHarness(speedup=20) as sitl:
            print(f"[+] SITL started on port {sitl.master_port}", file=sys.stderr)

            conn = sitl.connect()
            state = get_vehicle_state(conn)
            print(f"[+] Heartbeat received:", file=sys.stderr)
            print(f"    Mode: {state.get('mode', '?')}", file=sys.stderr)
            print(f"    Armed: {state.get('armed', '?')}", file=sys.stderr)
            print(f"    Type: {state.get('mav_type', '?')}", file=sys.stderr)

            conn.close()
            print("[+] Smoke test PASSED", file=sys.stderr)
            return True

    except Exception as e:
        print(f"[!] Smoke test FAILED: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    success = smoke_test()
    sys.exit(0 if success else 1)
