"""Add-ons' MiSTer_cmd lines (Zaparoo, SAM, MiSTer Extensions...): a game Coreshell launches itself, the rest to Main
as sent; the game announced in /tmp the way their trackers read it."""
import os, tempfile, unittest
from coreshell import launcher as ln, server as sv


class FakePipe(object):
    def __init__(self, main):
        self.sent, self.main = [], main
    def forward(self, line):
        self.sent.append(line)
    def main_path(self):
        return self.main


class Addons(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.paths = ln.Paths(t)
        sv.LAUNCH_LOG = os.path.join(t, 'launch.log')
        sv.STATE_DIR = os.path.join(t, 'state'); os.makedirs(sv.STATE_DIR)
        self.fifo = os.path.join(t, 'main_fifo')
        self.cs = sv.Coreshell(self.paths)
        self.cs.pipe = FakePipe(self.fifo)
        os.makedirs(os.path.join(t, 'games', 'Odd'))
        self.rom = os.path.join(t, 'games', 'Odd', 'A Game.bin')
        open(self.rom, 'w').write('x')

    def tearDown(self):
        self.tmp.cleanup()

    def state(self, name):
        return open(os.path.join(sv.STATE_DIR, name)).read()

    def test_mgl_launched_by_coreshell(self):
        mgl = os.path.join(self.tmp.name, '.LASTLAUNCH.mgl')       # (Zaparoo's and MiSTer Extensions' own)
        open(mgl, 'w').write('<mistergamedescription>\n\t<rbf>_Console/Odd_20260101</rbf>\n'
                             '\t<file delay="2" type="f" index="1" path="%s"/>\n</mistergamedescription>\n' % self.rom)
        self.cs.addon_command('load_core ' + mgl)
        self.assertEqual(self.cs.pipe.sent, [])                    # not passed on: Coreshell launched it
        self.assertIn('load_core ' + self.paths.launch, open(self.fifo).read())
        self.assertIn(self.rom, open(os.path.join(self.paths.launch, os.listdir(self.paths.launch)[0])).read())
        self.assertEqual(self.state('FULLPATH'), self.rom)
        self.assertEqual(self.state('CURRENTPATH'), 'A Game.bin')
        self.assertEqual(self.state('FILESELECT'), 'selected')
        self.assertEqual(self.state('ACTIVEGAME'), self.rom)
        # the menu back: ACTIVEGAME emptied (Coreshell wrote it)
        self.cs.handle(dict(v=sv.API, cmd='card_event', line='MENU'))
        self.assertEqual(self.state('ACTIVEGAME'), '')

    def test_relative_path_found_under_games(self):
        mgl = os.path.join(self.tmp.name, 'x.mgl')
        open(mgl, 'w').write('<mistergamedescription><rbf>_Console/Odd</rbf>'
                             '<file delay="1" type="f" index="0" path="A Game.bin"/></mistergamedescription>')
        self.cs.find_profile = lambda rbf, sn=None: 'odd'
        self.cs.profile = lambda pid: {'games_dir': 'games/Odd'}
        self.assertEqual(self.cs._addon_rom(self.cs.profile('odd'), 'A Game.bin'), self.rom)

    def test_mra(self):
        mra = os.path.join(self.tmp.name, 'Mercs.mra')
        open(mra, 'w').write('<misterromdescription/>')
        self.cs.addon_command('load_core ' + mra)                   # (MiSTer SAM's arcade games)
        self.assertEqual(open(self.fifo).read(), 'load_core %s\n' % mra)
        self.assertEqual(self.state('ACTIVEGAME'), mra)

    def test_passed_as_sent(self):
        for line in ('volume mute', 'screenshot', 'load_core /media/fat/menu.rbf', 'fb_cmd0 rgb32 1'):
            self.cs.addon_command(line)
        self.assertEqual(self.cs.pipe.sent, ['volume mute', 'screenshot', 'load_core /media/fat/menu.rbf',
                                             'fb_cmd0 rgb32 1'])
        self.assertFalse(os.path.exists(self.fifo))

    def test_unreadable_mgl_passed_as_sent(self):
        mgl = os.path.join(self.tmp.name, 'two.mgl')
        open(mgl, 'w').write('<mistergamedescription><rbf>_Computer/Amiga</rbf><file delay="1" type="f" index="0" '
                             'path="a.adf"/><file delay="1" type="f" index="1" path="b.adf"/></mistergamedescription>')
        self.cs.addon_command('load_core ' + mgl)
        self.assertEqual(self.cs.pipe.sent, ['load_core ' + mgl])

    def test_failed_launch_passed_as_sent(self):
        self.cs.addon_command('load_core /nowhere/Gone.mra')
        self.assertEqual(self.cs.pipe.sent, ['load_core /nowhere/Gone.mra'])

    def test_screenshot_in_menu_is_the_frontends(self):
        import socket, threading
        sock = os.path.join(self.tmp.name, 'fe.sock')
        srv = socket.socket(socket.AF_UNIX); srv.bind(sock); srv.listen(1)
        got = []
        def serve():
            c, _ = srv.accept(); got.append(c.recv(100).decode()); c.sendall(b'{"ok":true}\n'); c.close()
        t = threading.Thread(target=serve); t.start()
        r = self.cs.handle(dict(v=sv.API, cmd='frontend', screenshot={'socket': sock, 'send': '{"cmd":"shot"}'}))
        self.assertTrue(r['ok'], r)
        open(os.path.join(sv.STATE_DIR, 'CORENAME'), 'w').write('MENU')
        self.cs.addon_command('screenshot')
        t.join(3)
        self.assertEqual(got, ['{"cmd":"shot"}\n'])
        self.assertEqual(self.cs.pipe.sent, [])
        # in a game: Main's
        open(os.path.join(sv.STATE_DIR, 'CORENAME'), 'w').write('SNES')
        self.cs.addon_command('screenshot')
        self.assertEqual(self.cs.pipe.sent, ['screenshot'])
        # taken away: Main's again, menu or not
        self.cs.handle(dict(v=sv.API, cmd='frontend', screenshot=None))
        open(os.path.join(sv.STATE_DIR, 'CORENAME'), 'w').write('MENU')
        self.cs.addon_command('screenshot')
        self.assertEqual(self.cs.pipe.sent, ['screenshot', 'screenshot'])
        srv.close()


if __name__ == '__main__':
    unittest.main()
