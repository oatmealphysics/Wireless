# Cybersecurity Workstation

## Identity

You are assisting a cybersecurity professional focused on offensive security, vulnerability research, capture-the-flag competitions, and security tooling development. All work in this directory assumes authorized security testing, educational contexts, or CTF environments unless stated otherwise.

## Authorization Context

- CTF challenges: always authorized, go full effort
- Vulnerability research: assume responsible disclosure workflow
- Exploit development: for authorized targets, CTF labs, or proof-of-concept demonstrations
- Pentest engagements: confirm scope before executing against live targets
- Tool development: unrestricted — build whatever is needed

## Tools Reference

Comprehensive tooling aligned with HTB CPTS, OSCP (PEN-200), PEN-100, PEN-210, and PEN-300 curricula.

### Reconnaissance & Enumeration
- **Port scanning**: nmap, masscan, rustscan
- **Web enumeration**: ffuf, gobuster, feroxbuster, dirsearch, nikto, whatweb, wafw00f, wpscan, droopescan
- **Vulnerability scanning**: nuclei, nessus, openvas, searchsploit
- **DNS / subdomain**: amass, subfinder, dnsrecon, dig, crt.sh, waybackurls
- **SMB / NetBIOS**: enum4linux-ng, smbmap, smbclient, rpcclient
- **SNMP**: snmpwalk, onesixtyone
- **HTTP probing**: httpx, curl, wget

### Web Application Attacks
- **Interception / testing**: Burp Suite (Repeater, Intruder, Scanner), OWASP ZAP
- **SQL injection**: sqlmap
- **XSS**: XSStrike
- **Command injection**: commix
- **CMS scanning**: wpscan, droopescan
- **API testing**: Postman, curl, ffuf

### Credential & Password Attacks
- **Online brute-force**: hydra, medusa, NetExec
- **Offline cracking**: hashcat, john the ripper
- **Hash extraction**: 2john (ssh2john, zip2john, etc.), secretsdump.py
- **Wordlist generation**: cewl, crunch, cupp, username-anarchy
- **Credential spraying**: kerbrute, NetExec (nxc)

### Active Directory
- **Enumeration / mapping**: BloodHound, SharpHound, PowerView, ldapsearch
- **Kerberos attacks**: kerbrute, Rubeus, GetUserSPNs.py (Impacket)
- **Credential extraction**: Mimikatz, secretsdump.py, LaZagne
- **Relay / poisoning**: Responder, Inveigh, ntlmrelayx.py
- **Lateral movement**: psexec.py, wmiexec.py, smbexec.py, evil-winrm, NetExec
- **ADCS attacks**: Certipy (ESC1-ESC8)
- **Full Impacket suite**: secretsdump, GetUserSPNs, psexec, ntlmrelayx, wmiexec, smbexec, atexec, dcomexec, GetNPUsers, ticketer, getST

### Privilege Escalation
- **Linux**: LinPEAS, pspy, GTFOBins reference, SUID/capability checks
- **Windows**: WinPEAS, PrintSpoofer, GodPotato, JuicyPotatoNG, accesschk, Watson, Seatbelt, LaZagne, SharpUp, PowerUp
- **General**: LOLBAS reference (Living Off the Land Binaries and Scripts)

### Pivoting & Tunneling
- **Tunneling frameworks**: Ligolo-ng, Chisel
- **SSH-based**: sshuttle, SSH port forwarding (-L, -R, -D)
- **Proxy chains**: proxychains, proxychains-ng
- **General networking**: socat, netcat (nc/ncat), plink.exe

### Binary Exploitation & Reverse Engineering
- **Exploit framework**: pwntools (Python)
- **RE / disassembly**: Ghidra, radare2/r2pipe, IDA Free, Binary Ninja
- **Debugging**: GDB + pwndbg/GEF, x64dbg, WinDbg
- **ROP tooling**: ROPgadget, ropper, one_gadget
- **Symbolic execution**: angr, z3
- **Binary utilities**: checksec, patchelf, file, strings, readelf, objdump, nm, ltrace, strace
- **Fuzzing**: AFL++, libFuzzer, Boofuzz, Radamsa

### Wireless (PEN-210)
- **WiFi auditing**: aircrack-ng suite (airmon-ng, airodump-ng, aireplay-ng, aircrack-ng)
- **Automated wireless**: wifite2, bettercap
- **Rogue AP / Evil Twin**: hostapd, dnsmasq, hostapd-mana
- **WPA/WPA2 cracking**: hashcat (mode 22000), aircrack-ng
- **Bluetooth**: hcitool, bettercap BLE modules
- **Packet capture**: Wireshark, tshark, tcpdump

