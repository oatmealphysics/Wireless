# TinyHawk III Plus interface map

| Component/state | Interface | Direction | Evidence | Capability established | Limit |
|---|---|---|---|---|---|
| Betaflight normal USB | CDC ACM `0483:5740` | Host ↔ FC | Current descriptors and prior live CLI | Betaflight CLI/MSP identity and config reads | Port path can change; do not issue save/defaults/reboot/flash commands |
| Flight-controller application | MSP API 1.45 / Betaflight CLI | Host ↔ FC | Live version/config transcript | Version, target, resources, and settings readable | Does not provide an exact flash image |
| Integrated receiver | SPI bus 3, ExpressLRS | MCU ↔ radio | Live config and target resource map | ELRS RX is compiled/integrated, not an external serial receiver | Exact radio top-mark not physically observed |
| Radio front end | SX1280-class 2.4 GHz ELRS path | Air ↔ receiver | Target name/resources, source config, passive RF measurement | 4.4.2 v3 150 Hz LoRa behavior is source/capture supported | Live transmit/injection remains separately gated |
| Sensors | MPU6000-class gyro on SPI bus 1 | Sensor → MCU | Live resource/config output | Sensor family/bus identified | Physical part/revision not photographed |
| Actuators | Motor outputs | FC → motors | Device design and target resources | Hazard exists | No actuator test performed; propellers must be removed |
| Boot/update/debug | Bootloader/SWD pads | Host/probe ↔ MCU | Generic target capability only | No exact-unit path characterized here | Reset, bootloader, readout, and flash actions are separately gated |
