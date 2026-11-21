#!/usr/bin/env python3
"""build_patch: a Coreshell core patch for one stock core, start to finish (run on a PC, not the MiSTer).

    build_patch.py STOCK.rbf --design DESIGN.v --name memcard.vN [--out DIR]

1. coremap      what the stock core leaves free: the HPS UART's RXD node, a 50 MHz global, the free LABs it reaches
2. yosys        the design (Super MemCard's: the only patches Coreshell applies)
3. nextpnr      the design placed and routed into the stock core (NPNR_BASE_RBF; nextpnr-base.patch)
4. coreverify   the stock core untouched: every link and setting kept but the nodes taken over on purpose
5. mkpatch      the difference, as DIR/<core>.<name>.cspatch (only for that exact stock file)

Tools: NEXTPNR (nextpnr-mistral built with nextpnr-base.patch), COREMAP and COREVERIFY (tools/, built against
libmistral), yosys on PATH. Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NEXTPNR = os.environ.get('NEXTPNR', 'nextpnr-mistral')
COREMAP = os.environ.get('COREMAP', 'coremap')
COREVERIFY = os.environ.get('COREVERIFY', 'coreverify')
ROUTER = os.environ.get('ROUTER', '').split()
BASELINKS = os.environ.get('BASELINKS', 'baselinks')

# the design's pins: what it reads (taps) and what it drives (sinks, open-drain outputs)
TAPS = ['AG16']                                            # front port: DAT
PIN_SINKS = {'ATT': 'AH9', 'CMD': 'AH12', 'PCLK': 'AF15'}  # front port 1: ATT, CMD, CLK


def run(cmd, env=None, log=None):
    r = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if log:
        open(log, 'w').write(r.stdout)
    if r.returncode:
        sys.exit('%s failed:\n%s' % (cmd[0], r.stdout[-3000:]))
    return r.stdout


def cluster(tiles, want=40):
    """the densest group of free blocks: the design's logic stays together (only its pins' few wires cross the
    chip, and those meet the outside world, not our own flip-flops)"""
    pts = [tuple(map(int, t.split(','))) for t in tiles.split(';') if t]
    if len(pts) <= want:
        return tiles
    d = lambda a, b: abs(a[0] - b[0]) + abs(a[1] - b[1])
    best = min(pts, key=lambda c: sum(sorted(d(c, p) for p in pts)[:want]))
    pick = sorted(pts, key=lambda p: d(best, p))[:want]
    return ';'.join('%d,%d' % p for p in pick)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('stock')
    ap.add_argument('--design', required=True)
    ap.add_argument('--name', required=True, help='memcard.vN: Super MemCard patches only')
    ap.add_argument('--out', default='.')
    a = ap.parse_args()
    core = os.path.splitext(os.path.basename(a.stock))[0]
    work = os.path.join(a.out, 'work', core)
    os.makedirs(work, exist_ok=True)

    m = dict((l + ' ').split(' ', 1) for l in run([COREMAP, a.stock]).splitlines()
             if l.split(' ')[0] in ('rxd', 'rxdfree', 'clk', 'tiles'))
    m = {k: v.strip() for k, v in m.items()}
    if m.get('rxd', '-') == '-' or m.get('clk', '-') == '-' or not m.get('tiles', '').strip():
        sys.exit('%s: nothing to build on (rxd %s, clk %s, %d free blocks)' %
                 (core, m.get('rxd'), m.get('clk'), len(m.get('tiles', '').split(';'))))
    print('%s: rxd %s, clock %s, %d free blocks' % (core, m['rxd'], m['clk'], len(m['tiles'].split(';'))))

    # links the pinned Mistral doesn't decode (clock-mux leaves): off-limits too
    links = os.path.join(work, 'stock.links')
    run([BASELINKS, a.stock, links])
    old = set()
    run([BASELINKS + '_pinned', a.stock, links + '.pinned'])
    old = set(open(links + '.pinned').read().splitlines())
    extra = sorted({l.split()[1] for l in open(links).read().splitlines() if l not in old} |
                   {l.split()[0] for l in open(links).read().splitlines() if l not in old})
    blockf = os.path.join(work, 'block.txt')
    open(blockf, 'w').write('\n'.join(extra) + '\n')

    js = os.path.join(work, 'design.json')
    run(['yosys', '-q', '-p', 'read_verilog %s; synth_intel_alm -family cyclonev -noclkbuf -top top; write_json %s'
         % (a.design, js)])

    env = dict(os.environ,
               NPNR_BASE_RBF=a.stock, NPNR_SHARE=m['clk'],
               NPNR_TAPS=','.join(['CLK50=' + m['clk']] + TAPS), NPNR_FREE=m.get('rxdfree', ''), NPNR_BLOCK_FILE=blockf,
               NPNR_SINKS=','.join(['RXD=' + m['rxd']] + ['%s=PIN:%s' % kv for kv in PIN_SINKS.items()]))
    patched = os.path.join(work, core + '.patched.rbf')
    pool = [t for t in m['tiles'].split(';') if t]
    for attempt in range(7):
        env['NPNR_ONLY_TILES'] = cluster(';'.join(pool))
        r = subprocess.run([NEXTPNR, '--device', '5CSEBA6U23I7', '--freq', '75'] + ROUTER + ['--json', js, '--rbf',
                            patched], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        log = r.stdout
        open(os.path.join(work, 'nextpnr.log'), 'w').write(log)
        if r.returncode == 0:
            break
        # a block the stock core's routing boxes in: leave it out and place again
        bad = re.findall(r'Failed to route arc .* to (?:GOUT|WIRE)\.(\d+)\.(\d+)\.', log)
        if not bad or attempt == 6:
            sys.exit('nextpnr failed:\n' + log[-2000:])
        drop = '%s,%s' % bad[-1]
        print('  routing into block %s failed, leaving it out' % drop)
        pool = [t for t in pool if t != drop]
    taken = re.findall(r'Info: sink \w+: (\S+)', log) + [x for x in m.get('rxdfree', '').split(',') if x]
    fmax = re.findall(r'Max frequency for clock .clk.: ([\d.]+) MHz', log)
    print('  placed and routed, %s MHz (needs 50)' % (fmax[-1] if fmax else '?'))
    if not fmax or float(fmax[-1]) < 60:
        sys.exit('  timing too tight, not making a patch')

    v = run([COREVERIFY, a.stock, patched] + taken, log=os.path.join(work, 'coreverify.log'))
    print('  ' + v.strip().splitlines()[-2])
    # and nothing added touches what only the current Mistral sees
    run([BASELINKS + '_pinned', patched, links + '.patched'])
    added = set(open(links + '.patched').read().splitlines()) - old
    hit = {n for l in added for n in l.split()} & set(extra)
    if hit:
        sys.exit('  the patch touches stock clock muxes: %s' % sorted(hit)[:5])

    if not a.name.startswith('memcard.'):
        ap.error('only Super MemCard patches (--name memcard.vN): no core is patched for anything else')
    out = os.path.join(a.out, core + '.' + a.name + '.cspatch')
    p = run([sys.executable, os.path.join(HERE, 'mkpatch.py'), a.stock, patched, out,
             '--name', '%s front-port pad' % core])
    print('  ' + p.strip())


if __name__ == '__main__':
    main()
