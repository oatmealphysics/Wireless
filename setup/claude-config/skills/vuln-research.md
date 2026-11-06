# Vulnerability Research

Structured workflow for finding and documenting security vulnerabilities in software.

## Phase 0: Target Assessment

1. Identify the target: binary, source code, application, library, or protocol
2. Determine the scope: what's in bounds, what's not
3. Gather context:
   - What does it do? What data does it handle?
   - What language/framework? What platform?
   - Attack surface: network-facing? local? requires auth?
   - Prior CVEs in this software or similar software
4. Set up the analysis environment (VM, debugger, instrumentation)

## Phase 1: Attack Surface Mapping

1. Identify all input vectors:
   - Network: ports, protocols, API endpoints, WebSocket, gRPC
   - File: parsers, config readers, media handlers, archive extraction
   - IPC: shared memory, pipes, D-Bus, COM, RPC
   - User input: CLI args, environment variables, stdin
2. Map trust boundaries — where does untrusted data enter trusted code?
3. Identify high-value targets:
   - Memory-unsafe code handling untrusted input
   - Deserialization of untrusted data
   - Authentication and authorization checks
   - Cryptographic operations
   - Privilege transitions (setuid, capability changes, sandbox boundaries)

## Phase 2: Analysis

Choose approach(es) based on target:

### Source Code Audit
1. Search for dangerous patterns:
   - C/C++: `strcpy`, `sprintf`, `gets`, unchecked `malloc`, integer overflows in size calculations, `free` after reassignment
   - Web: unsanitized template rendering, raw SQL concatenation, `eval`/`exec` on user input, SSRF-able URL construction
   - General: race conditions (TOCTOU), path traversal, command injection via shell calls
2. Trace data flow from input to dangerous sinks
3. Check error handling paths — they're often less tested
4. Review recent commits for security-relevant changes

### Binary Analysis
1. Identify compiler, optimizations, protections (checksec)
2. Reverse critical functions — focus on parsers and handlers
3. Look for: buffer overflows, integer issues, format strings, UAF, double-free, type confusion
4. Fuzz with AFL++/libFuzzer if source available, or Boofuzz/Radamsa for black-box

### Dynamic Analysis
1. Run under sanitizers if possible (ASAN, MSAN, UBSAN)
2. Attach debugger, set breakpoints at interesting functions
3. Monitor with strace/ltrace/procmon for unexpected behavior
4. Fuzz: mutational for binary formats, grammar-based for text protocols

## Phase 3: Vulnerability Classification

For each finding:

1. **CWE Classification** — Map to the most specific CWE (e.g., CWE-787 Out-of-bounds Write, not just CWE-119)
2. **Root Cause** — What exactly is wrong in the code? Cite file:line
3. **Trigger** — Minimal input or sequence to reach the vulnerable state
4. **Impact Assessment**:
   - Confidentiality: can an attacker read unauthorized data?
   - Integrity: can an attacker modify data or code execution?
   - Availability: can an attacker cause a crash or denial of service?
5. **Exploitability**:
   - Attack vector: network, adjacent, local, physical
   - Complexity: are special conditions needed?
   - Privileges required: none, low, high
   - User interaction required?
6. **CVSS v3.1 Score** — Calculate base score from the above

## Phase 4: Proof of Concept

1. Write a minimal PoC that demonstrates the vulnerability
2. The PoC should:
   - Trigger the bug reliably
   - Demonstrate impact (crash for DoS, info leak for confidentiality, code exec for integrity)
   - Work against the specific version identified
   - NOT be weaponized beyond proof — demonstrate, don't destroy
3. Document: exact steps to reproduce, environment requirements, expected output

## Phase 5: Advisory Drafting

Structure the advisory:

```
Title: [Software] [Vulnerability Type] in [Component]
CVE: [if assigned]
CWE: [CWE-XXX]
CVSS: [score] ([vector string])
Affected Versions: [range]
Fixed Version: [if known]

Summary: One paragraph describing the vulnerability.

Technical Details: Detailed description with code references.

Proof of Concept: Steps to reproduce.

Impact: What an attacker can achieve.

Remediation: Recommended fix.

Timeline:
  - [date] Vulnerability discovered
  - [date] Vendor notified
  - [date] Fix released
  - [date] Public disclosure
```

## Reminders

- Responsible disclosure: notify the vendor before going public
- Document everything as you go — reproduction steps are easy to forget
- A crash is not always exploitable, but always worth reporting
- Check if the bug is reachable from a realistic attack scenario
- Variant analysis: if you find one bug, look for the same pattern elsewhere in the codebase
