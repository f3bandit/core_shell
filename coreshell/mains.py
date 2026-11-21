"""Every MiSTer Main that runs carries the Super MemCard / Coreshell shim (it tells Coreshell when a game starts, a file
is picked in the OSD, the menu is back, and passes the card's frames).

Phosphor's and Console Mode's Mains have it built in. For the others (stock /media/fat/MiSTer, the alternate Mains
MiSTer.ini's main= starts), Coreshell keeps a build of the same Main with the shim in /media/fat/coreshell/mains/,
listed in mains.json by the stock build's md5:

    {"2e99ab46...": {"name": "MiSTer", "shim": "MiSTer_20261010_shim", "release": "20261010"}}

A Main starts again as the file MiSTer.ini names (main=, "MiSTer" if none) each time it loads a core, so the shim build
has to be that file: Coreshell puts it there (the stock one kept in mains/stock/<name>_<md5>), and does again whenever
an update (Update All, the downloader) puts a stock build back. A Main running from a stock build while the menu is up is
started again from the shim build (as Phosphor switches Mains at boot). A stock build with no shim build yet: left as it
is, and said (the "main" event, and the log).

A Main whose file is replaced while it runs (the shim build put in, an update, a new build copied over) can't start
again by itself: each core it loads restarts it as /proc/self/exe names it, "/media/fat/MiSTer_RA (deleted)", that exec
fails and Main calls its reboot() (a hard reset of the board, no shutdown: the SD card isn't unmounted). So until it has
restarted, a file of exactly that name holds a two-line script that starts the Main's file as it is now, the same way
(the same arguments), and removes itself (bridge_replaced: each launch, each command to Main, and the watcher).

`python3 -m coreshell mains restore` puts every stock build back, and they stay (the watcher leaves the Main files
alone, mains/restored) until `python3 -m coreshell mains check` puts the shim builds in place again.
"""
import glob
import hashlib
import json
import os
import shutil
import signal
import subprocess
import threading
import time

SHIM_MARK = b'/tmp/smcd.sock'          # (as supermemcard.main_has_shim)


def md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def has_shim(path):
    try:
        with open(path, 'rb') as f:
            return SHIM_MARK in f.read()
    except OSError:
        return False


DELETED = ' (deleted)'
BRIDGE_MARK = '# coreshell: a replaced Main\'s restart'


_bridges = set()                        # the scripts this Coreshell wrote (a Main in a folder: zaparoo/, games/*/platform/)


def _running_exes():
    out = []
    for d in os.listdir('/proc'):
        if not d.isdigit():
            continue
        try:
            out.append(os.readlink('/proc/%s/exe' % d))
        except OSError:
            pass
    return out


def bridge_replaced(root='/media/fat', exes=None, say=None):
    """Each Main running from a file replaced since it started: the script its next restart runs (see above), at the
    name its restart uses. Scripts no Main needs any more are removed. Returns the scripts written."""
    exes = _running_exes() if exes is None else exes
    made, want = [], set()
    for exe in exes:
        if not exe.endswith(DELETED) or not exe.startswith(root + '/'):
            continue
        b = os.path.basename(exe).lower()
        if not (b.startswith('mister') or b.startswith('corehost')):
            continue
        real = exe[:-len(DELETED)]
        if not os.path.isfile(real):
            continue
        want.add(exe)
        try:
            with open(exe) as f:
                if BRIDGE_MARK in f.read(200):
                    continue
        except OSError:
            pass
        except UnicodeDecodeError:
            continue                           # (a real file of that name: never touched)
        tmp = exe + '.coreshell'
        with open(tmp, 'w') as f:
            f.write('#!/bin/sh\n%s\nrm -f "$0"\nexec %s "$@"\n' % (BRIDGE_MARK, _sh_quote(real)))
        os.chmod(tmp, 0o755)
        os.rename(tmp, exe)
        made.append(exe)
        _bridges.add(exe)
        if say:
            say('mains: %s was replaced while it runs: its next restart starts the new file' % real)
    for p in set(glob.glob(os.path.join(glob.escape(root), '*' + DELETED))) | set(_bridges):
        if p in want:
            continue
        _bridges.discard(p)
        try:
            with open(p) as f:
                if BRIDGE_MARK in f.read(200):
                    os.remove(p)
        except (OSError, UnicodeDecodeError):
            pass
    return made


def _sh_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


