"""Coreshell's developer command line: what's on a MiSTer (Mains, cores, what Coreshell sees), and the builds behind it
(a core's profile from its own menu, on the MiSTer; a Main with the Super MemCard / Coreshell shim, on a PC).

On the MiSTer:
    python3 -m coreshell status                 Coreshell, the Main running, the core, the game, smcd, the command pipe
    python3 -m coreshell mains scan             every Main on the SD card: md5, fork, version, shim, a shim build for it
    python3 -m coreshell mains info <file>      one Main
    python3 -m coreshell mains probe <file> [--out <file.json>]
                                             what Coreshell relies on, looked for in that Main itself (for a Main
                                             the daily watch can't check from source); a report to send in
    python3 -m coreshell mains check|restore    the shim builds put in place now | the stock builds back (they stay
                                             until `mains check`)
    python3 -m coreshell cores scan [--stale]   every core: its profile (and whether that's out of date)
    python3 -m coreshell core info <rbf|id>     a core's profile: options, slots, settings Coreshell keeps
    python3 -m coreshell core profile <rbf>     a core's profile built again from its own menu (loads the core)
    python3 -m coreshell events [--follow]      what happened (game started / ended, saves, achievements...)

On a PC (Linux, git, make, an ARM toolchain on PATH: arm-none-linux-gnueabihf-gcc 10.2):
    python3 -m coreshell mains build <fork|all> [--ref <tag>] [--patch <super-memcard main patch>] [--out <dir>]
                                             [--known <mains.json>]   (releases already in it: skipped)
                                             [--link <super-memcard main/link>]   (Phosphor's in-game link: the
                                             forks a frontend runs games on with its own menu, MiSTer_RA)
        a fork's release (the latest, or --ref) built with the shim; writes the binary and its mains.json entry
        (keyed by the release's own binary's md5, the file Coreshell finds on a MiSTer). Forks: see `mains forks`.
    python3 -m coreshell mains forks            the forks it knows how to build

The shim itself is GPL-3.0 (Main_MiSTer's licence): it lives in super-memcard (main/patch/main-mister-super-memcard
.patch), and the builds it makes are GPL-3.0 forks of each Main, credited to their authors. Coreshell only drives git
and make; none of that code is in it.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import hashlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile

from . import mains as mn

PATCH_URL = ('https://raw.githubusercontent.com/f3bandit/super-memcard/main/main/patch/'
             'main-mister-super-memcard.patch')
UPSTREAM = 'https://github.com/MiSTer-devel/Main_MiSTer'

# How each Main fork is built. kind: main (a Main_MiSTer fork: the patch applied, the conflicts settled, make),
# dvd (stock Main + the fork's overlay, its own build script), quake / 3s (a Main wrapper inside the fork's tree).
# asset: the release file (a regex) and, in a zip, the member that's the Main (a regex), for the stock build's md5.
FORKS = {
    'main': dict(repo='MiSTer-devel/Main_MiSTer', name='MiSTer', kind='main', by='Sorgelig and the MiSTer devs'),
    'ra': dict(repo='odelot/Main_MiSTer', name='MiSTer_RA', kind='main', by='odelot', link=True,
               asset=(r'^Main_Mister-.*\.zip$', r'(^|/)MiSTer$'), file=lambda t: 'Main_Mister-%s.zip' % t),
    'physical-cd': dict(repo='Anime0t4ku/Main_MiSTer_Physical_Disc', name='MiSTer_Physical-CD', kind='main',
                        by='Anime0t4ku', asset=(r'^MiSTer_Physical-CD\.zip$', r'(^|/)MiSTer_Physical-CD$'),
                        file=lambda t: 'MiSTer_Physical-CD.zip'),
    'zaparoo': dict(repo='ZaparooProject/Main_MiSTer', name='zaparoo/MiSTer_Zaparoo', kind='main',
                    by='the Zaparoo Project', asset=(r'^MiSTer_Zaparoo$', None), file=lambda t: 'MiSTer_Zaparoo',
                    tags=r'^MiSTer_Zaparoo_\d{8}$'),
    'dvd': dict(repo='owenb321/MiSTer_DVD', name='MiSTer_DVDcss', kind='dvd', by='owenb321',
                asset=(r'^MiSTer_DVD_.*\.zip$', r'(^|/)MiSTer_DVDcss$'), file=lambda t: 'MiSTer_DVD_%s.zip' % t),
    'quake': dict(repo='neofreno/Mister_Quake', name='MiSTer_Quake', kind='wrapper', by='neofreno',
                  asset=(r'^MiSTer_Quake_.*\.zip$', r'(^|/)MiSTer_Quake$'),
                  file=lambda t: 'MiSTer_Quake_%s.zip' % t.split('-', 1)[-1], tags=r'^release-\d{8}$',
                  makefile='Makefile.quake', lib='/media/fat/games/quake/lib'),
    'duke3d': dict(repo='neofreno/Mister_Duke3d', name='Mister_duke3d', kind='wrapper', by='neofreno',
                   asset=(r'^Mister_duke3d_.*\.zip$', r'(^|/)Mister_duke3d$'),
                   file=lambda t: 'Mister_duke3d_%s.zip' % t.split('-', 1)[-1], tags=r'^release-\d{8}$',
                   makefile='Makefile.duke3d', lib='/media/fat/games/DUKE3D/lib'),
    # the hybrid platform's Main (gmcnaught): no releases of its own, each port carries a copy in
    # games/<port>/platform/MiSTer_hybrid; stock: the md5s of the copies ports ship (one shim for all of them)
    'hybrid': dict(repo='gmcnaught/mister-hybrid-platform', name='games/*/platform/MiSTer_hybrid', kind='hybrid',
                   by='gmcnaught (mister-hybrid-platform)', stock_md5s=['7449221fc6411ec64a1ca321a9164d53']),
    '3s-arm': dict(repo='kimchiman52/3s-mister-arm', name='MiSTer_3S-ARM', kind='3s', by='kimchiman52',
                   asset=(r'^3s-mister-arm-.*\.zip$', r'(^|/)MiSTer_3S-ARM$'),
                   file=lambda t: '3s-mister-arm-%s-%s-%s.zip' % (t[1:5], t[5:7], t[7:9]), tags=r'^v\d{8}$'),
}

# What a binary's strings say about which fork it is
MARKS = [
    (b'/tmp/phosphor-main.sock', 'phosphor'), (b'duke3d-mister', 'duke3d'),
    (b'zaparoo_scanout', 'zaparoo'), (b'alt_launcher', 'zaparoo'),
    (b'retroachievements', 'ra'), (b'achievements_has_active_game', 'ra'),
    (b'physical_disc', 'physical-cd'), (b'dvdcss', 'dvd'), (b'quake-mister', 'quake'),
    (b'3s-arm', '3s-arm'), (b'3S-ARM', '3s-arm'), (b'phosphor-main', 'phosphor'), (b'/tmp/phosphor-main.sock', 'phosphor'),
]


def _say(*a):
    print(*a, flush=True)


# ====================================================================== on the MiSTer
def _cs(req, sock='/tmp/coreshell.sock', timeout=10):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock)
    req = dict(req, v=1)
    s.sendall((json.dumps(req) + '\n').encode())
    b = b''
    while not b.endswith(b'\n'):
        d = s.recv(1 << 16)
        if not d:
            break
        b += d
    s.close()
    return json.loads(b)


def _read(path, n=4096):
    try:
        with open(path, errors='replace') as f:
            return f.read(n).strip()
    except OSError:
        return ''


def main_info(path):
    """What a Main binary is: md5, size, shim, fork, version."""
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError as e:
        return {'file': path, 'error': str(e)}
    ver = re.search(rb'\$VER:(\d{6})', data)
    forks = [k for m, k in MARKS if m in data]
    forks = ['phosphor'] if 'phosphor' in forks else sorted(set(forks))   # (Phosphor's Main knows every fork's name)
    return {'file': path, 'md5': hashlib.md5(data).hexdigest(), 'size': len(data),
            'shim': mn.SHIM_MARK in data, 'version': ver.group(1).decode() if ver else None,
            'fork': (forks or ['main']) if data[:4] == b'\x7fELF' else ['not a program']}


# What Coreshell relies on in a Main (tools/watch.py's PROBES, which check Main_MiSTer's source), as the strings the
# built program holds. Finding them says the Main still has it; a missing one is what to look at in that Main.
BINARY_PROBES = [
    (b'load_core ', 'launching: games are started with load_core on the command pipe'),
    (b'/dev/MiSTer_cmd', 'the command pipe add-ons and Coreshell write to'),
    (b'setname', 'RA and alt cores: MGL <setname>'),
    (b'same_dir', 'RA cores: <setname same_dir="1">'),
    (b'option', 'settings by name: MGL <option>'),
    (b'.CFG', 'settings: the <core>.CFG file'),
    (b'_v%d', 'settings: the config version suffix (v,N in CONF_STR)'),
    (b'/tmp/CORENAME', 'which core runs (refresh, Super MemCard)'),
    (b'MAIN', 'main= in MiSTer.ini: which Main a core runs under'),
]


def mains_probe(path):
    """main_info plus, for each thing Coreshell relies on, whether this Main's program holds it."""
    info = main_info(path)
    if 'error' in info:
        return info
    with open(path, 'rb') as f:
        data = f.read()
    info['probes'] = [{'needs': what, 'string': s.decode(), 'found': s in data} for s, what in BINARY_PROBES]
    info['missing'] = [p['needs'] for p in info['probes'] if not p['found']]
    info['probed'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    return info


def mains_scan(root='/media/fat', home=None):
    m = mn.Mains(root, home)
    man = m.manifest()
    files = set()
    for d in (root, os.path.join(root, 'zaparoo'), os.path.join(root, 'phosphor', 'bin')):
        try:
            for n in os.listdir(d):
                p = os.path.join(d, n)
                if n.lower().startswith('mister') and os.path.isfile(p) and '.' not in n and os.access(p, os.X_OK):
                    files.add(p)
        except OSError:
            pass
    running = {exe: pid for pid, exe in m.running()}
    out = []
    for p in sorted(files):
        i = main_info(p)
        if 'md5' in i:
            ent = man.get(i['md5'])
            i['shim_build'] = ent.get('shim') if isinstance(ent, dict) else None
            i['running'] = running.get(p)
            stock = [s for s, e in man.items() if isinstance(e, dict) and os.path.join(root, e.get('name', '')) == p]
            i['stock_kept'] = [s for s in stock if os.path.exists(os.path.join(m.dir, 'stock', '%s_%s' % (
                e_name(man[s]), s)))]
        out.append(i)
    return out


def e_name(ent):
    return ent['name'].replace('/', '_')


def status(root='/media/fat'):
    st = {}
    try:
        st['coreshell'] = _cs({'cmd': 'hello'}).get('version') or 'up'
    except OSError as e:
        st['coreshell'] = 'down (%s)' % e
    m = mn.Mains(root)
    st['mains_running'] = [dict(pid=pid, file=exe, shim=mn.has_shim('/proc/%d/exe' % pid)) for pid, exe in m.running()]
    st['core'] = _read('/tmp/CORENAME')
    st['game'] = None if st['core'] == 'MENU' else (_read('/tmp/ACTIVEGAME') or _read('/tmp/FULLPATH') or None)
    st['smcd'] = 'up' if os.path.exists('/tmp/smcd.sock') else 'not running'
    import stat as stm
    def fifo(p):
        try:
            return os.stat(p).st_ino if stm.S_ISFIFO(os.stat(p).st_mode) else None
        except OSError:
            return None
    st['command_pipe'] = ('Coreshell has /dev/MiSTer_cmd (Main reads /dev/MiSTer_cmd_main)'
                          if fifo('/dev/MiSTer_cmd_main') else 'Main has /dev/MiSTer_cmd')
    return st


def events(follow=False, since=0):
    if not follow:
        for e in _cs({'cmd': 'events', 'since': since}).get('events', []):
            _say(json.dumps(e, ensure_ascii=False))
        return
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect('/tmp/coreshell.sock')
    s.sendall(b'{"v": 1, "cmd": "subscribe"}\n')
    f = s.makefile('rb')
    f.readline()
    try:
        for line in f:
            _say(line.decode('utf-8', 'replace').rstrip())
    except KeyboardInterrupt:
        pass


def cores_scan(shell, only_stale=False):
    from . import refresh as rf
    profiles = shell.profiles()
    stale = {i.get('rbf') or i.get('core') for i in rf.stale(shell.paths, profiles)} if only_stale else None
    out = []
    for pid, p in sorted(profiles.items()):
        row = dict(id=pid, system=p.get('system'), variant=p.get('variant', 'stock'), rbf=p.get('rbf'),
                   options=len(p.get('options') or p.get('menu') or []), games_dir=p.get('games_dir'))
        if stale is not None:
            if not any(s and (s == p.get('rbf') or os.path.basename(str(s)).startswith(os.path.basename(p['rbf'])))
                       for s in stale):
                continue
        out.append(row)
    if only_stale:
        out += [dict(stale=i) for i in rf.stale(shell.paths, profiles)
                if not any(r.get('rbf') and r['rbf'] in json.dumps(i) for r in out)]
    return out


def core_info(shell, what):
    try:
        pid = what if what in shell.profiles() else shell.find_profile(what)
    except KeyError:
        raise SystemExit('no profile for %s (python3 -m coreshell core profile %s builds one)' % (what, what))
    return dict(id=pid, **shell.profile(pid))


def core_profile(shell, rbf):
    """A core's profile again from its own menu: tools/dump_confstr.py loads it and reads the menu Main got, then
    tools/profiles_from_dumps.py builds the profile (as `refresh` does for every core whose profile is out of date)."""
    tools = os.path.join(shell.paths.home, 'tools')
    rbf = rbf if rbf.startswith('/') else os.path.join(shell.paths.root, rbf)
    r = subprocess.run([sys.executable, os.path.join(tools, 'dump_confstr.py'), rbf])
    if r.returncode:
        raise SystemExit('dump failed (%d)' % r.returncode)
    # this core's dump only (the dumps folder also holds older builds' dumps), named as dump_confstr.py names it
    name = os.path.relpath(rbf, shell.paths.root).replace('/', '__').replace(' ', '_')[:-4] + '.json'
    dumped = os.path.join(shell.paths.home, 'dumps', name)
    import tempfile
    one = tempfile.mkdtemp(prefix='coreshell-dump-')
    try:
        shutil.copy(dumped, one)
        _say('dump: %s' % dumped)
        r = subprocess.run([sys.executable, os.path.join(tools, 'profiles_from_dumps.py'), one])
    finally:
        shutil.rmtree(one, ignore_errors=True)
    return r.returncode


# ====================================================================== on a PC: shim builds
def _run(cmd, cwd=None, env=None, check=True, quiet=False):
    if not quiet:
        _say('  $ ' + (cmd if isinstance(cmd, str) else ' '.join(cmd)))
    r = subprocess.run(cmd, cwd=cwd, env=env, shell=isinstance(cmd, str),
                       stdout=subprocess.PIPE if quiet else None, stderr=subprocess.STDOUT if quiet else None)
    if check and r.returncode:
        out = r.stdout.decode(errors='replace')[-3000:] if quiet and r.stdout else ''
        raise RuntimeError('%s: exit %d\n%s' % (cmd if isinstance(cmd, str) else ' '.join(cmd), r.returncode, out))
    return r


def _out(cmd, cwd=None):
    """A command's output, for reading: stdout only (git's warnings on stderr would read as file names or a commit)."""
    r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode:
        raise RuntimeError('%s: exit %d\n%s' % (' '.join(cmd), r.returncode, r.stderr.decode(errors='replace')[-2000:]))
    return r.stdout.decode(errors='surrogateescape')


def _get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'coreshell-devtools'})
    tok = os.environ.get('GITHUB_TOKEN')
    if tok and 'api.github.com' in url:
        req.add_header('Authorization', 'Bearer ' + tok)
    return urllib.request.urlopen(req, timeout=120).read()


