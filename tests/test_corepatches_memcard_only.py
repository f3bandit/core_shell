"""Core patches: Super MemCard's only, and only when the user turned them on."""
import os
import tempfile
import unittest
from unittest import mock

from coreshell import corepatches as cp


class Paths(object):
    def __init__(self, root):
        self.root = root


def _patch(path, stock_md5, result_md5):
    with open(path, 'wb') as f:
        f.write(b'CSPATCH1' + bytes.fromhex(stock_md5) + bytes.fromhex(result_md5))


class MemcardOnly(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.paths = Paths(self.d)
        os.makedirs(os.path.join(self.d, cp.PATCHES))
        os.makedirs(os.path.join(self.d, 'coreshell/settings'))
        cp._index.clear()

    def test_only_memcard_patches_indexed(self):
        _patch(os.path.join(self.d, cp.PATCHES, 'NES_20260823.frontpad.v2.cspatch'), '11' * 16, '22' * 16)
        _patch(os.path.join(self.d, cp.PATCHES, 'PSX_20260807.memcard.v1.cspatch'), '33' * 16, '44' * 16)
        idx = cp.index(self.paths)
        self.assertEqual(list(idx), ['33' * 16])            # (the front-pad patch is never picked up)

    def test_off_by_default(self):
        self.assertEqual(cp.settings(self.paths), {'memcard_patch': False})
        rbf = os.path.join(self.d, 'PSX_20260807.rbf')
        open(rbf, 'wb').close()
        with mock.patch.object(cp, 'index') as idx:
            self.assertIsNone(cp.patched_rbf(self.paths, {'rbf': '_Console/PSX'}, rbf))
            idx.assert_not_called()                          # (off: not even looked for)

    def test_on_when_asked(self):
        with open(os.path.join(self.d, cp.SETTINGS), 'w') as f:
            f.write('{"memcard_patch": true}')
        self.assertEqual(cp.settings(self.paths), {'memcard_patch': True})


if __name__ == '__main__':
    unittest.main()
