"""A core with no profile still starts through Coreshell (no fallback in the frontend): the bare MGL."""
import os, tempfile, unittest
from coreshell import launcher as ln, server as sv


class Bare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = ln.Paths(self.tmp.name)
        self.fifo = os.path.join(self.tmp.name, 'fifo')
        sv.LAUNCH_LOG = os.path.join(self.tmp.name, 'launch.log')
        self.cs = sv.Coreshell(self.paths, fifo=self.fifo)

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, **kw):
        return self.cs.handle(dict(v=sv.API, cmd='launch', **kw))

    def test_game(self):
        r = self.ask(rbf='_Console/Odd_20260101.rbf', setname='RA_Odd', rom='/g/A & B.bin',
                     slot={'delay': 1, 'type': 'f', 'index': 2})
        self.assertTrue(r['ok'], r)
        mgl = open(r['plan']['mgl_file']).read()
        self.assertEqual(mgl, '<mistergamedescription>\n\t<rbf>_Console/Odd_20260101</rbf>\n'
                              '\t<setname same_dir="1">RA_Odd</setname>\n'
                              '\t<file delay="1" type="f" index="2" path="/g/A &amp; B.bin"/>\n'
                              '</mistergamedescription>\n')
        self.assertEqual(open(self.fifo).read(), 'load_core %s\n' % r['plan']['mgl_file'])
        log = open(sv.LAUNCH_LOG).read()
        self.assertIn('no profile', log)
        self.assertIn(' load ', log)

    def test_core_only(self):
        r = self.ask(rbf='_Utility/Thing.rbf')
        self.assertTrue(r['ok'], r)
        self.assertNotIn('<file', open(r['plan']['mgl_file']).read())

    def test_no_slot_fails_and_logs(self):
        r = self.ask(rbf='_Console/Odd.rbf', rom='/g/x.bin')
        self.assertFalse(r['ok'])
        self.assertFalse(os.path.exists(self.fifo))
        self.assertIn('failed', open(sv.LAUNCH_LOG).read())

    def test_named_profile_missing_still_errors(self):
        r = self.ask(profile='Nope', rom='/g/x.bin')
        self.assertFalse(r['ok'])
        self.assertIn('no profile', r['error'])


if __name__ == '__main__':
    unittest.main()


class Arcade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = ln.Paths(self.tmp.name)
        self.fifo = os.path.join(self.tmp.name, 'fifo')
        sv.LAUNCH_LOG = os.path.join(self.tmp.name, 'launch.log')
        self.cs = sv.Coreshell(self.paths, fifo=self.fifo)
        self.mra = os.path.join(self.tmp.name, 'Pac-Man.mra')
        open(self.mra, 'w').write('<misterromdescription/>')

    def tearDown(self):
        self.tmp.cleanup()

    def test_mra(self):
        r = self.cs.handle(dict(v=sv.API, cmd='launch', kind='arcade', rom=self.mra))
        self.assertTrue(r['ok'], r)
        self.assertEqual(open(self.fifo).read(), 'load_core %s\n' % self.mra)
        self.assertIn(' load ', open(sv.LAUNCH_LOG).read())

    def test_missing_mra(self):
        r = self.cs.handle(dict(v=sv.API, cmd='launch', kind='arcade', rom=self.mra + 'x'))
        self.assertFalse(r['ok'])
        self.assertFalse(os.path.exists(self.fifo))
        self.assertIn('failed', open(sv.LAUNCH_LOG).read())
