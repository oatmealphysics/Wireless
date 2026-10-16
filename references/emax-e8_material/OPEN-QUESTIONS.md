# EMAX E8 open questions

| Priority | Question | Why it matters | Smallest safe next step | Current gate |
|---|---|---|---|---|
| P0 | What exact model/variant, connector, regulatory label, and exterior revision does this unit carry? | Determines whether public E8 exhibits apply to this article | Photograph all exterior faces, connector, label, packaging, and battery markings; redact serials | L1 observation |
| P0 | What are the PCB revision, MCU, RF IC, flash, and oscillator markings? | Closes the hardware identity and selects correct datasheets/architecture | Close, well-lit exact-unit PCB photographs after a separately approved enclosure-opening plan | Opening gate required |
| P0 | What installed firmware name/version/build does the controller report? | Needed to bind source and findings to the unit | Ask EMAX for a documented read-only identity query or support record; no known field exists in the current 17-byte response | L0/L2 only |
| P0 | Is there an exact, vendor-attributable E8 firmware package for this revision? | Enables offline binary analysis without touching the only controller | Vendor support request; hash and classify any response before execution or unpacking | L0 acquisition |
| P1 | Which component serves the CDC read/update protocol? | Separates ROM/loader/application architecture | Resolve from exact firmware or attributable schematic/source; do not infer from a mode name | Offline first |
| P1 | What do the remaining config indexes mean? | Prevents bad UI/protocol assumptions | Locate labels in the statically unpacked host resource/code or vendor documentation | L0 offline; no write-to-learn experiment |
| P2 | Are SWD/JTAG/UART pads present and enabled? | Supports architecture mapping but is not needed for normal reads | Photograph pads first; electrical probing requires a separate hardware-debug plan | Opening/probing gates required |

The E8 can be called fully identified only after the P0 rows are resolved or
explicitly accepted as unavailable with a documented reason. The permanent
no-flash rule remains in force regardless of identification progress.
