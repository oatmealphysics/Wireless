#!/usr/bin/env python3
"""
ELRS Serial Interface Probe

Connects to an ELRS TX module over USB serial and:
1. Passively captures any CRSF frames being sent
2. Sends CRSF ping to identify the device
3. Reads device info (firmware version, hardware)
4. Extracts current configuration if accessible

CRSF frame format:
    [sync] [len] [type] [payload...] [crc8]
    sync = 0xC8 (from TX module) or 0xEC (to TX module)
"""

import serial
import struct
import time
import sys
import argparse


# CRSF sync bytes
CRSF_SYNC_TX = 0xC8   # frames FROM the TX module
CRSF_SYNC_RX = 0xEC   # frames TO the TX module (from handset)
CRSF_SYNC_MODULE = 0xEE  # module address

# CRSF frame types
CRSF_TYPES = {
    0x02: "GPS",
    0x07: "VARIO",
    0x08: "BATTERY_SENSOR",
    0x09: "BARO_ALTITUDE",
    0x0A: "HEARTBEAT",
    0x0B: "VIDEO_TRANSMITTER",
    0x0E: "LINK_STATISTICS",
    0x10: "OPENTX_SYNC",
    0x14: "SUBSET_RC_CHANNELS",
    0x16: "RC_CHANNELS_PACKED",
    0x1C: "LINK_RX_ID",
    0x1D: "LINK_TX_ID",
    0x21: "ATTITUDE",
    0x28: "PING_DEVICES",
    0x29: "DEVICE_INFO",
    0x2A: "PARAMETER_SETTINGS_ENTRY",
    0x2B: "PARAMETER_READ",
    0x2C: "PARAMETER_WRITE",
    0x2D: "ELRS_STATUS",
    0x2E: "COMMAND",
    0x32: "KISS_REQ",
    0x33: "KISS_RESP",
    0x36: "DISPLAYPORT_CMD",
    0x3A: "ARDUPILOT_RESP",
    0x78: "MSP_REQ",
    0x79: "MSP_RESP",
    0x7A: "MSP_WRITE",
    0x7C: "DISPLAYPORT_CMD_V2",
    0x80: "CRSF_FRAMETYPE_RADIO_ID",
}

# CRSF addresses
CRSF_ADDR = {
    0x00: "BROADCAST",
    0x10: "USB",
    0x12: "BLUETOOTH",
    0x80: "TBS_CORE_PNP_PRO",
    0x8A: "RESERVED1",
    0xC8: "CURRENT_SENSOR",
    0xCA: "GPS",
    0xCC: "TBS_BLACKBOX",
    0xCE: "FLIGHT_CONTROLLER",
    0xE0: "RESERVED2",
    0xEA: "RACE_TAG",
    0xEC: "RADIO_TRANSMITTER",
    0xEE: "CRSF_TRANSMITTER",
}


def crsf_crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0xD5
            else:
                crc = crc << 1
            crc &= 0xFF
    return crc


def build_crsf_frame(frame_type: int, payload: bytes, sync: int = CRSF_SYNC_RX) -> bytes:
    length = len(payload) + 2  # type + payload + crc
    frame = bytes([sync, length, frame_type]) + payload
    crc = crsf_crc8(frame[2:])  # CRC over type + payload
    return frame + bytes([crc])


def build_ping() -> bytes:
    # CRSF ping: addressed to broadcast
    # Extended frame: [sync][len][type][dest][origin][crc]
    dest = 0x00      # broadcast
    origin = 0xEC    # radio transmitter
    payload = bytes([dest, origin])
    return build_crsf_frame(0x28, payload)


def build_device_info_request() -> bytes:
    dest = 0xEE      # CRSF transmitter module
    origin = 0xEC    # radio transmitter
    payload = bytes([dest, origin])
    return build_crsf_frame(0x28, payload)


def build_param_read(param_index: int, chunk: int = 0) -> bytes:
    dest = 0xEE
    origin = 0xEC
    payload = bytes([dest, origin, param_index, chunk])
    return build_crsf_frame(0x2B, payload)


