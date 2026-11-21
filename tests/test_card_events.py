"""Step 4: the memory manager's Play and Main's card events go through Coreshell to smcd (a fake smcd here)."""
import os, socket, tempfile, threading, unittest
from coreshell import launcher as ln, server as sv, supermemcard as smc


class FakeSmcd(object):
    def __init__(self, path, answers):
        self.path, self.answers, self.got = path, answers, []
        self.s = socket.socket(socket.AF_UNIX)
        self.s.bind(path)
        self.s.listen(4)
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            f = c.makefile('rw')
            for line in f:
                line = line.rstrip('\n')
                self.got.append(line)
                if line.startswith('PORT '):
                    f.write('port %s\nEND\n' % line[5:]); f.flush(); continue
                f.write(self.answers(line)); f.flush()
                break
            c.close()


class Events(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.paths = ln.Paths(t)
        self.fifo = os.path.join(t, 'fifo')
        sv.LAUNCH_LOG = os.path.join(t, 'launch.log')
        self.mgl = os.path.join(t, 'game.mgl')
        open(self.mgl, 'w').write('<mistergamedescription>\n\t<rbf>_Console/Odd</rbf>\n'
                                  '\t<setname same_dir="1">RA_Odd</setname>\n'
                                  '\t<file delay="1" type="f" index="1" path="/g/Tom &amp; Jerry.nes"/>\n'
                                  '</mistergamedescription>\n')

        def answers(line):
            if line.startswith('PLAYPREP NES\t2\t'):
                return 'play ok 2\t%s\nEND\n' % self.mgl
            if line.startswith('PLAYPREP'):
                return 'play notfound\nEND\n'
            if line.startswith('LAUNCH '):
                return 'DONE saves\n'
            if line == 'MENU':
                return 'DONE\n'
            return 'unknown\nEND\n'
        self.sock = os.path.join(t, 'smcd.sock')
        self.smcd = FakeSmcd(self.sock, answers)
        self.cs = sv.Coreshell(self.paths, fifo=self.fifo)
        self.cs.smc = smc.SuperMemCard(self.sock, background=False)

    def tearDown(self):
        self.smcd.s.close()
        self.tmp.cleanup()

    def ask(self, **kw):
        return self.cs.handle(dict(v=sv.API, **kw))

    def test_play_save_launches(self):
        r = self.ask(cmd='play_save', sys='NES', card=2, save='Tom & Jerry', port=1)
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['answer'], 'play ok 2')
        self.assertEqual(self.smcd.got[:2], ['PORT 1', 'PLAYPREP NES\t2\tTom & Jerry'])
        mgl = open(r['plan']['mgl_file']).read()          # (no profile for _Console/Odd here: the bare MGL)
        self.assertIn('<rbf>_Console/Odd</rbf>', mgl)
        self.assertIn('path="/g/Tom &amp; Jerry.nes"', mgl)
        self.assertEqual(open(self.fifo).read(), 'load_core %s\n' % r['plan']['mgl_file'])

    def test_play_save_notfound(self):
        r = self.ask(cmd='play_save', sys='NES', card=3, save='Nope')
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['answer'], 'play notfound')
        self.assertFalse(os.path.exists(self.fifo))

    def test_events(self):
        r = self.ask(cmd='card_event', line='LAUNCH _Console/NES_20260101.rbf\t/x.mgl')
        self.assertEqual(r['reply'], 'DONE saves')
        r = self.ask(cmd='card_event', line='MENU')
        self.assertEqual(r['reply'], 'DONE')
        log = open(sv.LAUNCH_LOG).read()
        self.assertIn('card in', log)
        self.assertIn('card out', log)
        self.assertFalse(self.ask(cmd='card_event', line='CLEAR NES\t1')['ok'])   # only Main's events


class Mgl(unittest.TestCase):
    def test_parse(self):
        with tempfile.NamedTemporaryFile('w', suffix='.mgl', delete=False) as f:
            f.write('<mistergamedescription><rbf>_Console/SNES</rbf><file delay="2" type="s" index="0" '
                    'path="/a/b &quot;c&quot;.sfc"/></mistergamedescription>')
        try:
            self.assertEqual(sv._mgl_game(f.name), ('_Console/SNES', None, '/a/b "c".sfc',
                                                    {'delay': 2, 'type': 's', 'index': 0}))
        finally:
            os.unlink(f.name)


if __name__ == '__main__':
    unittest.main()
