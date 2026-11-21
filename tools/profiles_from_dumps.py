#!/usr/bin/env python3
"""Coreshell - build profiles from live CONF_STR dumps (tools/dump_confstr.py).

    python3 tools/profiles_from_dumps.py <dumps dir> [--ra-mgl <dir with RA .mgl files>]

Writes systems/<system>.json for stock cores and systems/<system>@<variant>.json
for the others (ra, dualram, other...). A hand-tuned overlay in
systems/overrides/<file name>.json is applied on top. Profiles made from a
dump carry the core file's md5, so Coreshell uses exact mode only on that
exact build.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from coreshell import profile as pf  # noqa: E402

SYSTEMS = os.path.join(ROOT, 'systems')

VARIANT_BY_FOLDER = {
    '_Console': 'stock',
    '_Computer': 'stock',
    '_Utility': 'stock',
    '_Other': 'other',
    '_RA_Cores': 'ra',
    '_Console (Dual SDRAM)': 'dualram',
    '_Physical Disc Cores': 'physicalcd',
}

# disc systems load their game through a mount (S) slot, not a file (F) slot
DISC_SLOT = {'PSX': ('s', 1)}


def read_setnames(mgl_dir):
    """RA MGLs: <rbf>_RA_Cores/Cores/SNES</rbf> <setname>RA_SNES</setname>."""
    out = {}
    for f in glob.glob(os.path.join(mgl_dir or '', '*.mgl')):
        t = open(f, encoding='utf-8', errors='replace').read()
        r = re.search(r'<rbf>\s*([^<]+?)\s*</rbf>', t)
        s = re.search(r'<setname[^>]*>\s*([^<]+?)\s*</setname>', t)
        if r and s:
            out[r.group(1)] = s.group(1)
    return out


def from_dump(d, setnames):
    rel = d['rbf']                                   # _Console/NES_20260823.rbf
    folder = rel.split('/', 1)[0]
    if rel.startswith('_RA_Cores/'):
        folder = '_RA_Cores'
    variant = VARIANT_BY_FOLDER.get(folder, 'other')
    rbf = re.sub(r'(_\d{8})?\.rbf$', '', rel)        # _Console/NES
    core = d['confstr'].split(';', 1)[0]
    system = re.sub(r'[^a-z0-9]+', '', core.lower()) or os.path.basename(rbf).lower()
    if variant == 'other':
        system = re.sub(r'[^a-z0-9]+', '', os.path.basename(rbf).lower())
    setname = setnames.get(rbf)
    if not setname and variant == 'ra':
        setname = 'RA_' + os.path.basename(rbf)      # Update All's RA MGLs all do this
    prof = pf.build(d['confstr'], system, core, rbf,
                    rom_slot=DISC_SLOT.get(core), setname=setname, variant=variant,
                    source={'from': 'dump', 'rbf': os.path.basename(rel),
                            'rbf_md5': d['rbf_md5'], 'main': d.get('main'),
                            'dumped': d.get('dumped')})
    fname = system + ('' if variant == 'stock' else '@' + variant)
    ov = os.path.join(SYSTEMS, 'overrides', fname + '.json')
    if os.path.exists(ov):
        with open(ov, encoding='utf-8') as f:
            pf.apply_overrides(prof, json.load(f))
    return fname, prof


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    dumps = argv[0]
    mgl_dir = argv[argv.index('--ra-mgl') + 1] if '--ra-mgl' in argv else None
    setnames = read_setnames(mgl_dir)
    seen = {}
    n = 0
    for f in sorted(glob.glob(os.path.join(dumps, '*.json'))):
        with open(f, encoding='utf-8') as fh:
            d = json.load(fh)
        try:
            fname, prof = from_dump(d, setnames)
        except Exception as e:
            print('SKIP %-45s %s' % (d.get('rbf'), e))
            continue
        if fname in seen:                     # two builds of one system: keep both
            fname = fname + '@' + re.sub(r'[^a-z0-9]+', '', os.path.basename(prof['rbf']).lower())
        seen[fname] = 1
        pf.save(prof, os.path.join(SYSTEMS, fname + '.json'))
        n += 1
        print('%-28s %-40s %3d options  rom %s' % (
            fname, d['rbf'], len(prof['menu']),
            prof['rom'] and '%s%d %s' % (prof['rom']['type'].upper(), prof['rom']['index'],
                                         ','.join(prof['rom']['exts']))))
    print('%d profiles' % n)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
