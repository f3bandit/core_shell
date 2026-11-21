"""Coreshell - a game's ID, for the memory card (Super MemCard's SELECT puts that game's own card on the bus).

PSX: the product code from the disc's SYSTEM.CNF (BOOT = cdrom:\\SLUS_010.41;1 -> SLUS-01041), read from a CUE/BIN,
a BIN or an ISO, with disc.py's parser (the one the drive's discs use). CHD images aren't read here: the frontend's
library has their serial and passes it as the launch's game_id.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import os
import re

from . import disc

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
        return disc._psx_serial(pvd, 0, read)
    except (OSError, ValueError, KeyError):
        return None
