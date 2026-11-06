# CTF Challenge Solver

Structured workflow for solving capture-the-flag challenges. Follow this methodology for any CTF challenge.

## Phase 0: Recon

1. Read the challenge name, description, category tags, point value, and any hints
2. Download and inventory all provided files
3. Run initial triage:
   - Binary files: `file`, `checksec`, `strings | head -50`
   - Web: visit the URL, view source, check robots.txt, note tech stack
   - Crypto: identify the algorithm or scheme from context/code
   - Forensics: `file`, `binwalk`, `exiftool`, check for steganography indicators
   - Misc/OSINT: identify what's being asked, note all given information

## Phase 1: Category Identification & Approach

Classify and select methodology:

### Pwn (Binary Exploitation)
1. `checksec` — note NX, PIE, canary, RELRO, ASLR
2. Reverse the binary — identify main vulnerability (BOF, format string, UAF, heap overflow, race condition)
3. Map out protections vs. available primitives
4. Plan exploit chain: leak → control → execute
5. Build pwntools script iteratively, testing each stage

### Rev (Reverse Engineering)
1. Static analysis first — decompile with Ghidra, read main logic
2. Identify the check/validation function
3. Determine approach: constraint solving (z3/angr), manual reversing, or dynamic (gdb breakpoints)
4. For obfuscated code: identify the obfuscation type, work backward from the comparison
5. Extract flag or generate valid input

### Web
1. Map the application: endpoints, forms, cookies, headers, JS files
2. Identify tech stack (server, framework, template engine, DB)
3. Test OWASP Top 10 in priority order for CTF: injection (SQLi, SSTI, command), auth bypass, IDOR, SSRF, XSS, deserialization
4. Check for source code disclosure, git repos, backup files
5. Craft payload, confirm exploitation, extract flag

### Crypto
1. Identify the cryptosystem and any custom modifications
2. Look for classic weaknesses: small exponent RSA, ECB mode, bad IV reuse, weak PRNG, padding oracle
3. Check if it's a known scheme with a known attack
4. Implement the attack in Python (PyCryptodome/SageMath)
5. Decrypt or forge to get flag

### Forensics
1. Identify file type(s) and any embedded files (`binwalk -e`)
2. Check metadata (`exiftool`), strings, hex dumps
3. For disk images: mount and browse filesystem
4. For memory dumps: use Volatility, identify OS profile first
5. For network captures: filter by protocol, follow TCP streams, extract files
6. For steganography: try `steghide`, `zsteg`, LSB analysis, spectrograms for audio

### Misc / OSINT
1. Read carefully — the answer is often in the framing
2. For programming challenges: parse the problem, code the solution, handle edge cases
3. For OSINT: use search engines, Wayback Machine, social media, WHOIS, DNS records
4. For jail/sandbox escapes: identify restrictions, find bypass primitives

## Phase 2: Execution

- Work iteratively — test each step before building on it
- Print debug output at every stage
- When stuck for >10 minutes on one approach, step back and reassess
- Check if there's a simpler interpretation of the challenge
- For multi-stage challenges: document each stage's output as you go

## Phase 3: Flag & Writeup

1. Confirm the flag format matches the expected pattern (e.g., `CTF{...}`, `flag{...}`)
2. Document the solution:
   - Challenge name and category
   - Key vulnerability or trick
   - Solution steps
   - Working exploit/script
   - Flag
3. Save the writeup alongside the exploit code

## Reminders

- CTFs reward creativity — if the obvious approach doesn't work, think laterally
- Point value hints at difficulty: low points = straightforward, high points = multi-step or obscure
- Check challenge updates/errata during the competition
- When a challenge gives source code, READ IT ALL before trying anything
- Timestamps matter in forensics — note timezones
