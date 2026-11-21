"""Coreshell - system profiles.

A profile is everything Coreshell needs to run one system on a stock core:
which .rbf to load, where its games and saves live, which file slot takes the
ROM, and the menu (options by page, name and value text).

Options are stored by NAME and VALUE TEXT, never by status bits. Main_MiSTer
(Oct 2026 and newer) maps MGL <option name= value=> to bits against the live
core, so a core update that moves its bits doesn't break a profile. The bits
are kept in the profile only for the CFG fallback on older Main builds and
for checking a profile against a newer core.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import hashlib
import json
import os
import re

from . import confstr as cs

PROFILE_VERSION = 1


_NOT_ROM = re.compile(r'bios|boot|palette|border|lut|memory card|exe|spc|nsf|filter|system rom|font', re.I)


def game_slots(core):
    """Every loader a game can go into (carts, discs, tapes, disks), in
    menu order, BIOS/boot/palette loaders and memory cards left out."""
    out = []
    for f in core['files'] + core['mounts']:
        if f['exts'] and not _NOT_ROM.search(f['label']) and \
                not set(e.upper() for e in f['exts']) <= {'SAV', 'MCD', 'MCR', 'SRM'}:
            out.append({'index': f['index'], 'type': f['type'], 'exts': f['exts'],
                        'label': f['label']})
    return out


def guess_rom_slot(core):
    """The game slot: first loader with extensions that isn't a BIOS, boot
    ROM, palette or similar (systems with a disc slot set rom_slot instead)."""
    for f in core['files']:
        if f['exts'] and not _NOT_ROM.search(f['label']):
            return f
    return next((f for f in core['files'] if f['exts']), None)


def page_title(core, page):
    return core['pages'].get(page, '') if page else ''


def build(confstr_text, system, name, rbf, games_dir=None, saves_dir=None,
          rom_slot=None, source=None, overrides=None, setname=None, variant='stock'):
    """CONF_STR text + system info -> profile dict."""
    core = cs.parse(confstr_text)
    cname = core['core_name']
    # an MGL <setname same_dir="1"> renames the core (RA_SNES -> MiSTer_RA via
    # MiSTer.ini) but keeps the original games/saves folders
    games_dir = games_dir or 'games/' + cname
    saves_dir = saves_dir or 'saves/' + cname

    files = core['files']
    if rom_slot is None:
        rom = guess_rom_slot(core)
        if rom is None and game_slots(core):
            rom = game_slots(core)[0]          # disk/CD-only systems: first mount slot
    else:
        kind, index = rom_slot
        pool = files if kind == 'f' else core['mounts']
        rom = next((f for f in pool if f['index'] == index and f['exts']), None)

    menu = []
    for o in core['options']:
        menu.append({
            'page': page_title(core, o['page']),
            'name': o['name'],
            'values': o['values'],
            'default': o['values'][0],
            'bits': o['bits'],
            'core_flags': o['flags'],
        })

    # unique keys: a core can list the same name twice (variants shown or
    # hidden by H/h flags); the second one becomes 'Name #2'. Main matches
    # MGL options by name, so a name it would call ambiguous is marked
    # mgl_ok=False and only exact mode (bits in the .CFG) can set it.
    seen = {}
    for o in menu:
        base = (o['page'] + '/' if o['page'] else '') + o['name']
        seen[base.lower()] = seen.get(base.lower(), 0) + 1
        o['key'] = base if seen[base.lower()] == 1 else '%s #%d' % (base, seen[base.lower()])
    for o in menu:
        same = [x for x in menu if x['name'].lower() == o['name'].lower()
                and (not o['page'] or x['page'].lower() == o['page'].lower())]
        o['mgl_ok'] = len(same) == 1

    cfg = (setname or cname) + ('_v%d' % core['config_ver'] if core['config_ver'] else '') + '.CFG'
    prof = {
        'coreshell_profile': PROFILE_VERSION,
        'system': system,
        'variant': variant,
        'name': name,
        'rbf': rbf,
        'setname': setname,
        'core_name': cname,
        'config_file': cfg,
        'games_dir': games_dir,
        'saves_dir': saves_dir,
        'rom': rom and {'index': rom['index'], 'type': rom['type'], 'exts': rom['exts']},
        'slots': game_slots(core),
        'files': files,
        'mounts': core['mounts'],
        'triggers': core['triggers'],
        'cheats': core['cheats'],
        'menu': menu,
        'hide': [],
        'source': dict(source or {}, confstr_sha1=hashlib.sha1(
            confstr_text.encode('utf-8')).hexdigest()),
    }
    if overrides:
        apply_overrides(prof, overrides)
    return prof


def apply_overrides(prof, ov):
    """Hand-tuned overlay on a generated profile.

    {"name": "...", "hide": ["Page/Option", "Option"], "show": [debug options to keep],
     "defaults": {"Page/Option": "Value"},
     "rom": {...}, "games_dir": "...", "saves_dir": "..."}
    """
    for k in ('name', 'games_dir', 'saves_dir', 'rbf', 'rom'):
        if k in ov:
            prof[k] = ov[k]
    prof['hide'] = list(ov.get('hide', prof.get('hide', [])))
    if 'show' in ov:
        prof['show'] = list(ov['show'])
    for key, val in ov.get('defaults', {}).items():
        opt = find_option(prof, key)
        if opt and val in opt['values']:
            opt['default'] = val
    return prof


def option_key(opt):
    return opt.get('key') or (opt['page'] + '/' if opt['page'] else '') + opt['name']


def find_option(prof, key):
    """By key ('Audio & Video/Aspect Ratio', 'Mapper #2'), or by bare name
    when only one option has it."""
    k = key.lower()
    hits = [o for o in prof['menu'] if option_key(o).lower() == k]
    if not hits and '/' not in key:
        hits = [o for o in prof['menu'] if o['name'].lower() == k]
    return hits[0] if len(hits) == 1 else None


# Developer aids in core menus, hidden from the unified menu on every core unless a profile's
# "show" lists them: a "Debug" page, debug overlays, test patterns. Hacks, DIP switches,
# service and self-test options are settings players use, and stay.
JUNK_PAGE = re.compile(r'^debug( menu)?$', re.I)
JUNK_NAME = re.compile(r'^(debug (dots|display|overlay)|display debug|cc test line|test pattern)$', re.I)


def is_junk(o):
    return bool(JUNK_PAGE.match(o['page'] or '') or JUNK_NAME.match(o['name'].strip()))


def visible_menu(prof):
    """The unified menu: every option the profile doesn't hide, debug aids left out."""
    hidden = {h.lower() for h in prof.get('hide', [])}
    shown = {h.lower() for h in prof.get('show', [])}

    def named(o, names):
        return option_key(o).lower() in names or o['name'].lower() in names
    return [o for o in prof['menu']
            if not named(o, hidden) and (named(o, shown) or not is_junk(o))]


