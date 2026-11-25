"""mains.py: the shim build of a Main put where Main starts itself from; the stock build kept and put back."""
import json, os, tempfile, unittest
from coreshell import mains as mn


class MainsFiles(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = self.d.name
        self.home = os.path.join(self.root, 'coreshell')
        os.makedirs(os.path.join(self.home, 'mains'))
        self.stock = b'\x7fELF stock Main 20261010'
        self.shim = b'\x7fELF Main 20261010 with /tmp/smcd.sock'
        self.put(os.path.join(self.root, 'MiSTer'), self.stock)
        self.put(os.path.join(self.home, 'mains', 'MiSTer_20261010_shim'), self.shim)
        self.sum = mn.md5(os.path.join(self.root, 'MiSTer'))
        self.manifest({self.sum: {'name': 'MiSTer', 'shim': 'MiSTer_20261010_shim', 'release': '20261010'}})
        self.events = []
        self.m = mn.Mains(self.root, self.home, emit=lambda t, **k: self.events.append(dict(k, type=t)))

    def tearDown(self):
        self.d.cleanup()

    def put(self, p, b):
        with open(p, 'wb') as f:
            f.write(b)

    def manifest(self, m):
        with open(os.path.join(self.home, 'mains', 'mains.json'), 'w') as f:
            json.dump(m, f)

    def main(self):
        return open(os.path.join(self.root, 'MiSTer'), 'rb').read()

    def test_shim_build_put_in_place_and_restored(self):
        self.assertEqual(self.m.check(), [os.path.join(self.root, 'MiSTer')])
        self.assertEqual(self.main(), self.shim)
        kept = os.path.join(self.home, 'mains', 'stock', 'MiSTer_' + self.sum)
        self.assertEqual(open(kept, 'rb').read(), self.stock)
        self.assertEqual(self.events[-1]['shim'], True)
        self.assertEqual(self.m.check(force=True), [])          # (has the shim: nothing to do)
        self.assertEqual(self.m.restore(), [os.path.join(self.root, 'MiSTer')])
        self.assertEqual(self.main(), self.stock)

    def test_restore_stays_until_check(self):
        self.m.check()
        self.m.restore()
        os.utime(os.path.join(self.root, 'MiSTer'), (2, 2))     # (the watcher would look at it again)
        self.assertEqual(self.m.check(), [])                    # the watcher leaves the stock build alone
        self.assertEqual(self.main(), self.stock)
        self.assertEqual(self.m.check(force=True), [os.path.join(self.root, 'MiSTer')])   # `mains check`
        self.assertEqual(self.main(), self.shim)
        os.utime(os.path.join(self.root, 'MiSTer'), (3, 3))
        self.put(os.path.join(self.root, 'MiSTer'), self.stock)   # (and the watcher is back to normal)
        self.assertEqual(self.m.check(), [os.path.join(self.root, 'MiSTer')])

    def test_update_puts_stock_back_then_shim_again(self):
        self.m.check()
        self.put(os.path.join(self.root, 'MiSTer'), self.stock)   # (Update All: the stock build again)
        os.utime(os.path.join(self.root, 'MiSTer'), (1, 1))
        self.assertEqual(self.m.check(), [os.path.join(self.root, 'MiSTer')])
        self.assertEqual(self.main(), self.shim)

    def test_unknown_stock_left_alone_and_told_once(self):
        self.put(os.path.join(self.root, 'MiSTer'), b'\x7fELF stock Main 20261120 (new)')
        self.assertEqual(self.m.check(), [])
        self.m.check(force=True)
        self.assertEqual(self.main(), b'\x7fELF stock Main 20261120 (new)')
        self.assertEqual([e['shim'] for e in self.events], [False])

    def test_main_in_a_folder(self):                       # (Zaparoo's frontend: zaparoo/MiSTer_Zaparoo)
        os.makedirs(os.path.join(self.root, 'zaparoo'))
        self.put(os.path.join(self.root, 'zaparoo', 'MiSTer_Zaparoo'), b'\x7fELF zaparoo stock')
        self.put(os.path.join(self.home, 'mains', 'zap_shim'), b'\x7fELF zaparoo /tmp/smcd.sock')
        zs = mn.md5(os.path.join(self.root, 'zaparoo', 'MiSTer_Zaparoo'))
        man = json.load(open(os.path.join(self.home, 'mains', 'mains.json')))
        man[zs] = {'name': 'zaparoo/MiSTer_Zaparoo', 'shim': 'zap_shim'}
        man['x'] = {'name': '../escape', 'shim': 'zap_shim'}
        self.manifest(man)
        self.assertIn(os.path.join(self.root, 'zaparoo', 'MiSTer_Zaparoo'), self.m.check())
        self.assertTrue(os.path.isfile(os.path.join(self.home, 'mains', 'stock', 'zaparoo_MiSTer_Zaparoo_' + zs)))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.root), 'escape')))
        self.assertEqual(sorted(self.m.restore()), sorted([os.path.join(self.root, 'MiSTer'),
                                                           os.path.join(self.root, 'zaparoo', 'MiSTer_Zaparoo')]))


