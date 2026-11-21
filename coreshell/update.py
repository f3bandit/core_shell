"""coreshell update: Coreshell brought up to its latest release from GitHub, all of it in one go: the code, the tools,
the core profiles, the quirks tables, the ARM program (corepatch), the Mains manifest, and the
shim build of any Main on this MiSTer that has one in the release (fetched by its md5).

    python3 -m coreshell update            the latest release, if it's newer than this one
    python3 -m coreshell update --check    only say what's there
    python3 -m coreshell update --force    the latest release even if it isn't newer (a broken install mended)
    python3 -m coreshell update --mains    only the shim builds of the Mains on this MiSTer
    python3 -m coreshell update --rollback the install before the last update back
    python3 -m coreshell update --file <coreshell.tar.gz>   a release archive copied here by hand

Where it comes from (coreshell/settings/update.json, all optional):
    {"repo": "f3bandit/core_shell", "token": "<a GitHub token, for a private repository>",
     "mains_tag": "shim-mains", "auto": false}
A release has coreshell.tar.gz (and coreshell.tar.gz.sha256); the shim builds are the assets of the release
"shim-mains" (each one, and mains.json). Both are made by the repository's GitHub Actions (docs/UPDATING.md).

What it never touches: settings/, state/, the user's saves and INIs, launch/, written/, dumps/, mains/stock/ (the
stock Mains kept), refresh.json. The install it replaces is kept in backups/update/ (the last 3).

A profile the release has for a core this MiSTer already re-dumped (the installed core is newer than the release's
profile, and this one matches it) is kept: the release never puts an older profile over a newer one.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

HOME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = 'f3bandit/core_shell'
MAINS_TAG = 'shim-mains'
# What a release replaces (whole folders: code; merged: data)
CODE = ['coreshell', 'tools', 'scripts', 'quirks', 'corepatch', 'docs']
FILES = ['coreshell.sh', 'VERSION', 'README.md', 'LICENSE']
BINS = ['bin/corepatch']
KEEP_BACKUPS = 3


def _say(*a):
    print(*a, flush=True)


def config(home=HOME):
    try:
        with open(os.path.join(home, 'settings', 'update.json')) as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    c.setdefault('repo', REPO)
    c.setdefault('mains_tag', MAINS_TAG)
    c.setdefault('token', os.environ.get('GITHUB_TOKEN') or None)
    return c


def version(home=HOME):
    try:
        with open(os.path.join(home, 'VERSION')) as f:
            return f.read().strip()
    except OSError:
        return '0'


def _newer(a, b):
    """Is release tag a newer than b? Tags: vYYYY.MM.DD[.n]"""
    def key(v):
        return [int(x) if x.isdigit() else 0 for x in v.lstrip('v').replace('-', '.').split('.')]
    return key(a) > key(b)


# ---------------------------------------------------------------- GitHub
def _get(url, cfg, accept=None, timeout=120):
    req = urllib.request.Request(url, headers={'User-Agent': 'coreshell-update'})
    if cfg.get('token') and ('api.github.com' in url):
        req.add_header('Authorization', 'Bearer ' + cfg['token'])
    if accept:
        req.add_header('Accept', accept)
    return urllib.request.urlopen(req, timeout=timeout).read()


def release(cfg, tag=None):
    url = 'https://api.github.com/repos/%s/releases/%s' % (cfg['repo'], ('tags/' + tag) if tag else 'latest')
    try:
        return json.loads(_get(url, cfg))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError('%s: no release%s (a private repository needs a token in settings/update.json)' % (
                cfg['repo'], (' ' + tag) if tag else ''))
        raise


def asset(cfg, rel, name):
    """An asset's bytes, or None. Through the API (works for private repositories, with the token)."""
    for a in rel.get('assets', []):
        if a['name'] == name:
            return _get(a['url'], cfg, accept='application/octet-stream', timeout=600)
    return None


# ---------------------------------------------------------------- install
def _backups(d):
    """The backups in d, oldest first: by the time in their name (<version>_<YYYYmmdd-HHMMSS>.tar.gz), not the
    name itself (v2026.11.18_ sorts after v2026.11.18.2_)."""
    def when(n):
        stamp = n[:-len('.tar.gz')].rsplit('_', 1)[-1] if n.endswith('.tar.gz') else ''
        return (stamp if re.match(r'^\d{8}-\d{6}$', stamp) else '', os.path.getmtime(os.path.join(d, n)), n)
    return sorted((n for n in os.listdir(d) if n.endswith('.tar.gz')), key=when) if os.path.isdir(d) else []


