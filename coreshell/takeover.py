"""When an add-on takes the system over (no Main running: DreamSTer, a port's own program), and how it comes back to the
frontend that started it, whatever the add-on's own menu offers (docs/stock-behaviour.md, addons.py).

- add-on started: no Main has been running for 2 s (a Main restarting on a core load, its binary still running before
  it opens the pipe again, is not an add-on). Event "addon_started" (its name when Coreshell knows it).
- the way out, always: the menu button (a pad's Home / guide button, or F12) held 3 s while it runs. Coreshell stops
  the add-on's processes and starts the frontend's Main on the menu core (as phosphor.sh does). Nothing can strand the
  user, even an add-on Coreshell has no entry for yet.
- back: a Main runs again. Event "addon_ended" (how: "exit" or "escape").
- the right Main: an add-on that starts /media/fat/MiSTer on exit gets the frontend's only through MiSTer.ini's
  [MiSTer] main=. If stock MiSTer is still the one running in the menu 6 s later and the frontend registered its Main
  (`frontend` command), that Main is started in its place, as Phosphor's boot does (phosphor.sh switch_main).

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import os
import select
import signal
import struct
import subprocess
import threading
import time

from . import addons as ad
from .cmdpipe import _Inotify

MENU_KEYS = (0x13c, 0x58, 0xac)        # BTN_MODE (guide / Home), KEY_F12, KEY_HOMEPAGE
EV_KEY = 1
EV_SIZE = struct.calcsize('llHHi')    # struct input_event: 16 bytes on the MiSTer's 32-bit ARM
HOLD_S = 3.0
GONE_S = 2.0
STOCK_GRACE_S = 6.0


_pipe_fd = {}         # pid -> the fd number its pipe was found on (a known Main is checked by that one link)


def _main_of(p):
    """(process name, exe) when process p is a Main holding /dev/MiSTer_cmd(_main), else None"""
    try:
        fds = os.listdir('/proc/%s/fd' % p)
    except OSError:
        return None
    for fd in fds:
        try:
            if os.readlink('/proc/%s/fd/%s' % (p, fd)).startswith('/dev/MiSTer_cmd'):
                comm = open('/proc/%s/comm' % p).read().strip()
                exe = os.readlink('/proc/%s/exe' % p).replace(' (deleted)', '')
                if comm.lower().startswith('mister') or comm == 'corehost':
                    _pipe_fd[str(p)] = fd
                    return (comm, exe)
                return None
        except OSError:
            continue
    return None


def _still(p, was):
    """a known Main still holding its pipe: the one link it was found on (one readlink, not every file it has)"""
    fd = _pipe_fd.get(str(p))
    if fd is not None:
        try:
            if os.readlink('/proc/%s/fd/%s' % (p, fd)).startswith('/dev/MiSTer_cmd'):
                return was
        except OSError:
            pass
    return _main_of(p)


_known = {}
_full_at = 0.0
FULL_S = 30.0         # (all of /proc this often anyway; at once when something opens the pipe: a Main starting)
_opens = None         # inotify on /dev: the pipe opened (made on first use)


def _pipe_opened():
    """something opened /dev/MiSTer_cmd(_main) since the last look (a Main starting beside the known one); None when
    there's no watch"""
    global _opens
    if _opens is None:
        _opens = _Inotify('/dev', _Inotify.IN_OPEN)
    return _opens.seen(('MiSTer_cmd',))


def main_processes():
    """The Mains running: {pid: (process name, exe)} for processes holding /dev/MiSTer_cmd(_main).
    The Mains found last time are looked at first, alone: while one still holds the pipe that's the answer. Every
    process's every file was looked at 4 times a second before, 14% of a CPU core all the time, and the menu's
    drawing stalled (300 ms, Nov 17 2026). A known Main is checked by the one link its pipe is on; all of /proc only
    when no Main known is there (a core load, an add-on), when something opened the pipe (inotify), or every FULL_S
    (Nov 19 2026: it was every file of the known Main 4 times a second, and all of /proc every 5 s)."""
    global _known, _full_at
    now = time.monotonic()
    opened = _pipe_opened()
    if _known and now - _full_at < (FULL_S if opened is not None else 5.0) and not opened:
        still = {}
        for p, was in _known.items():
            m = _still(p, was)
            if m:
                still[p] = m
        if still:
            _known = still
            return dict(still)
    out = {}
    for p in os.listdir('/proc'):
        if p.isdigit():
            m = _main_of(p)
            if m:
                out[int(p)] = m
    _known, _full_at = out, now
    return dict(out)


def exes_running():
    """{pid: exe} of every process (what a Main restarting looks like: its binary running, the pipe not open yet)."""
    out = {}
    for p in os.listdir('/proc'):
        if p.isdigit():
            try:
                out[int(p)] = os.readlink('/proc/%s/exe' % p).replace(' (deleted)', '')
            except OSError:
                pass
    return out


def _cmdline(pid):
    try:
        return open('/proc/%d/cmdline' % pid, 'rb').read().split(b'\0')
    except OSError:
        return []


def addon_processes(home):
    """The processes of an add-on Coreshell knows (its script running), or of a script from the Scripts menu
    (/tmp/script): what the escape stops."""
    scripts = [e['script'].encode() for e in ad.entries(home)]
    out = []
    for p in os.listdir('/proc'):
        if not p.isdigit() or int(p) == os.getpid():
            continue
        a = _cmdline(int(p))
        if any(x.endswith(b'/tmp/script') or x == b'/tmp/script' for x in a) or \
           any(os.path.basename(x) in scripts for x in a) or b'/tmp/coreshell-addons/' in b' '.join(a):
            out.append(int(p))
    return out