def _release(fork, ref=None):
    """The release (GitHub's API); where the API can't be reached (no token, rate limit, a proxy), its tag from git
    (--ref, or the newest tag) and its file by the fork's own naming."""
    repo = fork['repo']
    try:
        url = 'https://api.github.com/repos/%s/releases/%s' % (repo, 'tags/' + ref if ref else 'latest')
        return json.loads(_get(url))
    except (OSError, ValueError) as e:
        _say('  (GitHub API: %s: the tag from git, the file by its name)' % e)
    tag = ref
    if not tag:
        out = _out(['git', '-c', 'versionsort.suffix=-', 'ls-remote', '--tags', '--refs', '--sort=-v:refname',
                    'https://github.com/%s' % repo]).split()
        tags = [t.rsplit('/', 1)[1] for t in out if t.startswith('refs/tags/')]
        tags = [t for t in tags if re.search(fork.get('tags', r'^v?\d'), t)]
        if not tags:
            raise RuntimeError('%s: no release tag found (give --ref)' % repo)
        tag = tags[0]
    name = fork['file'](tag)
    return {'tag_name': tag, 'assets': [{'name': name, 'browser_download_url':
                                         'https://github.com/%s/releases/download/%s/%s' % (repo, tag, name)}]}


def _stock_md5(fork, rel):
    """The md5 of the fork's own build in its release (what Coreshell finds on a MiSTer)."""
    pat, member = fork['asset']
    assets = [a for a in rel.get('assets', []) if re.search(pat, a['name'])]
    if not assets:
        raise RuntimeError('%s %s: no release file matching %s' % (fork['repo'], rel.get('tag_name'), pat))
    data = _get(assets[0]['browser_download_url'])
    if member:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = [n for n in z.namelist() if re.search(member, n)]
        if not names:
            raise RuntimeError('%s: no %s in %s' % (fork['repo'], member, assets[0]['name']))
        data = z.read(names[0])
    return hashlib.md5(data).hexdigest()


