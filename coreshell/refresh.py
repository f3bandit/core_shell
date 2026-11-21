"""Coreshell - keep profiles in step with the cores on this MiSTer.

After Update All changes a core, its profile no longer matches the installed
build (Coreshell then launches it by option name, or with the core's own
settings, and warns). refresh() finds those cores, and installed cores with no
profile yet, dumps each one's menu here with tools/dump_confstr.py (it loads
the core, so it runs only while the menu is up) and rebuilds its profile with
tools/profiles_from_dumps.py. No download, nothing from GitHub: the cores on
the device are the source.

A core whose dump fails is remembered by its md5 (refresh.json) and not tried
again until it changes, so one bad core doesn't make every start slow.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import glob
import json
import os
import re
import subprocess
import sys
import threading
import time

from . import launcher as ln

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools')
from . import quirks             # (the folders and the cores to skip: quirks tables core_folders / core_skip)
_DATE = re.compile(r'(_\d{8})?\.rbf$', re.I)


def _tools():
    if TOOLS not in sys.path:
        sys.path.insert(0, TOOLS)
    import profiles_from_dumps as pfd     # noqa: E402  (tools/, as on the MiSTer)
    return pfd


def core_base(paths, rbf_file):
    """'/media/fat/_Console/NES_20260823.rbf' -> '_Console/NES'."""
    return _DATE.sub('', os.path.relpath(rbf_file, paths.root))


def installed(paths):
    """Newest build of every installed core: {base: rbf file}."""
    out = {}
    skip = quirks.core_skip()
    for d in quirks.get('core_folders'):
        for f in sorted(glob.glob(os.path.join(paths.root, d, '*.rbf'))):
            if not skip.match(os.path.basename(f)):
                out[core_base(paths, f)] = f      # sorted: the newest date wins
    return out


def state_path(paths):
    return os.path.join(paths.home, 'refresh.json')


def read_state(paths):
    try:
        with open(state_path(paths), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_state(paths, st):
    tmp = state_path(paths) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(st, f, indent=1, sort_keys=True)
    os.replace(tmp, state_path(paths))


def _md5_cache_load(paths):
    """Hashing ~200 cores takes minutes on the ARM: md5s are kept by (path, mtime, size)."""
    for path, (mt, size, h) in read_state(paths).get('md5', {}).items():
        ln._MD5.setdefault((path, mt, size), h)


def _md5_cache_save(paths, files):
    st = read_state(paths)
    keep = {}
    for (path, mt, size), h in list(ln._MD5.items()):
        if path in files:
            keep[path] = [mt, size, h]
    if keep != st.get('md5'):
        st['md5'] = keep
        write_state(paths, st)


def stale(paths, profiles):
    """Cores to dump: [{base, rbf, profile (None: new), why}].

    A profile whose installed build differs (md5 or dated name), and an
    installed core no profile covers. Cores whose dump already failed at this
    md5 are left out."""
    failed = read_state(paths).get('failed', {})
    _md5_cache_load(paths)
    cores = installed(paths)
    covered = {}
    for pid, p in profiles.items():
        if p.get('variant') != 'disc':           # made from the stock profile, not dumped
            covered.setdefault(p['rbf'], []).append(pid)
    out = []
    for base, rbf in sorted(cores.items()):
        pids = covered.get(base)
        if pids:
            p = profiles[pids[0]]
            if ln.is_exact(p, rbf):
                continue
            why = 'changed'
        else:
            why = 'new'
        if failed.get(base) == ln.file_md5(rbf):
            continue
        out.append({'base': base, 'rbf': rbf, 'profile': pids[0] if pids else None, 'why': why})
    _md5_cache_save(paths, set(cores.values()))
    return out


DISC_DIR = '_Physical Disc Cores'
_MGL_RBF = re.compile(r'<rbf>\s*([^<]+?)\s*</rbf>')
_MGL_SET = re.compile(r'<setname[^>]*>\s*([^<]+?)\s*</setname>')


def disc_profiles(paths, profiles):
    """Physical-CD games: <system>@disc profiles from the MGLs Update All puts in
    '_Physical Disc Cores' (<rbf>_Console/PSX</rbf> <setname>A0CD-PSX</setname>).

    The core is the stock one, so the profile is the stock core's, under that
    set name: MiSTer.ini's [A0CD-*] main=MiSTer_Physical-CD runs it, the game
    comes from the drive (no game file), and its settings keep their own
    config file (A0CD-PSX_v1.CFG). Rebuilt whenever the stock profile changes.
    Returns the profile ids written."""
    import copy
    from . import profile as pf
    stock = {p['rbf']: (pid, p) for pid, p in profiles.items() if p.get('variant', 'stock') == 'stock'}
    out = []
    for mgl in sorted(glob.glob(os.path.join(paths.root, DISC_DIR, '*.mgl'))):
        try:
            with open(mgl, encoding='utf-8', errors='replace') as f:
                t = f.read()
        except OSError:
            continue
        r, sn = _MGL_RBF.search(t), _MGL_SET.search(t)
        if not r or not sn or r.group(1) not in stock:
            continue
        base_id, base = stock[r.group(1)]
        setname = sn.group(1)
        system = re.sub(r'[^a-z0-9]+', '', re.sub(r'^A0CD-', '', setname, flags=re.I).lower())
        prof = copy.deepcopy(base)
        prof.update({
            'system': system, 'variant': 'disc', 'setname': setname,
            'name': os.path.splitext(os.path.basename(mgl))[0] + ' (disc drive)',
            'config_file': setname + base['config_file'][len(base.get('setname') or base['core_name']):],
            'rom': None, 'disc_drive': True, 'mgl': os.path.relpath(mgl, paths.root),
            'from_profile': base_id,
        })
        pid = system + '@disc'
        path = os.path.join(paths.systems, pid + '.json')
        try:
            if pf.load(path) == prof:
                continue
        except (OSError, ValueError):
            pass
        pf.save(prof, path)
        out.append(pid)
    return out


def dump_name(paths, rbf):
    """The file tools/dump_confstr.py writes for this core."""
    return os.path.join(paths.home, 'dumps', os.path.relpath(rbf, paths.root)
                        .replace('/', '__').replace(' ', '_')[:-4] + '.json')


def rebuild(paths, item, profiles):
    """The dump of item -> its profile in systems/. Returns the profile id."""
    pfd = _tools()
    with open(dump_name(paths, item['rbf']), encoding='utf-8') as f:
        d = json.load(f)
    old = profiles.get(item['profile']) if item['profile'] else None
    setnames = {item['base']: old['setname']} if old and old.get('setname') else {}
    pfd.SYSTEMS = paths.systems                  # overrides read from this MiSTer's systems/
    fname, prof = pfd.from_dump(d, setnames)
    if old:
        fname = item['profile']                  # same file: settings keep their profile
    elif os.path.exists(os.path.join(paths.systems, fname + '.json')):
        fname += '@' + re.sub(r'[^a-z0-9]+', '', os.path.basename(item['base']).lower())
    from . import profile as pf
    pf.save(prof, os.path.join(paths.systems, fname + '.json'))
    return fname


class Refresher(object):
    """refresh() in the background, cancelled by a launch."""

    def __init__(self, shell, corename='/tmp/CORENAME'):
        self.shell = shell
        self.corename = corename
        self.lock = threading.Lock()
        self.proc = None
        self.cancelled = False
        self.again = False
        self.status = {'state': 'idle'}

    def in_menu(self):
        try:
            with open(self.corename) as f:
                return f.read().strip().upper() == 'MENU'
        except OSError:
            return True              # Main hasn't loaded a core yet: the menu

    def start(self, wait=0):
        with self.lock:
            if self.status['state'] == 'running':
                self.again = True        # cores may have changed after this run's scan: look again after it
                return False
            self.again = False
            self.cancelled = False
            self.status = {'state': 'running', 'done': 0, 'total': 0, 'core': None, 'disc': [],
                           'updated': [], 'failed': []}
        threading.Thread(target=self._run, args=(wait,), daemon=True).start()
        return True

    def cancel(self):
        """A game is starting: stop dumping (the menu is reloaded by the dumper)."""
        self.cancelled = True
        p = self.proc
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
            return True
        return False

    def _run(self, wait):
        st = self.status
        try:
            time.sleep(wait)
            paths = self.shell.paths
            profiles = self.shell.profiles()
            st['disc'] = disc_profiles(paths, profiles)
            todo = stale(paths, profiles)
            st['total'] = len(todo)
            if not todo or self.cancelled:
                return
            if not self.in_menu():
                st['state'] = 'skipped'
                st['why'] = 'a core is running'
                return
            state = read_state(paths)
            failed = state.setdefault('failed', {})
            for item in todo:
                if self.cancelled:
                    break
                st['core'] = item['base']
                # one core per run of the dumper: its own 45 s watchdog, and a launch can stop it
                self.proc = subprocess.Popen(
                    [sys.executable, os.path.join(TOOLS, 'dump_confstr.py'), item['rbf']],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    self.proc.wait(90)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                if self.cancelled:
                    break
                try:
                    pid = rebuild(paths, item, profiles)
                    failed.pop(item['base'], None)
                    st['updated'].append(pid)
                except (OSError, ValueError, KeyError):
                    failed[item['base']] = ln.file_md5(item['rbf'])
                    st['failed'].append(item['base'])
                st['done'] += 1
            if st['updated']:
                st['disc'] += disc_profiles(paths, self.shell.profiles())   # their stock cores changed
            state['last'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            write_state(paths, state)
        finally:
            self.proc = None
            if st['state'] == 'running':
                st['state'] = 'cancelled' if self.cancelled else 'done'
            st['core'] = None
            if self.again and not self.cancelled:
                self.start(wait=2)
