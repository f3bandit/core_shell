"""Coreshell - Super MemCard page.

Super MemCard (smcd) keeps every console's saves on the card in a front port.
Its entries used to need building into each core's menu; with Coreshell's
unified menu they're just extra entries on the system's menu, served from
smcd, and no core is changed.

Talks to smcd's socket (/tmp/smcd.sock) with its own commands (super-memcard
smcd/smcd.c): CARDS <sys> for the card picker, CARD <sys>\\t<n> to pick one.
Coreshell doesn't tell smcd about games: the card is reached through Main's
front ports, so only a Main with the Super MemCard shim reaches it at all, and
that Main sends LAUNCH before any core loads (also one handed to another
Main by main=, e.g. MiSTer_RA) and MENU when the menu is back.

A system is smcd's saves folder name (NES, SNES, GAMEBOY, TGFX16...), which
is the profile's saves folder. PSX keeps its own cards (its SNAC card slots),
so it gets no card picker here.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import os
import socket
import threading
import time

SOCK = '/tmp/smcd.sock'
PAGE = 'Super MemCard'
CARD_KEY = PAGE + '/Card'
from . import quirks


def no_picker():
    """Systems the card keeps memory cards for itself (no picker): the quirks table card_no_picker."""
    return quirks.card_no_picker()


_NEW = ' (new)'


def _ask(cmd, sock=SOCK, timeout=20.0, ends=1):
    """smcd command(s), one a line; the answer's lines, up to the ends-th END / DONE."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock)
        s.sendall((cmd + '\n').encode('utf-8'))
        buf = b''
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                d = s.recv(65536)
            except socket.timeout:
                break
            if not d:
                break
            buf += d
            lines = buf.decode('utf-8', 'replace').split('\n')[:-1]
            if sum(1 for l in lines if l.startswith('END') or l.startswith('DONE')) >= ends:
                break
        return [l for l in buf.decode('utf-8', 'replace').split('\n')
                if l and not l.startswith('END')]
    finally:
        s.close()


def system_of(prof):
    """smcd's name for the profile's system: its saves folder."""
    return os.path.basename(prof.get('saves_dir', '').rstrip('/'))


CACHE_SECS = 300          # the card's lists, kept this long before a background re-read