def _backup(home):
    d = os.path.join(home, 'backups', 'update')
    os.makedirs(d, exist_ok=True)
    name = os.path.join(d, '%s_%s.tar.gz' % (version(home), time.strftime('%Y%m%d-%H%M%S')))
    with tarfile.open(name, 'w:gz') as t:
        for p in CODE + FILES + BINS + ['systems', 'mains/mains.json']:
            full = os.path.join(home, p)
            if os.path.exists(full):
                t.add(full, p, filter=lambda i: None if '__pycache__' in i.name else i)
    olds = _backups(d)
    for o in olds[:-KEEP_BACKUPS]:
        os.unlink(os.path.join(d, o))
    return name


def _safe_members(t):
    for m in t.getmembers():
        n = os.path.normpath(m.name)
        if n.startswith(('/', '..')) or not (m.isfile() or m.isdir()):
            raise RuntimeError('the release archive has an unsafe entry: %s' % m.name)
    return t.getmembers()


def _keep_profile(home, new_prof, old_prof):
    """The installed core is the old profile's build and not the new one's: the old (re-dumped here) is kept."""
    try:
        from . import launcher as ln
        rbf = ln.installed_rbf(ln.Paths(os.path.dirname(home)), new_prof['rbf'])
        return bool(rbf) and ln.is_exact(old_prof, rbf) and not ln.is_exact(new_prof, rbf)
    except Exception:
        return False


def _keep_file(home, sd, n, dst):
    try:
        with open(os.path.join(sd, n)) as a, open(dst) as b:
            return _keep_profile(home, json.load(a), json.load(b))
    except (OSError, ValueError, KeyError):
        return False


