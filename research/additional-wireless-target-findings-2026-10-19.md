# Additional wireless target findings

- **Date:** 2026-10-19
- **Scope:** Authorized CTF research against owned lab equipment and public
  firmware/source
- **Purpose:** Extend the wireless-subsystem survey to other aircraft,
  controllers, telemetry radios, video systems, and Remote ID receivers
- **Relationship to prior work:** This is a follow-up to
  [`common-drone-wireless-subsystem-rce-survey-2026-10-19.md`](common-drone-wireless-subsystem-rce-survey-2026-10-19.md).

This pass was source and public-artifact research only. It did not access a
serial port, transmit RF, connect to a third-party device, write configuration,
reset a device, enter a bootloader, extract firmware from hardware, or flash
anything. The EMAX E8 and TinyHawk absolute no-flashing rule remains in force.

## Executive result

Several targets are more promising than the original search suggested. The
most important *new* source-level candidates are:

1. a fragmented BLE receive-path object overwrite in the current Crazyflie 2.x
   nRF51 firmware;
2. an attacker-sized CRTP Memory response that overwrites beyond a global
   packet object in current Crazyflie flight firmware;
3. an RF-fed MAVLinkX decompression overflow in current mLRS firmware;
4. an RF-telemetry-to-Bluetooth object overwrite in current EdgeTX releases;
5. a zero-length UDP underflow and reliable assertion path in current
   Espressif ESP-Drone source.

None is yet a demonstrated RCE. The Crazyflie BLE and EdgeTX paths can corrupt
control-bearing object state, while the mLRS and Crazyflie Memory paths allow
substantially larger writes. Exact target builds, compiler layouts, mitigations,
and reproducibility still decide whether any can become a control-flow
primitive.

The survey also found already-confirmed alternatives where ROP/JOP is
unnecessary: SJRC F11 has a published unauthenticated root command-injection
chain, and several Crazyflie, Holy Stone, SiK, DJI, and toy-drone families expose
unauthenticated or weakly authenticated control planes.

## Ranked findings

The P0/P1/P2 labels below are internal research-order labels, not CVSS-style
severity ratings.

