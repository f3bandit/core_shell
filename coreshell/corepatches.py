"""Core patches at launch: Super MemCard's only (a stock core with the card's logic added, corepatch/).

Only Super MemCard patches are applied (<core>.memcard.<n>.cspatch: the PSX core reaching the real card in the front
port while a USB pad plays). No core is ever patched for the front port's controller: controllers are USB (or
Bluetooth) in Main, the frontend and every core, as stock (the front-port pad patch was dropped, Nov 2026).

A patch (coreshell/patches/*.cspatch) is made on a PC for one exact stock core file and holds only the added
logic. At launch, if one matches the installed stock core's MD5, bin/corepatch writes the patched core to
/tmp/coreshell/cores/<the stock file name> (once; kept while it matches) and the MGL's <rbf> points there:
Main's loader takes <rbf> relative to the SD card's root and picks the newest file with that name, so
../../tmp/coreshell/cores/NES finds NES_20260823.rbf there. No patch, or anything wrong: the stock core, as always.

With the patch the PSX core's own options mean what they say (f3bandit, Nov 27 2026): SNAC MemCard "Real" is the
memory cards in the front ports under any Pad type (a USB NeGcon, DualShock... from Main, the card from the front port);
a Pad set to Off keeps that port's virtual card; Pad = SNAC-portN (the front port's own controller), SNAC MemCard
"Virtual" and multitap are stock. So the patched core is used for a launch whenever the core will run with SNAC MemCard
= Real and a Pad that is neither SNAC-portN nor Off (its .CFG with what Coreshell sets over it): nothing extra is set,
the user picks the Pad type and SNAC MemCard in the core's menu as usual. Also on for every launch with
"memcard_patch": true in coreshell/settings/coreshell.json (off by default).

Before Nov 27 2026 SuperCard was a Pad1 / Pad2 choice ("USB pad + front-port card", settings keys
'Super MemCard/SuperCard' and '... port 2'), digital pads only: migrate_supercard() turns those picks into SNAC MemCard
= Real with the Pad they had (SNAC-portN -> Dualshock).

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


MEMCARD_KEY = 'SNAC MemCard'
# a Pad's front-port value (the front port's own controller): with it, or Off, that port is stock
PORTS = {'Pad1': 'SNAC-port1', 'Pad2': 'SNAC-port2'}
# the old SuperCard picks (a Pad choice before Nov 27 2026), migrated by migrate_supercard()
OLD_KEYS = {'Super MemCard/SuperCard': 'Pad1', 'Super MemCard/SuperCard port 2': 'Pad2'}


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


def available(paths, rbf_file):
    """A Super MemCard patch for this exact stock core is here, and the tool to apply it."""
    if not rbf_file or not os.access(os.path.join(paths.root, EXE), os.X_OK):
        return False
    try:
        return md5_of(rbf_file) in index(paths)
    except OSError:
        return False


def needed(values):
    """The core will run with SNAC MemCard = Real and a Pad that isn't the front port's controller or Off: the
    patched core (values: the options as the core will have them; a Pad not there is the core's default, Dualshock)."""
    if values.get(MEMCARD_KEY) != 'Real':
        return False
    return any(values.get(pad, 'Dualshock') not in (front, 'Off') for pad, front in PORTS.items())


def migrate_supercard(vals, below=None):
    """A settings layer with the old SuperCard picks -> SNAC MemCard = Real with the Pad they had (SNAC-portN ->
    Dualshock). below: the layers under it, merged (a game's "Off" under a system "On" keeps the game's card
    virtual). Returns the new layer, or None when there was nothing to migrate."""
    if not any(k in vals for k in OLD_KEYS):
        return None
    out = {k: v for k, v in vals.items() if k not in OLD_KEYS}
    on = [pad for k, pad in sorted(OLD_KEYS.items(), key=lambda kv: kv[1]) if vals.get(k) == 'On']
    if on:
        out[MEMCARD_KEY] = 'Real'
        for pad in on:
            if out.get(pad, (below or {}).get(pad)) == PORTS[pad]:
                out[pad] = 'Dualshock'
    elif any((below or {}).get(k) == 'On' for k in OLD_KEYS) and MEMCARD_KEY not in out:
        out[MEMCARD_KEY] = 'Virtual'
    return out


def decorate_menu(items, paths, rbf_file):
    """SNAC MemCard's note says what "Real" does with the patch (unchanged when there's no patch for the installed
    core)."""
    if not available(paths, rbf_file):
        return items
    for it in items:
        if it['key'] == MEMCARD_KEY:
            it['note'] = ('Real: the memory cards in the front ports, with any controller type from USB (a Pad set to '
                          'Off keeps its virtual card). The PSX core is patched for it at launch.')
    return items


def patched_rbf(paths, prof, rbf_file, log=None, on=False):
    """The <rbf> for a launch of prof's stock rbf_file with its patch applied, or None (no patch / turned off /
    anything failed: the stock core). on: the launch needs it (needed())."""
    log = log or (lambda m: None)
    if not rbf_file or not (on or settings(paths)['memcard_patch']):
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