def _checkout(cache, repo, ref, upstream=True):
    d = os.path.join(cache, repo.replace('/', '__'))
    if not os.path.isdir(os.path.join(d, '.git')):
        _run(['git', 'clone', '-q', 'https://github.com/%s' % repo, d])
    else:
        _run(['git', 'fetch', '-q', '--tags', 'origin'], cwd=d)
    if upstream and repo != 'MiSTer-devel/Main_MiSTer':
        # Main_MiSTer's history beside the fork's: git apply --3way finds the patch's base files there
        _run('git remote get-url up >/dev/null 2>&1 || git remote add up %s' % UPSTREAM, cwd=d, quiet=True)
        _run(['git', 'fetch', '-q', 'up'], cwd=d)
    _run(['git', 'reset', '-q', '--hard'], cwd=d)
    _run(['git', 'clean', '-qfdx'], cwd=d)
    _run(['git', 'checkout', '-q', '-f', ref], cwd=d)
    return d


# ---------------------------------------------------------------------- settling conflicts
_ROW_TAIL = '\t\t\t\tmenumask = (menumask << 1) | 1;\n\t\t\t\tentry++;\n\t\t\t\tselentry++;\n\t\t\t}\n\n'
_SEL_TAIL = '\t\t\t\t}\n\t\t\t}\n\t\t\telse '
_BUSY = 'timeout = smc_shim_busy() ? 1 : 25;   // (Super MemCard passing frames: on at once)'