def addon_name(home):
    for e in ad.entries(home):
        for p in addon_processes(home):
            if any(os.path.basename(x) == e['script'].encode() for x in _cmdline(p)):
                return e.get('name')
    return None


class Takeover:
    def __init__(self, home, root, emit=None, log=None, mains=main_processes, start=None, kill=None, clock=time.time,
                 in_menu=None, exes=exes_running):
        self.home, self.root = home, root
        self.emit = emit or (lambda *a, **k: None)
        self.log = log or (lambda s: None)
        self.mains = mains
        self.start_main = start or self._start_main
        self.kill = kill or self._kill
        self.clock = clock
        self.in_menu = in_menu or self._in_menu
        self.exes = exes
        self.main_exes = set()          # the Mains' binaries seen with the pipe open
        self.state = 'none'             # none (no Main seen yet: boot) | main | gone | addon
        self.since = clock()
        self.name = None
        self.held = {}                  # device fd -> when the menu key went down
        self.escaped = False
        self.stock_since = None
        self.stop_ev = threading.Event()

    # ---- the pieces that touch the system (replaced in tests)
    def _in_menu(self):
        try:
            return open('/tmp/CORENAME').read().strip() == 'MENU'
        except OSError:
            return False

    def frontend_main(self):
        m = ad.frontend(self.home).get('main')
        return m if m and os.access(m, os.X_OK) else None

    def _start_main(self, path):
        subprocess.Popen([path, os.path.join(self.root, 'menu.rbf')], cwd=self.root, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def _kill(self, pids):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for p in pids:
                try:
                    os.kill(p, sig)
                except OSError:
                    pass
            time.sleep(1.0)

    # ---- one look (every 0.25 s)
    def step(self, menu_held=False):
        now = self.clock()
        running = self.mains()
        if running:
            self.main_exes |= {exe for _, exe in running.values()}
        elif self.state in ('main', 'gone') and self.main_exes and \
                any(exe in self.main_exes for exe in self.exes().values()):
            # a Main restarting, not an add-on: stock Main execs itself on every core load (and the frontend's
            # Main does as stock does), and opens the pipe again a few seconds later
            self.since = now
            return
        if running:
            if self.state == 'addon':
                self.emit('addon_ended', name=self.name, how='escape' if self.escaped else 'exit')
                self.log('takeover: %s ended (%s)' % (self.name or 'an add-on', 'escape' if self.escaped else 'exit'))
            if self.state != 'main':
                self.state, self.escaped = 'main', False
            self.since = now                # (a Main last seen)
            self._right_main(running, now)
            return
        self.stock_since = None
        if self.state == 'none':
            return                          # (at boot, before any Main: not an add-on)
        if self.state == 'main':
            self.state = 'gone'
        if self.state == 'gone' and now - self.since >= GONE_S:
            self.state, self.name = 'addon', addon_name(self.home)
            self.emit('addon_started', name=self.name)
            self.log('takeover: %s has the system (no Main)' % (self.name or 'an add-on'))
        if self.state == 'addon' and menu_held and not self.escaped:
            self.escape()

    def escape(self):
        main = self.frontend_main() or os.path.join(self.root, 'MiSTer')
        self.log('takeover: the menu button held: %s stopped, %s started' % (self.name or 'the add-on', main))
        self.escaped = True
        self.kill(addon_processes(self.home))
        self.start_main(main)

    def _right_main(self, running, now):
        """stock MiSTer still the Main in the menu after an add-on brought it back: the frontend's in its place"""
        stock = [p for p, (comm, exe) in running.items() if exe == os.path.join(self.root, 'MiSTer')]
        fe = self.frontend_main() if stock else None     # (read only when stock MiSTer is the one running)
        if not fe or not stock or any(exe == fe for _, exe in running.values()) or not self.in_menu():
            self.stock_since = None
            return
        if self.stock_since is None:
            self.stock_since = now
        elif now - self.stock_since >= STOCK_GRACE_S:
            self.log('takeover: stock MiSTer in the menu, the frontend registered %s: started in its place' % fe)
            self.kill(stock)
            self.start_main(fe)
            self.stock_since = None

    # ---- the thread: the menu button on every input device, and a look every 0.25 s
    def run(self):
        devs = {}
        last_scan = 0
        while not self.stop_ev.is_set():
            now = time.time()
            if self.state == 'addon' and now - last_scan > 2:
                last_scan = now
                for n in os.listdir('/dev/input'):
                    if n.startswith('event') and n not in devs:
                        try:
                            devs[n] = os.open('/dev/input/' + n, os.O_RDONLY | os.O_NONBLOCK)
                        except OSError:
                            pass
            if self.state != 'addon' and devs:
                for fd in devs.values():
                    os.close(fd)
                devs, self.held = {}, {}
            held = False
            if devs:
                try:
                    r, _, _ = select.select(list(devs.values()), [], [], 0.25)
                except (OSError, ValueError):
                    r = []
                for fd in r:
                    try:
                        data = os.read(fd, EV_SIZE * 32)
                    except OSError:
                        data = b''
                    for i in range(0, len(data) - EV_SIZE + 1, EV_SIZE):
                        _s, _u, t, c, v = struct.unpack_from('llHHi', data, i)
                        if t == EV_KEY and c in MENU_KEYS:
                            if v == 1:
                                self.held[fd] = now
                            elif v == 0:
                                self.held.pop(fd, None)
                held = any(now - t0 >= HOLD_S for t0 in self.held.values())
            else:
                # a Main running (the usual): once a second is enough (its going is 2 s, GONE_S); 4 times a second
                # while one has gone or an add-on runs (the menu button's hold, a restart told from an add-on)
                time.sleep(1.0 if self.state == 'main' else 0.25)
            try:
                self.step(held)
            except Exception as e:      # (never stops watching)
                self.log('takeover: %s' % e)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self.stop_ev.set()