if __name__ == '__main__':
    unittest.main()


def test_bridge_replaced_main():
    """A Main whose file was replaced while it runs: its restart path ("<file> (deleted)") gets a script that starts
    the file as it is now and removes itself; a script no running Main needs is removed; real files never touched."""
    import subprocess
    import tempfile
    from coreshell.mains import bridge_replaced, BRIDGE_MARK
    root = tempfile.mkdtemp()
    real = os.path.join(root, 'MiSTer_RA')
    with open(real, 'w') as f:
        f.write('#!/bin/sh\necho "new build $*"\n')
    os.chmod(real, 0o755)
    gone = real + ' (deleted)'
    made = bridge_replaced(root, exes=[gone, '/usr/bin/python3', os.path.join(root, 'MiSTer')])
    assert made == [gone]
    assert BRIDGE_MARK in open(gone).read()
    assert bridge_replaced(root, exes=[gone]) == []          # (already there)
    out = subprocess.run([gone, '/media/fat/_RA_Cores/Cores/NES.rbf', 'a b.mgl'], capture_output=True, text=True).stdout
    assert out == 'new build /media/fat/_RA_Cores/Cores/NES.rbf a b.mgl\n'
    assert not os.path.exists(gone)                          # (used once: gone)
    bridge_replaced(root, exes=[gone])
    bridge_replaced(root, exes=[])                           # (that Main restarted: the script removed)
    assert not os.path.exists(gone)
    with open(gone, 'wb') as f:                              # a real file of that name: never touched
        f.write(b'\x7fELF\x00\xff')
    bridge_replaced(root, exes=[])
    assert os.path.exists(gone)
    os.remove(real)
    assert bridge_replaced(root, exes=[real + ' (deleted)']) == []   # (no new file: nothing to start)


def test_bridge_left_in_a_folder():
    """A script left beside a Main in a folder (phosphor/bin), from before Coreshell restarted: removed once that
    Main runs from its own file again; a real file there never touched."""
    import tempfile
    from coreshell import mains
    from coreshell.mains import bridge_replaced, BRIDGE_MARK
    root = tempfile.mkdtemp()
    d = os.path.join(root, 'phosphor', 'bin')
    os.makedirs(d)
    real = os.path.join(d, 'MiSTer_phosphor')
    open(real, 'w').write('x')
    left = real + ' (deleted)'
    open(left, 'w').write('#!/bin/sh\n%s\nrm -f "$0"\n' % BRIDGE_MARK)
    mains._bridges.discard(left)                             # (Coreshell restarted: it doesn't know the script)
    bridge_replaced(root, exes=[left])                       # still needed while that Main runs from the old file
    assert os.path.exists(left)
    bridge_replaced(root, exes=[real])                       # that Main runs from its file again
    assert not os.path.exists(left)
    other = os.path.join(d, 'MiSTer_x (deleted)')
    open(other, 'wb').write(b'\x7fELF\x00\xff')
    bridge_replaced(root, exes=[real])
    assert os.path.exists(other)