def _settle(ours, theirs, fname):
    """One conflict between a fork's lines (ours) and the shim's (theirs): both kept, the way the shim needs them.
    None: not a kind seen before (settled by hand)."""
    o_lines = [l for l in ours.splitlines() if l.strip()]
    t_lines = [l for l in theirs.splitlines() if l.strip()]
    if all(l.lstrip().startswith('#include') for l in o_lines + t_lines):
        return ours + theirs
    if '" Saves"' in theirs and 'MenuWrite' in ours:             # the OSD's row: each its own block
        return ours + _ROW_TAIL + theirs
    if 'MENU_SMC_SAVES1' in theirs and 'if (manual_submenu == menusub)' in ours and 'manual_submenu' in theirs:
        # its selection, where the fork's own rows end in the manual's: put before that one
        branch = theirs[:theirs.index('else if (manual_submenu == menusub)')].rstrip() + '\n'
        ind = re.search(r'([ \t]*)if \(manual_submenu == menusub\)', ours).group(1)
        return ours.replace('if (manual_submenu == menusub)', branch.lstrip() + ind + 'else if (manual_submenu == menusub)', 1)
    if 'MENU_SMC_SAVES1' in theirs and ours.lstrip().startswith('if ('):   # its selection: one more else-if
        return ours + _SEL_TAIL + theirs
    if 'smc_submenu' in theirs and not any('{' in l or '}' in l for l in t_lines):   # its statics and reset
        return ours + theirs
    if 'smc_shim_leave_game' in theirs:
        # the fork's own work before a core loads first (Zaparoo's: its own save-on-exit, which may put the load off
        # until its save is written), then the shim's (nothing left to save then: over at once)
        return ours + theirs
    if 'smc_shim_main_start' in theirs and not any('{' in l or '}' in l for l in o_lines):
        return ours + theirs
    if 'smc_shim_poll' in theirs and 'user_io_poll' in ours:
        lines = ours.splitlines(True)
        for i, l in enumerate(lines):
            if 'user_io_poll' in l:
                ind = re.match(r'\s*', l).group(0)
                lines.insert(i + 1, ind + 'smc_shim_poll();   // Super MemCard: the card frames smcd sends, the bounce '
                                          'back from the menu core\n')
                return ''.join(lines)
    if 'smc_shim_busy' in theirs and 'timeout = 25;' in ours:
        return ours.replace('timeout = 25;', _BUSY)
    return None


