#!/usr/bin/env python3
"""psx_controllers.py: coreshell/data/psx_controllers.tsv, the controllers each PS1 game takes, by serial (all
regions), for picking the PSX core's Pad type per game (docs: test_bench docs/psx-auto-controller.md).

    tools/psx_controllers.py LIBRETRO_DATABASE [OUT]

LIBRETRO_DATABASE: a checkout of libretro-database (github.com/libretro/libretro-database; only
metadat/analog/Sony - PlayStation.dat and metadat/redump/Sony - PlayStation.dat are read). Sources:
- analog: libretro-database's analog list (the games that use the analog sticks), by serial;
- negcon, guncon, justifier: tools/psx_controllers_titles.txt (titles from the published lists named there),
  turned into serials with the redump list.
Every region and disc of a title (the redump list's name, notes in brackets left out) gets the same.

A line: SERIAL<TAB>types, best first<TAB>title. Types: dualshock (analog sticks), negcon, guncon, justifier. A game
in both the analog and the NeGcon lists gets dualshock first (its own analog pad). A game not in the file: the core's
default, Dualshock (a game that doesn't ask for analog sees a digital pad). Titles the redump list doesn't have are
printed: fix them in the titles file.

The output is made from libretro-database's data: CC BY-SA 4.0 (THIRD_PARTY.md). This tool: Copyright (c) 2026
f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TITLES = os.path.join(HERE, 'psx_controllers_titles.txt')
OUT = os.path.join(HERE, '..', 'coreshell', 'data', 'psx_controllers.tsv')
ORDER = ['dualshock', 'negcon', 'guncon', 'justifier']
ROMAN = {'ii': '2', 'iii': '3', 'iv': '4'}


def norm(title):
    """A title for matching: no region/disc notes, no "The", no punctuation, II -> 2."""
    t = re.sub(r'\([^)]*\)', ' ', title).lower()
    t = re.sub(r',\s*the\b', ' ', t)
    t = re.sub(r'^the\s+', '', t)
    t = t.replace('&', ' and ').replace("'", '')
    t = re.sub(r'[^a-z0-9]+', ' ', t)
    return ' '.join(ROMAN.get(w, w) for w in t.split())


def dat_games(path):
    """(name or comment, [serials], analog) for each game ( ... ) of a clrmamepro dat."""
    text = open(path, encoding='utf-8', errors='replace').read()
    for block in re.findall(r'^game \((.*?)^\)', text, re.S | re.M):
        name = re.search(r'^\s*(?:name|comment) "([^"]*)"', block, re.M)
        # a disc's own code, as gameid.py reads it (SLUS-01041): the lists' longer forms cut to it (SCES-02845-T,
        # SLUS-00654GH), anything else (GER) left out
        serials = [m.group(1) for m in (re.match(r'^([A-Z]{4}-\d{5})(?!\d)', s.strip())
                                        for s in re.findall(r'serial "([^"]+)"', block)) if m]
        yield (name.group(1) if name else ''), sorted(set(serials)), 'analog 1' in block


def read_titles(path=TITLES):
    """{type: [[names of one game], ...]}"""
    out, cur = {}, None
    for line in open(path, encoding='utf-8'):
        line = line.split(' #')[0].strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r'\[(\w+)\]$', line)
        if m:
            cur = out.setdefault(m.group(1), [])
            continue
        cur.append([n.strip() for n in line.split(' / ') if n.strip()])
    return out


def build(db, titles=TITLES):
    """{serial: (types, title)}, [titles not found]"""
    redump = os.path.join(db, 'metadat', 'redump', 'Sony - PlayStation.dat')
    analog = os.path.join(db, 'metadat', 'analog', 'Sony - PlayStation.dat')
    by_title, names = {}, {}
    for name, serials, _ in dat_games(redump):
        for s in serials:
            by_title.setdefault(norm(name), set()).add(s)
            names.setdefault(s, name)
    games = {}

    def add(serial, kind, title):
        t = games.setdefault(serial, [set(), title])
        t[0].add(kind)
    for name, serials, is_analog in dat_games(analog):
        if is_analog:
            for s in serials:
                add(s, 'dualshock', names.get(s, name))
    missing = []
    for kind, lst in read_titles(titles).items():
        for alts in lst:
            found = set()
            for a in alts:
                found |= by_title.get(norm(a), set())
            if not found:
                missing.append('%s: %s' % (kind, ' / '.join(alts)))
            for s in sorted(found):
                add(s, kind, names.get(s, alts[0]))
    # every region and disc of a title gets what any of them has (the lists name one release; the analog list
    # often a single region)
    by_norm = {}
    for s, t in games.items():
        by_norm.setdefault(norm(names.get(s, t[1])), set()).update(t[0])
    for n, kinds in by_norm.items():
        for s in by_title.get(n, ()):
            games.setdefault(s, [set(), names.get(s, n)])[0].update(kinds)
    return {s: ([k for k in ORDER if k in t[0]], t[1]) for s, t in games.items()}, missing


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    games, missing = build(argv[1])
    out = argv[2] if len(argv) > 2 else OUT
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write('# The controllers each PS1 game takes, best first (tools/psx_controllers.py). Made from\n'
                '# libretro-database (github.com/libretro/libretro-database): CC BY-SA 4.0.\n')
        for s in sorted(games):
            f.write('%s\t%s\t%s\n' % (s, ' '.join(games[s][0]), games[s][1]))
    print('%d games -> %s' % (len(games), out))
    for m in missing:
        print('not in the redump list: ' + m)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
