"""cmdpipe: Coreshell has /dev/MiSTer_cmd, Main keeps reading its own FIFO (a fake Main here, in a temp folder)."""
import os, sys, tempfile, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from coreshell import cmdpipe as cp

class FakeMain(object):
    """Main's way (input.cpp): unlink + mkfifo + open O_RDWR, then read lines."""
    def __init__(self, path):
        self.path, self.got, self.fd = path, [], -1
        self.start()
    def start(self):
        if os.path.lexists(self.path):
            os.unlink(self.path)
        os.mkfifo(self.path, 0o666)
        self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
    def poll(self):
        try:
            d = os.read(self.fd, 4096)
        except BlockingIOError:
            return
        self.got += [l for l in d.decode().split('\n') if l]

def write(path, line):
    fd = os.open(path, os.O_WRONLY)      # (as a shell's echo: waits for a reader)
    os.write(fd, (line + '\n').encode()); os.close(fd)

def wait_for(cond, t=3):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()

d = tempfile.mkdtemp()
cmd, main, mark = os.path.join(d, 'MiSTer_cmd'), os.path.join(d, 'MiSTer_cmd_main'), os.path.join(d, 'mark')
m = FakeMain(cmd)
seen = []
pipe = None
def handler(line):
    seen.append(line)
    if line.startswith('pass '):
        pipe.forward(line)
pipe = cp.CmdPipe(handler, cmd=cmd, main=main, mark=mark)
pipe.start()
ok = True
def check(name, c):
    global ok
    print(('ok   ' if c else 'FAIL ') + name); ok &= bool(c)

check('taken over: Main reads its FIFO under the second name', wait_for(lambda: cp._fifo_ino(main) is not None and cp._fifo_ino(cmd) == pipe.ours))
write(cmd, 'load_core /x.mgl'); write(cmd, 'pass volume 5')
check('an add-on\'s lines come to Coreshell', wait_for(lambda: seen[-2:] == ['load_core /x.mgl', 'pass volume 5']))
wait_for(lambda: (m.poll(), m.got)[1] == ['pass volume 5'])
check('a line passed on reaches Main', m.got == ['pass volume 5'])
check('Coreshell\'s own launches write Main\'s FIFO', pipe.main_path() == main)
# Update All's chip-ID linker writes its commands with no newline (Main takes one read() as one command)
def write_raw(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK); os.write(fd, data.encode()); os.close(fd)
write_raw(cmd, 'load_core /u/Linker.rbf'); time.sleep(0.3); write_raw(cmd, 'load_core menu.rbf')
check('a command with no newline is one command, not glued to the next',
      wait_for(lambda: seen[-2:] == ['load_core /u/Linker.rbf', 'load_core menu.rbf']))
# Main starts a core: a new FIFO of its own at /dev/MiSTer_cmd
old_fd = m.fd; m.start()
check('Main\'s new FIFO taken over too', wait_for(lambda: cp._fifo_ino(cmd) == pipe.ours and os.stat(main).st_ino == os.fstat(m.fd).st_ino))
os.close(old_fd)
write(cmd, 'pass screenshot')
wait_for(lambda: (m.poll(), 'pass screenshot' in m.got)[1])
check('  and lines still reach the new Main', 'pass screenshot' in m.got)
# Coreshell stops without giving the FIFO back (killed): the next one takes the same FIFO, Main's stays Main's
pipe.stop_ev.set(); pipe.thread.join(2)
ino_before = pipe.ours
t = threading.Thread(target=write, args=(cmd, 'load_core /while-down.mgl')); t.start()   # (waits for a reader)
time.sleep(0.3)
seen2 = []
pipe2 = cp.CmdPipe(lambda l: seen2.append(l), cmd=cmd, main=main, mark=mark)
pipe2.start()
t.join(3)
check('a Coreshell started again takes the same FIFO (an add-on writing meanwhile is read)',
      wait_for(lambda: seen2 == ['load_core /while-down.mgl']) and pipe2.ours == ino_before)
check('  Main\'s FIFO still Main\'s', os.stat(main).st_ino == os.fstat(m.fd).st_ino)
pipe2.stop()
check('stopped: /dev/MiSTer_cmd is Main\'s again', cp._fifo_ino(cmd) == os.fstat(m.fd).st_ino and not os.path.exists(main))
print('ALL PASSED' if ok else 'FAILED')
sys.exit(0 if ok else 1)
