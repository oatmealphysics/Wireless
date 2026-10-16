# Device evidence dossiers

These folders apply the [new-hardware blueprint](../docs/new-hardware-research-blueprint.md)
to the two owned EMAX devices. They separate what was observed on the connected
unit from source-derived conclusions, related-product references, and open
questions. Private identifiers and raw captures remain under ignored `work/`.

| Device | Hardware | Device software | Installed firmware | Overall status |
|---|---|---|---|---|
| [EMAX E8](emax-e8_material/README.md) | Product/interface family confirmed; exact PCB revision, MCU, and RF IC unknown | USB HID and vendor-configuration personalities confirmed; vendor host tool statically characterized | Exact image and version not obtained; normal read response has no version field | **Partially identified** |
| [EMAX TinyHawk III Plus](tinyhawk-iii-plus_material/README.md) | Live target reports STM32F411 family, EMAX board target, integrated SPI ELRS/SX1280 configuration; exact chip top-marks and PCB revision not physically inspected | Betaflight and MSP identity confirmed live | Betaflight 4.4.2 build/revision confirmed; official version-matched binary/source acquired, but installed flash bytes were not read out | **Functionally identified; physical and byte-identity gaps remain** |

This status is intentionally stricter than “we know what model it is.” A device
is fully identified only when exact-unit hardware evidence, runtime identity,
and artifact provenance agree, with remaining differences stated explicitly.
