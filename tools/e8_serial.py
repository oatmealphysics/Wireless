#!/usr/bin/env python3
"""E-8 serial probe + capture driver.

Two modes:
  probe   — send a few common probe patterns, print the first response
  capture — open the port, print "READY", wait for you to drive the EMAX tool
            (Connect + Read), collect everything for a fixed window, save it

Usage:
  python3 e8_serial.py probe
  python3 e8_serial.py capture [seconds]      (default 30 s)
  python3 e8_serial.py capture 30 /tmp/out.bin

The E-8 must be in serial mode (power-on holding RIGHT on the right d-pad,
center LED flashing green) -> /dev/ttyACM0.
"""
import sys
import time
import serial

PORT = "/dev/ttyACM0"
BAUD = 115200


def open_port():
    try:
        s = serial.Serial(PORT, BAUD, timeout=1)
    except serial.SerialException as e:
        print(f"FAIL: cannot open {PORT}: {e}")
        print("Is the E-8 in serial mode? (power-on holding RIGHT on the right d-pad)")
        print("Check: dmesg | grep ttyACM")
        sys.exit(1)
    time.sleep(0.5)
    return s


def probe():
    s = open_port()
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
        r = s.read(128)
        if r:
            print(f"HIT: probe {p.hex()} -> response {r.hex()} ({len(r)} bytes)")
            hit = True
            break
    if not hit:
        print("No response to any probe.")
        print("Do the full capture: python3 e8_serial.py capture 30")
        print("Then drive the EMAX tool (Connect + Read) during the window.")
    s.close()


def capture(window, outfile):
    s = open_port()
    print(f"Listening on {PORT} for {window} s -> {outfile}")
    print("NOW drive the EMAX tool: select COM port, Connect, Read, (Write), Disconnect")
    print("Collecting...")
    sys.stdout.flush()

    chunks = []
    end = time.time() + window
    last_activity = time.time()
    while time.time() < end:
        n = s.in_waiting
        if n:
            data = s.read(n)
            chunks.append((time.time(), data))
            last_activity = time.time()
            hexs = data.hex()
            print(f"  [+{time.time()-end+window:5.1f}s] {len(data):3d} B: {hexs[:80]}{'...' if len(hexs)>80 else ''}")
            sys.stdout.flush()
        time.sleep(0.02)

    s.close()

    total = sum(len(c[1]) for c in chunks)
    if total == 0:
        print("NO DATA captured. Was the EMAX tool connected + did you click Read?")
        print("The E-8 may use a different baud rate — try: python3 e8_serial.py capture 30 (and check dmesg for the ACM port)")
        return

    with open(outfile, "wb") as f:
        f.write(b"".join(c[1] for c in chunks))

    print(f"\nSaved {total} bytes ({len(chunks)} chunks) -> {outfile}")
    print(f"\nFull hex dump:")
    for t, c in chunks:
        print(f"  {c.hex()}")
    print(f"\nUpload {outfile} to the enclave via the files API:")
    print(f"  path = /opt/data/Drone_Project/references/e8_capture.bin")


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