def settle_conflicts(d):
    """Every conflict git apply --3way left: settled (_settle), or listed for a person."""
    left = []
    files = [f for f in _out(['git', 'diff', '--name-only', '-z', '--diff-filter=U'], cwd=d).split('\0') if f]
    for f in files:
        p = os.path.join(d, f)
        s = open(p, encoding='utf-8', errors='surrogateescape').read()
        out, pos = [], 0
        for m in re.finditer(r'<<<<<<< ours\n(.*?)=======\n(.*?)>>>>>>> theirs\n', s, re.S):
            out.append(s[pos:m.start()])
            r = _settle(m.group(1), m.group(2), f)
            if r is None:
                left.append('%s: %s' % (f, m.group(2).strip().splitlines()[0][:80]))
                r = m.group(0)
            out.append(r)
            pos = m.end()
        out.append(s[pos:])
        open(p, 'w', encoding='utf-8', errors='surrogateescape').write(''.join(out))
        if not any(x.startswith(f + ':') for x in left):
            _run(['git', 'add', f], cwd=d, quiet=True)
    return left


def settle_old_main(d):
    """A Main older than the patch's base (no manual_submenu: Quake's and 3S-ARM's wrappers): the hunks patch(1)
    couldn't place, put in by hand, the same changes."""
    p = os.path.join(d, 'menu.cpp')
    s = open(p, errors='surrogateescape').read()
    if 'static uint32_t smc_submenu;' not in s:
        s = s.replace('\tstatic uint32_t dip_submenu;\n', '\tstatic uint32_t dip_submenu;\n\tstatic uint32_t smc_submenu;'
                      '   // Super MemCard: the generic menu\'s row of "Saves" (-1: none)\n'
                      '\tstatic uint32_t smc_ret_sub = 0;\n', 1)
    if 'smc_submenu = -1;' not in s:
        s = s.replace('\t\t\tdip_submenu = -1;\n', '\t\t\tdip_submenu = -1;\n\t\t\tsmc_submenu = -1;\n', 1)
    if 'menustate = MENU_SMC_SAVES1;\n\t\t\t\t}' not in s:
        s = s.replace('\t\t\tif (dip_submenu == menusub && select)', '\t\t\tif (smc_submenu == menusub && select)\n'
                      '\t\t\t{\n\t\t\t\t// Super MemCard "Saves": the game\'s saves on the card\n'
                      '\t\t\t\tif (smc_osd_open())\n\t\t\t\t{\n\t\t\t\t\tsmc_ret_sub = menusub;\n\t\t\t\t\tmenusub = 0;\n'
                      '\t\t\t\t\tmenustate = MENU_SMC_SAVES1;\n\t\t\t\t}\n\t\t\t}\n'
                      '\t\t\telse if (dip_submenu == menusub && select)', 1)
    open(p, 'w', errors='surrogateescape').write(s)
    p = os.path.join(d, 'input.cpp')
    s = open(p, errors='surrogateescape').read()
    if '#include "smc_shim.h"' not in s:
        s = s.replace('#include "frame_timer.h"\n', '#include "frame_timer.h"\n#include "smc_shim.h"\n', 1)
    if 'smc_shim_busy' not in s:
        s = s.replace('if (is_menu() && video_fb_state()) timeout = 25;',
                      'if (is_menu() && video_fb_state()) ' + _BUSY, 1)
    open(p, 'w', errors='surrogateescape').write(s)
    # a loader with no xml (3S-ARM's fpga_load_rbf_inner): the call goes where the core name and MGL are
    p = os.path.join(d, 'fpga_io.cpp')
    s = open(p, errors='surrogateescape').read()
    call = '\tif (strcasecmp(name, "menu.rbf")) smc_shim_core_start(name, xml);   // Super MemCard: the game about to run\n'
    m = re.search(r'static int fpga_load_rbf_inner\(const char \*name, const char \*cfg\)\n\{\n(.*?)\n\}\n', s, re.S)
    if m and call in m.group(0):
        s = s.replace(call, '', 1)
        s = s.replace('\treturn fpga_load_rbf_inner(name, nullptr);',
                      call.replace('xml', 'NULL') + '\treturn fpga_load_rbf_inner(name, nullptr);', 1)
        s = s.replace('\tint ret = fpga_load_rbf_inner(name, cfg);', call + '\tint ret = fpga_load_rbf_inner(name, cfg);', 1)
    # the save written before another core loads (smc_shim_leave_game), where a wrapper's loader isn't Main's
    if 'smc_shim_leave_game()' not in s:
        leave = '\tsmc_shim_leave_game();   // Super MemCard: the game\'s own save written first (its core saves when the OSD opens)\n'
        for sig in ('int fpga_load_rbf(const char *name, const char *cfg, const char *xml)\n{\n',
                    'int fpga_load_rbf_no_restart(const char *name)\n{\n'):
            s = s.replace(sig, sig + leave, 1)
    open(p, 'w', errors='surrogateescape').write(s)
    # a file that starts with another include (the hybrid hook's #include "hybrid_hook.h" in scheduler.cpp):
    # the patch's first hunk, anchored at line 1, misses it
    p = os.path.join(d, 'scheduler.cpp')
    s = open(p, errors='surrogateescape').read() if os.path.isfile(p) else ''
    if 'smc_shim_poll();' in s and '#include "smc_shim.h"' not in s:
        open(p, 'w', errors='surrogateescape').write('#include "smc_shim.h"   // Super MemCard\n' + s)
    for f in os.listdir(d):
        if f.endswith(('.rej', '.orig')):
            os.unlink(os.path.join(d, f))