| Priority | Target | Wireless ingress | Result | Evidence status |
|---|---|---|---|---|
| P0 | Crazyflie 2.x nRF51 | BLE CRTPUP characteristic | Fragment reassembly writes beyond a 32-byte object buffer and corrupts parser state; on the 32-bit layout it reaches the first byte of the callback field | Current release source; offline layout checked; RCE not proven |
| P0 | mLRS TX/RX | Accepted/decrypted mLRS RF payload -> MAVLinkX stream | Compressed input can expand past a 300-byte frame buffer before MAVLink CRC/signature validation | Current and tagged source; corruption not yet reproduced on an embedded build |
| P0 | Crazyflie 2.x STM32 | nRF24/BLE CRTP -> Memory port | Attacker-selected read length can write 231 bytes beyond the global CRTP packet object through the always-present memory tester | Current stable source; impact beyond corruption not proven |
| P0 | EdgeTX controller with Bluetooth telemetry | Bound FrSky-compatible RF telemetry -> S.Port parser -> Bluetooth forwarding | Byte stuffing can exceed a 33-byte member buffer; the first adjacent byte is the buffer index itself | Current v2.11/v2.12 and main source; OTA and control-flow impact unproven |
| P0 known | SJRC F11 / SJ-GPS-PRO | Open drone Wi-Fi -> TCP service | Published, physically reproduced unauthenticated command injection executes as root | Confirmed CVE; no patch documented by the disclosure project |
| P1 | ESP-Drone | Drone Wi-Fi -> UDP/2390 | Empty datagram underflows an index and checksum length; a minimal checksum-valid datagram reaches a fatal size assertion | Current source; expected reset/DoS, not RCE |
| P1 release lag | OpenDroneID `core-c` v2.0 users | Wi-Fi NAN Remote ID or MAVLink | Direct-RF OOB read and MAVLink stack overwrite are fixed on master but absent from the latest tag | Public issues and fixes; downstream exposure depends on pinned version |
| P1 | Holy Stone H120D | Open Wi-Fi -> UDP/TCP services | Unauthenticated flight/video protocols and a Telnet shell expose multiple Fullhan/RT-Thread parsers | Live-tested public corpus; no memory corruption yet |
| P1 | HDZero goggle Wi-Fi mode | Wi-Fi -> unauthenticated RTSP/TCP 8554 | Bundled LIVE555 path appears source-equivalent to the version affected by CVE-2023-37117 | Strong version inference; not device-reproduced |
| P1 conditional | EdgeTX Ghost telemetry | Ghost module UART -> `MENU_DESC` parser | Unchecked 8-bit line index selects beyond a seven-entry menu array before a full structure write | Current source; module-UART corruption confirmed, RF bridge unproven |
| P1/P2 | EdgeTX CRSF telemetry | Accepted CRSF telemetry -> `DEVICE_INFO` parser | Short valid frames underflow a derived name size and drive out-of-bounds reads | Current source; likely crash/stale-data exposure, not a write primitive |
| P1 conditional | Crazyflie P2P DTR example build | nRF24 P2P broadcast -> optional DTR callback | Packet-declared copy length can overflow a 59-byte stack object | Current source, but only when the optional example application is built |
| P2 | SiK telemetry radios | Valid RF control frame -> remote AT dispatcher | Remote reset and configuration commands are dispatched before the later AES payload check | Current source; control-plane weakness, not memory safety |
| P2 | Holy Stone HS720 / HS175D | Drone Wi-Fi -> Telnet/RTSP | Root/service exposure and poorly hardened extractable firmware | Published physical studies; patch status unknown |
| P2 | OpenHD-pinned wifibroadcast | Raw 802.11 capture -> radiotap/802.11 parser | Uses original packet length instead of captured length and lacks early minimum-length checks | Hardening candidate; no RF crash shown |
| P2 | Parrot ANAFI Thermal/USA | Joined WPA2 network -> MAVLink | Invalid mission type can crash the control process on documented versions | Published CVE; current patch status unresolved |
| P2 | Tello and inexpensive Wi-Fi toy drones | Drone AP -> UDP/TCP control/video | Broad unauthenticated command surfaces with public packet corpora | Good fuzz targets; no verified new corruption |

## 1. Crazyflie nRF51 BLE CRTPUP object overwrite

### Version and reachability

The issue is present in the Crazyflie 2.x nRF51 repository at current master
`36f284538af8254bc227ea93cd7aaa71c4dfa630`, the stable `2026.04` tag
`51f900b0140c20196a0ccf647b279ee342d28e15`, and the `2026.08`-tagged source
`884071f9c87978ef75a7c7ec34d46360405662cc` reviewed locally.

The default Crazyflie 2.x platform build enables BLE. The firmware registers
the CRTPUP characteristic with open write permissions and a 20-byte maximum.
The official protocol documentation explicitly describes a first fragment and
a continuation fragment for CRTP packets larger than 19 bytes:

- [official BLE protocol](https://www.bitcraze.io/documentation/repository/crazyflie2-nrf-firmware/master/protocols/ble/)
- [`on_write()` at reviewed master](https://github.com/bitcraze/crazyflie2-nrf-firmware/blob/36f284538af8254bc227ea93cd7aaa71c4dfa630/src/ble/ble_crazyflies.c#L11-L24)
- [service object layout](https://github.com/bitcraze/crazyflie2-nrf-firmware/blob/36f284538af8254bc227ea93cd7aaa71c4dfa630/interface/ble_crazyflies.h#L37-L52)

### Root cause

The first fragment stores `len - 1` bytes and records that count as
`packet_index`. A continuation copies another `len - 1` bytes at that index,
but the code never checks that the two lengths fit the 32-byte
`packet_buffer`. It also does not validate the advertised CRTP length, packet
identifier, fragment count, or cumulative index before invoking the callback.

An ignored, freestanding 32-bit layout fixture used Nordic's documented
four-`uint16_t` characteristic-handle structure and the exact public service
object fields. GCC reported these offsets:

| Field | Offset |
|---|---:|
| `packet_buffer` | 31 |
| `packet_length` | 63 |
| `packet_index` | 64 |
| `data_handler` | 68 |
| object size | 76 |

A maximum first fragment followed by a maximum continuation writes six bytes
past the array. On this 32-bit layout those bytes corrupt `packet_length`,
`packet_index`, alignment padding, and the first byte of `data_handler`. Since
the corrupted index is retained and never advanced or range-checked, a later
continuation can select an attacker-controlled eight-bit offset from the
object's buffer base for a subsequent write. This is not an arbitrary absolute
write. The callback is invoked immediately after each continuation.

This is a **source-confirmed BLE object overwrite and credible control-flow
candidate**, not demonstrated RCE. The released ARM ELF/map, exact compiler
layout, callback address, SoftDevice event behavior, and repeatable outcome
must be established in a safe host/unit reproduction before any stronger
claim. This note intentionally omits an operational GATT write sequence and
payload.

### Safe next step

Build the exact `2026.04` and `2026.08` targets with retained ELF/map files,
add a host regression around `on_write()`, and verify that a fix rejects any
fragment whose cumulative length exceeds 32 before the first copy. Because the
candidate affects current third-party hardware, coordinate with Bitcraze
before publishing a live proof.

## 2. Crazyflie STM32 CRTP Memory global overwrite

The issue is present in Crazyflie flight firmware current master
`9912584c85db92dbbba14334ffed20ebabba463e` and stable `2026.08`
`54f31e243a0b28b67efef5ba20dbb6d9890a5478`.

CRTP packets contain a one-byte size, one-byte header, and 30-byte data array.
The Memory port read handler takes `readLen` from packet data and passes a
pointer six bytes into that data array to `memRead()` without checking either
the request's actual size or the output capacity:

- [CRTP packet definition](https://github.com/bitcraze/crazyflie-firmware/blob/54f31e243a0b28b67efef5ba20dbb6d9890a5478/src/modules/interface/crtp.h#L35-L78)
- [Memory read handler](https://github.com/bitcraze/crazyflie-firmware/blob/54f31e243a0b28b67efef5ba20dbb6d9890a5478/src/modules/src/crtp_mem.c#L188-L208)
- [always-registered memory tester](https://github.com/bitcraze/crazyflie-firmware/blob/54f31e243a0b28b67efef5ba20dbb6d9890a5478/src/modules/src/mem.c#L57-L65)

Only 24 bytes remain in the packet data array at the destination. The virtual
memory tester is always registered first and fills exactly the requested
number of bytes with an address-derived incrementing byte sequence. The
sequence is not arbitrary attacker data, although its starting low byte is
selectable. The maximum one-byte request length therefore writes 231 bytes past
the packet object and wraps the one-byte response size specifically to `5`,
which passes the later `size <= 30` assertion.

The Memory port is part of the ordinary CRTP subsystem, which can be carried by
the Crazyflie nRF24-compatible radio or BLE. The radio channel/address still
has to be reached, but CRTP/SafeLink is not a cryptographic authenticator. This
is a **source-confirmed global out-of-bounds write with predictable data**.
Actual adjacent objects vary with the link map, and no saved return address,
function pointer, or program counter has been shown to be controlled.

The immediate safe work is an ASan host regression plus stable-build map
analysis. A correct fix must cap the response to the remaining CRTP payload
capacity and reject undersized requests before reading their fields.

## 3. mLRS MAVLinkX pre-validation decompression overflow

Current mLRS master `a61aa83f610283b3f329170060adb899eef509e3` and the
reviewed tagged pre-releases from `v1.3.04` through `v1.4.04` contain the same
decoder pattern. MAVLinkX is the default serial link mode in the source
configuration, the default bind phrase is public, and privacy is off by
default. Configured privacy levels 2/3 raise the bar by requiring a valid
message authenticator, but a paired or malicious peer remains relevant.

The shared stream parser copies the compressed payload into a work buffer and
then expands tokens directly back into the caller's output buffer. Run-length
tokens add an input-selected count with `memset`; literal tokens increment the
output length one byte at a time. The apparent range checks compile to nothing
unless both `DEBUG_ENABLED` and `MAVLINKX_CHECKRANGE` are defined, and one
literal-token path lacks even that debug-only macro.

The decompression is called as soon as all compressed payload bytes arrive.
Only afterward does the parser enlarge its received count/frame length and
continue toward the MAVLink CRC/signature bytes. The RX and TX interfaces each
use 300-byte buffers, while a compressed payload can expand well beyond that
capacity:

- [debug-only bounds macros](https://github.com/olliw42/mLRS/blob/a61aa83f610283b3f329170060adb899eef509e3/mLRS/Common/thirdparty/mavlinkx.h#L326-L331)
- [pre-validation decompression call](https://github.com/olliw42/mLRS/blob/a61aa83f610283b3f329170060adb899eef509e3/mLRS/Common/thirdparty/mavlinkx.h#L719-L735)
- [decoder writes](https://github.com/olliw42/mLRS/blob/a61aa83f610283b3f329170060adb899eef509e3/mLRS/Common/thirdparty/mavlinkx.h#L1082-L1169)
- [300-byte RX buffers](https://github.com/olliw42/mLRS/blob/a61aa83f610283b3f329170060adb899eef509e3/mLRS/CommonRx/mavlink_interface_rx.h#L43-L114)
- [RF validation/decryption path](https://github.com/olliw42/mLRS/blob/a61aa83f610283b3f329170060adb899eef509e3/mLRS/CommonRx/mlrs-rx.cpp#L448-L510)

This is a **source-confirmed unbounded decompression write on an RF-fed parser
path**. The likely immediate outcome is deterministic corruption or reset;
RCE, an exact overwritten object, and OTA reproduction remain unproven. A
proper decoder API must accept an output capacity, check before every literal
and run-length write, propagate failure, reset parser state, and validate all
length arithmetic. Switching away from MAVLinkX and enabling authenticated
privacy are useful compensating controls, not fixes.

## 4. EdgeTX FrSky telemetry-to-Bluetooth overwrite

The affected code remains in EdgeTX `v2.12.4`
`def35ad324896b45d6607d4778536b1bc5360d20`, maintained `v2.11.8`
`110a92701e3ddeaf44cb5875762623805cf69b08`, and current main
`788e1e1fd9384132703b647bb948f84584808b64`.
OpenTX issue
[#8623](https://github.com/opentx/opentx/issues/8623) independently identified
the capacity problem and remains open. The inherited code is also present in
OpenTX 2.3.15; this pass did not bisect the first affected release.

When Bluetooth telemetry mode is connected, each accepted/validated FrSky S.Port
record is passed to `Bluetooth::forwardTelemetry()`. The class owns a 33-byte
buffer followed immediately by an eight-bit `bufferIndex`. Forwarding adds
framing and byte-stuffs every data/checksum byte without a capacity check.
The buffer is flushed only after a whole record is appended:

- [buffer layout](https://github.com/EdgeTX/edgetx/blob/def35ad324896b45d6607d4778536b1bc5360d20/radio/src/bluetooth.h#L54-L119)
- [unchecked stuffing and post-record flush](https://github.com/EdgeTX/edgetx/blob/def35ad324896b45d6607d4778536b1bc5360d20/radio/src/bluetooth.cpp#L237-L245)
- [telemetry forwarding](https://github.com/EdgeTX/edgetx/blob/def35ad324896b45d6607d4778536b1bc5360d20/radio/src/bluetooth.cpp#L284-L298)
- [FrSky call site](https://github.com/EdgeTX/edgetx/blob/def35ad324896b45d6607d4778536b1bc5360d20/radio/src/telemetry/frsky_sport.cpp#L258-L270)

One record is 11 bytes before byte-stuffing. At most eight of its nine
data/checksum bytes can simultaneously require stuffing, so the encoded maximum
is 19 bytes. A 17-byte record can remain below the 18-byte flush threshold;
appending a following 19-byte record advances the nominal index to 36 against a
33-byte array. The first out-of-array byte aliases the index member itself on
the natural class layout. Once that byte is overwritten, subsequent behavior
is undefined and later write destinations may change; this is not a fixed,
deterministic three-byte overwrite.

The end-to-end path is conditional but concrete:

```text
FrSky-compatible telemetry over a bound RF link
  -> internal or multiprotocol module
  -> accepted/validated S.Port record
  -> EdgeTX telemetry parser
  -> enabled, connected Bluetooth telemetry forwarding
  -> unchecked byte stuffing
```

This does not apply to every radio or protocol. The target build must include
Bluetooth, the operator must select Bluetooth telemetry, and the malicious
telemetry must be accepted by the configured RF module. It is a
**source-confirmed controller object overwrite candidate**, not a demonstrated
wireless crash or RCE. The correct fix is to calculate worst-case encoded size
or flush/check capacity before every append. A target-specific simulator/unit
test and vendor coordination should precede live RF work.

Bluetooth is compiled by default on reviewed FrSky targets including X9E,
X9D+ 2019, Q X7 ACCESS, standard Q X7/Q X7S with runtime probing, X9 Lite S,
X-Lite/S/Pro, and Horus X10/X10S/X10 Express/X12S families. It is not a default
feature on several Jumper and RadioMaster builds, although custom builds can
enable it. Exact target firmware options must therefore be inventoried rather
than inferred from the radio brand.

### Secondary EdgeTX controller candidates

Two additional current parser defects are worth keeping below the confirmed
S.Port-to-Bluetooth path:

- The Ghost `MENU_DESC` handler uses a CRC-valid frame's eight-bit `lineIndex`
  to select an entry in a seven-element menu array without a bound, then writes
  a complete menu-line structure. This is a definite module-UART out-of-bounds
  write in
  [`ghost.cpp`](https://github.com/EdgeTX/edgetx/blob/788e1e1fd9384132703b647bb948f84584808b64/radio/src/telemetry/ghost.cpp),
  but the closed Ghost transmitter-module firmware prevents proving that a
  receiver can originate this frame over RF. Keep it as a conditional bridge
  target, not an OTA claim.
- The CRSF `DEVICE_INFO` handler subtracts a fixed overhead from a declared
  one-byte frame length. A short CRC-valid frame underflows that derived size
  and causes later indexed reads outside the declared message. Current
  ExpressLRS receiver source admits `DEVICE_INFO` into its receiver-side OTA
  queue, giving a partial RF route, but the complete receiver-to-controller
  reconstruction path was not closed in this pass. The result is an OOB-read /
  likely hardfault candidate, not an arbitrary write.

## 5. ESP-Drone Wi-Fi denial primitives

ESP-Drone current master `db0f6562e4f67cccee3acac4dff39c9aaea4e5fa`
listens for CRTP-over-UDP traffic on port 2390. The receive loop rejects only
negative results and datagrams longer than 32 bytes. It then reads
`rx_buffer[len - 1]` and passes `len - 1` to a checksum function that accepts
`size_t`:

- [official project and supported ESP32 family](https://github.com/espressif/esp-drone)
- [UDP receive path](https://github.com/espressif/esp-drone/blob/db0f6562e4f67cccee3acac4dff39c9aaea4e5fa/components/drivers/general/wifi/wifi_esp32.c#L132-L174)
- [CRTP forwarding](https://github.com/espressif/esp-drone/blob/db0f6562e4f67cccee3acac4dff39c9aaea4e5fa/components/core/crazyflie/hal/src/wifilink.c#L97-L152)

An empty UDP datagram produces the stack under-read `rx_buffer[-1]` and converts
the checksum length to the maximum `size_t`. The checksum routine then attempts
an enormous out-of-bounds read and is expected to fault or reset, although the
exact outcome is not source-guaranteed.

A one-byte datagram containing a valid zero checksum follows a separate path:
it queues a zero-length CRTP record. The forwarding layer later subtracts one
into an eight-bit size of 255 and reaches its fatal maximum-payload assertion.

Both are post-Wi-Fi-association denial-of-service candidates against an
ESP-Drone AP. Neither is an out-of-bounds write or RCE. Add explicit
minimum-length checks at both layers and cover zero-/one-byte datagrams in host
tests.

## 6. Confirmed and version-lag alternatives

### SJRC F11 / SJ-GPS-PRO

The strongest confirmed alternative is the SJRC F11 family research corpus.
It identifies a 2019 Fullhan FH8856 Linux/BusyBox build on an open Wi-Fi
network. Published CVEs cover unauthenticated FTP exposure, root Telnet, and a
TCP service command-injection path that passes attacker-controlled text to
`system()` as root. The researcher reports physical-device reproduction and
records vendor notification on 2026-05-17 with no response yet:

- [primary vulnerability repository](https://github.com/AshtonKopelevich/SJ-GPS-PRO-F11-Vulnerability)
- [disclosure timeline](https://github.com/AshtonKopelevich/SJ-GPS-PRO-F11-Vulnerability/blob/main/DISCLOSURE.md)

This is already a root execution path after joining the open drone network; a
ROP chain adds little value. Apparent rebadges such as Ruko F11 must be
fingerprinted before assuming the same firmware.

### OpenDroneID `core-c` release lag

The latest tagged release remains
[`v2.0`](https://github.com/opendroneid/opendroneid-core-c/releases/tag/v2.0).
Two 2026 reports affect that code:

- [issue #106](https://github.com/opendroneid/opendroneid-core-c/issues/106):
  a truncated, prefix-valid Wi-Fi NAN Remote ID frame causes unsigned-length
  underflow and out-of-bounds processing. Commit `2fe9d91` fixed it on master.
- [issue #107](https://github.com/opendroneid/opendroneid-core-c/issues/107):
  an oversized MAVLink message pack overflows arrays sized for nine messages.
  Commit `f16ec3b` fixed it on master.

The NAN issue is direct RF. MAVLink reachability depends on the integration.
Current master is fixed, but products pinned to the still-latest `v2.0` tag are
inventory targets. Public evidence establishes a crash/stack-smashing result,
not RCE.

### Crazyflie optional P2P DTR example

The bundled `app_p2p_DTR` example registers a callback for direct nRF24 P2P
broadcasts. Its handler takes the first received data byte as a copy length and
copies that many bytes into a 59-byte local `dtrPacket` without comparing it to
the received packet size or destination size:

- [DTR handler](https://github.com/bitcraze/crazyflie-firmware/blob/54f31e243a0b28b67efef5ba20dbb6d9890a5478/src/modules/src/p2pDTR/DTR_p2p_interface.c#L71-L83)
- [DTR packet definition](https://github.com/bitcraze/crazyflie-firmware/blob/54f31e243a0b28b67efef5ba20dbb6d9890a5478/src/modules/interface/p2pDTR/DTR_types.h#L42-L93)
- [example callback registration](https://github.com/bitcraze/crazyflie-firmware/blob/54f31e243a0b28b67efef5ba20dbb6d9890a5478/examples/app_p2p_DTR/src/p2p_DTR_app.c#L101-L126)

A declared length of 255 copies into the 59-byte local object, producing a
196-byte destination overrun plus a source over-read. Only the available P2P
data prefix is RF-controlled; bytes copied after the source object come from
adjacent memory. DoS is plausible and RCE is unproven. The callback is not
present in a stock release unless that optional example application is built,
so this is useful for source-audit regression and CTF custom-firmware inventory,
not as a blanket Crazyflie finding.

## 7. Other high-value targets

### Holy Stone / Fullhan families

The [H120D protocol corpus](https://github.com/zturner1/h120d-protocol)
documents a physically tested Fullhan FH8620/FH8830-family target running
RT-Thread 2.1.0, an open `HolyStoneFPV-*` AP, unauthenticated flight/control
traffic on UDP/8080, a stateless camera parser on UDP/8088, custom video on
TCP/8888, an unknown TCP/8830 service, and a Telnet/`finsh` shell. The best
offline fuzz candidates are the fixed time-sync message, variable-length
camera commands, custom video framing, and the unknown service. This is a
parser-rich target, not a proven overflow.

A 2025 physical study reports root Telnet on HS720 and unauthenticated RTSP plus
an authentication bypass on HS175D. A 2026 follow-up records ARM/Linux firmware
layouts and weak hardening:

- [2025 study](https://papers.academic-conferences.org/index.php/iccws/article/view/3286)
- [2026 firmware study](https://arxiv.org/abs/2605.11040)

No fixed version was identified. These are service and artifact-acquisition
targets rather than proven parser RCEs.

### HDZero goggle RTSP

HDZero goggle source at `648f63794bccf3b91df9f5f6f6949cd9a1932f91`
starts LIVE555 RTSP on TCP/8554 with a null authentication database whenever
Wi-Fi AP or STA service is enabled. The tree identifies bundled LIVE555
`2022.12.01`. Its RTSP SETUP/CSeq lifetime path is source-equivalent to the
`2023.05.10` code in the primary CVE-2023-37117 report:

- [HDZero RTSP creation](https://github.com/hd-zero/hdzero-goggle/blob/648f63794bccf3b91df9f5f6f6949cd9a1932f91/src/rtspLive/main.cpp#L336-L350)
- [bundled version](https://github.com/hd-zero/hdzero-goggle/blob/648f63794bccf3b91df9f5f6f6949cd9a1932f91/lib/live/include/liveMedia/liveMedia_version.hh#L22)
- [primary LIVE555 report](http://lists.live555.com/pipermail/live-devel/2023-June/022331.html)

This is a strong version/applicability inference, not device confirmation. It
requires joining the goggle AP or shared STA network. Expected impact is RTSP
process crash or memory disclosure; reliable RCE is not established.

### SiK telemetry radio remote command plane

Current SiK source accepts RF packets marked as remote commands, rewrites them
into local AT commands, and executes the ordinary command interpreter. That
branch runs before the later optional AES data-path CRC/decrypt block:

- [remote command dispatcher](https://github.com/ArduPilot/SiK/blob/dc90d9d6e79449929d1fb66003dbd9079b197ebf/Firmware/radio/tdm.c#L580-L613)
- [receive dispatch](https://github.com/ArduPilot/SiK/blob/dc90d9d6e79449929d1fb66003dbd9079b197ebf/Firmware/radio/tdm.c#L754-L773)

An injector still needs the radio's RF channel/hopping/network parameters and a
hardware-valid frame; key-derived hopping can raise that bar. Once reached,
remote reset and supported configuration/save operations are design-level
capabilities, not memory corruption. A robust mitigation would authenticate
control frames before dispatch or allow remote AT handling to be disabled.

### OpenHD-pinned wifibroadcast

The OpenHD-pinned wifibroadcast receive loop uses libpcap's original packet
length rather than the captured length, then performs radiotap/FCS/802.11
pointer arithmetic without first proving the captured bytes contain those
headers:

- [receive loop](https://github.com/OpenHD/wifibroadcast/blob/14d5fdb1b49a715529a39ce3583dae1e45776aeb/wifibroadcast/src/WBTxRx.cpp#L399-L415)
- [radiotap parser](https://github.com/OpenHD/wifibroadcast/blob/14d5fdb1b49a715529a39ce3583dae1e45776aeb/wifibroadcast/src/radiotap/RadiotapHeaderRx.hpp#L49-L167)
- [libpcap captured-length contract](https://www.man7.org/linux/man-pages/man3/pcap.3pcap.html)

Normal drivers generally provide complete link headers, so this is a hardening
candidate rather than a confirmed OTA crash. Truncated pcaps and driver-edge
fixtures should be tested before promotion.

### Parrot, Tello, and inexpensive Wi-Fi models

- Parrot's
  [CVE-2024-33844 discussion](https://forum.developer.parrot.com/t/cve-2024-33844-bugs-in-anafi-thermal-usa-firmware/22501)
  documents a control-process crash on ANAFI Thermal 1.8.2 and USA 1.10.4 after
  Wi-Fi association and MAVLink control. No fixed version was confirmed.
- Parrot still provides Bebop 2 firmware 4.7.1 downloads, making it a useful
  offline artifact target, but no current wireless parser corruption was
  verified. The well-known root shell requires a physical button sequence and
  is not a wireless bypass.
- The official Tello SDK documents unauthenticated text control on the drone's
  Wi-Fi network. Public firmware/dump corpora identify the Myriad 2 and Marvell
  radio stack, but no verified memory-safety bug was found.
- [`turbodrone`](https://github.com/marshallrichards/turbodrone) and the
  [Syma X22W corpus](https://github.com/rooterkyberian/syma) provide live-tested
  control/video implementations for many inexpensive models. They are valuable
  fuzzing corpora and authentication-failure targets, not current RCE claims.

## Fixed or historical findings retained only for version triage

- DJI BLE DUML control and credential-disclosure findings from 2026 affect a
  broad model list but are fixed above their published firmware thresholds.
  They remain useful only after exact-version fingerprinting.
- DJI QuickTransfer's 2023 TLV memory corruptions are strong parser-fuzzing
  precedent but are fixed on listed Mavic/Matrice/Mini builds.
- Holy Stone HSRID01 BLE power-off was fixed in the documented app/firmware
  delivery path. DBPOWER U818A FTP exposure and Parrot AR.Drone flooding issues
  concern legacy/EOL targets.
- No new source-verifiable direct proprietary-RF parser defect was found in
  Walksnail, OpenIPC, wfb-ng, or the HDZero video RF path during this pass.

## Recommended work order

1. Privately build the exact Crazyflie nRF and STM32 release targets with
   ELF/map files and add host memory-safety regressions.
2. Add a bounded mLRS MAVLinkX decompressor regression that supplies an output
   capacity and fails before any out-of-range write.
3. Add an EdgeTX simulator/unit regression for worst-case S.Port byte stuffing
   across consecutive retained records.
4. Add zero-/one-byte UDP tests to ESP-Drone and prepare a minimal fix.
5. Coordinate disclosure for new current-source findings before publishing
   live payloads or OTA demonstrations.
6. For competition inventory, prioritize confirmed SJRC/Fullhan services,
   OpenDroneID v2.0 downstreams, and Crazyflie design-level control paths before
   spending time on ROP/JOP.
7. Keep all active RF tests separately gated under `SECURITY.md`; do not use
   the E8 or TinyHawk as generic crash targets, and never flash either device.

The immediate strategic conclusion is that the project now has multiple
credible memory-corruption research tracks across aircraft, controllers, and
telemetry links. The next bottleneck is exact-build reproduction and responsible
disclosure, not finding another speculative parser.

Static source review identified bounds defects in current firmware paths. The
analysis establishes out-of-bounds memory access and, where noted, corruption
of control-adjacent state. It does not establish reliable code execution.
Results depend on the exact target build, compiler layout, enabled features,
and link reachability. Operational packet sequences, payload bytes, gadget
addresses, and live RF demonstrations are intentionally withheld pending
maintainer coordination and controlled reproduction.