class SuperMemCard(object):
    """smcd reads the card through a front port: SYSTEMS takes seconds, CARDS longer. The menu
    answers from a cache filled in the background (at start, and again when it's stale), so a
    menu never waits on the card."""

    def __init__(self, sock=SOCK, background=True):
        self.sock = sock
        self._systems = (0, set())
        self._cards = {}                     # system -> (time read, cards)
        self._busy = threading.Lock()
        self._qlock = threading.Lock()
        self._queue = []
        self._full = False
        self._pause_until = 0.0              # (a game starting: the reader waits, so the launch never queues behind it)
        self.background = background
        if background:
            self.warm()

    def warm(self, first=None):
        """Read every system's cards in the background, one reader at a time; `first` (a system
        someone just opened) goes to the front of the line."""
        with self._qlock:
            if first:
                if first in self._queue:
                    self._queue.remove(first)
                self._queue.insert(0, first)
            else:
                self._full = True
        if not self._busy.acquire(False):
            return

        def run():
            try:
                if not self.up():
                    self._busy.release()
                    return
                while True:
                    with self._qlock:
                        full, self._full = self._full, False
                    if full:                 # (smcd asked outside the lock: a menu's warm() never waits on the card)
                        self._systems = (0, set())
                        names = sorted(n for n in self.systems() if n not in no_picker())
                        with self._qlock:
                            self._queue += [n for n in names if n not in self._queue]
                    while time.time() < self._pause_until:
                        time.sleep(0.2)
                    with self._qlock:
                        if not self._queue:
                            self._busy.release()   # (released under the lock: a warm() now finds the reader gone)
                            return
                        sys_name = self._queue.pop(0)
                    try:
                        self._cards[sys_name] = (time.time(), self.cards(sys_name))
                    except OSError:
                        pass
            except BaseException:
                self._busy.release()
                raise
        threading.Thread(target=run, daemon=True).start()

    def pause(self, s=20.0):
        """A game is starting: the background reader asks smcd nothing more for s seconds (at most the one it's
        asking finishes), so the launch's card steps aren't queued behind every system's CARDS (7 s, Nov 23 2026)."""
        self._pause_until = time.time() + s

    def cached_cards(self, sys_name):
        t, c = self._cards.get(sys_name, (0, None))
        if self.background:
            if not t:
                self.warm(first=sys_name)      # (not read yet: this one next)
            elif time.time() - t > CACHE_SECS:
                self.warm()
            return c
        c = self.cards(sys_name)              # (no background reader: ask now)
        self._cards[sys_name] = (time.time(), c)
        return c

    def up(self):
        return os.path.exists(self.sock)

    def select(self, sys_name, game_id):
        """(launch, before the core loads) the game being started: smcd's SELECT. PSX: that game's own PS1
        card goes on the bus. Returns smcd's first answer line: "select ok", "select failed", "nocard"..."""
        lines = _ask('SELECT %s\t%s' % (sys_name, game_id), self.sock, timeout=30.0)
        return lines[0] if lines else 'no answer'

    def play_prep(self, sys_name, card, save, port=0):
        """(the memory manager's Play) smcd's PLAYPREP: that save's card switched in, the game's MGL back.
        (answer line, MGL path or None): "play ok <card>", "play notfound", "play psx", "play failed"..."""
        cmd = 'PLAYPREP %s\t%d\t%s' % (sys_name, int(card), save)
        lines = _ask(('PORT %d\n' % port if port in (1, 2) else '') + cmd, self.sock, timeout=60.0,
                     ends=2 if port in (1, 2) else 1)
        lines = [l for l in lines if not l.startswith('port ')]
        ans = lines[0] if lines else 'no answer'
        if ans.startswith('play ok') and '\t' in ans:
            ans, mgl = ans.split('\t', 1)
            return ans, mgl
        return ans, None

    def event(self, line, timeout=30.0):
        """Main's game start / menu / OSD file (LAUNCH, MENU, ROM), passed on to smcd: its DONE line."""
        lines = _ask(line, self.sock, timeout=timeout)
        done = [l for l in lines if l.startswith('DONE')]
        return done[0] if done else (lines[0] if lines else '')

    def systems(self):
        """Systems smcd handles (cached: SYSTEMS doesn't change while smcd runs)."""
        t, s = self._systems
        if time.time() - t > 3600 or not t:
            try:
                s = {l.split('\t')[0] for l in _ask('SYSTEMS', self.sock) if '\t' in l}
            except OSError:
                s = set()
            self._systems = (time.time(), s)
        return s

    def handles(self, prof):
        if not self.up():
            return False
        if self.background:                  # (from the cache: the reader fills it)
            return system_of(prof) in self._cards or system_of(prof) in self._systems[1]
        return system_of(prof) in self.systems()

    # -------------------------------------------------------------- card picker
    def cards(self, sys_name):
        """[{card, games, newest, current, new}] for that system on the card."""
        out = []
        for l in _ask('CARDS ' + sys_name, self.sock):
            f = l.split('\t')
            if f[0] == 'card' and len(f) >= 5:
                out.append({'card': int(f[1]), 'games': int(f[2]), 'newest': int(f[3]),
                            'current': f[4] == '1', 'new': False})
            elif f[0] == 'new' and len(f) >= 2:
                out.append({'card': int(f[1]), 'games': 0, 'newest': 0,
                            'current': False, 'new': True})
            elif f[0] in ('nocard', 'notgenuine'):
                return None
        return out

    def menu_items(self, prof):
        """The Super MemCard page for this profile's menu ([] when it has none)."""
        sys_name = system_of(prof)
        if sys_name in no_picker() or not self.handles(prof):
            return []
        try:
            cards = self.cached_cards(sys_name)
        except OSError:
            return []
        if not cards:
            return []
        values = [str(c['card']) + (_NEW if c['new'] else '') for c in cards]
        current = next((v for v, c in zip(values, cards) if c['current']), values[0])
        return [{'key': CARD_KEY, 'page': PAGE, 'name': 'Card', 'values': values,
                 'value': current, 'default': current, 'from': 'supermemcard',
                 'per_game': False, 'per_display': False, 'extra': 'supermemcard',
                 'cards': cards, 'system': sys_name}]

    def pick(self, prof, value):
        """The Card entry changed: that system plays from that card from now on."""
        sys_name = system_of(prof)
        if sys_name in no_picker():
            raise ValueError('%s keeps its own cards' % sys_name)
        n = int(str(value).replace(_NEW, '').strip())
        # switching moves this SuperStation's saves of that system over: can take a while
        reply = ' '.join(_ask('CARD %s\t%d' % (sys_name, n), self.sock, timeout=180.0))
        if not reply.startswith('card ok'):
            raise RuntimeError('Super MemCard: ' + (reply or 'no answer'))
        t, cards = self._cards.get(sys_name, (0, None))
        if cards:                            # the cache follows the pick at once
            for c in cards:
                c['current'] = c['card'] == n
                if c['card'] == n and c['new']:
                    c['new'] = False
            self._cards[sys_name] = (time.time(), cards)
        if self.background:
            self.warm(first=sys_name)        # (the next blank card, the games on the new one)
        return n


def main_has_shim(path):
    """Does this Main binary carry the Super MemCard shim (it tells smcd itself)?"""
    try:
        with open(path, 'rb') as f:
            return b'/tmp/smcd.sock' in f.read()
    except OSError:
        return False


def running_main(root='/media/fat'):
    """The Main binary running now."""
    for d in os.listdir('/proc'):
        if d.isdigit():
            try:
                exe = os.readlink('/proc/%s/exe' % d)
            except OSError:
                continue
            b = os.path.basename(exe).lower()
            if exe.startswith(root + '/') and (b.startswith('mister') or b.startswith('corehost')):
                return exe       # (corehost: Coreshell's own Main, core_shell/corehost)
    return None
