"""Core patches at launch: Super MemCard's only (a stock core with the card's logic added, corepatch/).

Only Super MemCard patches are applied (<core>.memcard.<n>.cspatch: the PSX core reaching the real card in the front
port while a USB pad plays). No core is ever patched for the front port's controller: controllers are USB (or
Bluetooth) in Main, the frontend and every core, as stock (the front-port pad patch was dropped, Nov 2026).

A patch (coreshell/patches/*.cspatch) is made on a PC for one exact stock core file and holds only the added
logic. At launch, if one matches the installed stock core's MD5, bin/corepatch writes the patched core to
/tmp/coreshell/cores/<the stock file name> (once; kept while it matches) and the MGL's <rbf> points there:
Main's loader takes <rbf> relative to the SD card's root and picks the newest file with that name, so
../../tmp/coreshell/cores/NES finds NES_20260823.rbf there. No patch, or anything wrong: the stock core, as always.

Turned on by the user, per system (or per game): SuperCard, the Pad1 / Pad2 choice "USB pad + front-port card" in the
core's menu (shown only when a patch for the installed core is here; f3bandit, Nov 22 2026). Its patch makes the PSX
core's "SNAC MemCard: Virtual" mean the other way round: the controller from USB (a pad command goes to the core's own
pad), the memory card the real one in the front port. So a SuperCard launch runs the patched core with that port's Pad
= SNAC-portN and SNAC MemCard = Virtual, set for that launch only (the handoff's status bits): the core's own Pad and
SNAC MemCard options stay as the user left them (f3bandit, Nov 14 2026). Also on for every launch with "memcard_patch": true in coreshell/settings/coreshell.json
(off by default).

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


SUPERCARD_KEY = 'Super MemCard/SuperCard'          # port 1 (the key from before port 2 had one)
SUPERCARD2_KEY = 'Super MemCard/SuperCard port 2'
SUPERCARD_VALUES = ['Off', 'On']
# what the patched PSX core takes for "USB pad, real card" on a port: that Pad on the front port, SNAC MemCard
# "Virtual" (the patch turns the virtual-card switch round: the pad goes virtual, the card stays real)
PORTS = {'Pad1': (SUPERCARD_KEY, 'SNAC-port1'), 'Pad2': (SUPERCARD2_KEY, 'SNAC-port2')}
MEMCARD_KEY = 'SNAC MemCard'
SUPERCARD_SET = {'Pad1': 'SNAC-port1', MEMCARD_KEY: 'Virtual'}   # port 1 alone (kept for older callers)


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


def supercard_value(system_vals, game_vals, key=SUPERCARD_KEY):
    """A port's SuperCard choice from the settings layers (game first), 'Off' when none."""
    for src in (game_vals, system_vals):
        v = (src or {}).get(key)
        if v in SUPERCARD_VALUES:
            return v
    return 'Off'


def supercard_ports(system_vals, game_vals):
    """The Pad keys ('Pad1', 'Pad2') with SuperCard on."""
    return [pad for pad, (key, _) in sorted(PORTS.items()) if supercard_value(system_vals, game_vals, key) == 'On']


def supercard_set(pads):
    """The options a SuperCard launch sets for those ports."""
    out = {pad: PORTS[pad][1] for pad in pads}
    if pads:
        out[MEMCARD_KEY] = 'Virtual'
    return out


# In Phosphor's (any frontend's) menu SuperCard is one more Pad1 / Pad2 choice, named for what it does: the core's own
# labels (Pad "SNAC-portN", SNAC MemCard "Virtual") say the opposite of what the patched core does with them.
SUPERCARD_CHOICE = 'USB pad + front-port card'


def decorate_menu(items, paths, rbf_file, system_vals, game_vals, game_vals_keys=()):
    """The core's menu with SuperCard as a Pad1 and Pad2 choice (unchanged when there's no patch for the installed
    core). game_vals_keys: the SuperCard keys set for this game (their choice shows as the game's)."""
    if not available(paths, rbf_file):
        return items
    on = supercard_ports(system_vals, game_vals)
    for it in items:
        if it['key'] in PORTS:
            key = PORTS[it['key']][0]
            n = it['key'][-1]
            it['values'] = list(it['values']) + [SUPERCARD_CHOICE]
            it['note'] = ('%s: your USB controller is player %s and the saves go to the memory card in front port %s '
                          '(the PSX core is patched for it at launch). The next time the game starts.'
                          % (SUPERCARD_CHOICE, n, n))
            if it['key'] in on:
                it['value'] = SUPERCARD_CHOICE
                it['from'] = 'game' if key in game_vals_keys else 'system'
                it['per_game'] = key in game_vals_keys
                it['per_display'] = False
        elif it['key'] == MEMCARD_KEY and on:
            it['value'] = 'Real'
            it['note'] = 'The card in front port %s: %s "%s".' % (
                ' and '.join(p[-1] for p in on), ' and '.join(on), SUPERCARD_CHOICE)
    return items


def patched_rbf(paths, prof, rbf_file, log=None, on=False):
    """The <rbf> for a launch of prof's stock rbf_file with its patch applied, or None (no patch / turned off /
    anything failed: the stock core). on: SuperCard was picked for this launch."""
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
