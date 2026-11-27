"""The controllers each PS1 game takes (data/psx_controllers.tsv, made by tools/psx_controllers.py), for picking the
PSX core's Pad type per game. A game not in the list: [] (the core's own default, Dualshock).

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE). The list itself is made from
libretro-database: CC BY-SA 4.0 (THIRD_PARTY.md).
"""
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'psx_controllers.tsv')
# the list's names -> the PSX core's Pad values (systems/psx.json)
PAD = {'dualshock': 'Dualshock', 'negcon': 'NeGcon', 'guncon': 'GunCon', 'justifier': 'Justifier'}
GUNS = ('guncon', 'justifier')

_cache = {}      # path -> ((size, mtime), {serial: [types]})


def table(path=DATA):
    """{serial: [types, best first]}"""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (st.st_size, st.st_mtime)
    if path in _cache and _cache[path][0] == key:
        return _cache[path][1]
    out = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('#') or '\t' not in line:
                continue
            serial, types = line.rstrip('\n').split('\t')[:2]
            out[serial] = [t for t in types.split() if t in PAD]
    _cache[path] = (key, out)
    return out


def psx(serial, path=DATA):
    """The types a game takes, best first ('dualshock', 'negcon', 'guncon', 'justifier'), [] when it's not listed."""
    return list(table(path).get((serial or '').upper(), []))


def psx_pad(serial, gun=False, path=DATA):
    """The PSX core's Pad value for this game: a light gun the game takes when gun, else its best pad type; the
    core's default (Dualshock) for a game not listed or one that only takes a gun."""
    types = psx(serial, path)
    for t in types:
        if (t in GUNS) == bool(gun):
            return PAD[t]
    return PAD['dualshock']


# Auto (f3bandit, Nov 26 2026: docs/psx-auto-controller.md in test_bench): Coreshell picks the PSX core's Pad types
# and SNAC MemCard for each game at launch, for that launch only (the handoff; the core's .CFG untouched). A choice
# the user made for that game always wins. Off by default.
SETTING = 'Controllers/Setup'
VALUES = ['Manual', 'Auto']
PORTS = {'Pad1': 'SNAC-port1', 'Pad2': 'SNAC-port2'}
MEMCARD = 'SNAC MemCard'


def auto_on(system_vals, game_vals):
    """Auto turned on for this launch: the game's choice, else the system's."""
    for src in (game_vals, system_vals):
        v = (src or {}).get(SETTING)
        if v in VALUES:
            return v == 'Auto'
    return False


def parse_probe(lines):
    """smcd's PROBE answer -> {1: 'smc'|'ps1'|'empty', 2: ...}; None when it couldn't look (busy, no ports)."""
    out = {}
    for line in lines or ():
        f = line.split()
        if len(f) >= 3 and f[0] == 'port' and f[1] in ('1', '2'):
            out[int(f[1])] = f[2]
    return out if len(out) == 2 else None


def picks(values, game_vals, serial, ports=None, gun=False):
    """Auto's options for a PSX launch: {option: value}. values: the options as the user has them (profile defaults
    under the user's layers); game_vals: the game's own choices (they win); serial: the disc's (SLUS-00405);
    ports: the front ports (parse_probe) or None (not known: SNAC MemCard left as it is)."""
    game_vals = game_vals or {}
    out = {}
    pad = psx_pad(serial, gun)
    for key, front in sorted(PORTS.items()):
        if key in game_vals:
            continue
        cur = values.get(key, 'Dualshock')
        if cur in (front, 'Off'):
            continue                         # the front port's own controller, or no controller: as the user has it
        if cur != pad:
            out[key] = pad
    if ports is not None and MEMCARD not in game_vals:
        card = any(ports.get(n) in ('smc', 'ps1') for n in (1, 2))
        want = 'Real' if card else 'Virtual'
        if values.get(MEMCARD, 'Virtual') != want:
            out[MEMCARD] = want
    return out


def menu_item(system_vals, game_vals, game=None):
    """The Controllers page's Setup entry for the PSX menu."""
    in_game = bool(game) and SETTING in (game_vals or {})
    v = (game_vals or {}).get(SETTING) if in_game else (system_vals or {}).get(SETTING)
    return {'key': SETTING, 'page': 'Controllers', 'name': 'Setup', 'values': list(VALUES),
            'value': v if v in VALUES else 'Manual', 'default': 'Manual', 'from': 'game' if in_game else 'system',
            'per_game': in_game, 'per_display': False,
            'note': ('Auto: each game gets the controller type it takes (NeGcon in Wipeout XL, a DualShock in Ape '
                     'Escape...) and the memory card in the front port when one is there, for that game only. A '
                     'controller you set for a game stays. Manual: the core as you set it.')}
