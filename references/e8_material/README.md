# E8 ELRS reference material

Local downloaded material for offline controller-interface research. Downloads
and upstream checkouts stay untracked; provenance and investigation notes are
tracked. The operator authorized downloading and retaining reference material
on 2026-10-14. Nothing here is authorization to flash a device.

## Layout

- `downloads/`: vendor documentation and host tools; device firmware goes here
  only when its origin and intended target have been identified.
- `upstream/`: official source/hardware-definition checkouts for comparison.
- `manifest.json`: source URLs, file hashes, sizes, classifications, and revision
  identifiers for locally acquired artifacts.

**Exact E8 ELRS firmware:** not yet obtained. Do not confuse the Windows
configurator with device firmware, or FrSky E8 / Aeris / flight-controller
images with the E8 ELRS transmitter image. See the manifest for current results.

## Acquisition results (2026-10-14)

- Downloaded the Tinyhawk III Plus RTF manual from the Google Drive file linked
  by [EMAX's manuals article](https://emaxusa2.freshdesk.com/support/solutions/articles/19000155807-tinyhawk-iii-plus-freestyle-manuals).
  Pages 12–14 describe the E8 serial update/configuration tool and refer to the
  vendor website for firmware files; they do not identify the MCU or provide a
  direct E8 firmware image URL.
- Downloaded the E8 configurator support page. Collected the two previously
  unpacked Windows host executables from the existing local vendor package;
  neither is device firmware. They were copied, not downloaded anew.
- Cloned official ExpressLRS firmware source and hardware definitions for
  offline comparison. Exact revisions are in the manifest. `gh` was unavailable,
  so the gh-cli skill's preferred local-clone workflow used HTTPS Git directly.
  No downloaded executable or build script was executed.
- The checked hardware definitions list EMAX OLED/Nano transmitter targets on
  ESP32, but no E8 entry. A targeted search of upstream source target files and
  `platformio.ini` did not establish an E8/Air32/AT32 target. This is not proof
  of the E8's MCU model or incompatibility with every upstream implementation.
- Checked the [vendor E8 support category](https://emaxusa2.freshdesk.com/support/solutions/folders/19000175667),
  its [configurator article](https://emaxusa2.freshdesk.com/support/solutions/articles/19000157264-expresslrs-e8-transmitter-custom-configurator),
  and the [current downloads page](https://shop.emax-usa.com/pages/downloads).
  No verified E8 ELRS device firmware image was obtained from these pages.
  A matching vendor-supplied image or matching board information remains needed
  for device-specific OS/console analysis.
