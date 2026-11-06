# Reverse Engineering

Comprehensive methodology for reverse engineering binaries, firmware, protocols, and obfuscated code. Complements the REA tool (morluto/rea) when installed.

## Phase 0: Target Identification

1. Run `file` to identify type, architecture, endianness
2. Check for known formats: ELF, PE, Mach-O, DEX, .NET assembly, Java class, firmware blob
3. Run `strings` (both ASCII and Unicode) — look for:
   - Version strings, build info, compiler signatures
   - Hardcoded URLs, IPs, paths, credentials
   - Error messages (map back to functions later)
   - Format strings (potential vulns)
   - Library names and versions
4. Check entropy with `binwalk -E` — high entropy regions suggest encryption or compression
5. Run `checksec` on ELF binaries for security properties

## Phase 1: Static Analysis

### ELF / PE Binaries
1. Load in Ghidra or radare2 (`r2 -A binary`)
2. Identify the entry point and follow to `main`
3. Map the call graph — which functions does main call?
4. Identify library functions (imports) — what capabilities does the binary have?
   - Network: socket, connect, send, recv
   - File: open, read, write, unlink
   - Process: fork, exec, system, popen
   - Memory: malloc, free, mmap
   - Crypto: SSL_*, EVP_*, AES_*, custom crypto
5. Rename functions as you understand them — `sub_401230` → `parse_user_input`
6. Annotate data structures — identify structs, vtables, global state
7. Look for cross-references to interesting strings

### Firmware
1. `binwalk -e` to extract filesystem and components
2. Identify bootloader, kernel, rootfs
3. Look for configuration files, default credentials, private keys
4. Check for update mechanisms and signature verification
5. Identify custom protocols on exposed interfaces

### .NET / Java / Managed Code
1. Use dnSpy/.NET Reflector for .NET, JD-GUI/CFR/Procyon for Java
2. Decompilation is nearly perfect — read it like source
3. Look for obfuscation: string encryption, control flow flattening, anti-tamper
4. For obfuscated .NET: de4dot first, then decompile
5. Check for reflection, dynamic loading, P/Invoke to native code

### JavaScript / Electron
1. Unpack: for Electron apps, extract the `app.asar`
2. Deobfuscate: for webpack, find the source map or use webcrack
3. For minified code: prettify, then trace the interesting logic
4. Look for hardcoded API keys, tokens, debug endpoints

## Phase 2: Dynamic Analysis

1. **Sandbox first** — run unknown binaries in a VM or container
2. **System call tracing**:
   - Linux: `strace -f -e trace=network,file,process`
   - Windows: Process Monitor, API Monitor
3. **Debugging**:
   - GDB + pwndbg/GEF for Linux ELF
   - x64dbg / WinDbg for Windows PE
   - Set breakpoints at: main, input parsing, crypto operations, comparison functions
4. **Network monitoring**: capture traffic with Wireshark while exercising the binary
5. **Code coverage**: run with different inputs to discover code paths
6. **Anti-debug bypass** (if present):
   - Linux: `ptrace` check bypass, `/proc/self/status` TracerPid
   - Windows: `IsDebuggerPresent`, `NtQueryInformationProcess`, timing checks
   - Use LD_PRELOAD or patching to neutralize checks

## Phase 3: Protocol Reverse Engineering

1. Capture traffic between client and server
2. Identify message boundaries (length-prefixed, delimited, fixed-size)
3. Replay traffic — does the server accept it? What changes between sessions?
4. Identify fields: magic bytes, version, message type, length, payload, checksum
5. Modify fields one at a time to understand their role
6. Build a protocol dissector or parser
7. Look for: authentication weaknesses, lack of encryption, replay vulnerabilities

## Phase 4: Defeating Obfuscation

### Common techniques and counters:
- **String encryption**: Find the decryption function, set a breakpoint after decryption, dump
- **Control flow flattening**: Identify the dispatcher variable, trace state transitions
- **Dead code insertion**: Ignore unreachable blocks, focus on actual execution path
- **Opaque predicates**: Evaluate the condition — if always true/false, simplify
- **VM-based protection**: Identify the bytecode, VM dispatcher, and handler table; write a disassembler for the custom VM
- **Packing**: Identify the packer (UPX, Themida, VMProtect), unpack or dump from memory at OEP

### Useful tools:
- `angr` for symbolic execution through obfuscated logic
- `z3` for solving constraints (license key checks, flag validators)
- `Triton` for dynamic symbolic execution
- `Miasm` for symbolic execution and IR manipulation
- `Frida` for runtime hooking and instrumentation

## Phase 5: Documentation

Record findings as you go:
- Function map with renamed symbols and annotations
- Data structure layouts with field types and sizes
- Protocol specification if applicable
- Vulnerability notes with offsets and proof
- Anti-analysis techniques encountered and bypasses used

## Reminders

- Rename functions and variables as you understand them — future you will thank you
- Cross-reference strings to find the code that uses them
- Error handlers often reveal the expected (valid) code path
- Comparison functions (strcmp, memcmp, hash checks) are high-value targets
- If a binary phones home, the server interaction reveals a lot about the protocol
- For CTF rev challenges: the flag check function is your target — trace backward from the success/failure output
