"""Coreshell - local API for frontends (Console Mode, Phosphor, test tools). Codename Kugutsu no Jutsu (傀儡の術).

Unix socket, one JSON object per line each way. Every request carries the
API version it was written for; Coreshell refuses versions it doesn't speak,
so a frontend never misreads a newer Coreshell (or the other way round).

    -> {"v": 1, "cmd": "systems"}
    <- {"ok": true, "systems": [...]}

Commands (v1):
    hello                               api version, coreshell version (codename), active INI and display
    kernel [action]                     the kernel's display against the last known-good state (status, check,
                                        accept, hold, unhold): adapted to a new kernel, or degraded and why
    systems                             every profile + whether its core is installed
    menu     profile [game]             the unified menu with current values (for the active display)
    set      profile key value [game] [display]
                                        store one setting; game = this game only,
                                        display = only on that display ("current" = the active one)
    reset    profile [game] [display] [key]
                                        drop stored settings
    plan     profile [rom] [game]       what a launch would do (mode, MGL, warnings)
    launch   profile rom [save]         start the game

Display sets: every INI MiSTer can switch to (MiSTer.ini, MiSTer_RGsB.ini...)
gets its own settings layer, so CRT and HDMI can each keep their own video
options. /media/fat/coreshell/displays.json can group INIs under one name
({"MiSTer_RGsB.ini": "crt", "MiSTer_YPbP.ini": "crt", "MiSTer.ini": "hdmi"}).
Layers, lowest first: system, system@display, game, game@display.

The frontend talks to this socket only; it never links any Coreshell or
MiSTer code.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import glob
import json
import os
import socket
import socketserver
import sys
import threading
import time

from . import corepatches as cpatch
from . import controllers as ct
from . import launcher as ln
from .mains import bridge_replaced
from .cmdpipe import write_main
from . import profile as pf
from . import supermemcard as smc
from . import refresh as rf
from . import tools as tl
from . import raviewer as rav
from . import gameid as gid
from . import cmdpipe as cp
from . import events as evm
from . import kernel as krn

STATE_DIR = '/tmp'                       # where Main writes CORENAME, FULLPATH...: the game announced there too

API = 1
VERSION = '0.1.0'
SOCKET = '/tmp/coreshell.sock'


def _log(msg):
    print('coreshell: ' + msg, file=sys.stderr, flush=True)


LAUNCH_LOG = '/tmp/coreshell/launch.log'
MAIN_SOCKS = ('/tmp/phosphor-main.sock', '/tmp/corehost.sock')   # the running Main's API (opt: an option's value)
AUTO_FILE = '/tmp/coreshell/auto.json'    # what Auto gave the running game's core (kept over a restart of Coreshell)


def _step(step, msg):
    """One line a launch step, in the launch log (and Coreshell's own log)."""
    import time
    line = '%s %-8s %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), step, msg)
    _log('launch: ' + step + ': ' + msg)
    try:
        os.makedirs(os.path.dirname(LAUNCH_LOG), exist_ok=True)
        with open(LAUNCH_LOG, 'a') as f:
            f.write(line + '\n')
    except OSError:
        pass


def _xml(s):
    """phosphord's xml_escape (play.c), for an MGL's path."""
    for a, b in (('&', '&amp;'), ('<', '&lt;'), ('>', '&gt;'), ('"', '&quot;'), ("'", '&apos;')):
        s = s.replace(a, b)
    return s


def _mgl_game(path):
    """An MGL's core and game: (rbf, setname, file path, slot {delay, type, index}); Nones when it has none."""
    import re
    try:
        t = open(path, encoding='utf-8', errors='replace').read()
    except OSError:
        return None, None, None, None
    def un(v):
        for a, b in (('&lt;', '<'), ('&gt;', '>'), ('&quot;', '"'), ('&apos;', "'"), ('&amp;', '&')):
            v = v.replace(a, b)
        return v
    rbf = re.search(r'<rbf>\s*([^<]+?)\s*</rbf>', t)
    sn = re.search(r'<setname[^>]*>\s*([^<]+?)\s*</setname>', t)
    f = re.search(r'<file\b([^>]*)/?>', t)
    rom = slot = None
    if f:
        at = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', f.group(1)))
        rom = un(at.get('path', '')) or None
        slot = {'delay': int(at.get('delay') or 0), 'type': at.get('type') or 'f', 'index': int(at.get('index') or 0)}
    return (un(rbf.group(1)) if rbf else None), (un(sn.group(1)) if sn else None), rom, slot


class Coreshell(object):
    def __init__(self, paths=None, fifo=ln.CMD_FIFO):
        self.paths = paths or ln.Paths()
        self._fifo = fifo
        self.pipe = None                 # cmdpipe: /dev/MiSTer_cmd is Coreshell's (start_pipe)
        self._announced = None           # the game Coreshell wrote in /tmp (ACTIVEGAME...)
        self._auto_last = None           # the last launch's PSX controller setup on Auto: {rom, serial, values}
        try:                             # (started again mid-game: the game a Coreshell before this one wrote)
            if open(os.path.join(STATE_DIR, 'CORENAME')).read().strip() not in ('', 'MENU'):
                self._announced = open(os.path.join(STATE_DIR, 'ACTIVEGAME')).read().strip() or None
        except OSError:
            pass
        try:                             # (and what Auto gave that game's core)
            with open(AUTO_FILE, encoding='utf-8') as f:
                last = json.load(f)
            if self._announced and last.get('rom') == self._announced:
                self._auto_last = last
        except (OSError, ValueError, AttributeError):
            pass
        self.events = evm.Events(say=_log)   # what happens, for whoever asks (cmd events / subscribe)
        self.lock = threading.Lock()
        self._profiles = {}
        self.smc = smc.SuperMemCard()
        self.refresher = rf.Refresher(self)
        self.tools = tl.Tools(self)
        # RetroAchievements for the frontend's screens: the RA Viewer add-on's data, hidden (raviewer.py)
        self.raviewer = rav.RAViewer(self.paths.root, say=_log)
        self.events.listeners.append(self.raviewer.on_event)
        # the kernel's display, kept working across Linux updates (kernel.py): checked at start (serve)
        self.kernel = krn.Kernel(self.paths.home, self.paths.root, say=_log, emit=self.events.emit)
        try:                             # an INI left patched by a launch (power cut): put back
            from . import inicheck as _ic
            _ic.restore(self.paths.root)
        except OSError:
            pass
        self._migrate_supercard()

    def _migrate_supercard(self):
        """The old SuperCard picks (a Pad choice before Nov 27 2026) -> SNAC MemCard = Real with the Pad they had
        (corepatches.migrate_supercard). A game's layer is migrated against its system's as it was (its own picks)."""
        root = self.paths.settings

        def load(f):
            try:
                with open(f, encoding='utf-8') as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                return None
        systems = {f: load(f) for f in glob.glob(os.path.join(root, '*.json'))}
        games = glob.glob(os.path.join(root, '*', '*.json'))
        for f in sorted(systems) + sorted(games):
            vals = systems[f] if f in systems else load(f)
            if not isinstance(vals, dict):
                continue
            below = None
            if f not in systems:                      # a game's layer: its system's (before migrating) under it
                below = systems.get(os.path.join(root, os.path.basename(os.path.dirname(f)) + '.json')) or {}
            new = cpatch.migrate_supercard(vals, below)
            if new is None:
                continue
            try:
                with open(f + '.tmp', 'w', encoding='utf-8') as fh:
                    json.dump(new, fh, indent=1, ensure_ascii=False)
                os.replace(f + '.tmp', f)
                _log('settings %s: SuperCard -> SNAC MemCard Real' % os.path.relpath(f, root))
            except OSError:
                pass

    # -------------------------------------------------------------- profiles
    def profiles(self):
        out = {}
        for f in sorted(glob.glob(os.path.join(self.paths.systems, '*.json'))):
            pid = os.path.splitext(os.path.basename(f))[0]
            st = os.stat(f)
            cached = self._profiles.get(pid)
            if cached and cached[0] == st.st_mtime:
                out[pid] = cached[1]
                continue
            try:
                prof = pf.load(f)
            except (OSError, ValueError):
                continue
            self._profiles[pid] = (st.st_mtime, prof)
            out[pid] = prof
        return out

    def profile(self, pid):
        p = self.profiles().get(pid)
        if not p:
            raise KeyError('no profile %r' % pid)
        return p

    @property
    def fifo(self):
        return self.pipe.main_path() if self.pipe else self._fifo

    # -------------------------------------------------------------- add-ons (Zaparoo, SAM, MiSTer Extensions...)
    def start_pipe(self):
        """/dev/MiSTer_cmd becomes Coreshell's: what add-ons send Main comes here first (cmdpipe.py)."""
        if self.pipe is None and self._fifo == ln.CMD_FIFO:
            self.pipe = cp.CmdPipe(self.addon_command, log=_log)
            self.pipe.start()

    def stop_pipe(self):
        if self.pipe:
            self.pipe.stop()
            self.pipe = None

    def _addon_rom(self, p, rom):
        """An MGL's file path as Main finds it: absolute, or under the core's games folder on the SD card or a USB
        drive (Main looks on the drives first)."""
        if not rom:
            return None
        if rom.startswith('/'):
            return os.path.normpath(rom)
        gd = p.get('games_dir') or ''
        gd = gd if gd.startswith('/') else os.path.join(self.paths.root, gd)
        name = os.path.relpath(gd, os.path.join(self.paths.root, 'games')) if gd.startswith(
            os.path.join(self.paths.root, 'games') + '/') else os.path.basename(gd)
        roots = ['/media/usb%d/games' % i for i in range(8)] + [os.path.join(self.paths.root, 'games')]
        for r in roots:
            cand = os.path.normpath(os.path.join(r, name, rom))
            if os.path.exists(cand):
                return cand
        return os.path.normpath(os.path.join(gd, rom))

    def addon_command(self, line):
        """One line an add-on wrote to /dev/MiSTer_cmd. A game, an arcade game or a core to start: Coreshell starts
        it (a launch like the frontend's); the menu and every other command: to Main as sent."""
        verb, _, arg = line.partition(' ')
        arg = arg.strip()
        if verb == 'screenshot' and not arg and self._hooked('screenshot'):
            return                       # (in the menu: the frontend's screen, not the menu core's)
        if verb != 'load_core' or not arg:
            self.pipe.forward(line)
            return
        low = arg.lower()
        if os.path.basename(low) in ('menu.rbf', 'menu') or low.startswith(self.paths.launch.lower() + '/'):
            self.pipe.forward(line)      # (the menu; or one of Coreshell's own MGLs)
            return
        req = None
        if low.endswith('.mra'):
            req = {'kind': 'arcade', 'rom': arg}
        elif low.endswith('.rbf'):
            # a core Coreshell has no profile for (Update All's Linker core, a utility): Main loads it as sent, as on
            # stock MiSTer; Coreshell starts the cores it has something to add to (settings, cards, achievements)
            try:
                self.find_profile(arg)
                req = {'rbf': arg}
            except KeyError:
                req = None
        elif low.endswith('.mgl'):
            import re
            try:
                files = len(re.findall(r'<file\b', open(arg, encoding='utf-8', errors='replace').read()))
            except OSError:
                files = -1
            rbf, setname, rom, slot = _mgl_game(arg)
            if rbf and files in (0, 1):
                req = {'rbf': rbf, 'setname': setname, 'slot': slot}
                if rom:
                    try:
                        req['rom'] = self._addon_rom(self.profile(self.find_profile(rbf, setname)), rom)
                    except KeyError:
                        req['rom'] = rom if rom.startswith('/') else None
                        if not req['rom']:
                            req = None
        if req is None:
            self.pipe.forward(line)      # (an MGL Coreshell can't read: several files, no core...)
            return
        req.update(v=API, cmd='launch', **{'from': 'add-on'})
        _log('add-on: %s -> Coreshell launch' % line)
        out = self.handle(req)
        if not out.get('ok'):
            _log('add-on: %s: %s: passed to Main as sent' % (line, out.get('error')))
            self.pipe.forward(line)

    def cmd_addons(self, req):
        """The add-ons Coreshell knows that take the system over (addons/: DreamSTer...): for each, its script, whether
        it's installed, and whether its menu is changed to name the frontend (the release matches its entry); and the
        frontend registered (`frontend`: name, main) that they come back to."""
        from . import addons as ad
        fe = ad.frontend(self.paths.home)
        out = []
        for e in ad.entries(self.paths.home):
            path = os.path.join(self.paths.root, 'Scripts', e['script'])
            item = {'id': e['id'], 'name': e.get('name'), 'script': path, 'installed': os.path.isfile(path),
                    'how': e.get('how')}
            if item['installed'] and e.get('menu'):
                try:
                    with open(path, encoding='utf-8', errors='surrogateescape') as f:
                        item['menu_named'] = ad.rewrite(f.read(), e, fe.get('name') or 'X') is not None
                except OSError:
                    item['menu_named'] = False
            out.append(item)
        return {'addons': out, 'frontend': {'name': fe.get('name'), 'main': fe.get('main')}}

    def cmd_caps(self, req):
        """What's installed and plugged in (caps.py, caps/): each capability present or not, what was found, the core
        to load for it. A frontend builds every menu in and shows each once what it needs is here; the event "caps"
        says which changed. {"refresh": true} looks again now."""
        from . import caps as cp
        c = getattr(self, 'caps', None)
        if c is None:
            c = self.caps = cp.Caps(self.paths.root, self.paths.home)
        if req.get('refresh') or not c.last:
            c.refresh()
        return {'caps': c.last}

    def cmd_events(self, req):
        """What happened since seq ({"since": seq, "wait": up to 30 s for one}): game_started, rom_loaded,
        game_ended, saves, core_changed, achievement_unlocked, retroachievements (events.py)."""
        evs, last = self.events.since(int(req.get('since') or 0), req.get('wait') or 0)
        return {'events': evs, 'seq': last}

    def _hooks_file(self):
        return os.path.join(self.paths.home, 'state', 'frontend.json')

    def frontend_hooks(self):
        try:
            with open(self._hooks_file()) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def cmd_frontend(self, req):
        """A frontend tells Coreshell what it does instead of Main while it's on screen (in the menu core):
        {"screenshot": {"socket": <its unix socket>, "send": <the line it takes>}}. Kept (it's the same after a
        restart); {"screenshot": null} takes it away. An add-on's `screenshot` in the menu is then the frontend's.
        {"name": "Phosphor", "main": "/media/fat/phosphor/bin/MiSTer_phosphor"}: who it is, for add-ons (addons.py)."""
        hooks = self.frontend_hooks()
        # who the frontend is: its name (what add-ons' own menus say instead of "MiSTer": addons/) and its Main (the
        # one to bring back when an add-on starts stock MiSTer's on exit)
        if req.get('name') is not None:
            n = str(req['name']).strip()[:40]
            if n:
                hooks['name'] = n
            else:
                hooks.pop('name', None)
        if req.get('main') is not None:
            m = str(req['main'])
            if m and not (m.startswith('/media/') and '..' not in m):
                raise ValueError('main: the frontend\'s Main, a path under /media')
            if m:
                hooks['main'] = m
            else:
                hooks.pop('main', None)
        for k in ('screenshot',):
            if k in req:
                if req[k]:
                    h = req[k]
                    if not isinstance(h, dict) or not str(h.get('socket', '')).startswith('/') or not h.get('send'):
                        raise ValueError('%s: {"socket": "/path", "send": "line"}' % k)
                    hooks[k] = {'socket': h['socket'], 'send': str(h['send'])}
                else:
                    hooks.pop(k, None)
        os.makedirs(os.path.dirname(self._hooks_file()), exist_ok=True)
        tmp = self._hooks_file() + '.tmp'      # (whole or not at all: the SS1 can lose power any time)
        with open(tmp, 'w') as f:
            json.dump(hooks, f)
        os.replace(tmp, self._hooks_file())
        return {'hooks': hooks}

    def _in_menu(self):
        try:
            return open(os.path.join(STATE_DIR, 'CORENAME')).read().strip() == 'MENU'
        except OSError:
            return False

    def _hooked(self, verb):
        """The frontend's hook for this command, when the frontend is on screen: sent there (True)."""
        h = self.frontend_hooks().get(verb)
        if not h or not self._in_menu():
            return False
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect(h['socket'])
            s.sendall((h['send'].rstrip('\n') + '\n').encode('utf-8'))
            s.recv(4096)                 # (its answer: done)
            s.close()
            return True
        except OSError as e:
            _log('add-on: the frontend\'s %s didn\'t answer (%s): Main\'s instead' % (verb, e))
            return False

    def _announce(self, game):
        """The game that started, as Main writes it when a file is picked (FULLPATH, CURRENTPATH, then FILESELECT
        "selected": Zaparoo's, MiSTer Extensions' and MiSTer Monitor's trackers read them), and ACTIVEGAME (Zaparoo's
        and MiSTer Extensions' own). None: the menu is back (ACTIVEGAME emptied if Coreshell wrote it)."""
        def put(name, text):
            try:
                with open(os.path.join(STATE_DIR, name), 'w') as f:
                    f.write(text)
            except OSError:
                pass
        if game:
            put('FULLPATH', game)
            put('CURRENTPATH', os.path.basename(game))
            put('FILESELECT', 'selected')
            put('ACTIVEGAME', game)
            self._announced = game
        elif self._announced:
            try:
                cur = open(os.path.join(STATE_DIR, 'ACTIVEGAME')).read().strip()
            except OSError:
                cur = ''
            if cur == self._announced:
                put('ACTIVEGAME', '')
            self._announced = None

    def find_profile(self, rbf, setname=None):
        """The profile for a core a frontend picked: its .rbf (path with or without the date,
        _Console/SNES_20260901.rbf or _Console/SNES) and set name (RA_SNES, A0CD-PSX)."""
        import re as _re
        base = _re.sub(r'(_\d{8})?(\.rbf)?$', '', (rbf or '').replace(self.paths.root + '/', ''), flags=_re.I)
        hits = [(pid, p) for pid, p in self.profiles().items() if p['rbf'].lower() == base.lower()]
        sn = (setname or '').lower()
        for pid, p in hits:
            if (p.get('setname') or '').lower() == sn:
                return pid
        if not sn:
            for pid, p in hits:
                if p.get('variant', 'stock') == 'stock':
                    return pid
            if len(hits) == 1:
                return hits[0][0]
        raise KeyError('no profile for %s%s' % (base, (' (' + setname + ')') if setname else ''))

    def _pid(self, req):
        """A request names its profile, or the core (rbf + setname) as a frontend picked it."""
        if req.get('profile'):
            return req['profile']
        if req.get('rbf'):
            return self.find_profile(req['rbf'], req.get('setname'))
        raise KeyError('profile')

    # -------------------------------------------------------------- commands
    def display(self):
        ini = ln.active_ini(self.paths)
        return ini, ln.display_set(self.paths, ini)

    def _disp(self, req):
        d = req.get('display')
        return self.display()[1] if d == 'current' else d

    def _layers(self, p, game=None):
        """(system values, game values) for the active display, each already
        merged with its display layer, plus where every value came from."""
        key = p['system'] + _vsuffix(p)
        disp = self.display()[1]
        names = ['system', 'display', 'game', 'game_display']
        raw = [ln.read_settings(self.paths, key), ln.read_settings(self.paths, key, None, disp),
               ln.read_settings(self.paths, key, game) if game else {},
               ln.read_settings(self.paths, key, game, disp) if game else {}]
        origin = {}
        for name, layer in zip(names, raw):
            for k in layer:
                o = pf.find_option(p, k)
                if o:
                    origin[pf.option_key(o)] = name
        sysv = dict(raw[0], **raw[1])
        gamev = dict(raw[2], **raw[3])
        return sysv, gamev, origin

    def cmd_hello(self, req):
        ini, disp = self.display()
        return {'api': API, 'coreshell': VERSION, 'codename': 'kugutsu_no_jutsu', 'ini': ini, 'display': disp,
                'refresh': self.refresher.status}

    def cmd_slots(self, req):
        """Every file slot of the core's menu (a second disk, memory cards...), for launch's 'files'."""
        return {'slots': ln.all_slots(self.profile(self._pid(req)))}

    def cmd_inis(self, req):
        """The INIs MiSTer can switch between (the OSD's INI choice), which is active,
        and the settings set (display) each one uses."""
        ini, _ = self.display()
        return {'inis': [{'ini': n, 'active': n.lower() == ini.lower(),
                          'display': ln.display_set(self.paths, n)} for n in ln.ini_list(self.paths)]}

    def cmd_set_ini(self, req):
        """Switch INI like the OSD does: Main restarts on the menu core with it."""
        return {'ini': ln.set_ini(self.paths, req['ini'], fifo=self.fifo)}

    def cmd_stale(self, req):
        """Cores whose profile doesn't match the installed build, or that have none."""
        return {'stale': [dict(i, rbf=os.path.relpath(i['rbf'], self.paths.root))
                          for i in rf.stale(self.paths, self.profiles())]}

    def cmd_refresh(self, req):
        """Dump the stale cores and rebuild their profiles, in the background (it loads
        each core, so only from the menu). Progress: hello's / this answer's 'refresh'."""
        if req.get('cancel'):
            # (a stop asked by the frontend, B on its card: the core being read goes, the menu comes back. The
            # dumper's own way back to the menu doesn't run when it's stopped; a game's launch loads its own core)
            if self.refresher.cancel() and not self.refresher.in_menu():
                bridge_replaced(self.paths.root)   # (a Main replaced while it runs: mains.py)
                write_main(self.fifo, 'load_core menu.rbf')
        elif not req.get('status'):
            self.refresher.start()
        return {'refresh': self.refresher.status}

    def cmd_systems(self, req):
        out = []
        for pid, p in self.profiles().items():
            out.append({
                'profile': pid, 'system': p['system'], 'variant': p.get('variant', 'stock'),
                'name': p['name'], 'installed': bool(ln.installed_rbf(self.paths, p['rbf'])),
                'rom_exts': p['rom'] and p['rom']['exts'], 'games_dir': p['games_dir'],
                'disc_drive': bool(p.get('disc_drive')),   # game from the drive: launch with no rom
            })
        return {'systems': out}

    def _sync(self, p):
        """Pick up settings saved in the core's own OSD since Coreshell launched it."""
        with self.lock:
            return ln.sync_back(self.paths, p, p['system'] + _vsuffix(p))

    def cmd_tools(self, req):
        """The Scripts folder, as the stock menu's Scripts page lists it."""
        return {'tools': self.tools.list()}

    def cmd_tool_run(self, req):
        """Run one in the background (scripts that need no input)."""
        return {'tool': self.tools.run(req['name'], force=bool(req.get('force')))}

    def cmd_tool_done(self, req):
        """The frontend ran a script on the TV through its Main, and it ended."""
        return {'tool': self.tools.done(req['name'], int(req.get('exit', 0)))}

    def cmd_tool_status(self, req):
        return {'tool': self.tools.status, 'output': self.tools.output(int(req.get('lines', 40)))}

    def cmd_tool_stop(self, req):
        return {'stopped': self.tools.stop()}

    def cmd_saves(self, req):
        """The saves a game can start with (current first, then newest); pass one's
        path as launch's 'save'."""
        p = self.profile(self._pid(req))
        return {'saves': ln.list_saves(self.paths, p, req.get('rom'))}

    def cmd_sync(self, req):
        return {'synced': self._sync(self.profile(self._pid(req)))}

    def cmd_menu(self, req):
        p = self.profile(self._pid(req))
        self._sync(p)
        game = req.get('game')
        sysv, gamev, origin = self._layers(p, game)
        vals = ln.effective(p, sysv, gamev)
        items = []
        for o in pf.visible_menu(p):
            k = pf.option_key(o)
            src = origin.get(k, 'default')
            items.append({'key': k, 'page': o['page'], 'name': o['name'], 'values': o['values'],
                          'value': vals[k], 'default': o['default'], 'from': src,
                          'per_game': src in ('game', 'game_display'),
                          'per_display': src in ('display', 'game_display')})
        # Super MemCard: its page on every system it keeps saves for (no core built for it)
        items += self.smc.menu_items(p)
        # the PSX controller setup (Manual / Auto: controllers.py); while the game Auto started runs, what the core
        # has now: asked from the running Main (a change in its own menu or the frontend's counts), else Auto's record
        last = self._auto_running(req.get('rom'), p) if pf.find_option(p, 'Pad1') else None
        if last:
            last = dict(last, auto=last.get('auto') or dict(last['values']),     # (what Auto gave it at the launch)
                        values=dict(last['values'], **self._core_values(p, last['values'])))
        if pf.find_option(p, 'Pad1'):
            items.append(ct.menu_item(sysv, gamev, game, last))
        # SNAC MemCard "Real" with the core patch: front-port cards under any Pad type (the note says so)
        items = cpatch.decorate_menu(items, self.paths, ln.installed_rbf(self.paths, p['rbf']))
        # while a game Auto started runs: its Pads and SNAC MemCard as the core has them, not the user's settings
        if last:
            ct.show_running(items, last)
        return {'menu': items, 'display': self.display()[1], 'profile': self._pid(req),
                'name': p['name'], 'variant': p.get('variant', 'stock')}

    def cmd_set(self, req):
        p = self.profile(self._pid(req))
        if req['key'] == smc.CARD_KEY:
            # the card a system plays from is the card's, for every game and display
            return {'card': self.smc.pick(p, req['value'])}
        if req['key'] == ct.SETTING:
            if not pf.find_option(p, 'Pad1'):
                raise KeyError('no option %r' % req['key'])
            if req['value'] not in ct.VALUES:
                raise ValueError('%r is not one of %s' % (req['value'], ct.VALUES))
            key = p['system'] + _vsuffix(p)
            with self.lock:
                vals = ln.read_settings(self.paths, key, req.get('game'))
                vals[ct.SETTING] = req['value']
                ln.write_settings(self.paths, key, vals, req.get('game'))
            return {}
        o = pf.find_option(p, req['key'])
        if not o:
            raise KeyError('no option %r' % req['key'])
        if req['value'] not in o['values']:
            raise ValueError('%r is not one of %s' % (req['value'], o['values']))
        # the frontend gave the running core this change too ("live": Phosphor through its Main): its menus show it
        last = self._auto_running(req.get('rom')) if req.get('live') else None
        if last and pf.option_key(o) in last['values']:
            last['values'][pf.option_key(o)] = req['value']
            self._keep_auto()
        key = p['system'] + _vsuffix(p)
        disp = self._disp(req)
        with self.lock:
            vals = ln.read_settings(self.paths, key, req.get('game'), disp)
            vals[pf.option_key(o)] = req['value']
            ln.write_settings(self.paths, key, vals, req.get('game'), disp)
        return {}

    def cmd_reset(self, req):
        p = self.profile(self._pid(req))
        if req.get('key') == smc.CARD_KEY:
            return {}   # kept on the card: nothing of Coreshell's to reset
        key = p['system'] + _vsuffix(p)
        disp = self._disp(req)
        with self.lock:
            vals = ln.read_settings(self.paths, key, req.get('game'), disp)
            if req.get('key'):
                o = pf.find_option(p, req['key'])
                vals.pop(pf.option_key(o) if o else req['key'], None)
            else:
                vals = {}
            ln.write_settings(self.paths, key, vals, req.get('game'), disp)
            if req.get('key') == ct.SETTING and disp:
                # (kept without a display: cmd_set)
                vals = ln.read_settings(self.paths, key, req.get('game'))
                if vals.pop(ct.SETTING, None) is not None:
                    ln.write_settings(self.paths, key, vals, req.get('game'))
        return {}

    def _plan_args(self, req):
        p = self.profile(self._pid(req))
        self._sync(p)
        rom = req.get('rom')
        game = req.get('game') or (rom and os.path.basename(rom))
        sysv, gamev, _ = self._layers(p, game)
        return p, rom, sysv, gamev

    def cmd_plan(self, req):
        p, rom, sysv, gamev = self._plan_args(req)
        out = ln.plan(self.paths, p, rom, None, gamev, sysv, req.get('files'),
                      auto=self._auto(p, rom, req, sysv, gamev))
        self._smc_note(p, out)
        out['ini'], out['display'] = self.display()
        return {'plan': out}

    def cmd_launch(self, req):
        self.refresher.cancel()          # a game wins over updating core menus
        if hasattr(self.smc, 'pause'):
            self.smc.pause()             # (and over reading the cards' lists for the menu)
        if req.get('kind') == 'arcade':
            return self._arcade_launch(req)
        try:
            self._pid(req)
        except KeyError as e:
            if req.get('rbf') and not req.get('profile'):
                return self._bare_launch(req, e)
            raise
        p, rom, sysv, gamev = self._plan_args(req)
        _step('start', '%s %s' % (p.get('system'), rom or '(no game)'))
        card = self._card_in(p, rom, req.get('game_id'))
        auto = self._auto(p, rom, req, sysv, gamev, card, look=True)
        with self.lock:
            out = ln.launch(self.paths, p, rom, req.get('save'), gamev, sysv, fifo=self.fifo,
                            files=req.get('files'), before_start=self._ini_for_main, log=_log, auto=auto)
            if out['mode'] in ('exact', 'handoff'):
                game = req.get('game') or (rom and os.path.basename(rom))
                handed = None
                if out['mode'] == 'handoff':     # what the core runs with: the user's .CFG with Coreshell's bits
                    mask, val = ln.handoff_status(p, out['values'], out.get('set'))
                    base = ln.read_cfg(self.paths, p) or bytes(16)
                    handed = bytes((b & ~m | v & m) & 0xFF for b, m, v in zip(base.ljust(16, b'\0'), mask, val))
                ln.remember_written(self.paths, p, p['system'] + _vsuffix(p), game, self.display()[1], handed)
        if out.get('auto'):
            _step('auto', ', '.join('%s %s' % kv for kv in sorted(out['auto'].items())))
        # (what the core got with Auto, for the menu of this game: controllers.menu_item)
        self._auto_last = None if auto is None else {
            'rom': rom, 'serial': auto.get('serial'),
            'values': {k: out['values'].get(k) for k in sorted(ct.PORTS) + [ct.MEMCARD] if k in out['values']}}
        if self._auto_last:
            self._auto_last['auto'] = dict(self._auto_last['values'])     # (what Auto gave the core at the launch)
        self._keep_auto()
        self._smc_note(p, out)
        if card:
            out['card'] = card
        _step('load', out.get('mgl_file') or '?')
        if rom:
            self._announce(rom)
        out['ini'], out['display'] = self.display()
        return {'plan': out}

    def _arcade_launch(self, req):
        """An arcade game: its .mra, which names its own core and ROMs (Main reads it): Coreshell loads it as it is,
        with the launch log, as phosphord did (play.c)."""
        mra = req.get('rom') or ''
        _step('start', 'arcade %s' % (mra or '(none)'))
        if not mra.lower().endswith('.mra') or not os.path.isfile(mra):
            raise RuntimeError("That arcade game's .mra isn't there")
        with self.lock:
            bridge_replaced(self.paths.root)   # (a Main replaced while it runs: mains.py)
            write_main(self.fifo, 'load_core %s' % mra)
        _step('load', mra)
        self._announce(mra)
        return {'plan': {'mode': 'arcade', 'mgl_file': mra}}

    def _bare_launch(self, req, why):
        """A core with no profile (its menu couldn't be read: refresh.py's failed dumps): Coreshell still starts it,
        as it is, with no settings: the MGL phosphord wrote for it before (play.c), the slot from the frontend."""
        rbf, setname, rom = req['rbf'], req.get('setname') or '', req.get('rom')
        _step('start', '%s %s (no profile: %s)' % (rbf, rom or '(no game)', str(why).strip("'\"")))
        # Main finds the core by a name its file starts with, followed by "." or "_" (mra_loader.cpp get_rbf): the
        # file's name without .rbf (the date kept: that exact core), relative to the SD card
        mrbf = rbf[len(self.paths.root) + 1:] if rbf.startswith(self.paths.root + '/') else rbf
        mrbf = mrbf[:-4] if mrbf.lower().endswith('.rbf') else mrbf
        lines = ['<mistergamedescription>', '\t<rbf>%s</rbf>' % _xml(mrbf)]
        if setname:
            lines.append('\t<setname same_dir="1">%s</setname>' % setname)
        if rom:
            slot = req.get('slot') or {}
            if not slot.get('type'):
                raise RuntimeError("Coreshell doesn't know how to load this game's file into %s"
                                   % os.path.basename(rbf))
            lines.append('\t<file delay="%d" type="%s" index="%d" path="%s"/>' % (
                int(slot.get('delay') or 0), slot['type'], int(slot.get('index') or 0), _xml(rom)))
        lines.append('</mistergamedescription>')
        with self.lock:
            os.makedirs(self.paths.launch, exist_ok=True)
            mgl = os.path.join(self.paths.launch, 'bare.mgl')
            with open(mgl, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
            bridge_replaced(self.paths.root)   # (a Main replaced while it runs: mains.py)
            write_main(self.fifo, 'load_core %s' % mgl)
        _step('load', mgl)
        if rom:
            self._announce(rom)
        return {'plan': {'mode': 'bare', 'mgl_file': mgl}}

    def _keep_auto(self):
        """_auto_last into AUTO_FILE (a Coreshell started again mid-game reads it), or the file gone (None)."""
        try:
            if self._auto_last is None:
                if os.path.exists(AUTO_FILE):
                    os.remove(AUTO_FILE)
                return
            os.makedirs(os.path.dirname(AUTO_FILE), exist_ok=True)
            with open(AUTO_FILE + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(self._auto_last, f)
            os.replace(AUTO_FILE + '.tmp', AUTO_FILE)
        except OSError:
            pass

    def _auto_running(self, rom, p=None):
        """The running game's launch with Auto ({rom, serial, values}), when rom is that game and it's still running
        (not back at the menu, and p's core is the one running); else None."""
        last = self._auto_last
        if not (last and rom and last['rom'] == rom == self._announced) or self._in_menu():
            return None
        if p is not None:
            try:
                core = open(os.path.join(STATE_DIR, 'CORENAME')).read().strip()
            except OSError:
                core = ''
            if core.lower() != (p.get('core_name') or '').lower():
                return None
        return last

    def _core_values(self, p, keys):
        """{option key: value} the running core has, asked from the running Main's API (opt); {} when no Main
        answers (a stock Main) or it doesn't know."""
        for path in MAIN_SOCKS:
            if not os.path.exists(path):
                continue
            out = {}
            s = socket.socket(socket.AF_UNIX)
            try:
                s.settimeout(1.0)
                s.connect(path)
                f = s.makefile('rw')
                for k in keys:
                    o = pf.find_option(p, k)
                    if not o or not o.get('bits'):
                        continue
                    lo, hi = o['bits']
                    f.write('opt O[%s]\n' % (lo if lo == hi else '%d:%d' % (hi, lo)))
                    f.flush()
                    a = json.loads(f.readline() or '{}')
                    v = a.get('value')
                    if a.get('ok') and isinstance(v, int) and 0 <= v < len(o['values']):
                        out[pf.option_key(o)] = o['values'][v]
            except (OSError, ValueError):
                continue
            finally:
                s.close()
            return out
        return {}

    def _auto(self, p, rom, req, sysv, gamev, card=None, look=False):
        """The PSX controller setup on Auto (controllers.py): the disc's serial and, for a launch (look) from the menu
        core, what the front ports hold (smcd's PROBE); None when Auto is off or it isn't the PSX core."""
        if not pf.find_option(p, 'Pad1') or not ct.auto_on(sysv, gamev):
            return None
        serial = (card or {}).get('game_id') or req.get('game_id') or (rom and gid.psx(rom))
        ports = None
        # the front ports answer only through the menu core (a launch from a running game: not known)
        if look and self.smc.up() and self._in_menu():
            try:
                ports = self.smc.probe()
            except OSError:
                ports = None
        _step('auto', 'PSX %s: %s; front ports %s' % (serial or '?', ' '.join(ct.psx(serial)) or 'not listed',
                                                       ports if ports is not None else 'not known'))
        return {'serial': serial, 'ports': ports}

    def _card_in(self, p, rom, game_id=None):
        """Launch step 2, card in: with a Super MemCard, the card is told the game before its core loads. PSX: that
        game's own PS1 card goes on the bus (its ID from the disc image, or the frontend's game_id for CHDs)."""
        sys_name = smc.system_of(p)
        if sys_name != 'PSX':
            return None                  # other systems: Main's shim tells smcd (LAUNCH) as the core loads
        if not self.smc.up():
            _step('card', 'no smcd: the PSX core uses its own card settings')
            return None
        game = game_id or gid.psx(rom)
        if not game:
            _step('card', 'no game ID for %s: the card keeps the card it has on' % (rom or 'this launch'))
            return None
        try:
            ans = self.smc.select('PSX', game)
        except OSError as e:
            ans = 'smcd unreachable (%s)' % e
        _step('card', 'PSX %s: %s' % (game, ans))
        return {'game_id': game, 'select': ans}

    def cmd_play_save(self, req):
        """The memory manager's Play: that save's card switched in (smcd), then the game started like any launch (its
        settings, its card): {sys, card, save, port?} -> {answer: smcd's "play ok <card>" | "play notfound" | ...}."""
        sys_name, card, save = req['sys'], int(req['card']), req['save']
        _step('start', 'memory manager: %s %s, card %d' % (sys_name, save, card))
        if not self.smc.up():
            raise RuntimeError("Super MemCard isn't running")
        ans, mgl = self.smc.play_prep(sys_name, card, save, int(req.get('port') or 0))
        _step('card', '%s %s: %s' % (sys_name, save, ans))
        if not mgl:
            return {'answer': ans}
        rbf, setname, rom, slot = _mgl_game(mgl)
        if not rbf:
            raise RuntimeError("The game's MGL can't be read: %s" % mgl)
        out = self.cmd_launch(dict(rbf=rbf, setname=setname, rom=rom, slot=slot, **{'from': 'memory manager'}))
        out['answer'] = ans
        return out

    def cmd_card_event(self, req):
        """Main's shim (phosphor-main smc_shim.cpp): a core starting (LAUNCH), the menu back (MENU), a file picked in a
        core's OSD (ROM), passed on to smcd (card in / card out), with the launch log: {line} -> {reply: smcd's DONE line}.
        Main passes smcd's card frames meanwhile."""
        line = req['line'].strip('\n')
        verb = line.split(' ', 1)[0]
        if verb not in ('LAUNCH', 'MENU', 'ROM'):
            raise ValueError('not a card event: %s' % verb)
        step = {'LAUNCH': 'card in', 'MENU': 'card out', 'ROM': 'card rom'}[verb]
        parts = line[len(verb):].strip().split('\t')   # (LAUNCH <rbf>\t<mgl>; ROM <rbf>\t<file>\t<index>)
        if verb == 'MENU':
            self._announce(None)         # (the game's over: ACTIVEGAME emptied, if Coreshell wrote it)
            self._auto_last = None
            self._keep_auto()
            self.events.game_over(by='main')
        elif verb == 'ROM' and len(parts) > 1 and parts[1]:
            last = self._auto_running(self._announced)
            self._announce(parts[1])     # (a file picked in the core's OSD: the game now)
            if last:                     # (the core keeps the Pads Auto gave it: the menus show them for this disc)
                last.update(rom=parts[1], serial=gid.psx(parts[1]))
                self._keep_auto()
            self.events.game = parts[1]
            self.events._live = True
            self.events.emit('rom_loaded', game=parts[1], rbf=parts[0] or None)
        elif verb == 'LAUNCH' and time.time() - self.events.launched_at > 8:
            # a core Coreshell didn't start (Main's own menu): told as it starts
            self.events.game_started(None, rbf=parts[0] or None, by='main')
        if not self.smc.up():
            _step(step, 'no smcd')
            return {'reply': ''}
        try:
            reply = self.smc.event(line)
        except OSError as e:
            reply = ''
            _step(step, 'smcd unreachable (%s)' % e)
            return {'reply': reply}
        _step(step, '%s: %s' % (line[len(verb):].strip().replace('\t', ' | ') or '-', reply or 'no answer'))
        if reply.startswith('DONE') and verb in ('LAUNCH', 'MENU'):
            self.events.emit('saves', to='sd' if verb == 'LAUNCH' else 'card', card=reply)
        return {'reply': reply}

    def _ini_for_main(self, plan):
        """An older Main (Quake's, Duke3D's...) shows "unknown option" for INI lines newer than it. It's
        handed Coreshell's view of the INI without them while it starts (a bind mount: the user's INI is
        never written), and the view is taken away once it's up."""
        from . import inicheck as ic
        if plan.get('handoff'):
            return                       # Phosphor's Main (and any with the hook): it knows every option
        state = os.path.join(self.paths.home, 'state')
        main, ini = plan.get('main') or 'MiSTer', ln.active_ini(self.paths)
        try:
            before = ic.pids()
            hy = plan.get('hybrid')
            keys = ic.view_for_launch(self.paths.root, ini, main, state,
                                      add=(hy['section'], 'main', hy['main']) if hy else None)
        except (OSError, ic.subprocess.CalledProcessError) as e:
            plan.setdefault('warnings', []).append('INI for %s: %s' % (main, e))
            return
        plan['ini_hidden'] = keys
        if keys:
            def put_back():
                ic.main_started(main, before)
                ic.restore(self.paths.root)
            # (not a daemon: a launch from the command line waits for it, or the view outlives the process)
            threading.Thread(target=put_back, daemon=False).start()

    def _smc_note(self, p, out):
        """Super MemCard: whether this game's save comes from / goes to the card."""
        if not self.smc.handles(p):
            return
        sys_name = smc.system_of(p)
        main = smc.running_main(self.paths.root)
        if sys_name in smc.no_picker():
            out['supermemcard'] = 'own cards'
        elif main and smc.main_has_shim(main):
            out['supermemcard'] = sys_name
        else:
            out['supermemcard'] = None
            out.setdefault('warnings', []).append(
                'Super MemCard: %s doesn\'t reach the card, this game\'s save stays on the SD card'
                % os.path.basename(main or 'Main'))

    def cmd_ra_viewer(self, req):
        """RetroAchievements from the RA Viewer add-on (raviewer.py): {} status; {"games": true} the user's games;
        {"game": <RetroAchievements game id>} one game's achievements; {"playing": true} the game being played on MiSTer_RA's
        (as game, or an error when there's none); "refresh": true asks RetroAchievements again
        (through the RA Viewer, in the background: "updating" until it's done); {"setup": {"user", "password", "key"}}
        checks the account with RetroAchievements and sets the RA Viewer up with it (password and key each optional)."""
        if req.get('forget'):
            r = self.raviewer.forget()
            if not r.get('ok'):
                raise ValueError(r.get('error'))
            return {'ra': self.raviewer.status()}
        te = req.get('test')
        if isinstance(te, dict):
            r = self.raviewer.test(te.get('password'))
            if not r.get('ok'):
                raise ValueError(r.get('error') or "RetroAchievements didn't take it")
            return {'user': r.get('user'), 'ra': self.raviewer.status()}
        su = req.get('setup')
        if isinstance(su, dict):
            r = self.raviewer.setup(su.get('user'), su.get('password'), su.get('key'), check=su.get('check', True) is not False)
            if not r.get('ok'):
                raise ValueError(r.get('error') or "RetroAchievements didn't take it")   # (an error answer)
            return {'user': r.get('user'), 'ra': self.raviewer.status()}
        if req.get('playing'):
            g = self.raviewer.playing()
            if g is None:
                raise ValueError('No RetroAchievements game is being played')
            return {'ra': g}
        if req.get('game') is not None:
            return {'ra': self.raviewer.game(req['game'], refresh=bool(req.get('refresh')))}
        if req.get('games'):
            return {'ra': self.raviewer.profile(refresh=bool(req.get('refresh')))}
        return {'ra': self.raviewer.status()}

    def cmd_kernel(self, req):
        """The kernel's display (kernel.py): its state against the last known-good one. action: check (again),
        accept (the state now is good), hold / unhold (Update All's Linux held back, downloader.ini with a backup)."""
        act = req.get('action') or 'status'
        if act == 'check':
            return {'kernel': self.kernel.check()}
        if act == 'accept':
            return {'kernel': self.kernel.accept()}
        if act == 'hold':
            return self.kernel.hold()
        if act == 'unhold':
            return self.kernel.unhold()
        if act != 'status':
            raise ValueError('action: status, check, accept, hold or unhold')
        return {'kernel': self.kernel.status, 'display': self.kernel._load('display.json')}

    def cmd_kugutsu_no_jutsu(self, req):
        # 傀儡の術: every stock core on the SS1 dances on Coreshell's strings
        return {'jutsu': '傀儡の術', 'puppeteer': 'coreshell',
                'puppets': sum(1 for p in self.profiles().values()
                               if ln.installed_rbf(self.paths, p['rbf']))}

    # -------------------------------------------------------------- dispatch
    def handle(self, req):
        if not isinstance(req, dict):
            return {'ok': False, 'error': 'request must be a JSON object'}
        if req.get('v') != API:
            return {'ok': False, 'error': 'api %r not supported, this coreshell speaks %d'
                    % (req.get('v'), API), 'api': API}
        fn = getattr(self, 'cmd_' + str(req.get('cmd')), None)
        if not fn:
            return {'ok': False, 'error': 'unknown cmd %r' % req.get('cmd')}
        if req.get('cmd') == 'launch':
            self.events.launched_at = time.time()   # (Main's card event comes while it starts: not a game of Main's)
        try:
            out = fn(req)
            if req.get('cmd') == 'launch':
                plan = out.get('plan') or {}
                self.events.game_started(req.get('rom') or None, system=plan.get('system'),
                                         rbf=plan.get('rbf_file') or req.get('rbf'), by=req.get('from') or 'frontend')
        except (KeyError, ValueError, OSError, RuntimeError) as e:
            msg = str(e.args[0] if e.args else e)
            if req.get('cmd') == 'launch':
                _step('failed', msg)     # (the launch log: every launch ends in load or failed)
            return {'ok': False, 'error': msg}
        out['ok'] = True
        return out


def _vsuffix(p):
    v = p.get('variant', 'stock')
    return '' if v == 'stock' else '@' + v


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            self._lines()
        except (ConnectionResetError, BrokenPipeError):
            pass                    # (a client that went away mid-answer: nothing to log)

    def _lines(self):
        for line in self.rfile:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line.decode('utf-8'))
            except ValueError:
                resp = {'ok': False, 'error': 'bad JSON'}
            else:
                if isinstance(req, dict) and req.get('cmd') == 'subscribe' and req.get('v') == API:
                    self._subscribe(int(req.get('since') or self.server.shell.events.seq))
                    return
                resp = self.server.shell.handle(req)
            self.wfile.write((json.dumps(resp, ensure_ascii=False) + '\n').encode('utf-8'))
            self.wfile.flush()

    def _subscribe(self, seq):
        """{"cmd": "subscribe"}: {"ok": true, "seq": n}, then each event as a JSON line, until the client goes."""
        ev = self.server.shell.events
        try:
            self.wfile.write((json.dumps({'ok': True, 'seq': ev.seq}) + '\n').encode('utf-8'))
            self.wfile.flush()
            import select
            while True:
                out, last = ev.since(seq, 5)
                for e in out:
                    self.wfile.write((json.dumps(e, ensure_ascii=False) + '\n').encode('utf-8'))
                seq = max(seq, last)
                self.wfile.flush()
                r, _, _ = select.select([self.connection], [], [], 0)
                if r and not self.connection.recv(1, socket.MSG_PEEK):
                    return               # (the client went)
        except (OSError, ValueError):
            return


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, shell, path=SOCKET):
        if os.path.exists(path):
            os.unlink(path)
        socketserver.UnixStreamServer.__init__(self, path, _Handler)
        os.chmod(path, 0o666)
        self.shell = shell


def request(req, path=SOCKET):
    """Tiny client, for scripts and tests."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(path)
    try:
        s.sendall((json.dumps(req) + '\n').encode('utf-8'))
        buf = b''
        while not buf.endswith(b'\n'):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode('utf-8'))
    finally:
        s.close()
