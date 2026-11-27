#!/usr/bin/env python3
"""check.py: the PSX "any pad" patch's logic, checked from the bitstream (every input of the changed and added
LUTs): the front ports' SNAC decodes and snacMCEN's next state, against what they should be.

    MISTERY=<Mistery>/rbf2src/netlist mistral-cv decomp 5CSEBA6U23I7 PATCHED.rbf p.bt && check.py p.bt
    check.py stock.bt stock      (the same checker on the stock core: it must give back the stock behaviour)

The ALM model is Mistery's (alm.py, meathax, BSD 3-Clause); LUT inputs are read inverted, as Mistery's RTL
(checked against the stock core's decompiled logic). Copyright (c) 2026 f3bandit. Licensed under the Apache
License 2.0 (LICENSE, NOTICE).
"""
import os
import sys
if not os.environ.get('MISTERY'):
    sys.exit('MISTERY=<Mistery>/rbf2src/netlist (its alm.py) is needed')
sys.path.insert(0, os.environ['MISTERY'])
import itertools
from alm import comb
PINS=['A','B','C','D','E0','F0','E1','F1','TF','BF']
def out(m,tm,bm,half,p):
    return comb(m,tm,bm,half,{k:1-v for k,v in p.items()})   # LUT pins read inverted (verified vs Mistery's RTL)
def remask(m,tm,bm,half,fn,used):
    """new mask: out == fn(p) for every value of the pins in `used`; bits not addressed by this half unchanged"""
    new=m; fixed={}
    for v in itertools.product([0,1],repeat=len(used)):
        p=dict.fromkeys(PINS,0); p.update(zip(used,v))
        want=fn(p); base=out(new,tm,bm,half,p)
        ks=[k for k in range(64) if out(new^(1<<k),tm,bm,half,p)!=base]
        assert len(ks)==1,(v,ks)
        k=ks[0]
        if k in fixed: assert fixed[k]==want,'conflict'
        fixed[k]=want
        if base!=want: new^=1<<k
    return new
def check(m,tm,bm,half,fn,used):
    for v in itertools.product([0,1],repeat=len(used)):
        p=dict.fromkeys(PINS,0); p.update(zip(used,v))
        assert out(m,tm,bm,half,p)==fn(p)
    return True
def hx(m): return '%08x.%08x'%(m>>32,m&0xffffffff)

import re
bt=sys.argv[1]; STOCK=len(sys.argv)>2
src={}; sett={}
for l in open(bt):
    f=l.split()
    if not f: continue
    if f[0]=='r': src[f[2]]=f[1]
    elif f[0]=='s': sett[f[1]]=f[2]
def origin(n):
    while n in src: n=src[n]
    return n
TAPS={'LAB.029.044.4:FFB1':'p4','LAB.029.044.4:FFB1L':'p4','LAB.023.044.0:FFT0':'p0','LAB.023.044.1:FFT0':'p1','LAB.029.044.1:FFB1':'p3','LAB.029.044.1:FFB1L':'p3',
 'LAB.024.048.8:FFT1':'u','LAB.024.048.2:FFB1':'v','LAB.024.048.5:FFB1':'w','LAB.024.048.8:FFB1':'z',
 'LAB.027.042.6:FFB0':'mt','LAB.048.019.8:FFT0':'mc','LAB.048.019.7:FFB0':'tA','LAB.043.032.4:FFB1':'tB',
 'LAB.043.032.5:FFT0':'tD','MLAB.034.035.0:FFB0':'j13','LAB.042.035.5:FFT1':'bt','LAB.053.035.6:FFT0':'dE','LAB.042.035.8:FFT0':'X',
 'LAB.048.019.3:FFT1L':'t3','LAB.048.019.0:FFB1L':'t0','LAB.048.019.2:FFT1L':'t2','LAB.048.019.5:FFT1L':'t5','LAB.043.032.8:FFT0':'t8',
 'MLAB.021.048.4:FFB1':'e0x','LAB.042.035.4:FFT1':'e1x','LAB.022.072.3:FFB1':'mcin'}
