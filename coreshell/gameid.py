"""Coreshell - a game's ID, for the memory card (Super MemCard's SELECT puts that game's own card on the bus).

PSX: the product code from the disc's SYSTEM.CNF (BOOT = cdrom:\\SLUS_010.41;1 -> SLUS-01041), read from a CUE/BIN,
a BIN or an ISO. CHD images aren't read here: the frontend's
library has their serial and passes it as the launch's game_id.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import os
import re

_TRACK = re.compile(r'^\s*TRACK\s+\d+\s+(MODE1/2048|MODE1/2352|MODE2/2352|MODE2/2336)', re.I)
_FILE = re.compile(r'^\s*FILE\s+"?([^"]+?)"?\s+BINARY', re.I)
_LAYOUT = {'MODE1/2048': (2048, 0), 'MODE1/2352': (2352, 16), 'MODE2/2352': (2352, 24), 'MODE2/2336': (2336, 8)}


def _first_data_track(cue):
    """(bin file, sector size, user data offset) of the CUE's first data track."""
    here = os.path.dirname(cue)
    with open(cue, encoding='utf-8', errors='replace') as f:
        lines = f.read().splitlines()
    current = None
    for line in lines:
        m = _FILE.match(line)
        if m:
            current = os.path.join(here, m.group(1))
            continue
        m = _TRACK.match(line)
        if m and current:
            size, off = _LAYOUT[m.group(1).upper()]
            return current, size, off
    return None


def _reader(path, size, off):
    def read(lba, n=1):
        out = b''
        with open(path, 'rb') as f:
            for k in range(n):
                f.seek((lba + k) * size + off)
                out += f.read(2048)
        return out
    return read


def psx(path):
    """SLUS-01041 for a PSX game's CUE, BIN or ISO; None if it can't be read (no SYSTEM.CNF, another format)."""
    if not path:
        return None
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == '.cue':
            t = _first_data_track(path)
            if not t:
                return None
            read = _reader(*t)
        elif ext == '.iso':
            read = _reader(path, 2048, 0)
        elif ext == '.bin':
            with open(path, 'rb') as f:
                head = f.read(16)
            # a raw 2352-byte sector starts with the sync pattern; mode 2 in byte 15
            if head[:12] == b'\x00' + b'\xff' * 10 + b'\x00':
                read = _reader(path, 2352, 24 if head[15] == 2 else 16)
            else:
                read = _reader(path, 2048, 0)
        else:
            return None
        pvd = read(16)
        if pvd[1:6] != b'CD001':
            return None
        return _psx_serial(pvd, read)
    except (OSError, ValueError, KeyError):
        return None


def _psx_serial(pvd, read):
    """SCUS-94351 from SYSTEM.CNF's BOOT line (ISO 9660 root directory). read(lba, n): the image's data track,
    2048 bytes of user data a sector."""
    root = pvd[156:190]
    lba, size = int.from_bytes(root[2:6], 'little'), int.from_bytes(root[10:14], 'little')
    d = read(lba, max(1, min(4, size // 2048)))
    i = 0
    while i < len(d):
        n = d[i]
        if not n:
            i = (i // 2048 + 1) * 2048
            continue
        name = d[i + 33:i + 33 + d[i + 32]]
        if name.upper().startswith(b'SYSTEM.CNF'):
            cnf = read(int.from_bytes(d[i + 2:i + 6], 'little')).decode('ascii', 'replace')
            return psx_code_from_cnf(cnf)
        i += n
    return None


def psx_code_from_cnf(cnf):
    """The product code from SYSTEM.CNF's BOOT line, as phosphord's ident.c (psx_boot) reads it: the boot file's
    name after its last \\ : or /, up to ; CR LF or a space."""
    b = cnf.find('BOOT')
    if b < 0:
        return None
    eq = cnf.find('=', b)
    if eq < 0:
        return None
    name = cnf[eq + 1:]
    k = 0
    while k < len(name) and name[k] not in '\n\r;':
        if name[k] in '\\:/':
            name, k = name[k + 1:], 0
            continue
        k += 1
    name = name.lstrip(' ')
    e = 0
    while e < len(name) and name[e] not in ';\r\n ':
        e += 1
    name = name[:e]
    return psx_code(name) if name else None


def psx_code(raw):
    """SLUS_010.41 -> SLUS-01041: phosphord's ident.c psx_serial, ported (the same code everywhere: the library,
    smcd, the card). The letters, a dash, the rest; a boot file with an extra letter (SLUSP012.06) drops it."""
    t = ''.join(c.upper() for c in raw if c.isalnum())[:31]
    a = 0
    while a < len(t) and t[a].isalpha():
        a += 1
    if a == 5 and a < len(t) and t[a].isdigit():
        t, a = t[:4] + t[5:], 4
    if 3 <= a <= 4 and t[a:]:
        return '%s-%s' % (t[:a], t[a:])
    return t or None
