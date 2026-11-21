#!/usr/bin/env python3
"""Coreshell - watch MiSTer and Update All for changes, and flag the breaking ones.

Run by the daily GitHub Action after tools/update_db.py. It looks at:

  * every Main Update All installs (MiSTer, MiSTer_RA, MiSTer_Physical-CD, the
    game ports' Mains...), menu.rbf and MiSTer Linux: new builds are noted;
  * Main_MiSTer's source at its latest commit: each thing Coreshell relies on
    (load_core, MGL <option> and <setname>, the .CFG layout, /tmp/CORENAME,
    the active-INI slot, main=) is checked to still be there, as it was;
  * the cores tools/update_db.py rebuilt this run (core_changes.json): options
    removed, values removed, bits moved, the config file renamed, the game
    slot moved;
  * changed cores the cloud can't read (needs_dump.json): consoles refresh
    those themselves (coreshell refresh).

Writes watch_state.json (what was seen, committed) and, when there is
something to tell, alert.md: the Action opens a GitHub issue with it, and
GitHub emails the repo owner. Breaking findings put [BREAKING] in the title.

    python3 tools/watch.py [--no-source]

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import update_db as ud      # noqa: E402  (the same databases, fetched the same way)

STATE = os.path.join(ROOT, 'watch_state.json')
ALERT = os.path.join(ROOT, 'alert.md')
CACHE = os.path.join(ROOT, '.cache')

# What Coreshell needs from Main_MiSTer, and where it lives: (file, regex, what breaks without it)
PROBES = [
    ('input.cpp', r'"load_core "',
     'launching: Coreshell starts every game with load_core on /dev/MiSTer_cmd'),
    ('support/arcade/mra_loader.cpp', r'strcasecmp\(node->tag, "option"\)',
     'settings by name: MGL <option> (used when a core changed since its profile)'),
    ('support/arcade/mra_loader.h', r'#define MGL_MAX_OPTIONS 16\b',
     'MGL options per launch: Coreshell sends at most 16 (launcher.MGL_MAX_OPTIONS)'),
    ('support/arcade/mra_loader.cpp', r'strcasecmp\(node->tag, "setname"\)',
     'RA and alt cores: <setname> picks the config name and main= section'),
    ('support/arcade/mra_loader.cpp', r'"same_dir"',
     'RA cores: <setname same_dir="1"> keeps saves and games in the core\'s own folders'),
    ('user_io.cpp', r'static char cur_status\[16\]',
     'settings: the 16-byte status block Coreshell writes into <core>.CFG'),
    ('user_io.cpp', r'strcat\(str, "\.CFG"\)',
     'settings: the <core>[_vN].CFG file name'),
    ('user_io.cpp', r'"_v%d"',
     'settings: the config version suffix (v,N in CONF_STR)'),
    ('user_io.cpp', r'MakeFile\("/tmp/CORENAME"',
     'refresh and Super MemCard: /tmp/CORENAME tells which core runs'),
    ('fpga_io.cpp', r'shmem_map\(0x1FFFF000, 0x1000\)',
     'display sets: the shared page where Main keeps the active INI'),
    ('cfg.cpp', r'\{ "MAIN", \(void\*\)\(&\(cfg\.main\)\)',
     'main=: which Main a core runs under (RA, disc, game ports)'),
]

# Mains Update All installs: a file at the SD root named MiSTer or MiSTer_<something>, no extension
_MAIN = re.compile(r'^MiSTer(_[A-Za-z0-9_.-]+)?$')


def read_state():
    try:
        with open(STATE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def system_files():
    """{name: {md5, db}} for Mains and menu.rbf, plus MiSTer Linux's version."""
    files, linux = {}, None
    for url in ud.SOURCES:
        try:
            d = ud.fetch_json(url)
        except Exception as e:
            print('db skipped: %s (%s)' % (url, e))
            continue
        entries = list(d.get('files', {}).items())
        for a in d.get('archives', {}).values():
            entries += list(a.get('summary_inline', {}).get('files', {}).items())
        for path, f in entries:
            name = os.path.basename(path)
            if '/' not in path.strip('|') and (_MAIN.match(name) or name == 'menu.rbf') \
                    and 'hash' in f and not name.endswith('.ini'):
                files[name] = {'md5': f['hash'], 'db': d.get('db_id', url)}
        if d.get('linux') and d.get('db_id') == 'distribution_mister':
            linux = {'version': d['linux'].get('version'), 'md5': d['linux'].get('hash')}
    return files, linux


