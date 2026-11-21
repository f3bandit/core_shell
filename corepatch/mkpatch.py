#!/usr/bin/env python3
"""mkpatch: the difference between a stock core and its patched build, as a Coreshell core patch.

    mkpatch.py STOCK.rbf PATCHED.rbf OUT.cspatch [RESULT.rbf] [--name NAME]

PATCHED.rbf is the stock core with Coreshell's logic placed into it (nextpnr with NPNR_BASE_RBF). The patch
holds only the bytes that differ: in the uncompressed header and in the decompressed logic frames (their CRCs
included), plus the stock core's MD5 (it only applies to that exact file) and the result's MD5 (checked after
applying). corepatch applies it on the MiSTer to the user's own stock file; no core is ever distributed.
The result keeps the stock file's own end (Quartus's postamble), recompressed the way Quartus and Mistral do.

Format (little endian):
    "CSPATCH1"  md5(stock)[16]  md5(result)[16]  prefix_len u32  framed_len u32  name_len u16  name
    runs: u8 area (0 header, 1 frames), u32 offset, u16 length, bytes...   (area 0xFF ends the list)

Cyclone V 5CSEBA6 (DE10-Nano / SuperStation): header 40408 bytes, frames 7605 x 916 bytes.
Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import hashlib
import struct
import sys

PREFIX = 40408
FRAMED = 7605 * 916


def decompress(rbf):
    """header, frames (decompressed), postamble: Cyclone V's nibble compression (a mask nibble saying which of
    the next four nibbles are non-zero, then those nibbles)"""
    head = rbf[:PREFIX]
    out = bytearray(FRAMED)
    pos = PREFIX * 2                     # in nibbles
    o = 0

    def nib():
        nonlocal pos
        b = rbf[pos >> 1]
        v = (b >> 4) if pos & 1 else (b & 0xF)
        pos += 1
        return v

    while o < FRAMED:
        key = nib()
        v = nib() if key & 1 else 0
        if key & 2:
            v |= nib() << 4
        out[o] = v
        v = nib() if key & 4 else 0
        if key & 8:
            v |= nib() << 4
        out[o + 1] = v
        o += 2
    end = (pos + 1) >> 1                 # the compressor pads to a whole byte
    return head, out, rbf[end:]


def compress(frames):
    out = bytearray()
    high = False

    def w(n):
        nonlocal high
        if high:
            out[-1] |= n << 4
            high = False
        else:
            out.append(n)
            high = True

    for i in range(0, len(frames), 2):
        a, b = frames[i], frames[i + 1]
        mask = (1 if a & 0x0F else 0) | (2 if a & 0xF0 else 0) | (4 if b & 0x0F else 0) | (8 if b & 0xF0 else 0)
        w(mask)
        if mask & 1: w(a & 0xF)
        if mask & 2: w(a >> 4)
        if mask & 4: w(b & 0xF)
        if mask & 8: w(b >> 4)
    while high:
        w(0xF)
    return bytes(out)


def apply(stock, rs):
    """what corepatch makes of the stock file"""
    h, f, p = decompress(stock)
    h, f = bytearray(h), bytearray(f)
    for area, off, data in rs:
        (h if area == 0 else f)[off:off + len(data)] = data
    return bytes(h) + compress(f) + p


def runs(a, b, area, gap=4):
    """(area, offset, bytes) runs where a and b differ; runs closer than gap are merged"""
    out = []
    i, n = 0, len(a)
    while i < n:
        if a[i] == b[i]:
            i += 1
            continue
        j = i
        k = i
        while k < n and (k - j) <= gap:
            if a[k] != b[k]:
                j = k
            k += 1
        out.append((area, i, bytes(b[i:j + 1])))
        i = j + 1
    return out


def main():
    args = sys.argv[1:]
    name = ''
    if '--name' in args:
        k = args.index('--name')
        name = args[k + 1]
        del args[k:k + 2]
    stock_f, patched_f, out_f = args[:3]
    stock = open(stock_f, 'rb').read()
    patched = open(patched_f, 'rb').read()
    h1, f1, p1 = decompress(stock)
    h2, f2, p2 = decompress(patched)
    rs = runs(h1, h2, 0) + runs(f1, f2, 1)
    result = apply(stock, rs)
    if decompress(result)[:2] != (h2, f2):
        sys.exit('the result does not match the patched build')
    if compress(f1) + p1 != stock[PREFIX:]:
        sys.exit('recompressing the stock core does not give it back: unknown compression')
    nm = name.encode()
    blob = bytearray(b'CSPATCH1' + hashlib.md5(stock).digest() + hashlib.md5(result).digest())
    blob += struct.pack('<IIH', PREFIX, FRAMED, len(nm)) + nm
    for area, off, data in rs:
        for k in range(0, len(data), 0xFFFF):
            part = data[k:k + 0xFFFF]
            blob += struct.pack('<BIH', area, off + k, len(part)) + part
    blob += b'\xff'
    open(out_f, 'wb').write(blob)
    changed = sum(len(d) for _, _, d in rs)
    print('%s: %d runs, %d bytes changed (header %d), patch %d bytes, result md5 %s' %
          (out_f, len(rs), changed, sum(len(d) for a, _, d in rs if a == 0), len(blob),
           hashlib.md5(result).hexdigest()))
    if len(args) > 3:
        open(args[3], 'wb').write(result)


if __name__ == '__main__':
    main()
