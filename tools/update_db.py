#!/usr/bin/env python3
"""Coreshell - keep systems/ in step with what Update All installs.

Reads the Update All databases (every core entry carries its md5), compares
each core with the profile Coreshell has for it, and for every new or changed
core:

  * open source: rebuilds the profile from CONF_STR at the commit that built
    that exact file (md5 checked against the release in the core's repo);
  * anything it can't trace to source: lists it in needs_dump.json, to be
    dumped once on the SS1 (tools/dump_confstr.py) and imported with
    tools/profiles_from_dumps.py.

Arcade cores are left out (MRAs already run them). Run by the scheduled
GitHub Action, or by hand:

    python3 tools/update_db.py [--dry-run] [--only <path substring>]

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from coreshell import vconst as vc     # noqa: E402
from coreshell import profile as pf   # noqa: E402
import profiles_from_dumps as pd      # noqa: E402

SYSTEMS = os.path.join(ROOT, 'systems')
CACHE = os.path.join(ROOT, '.cache')
NEEDS_DUMP = os.path.join(ROOT, 'needs_dump.json')
CHANGES = os.path.join(ROOT, 'core_changes.json')   # this run's changes, for tools/watch.py

# the core databases Update All can install from (its src/update_all/databases.py)
SOURCES = [
    'https://raw.githubusercontent.com/MiSTer-devel/Distribution_MiSTer/main/db.json.zip',
    'https://raw.githubusercontent.com/theypsilon/RetroAchievementsDB_MiSTer/db/db.json.zip',
    'https://raw.githubusercontent.com/theypsilon/Distribution_Unofficial_MiSTer/main/unofficialdb.json.zip',
    'https://raw.githubusercontent.com/MiSTer-LLAPI/LLAPI_folder_MiSTer/main/llapidb.json.zip',
    'https://raw.githubusercontent.com/MikeS11/YC_Builds-MiSTer/db/db.json.zip',
    'https://raw.githubusercontent.com/ajgowans/alt-cores/db/db.json.zip',
    'https://raw.githubusercontent.com/TheJesusFish/Dual-Ram-Console-Cores/db/db.json.zip',
    'https://raw.githubusercontent.com/agg23/mister-repository/db/manifest.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/physical-disc/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/3s-arm/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/mister-quake/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/duke3d/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/sonic-mania/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/solarus/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/maldita-castilla/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/paprium/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/mms2-gb/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/megavgmdrive/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/dreamster/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/mister-dvd/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/nblood/db.json',
    'https://raw.githubusercontent.com/theypsilon/MultiDatabases_MiSTer/db/diablo/db.json.zip',
    'https://raw.githubusercontent.com/Coin-OpCollection/Distribution-MiSTerFPGA/db/db.json.zip',
]

_RAW = re.compile(r'https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([0-9a-f]{40})/(.+)$')


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        raw = r.read()
    if url.endswith('.zip'):
        z = zipfile.ZipFile(io.BytesIO(raw))
        raw = z.read(z.namelist()[0])
    return json.loads(raw)


def is_arcade(path):
    return 'arcade' in path.split('/', 1)[0].lower()


def installed_cores():
    """{path: {md5, db, url}} for every non-arcade .rbf Update All knows."""
    out = {}
    for url in SOURCES:
        try:
            d = fetch_json(url)
        except Exception as e:  # one dead db must not stop the run
            print('db skipped: %s (%s)' % (url, e))
            continue
        base = d.get('base_files_url', '')
        entries = list(d.get('files', {}).items())
        for a in d.get('archives', {}).values():
            entries += list(a.get('summary_inline', {}).get('files', {}).items())
        for path, f in entries:
            if not path.endswith('.rbf') or is_arcade(path) or 'hash' not in f:
                continue
            out[path] = {'md5': f['hash'], 'db': d.get('db_id', url),
                         'url': f.get('url') or (base + path if base else None)}
    return out


def current_profiles():
    """{installed .rbf path: (profile file, profile)}."""
    out = {}
    for n in sorted(os.listdir(SYSTEMS)):
        if not n.endswith('.json'):
            continue
        p = pf.load(os.path.join(SYSTEMS, n))
        rbf = p['source'].get('rbf')
        if rbf:
            out[os.path.join(os.path.dirname(p['rbf']), rbf)] = (n, p)
    return out


os.environ['GIT_TERMINAL_PROMPT'] = '0'      # a missing repo must fail, not ask for a login


def git(d, *args, binary=False):
    out = subprocess.check_output(('git', '-C', d) + args)
    return out if binary else out.decode('utf-8', 'replace')


def clone(owner, repo):
    d = os.path.join(CACHE, owner + '__' + repo)
    if os.path.isdir(d):
        subprocess.call(['git', '-C', d, 'fetch', '-q', 'origin'])
    else:
        os.makedirs(CACHE, exist_ok=True)
        if subprocess.call(['git', 'clone', '-q', '--filter=blob:none', '--no-checkout',
                            'https://github.com/%s/%s.git' % (owner, repo), d],
                           stderr=subprocess.DEVNULL) != 0:
            return None
    return d


def confstr_at(d, commit):
    tree = git(d, 'ls-tree', '-r', '--name-only', commit).split('\n')
    files = [f for f in tree if re.search(r'\.s?v$', f) and '/' not in f]
    for f in files:
        src = git(d, 'show', '%s:%s' % (commit, f))
        if re.search(r'(?:localparam|parameter)\s+CONF_STR\s*=', src):
            # worked out as Quartus would: the build's macros (the .qsf and the Tcl it sources),
            # `ifdef, `define, localparams and ? : (coreshell/vconst.py)
            def read(path):
                try:
                    return git(d, 'show', '%s:%s' % (commit, path))
                except subprocess.CalledProcessError:
                    return None
            try:
                qsf = [q for q in tree if q.endswith('.qsf') and '/' not in q]
                macros = vc.build_macros(read, qsf[0]) if len(qsf) == 1 else {}
                return vc.conf_str(src, macros), f
            except vc.Unreadable as e:
                print('  %s: not readable from source (%s): a dump on the SS1 instead' % (f, e))
                return None, None
    return None, None


def trace_source(path, entry):
    """(repo, commit, CONF_STR) for this exact build, or None."""
    name = os.path.basename(path)
    candidates = []
    m = _RAW.match(entry.get('url') or '')
    if m and (m.group(1), m.group(2)) != ('MiSTer-devel', 'Distribution_MiSTer'):
        candidates.append((m.group(1), m.group(2)))     # db points straight at the core's repo
    base = re.sub(r'(_\d{8})?\.rbf$', '', name)
    candidates.append(('MiSTer-devel', base + '_MiSTer'))
    for owner, repo in candidates:
        d = clone(owner, repo)
        if not d:
            continue
        try:
            rels = [f for f in git(d, 'ls-tree', '-r', '--name-only', 'origin/HEAD').split('\n')
                    if os.path.basename(f) == name]
        except subprocess.CalledProcessError:
            continue
        for rel in rels:
            commit = git(d, 'log', '-1', '--format=%H', 'origin/HEAD', '--', rel).strip()
            blob = git(d, 'show', '%s:%s' % (commit, rel), binary=True)
            if hashlib.md5(blob).hexdigest() != entry['md5']:
                continue                       # same name, different build
            text, src_file = confstr_at(d, commit)
            if text:
                return {'repo': '%s/%s' % (owner, repo), 'commit': commit,
                        'file': src_file}, text
    return None


def rebuild(path, entry, old):
    found = trace_source(path, entry)
    if not found:
        return None
    src, text = found
    if old:
        p = old
        prof = pf.build(text, p['system'], p['name'], p['rbf'],
                        games_dir=p['games_dir'], saves_dir=p['saves_dir'],
                        setname=p.get('setname'), variant=p.get('variant', 'stock'),
                        source=dict(src, rbf=os.path.basename(path), rbf_md5=entry['md5']))
        if p.get('rom'):
            prof['rom'] = p['rom']
        prof['hide'] = p.get('hide', [])
        if p.get('show'):
            prof['show'] = p['show']
    else:
        fname, prof = pd.from_dump({'rbf': path, 'rbf_md5': entry['md5'], 'confstr': text}, {})
        prof['source'] = dict(src, rbf=os.path.basename(path), rbf_md5=entry['md5'])
    return prof


def main(argv):
    dry = '--dry-run' in argv
    only = argv[argv.index('--only') + 1] if '--only' in argv else None
    cores = installed_cores()
    profs = current_profiles()
    by_core = {re.sub(r'(_\d{8})?\.rbf$', '', k): v for k, v in profs.items()}
    updated, needs, changes = [], [], []
    for path, entry in sorted(cores.items()):
        if only and only not in path:
            continue
        cur = profs.get(path) or by_core.get(re.sub(r'(_\d{8})?\.rbf$', '', path))
        if cur and cur[1]['source'].get('rbf_md5') == entry['md5']:
            continue                                   # up to date
        prof = rebuild(path, entry, cur and cur[1])
        if not prof:
            needs.append({'rbf': path, 'md5': entry['md5'], 'db': entry['db'],
                          'has_profile': bool(cur)})
            continue
        fname = cur[0] if cur else (prof['system'] + ('' if prof.get('variant', 'stock') == 'stock'
                                                       else '@' + prof['variant']) + '.json')
        updated.append((fname, path, len(prof['menu'])))
        changes.append({'profile': fname, 'rbf': path, 'new': not cur,
                        'diff': pf.diff(cur[1], prof) if cur else []})
        if not dry:
            pf.save(prof, os.path.join(SYSTEMS, fname))
    if not dry:
        with open(NEEDS_DUMP, 'w', encoding='utf-8') as f:
            json.dump(needs, f, indent=1)
            f.write('\n')
        with open(CHANGES, 'w', encoding='utf-8') as f:
            json.dump(changes, f, indent=1)
            f.write('\n')
    print('cores in Update All (non-arcade): %d, profiles: %d' % (len(cores), len(profs)))
    print('rebuilt from source: %d' % len(updated))
    for fname, path, n in updated:
        print('  %-30s %-45s %d options' % (fname, path, n))
    print('need a dump on the SS1: %d (needs_dump.json)' % len(needs))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
