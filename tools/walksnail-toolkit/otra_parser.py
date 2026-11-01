"""
Walksnail Avatar OTRA Firmware Container Parser

Parses the OTRA firmware format used by Walksnail Avatar devices.
Extracts header, RSA signature, and payload partitions for offline analysis.

The OTRA format is a container wrapping multiple firmware partitions
(kernel, rootfs, usr) with RSA signature verification.

Usage:
    python3 otra_parser.py --file avatar_firmware.img --info
    python3 otra_parser.py --file avatar_firmware.img --extract output_dir/
    python3 otra_parser.py --file avatar_firmware.img --hexdump --offset 0 --length 512

For authorized security research only.
"""

import argparse
import struct
import hashlib
import os
import sys


# Known partition layout from SPI NOR flash (Winbond W25Q01JV, 128 MiB)
FLASH_PARTITION_MAP = {
    'uboot0':     {'offset': 0x000000, 'size': 0x080000, 'desc': 'Primary U-Boot'},
    'uboot1':     {'offset': 0x080000, 'size': 0x080000, 'desc': 'Backup U-Boot'},
    'uboot-env0': {'offset': 0x100000, 'size': 0x010000, 'desc': 'U-Boot environment'},
    'uboot-env1': {'offset': 0x110000, 'size': 0x010000, 'desc': 'Backup environment'},
    'dtb0':       {'offset': 0x120000, 'size': 0x020000, 'desc': 'Device tree blob'},
    'dtb1':       {'offset': 0x140000, 'size': 0x020000, 'desc': 'Backup DTB'},
    'kernel0':    {'offset': 0x160000, 'size': 0x400000, 'desc': 'Linux kernel'},
    'kernel1':    {'offset': 0x560000, 'size': 0x400000, 'desc': 'Backup kernel'},
    'rootfs0':    {'offset': 0x960000, 'size': 0xE00000, 'desc': 'Root filesystem'},
    'rootfs1':    {'offset': 0x1760000, 'size': 0xE00000, 'desc': 'Backup rootfs'},
    'usr0':       {'offset': 0x2560000, 'size': 0x3000000, 'desc': 'User partition 0'},
    'usr1':       {'offset': 0x5560000, 'size': 0x3000000, 'desc': 'User partition 1'},
    'cal':        {'offset': 0x8560000, 'size': 0x100000, 'desc': 'RF calibration'},
    'res':        {'offset': 0x8660000, 'size': None, 'desc': 'Reserved (to end)'},
}

# Common magic bytes in firmware headers
KNOWN_MAGICS = {
    b'\x27\x05\x19\x56': 'uImage (U-Boot legacy)',
    b'\xd0\x0d\xfe\xed': 'Device Tree Blob (FDT)',
    b'\x68\x73\x71\x73': 'SquashFS (little-endian)',
    b'\x73\x71\x73\x68': 'SquashFS (big-endian)',
    b'\x85\x19\x01\x28': 'JFFS2 (little-endian)',
    b'\x1f\x8b\x08': 'gzip compressed',
    b'\xfd\x37\x7a\x58\x5a\x00': 'xz compressed',
    b'\x89\x4c\x5a\x4f': 'LZO compressed',
    b'\x04\x22\x4d\x18': 'LZ4 compressed',
    b'\x30\x37\x30\x37\x30\x31': 'CPIO archive (newc)',
}

# U-Boot environment defaults
UBOOT_ENV_DELIM = b'\x00'
UBOOT_ENV_END = b'\x00\x00'


class OTRAHeader:
    """Represents a parsed OTRA firmware container header."""

    def __init__(self):
        self.magic = None
        self.version = None
        self.total_size = 0
        self.header_size = 0
        self.payload_offset = 0
        self.payload_size = 0
        self.signature_offset = 0
        self.signature_size = 0
        self.num_partitions = 0
        self.target_device = None
        self.firmware_version = None
        self.raw_header = None

    def __str__(self):
        lines = [
            "OTRA Container Header:",
            f"  Magic:            {self.magic}",
            f"  Version:          {self.version}",
            f"  Total size:       {self.total_size} bytes ({self.total_size / 1024 / 1024:.1f} MiB)",
            f"  Header size:      {self.header_size} bytes",
            f"  Payload offset:   0x{self.payload_offset:08x}",
            f"  Payload size:     {self.payload_size} bytes ({self.payload_size / 1024 / 1024:.1f} MiB)",
            f"  Signature offset: 0x{self.signature_offset:08x}",
            f"  Signature size:   {self.signature_size} bytes",
            f"  Partitions:       {self.num_partitions}",
            f"  Target device:    {self.target_device}",
            f"  FW version:       {self.firmware_version}",
        ]
        return '\n'.join(lines)


