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
    under the user's layers); game_vals: the game's own choices (they win); serial: the disc's (SLUS-00405), None
    when it couldn't be read (the pads left as they are); ports: the front ports (parse_probe) or None (not known:
    SNAC MemCard left as it is)."""
    game_vals = game_vals or {}
    out = {}
    pad = psx_pad(serial, gun)
    for key, front in sorted(PORTS.items()):
        if not serial:
            break                            # the disc couldn't be read: its pads not known, as the user has them
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


def show_running(items, last):
    """The menu while a game Auto started runs: its Pads and SNAC MemCard show what the core runs (last['values']),
    what Auto gave it (last['auto']) and the user's own setting in the note; note_plain: the item's own note, for a
    frontend to show once the user changes it (the value is then the user's)."""
    gave = last.get('auto') or last['values']
    for it in items:
        v = last['values'].get(it.get('key'))
        if v is None or it.get('value') == v:
            continue
        mine, a = it.get('value'), gave.get(it['key'])
        said = ('Auto set %s for this game while it runs' % v if a in (None, v) else
                'The core runs %s now (Auto started it with %s)' % (v, a))
        it['value'] = v
        it['note_plain'] = it.get('note') or ''       # (its note once the user changes it: the frontend's)
        it['note'] = '%s. Your setting: %s. %s' % (said, mine, it.get('note') or '')
    return items


def menu_item(system_vals, game_vals, game=None, last=None):
    """The Controllers page's Setup entry for the PSX menu. last: that game's last launch with Auto ({serial,
    values}: what the core got), said first in the note. live False: Coreshell's own, not one of the core's options
    (a frontend doesn't hand it to the running core: it counts from the next launch)."""
    in_game = bool(game) and SETTING in (game_vals or {})
    v = (game_vals or {}).get(SETTING) if in_game else (system_vals or {}).get(SETTING)
    note = ('Auto: each game gets the controller type it takes (NeGcon in Wipeout XL, a DualShock in Ape '
            'Escape...) and the memory card in the front port when one is there, for that game only. A '
            'controller you set for a game stays. Manual: the core as you set it.')
    if last:
        keys = [k for k in sorted(PORTS) + [MEMCARD] if last['values'].get(k)]
        got = ', '.join('%s %s' % (k, last['values'][k]) for k in keys)
        gave = last.get('auto') or last['values']
        takes = ' '.join(PAD[t] for t in psx(last.get('serial'))) or 'not listed'
        if all(gave.get(k) == last['values'][k] for k in keys):
            note = 'The core runs %s, set by Auto (this game takes: %s). %s' % (got, takes, note)
        else:
            note = 'The core runs %s now; Auto started it with %s (this game takes: %s). %s' % (
                got, ', '.join('%s %s' % (k, gave[k]) for k in keys if gave.get(k)), takes, note)
    return {'key': SETTING, 'page': 'Controllers', 'name': 'Setup', 'values': list(VALUES),
            'value': v if v in VALUES else 'Manual', 'default': 'Manual', 'from': 'game' if in_game else 'system',
            'per_game': in_game, 'per_display': False, 'live': False, 'note': note}
