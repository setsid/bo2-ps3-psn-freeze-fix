#!/usr/bin/env python3
"""
Fixes the CRM logging fault that freezes Black Ops 2 on PS3 when a PSN
session is active.

Run against a DECRYPTED binary (EBOOT.BIN or t6mp_ps3f.self as .elf).
Locates the fault by instruction pattern, so it is not tied to one region
or one title update.

    python3 patch-bo2.py v119.elf v119.patched.elf
"""
import struct
import sys

FMT = b"crm %lld %s\x00"
LI_R4_1B8 = 0x388001B8
NOP = 0x60000000
LOAD_BASE = 0x10000


def u32(d, o):
    return struct.unpack_from(">I", d, o)[0]


def sx16(v):
    return v - 0x10000 if v & 0x8000 else v


def find_format_string(d):
    off = d.find(FMT)
    if off < 0:
        raise SystemExit("format string not found - is this a decrypted BO2 binary?")
    if d.find(FMT, off + 1) >= 0:
        raise SystemExit("format string appears more than once - aborting")
    return off + LOAD_BASE


def find_construct(d, target):
    """Find where the format string address is built into a register."""
    out = []
    for o in range(0, len(d) - 0x80, 4):
        w = u32(d, o)
        if (w >> 26) != 15 or ((w >> 16) & 31) != 0:
            continue
        rt = (w >> 21) & 31
        hi = (w & 0xFFFF) << 16
        for k in range(1, 12):
            p = o + 4 * k
            w2 = u32(d, p)
            op = w2 >> 26
            imm = w2 & 0xFFFF
            if op in (12, 13, 14) and ((w2 >> 16) & 31) == rt:
                val = (hi + sx16(imm)) & 0xFFFFFFFF
            elif op == 24 and ((w2 >> 21) & 31) == rt:
                val = (hi | imm) & 0xFFFFFFFF
            else:
                continue
            if val == target:
                out.append(p)
            break
    return out


def find_call(d, start):
    """From the address construct, find the snprintf call to remove."""
    seen_size = False
    for k in range(0, 16):
        p = start + 4 * k
        w = u32(d, p)
        if w == LI_R4_1B8:
            seen_size = True
            continue
        if seen_size and (w >> 26) == 18 and (w & 1):
            return p
    raise SystemExit("could not locate the call - binary may differ from expected")


def main():
    if len(sys.argv) != 3:
        raise SystemExit(f"usage: {sys.argv[0]} <input.elf> <output.elf>")

    src, dst = sys.argv[1], sys.argv[2]
    d = bytearray(open(src, "rb").read())

    fmt_va = find_format_string(d)
    print(f'"crm %lld %s" at vaddr {fmt_va:08X}')

    sites = find_construct(d, fmt_va)
    if len(sites) != 1:
        raise SystemExit(f"expected 1 construct site, found {len(sites)} - aborting")

    call = find_call(d, sites[0])
    old = u32(d, call)
    print(f"call at vaddr {call + LOAD_BASE:08X} (file {call:08X}): {old:08X} -> {NOP:08X}")

    struct.pack_into(">I", d, call, NOP)
    open(dst, "wb").write(d)

    check = u32(bytearray(open(dst, "rb").read()), call)
    if check != NOP:
        raise SystemExit("write verification failed")
    print(f"written: {dst}")


if __name__ == "__main__":
    main()
