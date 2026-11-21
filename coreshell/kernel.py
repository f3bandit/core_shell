"""The kernel's display, kept working across MiSTer Linux updates (self-healing).

A frontend (Phosphor, Console Mode, any other) draws straight to Linux: /dev/fb0, MiSTer's framebuffer driver
(/sys/module/MiSTer_fb/parameters), the console's graphics mode, memory set aside at boot (/dev/mem). None of it is a
versioned interface: a kernel update can change any of it (Sept 7 2026, 5.15 -> 6.18: frontends had to be patched).

Coreshell asks the kernel how its display is set up (probe), keeps the answer from a boot where everything worked
(known good), and compares at every start:

  same            nothing to do
  values changed  the framebuffer reports another size, format, stride or address, or the driver's parameters moved:
                  the settings frontends read (display.json) are written from what the kernel says now, so a frontend
                  opening its screen through Coreshell's display layer (display/csdisplay.c) draws right on the new
                  kernel. The new state becomes the known-good one.
  missing         something a frontend needs is gone (the framebuffer device, the driver's mode parameter, the memory
                  set aside at boot, the framebuffer ioctls): it can't be adapted to. The known-good state is kept,
                  status "degraded", an event and a plain report. The frontend stays in charge (corehost passes the
                  verdict on); only if the framebuffer itself can't be drawn on does corehost keep the screen on the
                  OSD, which the FPGA draws, never a black screen. `kernel hold` stops Update All bringing that Linux
                  back, and update-guard's rollback (if installed) puts the old one back.

Files (Coreshell's own, in /media/fat/coreshell/state/kernel): known_good.json, now.json, display.json, status.json,
state (the state alone: ok, adapted, degraded; corehost reads it), history/.
Nothing of the user's is changed unless asked: `kernel hold` / `kernel unhold` set and restore [mister] update_linux in
downloader.ini alone, a copy kept before each change (ported from update-guard).

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import fcntl
import json
import os
import platform
import shutil
import struct
import time

FB_DEV = '/dev/fb0'
FB_SYS = '/sys/class/graphics/fb0'
MFB = '/sys/module/MiSTer_fb/parameters'
MAIN_FB_ADDR = 0x22001000                 # where Main's Linux framebuffer starts (video.cpp FB_ADDR + 4096: the first page is the driver's)
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602

# what each check is, and what a difference in it means
ADAPT = ('fb.xres', 'fb.yres', 'fb.bpp', 'fb.stride', 'fb.format', 'mfb.mode', 'mfb.width', 'mfb.height',
         'mfb.stride', 'mfb.format', 'kernel', 'linux')
NEEDED = ('fb.device', 'fb.ioctl', 'fb.smem', 'mfb.params', 'mem.reserved', 'dev.mem', 'dev.uinput')


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _fb_ioctls():
    """FBIOGET_VSCREENINFO / FSCREENINFO, as a frontend asks them. None: the device or the ioctls are gone."""
    try:
        fd = os.open(FB_DEV, os.O_RDONLY)
    except OSError:
        return None, None
    try:
        v = fcntl.ioctl(fd, FBIOGET_VSCREENINFO, bytes(160))
        f = fcntl.ioctl(fd, FBIOGET_FSCREENINFO, bytes(80))
    except OSError:
        return {}, {}
    finally:
        os.close(fd)
    xres, yres, xv, yv, xo, yo, bpp, gray = struct.unpack_from('8I', v, 0)
    roff, rlen, _, goff, glen, _, boff, blen, _, toff, tlen, _ = struct.unpack_from('12I', v, 32)
    # struct fb_fix_screeninfo: id[16], smem_start (ulong), smem_len, type, type_aux, visual (u32), xpanstep, ypanstep,
    # ywrapstep (u16), line_length (u32): native alignment, as the kernel's (the MiSTer is 32-bit ARM)
    t = struct.unpack_from('16sLIIIIHHHI', f, 0)
    smem_start, smem_len, line_length = t[1], t[2], t[9]
    fmt = '%d%d%d%d' % (tlen, rlen, glen, blen) if bpp == 32 else '%d' % bpp
    return ({'xres': xres, 'yres': yres, 'bpp': bpp, 'order': [roff, goff, boff, toff], 'format': fmt},
            {'id': t[0].split(b'\0')[0].decode('ascii', 'replace'), 'smem_start': smem_start, 'smem_len': smem_len,
             'line_length': line_length})


def probe():
    """How the kernel's display is set up now: {check: value}. A missing thing is None."""
    p = {}
    p['kernel'] = platform.release()
    p['linux'] = _read('/MiSTer.version')
    p['fb.device'] = os.path.exists(FB_DEV) or None
    v, f = _fb_ioctls()
    p['fb.ioctl'] = (v is not None and bool(v)) or None
    if v:
        p['fb.xres'], p['fb.yres'], p['fb.bpp'], p['fb.format'] = v['xres'], v['yres'], v['bpp'], v['format']
    if f:
        p['fb.stride'] = f['line_length']
        p['fb.smem'] = ('0x%08x+%d' % (f['smem_start'], f['smem_len'])) if f['smem_start'] == MAIN_FB_ADDR else None
        p['fb.smem_seen'] = '0x%08x+%d' % (f['smem_start'], f['smem_len'])
        p['fb.id'] = f['id']
    params = sorted(os.listdir(MFB)) if os.path.isdir(MFB) else []
    p['mfb.params'] = ','.join(params) if 'mode' in params else None
    for k in ('mode', 'width', 'height', 'stride', 'format'):
        if k in params:
            p['mfb.' + k] = _read(os.path.join(MFB, k))
    cmd = _read('/proc/cmdline') or ''
    mem = [w for w in cmd.split() if w.startswith(('mem=', 'memmap='))]
    p['mem.reserved'] = ' '.join(mem) if mem else None   # (the DDR set aside for the FPGA: cores, offload, ARM cores)
    p['dev.mem'] = os.path.exists('/dev/mem') or None
    p['dev.uinput'] = os.path.exists('/dev/uinput') or None
    try:
        with open('/proc/modules') as fh:
            p['modules'] = ','.join(sorted(l.split()[0] for l in fh))
    except OSError:
        p['modules'] = None
    return p


