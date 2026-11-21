#!/usr/bin/env python3
"""Coreshell - dump the live CONF_STR of installed cores (runs ON the MiSTer).

Loads each core through /dev/MiSTer_cmd, then reads the menu string Main got
from the core straight out of Main's memory (/proc/<pid>/mem, read-only). No
Main changes needed: works the same on stock Main, Phosphor's Main, MiSTer_RA
and any other fork. Each dump is saved with the core file's md5, so Coreshell
can tell later whether the installed build is the one the profile came from.

    python3 dump_confstr.py /media/fat/_Console/NES_20260823.rbf ...
    python3 dump_confstr.py --all [--resume]   (every console/computer/other/RA core)

Output: /media/fat/coreshell/dumps/<folder>__<rbf name>.json

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import glob
import hashlib
import signal
import json
import os
import re
import sys
import time

FAT = '/media/fat'
OUT = os.path.join(FAT, 'coreshell', 'dumps')
FIFO = '/dev/MiSTer_cmd'
MENU = os.path.join(FAT, 'menu.rbf')
FOLDERS = ['_Console', '_Computer', '_Other', '_Utility', '_RA_Cores/Cores',
           '_Console (Dual SDRAM)', '_Physical Disc Cores']

# a CONF_STR starts "NAME;" and ends "...;V,v<date>"
_HEAD = re.compile(rb'[A-Za-z0-9_][\x20-\x3a\x3c-\x7e]{0,30};')


def main_pids():
    pids = []
    for d in os.listdir('/proc'):
        if not d.isdigit():
            continue
        try:
            exe = os.readlink('/proc/%s/exe' % d)
        except OSError:
            continue
        if exe.startswith(FAT + '/') and os.path.basename(exe).lower().startswith('mister'):
            pids.append(int(d))
    return pids


def read_confstrs(pid):
    """Every CONF_STR-looking string in Main's writable memory.

    Searches for the ';V,v' version field every core ends with, then takes
    the whole 0-terminated string around it (a plain regex over Main's ~28 MB
    of memory is far too slow on the ARM)."""
    found = []
    with open('/proc/%d/maps' % pid) as maps:
        regions = []
        for line in maps:
            f = line.split()
            if len(f) < 2 or not f[1].startswith('rw'):
                continue
            name = f[5] if len(f) > 5 else ''
            if name and not (name.startswith(FAT) or name == '[heap]') or name == '/dev/mem':
                continue
            a, b = (int(x, 16) for x in f[0].split('-'))
            if b - a > 64 * 1024 * 1024:
                continue
            regions.append((a, b))
    with open('/proc/%d/mem' % pid, 'rb', 0) as mem:
        for a, b in regions:
            try:
                mem.seek(a)
                data = mem.read(b - a)
            except OSError:
                continue
            i = data.find(b';V,v')
            while i >= 0:
                a0 = data.rfind(b'\x00', 0, i) + 1
                b0 = data.find(b'\x00', i)
                b0 = len(data) if b0 < 0 else b0
                cand = data[a0:b0]
                if _HEAD.match(cand) and all(c >= 9 for c in cand):
                    found.append(cand.decode('ascii', 'replace'))
                i = data.find(b';V,v', b0)
    return found


def md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def send(cmd):
    # Main's own FIFO: while Coreshell has /dev/MiSTer_cmd (cmdpipe.py) Main reads it as /dev/MiSTer_cmd_main, and a
    # core loaded for its menu is loaded as it is, not started as a game
    import stat
    fifo = FIFO
    try:
        if stat.S_ISFIFO(os.stat(FIFO + '_main').st_mode):
            fifo = FIFO + '_main'
    except OSError:
        pass
    with open(fifo, 'w') as f:
        f.write(cmd + '\n')


def corename():
    try:
        with open('/tmp/CORENAME') as f:
            return f.read().strip()
    except OSError:
        return ''


def back_to_menu(wait=8.0, tries=3):
    """load_core of the menu, sent again if Main didn't act on it. A Main that has just started holds the core's
    menu string before it reads its command pipe, so the first one can be lost (/tmp/CORENAME says MENU when done)."""
    for _ in range(tries):
        send('load_core ' + MENU)
        end = time.time() + wait
        while time.time() < end:
            if corename() == 'MENU':
                return True
            time.sleep(0.5)
    print('the menu did not come back: load_core %s once more by hand' % MENU, flush=True)
    return False


def dump(rbf, wait=20.0, resend=8.0):
    prev = set()
    for pid in main_pids():
        try:
            prev.update(read_confstrs(pid))
        except OSError:
            pass
    before = corename()
    send('load_core ' + rbf)
    start = time.time()
    deadline = start + wait
    best = None
    resent = False
    time.sleep(1.5)
    while time.time() < deadline:
        if not resent and time.time() - start > resend and corename() == before:
            send('load_core ' + rbf)            # (lost, as back_to_menu says: sent once more)
            resent = True
        for pid in main_pids():
            try:
                hits = [c for c in read_confstrs(pid)
                        if not c.startswith('MENU;') and c not in prev]
            except OSError:
                continue
            if hits:
                best = max(hits, key=len)
                exe = os.readlink('/proc/%d/exe' % pid)
                break
        if best:
            break
        time.sleep(1.0)
    if not best:
        return None
    return {
        'rbf': os.path.relpath(rbf, FAT),
        'rbf_md5': md5(rbf),
        'main': os.path.relpath(exe, FAT),
        'confstr': best,
        'dumped': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }


def all_cores():
    out = []
    for d in FOLDERS:
        out += sorted(glob.glob(os.path.join(FAT, d, '*.rbf')))
        out += sorted(glob.glob(os.path.join(FAT, d, 'cores', '*.rbf')))
    return out


def _timeout(signum, frame):
    raise TimeoutError()


def main(argv):
    signal.signal(signal.SIGALRM, _timeout)
    skip_done = '--resume' in argv
    targets = all_cores() if '--all' in argv else [a for a in argv if not a.startswith('--')]
    os.makedirs(OUT, exist_ok=True)
    ok = bad = 0
    try:
        for rbf in targets:
            out = os.path.join(OUT, os.path.relpath(rbf, FAT).replace('/', '__')
                               .replace(' ', '_')[:-4] + '.json')
            if skip_done and os.path.exists(out):
                continue
            print('...  %s' % os.path.relpath(rbf, FAT), flush=True)
            signal.alarm(45)            # a core that hangs Main must not stall the run
            try:
                r = dump(rbf)
            except TimeoutError:
                r = None
            finally:
                signal.alarm(0)
            if r:
                with open(out, 'w') as f:
                    json.dump(r, f, indent=1)
                ok += 1
                print('OK   %-40s %s' % (r['rbf'], r['confstr'].split(';', 1)[0]), flush=True)
            else:
                bad += 1
                print('FAIL %s' % rbf, flush=True)
            time.sleep(0.5)
    finally:
        back_to_menu()
    print('done: %d dumped, %d failed' % (ok, bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
