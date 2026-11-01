#!/usr/bin/env python3
"""Static x64 serial-call inventory; never executes the input PE or device I/O.

Python 3.10+, pefile==2024.8.26 and capstone==5.0.6.
Usage: python tools/inspect-e8-serial-handlers.py FILE [--function 0xADDRESS]
The Enigma wrapper changes the advertised import directory. This helper finds
name-backed entries still present in .idata and labels direct import thunks.
Results are static analysis leads, not a verified source reconstruction.
"""
import argparse
import json
import struct
from pathlib import Path


def main():
    import capstone
    import pefile
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--function", type=lambda value: int(value, 0))
    parser.add_argument("--size", type=lambda value: int(value, 0), default=512,
                        help="Byte window when unwind function bounds are unavailable")
    args = parser.parse_args()
    pe = pefile.PE(data=args.file.read_bytes())
    base = pe.OPTIONAL_HEADER.ImageBase
    imports = {}
    for section in pe.sections:
        if section.Name.rstrip(b"\0") != b".idata":
            continue
        data = section.get_data()
        # MinGW import tables may start on a four-byte boundary even on x64.
        for offset in range(0, len(data) - 7, 4):
            rva = struct.unpack_from("<Q", data, offset)[0]
            if not 0 < rva < pe.OPTIONAL_HEADER.SizeOfImage:
                continue
            name = pe.get_string_at_rva(rva + 2)
            if name and (name.startswith(b"_Z") or name[:1].isalpha()) and all(32 <= b < 127 for b in name):
                imports[base + section.VirtualAddress + offset] = name.decode()
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoder.detail = True
    decoder.skipdata = True
    instructions = []
    for section in pe.sections:
        if section.Name.rstrip(b"\0") == b".text":
            instructions.extend(decoder.disasm(section.get_data(), base + section.VirtualAddress))
    thunks = {}
    for ins in instructions:
        if ins.mnemonic == "jmp" and ins.operands and ins.operands[0].type == capstone.CS_OP_MEM:
            mem = ins.operands[0].mem
            if mem.base == capstone.x86.X86_REG_RIP:
                name = imports.get(ins.address + ins.size + mem.disp)
                if name:
                    thunks[ins.address] = name
    ranges = []
    for section in pe.sections:
        if section.Name.rstrip(b"\0") == b".pdata":
            raw = section.get_data()
            for offset in range(0, len(raw) - 11, 12):
                begin, end, unwind = struct.unpack_from("<III", raw, offset)
                if 0 < begin < end < 0x24000:
                    ranges.append((base + begin, base + end))
    print(json.dumps({"image_base": hex(base), "import_entries": len(imports),
                      "thunks": len(thunks), "runtime_functions": len(ranges)}))
    for index, ins in enumerate(instructions):
        name = None
        if ins.mnemonic in ("call", "jmp") and ins.operands:
            operand = ins.operands[0]
            if operand.type == capstone.CS_OP_IMM:
                name = thunks.get(operand.imm)
            elif operand.type == capstone.CS_OP_MEM and operand.mem.base == capstone.x86.X86_REG_RIP:
                name = imports.get(ins.address + ins.size + operand.mem.disp)
        bounds = next(((start, end) for start, end in ranges if start <= ins.address < end), None)
        selected = args.function is not None and (
            (bounds and bounds[0] <= args.function < bounds[1]) or
            (not bounds and args.function <= ins.address < args.function + args.size))
        interesting = name and any(word in name for word in ("SerialPort", "QIODevice4read", "QIODevice5write", "QIODevice7readAll"))
        if selected or (args.function is None and interesting):
            if selected:
                print(f"{ins.address:#x}: {ins.mnemonic:8} {ins.op_str}" + (f" ; {name}" if name else ""))
            else:
                print(json.dumps({"address": hex(ins.address), "function": hex(bounds[0]) if bounds else None,
                                  "import": name, "previous": [f"{item.address:#x}: {item.mnemonic} {item.op_str}" for item in instructions[max(0, index - 5):index]]}))


if __name__ == "__main__":
    main()
