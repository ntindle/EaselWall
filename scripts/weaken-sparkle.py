#!/usr/bin/env python3
"""Flip the Sparkle load command in a Mach-O from LC_LOAD_DYLIB to
LC_LOAD_WEAK_DYLIB so the binary tolerates Sparkle.framework being absent.

Used for the App Store build: all Sparkle usage is gated behind #if !APPSTORE
and CI strips Sparkle.framework from the bundle (its helper XPC services trip
MAS validation), so the remaining hard load command would crash the app at
launch. Making it weak lets dyld skip the missing framework instead of aborting.

Handles thin and fat (universal) Mach-O. Operates in place. Idempotent.
"""
import struct
import sys

FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF
MH_MAGIC_64 = 0xFEEDFACF
MH_CIGAM_64 = 0xCFFAEDFE
LC_LOAD_DYLIB = 0x0C
LC_REQ_DYLD = 0x80000000
LC_LOAD_WEAK_DYLIB = 0x18 | LC_REQ_DYLD


def weaken_slice(data: bytearray, base: int) -> int:
    magic = struct.unpack_from(">I", data, base)[0]
    if magic in (MH_MAGIC_64, FAT_MAGIC):  # big-endian on disk
        endian = ">"
    else:
        endian = "<"
    # mach_header_64: magic, cputype, cpusubtype, filetype, ncmds,
    # sizeofcmds, flags, reserved
    _, _, _, _, ncmds, _, _, _ = struct.unpack_from(endian + "8I", data, base)
    off = base + 32  # sizeof(mach_header_64)
    changed = 0
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from(endian + "2I", data, off)
        if cmd == LC_LOAD_DYLIB:
            name_off = struct.unpack_from(endian + "I", data, off + 8)[0]
            name_start = off + name_off
            name_end = data.index(b"\x00", name_start)
            name = data[name_start:name_end].decode("utf-8", "replace")
            if "Sparkle" in name:
                struct.pack_into(endian + "I", data, off, LC_LOAD_WEAK_DYLIB)
                changed += 1
        off += cmdsize
    return changed


def main(path: str) -> int:
    with open(path, "rb") as f:
        data = bytearray(f.read())

    magic = struct.unpack_from(">I", data, 0)[0]
    total = 0
    if magic in (FAT_MAGIC, FAT_MAGIC_64):
        nfat = struct.unpack_from(">I", data, 4)[0]
        is64 = magic == FAT_MAGIC_64
        entry_size = 32 if is64 else 20
        for i in range(nfat):
            eoff = 8 + i * entry_size
            # fat_arch(_64): cputype, cpusubtype, offset, size, align[, reserved]
            if is64:
                offset = struct.unpack_from(">Q", data, eoff + 8)[0]
            else:
                offset = struct.unpack_from(">I", data, eoff + 8)[0]
            total += weaken_slice(data, offset)
    else:
        total += weaken_slice(data, 0)

    with open(path, "wb") as f:
        f.write(data)
    print(f"weaken-sparkle: flipped {total} Sparkle load command(s) to weak")
    return 0 if total else 0  # absent is fine (idempotent / already weak)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: weaken-sparkle.py <path-to-macho-binary>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