NEW={k.split(':')[0].rsplit('.',0)[0] for k in sett if k.startswith('LAB.')}
def alm(lab,a):
    m=int(sett['%s:LUT_MASK.%d'%(lab,a)].replace('.',''),16)
    return m, sett.get('%s:TMODE.%d'%(lab,a),'D_E'), sett.get('%s:BMODE.%d'%(lab,a),'C_E')
def ev(lab,a,half,sig,fb=None):
    m,tm,bm=alm(lab,a); pv={}
    for p in ['A','B','C','D','E0','F0','E1','F1']:
        n='%s.%d:%s'%(lab,a,p)
        if n in src: pv[p]=val(origin(n),sig)
    if fb: pv.update(fb)
    return out(m,tm,bm,half,{k:pv.get(k,1) for k in PINS})
def val(o,sig):
    if o in TAPS: return sig[TAPS[o]]
    if o=='LAB.048.019.8:FFT1': return ev('LAB.048.019',8,0,sig,{'TF':sig['mc']})
    if o=='LAB.024.048.9:FFT0': return int(sig['u'] and not sig['v'])
    m=re.match(r'(LAB\.\d+\.\d+)\.(\d):FF(T|B)(\d)L?$',o)
    return ev(m.group(1),int(m.group(2)),0 if m.group(3)=='T' else 1,sig)
P1={'snac':(0,0,1,1),'off':(1,0,0,0),'dig':(0,0,0,1),'neg':(1,1,0,0)}   # p4 p0 p1 p3 = b0 b2 b3 b1
P2={'snac':(1,0,1,0),'off':(0,0,0,1),'dig':(0,0,1,0),'neg':(0,1,0,1)}   # u v w z = b3 b2 b1 b0
isn1=lambda s: (not s['p4']) and (not s['p0']) and s['p1'] and s['p3']
off1=lambda s: s['p4'] and not s['p0'] and not s['p1'] and not s['p3']
isn2=lambda s: s['u'] and not s['v'] and s['w'] and not s['z']
off2=lambda s: (not s['u']) and not s['v'] and not s['w'] and s['z']
bad=0
for v in itertools.product([0,1],repeat=11):
    s=dict(zip(['p4','p0','p1','p3','u','v','w','z','mt','mc','e0x'],v)); s['e1x']=0; s['mcin']=0
    if STOCK:
        w1=int((not s['mt']) and isn1(s)); w2=int((not s['mt']) and isn2(s))
    else:
        w1=int((not s['mt']) and (isn1(s) or (s['mc'] and not off1(s)))); w2=int((not s['mt']) and (isn2(s) or (s['mc'] and not off2(s))))
    bad+=ev('LAB.029.044',2,1,s)!=w1
    bad+=ev('LAB.029.044',9,0,s)!=w2
    bad+=ev('LAB.029.044',2,0,s)!=int((not s['e0x']) and ((not s['p0']) or s['p4'] or s['p1'] or s['p3']))
    bad+=ev('LAB.029.044',9,1,s)!=int(s['p4'] and not s['p3'] and not s['p0'] and not s['p1'])
print('decodes: mismatches',bad)
bad=n=0
names=['tA','tB','tD','t3','t8','t0','t2','t5','mc','j13','bt','dE','X','q2']
for k1,k2 in itertools.product(P1,P2):
    for v in itertools.product([0,1],repeat=len(names)):
        s=dict(zip(names,v)); s.update(zip(['p4','p0','p1','p3'],P1[k1])); s.update(zip(['u','v','w','z'],P2[k2])); s.update(mt=0,e0x=0,e1x=0,mcin=0)
        nq=ev('LAB.042.035',2,0,s,{'TF':s['q2']})
        tb81=(not s['tA']) and (not s['tB']) and s['tD'] and not s['t3'] and not s['t8'] and not s['t0'] and s['t2'] and not s['t5']
        if STOCK: clr=tb81 and not s['mc']
        else:
            anyp=(not isn2(s) and not off2(s)) if s['j13'] else (not isn1(s) and not off1(s))
            clr=(not s['mc']) if tb81 else (s['mc'] and anyp and s['bt'] and (s['dE'] or s['X']))
        bad+= nq!=int(bool((s['X'] or s['q2']) and not clr)); n+=1
print('snacMCEN next: mismatches',bad,'of',n)
