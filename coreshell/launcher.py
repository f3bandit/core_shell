"""Coreshell - launcher.

Launch = settings -> core config -> MGL -> /dev/MiSTer_cmd. Main_MiSTer does
all the hardware work; Coreshell only writes files and sends one command, so
no Main code lives here.

Two ways settings reach the core:

  exact mode    the installed .rbf is the build the profile was made from, so
                the bit layout is known: Coreshell writes every setting into
                the core's .CFG, and Main loads it when the core starts.
  name mode     the .rbf is newer or unknown: Coreshell leaves the .CFG alone
                and sends changed settings as MGL <option name= value=>, which
                Main matches against the live core by name (max 16 per launch,
                Main's MGL_MAX_OPTIONS). Nothing can land on the wrong bit.
                Needs a Main built after Oct 20 2026 (the MGL option commit);
                release 20261010 and older don't have it.
  core mode     neither: the core starts with its own saved settings and the
                launch says why, so a stale profile never writes wrong bits.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import glob
import json
import os
import re
import shutil
import time
from xml.sax.saxutils import quoteattr

from . import profile as pf
from . import corepatches as cp

MGL_MAX_OPTIONS = 16
CMD_FIFO = '/dev/MiSTer_cmd'


class Paths(object):
    def __init__(self, root='/media/fat'):
        self.root = root
        self.home = os.path.join(root, 'coreshell')
        self.systems = os.path.join(self.home, 'systems')
        self.settings = os.path.join(self.home, 'settings')
        self.launch = os.path.join(self.home, 'launch')
        self.backups = os.path.join(self.home, 'backups')
        self.config = os.path.join(root, 'config')

    def abs(self, rel):
        return rel if rel.startswith('/') else os.path.join(self.root, rel)


# ------------------------------------------------------------------ settings

def _safe(name):
    return re.sub(r'[\\/:*?"<>|]', '_', name)


def settings_path(paths, system, game=None, display=None):
    tail = ('@' + _safe(display)) if display else ''
    if game:
        return os.path.join(paths.settings, system, _safe(game) + tail + '.json')
    return os.path.join(paths.settings, system + tail + '.json')


def read_settings(paths, system, game=None, display=None):
    import json
    p = settings_path(paths, system, game, display)
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_settings(paths, system, values, game=None, display=None):
    import json
    p = settings_path(paths, system, game, display)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(values, f, indent=1, ensure_ascii=False)
    os.replace(tmp, p)


def layers(paths, key, game=None, display=None):
    """Settings layers, lowest first: system, system for this display,
    game, game for this display."""
    out = [read_settings(paths, key)]
    if display:
        out.append(read_settings(paths, key, None, display))
    if game:
        out.append(read_settings(paths, key, game))
        if display:
            out.append(read_settings(paths, key, game, display))
    return out


def effective(prof, system_vals, game_vals, *more):
    """Profile defaults <- each layer in order (system, display, game...).

    Returns {option_key: value} for every option in the profile.
    """
    out = {}
    for o in prof['menu']:
        out[pf.option_key(o)] = o['default']
    for src in (system_vals, game_vals) + more:
        for k, v in (src or {}).items():
            o = pf.find_option(prof, k)
            if o and v in o['values']:
                out[pf.option_key(o)] = v
    return out


# ------------------------------------------------------------- core config

def installed_rbf(paths, rbf):
    """'_Console/SNES' -> newest '/media/fat/_Console/SNES_20260901.rbf' or None."""
    base = paths.abs(rbf)
    hits = sorted(glob.glob(base + '_*.rbf')) + glob.glob(base + '.rbf')
    return hits[-1] if hits else None


_MAIN_CAPS = {}

ALTCFG_PHYS = 0x1FFFF000        # Main's shared page; altcfg() keeps the INI slot at +0xF04


def alt_ini_names(paths):
    """Main's alternate INIs: the first 3 MiSTer_*.ini files in directory
    order, then sorted (cfg_get_name in Main's cfg.cpp)."""
    names = []
    try:
        for n in os.listdir(paths.root):
            if len(names) == 3:
                break
            if n.lower().startswith('mister_') and n.lower().endswith('.ini'):
                names.append(n)
    except OSError:
        pass
    return sorted(names, key=str.lower)


def active_ini_slot(mem='/dev/mem'):
    """0 = MiSTer.ini, 1-3 = alternate INI, as Main's altcfg() stores it."""
    import mmap
    try:
        fd = os.open(mem, os.O_RDONLY | getattr(os, 'O_SYNC', 0))
    except OSError:
        return 0
    try:
        m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ, offset=ALTCFG_PHYS)
        par = bytes(m[0xF04:0xF08])
        m.close()
    except (OSError, ValueError):
        return 0
    finally:
        os.close(fd)
    return par[3] if par[:3] == b'\x34\x99\xba' and 0 <= par[3] <= 3 else 0


