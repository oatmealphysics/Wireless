# EMAX E8 artifact manifest

| Artifact | SHA-256 | Class / match | Handling and limit |
|---|---|---|---|
| `EMAX E8 ELRS Configurator.exe` | `7ec41368c1d7fe9cbf7e25ba053b05519f7e1aa8542c698e70764162be99362d` | Manufacturer-linked host tool; product-related | Private/ignored; statically inspected; unsigned; not device firmware |
| FCC internal-photos exhibit 5536597 | `6a0791680a096019dc749bde18cdde7478d5c26e96a4e04965476ea539cbe566` | Public EMAXE8 product-family reference | Temporary/local copy; board layout visible, chip marks unreadable; not exact-unit proof |
| FCC test report exhibit 5536602 | `c75f307e436f024fc54f4d3acb6c4046a8e63327b1a1bf850e74d56870d34e58` | Public EMAXE8 product-family reference | Temporary/local copy; reports GFSK/2-FSK and internal antenna; no parts list |
| Live 17-byte Read Configuration response | Not published | Exact-unit private observation | Ignored `work/e8-session/`; contains pairing-sensitive UID; never commit raw bytes or digest |
| Exact E8 device firmware | — | **Not obtained** | Do not substitute the host tool, a related EMAX module build, or generic ExpressLRS source |

Public sources:

- [FCC EMAXE8 filing](https://fccid.io/2AL7Y-EMAXE8)
- [FCC internal photographs](https://fccid.io/2AL7Y-EMAXE8/Internal-Photos/Internal-Photos-5536597)
- [FCC user manual](https://fccid.io/2AL7Y-EMAXE8/User-Manual/User-Manual-5536603)

Every future firmware candidate must record its direct source, retrieval time,
hash, declared model/revision, container type, signing state, and why it matches
this exact unit before analysis claims are transferred to the controller.
