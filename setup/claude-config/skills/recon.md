# Reconnaissance

Structured workflow for enumerating attack surface. For authorized engagements and CTF environments only.

## Phase 0: Scope Definition

1. Confirm authorized scope:
   - In-scope IPs, domains, CIDR ranges
   - In-scope applications and URLs
   - Out-of-scope systems (production databases, third-party services, etc.)
   - Rules of engagement: scanning rate limits, time windows, no-DoS
2. Document scope in a `scope.txt` at the engagement root
3. Set up a logging/notes directory structure:
   ```
   engagement/
   ├── scope.txt
   ├── notes/
   ├── scans/
   ├── loot/
   └── exploits/
   ```

## Phase 1: Passive Reconnaissance

Gather without touching the target:

1. **DNS enumeration**:
   - Subdomain discovery: `subfinder -d target.com`, `amass enum -passive -d target.com`
   - DNS records: A, AAAA, MX, TXT, NS, CNAME, SOA
   - Zone transfer attempt: `dig axfr @ns.target.com target.com`
   - Reverse DNS on known IPs
2. **OSINT**:
   - WHOIS on domains and IPs
   - Certificate transparency logs: crt.sh, Censys
   - Shodan/Censys for exposed services
   - Google dorks: `site:target.com`, `inurl:`, `filetype:`, `intitle:`
   - GitHub/GitLab: search for target-related repos, leaked credentials, config files
   - Wayback Machine: historical pages, old endpoints, removed content
3. **Technology fingerprinting** (passive):
   - Wappalyzer/BuiltWith for web stack
   - HTTP headers from cached responses
   - Job postings mentioning tech stack

## Phase 2: Active Reconnaissance

Interact with the target (authorized only):

### Network
1. **Host discovery**: `nmap -sn <CIDR>` — find live hosts
2. **Port scanning**:
   - Quick: `nmap -sV -sC -O -T4 <target>` — top 1000 ports with version detection
   - Full TCP: `nmap -p- -sV <target>` — all 65535 ports
   - UDP top: `nmap -sU --top-ports 100 <target>`
3. **Service enumeration** — per service:
   - HTTP/HTTPS (80, 443, 8080, 8443): see Web section below
   - SMB (445): `enum4linux-ng`, `smbclient -L`, `crackmapexec smb`
   - SSH (22): banner grab, auth methods
   - FTP (21): anonymous login, version vulns
   - DNS (53): zone transfer, version query
   - SNMP (161): `snmpwalk` with default communities
   - LDAP (389/636): `ldapsearch` for AD enumeration
   - RDP (3389): NLA check, BlueKeep if old
   - WinRM (5985/5986): test with `evil-winrm` if creds obtained
   - MySQL (3306), MSSQL (1433), PostgreSQL (5432): default creds, version

### Web Application
1. **Directory/file discovery**: `ffuf -u https://target.com/FUZZ -w /path/to/wordlist`
2. **Endpoint mapping**: spider with Burp, review JS files for API routes
3. **Technology identification**: response headers, error pages, cookies
4. **Virtual host enumeration**: `ffuf -H "Host: FUZZ.target.com" -u https://IP/`
5. **API discovery**: check `/api/`, `/swagger`, `/graphql`, `/.well-known/`
6. **Authentication**: identify auth mechanism, test for defaults, enumerate users

### Active Directory (if in scope)
1. **User enumeration**: Kerberos pre-auth (`kerbrute`), RPC, LDAP
2. **BloodHound collection**: `bloodhound-python` or SharpHound
3. **Share enumeration**: `crackmapexec smb <targets> --shares`
4. **Password policy**: `crackmapexec smb <dc> --pass-pol`

## Phase 3: Consolidation

1. Merge all findings into structured notes:
   - Per-host: IP, hostname, OS, open ports, services, versions, notes
   - Per-web-app: URL, tech stack, interesting endpoints, parameters
   - Credentials found (hashes, defaults, leaked)
   - Potential vulnerabilities mapped to services
2. Prioritize targets:
   - Known vulnerable versions → immediate investigation
   - Default/weak credentials → quick wins
   - Large attack surface (many parameters, file uploads) → deeper testing
   - Internal-only services exposed externally → misconfig
3. Create a target list for the next phase (exploitation or deeper testing)

## Output Format

Produce a recon summary:

```
# Recon Summary: [Engagement Name]
## Scope: [brief]
## Hosts Discovered: [count]

### [Host IP/Hostname]
- OS: [detected]
- Ports: [port/proto - service - version]
- Notes: [interesting findings]
- Priority: [high/medium/low] — [reason]

### Web Applications
- [URL] — [tech stack] — [interesting findings]

### Credentials / Loot
- [any found]

### Recommended Next Steps
1. [prioritized action items]
```

## Reminders

- Stay in scope — recheck before scanning a new target
- Rate-limit scans on production systems
- Save all raw scan output to `scans/` directory
- Screenshot interesting web pages
- Check for low-hanging fruit first: default creds, known CVEs, exposed admin panels