def parse_frame(data: bytes) -> dict | None:
    if len(data) < 4:
        return None
    sync = data[0]
    length = data[1]
    if len(data) < length + 2:
        return None
    frame_type = data[2]
    payload = data[3:2 + length - 1]
    crc_received = data[2 + length - 1]
    crc_calc = crsf_crc8(data[2:2 + length - 1])

    return {
        "sync": sync,
        "length": length,
        "type": frame_type,
        "type_name": CRSF_TYPES.get(frame_type, f"UNKNOWN(0x{frame_type:02X})"),
        "payload": payload,
        "crc_ok": crc_received == crc_calc,
        "raw": data[:2 + length],
    }


def try_read_frames(ser: serial.Serial, timeout: float = 2.0) -> list[dict]:
    frames = []
    buf = bytearray()
    end_time = time.time() + timeout

    while time.time() < end_time:
        waiting = ser.in_waiting
        if waiting > 0:
            buf.extend(ser.read(waiting))
        else:
            time.sleep(0.01)

        # Try to find and parse frames in buffer
        while len(buf) >= 4:
            # Find a sync byte
            sync_pos = -1
            for i, b in enumerate(buf):
                if b in (CRSF_SYNC_TX, CRSF_SYNC_RX, CRSF_SYNC_MODULE, 0xC8, 0xEC, 0xEE):
                    sync_pos = i
                    break

            if sync_pos == -1:
                buf.clear()
                break
            if sync_pos > 0:
                buf = buf[sync_pos:]

            if len(buf) < 2:
                break
            frame_len = buf[1]
            total_len = frame_len + 2

            if frame_len > 64 or frame_len < 2:
                buf.pop(0)
                continue
            if len(buf) < total_len:
                break

            frame_data = bytes(buf[:total_len])
            frame = parse_frame(frame_data)
            if frame and frame["crc_ok"]:
                frames.append(frame)
            buf = buf[total_len:]

    return frames


def extract_device_info(payload: bytes) -> dict:
    info = {}
    if len(payload) < 3:
        return info
    info["dest"] = CRSF_ADDR.get(payload[0], f"0x{payload[0]:02X}")
    info["origin"] = CRSF_ADDR.get(payload[1], f"0x{payload[1]:02X}")
    # Device name is null-terminated string starting at byte 2
    name_end = payload.find(0x00, 2)
    if name_end > 2:
        info["name"] = payload[2:name_end].decode("ascii", errors="replace")
        rest = payload[name_end + 1:]
        if len(rest) >= 12:
            info["serial_no"] = rest[0:4].hex()
            info["hw_ver"] = struct.unpack(">I", rest[4:8])[0]
            info["sw_ver"] = struct.unpack(">I", rest[8:12])[0]
            if len(rest) >= 13:
                info["param_count"] = rest[12]
            if len(rest) >= 14:
                info["protocol_ver"] = rest[13]
    return info


