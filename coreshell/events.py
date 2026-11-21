"""What happens on the console, told to whoever asks: a game started (by the frontend, an add-on, Main's own menu), a
file picked in a core's OSD, the game over, saves synced to the Super MemCard.

Coreshell's own launches and Main's card events (the shim) say so as they happen. Beside them /tmp/CORENAME is
watched, so nothing goes unseen when they don't (a stock Main has no shim; Coreshell may have been restarted): every
Main writes it when a core starts, so a core Coreshell didn't start is a game too, and MENU is the game over.

On Coreshell's socket: {"cmd": "events", "since": <seq>, "wait": <s>} answers the events after seq (waiting up to
wait seconds, 30 at most, for one); {"cmd": "subscribe"} keeps the connection and sends each event as a JSON line as it
happens. Every event: {"seq", "time", "type", ...}. The last 500 are kept, in memory and in
/tmp/coreshell/events.jsonl (for scripts that would rather tail a file).
"""
import collections
import json
import os
import threading
import time

from . import cmdpipe as cp

KEEP = 500
LOG = '/tmp/coreshell/events.jsonl'


class Events(object):
    def __init__(self, log=LOG, tmp='/tmp', say=None):
        self.buf = collections.deque(maxlen=KEEP)
        self.cond = threading.Condition()
        self.seq = 0
        self.log, self.tmp = log, tmp
        self.say = say or (lambda *a: None)
        self.core = None                  # the core now (CORENAME)
        self.game = None                  # the game now, as Coreshell knows it
        self._live = False                # a game or core is running whose end hasn't been told yet
        self.launched_at = 0              # when Coreshell (or a card event) last said a game started
        self.listeners = []               # f(event): each event as it's told (the RA Viewer's refreshes: raviewer.py)
        self._stop = threading.Event()
        self._lines = 0

    # ------------------------------------------------------------------ telling
    def emit(self, type_, **fields):
        with self.cond:
            self.seq += 1
            ev = dict(seq=self.seq, time=round(time.time(), 3), type=type_)
            ev.update((k, v) for k, v in fields.items() if v is not None)
            self.buf.append(ev)
            self.cond.notify_all()
        self._to_file(ev)
        for f in list(self.listeners):
            try:
                f(ev)
            except Exception:             # (a listener never stops the telling)
                pass
        return ev

    def _to_file(self, ev):
        try:
            os.makedirs(os.path.dirname(self.log), exist_ok=True)
            self._lines += 1
            if self._lines > 2 * KEEP:    # (kept short: the last KEEP again)
                with open(self.log + '.new', 'w', encoding='utf-8') as f:
                    for e in list(self.buf):
                        f.write(json.dumps(e, ensure_ascii=False) + '\n')
                os.rename(self.log + '.new', self.log)
                self._lines = len(self.buf)
                return
            with open(self.log, 'a', encoding='utf-8') as f:
                f.write(json.dumps(ev, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def since(self, seq, wait=0):
        """The events after seq; with wait, up to that many seconds for one to come."""
        end = time.time() + max(0, min(float(wait or 0), 30))
        with self.cond:
            while True:
                out = [e for e in self.buf if e['seq'] > seq]
                left = end - time.time()
                if out or left <= 0:
                    return out, self.seq
                self.cond.wait(left)

    # ------------------------------------------------------------------ what Coreshell and Main say
    def game_started(self, game, system=None, core=None, by=None, rbf=None):
        self.game = game
        self._live = True
        self.launched_at = time.time()
        self.emit('game_started', game=game, system=system, core=core, rbf=rbf, by=by)

    def game_over(self, by=None, core=None):
        """The game (or core) ended: told once, whichever saw it first (Main's card event, CORENAME)."""
        core = core or self.core
        if self._live and (self.game is not None or (core and core != 'MENU')):
            self.emit('game_ended', game=self.game, core=core, by=by)
        self._live = False
        self.game = None

    # ------------------------------------------------------------------ the files watched
    def start(self):
        try:
            self.core = open(os.path.join(self.tmp, 'CORENAME')).read().strip() or None
        except OSError:
            self.core = None
        if self.core and self.core != 'MENU':
            # Coreshell started again in the middle of a game: the game it had (ACTIVEGAME, as it wrote it), so the
            # end of it is still told
            self._live = True
            try:
                self.game = open(os.path.join(self.tmp, 'ACTIVEGAME')).read().strip() or None
            except OSError:
                pass
        threading.Thread(target=self._watch, name='events', daemon=True).start()

    def stop(self):
        self._stop.set()

    def _watch(self):
        tmp_watch = cp._Inotify(self.tmp)
        names = {'CORENAME'}
        while not self._stop.is_set():
            tmp_watch.wait(names, 1.0)
            self.check()

    def check(self):
        """CORENAME looked at again (the watcher; tests call it)."""
        try:
            core = open(os.path.join(self.tmp, 'CORENAME')).read().strip() or None
        except OSError:
            core = None
        if core and core != self.core:
            before, self.core = self.core, core
            self.emit('core_changed', core=core, before=before)
            if core != 'MENU':
                self._live = True
            if core == 'MENU':
                self.game_over(by='main', core=before)
            elif time.time() - self.launched_at > 8:
                # a core nobody told Coreshell about (Main's own menu, a stock Main): the game, if Main wrote it
                game = None
                try:
                    fp = os.path.join(self.tmp, 'FULLPATH')
                    if time.time() - os.path.getmtime(fp) < 15:
                        game = open(fp).read().strip() or None
                except OSError:
                    pass
                self.game = game
                self.emit('game_started', game=game, core=core, by='main')