def main_source():
    """(commit, date, [failed probes]) for Main_MiSTer's latest commit."""
    d = os.path.join(CACHE, 'MiSTer-devel__Main_MiSTer')
    if os.path.isdir(d):
        subprocess.call(['git', '-C', d, 'fetch', '-q', '--depth', '1', 'origin'])
        subprocess.call(['git', '-C', d, 'reset', '-q', '--hard', 'origin/HEAD'])
    else:
        os.makedirs(CACHE, exist_ok=True)
        subprocess.check_call(['git', 'clone', '-q', '--depth', '1',
                               'https://github.com/MiSTer-devel/Main_MiSTer.git', d])
    commit = subprocess.check_output(['git', '-C', d, 'log', '-1', '--format=%H %cs']).decode().split()
    failed = []
    for f, rx, why in PROBES:
        try:
            with open(os.path.join(d, f), encoding='utf-8', errors='replace') as fh:
                ok = re.search(rx, fh.read())
        except OSError:
            ok = None
        if not ok:
            failed.append({'file': f, 'looked_for': rx, 'breaks': why})
    return commit[0], commit[1], failed


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def main(argv):
    old = read_state()
    first = not old
    files, linux = system_files()
    breaking, info = [], []

    # Mains, menu.rbf and Linux
    seen = old.get('files', {})
    for name, f in sorted(files.items()):
        if name not in seen:
            if not first:
                info.append('New Main in Update All: **%s** (%s)' % (name, f['db']))
        elif seen[name]['md5'] != f['md5']:
            info.append('New build of **%s** (%s)' % (name, f['db']))
    for name in sorted(set(seen) - set(files)):
        info.append('**%s** is no longer in Update All' % name)
    if linux and old.get('linux') and old['linux'].get('version') != linux['version']:
        info.append('New MiSTer Linux: **%s** (was %s). Check on the SS1: the Coreshell service '
                    'starts, `python3 -m coreshell ini-check`, one launch per Main.'
                    % (linux['version'], old['linux'].get('version')))

    # Main_MiSTer's source: is everything Coreshell uses still there?
    src = old.get('main_source', {})
    if '--no-source' not in argv:
        commit, date, failed = main_source()
        if commit != src.get('commit') and not first:
            info.append('Main_MiSTer source moved to %s (%s)' % (commit[:10], date))
        was = {p['looked_for'] for p in src.get('failed', [])}
        for p in failed:
            if p['looked_for'] not in was:
                breaking.append('Main_MiSTer `%s` no longer has `%s`: **%s**'
                                % (p['file'], p['looked_for'], p['breaks']))
        src = {'commit': commit, 'date': date, 'failed': failed}

    # cores rebuilt this run
    for c in load_json(os.path.join(ROOT, 'core_changes.json'), []):
        bad = [d for d in c['diff'] if d.get('breaking')]
        moved = [d['what'] for d in c['diff'] if d['what'] in ('game slot moved', 'config file renamed')]
        if moved:
            info.append('Core %s (%s): %s, handled by its rebuilt profile'
                        % (c['profile'], c['rbf'], ', '.join(moved)))
        if c.get('new'):
            info.append('New core profile: %s (%s)' % (c['profile'], c['rbf']))
        elif bad:
            lines = []
            for d in bad:
                lines.append('  - %s%s%s' % (
                    d['what'], (': `%s`' % d['option']) if d.get('option') else '',
                    (' %s -> %s' % (d.get('from'), d.get('to'))) if 'to' in d else
                    (' %s' % d['values'] if d.get('values') else '')))
            breaking.append('Core **%s** (%s): settings saved for these are dropped (reset to '
                            'the default)\n%s' % (c['profile'], c['rbf'], '\n'.join(lines)))

    # changed cores only a console can read
    needs = [n for n in load_json(os.path.join(ROOT, 'needs_dump.json'), []) if n.get('has_profile')]
    old_needs = set(old.get('needs_dump', []))
    fresh = [n for n in needs if n['rbf'] + n['md5'] not in old_needs]
    if fresh and not first:
        info.append('%d changed core(s) the cloud can\'t read; consoles refresh them themselves '
                    '(`coreshell refresh`): %s' % (len(fresh), ', '.join(n['rbf'] for n in fresh[:20])
                                                  + (' ...' if len(fresh) > 20 else '')))

    state = {'files': files,          # no timestamp: a quiet day changes nothing, commits nothing
             'linux': linux or old.get('linux'), 'main_source': src,
             'needs_dump': sorted(n['rbf'] + n['md5'] for n in needs)}
    with open(STATE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=1, sort_keys=True)
        f.write('\n')

    if os.path.exists(ALERT):
        os.unlink(ALERT)
    if breaking or info:
        title = '%sMiSTer / Update All changes %s' % ('[BREAKING] ' if breaking else '',
                                                      time.strftime('%Y-%m-%d', time.gmtime()))
        body = ['# ' + title, '']
        if breaking:
            body += ['## Breaking for Coreshell', ''] + ['- ' + b for b in breaking] + ['']
        if info:
            body += ['## Changes', ''] + ['- ' + i for i in info] + ['']
        body += ['Games keep launching either way: a changed core runs by option name, or with '
                 'its own settings, with a warning. Consoles rebuild changed profiles at their '
                 'next start.']
        with open(ALERT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(body) + '\n')
        print('\n'.join(body))
    else:
        print('nothing new')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
