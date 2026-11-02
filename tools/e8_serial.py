#!/usr/bin/env python3
"""E-8 serial probe + capture driver.

The E-8 (EMAX ELRS E8) speaks a proprietary protocol over /dev/ttyACM0
(USB CDC-ACM, the "serial mode" — power-on holding RIGHT on the right d-pad,
center LED flashing green). This script gets the wire format so we can build
a native reader + run the RCE gate on the protocol parser.

TWO MODES:

  probe    — open the port, send 8 common probe patterns, print the first
             response. Quick "does it talk?" check. The port must NOT be held
             by another process (close the EMAX tool first).

  capture  — the reliable way. Uses a SERIAL TAP (socat or a raw dd) so the
             EMAX tool (Wine) can ALSO hold the port at the same time. The
             EMAX tool knows the protocol (it reads the UID + params per
             §41c), so when it Connects + Reads, the tap grabs both directions
             (tool's commands + E-8's responses) as raw bytes.

             The capture mode does NOT open the port with pyserial (that would
             conflict with the EMAX tool). Instead it runs `socat` or `dd` as
             a subprocess to TAP the port, while the operator drives the EMAX
             tool in another terminal.

USAGE:
  python3 e8_serial.py probe
  python3 e8_serial.py capture [seconds] [outfile]      (default 30 s, /tmp/e8_capture.bin)

The E-8 must be in serial mode (power-on holding RIGHT on the right d-pad,
center LED flashing green) -> /dev/ttyACM0. Check: dmesg | grep ttyACM
"""
import os
import subprocess
import sys
import time

PORT = "/dev/ttyACM0"
BAUD = 115200


def check_port():
    """Check if the ACM port exists + is accessible."""
    if not os.path.exists(PORT):
        print(f"FAIL: {PORT} does not exist.")
        print("Is the E-8 in serial mode? (power-on holding RIGHT on the right d-pad)")
        print("Check: dmesg | grep ttyACM")
        sys.exit(1)
    print(f"OK: {PORT} exists")


def probe():
    """Send probe patterns, print the first response. Port must be free."""
    try:
        import serial
    except ImportError:
        print("FAIL: pyserial not installed. pip3 install pyserial")
        sys.exit(1)

    check_port()
    try:
        s = serial.Serial(PORT, BAUD, timeout=1)
    except serial.SerialException as e:
        print(f"FAIL: cannot open {PORT}: {e}")
        print("Is the EMAX tool (Wine) still connected? Close it first, then retry.")
        sys.exit(1)
    time.sleep(0.5)

    probes = [
        b"\xaa\x01\x00",
        b"\x55\xaa\x01\x00",
        b"\xff\x01\x00\x00",
        b"\x01\x00\x00\x00\x00",
        b"\x7e\x01\x00\x00",
        b"\x20\x01\x00",
        b"\xaa\x00\x00",
        b"\x55\xaa\x00\x00",
    ]
    print(f"Probing {PORT} @ {BAUD} ...")
    hit = False
    for p in probes:
        s.reset_input_buffer()
        s.write(p)
        time.sleep(0.6)
        try:
            r = s.read(128)
        except serial.SerialException:
            r = b""
        if r:
            print(f"HIT: probe {p.hex()} -> response {r.hex()} ({len(r)} bytes)")
            hit = True
            break
    if not hit:
        print("No response to any probe.")
        print("Do the full capture: python3 e8_serial.py capture 30")
        print("(the capture uses a TAP, so the EMAX tool can hold the port too)")
    s.close()