def set_ini_slot(slot, mem='/dev/mem'):
    """Write the INI slot where Main's altcfg() keeps it (34 99 BA <slot>)."""
    import mmap
    fd = os.open(mem, os.O_RDWR | getattr(os, 'O_SYNC', 0))
    try:
        m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=ALTCFG_PHYS)
        m[0xF04:0xF08] = bytes([0x34, 0x99, 0xBA, slot])   # (no flush: /dev/mem refuses msync; O_SYNC writes through)
        m.close()
    finally:
        os.close(fd)


def ini_list(paths):
    """The INIs Main can switch between, in its order: MiSTer.ini, then up to 3 MiSTer_*.ini."""
    names = ['MiSTer.ini'] + alt_ini_names(paths)
    return [n for n in names if os.path.exists(os.path.join(paths.root, n))]


def set_ini(paths, ini, fifo=None, mem='/dev/mem', corename='/tmp/CORENAME'):
    """Switch MiSTer to another INI, as the OSD's INI choice does (Main's user_io_set_ini):
    the slot into Main's shared page, then the menu core loaded again so Main restarts
    reading that INI (video mode, scaler, everything). From the menu only."""
    names = ['MiSTer.ini'] + alt_ini_names(paths)
    match = [i for i, n in enumerate(names) if n.lower() == ini.lower()]
    if not match or not os.path.exists(os.path.join(paths.root, names[match[0]])):
        raise KeyError('%s is not one of the INIs Main can switch to: %s' % (ini, ', '.join(ini_list(paths))))
    try:
        with open(corename) as f:
            if f.read().strip().upper() not in ('', 'MENU'):
                raise RuntimeError('a core is running: switch INI from the menu')
    except OSError:
        pass
    set_ini_slot(match[0], mem)
    from .mains import bridge_replaced
    bridge_replaced(paths.root)   # (a Main replaced while it runs: mains.py)
    from .cmdpipe import write_main
    write_main(fifo or CMD_FIFO, 'load_core %s' % os.path.join(paths.root, 'menu.rbf'))
    return names[match[0]]


def active_ini(paths, slot=None):
    slot = active_ini_slot() if slot is None else slot
    names = alt_ini_names(paths)
    if slot and slot <= len(names):
        return names[slot - 1]
    return 'MiSTer.ini'


def display_set(paths, ini):
    """Which settings set goes with this INI: displays.json maps INI files to
    set names ({"MiSTer_RGsB.ini": "crt", "MiSTer.ini": "hdmi"}); an INI not
    listed is its own set, named after the file."""
    import json
    try:
        with open(os.path.join(paths.home, 'displays.json'), encoding='utf-8') as f:
            m = json.load(f)
    except (OSError, ValueError):
        m = {}
    for k, v in m.items():
        if k.lower() == ini.lower():
            return v
    return os.path.splitext(ini)[0]


