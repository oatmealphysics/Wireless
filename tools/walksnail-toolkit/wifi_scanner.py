"""
Walksnail Avatar WiFi Discovery & Enumeration

Discovers Walksnail Avatar devices on WiFi networks:
- Passive scan for known Avatar/Walksnail SSIDs
- Default credential testing (WPA2: 12345678)
- Post-connect service enumeration
- Device fingerprinting

Usage:
    # Scan for Walksnail devices (requires wireless interface):
    python3 wifi_scanner.py --scan --interface wlan0

    # Enumerate services on a connected Walksnail device:
    python3 wifi_scanner.py --enumerate --target 192.168.42.1

    # Full discovery (scan + connect + enumerate):
    python3 wifi_scanner.py --full --interface wlan0

For authorized security research only.
"""

import argparse
import subprocess
import socket
import sys
import json
import re
import time


# Known Walksnail Avatar WiFi patterns
KNOWN_SSID_PATTERNS = [
    r'Avatar[-_]?[A-Fa-f0-9]+',
    r'Walksnail[-_]?[A-Fa-f0-9]+',
    r'Caddx[-_]?[A-Fa-f0-9]+',
    r'WS[-_]?VTX[-_]?[A-Fa-f0-9]+',
    r'WS[-_]?VRX[-_]?[A-Fa-f0-9]+',
    r'Avatar[-_]?HD',
]

# Default WiFi credentials
DEFAULT_CREDENTIALS = [
    '12345678',       # Primary default
    '1234567890',     # Sometimes seen
    '00000000',       # Alternate default
]

# Known service ports on Walksnail devices
WALKSNAIL_PORTS = {
    22:    {'service': 'SSH (dropbear)', 'risk': 'HIGH', 'notes': 'Root shell after exploit'},
    80:    {'service': 'HTTP', 'risk': 'HIGH', 'notes': 'Firmware update interface'},
    443:   {'service': 'HTTPS', 'risk': 'MEDIUM', 'notes': 'Possible management'},
    5555:  {'service': 'ADB', 'risk': 'CRITICAL', 'notes': 'Android Debug Bridge (if present)'},
    8080:  {'service': 'HTTP Alt', 'risk': 'MEDIUM', 'notes': 'Possible web interface'},
    8554:  {'service': 'RTSP', 'risk': 'LOW', 'notes': 'Video stream'},
    554:   {'service': 'RTSP', 'risk': 'LOW', 'notes': 'Video stream'},
    23:    {'service': 'Telnet', 'risk': 'CRITICAL', 'notes': 'Unencrypted shell'},
    2222:  {'service': 'SSH Alt', 'risk': 'HIGH', 'notes': 'Alternate SSH port'},
    9100:  {'service': 'Raw TCP', 'risk': 'MEDIUM', 'notes': 'Debug/data port'},
    67:    {'service': 'DHCP', 'risk': 'LOW', 'notes': 'DHCP server'},
    53:    {'service': 'DNS', 'risk': 'LOW', 'notes': 'DNS server'},
    69:    {'service': 'TFTP', 'risk': 'HIGH', 'notes': 'File transfer (firmware)'},
    161:   {'service': 'SNMP', 'risk': 'MEDIUM', 'notes': 'Device management'},
}

# HTTP endpoints to probe on Walksnail devices
HTTP_PROBES = [
    '/',
    '/index.html',
    '/firmware',
    '/upload',
    '/update',
    '/config',
    '/api/info',
    '/api/version',
    '/api/status',
    '/cgi-bin/',
    '/debug',
    '/shell',
    '/console',
]


