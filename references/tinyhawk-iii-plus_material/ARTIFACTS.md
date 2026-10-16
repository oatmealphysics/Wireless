# TinyHawk III Plus artifact manifest

| Tracked artifact | SHA-256 | Match class | Limit |
|---|---|---|---|
| `firmware/betaflight/betaflight_4.4.2_STM32F411.hex` | `bed2ed630acf8301b966ed6ac9fadea53ad0337535856ff8d05e45f852ea9729` | Official release; version/family matched | Not proven byte-identical to installed flash or customized target state |
| `firmware/betaflight/betaflight_4.4.2_STM32F411.bin` | `33830bda10262ebc2b3a8c337de611a6f5c9f4b3560bf1b0997f1d1081ffbf3d` | Derived analysis binary | Converted from the HEX; same match limitation |
| `firmware/src/betaflight-4.4.2.tar.gz` | `195b0c6eb66741f6be64f30c63e6a41cc0cf2e990505508f1f7cc270de477c29` | Version-matched source, revision `23d066d08` | Source equivalence does not prove compiled options or installed bytes |
| `firmware/betaflight/EMAX_TINYHAWKF4SX1280.config` | `7b21b072fede3e23fdc43b4d4722d47276f789f6517573e956ec228b1f6bea94` | EMAX target configuration | Header originated from an earlier target/config version; use as pin/resource evidence with that qualifier |
| Private live CLI transcript | Not published | Exact-unit observation | Contains pairing-sensitive configuration; ignored under `work/` |
| Exact installed flash image | — | **Not obtained** | Do not describe the official release artifact as a dump |

Canonical URLs, retrieval metadata, transform commands, and further corpus
artifacts are in `firmware/artifact-register.json`, `firmware/SHA256SUMS`, and
`firmware/README.md`.