class FlashImage:
    """Analyzer for raw SPI NOR flash dumps."""

    def __init__(self, data):
        self.data = data
        self.size = len(data)

    def extract_partition(self, name):
        """Extract a named partition from the flash dump."""
        if name not in FLASH_PARTITION_MAP:
            print(f"[-] Unknown partition: {name}")
            return None

        part = FLASH_PARTITION_MAP[name]
        offset = part['offset']
        size = part['size']

        if size is None:
            size = self.size - offset

        if offset + size > self.size:
            print(f"[-] Partition {name} extends beyond flash dump "
                  f"(need {offset + size}, have {self.size})")
            return None

        return self.data[offset:offset + size]

    def identify_partition_content(self, name):
        """Identify the content type of a partition by its magic bytes."""
        data = self.extract_partition(name)
        if data is None:
            return "EXTRACT FAILED"

        if all(b == 0xFF for b in data[:256]):
            return "EMPTY (0xFF)"
        if all(b == 0x00 for b in data[:256]):
            return "ZEROED"

        for magic, desc in KNOWN_MAGICS.items():
            if data[:len(magic)] == magic:
                return desc

        return f"Unknown (first bytes: {data[:8].hex()})"

    def extract_uboot_env(self, bank=0):
        """Extract and decode U-Boot environment variables."""
        part_name = f'uboot-env{bank}'
        data = self.extract_partition(part_name)
        if data is None:
            return {}

        # U-Boot env format: [CRC32(4 bytes)][flags(1 byte, redundant only)][env data...]
        # Env data is null-terminated key=value pairs, ended by double-null
        crc_stored = struct.unpack('<I', data[:4])[0]
        env_data = data[4:]  # Skip CRC; some formats have a 1-byte flag too

        # Try with and without flags byte
        for skip in (0, 1):
            env_bytes = env_data[skip:]
            env_vars = {}
            pos = 0
            while pos < len(env_bytes) - 1:
                if env_bytes[pos] == 0x00:
                    break
                end = env_bytes.index(b'\x00', pos) if b'\x00' in env_bytes[pos:] else len(env_bytes)
                try:
                    entry = env_bytes[pos:end].decode('ascii', errors='replace')
                    if '=' in entry:
                        key, val = entry.split('=', 1)
                        env_vars[key] = val
                except (UnicodeDecodeError, ValueError):
                    pass
                pos = end + 1

            if env_vars:
                return env_vars

        return {}

    def print_partition_map(self):
        """Print the partition map with content identification."""
        print("\n" + "=" * 80)
        print("FLASH PARTITION MAP")
        print("=" * 80)
        print(f"{'Name':15s} {'Offset':>10s} {'Size':>10s} {'Content':30s} Description")
        print("-" * 80)

        for name, part in FLASH_PARTITION_MAP.items():
            offset = part['offset']
            size = part['size'] or (self.size - offset)

            if offset < self.size:
                content = self.identify_partition_content(name)
            else:
                content = "BEYOND DUMP"

            print(f"{name:15s} 0x{offset:08x} {size:>9d} {content:30s} {part['desc']}")

    def find_strings(self, partition_name, min_length=6):
        """Extract printable strings from a partition."""
        data = self.extract_partition(partition_name)
        if data is None:
            return []

        strings = []
        current = []
        for byte in data:
            if 0x20 <= byte <= 0x7E:
                current.append(chr(byte))
            else:
                if len(current) >= min_length:
                    strings.append(''.join(current))
                current = []
        if len(current) >= min_length:
            strings.append(''.join(current))

        return strings