def _apply_3way(d, patch):
    r = _run(['git', 'apply', '--3way', patch], cwd=d, check=False, quiet=True)
    left = settle_conflicts(d)
    if left:
        raise RuntimeError('conflicts to settle by hand in %s:\n  %s' % (d, '\n  '.join(left)))
    if r.returncode and _run(['git', 'diff', '--cached', '--quiet', '--', 'smc_shim.cpp'], cwd=d, check=False,
                             quiet=True).returncode == 0:
        raise RuntimeError('the patch didn\'t apply in %s:\n%s' % (d, r.stdout.decode(errors='replace')[-2000:]))


def _patch_old(d, patch):
    _run('patch -p1 --forward --no-backup-if-mismatch < "%s" || true' % patch, cwd=d, quiet=True)
    settle_old_main(d)


def _make(d, *args, env=None):
    _run(['make', '-j%d' % (os.cpu_count() or 4)] + list(args), cwd=d, env=env, quiet=True)


def _link(d, link, fid):
    """Phosphor's in-game link (super-memcard main/link, GPL-3.0) hooked into the fork's tree, after the shim."""
    hooks = os.path.join(os.path.abspath(link), 'hooks.py')
    if not os.path.isfile(hooks):
        raise RuntimeError('%s: no hooks.py in %s' % (fid, link))
    _run([sys.executable, hooks, d], quiet=True)


