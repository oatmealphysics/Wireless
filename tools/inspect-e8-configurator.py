#!/usr/bin/env python3
"""Download and statically inventory EMAX's publicly linked E8 configurator.

Usage: python tools/inspect-e8-configurator.py --download
Python 3.10+; dependencies pycryptodome==3.23.0 and pefile==2024.8.26.
Never executes the downloaded application or opens a serial port. All downloaded
and derived files stay in ignored work/e8-session/vendor. Existing files are
preserved. The share URL is public in EMAX's support article, not a credential.
Mega decryption is for inspection only: this helper does not verify Mega's file
MAC or establish vendor authenticity; record SHA-256 and inspect signing too.
"""
import argparse
import base64
import hashlib
import json
import re
import struct
from pathlib import Path
from urllib.request import Request, urlopen

SOURCE = "https://emaxusa2.freshdesk.com/support/solutions/articles/19000157264-expresslrs-e8-transmitter-custom-configurator"
PUBLIC_SHARE = "https://mega.nz/file/pkICkJhQ#xO-EF1GunGGqs-GASkFishIIPg1iRWMo_-i_owCs-Ag"
DIRECTORY = Path(__file__).resolve().parents[1] / "work/e8-session/vendor"


def decode64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def download():
    from Crypto.Cipher import AES
    handle, public_key = PUBLIC_SHARE.split("/file/")[1].split("#")
    request = Request("https://g.api.mega.co.nz/cs?id=1", data=json.dumps([
        {"a": "g", "g": 1, "p": handle}]).encode(), headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=30) as response:
        metadata = json.load(response)[0]
    if not isinstance(metadata, dict) or "g" not in metadata:
        raise ValueError(f"File endpoint did not return download metadata: {metadata}")
    words = struct.unpack(">8I", decode64(public_key))
    key = struct.pack(">4I", *(words[i] ^ words[i + 4] for i in range(4)))
    attributes = AES.new(key, AES.MODE_CBC, iv=bytes(16)).decrypt(decode64(metadata["at"]))
    if not attributes.startswith(b"MEGA"):
        raise ValueError("Unexpected share attributes")
    attributes = json.loads(attributes[4:].rstrip(b"\0"))
    name = Path(attributes["n"]).name
    if name in ("", ".", ".."):
        raise ValueError("Invalid filename")
    path = DIRECTORY / name
    counter = int.from_bytes(struct.pack(">4I", words[4], words[5], 0, 0), "big")
    cipher = AES.new(key, AES.MODE_CTR, nonce=b"", initial_value=counter)
    with path.open("xb") as target, urlopen(metadata["g"], timeout=30) as stream:
        while chunk := stream.read(65536):
            target.write(cipher.decrypt(chunk))
    if path.stat().st_size != metadata["s"]:
        raise ValueError("Downloaded size differs from API metadata")
    return path


def inspect(path):
    import pefile
    data = path.read_bytes()
    strings = [item.decode("ascii") for item in re.findall(rb"[\x20-\x7e]{5,}", data)]
    strings += [item.decode("utf-16le") for item in re.findall(rb"(?:[\x20-\x7e]\x00){5,}", data)]
    (DIRECTORY / f"{path.stem}.strings.txt").write_text("\n".join(strings), encoding="utf-8")
    result = {"reference_article": SOURCE, "reference_public_share": PUBLIC_SHARE,
              "input_path": str(path),
              "filename": path.name, "size_bytes": len(data),
              "sha256": hashlib.sha256(data).hexdigest(), "executed": False}
    if data.startswith(b"MZ"):
        pe = pefile.PE(data=data)
        result["machine"] = hex(pe.FILE_HEADER.Machine)
        result["sections"] = [section.Name.rstrip(b"\0").decode("ascii", errors="replace") for section in pe.sections]
        result["imports"] = [item.dll.decode("ascii", errors="replace") for item in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]
        result["clr_directory_present"] = bool(pe.OPTIONAL_HEADER.DATA_DIRECTORY[14].VirtualAddress)
        result["versions"] = {}
        for group in getattr(pe, "FileInfo", []):
            for entry in group:
                for table in getattr(entry, "StringTable", []):
                    result["versions"].update({key.decode(errors="replace"): value.decode(errors="replace") for key, value in table.entries.items()})
        overlay = pe.get_overlay_data_start_offset()
        result["overlay_offset"] = overlay
        if overlay is not None:
            result["overlay_size"] = len(data) - overlay
    result["interesting_strings"] = sorted(set(s for s in strings if re.search(
        r"firmware|version|baud|115200|460800|420000|STM32|GD32|CH32|ESP32|ELRS|IAP|readData|readVersion|serialPort", s, re.I)))[:100]
    (DIRECTORY / f"{path.stem}.inspection.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Explicitly fetch public manufacturer-linked file")
    parser.add_argument("--file", type=Path, help="Statically inspect an already downloaded file")
    args = parser.parse_args()
    if args.download == bool(args.file):
        parser.error("select exactly one of --download or --file")
    DIRECTORY.mkdir(parents=True, exist_ok=True)
    inspect(download() if args.download else args.file)


if __name__ == "__main__":
    main()
