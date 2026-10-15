# Drone Project — detailed evening session notes

Date: October 14, 2026. User-facing timezone: America/New_York.

Repository: [oatmealphysics/research-project](https://github.com/oatmealphysics/research-project).
Local checkout: `C:\Users\Board\OneDrive\Desktop\Drone_Project\research-project`.
Working branch: `main`.

This report consolidates the work visible in this chat, the earlier same-day
handoff, and the shared Git history. It distinguishes Codex actions, owner
observations, and Claude's reported results. It is not a claim to have observed
every action in Claude's separate session. The documentation/integration pass
reviewed main through `18d9f05` and created merge `1f42eb6` before adding this
report. Some older companion notes label their session October 15; this report
uses the owner's October 14 date and does not treat those labels as proof of
different test times.

## 1. Executive summary

- Reviewed the existing research project and chose complementary work while
  Claude handled protocol tools, wordlists, comparative research and playbooks.
- Fixed firmware-corpus byte preservation across Windows/Linux checkouts and
  added reproducible integrity/provenance checks for all 11 registered artifacts.
- Repaired the serial diagnostic helper: passive default, explicit discovery/read
  mode, corrected CRSF definitions, preserved raw captures and offline decoding.
- Audited all 97 fully written CVE identifiers in the reference and substantively
  reviewed 16 claims. Recorded 95 published records, one rejected record and one
  identifier without a public record at the time checked.
- Added offline regression tests and a four-combination Windows/Linux CI matrix.
  The 33-test `tools/tests` suite passed again during this documentation pass.
- Identified the connected E8-like controller's Windows USB serial endpoint as
  COM4, VID:PID `8888:2001`, using the Microsoft CDC serial driver. This did not
  establish the actual board, MCU, radio IC or installed firmware version.
- Downloaded EMAX's custom configurator and inspected it statically without
  executing it in this chat. Recovered its normal 64-byte configuration-read
  request and the host's expected 17-byte reply format.
- Our normal vendor reads received no bytes, including after the owner reported
  the documented serial-mode startup. Some other attempts were blocked before
  transmission because COM4 could not be opened.
- Recorded the owner's earlier successful Ubuntu/Wine read, with `/dev/ttyACM0`
  and recalled Wine port COM33. Those names are historical, not firmware IDs.
- Stopped competing for COM4, published a Claude/Codex handoff, and made the
  owner's absolute no-flashing instruction prominent in `AGENTS.md`.
- Integrated the one additional Claude branch whose seven workflow files were
  still absent from main. No new workflow, RF tool or hardware probe was run
  during the notes/integration pass.

Bottom line: the project has better evidence, tooling and reproducibility, but
there is still no captured successful E8 reply in this chat, no installed firmware
version, and no demonstrated exploit or wired-to-wireless delivery path.

## 2. Owner instructions and scope

The owner requested a review of the Git project, non-conflicting progress toward
the CTF goal, publication to GitHub, and characterization of the connected EMAX
controller. They later requested detailed Markdown notes and that completed work
be present on GitHub's main branch.

Important operating instructions:

1. Speak English even if the owner uses another language.
2. **Do not flash the EMAX controller with anything under any circumstances.**
   This includes upload, erase, restore, replacement and firmware update work.
3. Current controller characterization is identification and normal configuration
   reads only. Do not use speculative settings writes, reset/bootloader-entry
   sequences, live fuzzing or RF transmission to resolve the connection issue.
4. Coordinate with Claude rather than competing for the same serial endpoint.
5. Preserve concurrent work and Git history. Pull before edits; do not force-push.
6. Keep private configuration, captures, local dependencies and downloaded
   inspection artifacts out of the public/shared source history.

The broader CTF repository contains active tools and historical research plans.
Their presence does not override the connected-controller restrictions or prove
that any capability works against this particular handset.

## 3. Starting point and division of work

The existing project contained a wireless/chipset vulnerability reference,
research plan, firmware/source corpus, an initial 193-phrase ELRS lookup table,
and a serial probe. Earlier same-day handoff work is preserved in
[HOME-LAB-HANDOFF.md](https://github.com/oatmealphysics/research-project/blob/main/HOME-LAB-HANDOFF.md)
and Claude's
[SESSION-NOTES.md](https://github.com/oatmealphysics/research-project/blob/main/SESSION-NOTES.md).
Those are historical snapshots; some assumptions were corrected later.

The owner said Claude was handling OTA-version parameterization, wordlist
expansion, a MAVLink fuzzer, failsafe comparisons, a game-day playbook, ELRS v3/v4
comparison and a GPS-frame proof of concept. Codex concentrated on review,
evidence quality, corpus integrity, serial-tool correctness, reproducibility and
the connected-device investigation. Later Git synchronization brought in a much
broader set of Claude contributions, listed in Section 11.

The earlier handoff also documented source extraction, binary analysis settings,
hardware-identification gaps and an evidence template. Its instructions about
firmware images are for offline test articles, not permission to flash the E8.

## 4. Repository integrity and provenance work

### 4.1 Cross-platform corpus byte preservation

The downloaded text-like firmware/reference files needed exact byte preservation:
Git line-ending conversion could otherwise make a correct corpus fail its
SHA-256 manifest after a Windows checkout.

Commit `3415ca9` added `.gitattributes` rules that disable text conversion for
the Betaflight/reference artifact directories and source archives, while keeping
the checksum manifest itself LF-normalized. The firmware README explains why.
An earlier fresh-index export/check verified all 11 manifest entries after the
change. Current offline checks also verify the corpus on this checkout.

This establishes byte consistency with the manifest, not vendor authenticity,
compatibility with the E8 or the installed firmware version.

### 4.2 Artifact register

[firmware/artifact-register.json](https://github.com/oatmealphysics/research-project/blob/main/firmware/artifact-register.json)
records the 11 artifacts' paths, lengths, SHA-256 values, type, declared target,
declared version where known, upstream project, hardware-match classification
and evidence limitations.

Important qualifications were preserved rather than filled with guesses:

- Missing original download URLs and upstream commit attribution remain null.
- Related EMAX module definitions do not identify the owner's E8.
- None of the registered corpus entries is established as an E8-specific image.
- Source archives, target registries and a Lua UI script are not interchangeable
  with device firmware binaries.
- Converted raw binaries identify their Intel HEX parents, but an unavailable
  conversion log was not invented or marked verified.
- A master snapshot or nearby target name is not an exact installed-build match.

## 5. Serial diagnostic repair and regression coverage

The earlier serial helper mixed a passive-sounding interface with active
operations and had protocol/response-handling assumptions needing correction.
Commit `7b2a089` reworked
[tools/elrs-serial-probe.py](https://github.com/oatmealphysics/research-project/blob/main/tools/elrs-serial-probe.py).

The repaired behavior includes:

- Receive-only operation by default; discovery/configuration reads require an
  explicit probe flag, and parameter dumping requires probe mode.
- An explicitly selected endpoint rather than treating a hardcoded port as the
  connected controller. Listing ports is separate from opening one.
- CRSF addresses/frame definitions checked against pinned ExpressLRS 4.1.0
  source, CRC checking and a 64-byte total-frame bound.
- Stateful handling of fragmented frames, noise, corrupt lengths/CRC and
  addressed frame variants.
- Validation of complete device-info payloads and the responding origin before
  treating a frame as the module identity.
- Parameter-read chunk handling instead of silently accepting incomplete output.
- Raw RX capture that retains all bytes, creates parent directories and refuses
  to replace an existing capture.
- Offline decoding without requiring a serial connection or pyserial.
- JSON-line results on standard output and action/error logs on standard error.
- Error returns for missing/incomplete discovery rather than a false success.

The tool does not support parameter writes or firmware updates. Opening a serial
port can still affect control-line state, so passive means no application request
is intentionally transmitted, not zero interaction with the device.

Regression tests use simulated serial endpoints. They test the above behavior,
capture preservation and error-path cleanup; they do not prove the E8 implements
USB CRSF. No valid CRSF response was obtained from this unit in this chat.

## 6. Vulnerability-reference audit

The large reference contained claims at different evidence levels. We added
[research/CVE-AUDIT.md](https://github.com/oatmealphysics/research-project/blob/main/research/CVE-AUDIT.md),
[research/cve-record-index.json](https://github.com/oatmealphysics/research-project/blob/main/research/cve-record-index.json)
and `tools/audit-cve-records.py` to make the checks reproducible.

The recorded inventory covers 97 distinct, fully written CVE identifiers:

| Recorded publication result | Count |
|---|---:|
| Published | 95 |
| Rejected | 1 |
| HTTP 404 / no public record returned at the time checked | 1 |

Sixteen claims received substantive review; the other entries are explicitly
marked record-only. Publication alone does not prove a public exploit, radio
reachability, applicability to the lab equipment or a flight effect.

Examples of corrections/qualifications:

- Distinguished the Broadcom proprietary driver from `brcmfmac`, and advisory
  possible-RCE language from the typically described denial-of-service result.
- Qualified a Realtek driver report based on interface fuzzing rather than
  treating its network CVSS label as a demonstrated unauthenticated RF trigger.
- Corrected Qualcomm buffer-over-read descriptions rather than calling them
  demonstrated overwrite/RCE primitives.
- Preserved CVE-2025-71226 as rejected, and CVE-2019-15063 as unresolved after a
  missing public record; a 404 was not described as proof of fabrication.
- Corrected the swapped Espressif CVE-2026-25507 / CVE-2026-25508 descriptions.
- Recorded provisioning state, shell access and unsigned-link prerequisites
  where they materially affect applicability.
- Preserved inconsistent vendor/CNA fixed-version lists as a discrepancy to
  resolve, rather than presenting one universal fixed-version claim.

The refresh tool requires an explicit network-refresh flag. Raw downloaded CVE
records/candidate data remain under ignored `work/`. No PoC or hardware exploit
was run as part of the audit.

## 7. Offline validation and CI

[tools/validate-project.py](https://github.com/oatmealphysics/research-project/blob/main/tools/validate-project.py)
checks Python 3.10-compatible syntax without importing research scripts, JSON,
corpus hashes/register coverage, stored rainbow CSV/JSON consistency, CVE-index
coverage and local Markdown file targets. It does not validate external links,
Markdown anchors, firmware authenticity or every tool's protocol mathematics.

The GitHub Actions workflow has read-only repository permissions and pinned
action revisions. It runs validation and the standard-library test suite on:

| OS | Python |
|---|---|
| Ubuntu | 3.10 |
| Ubuntu | 3.12 |
| Windows | 3.10 |
| Windows | 3.12 |

Earlier in this session all four jobs were observed passing for the validation
work: [recorded CI run](https://github.com/oatmealphysics/research-project/actions/runs/38664442873).
This is a historical result for that run, not a blanket assertion about future
commits. Local validation passed after the additional workflow branch merge,
covering 80 repository files before this report was added. The 33-test suite
passed again while preparing these notes.

Final local validation after adding this report passed across **81 files**, and
the offline regression suite again passed **all 33 tests**. Neither command
opened a serial port, imported the active workflow scripts or transmitted RF.

Claude separately added a root `tests/` pytest suite described as 90 tests in its
commit. It is not the same as the 33 tests under `tools/tests`, and Codex has not
claimed to run or certify that separate suite in this chat. The CI commands here
do not establish that all of Claude's active tools are field-tested.

Offline reproduction from the repository root:

```sh
python -B tools/validate-project.py
python -B -m unittest discover -s tools/tests -v
```

The local Python environment is `work/validation-venv/Scripts/python.exe`, using
Python 3.12.10. Its Windows runtime required approved execution outside the
restricted shell. Git also had to be available on the command search path for
the validator; an initial missing-executable error was resolved without changing
the validator or bypassing the sandbox. Local analysis dependencies included
pyserial 3.5, PyYAML 6.0.3, pycryptodome 3.23.0, pefile 2024.8.26,
evbunpack 0.2.6, aplib 0.6 and capstone 5.0.6.

## 8. Connected controller: observations and identity limits

The owner reported a screenless, unlabelled controller matching EMAX E8 product
photos. They believed it was battery-powered and described a red light while
USB-connected. Subsequently they followed the R4+ plus power startup procedure
and observed a flashing green signal/mode light.

The owner also reported a previous successful configuration read using EMAX's
custom configurator on Ubuntu under Wine. The historical Linux endpoint was
`/dev/ttyACM0`; they recalled COM33 in Wine. The earlier response data, exact
software build and Wine port mapping are not currently available to this chat.

| Item | Observed/reported value | What it does not establish |
|---|---|---|
| Current Windows controller endpoint | COM4 | A portable port number across computers/reconnects |
| USB identity | VID:PID `8888:2001` | E8 MCU, radio IC or a unique product fingerprint |
| Interface | CDC ACM-style class/subclass/protocol `02/02/01` | Application authentication or command semantics |
| Bus description | USB Serial | Product/firmware version |
| USB revision field | REV_0100 | Installed firmware 1.00 |
| Windows driver | Microsoft usbser, 10.0.26100.9278 | Controller firmware version |
| Other endpoint | COM3, Prolific PL2303GC adapter | That it is the E8; Codex did not probe it |
| Earlier Ubuntu endpoint | Owner-reported `/dev/ttyACM0` | An exact identity/build match without descriptor evidence |
| Earlier configurator port | Owner-recalled COM33 | That COM33 must be used on Windows now |
| Installed controller firmware | Not recovered | Any version-specific vulnerability match |

COM33 was likely Wine's Windows-style alias for the Linux serial endpoint. This
is an inference, not an inspected mapping. The `ttyACM` name is consistent with
the USB serial class observed here; it does not prove ESP32, STM32 or SX1280.

EMAX's product material distinguishes visually similar E8 variants by connector
type: USB-C for ELRS and micro-USB for FrSky D8. The owner's reply established USB
use and the startup action, but was not an unambiguous connector/PCB inspection.
No screen, LED pattern or related-module target alone establishes installed ELRS
version. See the
[unit evidence record](https://github.com/oatmealphysics/research-project/blob/main/research/E8-USB-CHARACTERIZATION.md).

## 9. Live operations actually performed by Codex

These are this chat's operations, not a consolidation of every probe Claude
reported. No firmware upload, erase, update, intentional reset/bootloader-entry
sequence, settings-write request, malformed-input test or RF transmission was
performed by Codex against the controller.

| Operation | Configuration | Actual outcome |
|---|---|---|
| Passive serial capture | COM4, 115200, five seconds | Zero received bytes |
| CRSF discovery | COM4, 420000; passive period, broadcast ping and targeted device ping | Zero received bytes; no device info |
| CRSF attempt at 115200 | COM4 | Port-open denied; no requests sent in this attempt |
| Subsequent passive open | COM4, 115200, DTR/RTS requested false | Open succeeded; zero bytes |
| First normal vendor read | COM4, 115200, 8-N-1, DTR/RTS requested false; one immutable 64-byte request | Zero received bytes within three seconds |
| Normal vendor read after reported R4+ startup | Same request/settings | Zero received bytes within three seconds |
| Planned longer-settle vendor read | Two-second settle/eight-second receive window intended | Port-open denied before any send; the longer observation did not run |

The actual CRSF discovery requests were `c8042800ea54` and `c80428eeea97`.
Failure to open is not an unanswered request: those attempts must not be counted
as evidence that a baud/protocol failed after transmission.

Raw files currently present under ignored `work/e8-session/`:

| Capture | Recorded length |
|---|---:|
| `usb-passive-115200.bin` | 0 bytes |
| `crsf-discovery-420000.bin` | 0 bytes |
| `crsf-discovery-115200.bin` | 0 bytes; associated attempt was denied before send |
| `vendor-read-1792027919205780000.bin` | 0 bytes |
| `vendor-read-1792028341007747700.bin` | 0 bytes |

The empty files preserve negative RX observations, not proof of valid request
delivery at USB level, of the correct operating mode, or of a defective device.
There was no recovered private binding/configuration response in these captures.

## 10. EMAX configurator acquisition and offline analysis

### 10.1 Exact local artifact

The manufacturer support page linked a public Mega download. The downloaded file
remains local and ignored by Git:

```text
C:\Users\Board\OneDrive\Desktop\Drone_Project\research-project\work\e8-session\vendor\EMAX E8 ELRS Configurator.exe
```

| Attribute | Result |
|---|---|
| Filename | EMAX E8 ELRS Configurator.exe |
| Length | 23,782,400 bytes |
| SHA-256 | `7ec41368c1d7fe9cbf7e25ba053b05519f7e1aa8542c698e70764162be99362d` |
| PE architecture | x64 |
| Framework/toolchain evidence | Qt5 / MinGW |
| Packaging | Enigma Virtual Box wrapper |
| Windows Authenticode result | NotSigned |
| Execution by Codex | None |

The filename, length and hash were rechecked for this report. The downloader
checks size/decryption structure but does not verify Mega's file MAC. Neither a
public share nor an unsigned executable independently authenticates the build.

`tools/inspect-e8-configurator.py` downloads/inventories the artifact without
running it. Static package listing exposed Qt/runtime DLLs, translations and
`serial_bootloader.exe`. No separately named firmware `.bin` appeared in that
listing; this was not an exhaustive embedded-resource analysis. The loader was
not executed by Codex and must not be used against the connected controller.

Unpacker variants 9_70 and 10_70 produced approximate reconstructed PE files with
zero-size import/relocation warnings. They were not treated as trustworthy
runnable restorations. Analysis instead used intact code/name-backed imports in
the original file. `tools/inspect-e8-serial-handlers.py` labels imports and
disassembles selected windows with pefile/capstone; it does no device I/O.

### 10.2 Attributed normal read protocol

The recovered operation is the vendor UI's Read Configuration action, not a
firmware-version query and not the distinct settings-write/update actions.

- Serial settings: 115200, eight data bits, no parity, one stop bit, no flow
  control.
- Request: exactly 64 bytes, `02 01` followed by 62 zero bytes.
- The request construction includes a zeroed payload and CRC bytes; the
  inspected CRC16 routine uses polynomial `0x1021` with zero initialization.
- Host callback: asks for 17 response bytes and checks a leading `02`.
- The reviewed UI includes rate, telemetry ratio, power and six-byte binding
  data. A valid reply may contain sensitive material and must remain private.
- No dedicated firmware-version display/query was identified in this bounded
  inspection. The helper deliberately reports firmware version as unknown.

Selected original-file virtual addresses, with image base `0x400000`:

| Evidence | Address |
|---|---|
| Qt static metaobject | `0x40e020` |
| String/method metadata | `0x413c00` / `0x413a60` |
| Read button dispatch and handler | method 10 → `0x407fb0` → `0x404620` |
| Read request construction | `0x404708–0x404807` |
| Separate write button handler | method 11 → `0x407f40` → `0x405940` |
| Baud-rate setter call | `0x40636e`, argument `0x1c200` |
| Read-response callback | `0x406c60` |
| Serial-open handler | `0x4018b0`; open call `0x401b0c` |
| Reviewed open-success path | `0x401de5–0x401f26`, returning through `0x401a3d` |

The reviewed open-success path changes UI state; no extra application protocol
write/handshake was identified there. This is not a runtime trace of Qt, the USB
driver or the device, and does not establish Windows/Wine line-control equality.

`tools/e8-read-config.py` implements only that immutable read request. It defaults
to dry-run, checks the selected VID/PID before opening, preserves raw responses
privately, does not print binding payloads and has no write/update/reset support.
No live command should be launched while Claude owns COM4.

### 10.3 Version distinctions and research lead

`Serial Update Tool 2.1` is a host application title. `REV_0100` is a USB revision
field. The Microsoft driver version describes the PC. None is the recovered E8
firmware version.

A bounded static review found that a host response path tests a nonzero read,
checks the first byte and copies 16 payload bytes without a full-length check
identified on that path. Short/fragmented-response robustness is therefore a
host-side research hypothesis. No offline crash reproduction, memory corruption,
controller vulnerability or RF reachability has been demonstrated from it.

## 11. Claude contributions visible in shared Git history

The following are merged repository contributions, not independent verification
by Codex that their advertised capabilities work on the owner's hardware.

| PR / commit | Added or updated material |
|---|---|
| #1 `0a2f4da` | Static CRAZYBEEF4SX1280 firmware analysis document |
| #2 `f0a5e4c` | WiFi recon, MAVLink recon/injection, Tello and ELRS analysis scripts |
| #3 `ec9deb6` | OTA-version parameterization and lookup/SYNC documentation corrections |
| #4 `a6dfb17` | MAVLink protocol fuzzer |
| #5 `4add944` | Expanded binding-phrase list, 193 → 723 phrases |
| #6 `2e53926` | Betaflight/ArduPilot/iNav failsafe comparison |
| #7 `e48f4e6` | GPS serial-frame proof-of-concept tool |
| #8 `6d9e10a` | ELRS v3/v4 security-relevant comparison |
| #9 `5f1f05d` | BLE reconnaissance scanner |
| #10 `b10693d` | Separate root pytest suite, described as 90 tests |
| #11 `8f28f80` | ELRS packet-construction tool |
| #12 `2da17ce` | WiFi attack-surface reference |
| #13 `c1fa976` | Recon dashboard and supporting scanner integration |
| #14 `00872fb` | Shared SoapySDR abstraction module |
| #15 `54fba6e` | Tello video-capture tool |
| #16 `52196a7` | Passive telemetry interception/recon tool |
| #17 `6262fa5` | Game-day playbook |
| #18 `81eab5d` | ELRS transmitter-emulation tool |
| #19 `9cf1546` | MAVLink signing/replay research and tool |
| #20 `ca9f3eb` | CRSF protocol analysis and packet tool |

The OTA lookup now exposes an OTA-version selection flag for versions 3/4 and
records the selected version in generated data. This does not identify which
version is installed on the connected handset. The stored-data consistency
validator does not independently re-derive every protocol value.

While fulfilling the final main-branch request, Codex found one additional remote
branch, `claude/automated-exploit-chains-srzygm`, commit `000e2fc`. Its seven
paths were absent from main; all other inspected Claude branch file paths were
already represented there. Merge `1f42eb6` integrated it without conflicts:

- `tools/exploit_chains/README.md`
- `tools/exploit_chains/__init__.py`
- `tools/exploit_chains/common.py`
- `tools/exploit_chains/elrs_chain.py`
- `tools/exploit_chains/mavlink_chain.py`
- `tools/exploit_chains/multi_recon.py`
- `tools/exploit_chains/wifi_tello_chain.py`

The merge added source/documentation only and preserved the main-branch safety
rules. These workflows were not run, armed, connected to hardware or field-tested
by Codex. Their README's safety claims are author documentation, not a newly
completed operational audit. Older PR branches may remain listed as unmerged by
Git ancestry after squash merges; branch retention alone does not mean their
files are missing from main. This pass did not delete feature branches.

## 12. Concurrent E8 reports: what remains qualified

Claude's root
[E8-SERIAL-CHARACTERIZATION.md](https://github.com/oatmealphysics/research-project/blob/main/E8-SERIAL-CHARACTERIZATION.md)
reports broader testing than this chat performed: protocol/baud/line-format
sweeps, control-line changes, reset/boot-trigger patterns, several serial stacks,
and later vendor-frame variations. Revisions `974dbf6` and `18d9f05` were pulled
while preparing the handoff/report.

The latest revision also reports that the official configurator was run on
Windows and failed to read. That is another session's reported observation.
Codex did not execute the application, witness its UI or obtain a successful
device response. Do not combine the sessions into a claim that Codex ran it.

Two important qualifications were added while preparing these notes:

1. **Windows usbser incompatibility remains a hypothesis.** A generic class
   driver, driver-ranking information, zero modem-status lines and an empty read
   do not demonstrate the cause. Even a reported failure in the vendor UI does
   not isolate the driver. No paired successful Linux USB capture and failing
   Windows trace was supplied to this chat. Hardware state, exact app/build,
   timing, line controls and USB transport behavior remain comparison points.
2. **The listed `02 02` zero-payload variant is not an established read.** Our
   vendor static analysis attributes a separate settings-write operation to that
   opcode. Claude's document lists the frame among its tests; Codex did not send
   it. No successful readback establishes whether it was accepted or had an
   effect. It must not be repeated or labelled harmless because no response was
   received.

Earlier claims that battery/charging state explained every failure, that the
handset is confirmed ESP32/SX1280, that its USB commands are unauthenticated, or
that WiFi/update paths exist on this unit are also not established by the
observations. Older update/bootloader/driver-replacement suggestions are not
authorized next steps for the connected controller.

## 13. Coordination and shared hardware access

COM4 was intermittently locked. The owner confirmed Claude was also probing and
asked both agents to work together. We did not prove which process held the
port during each denial, and no other process was killed or forcibly detached.

Codex stopped opening COM4 and continued offline. We published
[research/E8-COLLABORATION.md](https://github.com/oatmealphysics/research-project/blob/main/research/E8-COLLABORATION.md)
and added the no-flashing/access rules to `AGENTS.md`. The handoff includes the
vendor read attribution, unsuccessful outcomes and earlier Ubuntu/Wine context.

There is no direct messaging connection from this chat to the external Claude
app. The owner was asked to relay the note. Publication of a handoff is not
confirmation that Claude read or acknowledged it. A free port is not an explicit
release; confirm ownership before any further live read.

## 14. Important corrections and non-results

| Tempting conclusion | Evidence-supported status |
|---|---|
| USB enumerated, so the application is active and accepts commands | Enumeration alone establishes neither |
| No response proves charging-only mode or an incompatible driver | Cause remains unresolved |
| The E8 is an ESP32/SX1280 because related EMAX modules are | Related-module hypothesis only |
| Configurator 2.1 or USB REV_0100 is the installed firmware version | Incorrect; firmware version remains unknown |
| The vendor normal read is now fully implemented/tested | Request attributed; no successful live reply captured here |
| A USB configuration request can simply be sent over RF | No shared parser/bridge or wireless reachability demonstrated |
| A CVE record proves a usable radio exploit | Publication, applicability, reachability and outcome are distinct |
| Merged tools / passing syntax checks prove takeover capability | No such hardware validation occurred in this chat |
| No response to a possible write proves settings were unchanged | No successful readback exists to establish effects |

No controller exploit, firmware extraction, flight effect, wireless configuration
access, denial of service or installed-version vulnerability match was
demonstrated by Codex. The handset was not flashed by Codex. The other session's
broader actions are attributed to its report rather than independently certified.

## 15. Git milestones and publication record

These are commit timestamps from the local Git history in Eastern time, not
precise measurement timestamps for individual experiments.

| Time, October 14 | Commit | Milestone |
|---|---|---|
| 15:32 | `5e15ddb` | Detailed home-lab handoff and complete checksum manifest |
| 15:34 | `4f0d9e4` | Cross-referenced concurrent notes and qualified earlier device claims |
| 17:26 | `3415ca9` | Corpus byte-preserving checkout rules |
| 21:12 | `7b2a089` | Serial repair, provenance/CVE audit, tests and CI |
| 21:20–21:26 | PRs #1–#20 | Claude's main-branch contributions summarized above |
| 21:34 | `8285632` | E8 endpoint/vendor-read tools and evidence |
| 21:35 | `0321837` | Normal merge preserving concurrent main histories |
| 21:36 | `98dd02e` | Qualified concurrent E8 claims |
| 21:45 | `d108d6a` | Shared controller-access handoff and no-flashing rule |
| 21:46 | `c6e21f8` | Recorded earlier Ubuntu/Wine port names |
| 21:49 | `974dbf6`, `0d74273` | Claude's powered-on report and merge |
| 22:53 | `18d9f05` | Claude's later serial-mode/Windows-driver report |
| 22:56 | `1f42eb6` | Integrated remaining seven-file workflow branch into main |

The earlier Codex commits through `c6e21f8` were pushed and synchronized with
origin/main. The owner subsequently requested this report, all completed changes
uploaded, and everything integrated into main. The final publication commit will
contain this report and the additional qualifications; its own hash is obtained
from Git history rather than embedded self-referentially in this file.

No history rewrite or force-push was used. The unsigned configurator, venv,
unpacked analysis products, cached CVE responses and private serial captures are
deliberately not uploaded. Uploading project work does not mean publishing
ignored private data or every dependency/cache.

## 16. File map and restart checklist

| Location | Use |
|---|---|
| `AGENTS.md` | Collaboration rules and absolute controller restrictions |
| `research/SESSION-2026-10-14-EVENING.md` | This comprehensive session record |
| `research/E8-USB-CHARACTERIZATION.md` | Codex unit evidence, normal read attribution and limits |
| `research/E8-COLLABORATION.md` | Shared port ownership and Claude handoff |
| `E8-SERIAL-CHARACTERIZATION.md` | Claude's broader reported tests, with qualification banners |
| `HARDWARE-IDENTITY.md` | Discovery history and identity limits |
| `tools/e8-read-config.py` | Dry-run-by-default immutable vendor reader |
| `tools/inspect-e8-configurator.py` | Download/static inventory only |
| `tools/inspect-e8-serial-handlers.py` | Offline PE disassembly helper |
| `VALIDATION.md`, `tools/tests/` | Offline checks and 33-test suite |
| `research/CVE-AUDIT.md`, `research/cve-record-index.json` | Evidence audit and record coverage |
| `firmware/artifact-register.json`, `firmware/SHA256SUMS` | Artifact identity/provenance |
| `work/e8-session/vendor/` | Ignored local configurator and derived inspection files |
| `work/e8-session/` | Ignored private captures |
| `work/validation-venv/` | Ignored local analysis/test environment |

Next useful actions, without expanding authority:

1. Read current `AGENTS.md`, pull main and check for concurrent changes.
2. Confirm who owns the controller port; do not resume live testing without an
   explicit handoff. Re-identify the endpoint after any reconnect.
3. Recover the exact known-working Ubuntu/Wine setup and its app/build/settings,
   if available. Preserve a normal successful read privately before drawing
   conclusions about drivers or firmware. Do not change Windows drivers merely
   to test the current hypothesis.
4. If a successful normal read is obtained by the agreed owner, record exact
   conditions and byte count, preserve private binding data, and distinguish any
   real version-labelled output from host/USB metadata. No flash or settings write.
5. Continue offline host-response robustness and exact-image provenance work.
   Do not claim that an upstream source sample or related module is this unit.
6. Treat Claude's active tools as unvalidated until separately reviewed/tested in
   an authorized setup. Do not run them against the E8 to fill documentation gaps.
7. Verify final local main, remote main and publication status. Retain ignored
   local artifacts; do not delete them as part of a push.

## 17. Primary references used during the session

- [EMAX E8 custom configurator support article](https://emaxusa2.freshdesk.com/support/solutions/articles/19000157264-expresslrs-e8-transmitter-custom-configurator)
- [EMAX E8 product page](https://emax-usa.com/collections/diy/products/e8-transmitter)
- [Manufacturer Nanoscout RTF manual, hosted mirror](https://myosuploads3.banggood.com/products/20250204/20250204015303TinyhawkNanoscoutRTFV2.0.pdf), PDF page 16 / printed page 14 for E8 serial startup/read workflow
- [Linux kernel ACM driver documentation](https://docs.kernel.org/usb/acm.html)
- [Qt serial-port Windows implementation, 5.15 branch](https://raw.githubusercontent.com/qt/qtserialport/5.15/src/serialport/qserialport_win.cpp), reference behavior only; exact bundled Qt/runtime equivalence is not established
- [evbunpack project documentation](https://github.com/mos9527/evbunpack/blob/main/README.md)
- [CVE evidence audit and its per-claim primary links](https://github.com/oatmealphysics/research-project/blob/main/research/CVE-AUDIT.md)

These links support the research record; the report-writing pass did not repeat
all external research or hardware experiments. Detailed claims retain the limits
described above. The next session must preserve the no-flashing constraint.
