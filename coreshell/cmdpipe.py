"""Coreshell's own /dev/MiSTer_cmd: the commands add-ons send Main (Zaparoo, MiSTer SAM, MiSTer Extensions' Remote,
search and random, MiSTer Companion, MiSTer Monitor...) come to Coreshell first.

Main reads its commands from a FIFO, /dev/MiSTer_cmd, which it makes when it starts (and again each time it starts a
core). Coreshell gives that FIFO a second name, /dev/MiSTer_cmd_main (Main keeps reading it: it's the same FIFO), and
puts its own FIFO in its place, in one rename: there is never a moment with no /dev/MiSTer_cmd (a shell's
`echo ... > /dev/MiSTer_cmd` would make a plain file). Coreshell's own launches write /dev/MiSTer_cmd_main.

What an add-on sends:
- load_core <a game's .mgl / an .mra / a core's .rbf>: launched by Coreshell (cmd_launch), with the game's settings,
  RetroAchievements and Super MemCard, like a launch from the frontend; anything Coreshell can't launch
  goes to Main as it was sent.
- everything else (load_core of the menu, volume, screenshot, fb_cmd...): to Main as it was sent.

When Coreshell stops, Main's FIFO gets its own name back (Main never stops hearing commands).
"""
import ctypes
import ctypes.util
import errno
import os
import stat
import struct
import threading
import time

CMD = '/dev/MiSTer_cmd'
MAIN = '/dev/MiSTer_cmd_main'
MARK = '/tmp/coreshell.cmdpipe'   # the inode of Coreshell's FIFO: a Coreshell started again knows it's not Main's


