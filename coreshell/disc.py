"""The disc in the drive as a game like any other (discfs).

discfs (discfs/, in C) serves the drive's disc as <name>.cue and <name>.bin under
MOUNT. A <system>@disc launch mounts it, names it after the game on it (the
serial and volume name, so the core's saves and Coreshell's per-game settings
are that game's), and launches the system's own profile with the cue: the
frontend's Main runs it like a game on the SD card, with Coreshell's settings,
Super MemCard and the in-game menu. No MiSTer_Physical-CD.

A disc swapped while a game runs isn't seen until the next launch from the
drive (each launch mounts the disc that's in the drive then).
"""
import hashlib
import os
import re
import signal
import subprocess
import time

EXE = 'coreshell/bin/discfs'                # under the SD card's root
MOUNT = '/tmp/coreshell/disc'
DEVICE = '/dev/sr0'
LOG = '/tmp/discfs.log'


def exe_path(paths):
    p = os.path.join(paths.root, EXE)
    return p if os.access(p, os.X_OK) else None


def _toc(exe):
    """[(track, lba, audio)], lead-out; from discfs --toc."""
    out = subprocess.run([exe, '-d', DEVICE, '--toc'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         timeout=60).stdout.decode('utf-8', 'replace')
    tracks, lead = [], None
    no = None
    for line in out.splitlines():
        m = re.match(r'\s*TRACK (\d+) (\S+)', line)
        if m:
            no, audio = int(m.group(1)), m.group(2) == 'AUDIO'
            continue
        m = re.match(r'\s*INDEX 01 (\d+):(\d+):(\d+)', line)
        if m and no is not None:
            tracks.append((no, (int(m.group(1)) * 60 + int(m.group(2))) * 75 + int(m.group(3)), audio))
        m = re.match(r'lead-out (\d+)', line)
        if m:
            lead = int(m.group(1))
    if not tracks or not lead:
        raise OSError('no readable disc in the drive' + (': ' + out.strip() if out.strip() else ''))
    return tracks, lead


def _sectors(lba, n=1):
    """User data (2048 bytes a sector) of a data track, from the block device."""
    with open(DEVICE, 'rb') as f:
        f.seek(lba * 2048)
        return f.read(2048 * n)


def _text(b):
    return re.sub(r'\s+', ' ', b.decode('ascii', 'replace').replace('�', ' ')).strip()


def identify(tracks, lead):
    """What's on the disc: (system guess, a name for its files)."""
    data = [lba for _, lba, audio in tracks if not audio]
    ident = None
    if data:
        try:
            first = _sectors(data[0])
            if first.startswith(b'SEGA SEGASATURN'):
                ident = 'saturn', '%s (%s)' % (_text(first[0x60:0xD0]), _text(first[0x20:0x2A]))
            elif first.startswith(b'SEGADISCSYSTEM'):
                ident = 'megacd', '%s (%s)' % (_text(first[0x150:0x180]) or _text(first[0x120:0x150]),
                                               _text(first[0x180:0x18E]))
            else:
                pvd = _sectors(data[0] + 16)
                if pvd[1:6] == b'CD001':
                    vol = _text(pvd[40:72])
                    serial = _psx_serial(pvd, data[0])
                    ident = ('psx' if serial else None), ('%s (%s)' % (vol, serial) if serial else vol)
        except OSError:
            pass
    if not ident or not ident[1]:
        toc = ','.join('%d:%d' % (t, l) for t, l, _ in tracks) + ',%d' % lead
        ident = None, 'Disc ' + hashlib.sha1(toc.encode()).hexdigest()[:8]
    return ident[0], re.sub(r'[\\/:*?"<>|]+', ' ', ident[1]).strip()[:120]


def _psx_serial(pvd, base, read=None):
    """SCUS-94351 from SYSTEM.CNF's BOOT line (ISO 9660 root directory). read(lba, n): the data track's user
    data (2048 bytes a sector); the drive's by default, a disc image's for gameid.py."""
    read = read or _sectors
    root = pvd[156:190]
    lba, size = int.from_bytes(root[2:6], 'little'), int.from_bytes(root[10:14], 'little')
    d = read(lba, max(1, min(4, size // 2048)))
    i = 0
    while i < len(d):
        n = d[i]
        if not n:
            i = (i // 2048 + 1) * 2048
            continue
        name = d[i + 33:i + 33 + d[i + 32]]
        if name.upper().startswith(b'SYSTEM.CNF'):
            cnf = read(int.from_bytes(d[i + 2:i + 6], 'little')).decode('ascii', 'replace')
            return psx_code_from_cnf(cnf)
        i += n
    return None


def psx_code_from_cnf(cnf):
    """The product code from SYSTEM.CNF's BOOT line, as phosphord's ident.c (psx_boot) reads it: the boot file's
    name after its last \\ : or /, up to ; CR LF or a space."""
    b = cnf.find('BOOT')
    if b < 0:
        return None
    eq = cnf.find('=', b)
    if eq < 0:
        return None
    name = cnf[eq + 1:]
    k = 0
    while k < len(name) and name[k] not in '\n\r;':
        if name[k] in '\\:/':
            name, k = name[k + 1:], 0
            continue
        k += 1
    name = name.lstrip(' ')
    e = 0
    while e < len(name) and name[e] not in ';\r\n ':
        e += 1
    name = name[:e]
    return psx_code(name) if name else None


def psx_code(raw):
    """SLUS_010.41 -> SLUS-01041: phosphord's ident.c psx_serial, ported (the same code everywhere: the library,
    smcd, the card). The letters, a dash, the rest; a boot file with an extra letter (SLUSP012.06) drops it."""
    t = ''.join(c.upper() for c in raw if c.isalnum())[:31]
    a = 0
    while a < len(t) and t[a].isalpha():
        a += 1
    if a == 5 and a < len(t) and t[a].isdigit():
        t, a = t[:4] + t[5:], 4
    if 3 <= a <= 4 and t[a:]:
        return '%s-%s' % (t[:a], t[a:])
    return t or None


class Disc(object):
    def __init__(self, paths):
        self.paths = paths
        self.proc = None
        self.cue = None

    def unmount(self):
        """The disc served before (and any discfs left from a Coreshell restarted)."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc, self.cue = None, None
        for d in os.listdir('/proc'):
            if d.isdigit():
                try:
                    with open('/proc/%s/cmdline' % d, 'rb') as f:
                        argv0 = f.read().split(b'\0')[0]
                except OSError:
                    continue
                if os.path.basename(argv0) == b'discfs':
                    try:
                        os.kill(int(d), signal.SIGTERM)
                    except OSError:
                        pass
        try:
            with open('/proc/mounts') as f:
                if (' %s ' % MOUNT) in f.read():
                    subprocess.run(['umount', '-l', MOUNT], stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def mount(self):
        """The disc that's in the drive now: (cue path, game name, system guess)."""
        exe = exe_path(self.paths)
        if not exe:
            raise OSError('discfs is not installed (%s)' % EXE)
        self.unmount()
        tracks, lead = _toc(exe)
        system, name = identify(tracks, lead)
        os.makedirs(MOUNT, exist_ok=True)
        log = open(LOG, 'wb')
        self.proc = subprocess.Popen([exe, '-d', DEVICE, '-n', name, MOUNT], stdin=subprocess.DEVNULL, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        log.close()
        cue = os.path.join(MOUNT, name + '.cue')
        for _ in range(150):
            if self.proc.poll() is not None:
                break
            if os.path.exists(cue):
                self.cue = cue
                return cue, name, system
            time.sleep(0.2)
        try:
            with open(LOG, encoding='utf-8', errors='replace') as f:
                why = f.read().strip()
        except OSError:
            why = ''
        self.unmount()
        raise OSError('the disc could not be mounted' + (': ' + why if why else ''))

    def serving(self, rom):
        return bool(rom and self.cue and self.proc and self.proc.poll() is None and rom == self.cue)
