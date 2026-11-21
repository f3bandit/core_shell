"""What's installed and plugged in, for every frontend: a menu is built in and shown only once what it needs is here
(docs/stock-behaviour.md). A frontend can have its own music player, disc screen or DVD row with nothing installed;
the day a user installs the add-on that feeds it (MiSTer DVD, MiSTer Physical Disc, Zaparoo...) or plugs in the
hardware (the dock's drive), the menu shows on its own: no rebuild, no merge.

caps/ holds one entry per capability (git-stored, like addons/: a dev adds one without touching code):

    {"name": "DVD video", "kind": "addon", "by": "owenb321 (MiSTer_DVD)",
     "all": [{"path": "MiSTer_DVDcss"}, {"glob": "_Other/DVD_*.rbf"}],
     "needs": ["disc_drive"],
     "launch": {"rbf": "_Other/DVD_*.rbf"}}

kind: hardware (plugged in), addon (installed by Update All / MiSTer Companion), port (an ARM port with its own Main).
A capability is present when every check in "all" passes, at least one in "any" does (when it has any), and every
capability in "needs" is present. Checks (paths relative to the SD card's root unless absolute):

    {"path": P}          the file or folder exists
    {"glob": G}          something matches
    {"ini": SECTION}     MiSTer.ini has [SECTION] (read only: Coreshell never writes it)
    {"usb": "VVVV:PPPP"} a USB device with that id is plugged in

The answer (`caps` on the socket, and state/caps.json for frontends that read files) gives each one: present, name,
kind, by, via (what was found), and launch (the core to load, the newest match) when it has one. Looked at every
10 s; a change is the event "caps" (which ones changed), so a menu appears the moment the add-on lands.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import glob as _glob
import json
import os
import threading

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERIOD_S = 10.0


def entries(home=HERE):
    out = {}
    d = os.path.join(home, 'caps')
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for n in names:
        if not n.endswith('.json'):
            continue
        try:
            with open(os.path.join(d, n)) as f:
                e = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(e, dict) and e.get('name'):
            out[n[:-5]] = e
    return out


def _abs(root, p):
    return p if os.path.isabs(p) else os.path.join(root, p)


def _ini_sections(root):
    try:
        with open(os.path.join(root, 'MiSTer.ini'), encoding='utf-8', errors='replace') as f:
            return {l.strip()[1:-1].strip().lower() for l in f if l.strip().startswith('[') and l.strip().endswith(']')}
    except OSError:
        return set()


def _usb_ids(usb_sys):
    ids = set()
    try:
        devs = os.listdir(usb_sys)
    except OSError:
        return ids
    for d in devs:
        try:
            with open(os.path.join(usb_sys, d, 'idVendor')) as a, open(os.path.join(usb_sys, d, 'idProduct')) as b:
                ids.add('%s:%s' % (a.read().strip().lower(), b.read().strip().lower()))
        except OSError:
            continue
    return ids


class Caps(object):
    def __init__(self, root='/media/fat', home=HERE, emit=None, usb_sys='/sys/bus/usb/devices'):
        self.root, self.home = root, home
        self.emit = emit or (lambda *a, **k: None)
        self.usb_sys = usb_sys
        self.last = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()       # (the 10 s look and a `caps` refresh on the socket)

    def _check(self, c, ctx):
        """(passed, what was found)"""
        if 'path' in c:
            p = _abs(self.root, c['path'])
            return os.path.exists(p), p
        if 'glob' in c:
            m = sorted(_glob.glob(_abs(self.root, c['glob'])))
            return bool(m), (m[-1] if m else None)
        if 'ini' in c:
            if 'ini' not in ctx:
                ctx['ini'] = _ini_sections(self.root)
            return str(c['ini']).strip().lower() in ctx['ini'], 'MiSTer.ini [%s]' % c['ini']
        if 'usb' in c:
            if 'usb' not in ctx:
                ctx['usb'] = _usb_ids(self.usb_sys)
            return str(c['usb']).lower() in ctx['usb'], 'USB %s' % c['usb']
        return False, None

    def look(self):
        """Every capability, now."""
        ents = entries(self.home)
        ctx, own = {}, {}
        for cid, e in ents.items():
            via, ok = [], True
            for c in e.get('all') or []:
                p, w = self._check(c, ctx)
                ok = ok and p
                if p and w:
                    via.append(w)
            anyc = e.get('any') or []
            if anyc:
                hit = False
                for c in anyc:
                    p, w = self._check(c, ctx)
                    if p:
                        hit = True
                        if w:
                            via.append(w)
                ok = ok and hit
            own[cid] = (ok, via)
        out = {}

        def present(cid, seen=()):
            if cid not in own or cid in seen:
                return False
            return own[cid][0] and all(present(n, seen + (cid,)) for n in ents[cid].get('needs') or [])
        for cid, e in ents.items():
            item = {'present': present(cid), 'name': e.get('name'), 'kind': e.get('kind', 'addon'),
                    'by': e.get('by'), 'via': own[cid][1]}
            if e.get('needs'):
                item['needs'] = list(e['needs'])
            lr = (e.get('launch') or {}).get('rbf')
            if lr:
                m = sorted(_glob.glob(_abs(self.root, lr)))
                item['launch'] = {'rbf': m[-1] if m else None}
            out[cid] = item
        return out

    def refresh(self):
        with self._lock:
            return self._refresh()

    def _refresh(self):
        now = self.look()
        changed = sorted(k for k in set(now) | set(self.last)
                         if (now.get(k) or {}).get('present') != (self.last.get(k) or {}).get('present'))
        first = not self.last
        same = now == self.last
        self.last = now
        if not same or not os.path.exists(os.path.join(self.home, 'state', 'caps.json')):
            self._write(now)            # (only when something changed: frontends watch the file's time)
        if changed and not first:
            self.emit('caps', changed=changed, present=[k for k in changed if now.get(k, {}).get('present')])
        return now

    def _write(self, now):
        d = os.path.join(self.home, 'state')
        try:
            os.makedirs(d, exist_ok=True)
            tmp = os.path.join(d, '.caps.json.%d.tmp' % os.getpid())
            with open(tmp, 'w') as f:
                json.dump({'caps': now}, f, indent=1, sort_keys=True)
            os.replace(tmp, os.path.join(d, 'caps.json'))
        except OSError:
            pass

    def run(self):
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception:
                pass
            self._stop.wait(PERIOD_S)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self._stop.set()