def capture(window, outfile):
    """TAP the serial port (via socat or dd) while the EMAX tool drives it.

    The key insight: a serial port can only have ONE reader at a time. The
    EMAX tool (Wine) needs to hold the port to Connect + Read. So this script
    does NOT open the port with pyserial. Instead it runs a subprocess that
    TAPS the port (reads raw bytes) while the operator drives the EMAX tool
    in another terminal.

    Two TAP strategies (tried in order):
      1. socat — if installed, `socat - FILE` on the port (cleanest)
      2. dd — fallback, `dd if=PORT of=FILE bs=1 count=<n>` (slower, but works)

    If NEITHER works (port is busy + no TAP tool), the script prints
    instructions for a manual `cat` capture.
    """
    check_port()

    # Strategy 1: socat (if available)
    socat = _which("socat")
    if socat:
        print(f"Using socat to TAP {PORT} for {window} s -> {outfile}")
        print("NOW drive the EMAX tool in another terminal:")
        print("  1. Select the COM port (COM33 -> /dev/ttyACM0)")
        print("  2. Click Connect")
        print("  3. Click Read (the Params Configuration read)")
        print("  4. (Optional) Click Write with the same values (no-op)")
        print("  5. Click Disconnect")
        print("Collecting...")
        sys.stdout.flush()

        # socat: read from the port, write to the file, for `window` seconds
        # -t is the timeout; we use a background process + a timer
        cmd = [socat, "-b", str(BAUD), f"OPEN:{PORT},nonblock", f"OPEN:{outfile},creat,trunc"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as e:
            print(f"socat failed to start: {e}")
            return _manual_capture_fallback(window, outfile)

        # Wait for the window, then kill socat
        try:
            proc.wait(timeout=window)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        size = os.path.getsize(outfile) if os.path.exists(outfile) else 0
        _report_capture(outfile, size)
        return

    # Strategy 2: dd (fallback, no socat)
    print(f"socat not found. Using dd to TAP {PORT} for {window} s -> {outfile}")
    print("NOW drive the EMAX tool in another terminal:")
    print("  1. Select the COM port  2. Connect  3. Read  4. (Write)  5. Disconnect")
    print("Collecting...")
    sys.stdout.flush()

    # dd: read from the port (non-blocking) into the file
    # bs=64 (serial is chunky), count is high (we'll kill it after the window)
    cmd = ["dd", f"if={PORT}", f"of={outfile}", "bs=64", "count=100000", "iflag=nonblock"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as e:
        print(f"dd failed to start: {e}")
        return _manual_capture_fallback(window, outfile)

    try:
        proc.wait(timeout=window)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    size = os.path.getsize(outfile) if os.path.exists(outfile) else 0
    _report_capture(outfile, size)


def _manual_capture_fallback(window, outfile):
    """If no TAP tool works, print manual instructions."""
    print("\n" + "=" * 60)
    print("AUTOMATIC TAP FAILED (port busy or no TAP tool).")
    print("Do a MANUAL capture instead:")
    print("=" * 60)
    print()
    print("Terminal 1 — start the raw capture:")
    print(f"  sudo timeout {window} cat {PORT} > {outfile}")
    print()
    print("Terminal 2 — drive the EMAX tool (within the timeout):")
    print("  wine ~/Downloads/EMAX_E8_unpacked/expresslrs-configurator.exe")
    print("  # Select COM port -> Connect -> Read -> (Write) -> Disconnect")
    print()
    print(f"When the capture ends, check: ls -la {outfile}")
    print("Then either:")
    print(f"  1. Upload {outfile} to the enclave via the files API")
    print("     (path = /opt/data/Drone_Project/references/e8_capture.bin)")
    print("  2. OR paste the hex dump here:  xxd {outfile} | head -40")
    print()


def _report_capture(outfile, size):
    """Print the capture result + a hex dump."""
    if size == 0:
        print("\nNO DATA captured.")
        print("Was the EMAX tool connected + did you click Read during the window?")
        print("The TAP may have failed (port held by the tool before the TAP started).")
        print("Try again: start the TAP FIRST, then Connect + Read in the tool.")
        return

    print(f"\nSaved {size} bytes -> {outfile}")
    print("\nFirst 256 bytes (hex):")
    with open(outfile, "rb") as f:
        data = f.read(256)
    for i in range(0, len(data), 16):
        hexs = data[i:i+16].hex()
        print(f"  {i:04x}: {hexs}")
    print(f"\nFull hex dump (first 4 KB):")
    with open(outfile, "rb") as f:
        data = f.read(4096)
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hexs = chunk.hex()
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {i:04x}: {hexs:<32s} {asc}")
    print(f"\nUpload {outfile} to the enclave via the files API:")
    print(f"  path = /opt/data/Drone_Project/references/e8_capture.bin")
    print(f"  OR paste the hex dump above here.")


def _which(prog):
    """Find a program in PATH (like `which`)."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, prog)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("probe", "capture"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "probe":
        probe()
    else:
        window = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        outfile = sys.argv[3] if len(sys.argv) > 3 else "/tmp/e8_capture.bin"
        capture(window, outfile)