def compare(old, new):
    """[(check, old, new, kind)]: kind 'adapt' (a value frontends read: written for them) or 'missing' (needed and gone)."""
    out = []
    for k in NEEDED:
        if old.get(k) is not None and new.get(k) is None:
            out.append((k, old.get(k), new.get(k), 'missing'))
    for k in ADAPT:
        if old.get(k) != new.get(k) and new.get(k) is not None:
            out.append((k, old.get(k), new.get(k), 'adapt'))
    if old.get('modules') and new.get('modules'):
        gone = sorted(set(old['modules'].split(',')) - set(new['modules'].split(',')))
        if gone:
            out.append(('modules', ','.join(gone), None, 'note'))   # (a driver not loaded now: WiFi, a pad... said)
    return out


class Kernel(object):
    def __init__(self, home='/media/fat/coreshell', root='/media/fat', say=None, emit=None, prober=probe):
        self.dir = os.path.join(home, 'state', 'kernel')
        self.root = root
        self.say = say or (lambda *a: None)
        self.emit = emit or (lambda *a, **k: None)
        self.probe = prober
        self.status = self._load('status.json') or {'state': 'unknown'}   # (the last check's, as saved)

    def _path(self, name):
        return os.path.join(self.dir, name)

    def _load(self, name):
        try:
            with open(self._path(name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _save(self, name, data):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._path(name + '.tmp')
        with open(tmp, 'w') as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path(name))

    def display_settings(self, p):
        """What a frontend needs to open its screen, from what the kernel says (display.json, csdisplay.c reads it)."""
        return {'xres': p.get('fb.xres'), 'yres': p.get('fb.yres'), 'bpp': p.get('fb.bpp'), 'stride': p.get('fb.stride'),
                'format': p.get('fb.format'), 'smem': p.get('fb.smem_seen'), 'mode_param': os.path.join(MFB, 'mode')
                if p.get('mfb.params') else None, 'kernel': p.get('kernel'), 'linux': p.get('linux'),
                'checked': int(time.time())}

    def check(self):
        """At start (and on asking): probe, compare with the known-good state, adapt or report."""
        now = self.probe()
        self._save('now.json', now)
        good = self._load('known_good.json')
        if not good:
            if any(now.get(k) is None for k in NEEDED):
                st = {'state': 'degraded', 'first': True, 'missing': [k for k in NEEDED if now.get(k) is None]}
            else:
                self._save('known_good.json', now)
                self._save('display.json', self.display_settings(now))
                st = {'state': 'ok', 'first': True}
            return self._done(st, now)
        diffs = compare(good, now)
        missing = [d for d in diffs if d[3] == 'missing']
        adapted = [d for d in diffs if d[3] == 'adapt']
        notes = [d for d in diffs if d[3] == 'note']
        if missing:
            st = {'state': 'degraded', 'missing': [d[0] for d in missing], 'changes': self._list(diffs),
                  'advice': self._advice(missing)}
            self.say('kernel: %s changed under the display and can\'t be adapted to: %s'
                     % (now.get('kernel'), ', '.join(d[0] for d in missing)))
            self.emit('kernel', state='degraded', missing=st['missing'], kernel=now.get('kernel'))
            return self._done(st, now)
        if notes and not adapted:
            # only drivers not loaded now (Wi-Fi, Bluetooth, a pad: unplugged, or not loaded yet at boot, which
            # Coreshell's start can beat by a minute): said, and the known-good state, with its full list, kept
            return self._done({'state': 'ok', 'notes': self._list(notes)}, now)
        if adapted:
            os.makedirs(self._path('history'), exist_ok=True)
            shutil.copy(self._path('known_good.json'),
                        self._path('history/%s_%s.json' % (time.strftime('%Y%m%d-%H%M%S'), good.get('kernel'))))
            self._save('known_good.json', now)
            self._save('display.json', self.display_settings(now))
            st = {'state': 'adapted', 'changes': self._list(diffs)}
            self.say('kernel: adapted to %s: %s' % (now.get('kernel'), '; '.join(
                '%s %s -> %s' % (d[0], d[1], d[2]) for d in adapted + notes)))
            self.emit('kernel', state='adapted', changes=st['changes'], kernel=now.get('kernel'))
            return self._done(st, now)
        if not os.path.exists(self._path('display.json')):
            self._save('display.json', self.display_settings(now))
        return self._done({'state': 'ok'}, now)

    def _list(self, diffs):
        return [{'check': k, 'was': a, 'now': b, 'kind': kind} for k, a, b, kind in diffs]

    def _advice(self, missing):
        names = {'fb.device': "Linux's framebuffer device (/dev/fb0) is gone",
                 'fb.ioctl': "the framebuffer doesn't answer a frontend's questions (FBIOGET_*SCREENINFO)",
                 'fb.smem': "the framebuffer isn't where MiSTer's Main shows it (0x22001000)",
                 'mfb.params': "MiSTer's framebuffer driver has no mode setting",
                 'mem.reserved': "the memory set aside for the FPGA at boot (mem= memmap=) is gone",
                 'dev.mem': '/dev/mem is gone', 'dev.uinput': '/dev/uinput is gone (virtual controllers)'}
        out = [names.get(d[0], d[0]) for d in missing]
        out.append("Your frontend stays in charge; if it can't draw at all, the screen stays on the OSD. "
                   "`python3 -m coreshell kernel hold` stops Update All bringing this Linux back; "
                   "update-guard's rollback puts the last one back.")
        return out

    def _done(self, st, now):
        st.update(kernel=now.get('kernel'), linux=now.get('linux'), checked=int(time.time()))
        self.status = st
        self._save('status.json', st)
        os.makedirs(self.dir, exist_ok=True)       # the state alone, one word, for C (corehost's display check)
        with open(self._path('state.tmp'), 'w') as f:
            f.write(st['state'] + '\n')
        os.replace(self._path('state.tmp'), self._path('state'))
        return st

    def accept(self):
        """The state now is the known-good one (after a fix the probe can't see)."""
        now = self.probe()
        self._save('known_good.json', now)
        self._save('display.json', self.display_settings(now))
        return self._done({'state': 'ok', 'accepted': True}, now)

    # ---- Update All's Linux: held back. Ported from phosphor-modules/update-guard (restore-points.sh: ini_value, ini_put,
    # hold_linux, unhold_linux): only [mister] update_linux is touched, a copy of downloader.ini is kept before each
    # change, and unhold puts back the value it had (or removes the key), so anything else changed meanwhile stays.
    def _dl_ini(self):
        return os.path.join(self.root, 'downloader.ini')

    def _ug_held(self):
        """update-guard (Phosphor) already holding Linux: it owns the hold, Coreshell leaves it alone."""
        try:
            with open(os.path.join(self.root, 'phosphor', 'state', 'update-guard.state')) as f:
                return any(l.strip() == 'hold_set=1' for l in f)
        except OSError:
            return False

    @staticmethod
    def ini_value(text, section, key):
        in_s = False
        for line in text.splitlines():
            line = line.rstrip('\r')
            if line.startswith('['):
                in_s = line == '[%s]' % section
                continue
            if in_s and line.split('=', 1)[0].replace(' ', '').replace('\t', '') == key and '=' in line:
                return line.split('=', 1)[1].strip(' \t')
        return None

    @staticmethod
    def ini_put(text, section, key, value):
        """Set, add or (value None) remove a key, as update-guard's ini_put: the key's line is replaced in place, a new
        one goes at the end of its section, a missing section is added at the end."""
        out, in_s, seen, done = [], False, False, False

        def emit():
            nonlocal done
            if not done and value is not None:
                out.append('%s = %s' % (key, value))
            done = True
        for line in text.splitlines():
            line = line.rstrip('\r')
            if line.startswith('['):
                if in_s:
                    emit()
                in_s = line == '[%s]' % section
                seen = seen or in_s
                out.append(line)
                continue
            if in_s and '=' in line and line.split('=', 1)[0].replace(' ', '').replace('\t', '') == key:
                emit()
                continue
            out.append(line)
        if in_s:
            emit()
        elif not seen and value is not None:
            out += ['', '[%s]' % section, '%s = %s' % (key, value)]
        return '\n'.join(out) + '\n' if out else ''

    def _dl_write(self, text):
        p = self._dl_ini()
        if os.path.exists(p):                      # a copy before each change (state/kernel/backups)
            bdir = self._path('backups')
            os.makedirs(bdir, exist_ok=True)
            with open(p) as f, open(os.path.join(bdir, 'downloader.ini.%d' % int(time.time())), 'w') as b:
                b.write(f.read())
        with open(p + '.tmp', 'w') as f:
            f.write(text)
        os.replace(p + '.tmp', p)

    def hold(self):
        held = self._load('hold.json') or {}
        if held.get('hold_set'):
            return {'held': True, 'note': 'already held'}
        if self._ug_held():
            return {'held': True, 'note': 'already held by update-guard (update-guard.sh unhold releases it)'}
        p = self._dl_ini()
        try:
            with open(p) as f:
                text = f.read()
        except OSError:
            text = ''
        before = self.ini_value(text, 'mister', 'update_linux')
        self._dl_write(self.ini_put(text, 'mister', 'update_linux', 'false'))
        self._save('hold.json', {'hold_set': True, 'hold_before': before})
        return {'held': True}

    def unhold(self):
        held = self._load('hold.json') or {}
        if not held.get('hold_set'):
            return {'held': self._ug_held(), 'note': 'not held by Coreshell'}
        p = self._dl_ini()
        try:
            with open(p) as f:
                text = f.read()
        except OSError:
            text = ''
        self._dl_write(self.ini_put(text, 'mister', 'update_linux', held.get('hold_before')))
        self._save('hold.json', {'hold_set': False})
        return {'held': False}