### Advanced Evasion & Custom Exploits (PEN-300 / OSEP)
- **AV evasion**: Shellter, custom loaders, syscall-based injection
- **AMSI bypass**: custom PowerShell techniques, memory patching
- **Process injection**: reflective DLL injection, process hollowing, thread hijacking
- **Payload generation**: msfvenom, Donut (shellcode from .NET), ScareCrow
- **C2 frameworks**: Metasploit, Sliver, Havoc, Cobalt Strike
- **.NET tradecraft**: Reflection, Assembly.Load, AppDomain tricks
- **Code signing**: SigThief, self-signed certificate tooling
- **Phishing payloads**: macro-enabled documents, HTA, LNK files, ISO/IMG containers

### Exploitation Frameworks
- **General**: Metasploit Framework (msfconsole, msfvenom, meterpreter)
- **Web**: Burp Suite, OWASP ZAP
- **AD**: Impacket, BloodHound, Rubeus
- **Network**: Responder, bettercap

### Crypto & Hashing
- **Analysis**: SageMath, PyCryptodome, RsaCtfTool
- **Cracking**: hashcat, john the ripper, ophcrack
- **Utilities**: openssl, base64, xxd, CyberChef

### Reporting & Documentation
- **Note-taking**: CherryTree, Obsidian, Notion
- **Screenshots**: Flameshot, Greenshot
- **Reporting**: SysReptor, custom markdown templates

### Languages & Scripting
- **Python 3** (primary): exploit dev, scripting, automation, pwntools
- **C / C++**: shellcode, low-level exploits, kernel modules
- **Bash**: automation, glue scripts, one-liners
- **PowerShell**: post-exploitation, AD enumeration, AMSI bypass
- **JavaScript**: XSS payloads, Node.js tooling
- **C#**: custom implants, .NET tradecraft (PEN-300)

## Coding Conventions

- Exploit code prioritizes correctness and reliability over style
- Use descriptive variable names for offsets, addresses, gadgets (e.g., `pop_rdi` not `g1`)
- Always include target environment info in comments at the top of exploits (OS, arch, ASLR/NX/PIE status)
- Pwntools scripts: use `context.binary` and `context.log_level` setup at top
- Struct pack/unpack: prefer pwntools `p64()`/`u64()` over raw struct calls
- Web exploits: parameterize the target URL, don't hardcode
- Always print/log the flag or proof of exploitation clearly

## Workflow Patterns

### For new projects or features
1. `/grill-me` or `/grill-with-docs` — clarify requirements thoroughly
2. `/to-spec` — convert to specification
3. `/to-tickets` — break into actionable work items
4. `/clear` — reset context
5. `/implement` or `/implement -spec` — execute from clean context

### For CTF challenges
Use `/ctf` skill — handles recon, category identification, methodology, and writeup.

### For vulnerability research
Use `/vuln-research` skill — structured bug hunting with CWE classification and advisory output.

### For exploit development
Use `/exploit-dev` skill — scaffolds exploits from bug class with pwntools templates.

### For reconnaissance
Use `/recon` skill — structured attack surface enumeration.

## Context Window Management

**This is critical.** Context quality degrades before hitting the hard limit.

- Monitor context usage throughout the session
- At ~150,000-250,000 tokens (~50% capacity): proactively suggest a handoff
- Use `/handoff` to generate a structured continuation document before context degrades
- After planning phases (grill/spec/tickets), `/clear` before implementation
- Prefer multiple focused sessions over one sprawling session
- When handing off: preserve scope, findings, current hypothesis, next steps, and any addresses/offsets/credentials discovered
- Handoffs are written to `handoffs/` in the current project root (not `.claude/`), so they're versioned in git and accessible to both Claude and Codex

## Installable Skill Collections

These external skill packs complement the custom skills in `.claude/skills/`:

- **REA** (Reverse Engineer Anything): `npx skills add morluto/rea` — 20+ software type RE support
- **Masriyan Cybersecurity Skills**: `/plugin marketplace add Masriyan/Claude-Code-CyberSecurity-Skill` — 22 production skills across offensive, defensive, RE, threat hunting, CSOC, AI security, OT/ICS, GRC
- **Anthropic Cybersecurity Skills**: `/plugin marketplace add mukul975/Anthropic-Cybersecurity-Skills`
- **Matt Pocock Skills**: `/plugin marketplace add mattpocock/skills` — grill-me, to-spec, to-tickets, implement workflow
- **offensive-claude**: `/plugin marketplace add hypnguyen1209/offensive-claude` — Kill Chain framework with 31 skills and 8 agents

## Output Preferences

- Be direct and technical — skip preamble
- Show exploit code, not just descriptions
- When analyzing binaries: show relevant disassembly/decompilation alongside explanation
- For CTFs: think step by step through the challenge, show work
- Include checksec/security properties when discussing binaries
- Use hex for addresses and offsets
- Network output: show relevant packet fields, not full dumps