class OTRAParser:
    """Parser for OTRA firmware container files."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.data = None
        self.header = None
        self.partitions = []

    def load(self):
        """Load firmware file into memory."""
        if not os.path.exists(self.filepath):
            print(f"[-] File not found: {self.filepath}")
            return False

        with open(self.filepath, 'rb') as f:
            self.data = f.read()

        print(f"[+] Loaded {len(self.data)} bytes ({len(self.data) / 1024 / 1024:.1f} MiB)")
        return True

    def detect_format(self):
        """Detect whether this is an OTRA container or raw flash dump."""
        if self.data is None:
            return None

        # Check for known firmware magic bytes at offset 0
        for magic, desc in KNOWN_MAGICS.items():
            if self.data[:len(magic)] == magic:
                return f"raw:{desc}"

        # Check if it looks like a flash dump (U-Boot at offset 0)
        # U-Boot binaries often start with ARM exception vectors
        if len(self.data) >= 0x100000:
            # Check for U-Boot strings near expected offset
            uboot_region = self.data[:0x80000]
            if b'U-Boot' in uboot_region or b'uboot' in uboot_region:
                return "flash_dump"

        # Check for OTRA magic (format not fully documented — probe common patterns)
        # OTRA containers likely have a header with:
        # - Magic bytes (4-8 bytes)
        # - Version field
        # - Size fields
        # - Signature block
        header_16 = self.data[:16]

        # Look for "OTRA" ASCII magic
        if b'OTRA' in self.data[:64]:
            return "otra_container"

        # Look for other container indicators
        if b'AVTR' in self.data[:64] or b'WSNL' in self.data[:64]:
            return "otra_container"

        # Heuristic: if file is close to known partition sizes, likely a partition image
        size = len(self.data)
        for name, part in FLASH_PARTITION_MAP.items():
            if part['size'] and abs(size - part['size']) < 4096:
                return f"partition:{name}"

        return "unknown"

    def parse_header(self):
        """Attempt to parse OTRA container header."""
        if self.data is None:
            return False

        fmt = self.detect_format()
        print(f"[*] Detected format: {fmt}")

        if fmt == "flash_dump":
            print("[+] This appears to be a raw SPI NOR flash dump")
            flash = FlashImage(self.data)
            flash.print_partition_map()
            return True

        if fmt and fmt.startswith("raw:"):
            print(f"[+] This appears to be a raw image: {fmt[4:]}")
            return True

        if fmt == "otra_container":
            print("[+] OTRA container detected — parsing header...")
            return self._parse_otra_header()

        # Unknown format — do best-effort analysis
        print("[?] Unknown format — performing heuristic analysis")
        self._heuristic_analysis()
        return True

    def _parse_otra_header(self):
        """Parse an OTRA firmware container header (best-effort, format partially known)."""
        self.header = OTRAHeader()
        self.header.raw_header = self.data[:256]

        # Search for magic and extract what we can
        otra_pos = self.data.find(b'OTRA')
        if otra_pos >= 0:
            self.header.magic = f"OTRA at offset {otra_pos}"
            # Try to read version and size fields after magic
            if otra_pos + 16 <= len(self.data):
                after_magic = self.data[otra_pos + 4:otra_pos + 20]
                # Attempt little-endian uint32 reads
                fields = struct.unpack('<IIII', after_magic)
                print(f"    Fields after magic: {[f'0x{f:08x}' for f in fields]}")

        # Look for RSA signature (typically 256 bytes for RSA-2048)
        # Common RSA signature patterns: high-entropy block of exactly 256 bytes
        self._find_signature_blocks()

        print(self.header)
        return True

    def _find_signature_blocks(self):
        """Search for RSA signature blocks in the firmware."""
        # RSA-2048 signatures are 256 bytes, RSA-4096 are 512 bytes
        print("\n[*] Searching for potential RSA signature blocks...")

        # Look for high-entropy 256-byte blocks at common offsets
        check_offsets = [0x100, 0x200, 0x400, 0x800, 0x1000]

        for offset in check_offsets:
            if offset + 256 > len(self.data):
                continue
            block = self.data[offset:offset + 256]
            entropy = self._shannon_entropy(block)
            if entropy > 7.5:  # High entropy suggests signature/encrypted data
                print(f"    [!] High-entropy block at 0x{offset:08x} "
                      f"(entropy={entropy:.2f}, likely signature/crypto)")

    def _shannon_entropy(self, data):
        """Calculate Shannon entropy of a data block."""
        if not data:
            return 0
        freq = {}
        for byte in data:
            freq[byte] = freq.get(byte, 0) + 1
        length = len(data)
        entropy = 0
        import math
        for count in freq.values():
            p = count / length
            if p > 0:
                entropy -= p * math.log2(p)
        return entropy

    def _heuristic_analysis(self):
        """Perform heuristic analysis on unknown firmware format."""
        print("\n[*] Heuristic analysis:")
        print(f"    File size:     {len(self.data)} bytes ({len(self.data) / 1024 / 1024:.2f} MiB)")
        print(f"    First 16 hex:  {self.data[:16].hex()}")

        # Check for embedded filesystems
        print("\n[*] Scanning for embedded filesystem signatures...")
        for magic, desc in KNOWN_MAGICS.items():
            pos = 0
            while True:
                idx = self.data.find(magic, pos)
                if idx == -1:
                    break
                print(f"    [{desc}] found at offset 0x{idx:08x}")
                pos = idx + 1
                if pos > len(self.data):
                    break

        # Check for strings that identify the firmware
        print("\n[*] Scanning for identifying strings...")
        interesting = [b'Walksnail', b'Avatar', b'Caddx', b'Artosyn',
                       b'AR9201', b'U-Boot', b'Linux', b'root',
                       b'dropbear', b'busybox', b'OTRA', b'firmware',
                       b'version', b'serial', b'password']
        for pattern in interesting:
            pos = 0
            count = 0
            first_pos = None
            while True:
                idx = self.data.find(pattern, pos)
                if idx == -1:
                    break
                if first_pos is None:
                    first_pos = idx
                count += 1
                pos = idx + 1
            if count > 0:
                print(f"    '{pattern.decode()}': {count} occurrence(s), "
                      f"first at 0x{first_pos:08x}")

        # Compute overall hashes
        print(f"\n    SHA256: {hashlib.sha256(self.data).hexdigest()}")
        print(f"    MD5:    {hashlib.md5(self.data).hexdigest()}")

    def extract_partitions(self, output_dir):
        """Extract all identifiable partitions to output directory."""
        os.makedirs(output_dir, exist_ok=True)

        fmt = self.detect_format()

        if fmt == "flash_dump":
            flash = FlashImage(self.data)
            for name, part in FLASH_PARTITION_MAP.items():
                data = flash.extract_partition(name)
                if data is not None:
                    out_path = os.path.join(output_dir, f"{name}.bin")
                    with open(out_path, 'wb') as f:
                        f.write(data)
                    content = flash.identify_partition_content(name)
                    print(f"    [+] {name}: {len(data)} bytes → {out_path} ({content})")
            return True

        # For other formats, extract embedded filesystems
        print("[*] Extracting embedded filesystems...")
        count = 0
        for magic, desc in KNOWN_MAGICS.items():
            pos = 0
            idx_count = 0
            while True:
                idx = self.data.find(magic, pos)
                if idx == -1:
                    break
                out_path = os.path.join(output_dir,
                                        f"embedded_{desc.replace(' ', '_')}_{idx_count}_0x{idx:08x}.bin")
                # Extract a reasonable chunk (up to next magic or end)
                next_magic = len(self.data)
                for m2 in KNOWN_MAGICS:
                    next_idx = self.data.find(m2, idx + len(magic))
                    if next_idx != -1 and next_idx < next_magic:
                        next_magic = next_idx
                chunk = self.data[idx:next_magic]
                with open(out_path, 'wb') as f:
                    f.write(chunk)
                print(f"    [+] {desc} at 0x{idx:08x}: {len(chunk)} bytes → {out_path}")
                count += 1
                idx_count += 1
                pos = idx + 1

        if count == 0:
            # Dump the whole file
            out_path = os.path.join(output_dir, "firmware_raw.bin")
            with open(out_path, 'wb') as f:
                f.write(self.data)
            print(f"    [*] No embedded FS found; raw dump → {out_path}")

        return True

    def hexdump(self, offset=0, length=256):
        """Print hex dump of firmware data."""
        end = min(offset + length, len(self.data))
        data = self.data[offset:end]

        print(f"\nHex dump: offset=0x{offset:08x} length={end - offset}")
        print("-" * 76)

        for i in range(0, len(data), 16):
            hex_part = ' '.join(f'{b:02x}' for b in data[i:i + 16])
            ascii_part = ''.join(chr(b) if 0x20 <= b <= 0x7E else '.' for b in data[i:i + 16])
            print(f"  {offset + i:08x}  {hex_part:<48s}  |{ascii_part}|")


def analyze_uboot_env(env_vars):
    """Analyze U-Boot environment for security-relevant settings."""
    print("\n[*] U-Boot Environment Security Analysis:")
    print("=" * 60)

    security_keys = {
        'bootcmd': 'Boot command (controls boot flow)',
        'bootargs': 'Kernel command line (init=, root=, console=)',
        'console': 'Console device (debug access)',
        'ethaddr': 'MAC address',
        'ipaddr': 'IP address',
        'serverip': 'TFTP server IP',
        'gatewayip': 'Gateway IP',
        'verify': 'Image verification (yes/no)',
        'secure_boot': 'Secure boot setting',
        'bootdelay': 'Boot delay (seconds to interrupt)',
    }

    for key, desc in security_keys.items():
        if key in env_vars:
            val = env_vars[key]
            indicator = "!"
            if key == 'verify' and val.lower() == 'no':
                indicator = "!!"
            elif key == 'bootdelay' and int(val) > 0:
                indicator = "!"
            elif key == 'console':
                indicator = "!"
            print(f"  [{indicator}] {key:20s} = {val}")
            print(f"        → {desc}")

    # Print all env vars
    print(f"\n[*] All {len(env_vars)} environment variables:")
    for key in sorted(env_vars.keys()):
        print(f"    {key}={env_vars[key]}")


def main():
    parser = argparse.ArgumentParser(description='Walksnail Avatar OTRA Firmware Parser')
    parser.add_argument('--file', '-f', required=True, help='Firmware file to analyze')
    parser.add_argument('--info', '-i', action='store_true', help='Show firmware info')
    parser.add_argument('--extract', '-e', help='Extract partitions to directory')
    parser.add_argument('--hexdump', '-x', action='store_true', help='Show hex dump')
    parser.add_argument('--offset', type=lambda x: int(x, 0), default=0, help='Hex dump offset')
    parser.add_argument('--length', type=int, default=256, help='Hex dump length')
    parser.add_argument('--uboot-env', action='store_true', help='Extract U-Boot environment')
    parser.add_argument('--strings', help='Extract strings from named partition')

    args = parser.parse_args()

    otra = OTRAParser(args.file)
    if not otra.load():
        sys.exit(1)

    if args.info or (not args.extract and not args.hexdump
                     and not args.uboot_env and not args.strings):
        otra.parse_header()

    if args.extract:
        otra.extract_partitions(args.extract)

    if args.hexdump:
        otra.hexdump(args.offset, args.length)

    if args.uboot_env:
        fmt = otra.detect_format()
        if fmt == "flash_dump":
            flash = FlashImage(otra.data)
            for bank in (0, 1):
                env = flash.extract_uboot_env(bank)
                if env:
                    print(f"\n[+] U-Boot environment bank {bank}: {len(env)} variables")
                    analyze_uboot_env(env)
                else:
                    print(f"[-] No env vars found in bank {bank}")
        else:
            print("[-] U-Boot env extraction requires a raw flash dump")

    if args.strings:
        fmt = otra.detect_format()
        if fmt == "flash_dump":
            flash = FlashImage(otra.data)
            strings = flash.find_strings(args.strings)
            print(f"\n[+] {len(strings)} strings from '{args.strings}':")
            for s in strings[:200]:
                print(f"    {s}")
            if len(strings) > 200:
                print(f"    ... and {len(strings) - 200} more")
        else:
            print("[-] Partition string extraction requires a raw flash dump")


if __name__ == '__main__':
    main()
