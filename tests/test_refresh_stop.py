"""The frontend stops Coreshell reading updated cores (B on its card): the core being read goes, the menu comes back.
A stop for a game's launch leaves the core to the launch."""
import os, subprocess, sys, tempfile, unittest
from coreshell import launcher as ln, server as sv


class Stop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = ln.Paths(self.tmp.name)
        self.fifo = os.path.join(self.tmp.name, 'fifo')
        self.corename = os.path.join(self.tmp.name, 'CORENAME')
        self.cs = sv.Coreshell(self.paths, fifo=self.fifo)
        self.cs.refresher.corename = self.corename

    def tearDown(self):
        p = self.cs.refresher.proc
        if p and p.poll() is None:
            p.kill()
        self.tmp.cleanup()

    def reading(self, core):
        with open(self.corename, 'w') as f:
            f.write(core)
        self.cs.refresher.proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])

    def stop(self):
        return self.cs.handle({'v': sv.API, 'cmd': 'refresh', 'cancel': True})

    def test_menu_back(self):
        self.reading('MTX512')
        self.assertTrue(self.stop()['ok'])
        self.assertEqual(open(self.fifo).read(), 'load_core menu.rbf\n')
        self.assertIsNotNone(self.cs.refresher.proc is None or self.cs.refresher.proc.poll())

    def test_already_in_menu(self):
        self.reading('MENU')
        self.stop()
        self.assertFalse(os.path.exists(self.fifo))          # (nothing sent: the menu is up)

    def test_nothing_running(self):
        with open(self.corename, 'w') as f:
            f.write('MTX512')
        self.stop()
        self.assertFalse(os.path.exists(self.fifo))          # (no dumper stopped: a core the user chose stays)


if __name__ == '__main__':
    unittest.main()