def _fifo_ino(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_ino if stat.S_ISFIFO(st.st_mode) else None


def _opened(ino, dev=None):
    """True when some process has the FIFO <ino> open (Main reads its FIFO from the moment it opens it)."""
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        d = '/proc/%s/fd' % pid
        try:
            fds = os.listdir(d)
        except OSError:
            continue
        for fd in fds:
            try:
                st = os.stat(os.path.join(d, fd))
            except OSError:
                continue
            if st.st_ino == ino and stat.S_ISFIFO(st.st_mode) and (dev is None or st.st_dev == dev):
                return True
    return False


def write_main(path, line, timeout=3.0, log=None):
    """One command to Main's FIFO: never waits on a FIFO nobody reads (a blocking open would hold Coreshell's launch
    lock for good). Raises OSError when Main hasn't read it within <timeout>."""
    if _fifo_ino(path) is None:              # (not a FIFO: written as before, as a plain open would)
        with open(path, 'a') as f:
            f.write(line.rstrip('\n') + '\n')
        return
    end = time.time() + timeout
    while True:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            break
        except OSError as e:
            if e.errno != errno.ENXIO or time.time() > end:   # (ENXIO: nobody has it open to read)
                raise OSError(e.errno, "Main isn't reading %s (%s)" % (path, e.strerror))
            time.sleep(0.05)
    try:
        data = (line.rstrip('\n') + '\n').encode('utf-8')
        while data:
            try:
                n = os.write(fd, data)
                data = data[n:]
            except OSError as e:
                if e.errno != errno.EAGAIN or time.time() > end:
                    raise
                time.sleep(0.05)
    finally:
        os.close(fd)


class _Inotify(object):
    """inotify on a folder (libc, through ctypes); none: wait() just sleeps (the pipe is checked by polling)."""
    IN_CREATE, IN_MOVED_TO, IN_DELETE, IN_MOVED_FROM, IN_OPEN = 0x100, 0x80, 0x200, 0x40, 0x20

    def __init__(self, folder, mask=None):
        self.fd = -1
        try:
            libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6', use_errno=True)
            fd = libc.inotify_init1(os.O_CLOEXEC)
            if mask is None:
                mask = self.IN_CREATE | self.IN_MOVED_TO | self.IN_DELETE | self.IN_MOVED_FROM
            if fd >= 0 and libc.inotify_add_watch(fd, folder.encode(), mask) >= 0:
                self.fd = fd
            elif fd >= 0:
                os.close(fd)
        except (OSError, AttributeError):
            self.fd = -1

    def seen(self, prefixes):
        """Without waiting: whether a name starting with one of prefixes had an event since the last look (every event
        waiting is read). None when there's no watch (the caller looks for itself, as before)."""
        if self.fd < 0:
            return None
        import select
        hit = False
        while select.select([self.fd], [], [], 0)[0]:
            buf = os.read(self.fd, 4096)
            i = 0
            while i + 16 <= len(buf):
                _wd, mask, _cookie, ln = struct.unpack_from('iIII', buf, i)
                name = buf[i + 16:i + 16 + ln].split(b'\0', 1)[0].decode(errors='replace')
                i += 16 + ln
                if mask & 0x4000 or any(name.startswith(x) for x in prefixes):   # (IN_Q_OVERFLOW: anything)
                    hit = True
        return hit

    def wait(self, names, timeout):
        """Until one of names changes in the folder, or timeout seconds. Every event waiting is read (timeout 0: just
        that, the queue drained), so a select() on fd doesn't find it readable again for the same events."""
        if self.fd < 0:
            time.sleep(timeout)
            return
        import select
        end = time.time() + timeout
        while True:
            left = max(0.0, end - time.time())
            r, _, _ = select.select([self.fd], [], [], left)
            if not r:
                return
            buf = os.read(self.fd, 4096)
            i = 0
            while i + 16 <= len(buf):
                _wd, _mask, _cookie, ln = struct.unpack_from('iIII', buf, i)
                name = buf[i + 16:i + 16 + ln].split(b'\0', 1)[0].decode(errors='replace')
                i += 16 + ln
                if name in names:
                    return


class CmdPipe(object):
    """The FIFO add-ons write; handler(line) is called for each command line (from this object's thread)."""

    def __init__(self, handler, cmd=CMD, main=MAIN, log=None, mark=MARK):
        self.cmd, self.main, self.handler, self.mark = cmd, main, handler, mark
        self.tmp = os.path.join(os.path.dirname(cmd), '.' + os.path.basename(cmd) + '_coreshell')
        self.log = log or (lambda *a: None)
        self.ours = None            # the inode of Coreshell's FIFO
        self.rfd = -1
        self.stop_ev = threading.Event()
        self.thread = None
        self.taken = 0              # times Main's FIFO was taken over
        self._wait_open = None      # since when Main's new FIFO waits for Main to open it

    # ------------------------------------------------------------------ where Coreshell's launches write
    def main_path(self):
        """Main's FIFO: its second name while Coreshell has /dev/MiSTer_cmd, else /dev/MiSTer_cmd."""
        return self.main if _fifo_ino(self.main) is not None and self.ours is not None else self.cmd

    # ------------------------------------------------------------------ taking over
    def _take(self):
        """/dev/MiSTer_cmd is Main's (it made a new one): it becomes /dev/MiSTer_cmd_main, and Coreshell's goes in
        its place. True when Coreshell has /dev/MiSTer_cmd afterwards."""
        cur = _fifo_ino(self.cmd)
        if cur is not None and cur == self.ours:
            return True
        if cur is None and os.path.lexists(self.cmd):
            self.log('cmdpipe: %s is not a FIFO: left alone' % self.cmd)
            return False
        try:
            before = int(open(self.mark).read().strip() or 0)
        except (OSError, ValueError):
            before = 0
        if cur is not None and cur == before and _fifo_ino(self.main) is not None:
            # the FIFO a Coreshell before this one put there (it stopped without giving it back): taken as it is,
            # so a command an add-on is writing now is read
            if self.rfd >= 0:
                os.close(self.rfd)
            self.rfd = os.open(self.cmd, os.O_RDWR | os.O_NONBLOCK)
            self.ours = cur
            self.log('cmdpipe: %s is Coreshell\'s again (Main reads %s)' % (self.cmd, self.main))
            return True
        if cur is not None and not _opened(cur):
            # Main makes its FIFO, then opens it (input.cpp: unlink, mkfifo, open): renamed in between, Main would
            # open Coreshell's FIFO by the name and its own would have no reader (a launch written there went nowhere)
            if self._wait_open is None:
                self._wait_open = time.time()
            if time.time() - self._wait_open < 5:
                return False
            self.log('cmdpipe: %s: nobody opened it in 5 s: taken over anyway' % self.cmd)
        self._wait_open = None
        if cur is not None:
            # Main's: its second name first (replacing an old one), so writing Main never fails
            tmp_main = self.main + '.new'
            try:
                if os.path.lexists(tmp_main):
                    os.unlink(tmp_main)
                os.link(self.cmd, tmp_main)
                os.rename(tmp_main, self.main)
            except OSError as e:
                self.log('cmdpipe: %s not taken over (%s)' % (self.cmd, e))
                return False
        # Coreshell's FIFO, made under another name and put in place in one rename
        if self.ours is None or _fifo_ino(self.tmp) is None:
            if os.path.lexists(self.tmp):
                os.unlink(self.tmp)
            os.mkfifo(self.tmp, 0o622)
            os.chmod(self.tmp, 0o622)
        ino = os.stat(self.tmp).st_ino
        if self.rfd >= 0:
            os.close(self.rfd)
        # O_RDWR: the read end never sees end-of-file when a writer closes (and opening doesn't wait for one)
        self.rfd = os.open(self.tmp, os.O_RDWR | os.O_NONBLOCK)
        with open(self.mark, 'w') as f:
            f.write('%d\n' % ino)
        os.rename(self.tmp, self.cmd)
        self.ours = ino
        self.taken += 1
        self.log('cmdpipe: %s is Coreshell\'s (Main reads %s)' % (self.cmd, self.main))
        return True

    def _give_back(self):
        """Main's FIFO gets /dev/MiSTer_cmd back (Coreshell stops)."""
        if _fifo_ino(self.main) is not None and (self.ours is None or _fifo_ino(self.cmd) in (None, self.ours)):
            try:
                os.rename(self.main, self.cmd)
            except OSError as e:
                self.log('cmdpipe: %s not given back (%s)' % (self.cmd, e))
        if self.rfd >= 0:
            os.close(self.rfd)
            self.rfd = -1
        self.ours = None
        try:
            os.unlink(self.mark)
        except OSError:
            pass

    # ------------------------------------------------------------------ the loop
    def start(self):
        self.thread = threading.Thread(target=self._run, name='cmdpipe', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()
        if self.thread:
            self.thread.join(2)
        self._give_back()

    def _run(self):
        import select
        watch = _Inotify(os.path.dirname(self.cmd))
        names = {os.path.basename(self.cmd)}
        buf = b''
        while not self.stop_ev.is_set():
            if _fifo_ino(self.cmd) != self.ours or self.rfd < 0:
                if _fifo_ino(self.cmd) is None and not os.path.lexists(self.cmd):
                    watch.wait(names, 0.5)          # (no Main yet: it makes the FIFO when it starts)
                    continue
                try:
                    if not self._take():
                        time.sleep(0.05)            # (Main opening its new FIFO)
                        continue
                except OSError as e:
                    self.log('cmdpipe: %s' % e)
                    watch.wait(names, 1)
                    continue
            fds = [self.rfd] + ([watch.fd] if watch.fd >= 0 else [])
            try:
                r, _, _ = select.select(fds, [], [], 0.5 if watch.fd >= 0 else 0.2)
            except (OSError, ValueError):
                r = []
            if watch.fd >= 0 and watch.fd in r:
                watch.wait(names, 0)                # (drain: the loop checks the FIFO's name again)
            if self.rfd in r:
                try:
                    chunk = os.read(self.rfd, 4096)
                except OSError as e:
                    chunk = b''
                    if e.errno not in (errno.EAGAIN, errno.EINTR):
                        # (raising ended this thread: every add-on's command was lost until a restart) the FIFO
                        # is taken over again on the next turn of the loop
                        self.log('cmdpipe: reading %s: %s' % (self.cmd, e))
                        try:
                            os.close(self.rfd)
                        except OSError:
                            pass
                        self.rfd = -1
                        self.ours = None
                        watch.wait(names, 0.5)
                        continue
                # As Main reads its FIFO (input.cpp: one read() is one command, its newline optional): lines, and what's
                # left after the last newline is a command too. Update All's chip-ID linker writes "load_core <rbf>"
                # with no newline; waiting for one glued its next commands onto it.
                buf += chunk
                parts = buf.split(b'\n')
                buf = b''
                for line in parts:
                    line = line.decode('utf-8', errors='replace').strip()
                    if line:
                        try:
                            self.handler(line)
                        except Exception as e:      # (one bad command never stops the pipe)
                            self.log('cmdpipe: %r: %s' % (line, e))
                            self.forward(line)

    # ------------------------------------------------------------------ to Main
    def forward(self, line):
        """A command to Main as it was sent (a few seconds at most if Main isn't reading)."""
        path = self.main_path()
        try:                        # (a Main replaced while it runs: any core it loads restarts it: mains.py)
            from .mains import bridge_replaced
            bridge_replaced(say=self.log)
        except Exception as e:
            self.log('cmdpipe: %s' % e)
        if _fifo_ino(path) is None:
            self.log('cmdpipe: no Main to pass %r to' % line)
            return False
        end = time.time() + 3
        while True:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError as e:
                if e.errno != errno.ENXIO or time.time() > end:   # (ENXIO: no reader yet)
                    self.log('cmdpipe: %r not passed to Main (%s)' % (line, e))
                    return False
                time.sleep(0.05)
        try:
            os.write(fd, (line + '\n').encode('utf-8'))
        except OSError as e:        # (Main's FIFO full: EAGAIN) - logged, never raised into the pipe's thread
            self.log('cmdpipe: %r not passed to Main (%s)' % (line, e))
            return False
        finally:
            os.close(fd)
        return True