def main():
    parser = argparse.ArgumentParser(description="ELRS Serial Interface Probe")
    parser.add_argument("-p", "--port", default="/dev/ttyACM0", help="Serial port")
    parser.add_argument("-b", "--baud", type=int, default=460800, help="Baud rate")
    parser.add_argument("-t", "--timeout", type=float, default=3.0, help="Listen timeout per phase")
    parser.add_argument("--listen-only", action="store_true", help="Only listen, don't send")
    parser.add_argument("--dump-params", action="store_true", help="Read all parameter entries")
    args = parser.parse_args()

    print(f"[*] Opening {args.port} at {args.baud} baud")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=0.1)
    except serial.SerialException as e:
        print(f"[!] Failed to open port: {e}")
        # Try common ELRS baud rates
        for baud in [420000, 400000, 921600, 115200]:
            try:
                print(f"[*] Trying {baud} baud...")
                ser = serial.Serial(args.port, baud, timeout=0.1)
                print(f"[+] Connected at {baud}")
                break
            except serial.SerialException:
                continue
        else:
            print("[!] Could not connect at any baud rate")
            sys.exit(1)

    ser.reset_input_buffer()

    # Phase 1: Passive listen
    print(f"\n[*] Phase 1: Passive capture ({args.timeout}s)...")
    frames = try_read_frames(ser, args.timeout)
    if frames:
        print(f"[+] Captured {len(frames)} frames:")
        type_counts = {}
        for f in frames:
            t = f["type_name"]
            type_counts[t] = type_counts.get(t, 0) + 1
            if len(frames) <= 20:
                payload_hex = f["payload"].hex() if f["payload"] else "(empty)"
                print(f"    sync=0x{f['sync']:02X} type={t} len={f['length']} payload={payload_hex}")
        if len(frames) > 20:
            for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
                print(f"    {t}: {c} frames")
    else:
        print("[-] No frames captured during passive listen")

    if args.listen_only:
        ser.close()
        return

    # Phase 2: Send ping
    print(f"\n[*] Phase 2: Sending CRSF ping...")
    ping = build_ping()
    print(f"    TX: {ping.hex()}")
    ser.write(ping)
    ser.flush()

    frames = try_read_frames(ser, args.timeout)
    device_info = None
    for f in frames:
        print(f"    RX: type={f['type_name']} payload={f['payload'].hex()}")
        if f["type"] == 0x29:  # DEVICE_INFO
            device_info = extract_device_info(f["payload"])
            print(f"\n[+] Device Info:")
            for k, v in device_info.items():
                print(f"    {k}: {v}")

    # Phase 3: Targeted device info request
    if not device_info:
        print(f"\n[*] Phase 3: Requesting device info directly...")
        req = build_device_info_request()
        print(f"    TX: {req.hex()}")
        ser.write(req)
        ser.flush()

        frames = try_read_frames(ser, args.timeout)
        for f in frames:
            print(f"    RX: type={f['type_name']} payload={f['payload'].hex()}")
            if f["type"] == 0x29:
                device_info = extract_device_info(f["payload"])
                print(f"\n[+] Device Info:")
                for k, v in device_info.items():
                    print(f"    {k}: {v}")

    # Phase 4: Read parameters
    if args.dump_params:
        param_count = device_info.get("param_count", 20) if device_info else 20
        print(f"\n[*] Phase 4: Reading {param_count} parameters...")
        for idx in range(1, param_count + 1):
            req = build_param_read(idx)
            ser.write(req)
            ser.flush()
            time.sleep(0.05)

            frames = try_read_frames(ser, 0.5)
            for f in frames:
                if f["type"] == 0x2A:  # PARAMETER_SETTINGS_ENTRY
                    payload = f["payload"]
                    if len(payload) > 6:
                        # Extended header: dest, origin, index, chunks_remaining, parent, type, name...
                        p_dest = payload[0]
                        p_origin = payload[1]
                        p_index = payload[2]
                        p_chunks = payload[3]
                        p_parent = payload[4]
                        p_type = payload[5]
                        name_start = 6
                        name_end = payload.find(0x00, name_start)
                        if name_end > name_start:
                            name = payload[name_start:name_end].decode("ascii", errors="replace")
                            value_data = payload[name_end + 1:]
                            print(f"    [{idx:2d}] parent={p_parent} type={p_type} name=\"{name}\" value={value_data.hex()}")
                        else:
                            print(f"    [{idx:2d}] raw={payload.hex()}")
                elif f["type_name"] != "UNKNOWN(0x00)":
                    print(f"    [{idx:2d}] unexpected: type={f['type_name']} payload={f['payload'].hex()}")

    # Phase 5: Check for WiFi mode trigger
    print(f"\n[*] Summary:")
    if device_info:
        print(f"    Device: {device_info.get('name', 'unknown')}")
        print(f"    HW ver: {device_info.get('hw_ver', '?')}")
        print(f"    SW ver: {device_info.get('sw_ver', '?')}")
        print(f"    Params: {device_info.get('param_count', '?')}")
    print(f"    Port: {args.port}")
    print(f"    Baud: {ser.baudrate}")

    ser.close()
    print("\n[*] Done.")


if __name__ == "__main__":
    main()