class Mains(object):
    def __init__(self, root='/media/fat', home=None, say=None, emit=None, corename='/tmp/CORENAME'):
        self.root = root
        self.home = home or os.path.join(root, 'coreshell')
        self.dir = os.path.join(self.home, 'mains')
        self.say = say or (lambda *a: None)
        self.emit = emit or (lambda *a, **k: None)
        self.corename = corename
        self._seen = {}                  # path -> (size, mtime) last looked at
        self._warned = set()
        self._stop = threading.Event()

    def manifest(self):
        try:
            with open(os.path.join(self.dir, 'mains.json')) as f:
                m = json.load(f)
            return m if isinstance(m, dict) else {}
        except (OSError, ValueError):
            return {}

    def _targets(self, man):
        """The Main files to look at: every name the manifest knows, in the SD card's root."""
        names = {v.get('name') for v in man.values() if isinstance(v, dict) and v.get('name')}
        names.add('MiSTer')
        # (a name may be in a folder of the SD card: zaparoo/MiSTer_Zaparoo; never outside it. A name with a * is
        # every file it matches: games/*/platform/MiSTer_hybrid, the hybrid platform's Main each port carries)
        out = []
        for n in sorted(names):
            if not n or n.startswith('/') or '..' in n.split('/'):
                continue
            p = os.path.join(self.root, n)
            out += sorted(glob.glob(p)) if '*' in n else [p]
        return out

    # ------------------------------------------------------------------ the files
    def _restored(self):
        return os.path.join(self.dir, 'restored')

    def check(self, force=False):
        """Each Main file that's a stock build with a shim build: the shim build put in its place. Returns the
        files replaced. After `mains restore` only force (`mains check`) does it, and the stock builds stay till then."""
        if force:
            try:
                os.remove(self._restored())
            except OSError:
                pass
        elif os.path.exists(self._restored()):
            return []
        man = self.manifest()
        done = []
        for path in self._targets(man):
            try:
                st = os.stat(path)
            except OSError:
                continue
            key = (st.st_size, st.st_mtime)
            if not force and self._seen.get(path) == key:
                continue
            self._seen[path] = key
            if has_shim(path):
                continue
            sum_ = md5(path)
            ent = man.get(sum_)
            shim = os.path.join(self.dir, ent['shim']) if isinstance(ent, dict) and ent.get('shim') else None
            if not shim or not os.path.isfile(shim) or not has_shim(shim):
                if (path, sum_) not in self._warned:
                    self._warned.add((path, sum_))
                    self.say('mains: %s (%s) has no shim build: left as it is' % (path, sum_))
                    self.emit('main', main=path, md5=sum_, shim=False)
                continue
            keep = os.path.join(self.dir, 'stock', '%s_%s' % (os.path.relpath(path, self.root).replace('/', '_'), sum_))
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            if not os.path.exists(keep):
                shutil.copy2(path, keep)
            tmp = path + '.coreshell'
            shutil.copy2(shim, tmp)
            os.chmod(tmp, 0o755)
            os.rename(tmp, path)          # (a Main running from the stock build keeps running it)
            st = os.stat(path)
            self._seen[path] = (st.st_size, st.st_mtime)
            done.append(path)
            self.say('mains: %s: the shim build of %s (stock kept in %s)' % (path, ent.get('release', sum_), keep))
            self.emit('main', main=path, md5=sum_, shim=True, release=ent.get('release'))
        if done:
            bridge_replaced(self.root, say=self.say)
        return done

    def restore(self):
        """Every stock build back where it was (Coreshell taken off)."""
        man = self.manifest()
        back = []
        for path in self._targets(man):
            if not has_shim(path):
                continue
            rel = os.path.relpath(path, self.root).replace('/', '_')
            for sum_ in man:
                keep = os.path.join(self.dir, 'stock', '%s_%s' % (rel, sum_))
                if os.path.isfile(keep):
                    tmp = path + '.coreshell'
                    shutil.copy2(keep, tmp)
                    os.rename(tmp, path)
                    back.append(path)
                    break
        os.makedirs(self.dir, exist_ok=True)
        with open(self._restored(), 'w') as f:          # (the watcher leaves the files alone until `mains check`)
            f.write(time.strftime('%Y-%m-%dT%H:%M:%S') + '\n')
        return back

    # ------------------------------------------------------------------ the Main running
    def running(self):
        """(pid, exe path) of each MiSTer Main running."""
        out = []
        for d in os.listdir('/proc'):
            if not d.isdigit():
                continue
            try:
                exe = os.readlink('/proc/%s/exe' % d)
            except OSError:
                continue
            if os.path.basename(exe).split(' ')[0].startswith(('MiSTer', 'Mister')):
                out.append((int(d), exe.replace(' (deleted)', '')))
        return out

    def in_menu(self):
        try:
            return open(self.corename).read().strip() == 'MENU'
        except OSError:
            return False

    def swap_running(self):
        """A Main running from a stock build (its file has the shim build now) while the menu is up: started again
        from the shim build, the way it was started (from the SD card's root, no arguments: the menu)."""
        if not self.in_menu():
            return None
        for pid, exe in self.running():
            try:
                stale = os.path.exists('/proc/%d/exe' % pid) and not has_shim('/proc/%d/exe' % pid)
            except OSError:
                continue
            if not stale or not has_shim(exe):
                continue                 # (its file is still stock, or it runs the shim build already)
            self.say('mains: %s (pid %d) runs the stock build: started again from the shim build' % (exe, pid))
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return None
            for _ in range(50):
                if not os.path.exists('/proc/%d' % pid):
                    break
                time.sleep(0.1)
            subprocess.Popen([exe], cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            self.emit('main', main=exe, restarted=True)
            return exe
        return None

    # ------------------------------------------------------------------ in the background
    def start(self, every=15):
        def run():
            while not self._stop.is_set():
                try:
                    if self.check():
                        pass
                    if not os.path.exists(self._restored()):
                        self.swap_running()
                    bridge_replaced(self.root, say=self.say)
                except Exception as e:         # (never stops Coreshell)
                    self.say('mains: %s' % e)
                self._stop.wait(every)
        threading.Thread(target=run, name='mains', daemon=True).start()

    def stop(self):
        self._stop.set()
