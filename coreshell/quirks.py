"""What Coreshell knows about particular cores and systems that their own menus don't say: kept as data
(quirks/quirks.json), so a fix for an odd core ships with `coreshell update` as a data file, with no code change.

Each table has its built-in value here (what this version of the code was written with); quirks/quirks.json, when
there is one, adds to it and wins where they differ:
    ra_console       {RA core file: RetroAchievements console id}
    ra_by_ext        {"<core>/<EXT>": console id}: a game's extension that's another console on that core
    core_folders     the folders on the SD card whose cores Coreshell keeps profiles of     (refresh.py)
    core_skip        a regex: core files with nothing to set, or ours (menu, memtest, ...)
    card_no_picker   systems the card itself keeps memory cards for (no card picker)     (supermemcard.py)

The rest of what Coreshell knows of each core is data already: its profile (systems/*.json, made from its own menu,
by its md5), the Super MemCard driver's own tables in smcd, the core patches (coreshell/patches, by the stock core's
md5) and the Mains it has shim builds for (mains/mains.json, by md5). `coreshell update` brings all of them.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import json
import os
import re

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(HOME, 'quirks', 'quirks.json')

BUILTIN = {
    'ra_console': {
        'NES': 7, 'SNES': 3, 'MegaDrive': 1, 'SMS': 11, 'Gameboy': 4, 'GBA': 5, 'N64': 2, 'PSX': 12,
        'MegaCD': 9, 'NeoGeo': 27, 'TurboGrafx16': 8, 'Atari7800': 51, 'S32X': 10, 'Saturn': 39,
        'VirtualBoy': 28,
    },
    'ra_by_ext': {
        'NES/FDS': 81, 'SMS/GG': 15, 'Gameboy/GBC': 6, 'Atari7800/A26': 25,
        'TurboGrafx16/CUE': 76, 'TurboGrafx16/CHD': 76,
    },
    'core_folders': ['_Console', '_Computer', '_Other', '_Utility', '_RA_Cores/Cores', '_Console (Dual SDRAM)',
                     '_Physical Disc Cores'],
    'core_skip': r'^(menu|memtest|ss1_|chip-id|linker)',
    'card_no_picker': ['PSX'],
}

_cache = {}


def _load(path):
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (st.st_size, st.st_mtime)
    if _cache.get(path, (None,))[0] != key:
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}                    # (a broken file: the built-in tables, not a crash)
        _cache[path] = (key, data if isinstance(data, dict) else {})
    return _cache[path][1]


def get(name, path=None):
    """A table: the built-in one with quirks.json's added (dicts merged, lists joined, anything else replaced)."""
    base = BUILTIN.get(name)
    extra = _load(path or FILE).get(name)
    if extra is None:
        return base
    if isinstance(base, dict) and isinstance(extra, dict):
        return dict(base, **extra)
    if isinstance(base, list) and isinstance(extra, list):
        return base + [x for x in extra if x not in base]
    return extra


def ra_console(core, ext, path=None):
    return get('ra_by_ext', path).get('%s/%s' % (core, ext), get('ra_console', path).get(core))


def core_skip(path=None):
    return re.compile(get('core_skip', path), re.I)


def card_no_picker(path=None):
    return set(get('card_no_picker', path))
