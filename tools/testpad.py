#!/usr/bin/env python3
"""A virtual Xbox 360 pad (uinput) for tests on the MiSTer: a game played without anyone at the console (the save tests:
a Zelda file registered, then left without the OSD). Presses read from a FIFO (default /tmp/pad.fifo), one per line:
   <button> [hold_ms]       a b x y lb rb back start mode up down left right
   <b1>+<b2> [hold_ms]      held together (up+a: Zelda's save menu on pad 2)
   wait <ms>
   python3 tools/testpad.py [/tmp/pad2.fifo] &   then   printf "start\nwait 1500\na\n" > /tmp/pad.fifo
In-game screenshots to see where it is: echo screenshot > /dev/MiSTer_cmd (screenshots/<core>/).

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE)."""
import fcntl, os, struct, time
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
UI_DEV_CREATE = 0x5501
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
BTN = {'a': 0x130, 'b': 0x131, 'x': 0x133, 'y': 0x134, 'lb': 0x136, 'rb': 0x137, 'back': 0x13a, 'start': 0x13b,
       'mode': 0x13c}
ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y = 0, 1, 0x10, 0x11
fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY); fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
for c in BTN.values():
    fcntl.ioctl(fd, UI_SET_KEYBIT, c)
for a in (ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y):
    fcntl.ioctl(fd, UI_SET_ABSBIT, a)
absmax = [0] * 64; absmin = [0] * 64; absfuzz = [0] * 64; absflat = [0] * 64
for a in (ABS_X, ABS_Y):
    absmin[a], absmax[a] = -32768, 32767
for a in (ABS_HAT0X, ABS_HAT0Y):
    absmin[a], absmax[a] = -1, 1
name = b'Microsoft X-Box 360 pad'.ljust(80, b'\0')
dev = name + struct.pack('HHHHi', 3, 0x045e, 0x028e, 0x0114, 0) + struct.pack('64i', *absmax) \
      + struct.pack('64i', *absmin) + struct.pack('64i', *absfuzz) + struct.pack('64i', *absflat)
os.write(fd, dev)
fcntl.ioctl(fd, UI_DEV_CREATE)

def ev(t, c, v):
    s = time.time()
    os.write(fd, struct.pack('llHHi', int(s), int((s % 1) * 1e6), t, c, v))

def sync():
    ev(EV_SYN, 0, 0)

def press(b, hold):
    if b in ('up', 'down', 'left', 'right'):
        ax = ABS_HAT0Y if b in ('up', 'down') else ABS_HAT0X
        v = -1 if b in ('up', 'left') else 1
        ev(EV_ABS, ax, v); sync(); time.sleep(hold); ev(EV_ABS, ax, 0); sync()
    else:
        ev(EV_KEY, BTN[b], 1); sync(); time.sleep(hold); ev(EV_KEY, BTN[b], 0); sync()

def down(b, on):
    if b in ('up', 'down', 'left', 'right'):
        ax = ABS_HAT0Y if b in ('up', 'down') else ABS_HAT0X
        ev(EV_ABS, ax, (-1 if b in ('up', 'left') else 1) if on else 0)
    else:
        ev(EV_KEY, BTN[b], 1 if on else 0)

import sys
F = sys.argv[1] if len(sys.argv) > 1 else '/tmp/pad.fifo'
if os.path.exists(F):
    os.unlink(F)
os.mkfifo(F)
while True:
    with open(F) as f:
        for line in f:
            p = line.split()
            if not p:
                continue
            if p[0] == 'wait':
                time.sleep(int(p[1]) / 1000)
            elif p[0] == 'quit':
                raise SystemExit
            elif '+' in p[0]:
                hold = int(p[1]) / 1000 if len(p) > 1 else 0.3
                for b in p[0].split('+'):
                    down(b, 1)
                sync(); time.sleep(hold)
                for b in p[0].split('+'):
                    down(b, 0)
                sync()
            else:
                press(p[0], int(p[1]) / 1000 if len(p) > 1 else 0.12)
                time.sleep(0.15)