def install(home, data, log=_say):
    """The release archive's files put in place. Returns the files written."""
    stage = os.path.join(home, '.update')
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as t:
        kw = {'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}   # (and Python's own check where it has one)
        t.extractall(stage, members=_safe_members(t), **kw)
    if not os.path.isfile(os.path.join(stage, 'coreshell', '__main__.py')):
        raise RuntimeError('not a Coreshell release (no coreshell/__main__.py)')
    # it must at least load before it replaces what runs
    r = subprocess.run([sys.executable, '-c', 'import coreshell.__main__, coreshell.server'], cwd=stage,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if r.returncode:
        raise RuntimeError('the release doesn\'t load here:\n' + r.stdout.decode(errors='replace')[-1500:])
    done = []
    for p in CODE:
        src = os.path.join(stage, p)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(home, p)
        old = dst + '.old'
        shutil.rmtree(old, ignore_errors=True)
        if os.path.exists(dst):
            os.rename(dst, old)
        os.rename(src, dst)
        shutil.rmtree(old, ignore_errors=True)
        done.append(p + '/')
    for p in FILES + BINS:
        src = os.path.join(stage, p)
        if os.path.isfile(src):
            dst = os.path.join(home, p)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + '.new'
            shutil.copy2(src, tmp)
            if p.startswith('bin/') or p.endswith('.sh'):
                os.chmod(tmp, 0o755)
            os.rename(tmp, dst)
            done.append(p)
    # profiles: merged (the release's, unless this MiSTer re-dumped a newer core)
    sd = os.path.join(stage, 'systems')
    kept = 0
    if os.path.isdir(sd):
        os.makedirs(os.path.join(home, 'systems'), exist_ok=True)
        for base, _, files in os.walk(sd):
            for f in files:
                n = os.path.relpath(os.path.join(base, f), sd)
                dst = os.path.join(home, 'systems', n)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if n.endswith('.json') and os.sep not in n and os.path.isfile(dst) and _keep_file(home, sd, n, dst):
                    kept += 1
                    continue
                shutil.copy2(os.path.join(sd, n), dst)
        done.append('systems/ (%d profiles kept: re-dumped here for a newer core)' % kept)
    # the Mains manifest: merged (entries this MiSTer has that the release doesn't stay)
    mj = os.path.join(stage, 'mains', 'mains.json')
    if os.path.isfile(mj):
        merge_mains(home, json.load(open(mj)))
        done.append('mains/mains.json')
    shutil.rmtree(stage, ignore_errors=True)
    return done


def _install_or_back(home, data, backup):
    """install(); if it fails part way, the install before it put back (from the backup just made)."""
    try:
        return install(home, data)
    except Exception as e:
        try:
            with open(backup, 'rb') as f:
                install(home, f.read(), log=lambda *a: None)
            raise RuntimeError('the release didn\'t install (%s): the install before it is back' % e)
        except RuntimeError:
            raise
        except Exception as e2:
            raise RuntimeError('the release didn\'t install (%s), nor did the backup go back (%s): '
                               'python3 -m coreshell update --rollback' % (e, e2))


def merge_mains(home, new):
    p = os.path.join(home, 'mains', 'mains.json')
    try:
        man = json.load(open(p))
    except (OSError, ValueError):
        man = {}
    man.update(new)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.new', 'w') as f:
        json.dump(man, f, indent=1, sort_keys=True)
    os.rename(p + '.new', p)
    return man


def fetch_mains(home, cfg, root=None, log=_say):
    """The shim build of each Main on this MiSTer that has one in the release "shim-mains" and isn't here yet.
    Returns the files fetched."""
    from . import mains as mn
    from . import devtools as dt
    root = root or os.path.dirname(home)
    try:
        rel = release(cfg, cfg['mains_tag'])
    except Exception as e:
        log('update: no shim builds to fetch (%s)' % e)
        return []
    data = asset(cfg, rel, 'mains.json')
    man = merge_mains(home, json.loads(data)) if data else mn.Mains(root, home).manifest()
    got = []
    for m in dt.mains_scan(root, home):
        ent = man.get(m.get('md5'))
        if m.get('shim') or not isinstance(ent, dict) or not ent.get('shim'):
            continue
        dst = os.path.join(home, 'mains', ent['shim'])
        if os.path.isfile(dst) and (not ent.get('shim_md5') or mn.md5(dst) == ent['shim_md5']):
            continue
        b = asset(cfg, rel, ent['shim'])
        if b is None:
            log('update: %s: %s isn\'t in the release' % (m['file'], ent['shim']))
            continue
        if ent.get('shim_md5') and hashlib.md5(b).hexdigest() != ent['shim_md5']:
            log('update: %s: the download doesn\'t match its md5: not used' % ent['shim'])
            continue
        if mn.SHIM_MARK not in b:
            log('update: %s has no shim in it: not used' % ent['shim'])
            continue
        with open(dst + '.new', 'wb') as f:
            f.write(b)
        os.chmod(dst + '.new', 0o755)
        os.rename(dst + '.new', dst)
        got.append(ent['shim'])
        log('update: %s: the shim build %s fetched' % (m['file'], ent['shim']))
    return got


def rollback(home, log=_say):
    d = os.path.join(home, 'backups', 'update')
    olds = _backups(d)
    if not olds:
        raise RuntimeError('no update to roll back')
    with open(os.path.join(d, olds[-1]), 'rb') as f:
        data = f.read()
    done = install(home, data, log)
    os.unlink(os.path.join(d, olds[-1]))
    return olds[-1], done


def restart(log=_say):
    """Coreshell started again from the new code (its supervisor starts it when it ends)."""
    me = os.getpid()
    for pid in os.listdir('/proc'):
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            with open('/proc/%s/cmdline' % pid, 'rb') as f:
                cmd = f.read().split(b'\0')
        except OSError:
            continue
        if cmd[:1] and cmd[0].endswith(b'python3') and b'serve' in cmd and b'coreshell' in cmd:
            os.kill(int(pid), 15)
            log('update: Coreshell started again')


def main(args, home=HOME):
    try:
        return _main(args, home)
    except (RuntimeError, OSError) as e:
        _say('update: %s' % e)
        return 1


def _main(args, home):
    cfg = config(home)
    if '--rollback' in args:
        name, done = rollback(home)
        _say('update: back to %s (%s)' % (name, ', '.join(done)))
        restart()
        return 0
    if '--mains' in args:
        got = fetch_mains(home, cfg)
        if got:
            from . import mains as mn
            mn.Mains(os.path.dirname(home), home, say=_say).check(force=True)
        return 0
    if '--file' in args:
        # a release archive copied here by hand (no network, or a build to try): installed as one from GitHub
        with open(args[args.index('--file') + 1], 'rb') as f:
            data = f.read()
        b = _backup(home)
        _say('update: the install now kept in %s' % b)
        done = _install_or_back(home, data, b)
        _say('update: %s installed (%s)' % (version(home), ', '.join(done)))
        from . import mains as mn
        mn.Mains(os.path.dirname(home), home, say=_say).check(force=True)
        restart()
        return 0
    rel = release(cfg)
    tag, here = rel['tag_name'], version(home)
    _say('update: this is %s; the latest release of %s is %s' % (here, cfg['repo'], tag))
    if '--check' in args:
        _say('update: %s' % ('newer: python3 -m coreshell update' if _newer(tag, here) else 'up to date'))
        return 0
    if not _newer(tag, here) and '--force' not in args:
        _say('update: up to date')
        fetch_mains(home, cfg)
        return 0
    data = asset(cfg, rel, 'coreshell.tar.gz')
    if data is None:
        raise RuntimeError('%s has no coreshell.tar.gz' % tag)
    want = asset(cfg, rel, 'coreshell.tar.gz.sha256')
    if want and want.split()[0].decode() != hashlib.sha256(data).hexdigest():
        raise RuntimeError('coreshell.tar.gz doesn\'t match its sha256: not installed')
    b = _backup(home)
    _say('update: the install now kept in %s' % b)
    done = _install_or_back(home, data, b)
    _say('update: %s installed (%s)' % (tag, ', '.join(done)))
    got = fetch_mains(home, cfg)
    from . import mains as mn
    mn.Mains(os.path.dirname(home), home, say=_say).check(force=True)
    restart()
    return 0