def scan_wifi(interface='wlan0'):
    """Scan for WiFi networks and identify Walksnail devices."""
    print(f"[*] Scanning WiFi on {interface}...")

    try:
        result = subprocess.run(
            ['iwlist', interface, 'scan'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            # Try nmcli as fallback
            result = subprocess.run(
                ['nmcli', '-t', '-f', 'SSID,BSSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list'],
                capture_output=True, text=True, timeout=30
            )
    except FileNotFoundError:
        print("[-] Neither iwlist nor nmcli found. Install wireless-tools or NetworkManager.")
        return []
    except subprocess.TimeoutExpired:
        print("[-] WiFi scan timed out")
        return []

    networks = parse_scan_results(result.stdout)

    # Filter for Walksnail devices
    walksnail_devices = []
    for net in networks:
        for pattern in KNOWN_SSID_PATTERNS:
            if re.match(pattern, net.get('ssid', ''), re.IGNORECASE):
                net['identified_as'] = 'Walksnail Avatar'
                walksnail_devices.append(net)
                break

    return walksnail_devices


def parse_scan_results(output):
    """Parse WiFi scan output (iwlist or nmcli format)."""
    networks = []

    # Try nmcli format first (SSID:BSSID:SIGNAL:SECURITY)
    for line in output.strip().split('\n'):
        parts = line.split(':')
        if len(parts) >= 4:
            networks.append({
                'ssid': parts[0],
                'bssid': ':'.join(parts[1:7]) if len(parts) > 7 else parts[1],
                'signal': parts[-2] if len(parts) > 2 else 'unknown',
                'security': parts[-1] if len(parts) > 3 else 'unknown',
            })

    if networks:
        return networks

    # Try iwlist format
    current = {}
    for line in output.split('\n'):
        line = line.strip()
        if line.startswith('Cell '):
            if current:
                networks.append(current)
            current = {}
            match = re.search(r'Address: ([\w:]+)', line)
            if match:
                current['bssid'] = match.group(1)
        elif 'ESSID:' in line:
            match = re.search(r'ESSID:"(.+?)"', line)
            if match:
                current['ssid'] = match.group(1)
        elif 'Signal level' in line:
            match = re.search(r'Signal level[=:](-?\d+)', line)
            if match:
                current['signal'] = match.group(1)
        elif 'Encryption key:' in line:
            current['security'] = 'encrypted' if 'on' in line else 'open'

    if current:
        networks.append(current)

    return networks


def port_scan(target, ports=None, timeout=2):
    """Scan target for open TCP ports."""
    if ports is None:
        ports = sorted(WALKSNAIL_PORTS.keys())

    print(f"\n[*] Port scanning {target}...")
    open_ports = []

    for port in ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((target, port))
            if result == 0:
                info = WALKSNAIL_PORTS.get(port, {'service': 'unknown', 'risk': '?', 'notes': ''})
                open_ports.append({
                    'port': port,
                    'service': info['service'],
                    'risk': info['risk'],
                    'notes': info['notes'],
                })
                print(f"  [+] {port:5d}/tcp  OPEN  {info['service']:20s} [{info['risk']}] {info['notes']}")
            sock.close()
        except (socket.error, OSError):
            pass

    if not open_ports:
        print("  [-] No open ports found")

    return open_ports


def grab_banner(target, port, timeout=3):
    """Attempt to grab a service banner."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((target, port))

        # Send a minimal probe based on service type
        if port in (80, 8080):
            sock.send(b'GET / HTTP/1.0\r\nHost: ' + target.encode() + b'\r\n\r\n')
        elif port == 22:
            pass  # SSH sends banner first
        else:
            sock.send(b'\r\n')

        banner = sock.recv(1024)
        sock.close()
        return banner
    except (socket.error, OSError):
        return None


def http_probe(target, port=80, timeout=5):
    """Probe HTTP endpoints on the target."""
    print(f"\n[*] Probing HTTP endpoints on {target}:{port}...")

    try:
        import urllib.request
        import urllib.error
    except ImportError:
        print("[-] urllib not available")
        return []

    results = []
    for path in HTTP_PROBES:
        url = f'http://{target}:{port}{path}'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=timeout)
            status = resp.getcode()
            content_type = resp.headers.get('Content-Type', 'unknown')
            body_len = len(resp.read(4096))
            results.append({
                'path': path,
                'status': status,
                'content_type': content_type,
                'size': body_len,
            })
            print(f"  [+] {path:20s}  {status}  {content_type}  {body_len} bytes")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                results.append({'path': path, 'status': e.code, 'error': str(e)})
                print(f"  [?] {path:20s}  {e.code}  {e.reason}")
        except (urllib.error.URLError, OSError, TimeoutError):
            pass

    if not results:
        print("  [-] No HTTP endpoints responded")

    return results


def enumerate_device(target='192.168.42.1'):
    """Full enumeration of a Walksnail device."""
    print(f"\n{'=' * 70}")
    print(f"WALKSNAIL DEVICE ENUMERATION: {target}")
    print(f"{'=' * 70}")

    results = {
        'target': target,
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'ports': [],
        'banners': {},
        'http': [],
    }

    # Port scan
    results['ports'] = port_scan(target)

    # Banner grabbing for open ports
    print(f"\n[*] Grabbing service banners...")
    for port_info in results['ports']:
        port = port_info['port']
        banner = grab_banner(target, port)
        if banner:
            banner_str = banner[:200].decode('utf-8', errors='replace')
            results['banners'][port] = banner_str
            print(f"  [{port}] {banner_str[:80]}")

    # HTTP probing
    for port_info in results['ports']:
        if port_info['port'] in (80, 8080, 443):
            results['http'] = http_probe(target, port_info['port'])

    # Summary
    print(f"\n{'=' * 70}")
    print("ENUMERATION SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Target:     {target}")
    print(f"  Open ports: {len(results['ports'])}")

    high_risk = [p for p in results['ports'] if p['risk'] in ('HIGH', 'CRITICAL')]
    if high_risk:
        print(f"  High-risk:  {len(high_risk)} service(s)")
        for p in high_risk:
            print(f"    - {p['port']}/tcp {p['service']} [{p['risk']}]")

    return results


def fingerprint_device(target='192.168.42.1'):
    """Attempt to fingerprint the exact Walksnail device model and firmware."""
    print(f"\n[*] Fingerprinting device at {target}...")

    fingerprint = {
        'model': None,
        'firmware': None,
        'soc': None,
        'features': [],
    }

    # Check SSH banner for version info
    banner = grab_banner(target, 22)
    if banner:
        banner_str = banner.decode('utf-8', errors='replace')
        print(f"  SSH banner: {banner_str.strip()}")
        if 'dropbear' in banner_str.lower():
            fingerprint['features'].append('dropbear SSH')
        fingerprint['ssh_banner'] = banner_str.strip()

    # Check HTTP for device info
    try:
        import urllib.request
        for endpoint in ['/api/info', '/api/version', '/api/status', '/']:
            try:
                req = urllib.request.Request(
                    f'http://{target}{endpoint}',
                    headers={'User-Agent': 'Mozilla/5.0'}
                )
                resp = urllib.request.urlopen(req, timeout=5)
                body = resp.read(4096).decode('utf-8', errors='replace')
                if any(kw in body.lower() for kw in ['version', 'firmware', 'model', 'avatar']):
                    print(f"  [{endpoint}] Device info found:")
                    print(f"    {body[:200]}")
                    fingerprint['http_info'] = body[:500]
                    break
            except Exception:
                pass
    except ImportError:
        pass

    return fingerprint


def main():
    parser = argparse.ArgumentParser(description='Walksnail Avatar WiFi Discovery')
    parser.add_argument('--scan', action='store_true', help='Scan for Walksnail WiFi networks')
    parser.add_argument('--interface', '-i', default='wlan0', help='WiFi interface')
    parser.add_argument('--enumerate', action='store_true', help='Enumerate connected device')
    parser.add_argument('--target', '-t', default='192.168.42.1', help='Target IP')
    parser.add_argument('--full', action='store_true', help='Full discovery workflow')
    parser.add_argument('--fingerprint', action='store_true', help='Fingerprint device')
    parser.add_argument('--ports', action='store_true', help='Port scan only')
    parser.add_argument('--http', action='store_true', help='HTTP probe only')
    parser.add_argument('--output', '-o', help='Save results to JSON file')

    args = parser.parse_args()

    results = {}

    if args.scan or args.full:
        devices = scan_wifi(args.interface)
        results['scan'] = devices
        if devices:
            print(f"\n[+] Found {len(devices)} Walksnail device(s):")
            for dev in devices:
                print(f"    SSID: {dev['ssid']}  BSSID: {dev.get('bssid', '?')}  "
                      f"Signal: {dev.get('signal', '?')}")
        else:
            print("[-] No Walksnail devices found in WiFi scan")

    if args.enumerate or args.full:
        results['enumeration'] = enumerate_device(args.target)

    if args.fingerprint:
        results['fingerprint'] = fingerprint_device(args.target)

    if args.ports:
        results['ports'] = port_scan(args.target)

    if args.http:
        results['http'] = http_probe(args.target)

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\n[+] Results saved to {args.output}")

    if not any([args.scan, args.enumerate, args.full, args.fingerprint, args.ports, args.http]):
        parser.print_help()


if __name__ == '__main__':
    main()