def diff(old, new):
    """What changed between two profiles of one core.

    [{'what', 'option'?, ...}]. Settings are kept by option name and the profile
    is rebuilt, so moved bits or a renamed config file carry over; 'breaking':
    True only where a saved setting is lost (its option or value is gone, or a
    game file type the core took is no longer taken)."""
    out = []
    for o in old['menu']:
        n = find_option(new, option_key(o))
        if not n:
            out.append({'what': 'option removed', 'option': option_key(o), 'breaking': True})
            continue
        gone = [v for v in o['values'] if v not in n['values']]
        if gone:
            out.append({'what': 'values removed', 'option': option_key(o), 'values': gone,
                        'breaking': True})
        if n['bits'] != o['bits']:
            out.append({'what': 'bits moved', 'option': option_key(o), 'from': o['bits'],
                        'to': n['bits'], 'breaking': False})
    for n in new['menu']:
        if not find_option(old, option_key(n)):
            out.append({'what': 'option added', 'option': option_key(n), 'breaking': False})
    if old.get('config_file') != new.get('config_file'):
        out.append({'what': 'config file renamed',
                    'from': old.get('config_file'), 'to': new.get('config_file'), 'breaking': False})
    ro, rn = old.get('rom') or {}, new.get('rom') or {}
    if (ro.get('type'), ro.get('index')) != (rn.get('type'), rn.get('index')):
        out.append({'what': 'game slot moved', 'from': ro, 'to': rn, 'breaking': False})
    elif sorted(ro.get('exts', [])) != sorted(rn.get('exts', [])):
        out.append({'what': 'game file types changed', 'from': ro.get('exts'),
                    'to': rn.get('exts'), 'breaking': bool(set(ro.get('exts', [])) - set(rn.get('exts', [])))})
    return out


def load(path):
    with open(path, 'r', encoding='utf-8') as f:
        prof = json.load(f)
    if prof.get('coreshell_profile') != PROFILE_VERSION:
        raise ValueError('%s: profile version %r, Coreshell reads %d'
                         % (path, prof.get('coreshell_profile'), PROFILE_VERSION))
    return prof


def save(prof, path):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path + '.tmp', 'w', encoding='utf-8') as f:     # (a profile cut off by a power loss breaks its core)
        json.dump(prof, f, indent=1, ensure_ascii=False)
        f.write('\n')
    os.replace(path + '.tmp', path)
