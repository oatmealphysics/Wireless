#!/usr/bin/env python3
"""Native EMAX E8 ELRS Read-params client — no Wine, no vendor tool.

Protocol reconstruction confirmed in §49/§50 (notes). Sends the 64-byte Read
command, receives the 17-byte config response, and prints the parsed fields.

The E-8 must be in serial mode (power-on holding RIGHT on the right d-pad,
center LED flashing green). The vendor tool must NOT be running — only one
process can hold /dev/ttyACM0 at a time.

Usage:
  python3 tools/e8_read.py                 # read + print once
  python3 tools/e8_read.py --port /dev/ttyACM0
  python3 tools/e8_read.py --raw           # also print raw hex
"""
from __future__ import annotations
import argparse
import sys
import time

try:
    import serial
except ImportError:
    print("FAIL: pyserial not installed. pip3 install pyserial", file=sys.stderr)
    sys.exit(2)

BAUD = 115200  # §45: 115200 8N1 no flow control (decoded from setBaudRate immediate)
READ_CMD = bytes([0x02, 0x01]) + bytes(62)  # §49: TX header 0x0201 + 62 zero bytes
# §42a / §49/§50: 17-byte response is 02 c5 <cfg0:1> <cfg1:1> <cfg2:1> <uid:6> <padding:6>
# cfg0 is confirmed Rate (byte 0x02 == 150Hz per the GUI screenshot in §41c).
# cfg1 and cfg2 are the Power + tlm fields, but the exact index→label mapping
# isn't yet nailed down (the strings in .rdata list 10/25/50/100 mW and
# 1:128/1:64/1:32/1:16, but the GUI showed Power=100mW tlm=OFF while the frame
# has cfg1=0x01 cfg2=0x03 — index-to-label needs a Write-then-Read capture
# with each field varied). Printing raw hex + a best-guess label.
RATES = {0x00: "500Hz", 0x01: "250Hz", 0x02: "150Hz", 0x03: "50Hz"}  # confirmed by GUI screenshot
POWERS_GUESS = {0x00: "10mW?", 0x01: "25mW?", 0x02: "50mW?", 0x03: "100mW?"}
TLMS_GUESS = {0x00: "1:128?", 0x01: "1:64?", 0x02: "1:32?", 0x03: "1:16?"}


def read_once(port: str, show_raw: bool = False) -> int:
    try:
        s = serial.Serial(port, BAUD, timeout=2, bytesize=8, parity='N', stopbits=1,
                          xonxoff=False, rtscts=False, dsrdtr=False)
    except serial.SerialException as e:
        print(f"FAIL: cannot open {port}: {e}", file=sys.stderr)
        print("Is the E-8 in serial mode (R-pad RIGHT at power-on, green LED)?", file=sys.stderr)
        print("Is the vendor tool holding the port? Close it first.", file=sys.stderr)
        return 1

    time.sleep(0.2)
    s.reset_input_buffer()
    s.write(READ_CMD); s.flush()
    resp = s.read(17)
    s.close()

    if len(resp) != 17 or resp[0] != 0x02 or resp[1] != 0xc5:
        print(f"FAIL: unexpected response ({len(resp)} bytes): {resp.hex()}", file=sys.stderr)
        return 1

    rate, cfg1, cfg2 = resp[2], resp[3], resp[4]
    uid = resp[5:11]
    pad = resp[11:17]
    print(f"EMAX E8 ELRS — Read")
    print(f"  Rate         : {RATES.get(rate, f'unknown=0x{rate:02x}')}  (byte 0x{rate:02x})")
    print(f"  cfg1 (Power?): {POWERS_GUESS.get(cfg1, f'0x{cfg1:02x}')}  (byte 0x{cfg1:02x}; mapping TBD — needs Write-cycle capture)")
    print(f"  cfg2 (tlm?)  : {TLMS_GUESS.get(cfg2, f'0x{cfg2:02x} (OFF?)')}  (byte 0x{cfg2:02x}; mapping TBD)")
    print(f"  bind UID     : {' '.join(f'{b:02x}' for b in uid)}")
    print(f"  padding      : {' '.join(f'{b:02x}' for b in pad)}")
    if show_raw:
        print(f"  raw      : {resp.hex()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', default='/dev/ttyACM0')
    ap.add_argument('--raw', action='store_true')
    args = ap.parse_args()
    return read_once(args.port, args.raw)


if __name__ == '__main__':
    sys.exit(main())