def ini_main(paths, names, ini=None):
    """Which Main binary MiSTer.ini hands this core to.

    Update All adds e.g. [RA_*] main=MiSTer_RA (odelot's RetroAchievements
    Main) and [A0CD-*] main=MiSTer_Physical-CD. Sections match like Main's
    cfg.cpp: case-insensitive, '*' = prefix match, against the core's names.
    Later matching sections win, as in Main."""
    main = 'MiSTer'
    ini = ini or active_ini(paths)
    try:
        with open(os.path.join(paths.root, ini), 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError:
        return main
    names = [n.lower() for n in names if n]
    active = False
    for line in lines:
        t = line.strip()
        if t.startswith('[') and ']' in t:
            sec = t[1:t.index(']')].lower()
            if sec == 'mister':
                active = True
            elif '*' in sec:
                pre = sec[:sec.index('*')]
                active = any(n.startswith(pre) for n in names)
            else:
                active = sec in names
            continue
        if active and '=' in t and not t.startswith(';'):
            k, v = t.split('=', 1)
            if k.strip().lower() == 'main':
                main = v.split(';', 1)[0].strip() or main
    return main


def ini_core_main(paths, names, ini=None):
    """The main= line a core's own section of MiSTer.ini has (not [MiSTer]'s): 'on' (main=...), 'off' (only a
    commented ;main=..., the port's menu turned it off) or None (none)."""
    ini = ini or active_ini(paths)
    try:
        with open(os.path.join(paths.root, ini), 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    names = [n.lower() for n in names if n]
    active, found = False, None
    for line in lines:
        t = line.strip()
        if t.startswith('[') and ']' in t:
            sec = t[1:t.index(']')].lower()
            active = sec != 'mister' and (any(n.startswith(sec[:sec.index('*')]) for n in names) if '*' in sec
                                           else sec in names)
            continue
        if active and re.match(r';?\s*main\s*=', t):
            if not t.startswith(';'):
                return 'on'
            found = 'off'
    return found


def hybrid_main(paths, names):
    """The hybrid platform's Main for a core one of its ports registers (games/<port>/platform/hybrid.d/<core>.conf,
    gmcnaught's mister-hybrid-platform), when it carries Coreshell's shim: its full path, else None. Stock, the
    port's installer puts main=<it> in MiSTer.ini's [<core>]; Coreshell shows the same line in its INI view."""
    from .mains import has_shim
    want = {n.lower() for n in names if n}
    for conf in sorted(glob.glob(os.path.join(paths.root, 'games', '*', 'platform', 'hybrid.d', '*.conf'))):
        if os.path.splitext(os.path.basename(conf))[0].lower() in want:
            main = os.path.join(os.path.dirname(os.path.dirname(conf)), 'MiSTer_hybrid')
            if os.path.isfile(main) and has_shim(main):
                return main
    return None


def main_supports_options(paths, main='MiSTer'):
    """True when this Main build understands MGL <option> (Oct 20 2026+).

    Looks for the log string that feature prints; cheap and works on any
    Main build, stock or forked (MiSTer_RA, MiSTer_Physical-CD...)."""
    path = os.path.join(paths.root, main)
    try:
        st = os.stat(path)
    except OSError:
        return False
    key = (path, st.st_mtime, st.st_size)
    if key not in _MAIN_CAPS:
        with open(path, 'rb') as f:
            _MAIN_CAPS[key] = b'MGL option: ' in f.read()
    return _MAIN_CAPS[key]


_HANDOFF = {}


def main_has_handoff(path):
    """True when this Main build takes Coreshell's handoff from the MGL (<status>, <setting>):
    the core's settings and INI settings applied in memory, the user's .CFG and INI never written
    (Phosphor's Main; any Main with the same hook)."""
    try:
        st = os.stat(path)
    except (OSError, TypeError):
        return False
    key = (path, st.st_mtime, st.st_size)
    if key not in _HANDOFF:
        with open(path, 'rb') as f:
            _HANDOFF[key] = b'Coreshell handoff: ' in f.read()
    return _HANDOFF[key]


def handoff_main(paths, main, running=None):
    """Will the Main that ends up running this launch take the handoff? The running Main gets the MGL;
    a main= other than the usual hands it to that Main, unless the running one is Phosphor's (which keeps
    itself for the ports, the RetroAchievements cores...)."""
    if running is None:
        from .supermemcard import running_main
        running = running_main(paths.root)
    if running and running.endswith(' (deleted)'):
        running = running[:-len(' (deleted)')]   # replaced on the SD card: loading a core starts the new file
    if not main_has_handoff(running):
        return False
    if (main or 'MiSTer').lower() == 'mister':
        return True
    # (Phosphor's Main follows main= the stock way since Nov 16 2026: the game ports' Mains run their games, so
    # it's that Main that has to take the handoff, as for any other Main)
    # corehost stands in for the Main MiSTer.ini names for the menu, and keeps the launch (corehost.cpp
    # corehost_keep_main); only a core naming a fork of its own goes to that Main
    if os.path.basename(running or '').lower() in ('corehost', 'mister_corehost'):
        b = os.path.basename(main or '')
        if b == _menu_main(paths):
            return True
    return main_has_handoff(os.path.join(paths.root, main))


def _menu_main(paths):
    """MiSTer.ini's [MiSTer] main= (the Main for the menu and every core without its own), its file name."""
    try:
        with open(os.path.join(paths.root, 'MiSTer.ini'), errors='replace') as f:
            sect = None
            for line in f:
                line = line.split(';', 1)[0].strip()
                if line.startswith('['):
                    sect = line.strip('[]').strip().lower()
                elif sect == 'mister' and line.lower().startswith('main') and '=' in line:
                    return os.path.basename(line.split('=', 1)[1].strip())
    except OSError:
        pass
    return 'MiSTer'


_MD5 = {}


def file_md5(path):
    import hashlib
    st = os.stat(path)
    key = (path, st.st_mtime, st.st_size)
    if key not in _MD5:
        h = hashlib.md5()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        _MD5[key] = h.hexdigest()
    return _MD5[key]


def is_exact(prof, rbf_file):
    """Is the installed .rbf the very build the profile was made from?

    By md5 when the profile has one (dumped profiles, undated RA cores),
    else by the dated file name (profiles generated from source)."""
    src = prof.get('source', {})
    if not rbf_file:
        return False
    if src.get('rbf_md5'):
        return file_md5(rbf_file) == src['rbf_md5']
    return bool(src.get('rbf')) and os.path.basename(rbf_file) == src['rbf']


def status_bytes(prof, values, base=None):
    """Settings -> the 16-byte status block Main keeps in <core>.CFG.

    Bit n of the status is bit (n % 8) of byte (n // 8), little-endian, the
    same layout Main reads back with FileLoadConfig.
    """
    st = bytearray(base or bytes(16))
    st = st[:16] + bytearray(16 - len(st[:16]))
    for o in prof['menu']:
        v = values.get(pf.option_key(o))
        if v is None:
            continue
        idx = o['values'].index(v)
        start, end = o['bits']
        for b in range(start, end + 1):
            bit = (idx >> (b - start)) & 1
            if bit:
                st[b // 8] |= 1 << (b % 8)
            else:
                st[b // 8] &= ~(1 << (b % 8)) & 0xFF
    st[0] &= 0xFE          # bit 0 is the core's reset line, never stored set
    return bytes(st)


def values_from_status(prof, st):
    """The 16-byte status block -> {option_key: value} (the reverse of status_bytes)."""
    st = bytes(st or b'')[:16].ljust(16, b'\0')
    out = {}
    for o in prof['menu']:
        start, end = o['bits']
        idx = 0
        for b in range(start, end + 1):
            if st[b // 8] >> (b % 8) & 1:
                idx |= 1 << (b - start)
        if idx < len(o['values']):
            out[pf.option_key(o)] = o['values'][idx]
    return out


def read_cfg(paths, prof):
    try:
        with open(os.path.join(paths.config, prof['config_file']), 'rb') as f:
            return f.read(16)
    except OSError:
        return None


def cfg_mtime(paths, prof):
    try:
        return os.stat(os.path.join(paths.config, prof['config_file'])).st_mtime_ns
    except OSError:
        return None


def written_path(paths, prof):
    return os.path.join(paths.home, 'written', prof['config_file'] + '.json')


def remember_written(paths, prof, key, game, display, handed=None):
    """What the core started with, and for which launch: sync_back compares the file with it later.
    handed: the status Coreshell handed Main in memory (handoff mode: the .CFG itself wasn't written,
    so only a save from the core's OSD changes it)."""
    st = read_cfg(paths, prof)
    if st is None and handed is None:
        return
    rec = {'cfg': (handed or st).hex(), 'settings': key, 'game': game, 'display': display}
    if handed is not None:
        rec['file'] = st.hex() if st is not None else None
        rec['mtime'] = cfg_mtime(paths, prof)    # (a save of the same bytes is still a save: the file's time says so)
    path = written_path(paths, prof)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(rec, f)
    os.replace(path + '.tmp', path)


def sync_back(paths, prof, key):
    """Settings the user saved in the core's own OSD ("Save settings" writes its .CFG)
    -> back into Coreshell's settings, so both menus agree.

    Only options whose bits changed since Coreshell wrote the file, and only when
    the installed core is the profile's build (otherwise the bits mean something
    else). Each goes to the layer it came from at that launch: the game's, else
    the display's, else the system's. Returns {option_key: value} it took."""
    try:
        with open(written_path(paths, prof), encoding='utf-8') as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return {}
    now = read_cfg(paths, prof)
    if now is None or now.hex() == rec['cfg'] or rec.get('settings') != key:
        return {}
    if 'file' in rec and now.hex() == rec['file'] and rec.get('mtime', cfg_mtime(paths, prof)) == cfg_mtime(paths, prof):
        return {}                         # handoff: the file is as it was and not written since: nothing was saved
    if not is_exact(prof, installed_rbf(paths, prof['rbf'])):
        return {}
    was = values_from_status(prof, bytes.fromhex(rec['cfg']))
    new = values_from_status(prof, now)
    changed = {k: v for k, v in new.items() if was.get(k) != v}
    game, disp = rec.get('game'), rec.get('display')
    layers = [(game, disp), (game, None), (None, disp)] if game else [(None, disp)]
    taken = set()
    for g, d in layers:
        if g is None and d is None:
            continue
        vals = read_settings(paths, key, g, d)
        mine = {k: v for k, v in changed.items() if k in vals and k not in taken}
        if mine:
            vals.update(mine)
            write_settings(paths, key, vals, g, d)
            taken.update(mine)
    rest = {k: v for k, v in changed.items() if k not in taken}
    if rest:
        vals = read_settings(paths, key)
        vals.update(rest)
        write_settings(paths, key, vals)
    rec['cfg'] = now.hex()
    rec.pop('file', None)
    with open(written_path(paths, prof) + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(rec, f)
    os.replace(written_path(paths, prof) + '.tmp', written_path(paths, prof))
    return changed


def write_cfg(paths, prof, values):
    """Merge the settings into the core's .CFG (other bits, and anything after the status block, kept as they
    are). Nothing to set: the file isn't touched, or made when there's none (as stock: Main makes it on a save)."""
    path = os.path.join(paths.config, prof['config_file'])
    if not any(v is not None for v in values.values()):
        return path
    try:
        with open(path, 'rb') as f:
            whole = f.read()
    except OSError:
        whole = None
    base = whole[:16] if whole is not None else None
    new = status_bytes(prof, values, base)
    if base == new:
        return path
    new += (whole or b'')[16:]
    os.makedirs(paths.config, exist_ok=True)
    if base is not None:
        os.makedirs(paths.backups, exist_ok=True)
        shutil.copy2(path, os.path.join(paths.backups, prof['config_file']))
    tmp = path + '.coreshell'
    with open(tmp, 'wb') as f:
        f.write(new)
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------- saves

def save_path(paths, prof, rom):
    """Where Main looks for this ROM's save: saves/<core>/<rom name>.sav."""
    name = os.path.splitext(os.path.basename(rom))[0] + '.sav'
    return os.path.join(paths.abs(prof['saves_dir']), name)


def list_saves(paths, prof, rom):
    """Saves this game can start with, newest first: [{path, from, time, bytes}].

    from: 'current' (the one Main loads: saves/<core>/<game>.sav), 'backup' (one
    Coreshell replaced, backups/saves/<system>/), 'library' (any file put in
    coreshell/saves/<system>/<game>/, e.g. a save copied from a PC). Launch with
    one as 'save' and it's put in place, the current one backed up first.
    Super MemCard saves are picked with its Card entry instead."""
    if not rom or not prof.get('saves_dir'):
        return []
    cur = save_path(paths, prof, rom)
    name = os.path.basename(cur)
    stem = os.path.splitext(name)[0]
    found = []
    if os.path.isfile(cur):
        found.append((cur, 'current'))
    for f in glob.glob(os.path.join(glob.escape(os.path.join(paths.backups, 'saves', prof['system'])),
                                    glob.escape(name) + '.*')):
        found.append((f, 'backup'))
    lib = os.path.join(paths.home, 'saves', prof['system'], stem)
    for f in glob.glob(os.path.join(glob.escape(lib), '*')):
        if os.path.isfile(f):
            found.append((f, 'library'))
    out = []
    for f, src in found:
        st = os.stat(f)
        out.append({'path': f, 'from': src, 'time': int(st.st_mtime), 'bytes': st.st_size,
                    'name': os.path.basename(f)})
    out.sort(key=lambda x: (x['from'] != 'current', -x['time']))
    return out


def place_save(paths, prof, rom, save):
    """Put the chosen save where Main will mount it, keeping a backup."""
    dst = save_path(paths, prof, rom)
    if os.path.abspath(save) == os.path.abspath(dst):
        return dst
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        bdir = os.path.join(paths.backups, 'saves', prof['system'])
        os.makedirs(bdir, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        shutil.copy2(dst, os.path.join(bdir, os.path.basename(dst) + '.' + stamp))
    shutil.copy2(save, dst)
    return dst


# ----------------------------------------------------------------------- MGL

def slot_for(prof, rom):
    """The game slot for this file: the profile's ROM slot if it takes the
    extension, else the first slot that does (CD image on a cart+CD core)."""
    ext = os.path.splitext(rom)[1][1:].upper()
    slots = [prof['rom']] if prof.get('rom') else []
    slots += prof.get('slots', [])
    for s in slots:
        if s and ext in [e.upper() for e in s['exts']]:
            return s
    return prof.get('rom')


MGL_MAX_FILES = 6          # Main's mgl.item[] holds 6 entries from the MGL


def all_slots(prof):
    """Every file the core's menu can load or mount: [{id, type, index, label, exts, game}].
    id is 'f<index>' / 's<index>' (what launch's 'files' takes); game marks the slots a
    game goes in (the others: a second disk, memory cards, BIOS, cartridges...)."""
    games = {(s['type'], s['index']) for s in ([prof['rom']] if prof.get('rom') else []) + prof.get('slots', [])}
    out = []
    for e in prof.get('files', []) + prof.get('mounts', []):
        out.append({'id': '%s%d' % (e['type'], e['index']), 'type': e['type'], 'index': e['index'],
                    'label': e.get('label', '').strip(), 'exts': e.get('exts', []),
                    'game': (e['type'], e['index']) in games})
    return out


def extra_files(prof, files):
    """launch's 'files' ({"s2": "/path/card1.sav", ...}) -> [(slot, path)], checked."""
    slots = {s['id']: s for s in all_slots(prof)}
    out = []
    for sid, path in (files or {}).items():
        s = slots.get(str(sid).lower())
        if not s:
            raise KeyError('%s has no slot %r (slots: %s)' % (prof['system'], sid, ', '.join(slots)))
        ext = os.path.splitext(path)[1][1:].upper()
        if s['exts'] and ext not in [e.upper() for e in s['exts']]:
            raise ValueError('%s takes %s, not .%s' % (s['label'] or sid, '/'.join(s['exts']), ext.lower()))
        out.append((s, path))
    return out


def handoff_status(prof, values, keys=None):
    """(mask, value): the status bits of the options Coreshell sets (bit 0, the reset line, never).
    keys: only those options (plan's 'set': what the user chose in a frontend). Every other option keeps the
    core's own setting, as the user left it in the core's menu: Coreshell never puts a setting nobody chose back
    to a default, the profile's own defaults included (docs/stock-behaviour.md: what Coreshell is for)."""
    mask = bytearray(16)
    for o in prof['menu']:
        if values.get(pf.option_key(o)) is None:
            continue
        if keys is not None and pf.option_key(o) not in keys:
            continue
        start, end = o['bits']
        for b in range(start, end + 1):
            if b:
                mask[b // 8] |= 1 << (b % 8)
    return bytes(mask), status_bytes(prof, values)


def build_mgl(prof, rom, values, send_options, extra=None, status=None, ini=None):
    """The MGL Main runs: core, ROM slot, other slots' files, (name mode) the options, and (handoff mode)
    the core's settings as status bits and the launch's INI settings."""
    lines = ['<mistergamedescription>',
             '\t<rbf>%s</rbf>' % prof['rbf']]
    if prof.get('setname'):
        lines.append('\t<setname same_dir="1">%s</setname>' % prof['setname'])
    if rom:
        slot = slot_for(prof, rom)
        lines.append('\t<file delay="1" type=%s index=%s path=%s/>' % (
            quoteattr(slot['type']), quoteattr(str(slot['index'])), quoteattr(rom)))
    for slot, path in (extra or []):
        lines.append('\t<file delay="1" type=%s index=%s path=%s/>' % (
            quoteattr(slot['type']), quoteattr(str(slot['index'])), quoteattr(path)))
    for key in send_options:
        o = pf.find_option(prof, key)
        lines.append('\t<option name=%s%s value=%s/>' % (
            quoteattr(o['name']),
            (' page=' + quoteattr(o['page'])) if o['page'] else '',
            quoteattr(values[key])))
    if status:
        lines.append('\t<status mask="%s" value="%s"/>' % (status[0].hex(), status[1].hex()))
    for k, v in (ini or {}).items():
        lines.append('\t<setting key=%s value=%s/>' % (quoteattr(str(k)), quoteattr(str(v))))
    lines.append('</mistergamedescription>')
    return '\n'.join(lines) + '\n'


def plan(paths, prof, rom=None, save=None, game_vals=None, system_vals=None, files=None, running=None):
    """Work out a launch without touching anything (used by the API and tests)."""
    if system_vals is None:
        system_vals = read_settings(paths, prof['system'])
    if game_vals is None and rom:
        game_vals = read_settings(paths, prof['system'], os.path.basename(rom))
    values = effective(prof, system_vals, game_vals)
    rbf_file = installed_rbf(paths, prof['rbf'])
    exact = is_exact(prof, rbf_file)
    rbf_name = re.sub(r'_\d{8}$', '', os.path.splitext(os.path.basename(rbf_file or prof['rbf']))[0])
    names = [prof.get('setname') or prof['core_name'], rbf_name]
    main = ini_main(paths, names)
    # a hybrid port's core (Solarus...): its game runs under the port's own Main, as stock with the installer's
    # [<core>] main= line; unless MiSTer.ini's [<core>] has a main= of its own (on, or turned off), Coreshell's
    # INI view (server.py _ini_for_main) shows that line to both Mains while it starts. Every Main reads the
    # INI, so both agree on it (a main= in the MGL reached only the running Main: the other sent it back).
    hybrid = hybrid_main(paths, names) if ini_core_main(paths, names) is None else None
    if hybrid:
        main = hybrid
    extra = extra_files(prof, files)
    if len(extra) + (1 if rom else 0) > MGL_MAX_FILES:
        raise ValueError('Main loads %d files per launch' % MGL_MAX_FILES)

    # only what the user chose in a frontend (game first, then system): nothing else, not even a profile
    # default. A launch is the stock launch plus Coreshell's return to the frontend (docs/stock-behaviour.md: what Coreshell is for)
    def keys(src):
        out = []
        for k in (src or {}):
            o = pf.find_option(prof, k)
            if o:
                out.append(pf.option_key(o))
        return out
    changed = []
    for k in keys(game_vals) + keys(system_vals):
        if k not in changed:
            changed.append(k)
    warnings = []
    handoff = handoff_main(paths, main, running)
    if exact:
        mode = 'handoff' if handoff else 'exact'
    else:
        mode = 'name' if main_supports_options(paths, main) else 'core'
    status = handoff_status(prof, values, changed) if mode == 'handoff' else None
    ini = dict(prof.get('ini') or {}) if handoff else {}     # the profile's INI settings for this core
    if prof.get('ini') and not handoff:
        warnings.append('This Main doesn\'t take Coreshell\'s INI settings: %s not applied'
                        % ', '.join(sorted(prof['ini'])))
    if mode in ('exact', 'handoff'):
        send = []
    elif mode == 'core':
        send = []
        warnings.append('%s is not the build this profile was made from (%s) and Main has '
                        'no MGL options: starting with the core\'s own settings; '
                        'regenerate the profile' % (os.path.basename(rbf_file or prof['rbf']),
                                                     prof.get('source', {}).get('rbf')))
    else:
        cant = [k for k in changed if not pf.find_option(prof, k).get('mgl_ok', True)]
        if cant:
            warnings.append('Main can\'t tell these apart by name, not sent: %s' % ', '.join(cant))
        changed = [k for k in changed if k not in cant]
        send = changed[:MGL_MAX_OPTIONS]
        if len(changed) > MGL_MAX_OPTIONS:
            warnings.append('%d settings changed, Main takes %d per launch: %s not sent'
                            % (len(changed), MGL_MAX_OPTIONS, ', '.join(changed[MGL_MAX_OPTIONS:])))
    return {
        'system': prof['system'],
        'rbf_file': rbf_file,
        'mode': mode,
        'main': main,
        'values': values,
        'set': changed,               # the options Coreshell sets (the user's choices); every other one stays as is
        'hybrid': {'section': names[0], 'main': hybrid} if hybrid else None,     # (the INI view's [<core>] main=)
        'mgl': build_mgl(prof, rom, values, send, extra, status, ini),
        'handoff': handoff,
        'save': save and save_path(paths, prof, rom),
        'warnings': warnings,
    }


def launch(paths, prof, rom=None, save=None, game_vals=None, system_vals=None,
           fifo=CMD_FIFO, files=None, before_start=None, running=None, log=None):
    p = plan(paths, prof, rom, save, game_vals, system_vals, files, running)
    if not p['rbf_file']:
        raise RuntimeError('core not installed: %s' % prof['rbf'])
    # a core patch for this exact stock core (corepatches.py): the patched copy, from /tmp, instead
    patched = cp.patched_rbf(paths, prof, p['rbf_file'], log=log)
    if patched:
        p['mgl'] = p['mgl'].replace('\t<rbf>%s</rbf>' % prof['rbf'], '\t<rbf>%s</rbf>' % patched, 1)
    p['core_patch'] = bool(patched)
    if p['mode'] == 'exact':
        # (a Main without the handoff: the .CFG is the only way in) only the options Coreshell sets
        write_cfg(paths, prof, {k: v for k, v in p['values'].items() if k in p['set']})
    if save:
        place_save(paths, prof, rom, save)
    os.makedirs(paths.launch, exist_ok=True)
    mgl = os.path.join(paths.launch, prof['system'] + '.mgl')
    with open(mgl, 'w', encoding='utf-8') as f:
        f.write(p['mgl'])
    if before_start:
        before_start(p)                   # (e.g. the INI made right for an older Main, just for its start)
    from .mains import bridge_replaced
    bridge_replaced(paths.root, say=log)  # (a Main replaced while it runs: its restart for this core would reboot)
    from .cmdpipe import write_main
    write_main(fifo, 'load_core %s' % mgl)
    p['mgl_file'] = mgl
    return p
