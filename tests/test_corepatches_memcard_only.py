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



class AnyPad(unittest.TestCase):
    """The PSX patch (f3bandit, Nov 27 2026): the core's own options mean what they say. SNAC MemCard = Real with a Pad
    that isn't SNAC-portN or Off: the patched core, nothing else set; the user's own options sent as they are."""

    def setUp(self):
        import json
        from coreshell import launcher as ln
        self.ln = ln
        here = os.path.dirname(os.path.abspath(__file__))
        self.prof = json.load(open(os.path.join(here, '..', 'systems', 'psx.json')))
        self.d = tempfile.mkdtemp()
        self.paths = Paths(self.d)
        self.paths.config = os.path.join(self.d, 'config')
        os.makedirs(self.paths.config)
        self.rbf = os.path.join(self.d, 'PSX_20260807.rbf')
        with open(self.rbf, 'wb') as f:
            f.write(b'stock core')
        self.patches = mock.patch.multiple(ln, installed_rbf=mock.Mock(return_value=self.rbf),
                                           is_exact=mock.Mock(return_value=True),
                                           handoff_main=mock.Mock(return_value=True),
                                           ini_main=mock.Mock(return_value='MiSTer'),
                                           hybrid_main=mock.Mock(return_value=None),
                                           ini_core_main=mock.Mock(return_value=None))
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def plan(self, user, cfg=None, available=True):
        if cfg is not None:
            with open(os.path.join(self.paths.config, self.prof['config_file']), 'wb') as f:
                f.write(self.ln.status_bytes(self.prof, cfg))
        with mock.patch.object(cp, 'available', return_value=available):
            return self.ln.plan(self.paths, self.prof, None, game_vals={}, system_vals=user)

    def bits(self, plan, lo, hi):
        v = int.from_bytes(self.ln.handoff_status(self.prof, plan['values'], plan['set'])[1], 'little')
        return (v >> lo) & ((1 << (hi - lo + 1)) - 1)

    def test_needed(self):
        self.assertTrue(cp.needed({'SNAC MemCard': 'Real', 'Pad1': 'NeGcon', 'Pad2': 'Off'}))
        self.assertTrue(cp.needed({'SNAC MemCard': 'Real'}))                       # (Dualshock, the default)
        self.assertFalse(cp.needed({'SNAC MemCard': 'Virtual', 'Pad1': 'NeGcon'}))
        self.assertFalse(cp.needed({'SNAC MemCard': 'Real', 'Pad1': 'SNAC-port1', 'Pad2': 'Off'}))   # stock does it
        self.assertFalse(cp.needed({'SNAC MemCard': 'Real', 'Pad1': 'Off', 'Pad2': 'SNAC-port2'}))

    def test_real_with_a_usb_pad(self):
        user = {'Pad1': 'NeGcon', 'SNAC MemCard': 'Real'}
        p = self.plan(user)
        self.assertTrue(p['supercard'])
        self.assertEqual(self.bits(p, 45, 48), 0b0101)    # NeGcon, as the user chose
        self.assertEqual(self.bits(p, 66, 66), 1)         # Real
        self.assertEqual(sorted(p['set']), ['Pad1', 'SNAC MemCard'])   # nothing else set
        self.assertEqual(p['warnings'], [])

    def test_from_the_cores_own_settings(self):
        """Set in the core's own menu (its .CFG), not in a frontend: still the patched core, nothing sent."""
        p = self.plan({}, cfg={'Pad1': 'Analog', 'SNAC MemCard': 'Real'})
        self.assertTrue(p['supercard'])
        self.assertEqual(p['set'], [])
        p = self.plan({'SNAC MemCard': 'Virtual'}, cfg={'Pad1': 'Analog', 'SNAC MemCard': 'Real'})
        self.assertFalse(p['supercard'])                  # (what Coreshell sets over the .CFG wins)

    def test_stock_cases(self):
        self.assertFalse(self.plan({'SNAC MemCard': 'Virtual'})['supercard'])
        self.assertFalse(self.plan({'SNAC MemCard': 'Real', 'Pad1': 'SNAC-port1', 'Pad2': 'Off'})['supercard'])
        self.assertFalse(self.plan({'SNAC MemCard': 'Real', 'Pad1': 'NeGcon'}, available=False)['supercard'])

    def test_any_main(self):
        """No settings to hand over: a Main without the handoff gets the patched core too."""
        with mock.patch.object(self.ln, 'handoff_main', return_value=False):
            p = self.plan({'Pad1': 'Digital', 'SNAC MemCard': 'Real'})
        self.assertTrue(p['supercard'])
        self.assertEqual(p['mode'], 'exact')

    def test_menu_note(self):
        items = [{'key': 'Pad1', 'values': ['Dualshock', 'SNAC-port1'], 'value': 'Dualshock'},
                 {'key': 'SNAC MemCard', 'values': ['Virtual', 'Real'], 'value': 'Virtual'}]
        with mock.patch.object(cp, 'available', return_value=True):
            out = cp.decorate_menu([dict(i) for i in items], self.paths, self.rbf)
        self.assertEqual(out[0]['values'], ['Dualshock', 'SNAC-port1'])   # the core's own list, nothing added
        self.assertIn('any controller type', out[1]['note'])
        with mock.patch.object(cp, 'available', return_value=False):
            out = cp.decorate_menu([dict(i) for i in items], self.paths, self.rbf)
        self.assertNotIn('note', out[1])

    def test_migrate_old_supercard(self):
        old1, old2 = sorted(cp.OLD_KEYS)
        self.assertIsNone(cp.migrate_supercard({'Pad1': 'NeGcon'}))
        self.assertEqual(cp.migrate_supercard({old1: 'On', 'Pad1': 'Digital'}),
                         {'Pad1': 'Digital', 'SNAC MemCard': 'Real'})
        self.assertEqual(cp.migrate_supercard({old2: 'On', 'Pad2': 'SNAC-port2'}),
                         {'Pad2': 'Dualshock', 'SNAC MemCard': 'Real'})   # (the front port's pad: Dualshock)
        self.assertEqual(cp.migrate_supercard({old1: 'Off'}), {})
        # a game's "Off" under a system's "On": that game stays on its virtual card
        self.assertEqual(cp.migrate_supercard({old1: 'Off'}, {old1: 'On'}), {'SNAC MemCard': 'Virtual'})
        self.assertEqual(cp.migrate_supercard({old1: 'On'}, {'Pad1': 'SNAC-port1'}),
                         {'SNAC MemCard': 'Real', 'Pad1': 'Dualshock'})

    def test_patched_rbf_follows_the_choice(self):
        exe = os.path.join(self.d, cp.EXE)
        os.makedirs(os.path.dirname(exe))
        with open(exe, 'w') as f:
            f.write('#!/bin/sh\n')
        os.chmod(exe, 0o755)
        with mock.patch.object(cp, 'index', return_value={}) as idx:
            self.assertIsNone(cp.patched_rbf(self.paths, {'rbf': '_Console/PSX'}, self.rbf, on=False))
            idx.assert_not_called()                       # not needed (and memcard_patch off): not looked for
            cp.patched_rbf(self.paths, {'rbf': '_Console/PSX'}, self.rbf, on=True)
            idx.assert_called()                           # needed: looked for (none here: the stock core)


class Migration(unittest.TestCase):
    """The server turns old SuperCard picks into SNAC MemCard Real once, at start."""

    def test_settings_files(self):
        import json
        from coreshell import server
        d = tempfile.mkdtemp()
        old1 = sorted(cp.OLD_KEYS)[0]
        os.makedirs(os.path.join(d, 'psx'))
        with open(os.path.join(d, 'psx.json'), 'w') as f:
            json.dump({old1: 'On', 'Pad1': 'SNAC-port1'}, f)
        with open(os.path.join(d, 'psx', 'Game.json'), 'w') as f:
            json.dump({old1: 'Off'}, f)
        fake = mock.Mock()
        fake.paths.settings = d
        server.Coreshell._migrate_supercard(fake)
        self.assertEqual(json.load(open(os.path.join(d, 'psx.json'))), {'Pad1': 'Dualshock', 'SNAC MemCard': 'Real'})
        self.assertEqual(json.load(open(os.path.join(d, 'psx', 'Game.json'))), {'SNAC MemCard': 'Virtual'})


if __name__ == '__main__':
    unittest.main()
