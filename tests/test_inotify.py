"""cmdpipe._Inotify: a drain (timeout 0) reads the events waiting, so the fd isn't readable again for them (a select()
loop on it spun at 100% of a core, Nov 2026)."""
import os, select, tempfile, unittest
from coreshell import cmdpipe as cp


class Drain(unittest.TestCase):
    def test_drained(self):
        d = tempfile.mkdtemp()
        w = cp._Inotify(d)
        if w.fd < 0:
            self.skipTest('no inotify')
        open(os.path.join(d, 'other'), 'w').close()
        self.assertTrue(select.select([w.fd], [], [], 1)[0])
        w.wait({'MiSTer_cmd'}, 0)
        self.assertFalse(select.select([w.fd], [], [], 0.1)[0])


if __name__ == '__main__':
    unittest.main()