def build_fork(fid, ref=None, patch=None, out='shim-mains', cache=None, known=None, link=None):
    fork = FORKS[fid]
    cache = cache or os.path.expanduser('~/.cache/coreshell/src')
    os.makedirs(cache, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    if not patch:
        patch = os.path.join(cache, 'main-mister-super-memcard.patch')
        open(patch, 'wb').write(_get(PATCH_URL))
    patch = os.path.abspath(patch)
    shutil.which('arm-none-linux-gnueabihf-gcc') or _say('  (no arm-none-linux-gnueabihf-gcc on PATH: make will fail)')

    if fid == 'main':
        # Main_MiSTer's releases are its releases/ folder: MiSTer_<date>, built from the commit that added it
        d = _checkout(cache, fork['repo'], 'origin/master', upstream=False)
        rels = sorted(f for f in os.listdir(os.path.join(d, 'releases')) if re.match(r'MiSTer_\d{8}$', f))
        rel_name = ref if ref and ref.startswith('MiSTer_') else (('MiSTer_' + ref) if ref else rels[-1])
        commit = _out(['git', 'log', '--diff-filter=A', '--format=%H', '-1', '--', 'releases/' + rel_name], cwd=d).strip()
        stock = hashlib.md5(open(os.path.join(d, 'releases', rel_name), 'rb').read()).hexdigest()
        tag = rel_name.split('_', 1)[1]
        _run(['git', 'checkout', '-q', '-f', commit], cwd=d)
        rel_label = '%s %s (%s)' % (fork['repo'], tag, commit[:7])
    elif fork['kind'] == 'hybrid':
        d = _checkout(cache, fork['repo'], ref or 'origin/main', upstream=False)
        tag = _out(['git', 'rev-parse', '--short', 'HEAD'], cwd=d).strip()
        up = re.search(r'UPSTREAM_COMMIT:-(\w+)', open(os.path.join(d, 'device', 'main-hook', 'build-hps.sh')).read()).group(1)
        stock = fork['stock_md5s'][0]
        rel_label = '%s %s (Main_MiSTer %s)' % (fork['repo'], tag, up[:7])
    else:
        rel = _release(fork, ref)
        tag = rel['tag_name']
        stock = _stock_md5(fork, rel)
        rel_label = '%s %s' % (fork['repo'], tag)
    _say('%s: %s, stock md5 %s' % (fid, rel_label, stock))
    if known and stock in known:
        _say('  already built (%s): skipped' % known[stock].get('shim'))
        return None

    if fork['kind'] == 'main':
        if fid != 'main':
            d = _checkout(cache, fork['repo'], tag)
        _apply_3way(d, patch)
        if fork.get('link') and link:
            _link(d, link, fid)
        elif fork.get('link'):
            _say('  (no --link: built without Phosphor\'s in-game link)')
        _make(d)
        built = os.path.join(d, 'bin', 'MiSTer')
    elif fork['kind'] == 'dvd':
        d = _checkout(cache, fork['repo'], tag, upstream=False)
        ref_main = re.search(r'MAIN_MISTER_REF="\$\{MAIN_MISTER_REF:-(\w+)\}"',
                             open(os.path.join(d, 'main', 'build_main.sh')).read()).group(1)
        m = _checkout(cache, 'MiSTer-devel/Main_MiSTer', ref_main, upstream=False)
        _apply_3way(m, patch)
        _run(['git', '-c', 'user.name=coreshell', '-c', 'user.email=coreshell@localhost', 'commit', '-qam',
              'Super MemCard shim'], cwd=m)
        env = dict(os.environ, MAIN_MISTER_SRC=m, MAIN_MISTER_REF='HEAD')
        shutil.rmtree(os.path.join(d, 'main', '.build'), ignore_errors=True)
        _run(['bash', 'main/build_main.sh'], cwd=d, env=env, quiet=True)
        built = os.path.join(d, 'main', '.build', 'MiSTer_DVDcss')
        _run(['git', 'reset', '-q', '--hard', ref_main], cwd=m)
    elif fork['kind'] == 'wrapper':
        # neofreno's ports (Quake, Duke3D): a Main in src/mister-wrapper-hps, run with the game's own libraries
        d = _checkout(cache, fork['repo'], tag, upstream=False)
        w = os.path.join(cache, fid + '-wrapper')
        shutil.rmtree(w, ignore_errors=True)
        shutil.copytree(os.path.join(d, 'src', 'mister-wrapper-hps'), w)
        _patch_old(w, patch)
        _make(w, '-f', fork['makefile'], 'BASE=arm-none-linux-gnueabihf')
        prj = re.search(r'^PRJ\s*=\s*(\S+)', open(os.path.join(w, fork['makefile'])).read(), re.M).group(1)
        built = os.path.join(w, 'bin', prj)
        if not shutil.which('patchelf'):
            raise RuntimeError('patchelf needed (pip install patchelf): %s runs with its game\'s own libraries' % prj)
        _run(['patchelf', '--set-interpreter', fork['lib'] + '/ld-linux-armhf.so.3', '--set-rpath',
              fork['lib'] + ':/lib:/usr/lib', built])
    elif fork['kind'] == 'hybrid':
        # their own build script (device/main-hook/build-hps.sh: upstream Main at its pin, the hook's edits and
        # overlay), with a stand-in for its Docker step: the shim patch applied, then MiSTer's ARM toolchain
        fake = os.path.join(cache, 'hybrid-docker')
        os.makedirs(fake, exist_ok=True)
        with open(os.path.join(fake, 'docker'), 'w') as f:
            f.write('#!/bin/sh\n'
                    '# Coreshell: docker for build-hps.sh: "image inspect" succeeds, "run -v SRC:/src ... make" is\n'
                    '# the shim patch (old-Main path) and make in SRC with arm-none-linux-gnueabihf\n'
                    '[ "$1" = image ] && exit 0\n'
                    'src=; for a; do case "$a" in *:/src) src=${a%%:/src};; esac; done\n'
                    'cd "$src" && python3 -c "import sys; sys.path.insert(0, \'%s\'); from coreshell import devtools as d; '
                    'd._patch_old(\'.\', \'%s\')" && make -f Makefile.hybrid BASE=arm-none-linux-gnueabihf -j%d\n'
                    % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), patch, os.cpu_count() or 4))
        os.chmod(os.path.join(fake, 'docker'), 0o755)
        # their pin (Main_MiSTer 3380931, Mar 2026) is older than the patch's base: the old-Main path
        # (_patch_old, as Quake's wrapper); upstream from Coreshell's own clone, no second download
        up_d = _checkout(cache, 'MiSTer-devel/Main_MiSTer', up, upstream=False)
        env = dict(os.environ, PATH=fake + os.pathsep + os.environ.get('PATH', ''), UPSTREAM_URL=up_d,
                   OUTPUT_DIR=os.path.join(cache, 'hybrid-build'))
        shutil.rmtree(env['OUTPUT_DIR'], ignore_errors=True)
        _run(['bash', 'device/main-hook/build-hps.sh'], cwd=d, env=env, quiet=True)
        built = os.path.join(env['OUTPUT_DIR'], 'MiSTer_hybrid')
    elif fork['kind'] == '3s':
        d = _checkout(cache, fork['repo'], tag, upstream=False)
        _run(['bash', 'tools/mister-wrapper/build-hps.sh', '--prepare-source'], cwd=d, quiet=True)
        w = os.path.join(d, 'build', 'mister-wrapper-hps', 'src')
        _patch_old(w, patch)
        _make(w, '-f', 'Makefile.3s-arm')
        built = os.path.join(w, 'bin', 'MiSTer_3S-ARM')
    else:
        raise RuntimeError('unknown kind %s' % fork['kind'])

    data = open(built, 'rb').read()
    if mn.SHIM_MARK not in data:
        raise RuntimeError('%s: built, but the shim isn\'t in it' % built)
    base = os.path.basename(fork['name'])
    shim_name = '%s_shim' % tag if tag.startswith(base) else '%s_%s_shim' % (base, tag)
    dest = os.path.join(out, shim_name)
    shutil.copy2(built, dest)
    os.chmod(dest, 0o755)
    # the exact source of this GPL binary (GPL: the matching source findable): the fork's commit it was built from,
    # the upstream Main it carries where the fork builds one in (DVD, hybrid), and the shim patch (super-memcard's
    # commit, from the workflow's SHIM_COMMIT, and the patch file's sha256)
    src_commit = _out(['git', 'rev-parse', 'HEAD'], cwd=d).strip()
    one = {'name': fork['name'], 'shim': shim_name, 'release': rel_label,
           'shim_md5': hashlib.md5(data).hexdigest(), 'by': fork['by'], 'licence': 'GPL-3.0',
           'src_commit': src_commit, 'src': 'https://github.com/%s/tree/%s' % (fork['repo'], src_commit),
           'shim_commit': os.environ.get('SHIM_COMMIT', ''),
           'shim_src': ('https://github.com/f3bandit/super-memcard/blob/%s/main/patch/main-mister-super-memcard.patch'
                        % os.environ['SHIM_COMMIT']) if os.environ.get('SHIM_COMMIT') else '',
           'shim_patch_sha256': hashlib.sha256(open(patch, 'rb').read()).hexdigest()}
    if fork.get('link') and link:
        one['link'] = True             # (Phosphor's in-game link: super-memcard main/link, the same commit as the shim)
        if os.environ.get('SHIM_COMMIT'):
            one['link_src'] = 'https://github.com/f3bandit/super-memcard/tree/%s/main/link' % os.environ['SHIM_COMMIT']
    if fork['kind'] == 'dvd':
        one['main_commit'] = ref_main
    elif fork['kind'] == 'hybrid':
        one['main_commit'] = up
    entry = {m: dict(one) for m in fork.get('stock_md5s', [stock])}
    mp = os.path.join(out, 'mains.json')
    try:
        man = json.load(open(mp))
    except (OSError, ValueError):
        man = {}
    man.update(entry)
    json.dump(man, open(mp, 'w'), indent=1, sort_keys=True)
    _say('  -> %s (%s)' % (dest, entry[stock]['shim_md5']))
    return dest


