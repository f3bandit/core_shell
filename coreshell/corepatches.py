"""Core patches at launch: Super MemCard's only (a stock core with the card's logic added, corepatch/).

Only Super MemCard patches are applied (<core>.memcard.<n>.cspatch: the PSX core reaching the real card in the front
port while a USB pad plays). No core is ever patched for the front port's controller: controllers are USB (or
Bluetooth) in Main, the frontend and every core, as stock (the front-port pad patch was dropped, Nov 2026).

A patch (coreshell/patches/*.cspatch) is made on a PC for one exact stock core file and holds only the added
logic. At launch, if one matches the installed stock core's MD5, bin/corepatch writes the patched core to
/tmp/coreshell/cores/<the stock file name> (once; kept while it matches) and the MGL's <rbf> points there:
Main's loader takes <rbf> relative to the SD card's root and picks the newest file with that name, so
../../tmp/coreshell/cores/NES finds NES_20260823.rbf there. No patch, or anything wrong: the stock core, as always.

Settings (coreshell/settings/coreshell.json): "memcard_patch": true/false (default false: opt-in, a launch is the
stock core unless the user turned the Super MemCard patch on).

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import glob
import hashlib
import json
import os
import subprocess

EXE = 'coreshell/bin/corepatch'
PATCHES = 'coreshell/patches'
OUT = '/tmp/coreshell/cores'
SETTINGS = 'coreshell/settings/coreshell.json'

_md5_cache = {}      # path -> ((size, mtime), md5)
_index = {}          # (dir mtime) -> {stock md5: (patch path, result md5)}


def settings(paths):
    try:
        with open(os.path.join(paths.root, SETTINGS)) as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    return {'memcard_patch': bool(s.get('memcard_patch', False))}


def md5_of(path):
    st = os.stat(path)
    key = (st.st_size, st.st_mtime)
    c = _md5_cache.get(path)
    if c and c[0] == key:
        return c[1]
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    _md5_cache[path] = (key, h.hexdigest())
    return _md5_cache[path][1]


def index(paths):
    """{stock md5: (patch file, result md5)}"""
    d = os.path.join(paths.root, PATCHES)
    try:
        stamp = os.stat(d).st_mtime
    except OSError:
        return {}
    if stamp in _index:
        return _index[stamp]
    out = {}
    for p in glob.glob(os.path.join(d, '*.memcard.*cspatch')):   # (Super MemCard's only: nothing else is patched)
        try:
            with open(p, 'rb') as f:
                head = f.read(40)
        except OSError:
            continue
        if len(head) == 40 and head[:8] == b'CSPATCH1':
            out[head[8:24].hex()] = (p, head[24:40].hex())
    _index.clear()
    _index[stamp] = out
    return out


def patched_rbf(paths, prof, rbf_file, log=None):
    """The <rbf> for a launch of prof's stock rbf_file with its patch applied, or None (no patch / turned off /
    anything failed: the stock core)."""
    log = log or (lambda m: None)
    if not rbf_file or not settings(paths)['memcard_patch']:
        return None
    exe = os.path.join(paths.root, EXE)
    if not os.access(exe, os.X_OK):
        return None
    try:
        hit = index(paths).get(md5_of(rbf_file))
    except OSError:
        return None
    if not hit:
        return None
    patch, result = hit
    name = os.path.basename(rbf_file)
    prefix = os.path.basename(prof['rbf'])
    out = os.path.join(OUT, name)
    try:
        os.makedirs(OUT, exist_ok=True)
        for old in glob.glob(os.path.join(OUT, prefix + '*.rbf')):   # only this core in the loader's way
            if old != out:
                os.remove(old)
        if not (os.path.exists(out) and md5_of(out) == result):
            r = subprocess.run([exe, patch, rbf_file, out], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=30)
            if r.returncode:
                log('core patch %s: %s' % (os.path.basename(patch), r.stdout.decode('utf-8', 'replace').strip()))
                return None
    except (OSError, subprocess.SubprocessError) as e:
        log('core patch %s: %s' % (os.path.basename(patch), e))
        return None
    log('core patch: %s' % os.path.basename(patch))
    return os.path.join(os.path.relpath(OUT, paths.root), prefix)
