# MAVLink v2 Message Signing — Weakness Analysis & Bypass Reference

> **Context:** Authorized CTF preparation. All testing targets hardware we own.
> **Last updated:** 2026-10-15

---

## Table of Contents

- [Signing Mechanism Overview](#signing-mechanism-overview)
- [1. Replay Attack Window](#1-replay-attack-window)
- [2. Key Extraction from Firmware](#2-key-extraction-from-firmware)
- [3. Signing Downgrade / accept_unsigned Bypass](#3-signing-downgrade--accept_unsigned-bypass)
- [4. Timestamp Manipulation](#4-timestamp-manipulation)
- [5. Additional Weaknesses](#5-additional-weaknesses)
- [6. PoC Tool Reference](#6-poc-tool-reference)
- [References](#references)

---

## Signing Mechanism Overview

MAVLink v2 introduced message signing in 2017 as an optional authentication layer. Understanding the mechanism is essential to attacking it.

### How Signing Works

Each signed MAVLink v2 packet appends a 13-byte **signature block** after the payload + CRC:

```
[ link_id (1 byte) | timestamp (6 bytes, little-endian) | signature (6 bytes) ]
```

The signature is computed as:

```
signature = SHA-256(
    secret_key (32 bytes) ||
    header (excluding incompatibility flags byte) ||
    payload ||
    CRC (2 bytes) ||
    link_id (1 byte) ||
    timestamp (6 bytes)
)[:6]
```

Only the **first 6 bytes** of the SHA-256 are used — a 48-bit truncated HMAC.

### Key Parameters

| Parameter | Value | Notes |
|---|---|---|
| Secret key length | 32 bytes (256-bit) | Shared symmetric key, same on TX and RX |
| Signature length | 6 bytes (48-bit) | Truncated SHA-256 — brute-forceable in theory (~281 trillion combinations) |
| Timestamp resolution | 10 microseconds | 6-byte counter, epoch 2015-01-01 00:00:00 UTC |
| Timestamp rollover | ~89 years from epoch | Not a practical concern |
| Link ID | 1 byte | Identifies the communication channel (serial0, serial1, UDP, etc.) |

### Incompatibility Flag

Signing is indicated by bit 0 of the `incompat_flags` byte in the MAVLink v2 header:

```
Header byte 2 (incompat_flags):
  Bit 0 = MAVLINK_IFLAG_SIGNED (0x01)
```

If this bit is set, the receiver expects a 13-byte signature block after the CRC. If unset, no signature is present.

---

## 1. Replay Attack Window

### The Vulnerability

MAVLink v2 signing uses a monotonically increasing timestamp for anti-replay. The receiver tracks the **highest timestamp seen per (link_id, system_id, component_id)** tuple and rejects messages with timestamps at or below that value.

However, the implementation has a critical tolerance window to handle out-of-order delivery:

### ArduPilot Implementation

From ArduPilot's `GCS_MAVLink/MAVLink_routing.cpp` and `libraries/GCS_MAVLink/GCS_signing.cpp`:

```
The accept window allows timestamps up to 1 minute (6,000,000 timestamp ticks)
behind the latest accepted timestamp.
```

**Key behavior:**
- ArduPilot has a ~60-second **clock tolerance** for out-of-order delivery across physical links
- This tolerance does NOT eliminate per-stream anti-replay state: the receiver still tracks the highest accepted timestamp per (link_id, system_id, component_id) and rejects duplicates
- Valid authentication (correct signing key) is always required separately
- The tolerance window alone does not establish that a replayed packet will be accepted

### PX4 Implementation

From PX4's `src/modules/mavlink/mavlink_main.cpp`:

- PX4 also implements a timestamp tolerance window
- The default window is similar (~60 seconds)
- PX4's signing implementation is less mature than ArduPilot's; some builds have signing disabled entirely

### Hypothetical Attack Scenarios (not yet validated against a receiver)

> **Status:** These scenarios are based on spec analysis. None have been
> validated against a real ArduPilot or PX4 receiver. Acceptance depends on
> valid authentication, per-stream anti-replay state, and implementation-
> specific policy. Do not treat these as confirmed bypasses.

**Scenario — Same link_id replay (requires signing key or accept_unsigned):**

1. Capture signed MAVLink packets over the wire (WiFi UDP or serial radio)
2. Replay within the clock tolerance window (~60s)
3. Acceptance requires: valid signature OR receiver configured with accept_unsigned,
   AND the receiver's per-stream timestamp tracker has not advanced past the packet's timestamp

**Scenario — Cross-link replay (requires signing key or accept_unsigned):**

1. Capture signed packets on link_id 0 (e.g., serial telemetry radio)
2. Replay on a different link_id the receiver hasn't seen
3. A new link_id may lack a timestamp baseline, but valid authentication is still required
4. Without the signing key, the replayed packet must be sent unsigned and the
   receiver must have accept_unsigned enabled for that channel

**Scenario — Cold-start replay:**

1. Capture signed packets at any time
2. Wait for target reboot (power cycle, crash, battery swap)
3. After reboot, timestamp tracker resets — but valid authentication is still required
4. Unchanged authentic packets may be accepted if the receiver has no newer baseline

### Key Takeaways

| Scenario | Constraint | Authentication Required |
|---|---|---|
| Same link_id, system running | ~60s clock tolerance | Valid signature or accept_unsigned |
| Different link_id | No timestamp baseline | Valid signature or accept_unsigned |
| After target reboot | Unlimited | Wait for or cause a reboot |

---

## 2. Key custody and storage verification

Key storage is implementation- and release-specific. Do not infer a current
path or persistence mechanism from another flight stack or an old release.
`SETUP_SIGNING` provisions a 32-byte secret and initial timestamp, but the
receiving stack decides how that material is persisted and recovered.

#### ArduPilot

The earlier `/APM/signing.key`, parameter-file, and generic flash-location
claims in this document were not tied to an exact ArduPilot release and source
location. Treat ArduPilot persistence as **unverified** until a promoted audit
pins a release and traces its `SETUP_SIGNING` handler, storage backend, file
permissions, log behavior, and lost-key recovery path. No key value, raw
storage image, or private path should be retained in tracked evidence.

#### PX4

Current PX4 `main` documentation says the signing key is stored on the SD card
at `/mavlink/mavlink-signing-key.bin`, applies signing rules to USB as well as
other links, and uses physical key-file removal as part of lost-key recovery.
That is a current-main statement, not proof about an older installed release.
Any platform finding must pin the exact PX4 version and confirm its source and
configuration.

Defensive verification should establish:

- where the pinned release persists the key and which principals can read,
  replace, or delete it;
- whether every enabled MAVLink link enforces the intended signed/unsigned
  policy, including USB and companion-computer paths;
- whether setup material or signatures can enter logs or support bundles;
- how initial provisioning, rotation, revocation, and lost-key recovery work;
- whether the deployment protects removable storage and debug interfaces.

Primary source for the current PX4 statement:
[PX4 MAVLink security hardening](https://github.com/PX4/PX4-Autopilot/blob/main/docs/en/mavlink/security_hardening.md).

### Weak Key Generation

Neither ArduPilot nor PX4 enforce strong key generation:

- Many setups use **short passphrases** hashed to produce the 32-byte key
- MAVProxy's signing setup uses `hashlib.sha256(passphrase.encode()).digest()`
- Common passphrases observed in the wild: `password`, `drone`, team names, competition names
- **Dictionary attack:** Hash common phrases and compare against captured signature blocks

---

## 3. Signing Downgrade / accept_unsigned Bypass

### The accept_unsigned Flag

MAVLink signing has a critical architectural weakness: the `accept_unsigned` mechanism.

When signing is enabled on a MAVLink endpoint, it can be configured to **still accept unsigned messages**. This exists for backward compatibility and for specific message types that are always unsigned.

#### ArduPilot's accept_unsigned Behavior

ArduPilot by default accepts unsigned messages from **known system IDs** if:
1. The `MAVLINK_SIGNING_FLAG_ACCEPT_UNSIGNED` flag is set (which it often is)
2. The message is from a system/component pair that has previously sent signed messages
3. OR: The message type is in the "always accept unsigned" list

**Messages always accepted unsigned (ArduPilot):**
- `RADIO_STATUS` (message ID 109) — telemetry radio status
- `HEARTBEAT` (message ID 0) — used for system discovery

This means even with signing enabled, you can:
1. Send unsigned `HEARTBEAT` to establish your system ID
2. Some implementations then accept unsigned commands from that system ID

#### PX4's accept_unsigned Behavior

PX4 is more permissive:
- Many PX4 configurations accept unsigned messages by default even when signing is configured
- The `MAV_SYS_STATUS_SENSOR_3D_GYRO` and other status messages are accepted unsigned
- Some PX4 versions have bugs where unsigned messages are always accepted if the system hasn't received a signed message on that link yet

### Downgrade Attack Procedure

**Attack 1 — Pure unsigned injection:**

1. Sniff traffic to determine if signing is enabled (check `incompat_flags` bit 0)
2. Send unsigned MAVLink v2 messages (with `incompat_flags` = 0x00)
3. If `accept_unsigned` is enabled on the target, your commands execute
4. Many targets accept unsigned messages on new link_ids or from new system_ids

**Attack 2 — MAVLink v1 downgrade:**

1. MAVLink v1 has no signing at all — no `incompat_flags`, no signature block
2. Many endpoints that support MAVLink v2 signing also accept MAVLink v1 for compatibility
3. Send MAVLink v1 frames (magic byte `0xFE` instead of `0xFD`)
4. If the target accepts v1, all signing is bypassed entirely

**Detection in captured traffic:**

```python
# Check if signing is enabled in a captured MAVLink v2 packet
def is_signed(packet_bytes):
    if packet_bytes[0] != 0xFD:  # Not MAVLink v2
        return False
    incompat_flags = packet_bytes[2]
    return bool(incompat_flags & 0x01)
```

### Key Indicators for CTF

| Indicator | How to Check | Implication |
|---|---|---|
| Mixed v1/v2 on same link | Wireshark MAVLink dissector | v1 = no signing possible |
| `incompat_flags` = 0x00 | Inspect MAVLink v2 header | Signing not enabled |
| Accepts unsigned HEARTBEAT | Send unsigned HEARTBEAT, watch for response | May accept other unsigned msgs |
| Response to v1 frames | Send v1 HEARTBEAT (0xFE) | Full signing bypass |

---

## 4. Timestamp Manipulation

### Timestamp Structure

The MAVLink signing timestamp is a 48-bit (6-byte) counter:

- **Unit:** 10 microseconds per tick
- **Epoch:** 2015-01-01 00:00:00 UTC (MAVLink-specific epoch)
- **Monotonic requirement:** Each new message on a given (link_id, system_id, component_id) must have a timestamp > the last accepted timestamp for that tuple

### Attack Vectors

**Vector 1 — Timestamp exhaustion (theoretical):**

- Rapidly send messages with very high timestamps
- Once a large timestamp is accepted, the receiver's tracker jumps forward
- Legitimate messages with earlier (correct) timestamps are then rejected as replays
- **Effect:** DoS — the legitimate transmitter is locked out until its own clock catches up
- **Practical note:** The timestamp tracks forward across reboots on ArduPilot (persisted to storage), so this can be persistent

**Vector 2 — Timestamp rollback via reboot:**

- On platforms that don't persist the timestamp tracker (or lose it on crash), force a reboot:
  - Power cycle (physical access or relay-controlled power)
  - Watchdog trigger via firmware bug
  - MAVLink `PREFLIGHT_REBOOT_SHUTDOWN` command (if unsigned commands accepted)
  - GPS time loss causing NTP/system clock reset
- After reboot, timestamp tracking resets
- Replay previously captured signed packets

**Vector 3 — GPS time spoofing → timestamp confusion:**

- MAVLink timestamps on some implementations derive from GPS time
- Spoof GPS time backward using SDR (Ettus B210 + `gps-sdr-sim`)
- If the target's MAVLink timestamp source is GPS-derived, the timestamp will jump backward
- New legitimate messages may have timestamps lower than previously accepted ones
- Can cause the target to reject its own GCS's messages (DoS) or accept replayed packets

**Vector 4 — Predict future timestamps:**

- If you know the target's clock source and current time
- Compute future timestamps: `timestamp = (current_time_utc - mavlink_epoch) / 10µs`
- Craft signed messages with predicted timestamps (requires the key)
- Pre-compute attack packets for use at a specific future time

### Timestamp Calculation

```python
import time

MAVLINK_EPOCH = 1420070400  # 2015-01-01 00:00:00 UTC

def utc_to_mavlink_timestamp(utc_seconds):
    """Convert UTC epoch seconds to MAVLink signing timestamp ticks."""
    return int((utc_seconds - MAVLINK_EPOCH) * 100000)  # 10µs ticks

def mavlink_timestamp_to_utc(ticks):
    """Convert MAVLink timestamp ticks to UTC epoch seconds."""
    return MAVLINK_EPOCH + (ticks / 100000.0)

# Example: current timestamp
now = time.time()
ts = utc_to_mavlink_timestamp(now)
print(f"Current MAVLink timestamp: {ts} ({ts.bit_length()} bits)")
```

---

## 5. Additional Weaknesses

### 5.1 Truncated Signature (48-bit)

The 6-byte (48-bit) signature is a truncated SHA-256. While brute-forcing a single signature requires ~2^48 attempts, there are optimization opportunities:

- **Birthday attack:** Collect enough signed packets, look for collisions
- **Known-plaintext:** MAVLink message formats are structured and predictable — many fields are known or constrained
- **Multi-target:** With many captured packets, the probability of finding a valid forgery increases

In practice, 48 bits is sufficient to prevent real-time brute force with current hardware, but it's significantly weaker than a full HMAC. For CTF purposes, key extraction or downgrade attacks are more practical.

### 5.2 No Forward Secrecy

- The same 32-byte key is used for the lifetime of the signing relationship
- Compromising the key at any point allows decryption/forging of all past and future messages
- No key rotation mechanism is built into the protocol

### 5.3 No Encryption

Signing provides **authentication only**, not **confidentiality**:

- All MAVLink payload data is transmitted in cleartext
- Telemetry (GPS position, altitude, speed, battery) is fully visible
- Command parameters are visible (destination waypoints, geofence boundaries)
- An attacker can read all telemetry even if they can't forge commands

### 5.4 System ID Spoofing

- The system_id and component_id in MAVLink headers are self-declared
- No verification of system_id is performed beyond signing
- An attacker who has the signing key can impersonate any system_id

### 5.5 No Mutual Authentication

- Only the sender authenticates to the receiver
- The receiver does not authenticate to the sender
- Man-in-the-middle is possible: an attacker can relay signed messages and inject their own (if they have the key or if unsigned is accepted)

### 5.6 Single Key for All Commands

- The same signing key authenticates all message types equally
- No privilege separation — a key that can send HEARTBEAT can also send COMPONENT_ARM_DISARM
- A compromised telemetry key grants full command authority

---

## 6. PoC Tool Reference

### mavlink-signing-replay.py

Location: `tools/mavlink-signing-replay.py`

A proof-of-concept tool that:
1. Captures signed MAVLink v2 packets on a UDP or serial interface
2. Analyzes the signing parameters (timestamp, link_id, system_id)
3. Replays captured packets within the anti-replay tolerance window
4. Supports cross-link replay (changing link_id to bypass timestamp tracking)

See the tool's `--help` output for usage details.

### Attack Workflow

```
1. Reconnaissance
   └─ mavlink-recon.py (from attack plan) or Wireshark
      └─ Determine: signed? version? system IDs? link IDs?

2. Attempt Downgrade
   ├─ Send unsigned MAVLink v2 → does it execute?
   ├─ Send MAVLink v1 frame → does it execute?
   └─ If either works → full bypass, no signing needed

3. If Signing Required
   ├─ Key Extraction
   │   ├─ Physical: SD card, SWD/JTAG dump
   │   ├─ Remote: MAVLink FTP (if available unsigned)
   │   └─ Dictionary: Hash common passphrases, compare
   │
   ├─ Replay Attack
   │   ├─ Capture signed packets (mavlink-signing-replay.py)
   │   ├─ Same-link replay (within 60s)
   │   ├─ Cross-link replay (change link_id)
   │   └─ Post-reboot replay (wait for/force reboot)
   │
   └─ Timestamp Manipulation
       ├─ Exhaust timestamp (DoS legitimate TX)
       └─ GPS spoof → timestamp confusion
```

---

## References

### MAVLink Protocol

- [MAVLink v2 Signing Specification](https://mavlink.io/en/guide/message_signing.html)
- [MAVLink Serialization](https://mavlink.io/en/guide/serialization.html)
- [pymavlink source — signing implementation](https://github.com/ArduPilot/pymavlink)

### ArduPilot

- [ArduPilot MAVLink Signing Documentation](https://ardupilot.org/dev/docs/mavlink-signing.html)
- [ArduPilot GCS_signing.cpp source](https://github.com/ArduPilot/ardupilot)

### PX4

- [PX4 MAVLink module source](https://github.com/PX4/PX4-Autopilot/tree/main/src/modules/mavlink)

### Academic

- [MAVLink Protocol Vulnerability Analysis (ResearchGate)](https://www.researchgate.net/publication/326860779)
- [MAVLink Hijacking via Legitimate Commands (2026)](https://arxiv.org/html/2606.22289v1)
- [MUVIDS — MAVLink IDS (NDSS AutoSec 2021)](https://www.ndss-symposium.org/wp-content/uploads/autosec2021_23036_paper.pdf)