# ====================================================================== the command line
def main(argv, shell_factory):
    if not argv or '-h' in argv or '--help' in argv:
        print(__doc__)
        return 0 if argv else 2
    cmd, args = argv[0], argv[1:]
    js = lambda o: print(json.dumps(o, indent=1, ensure_ascii=False))
    if cmd == 'status':
        js(status())
    elif cmd == 'events':
        events(follow='--follow' in args or '-f' in args)
    elif cmd == 'mains' and args[:1] == ['scan']:
        rows = mains_scan()
        if '--json' in args:
            js(rows)
        else:
            for r in rows:
                if 'md5' not in r:
                    print('%-46s %s' % (r['file'], r.get('error')))
                    continue
                state = ('shim' + (' (stock kept)' if r['stock_kept'] else '')) if r['shim'] else (
                    'no shim: a shim build is here, put in at the next check' if r['shim_build'] else
                    'NO SHIM, no shim build known (python3 -m coreshell mains build ... on a PC)')
                print('%-46s %s  %-12s v%-7s %s%s' % (r['file'], r['md5'][:12], ','.join(r['fork']), r['version'] or '?',
                                                    state, '  [running]' if r['running'] else ''))
    elif cmd == 'mains' and args[:1] == ['info'] and len(args) > 1:
        js(main_info(args[1]))
    elif cmd == 'mains' and args[:1] == ['probe'] and len(args) > 1:
        r = mains_probe(args[1])
        if '--out' in args:
            with open(args[args.index('--out') + 1], 'w') as f:
                json.dump(r, f, indent=1)
        for p in r.get('probes', []):
            print('%-8s %-18s %s' % ('ok' if p['found'] else 'MISSING', p['string'], p['needs']))
        print('%s: %s' % (r['file'], 'everything Coreshell needs is there' if not r.get('missing')
                           else '%d missing' % len(r['missing'])) if 'probes' in r else r)
        return 1 if r.get('missing') or 'error' in r else 0
    elif cmd == 'mains' and args[:1] == ['forks']:
        js({k: '%s (%s), by %s' % (v['repo'], v['name'], v['by']) for k, v in FORKS.items()})
    elif cmd == 'mains' and args[:1] == ['build'] and len(args) > 1:
        opt = lambda n: args[args.index(n) + 1] if n in args else None
        which = list(FORKS) if args[1] == 'all' else [args[1]]
        failed = []
        known = None
        if opt('--known'):
            try:
                known = json.load(open(opt('--known')))
            except (OSError, ValueError):
                known = {}
        for fid in which:
            if fid not in FORKS:
                raise SystemExit('unknown fork %s: %s' % (fid, ', '.join(FORKS)))
            try:
                build_fork(fid, ref=opt('--ref') if len(which) == 1 else None, patch=opt('--patch'),
                           out=opt('--out') or 'shim-mains', known=known, link=opt('--link'))
            except (RuntimeError, OSError, KeyError, ValueError) as e:
                failed.append(fid)
                _say('%s: FAILED: %s' % (fid, e))
        return 1 if failed else 0
    elif cmd == 'cores' and args[:1] == ['scan']:
        js(cores_scan(shell_factory(), '--stale' in args))
    elif cmd == 'core' and args[:1] == ['info'] and len(args) > 1:
        js(core_info(shell_factory(), args[1]))
    elif cmd == 'core' and args[:1] == ['profile'] and len(args) > 1:
        return core_profile(shell_factory(), args[1])
    else:
        print(__doc__)
        return 2
    return 0
