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



class SuperCard(unittest.TestCase):
    """SuperCard (f3bandit, Nov 14 2026): its own entry; on, the patched PSX core with Pad1 = SNAC-port1 and SNAC MemCard =
    Virtual for that launch only (what the patch reads as "USB pad, real card"); the user's own options untouched."""

    def setUp(self):
        import json
        from coreshell import launcher as ln
        self.ln = ln
        here = os.path.dirname(os.path.abspath(__file__))
        self.prof = json.load(open(os.path.join(here, '..', 'systems', 'psx.json')))
        self.d = tempfile.mkdtemp()
        self.paths = Paths(self.d)
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

    def bits(self, plan, lo, hi):
        v = int.from_bytes(self.ln.handoff_status(self.prof, plan['values'], plan['set'])[1], 'little')
        return (v >> lo) & ((1 << (hi - lo + 1)) - 1)

    def test_on_with_a_patch(self):
        user = {'Pad1': 'Dualshock', 'SNAC MemCard': 'Real', cp.SUPERCARD_KEY: 'On'}
        with mock.patch.object(cp, 'available', return_value=True):
            p = self.ln.plan(self.paths, self.prof, None, game_vals={}, system_vals=user)
        self.assertTrue(p['supercard'])
        self.assertEqual(self.bits(p, 45, 48), 0b1010)    # Pad1: SNAC-port1
        self.assertEqual(self.bits(p, 66, 66), 0)         # SNAC MemCard: Virtual (the patch: the pad virtual)
        self.assertEqual(user['Pad1'], 'Dualshock')       # the user's own choices aren't changed

    def test_off_is_the_users_settings(self):
        user = {'Pad1': 'Dualshock', 'SNAC MemCard': 'Real'}
        with mock.patch.object(cp, 'available', return_value=True):
            p = self.ln.plan(self.paths, self.prof, None, game_vals={}, system_vals=user)
        self.assertFalse(p['supercard'])
        self.assertEqual(self.bits(p, 45, 48), 0)         # Dualshock
        self.assertEqual(self.bits(p, 66, 66), 1)         # Real

    def test_no_patch_no_supercard(self):
        with mock.patch.object(cp, 'available', return_value=False):
            p = self.ln.plan(self.paths, self.prof, None, game_vals={}, system_vals={cp.SUPERCARD_KEY: 'On'})
            self.assertFalse(p['supercard'])
            items = [{'key': 'Pad1', 'values': ['Dualshock', 'SNAC-port1'], 'value': 'Dualshock'}]
            self.assertEqual(cp.decorate_menu(items, self.paths, self.rbf, {}, {})[0]['values'],
                             ['Dualshock', 'SNAC-port1'])   # and no Pad1 choice for it in the menu

    def test_game_choice_wins(self):
        self.assertEqual(cp.supercard_value({cp.SUPERCARD_KEY: 'On'}, {cp.SUPERCARD_KEY: 'Off'}), 'Off')
        self.assertEqual(cp.supercard_value({cp.SUPERCARD_KEY: 'On'}, {}), 'On')
        self.assertEqual(cp.supercard_value({}, None), 'Off')

    def menu(self, system_vals, game_vals=None, game_keys=()):
        items = [{'key': 'Pad1', 'values': ['Dualshock', 'SNAC-port1'], 'value': 'Dualshock', 'from': 'default',
                  'per_game': False, 'per_display': False},
                 {'key': 'Pad2', 'values': ['Dualshock', 'SNAC-port2'], 'value': 'Dualshock', 'from': 'default',
                  'per_game': False, 'per_display': False},
                 {'key': 'SNAC MemCard', 'values': ['Virtual', 'Real'], 'value': 'Virtual', 'from': 'default'}]
        with mock.patch.object(cp, 'available', return_value=True):
            return cp.decorate_menu(items, self.paths, self.rbf, system_vals, game_vals or {}, game_keys)

    def test_a_pad_choice_in_the_menu(self):
        """SuperCard is a Pad1 / Pad2 choice named for what it does, not the core's own labels (f3bandit, Nov 22 2026)."""
        off = self.menu({})
        self.assertEqual(off[0]['values'], ['Dualshock', 'SNAC-port1', cp.SUPERCARD_CHOICE])
        self.assertEqual(off[1]['values'], ['Dualshock', 'SNAC-port2', cp.SUPERCARD_CHOICE])
        self.assertEqual([it['value'] for it in off], ['Dualshock', 'Dualshock', 'Virtual'])
        on = self.menu({cp.SUPERCARD_KEY: 'On'})
        self.assertEqual([it['value'] for it in on], [cp.SUPERCARD_CHOICE, 'Dualshock', 'Real'])
        self.assertEqual(on[0]['from'], 'system')
        two = self.menu({cp.SUPERCARD2_KEY: 'On'})
        self.assertEqual([it['value'] for it in two], ['Dualshock', cp.SUPERCARD_CHOICE, 'Real'])
        game = self.menu({}, {cp.SUPERCARD2_KEY: 'On'}, [cp.SUPERCARD2_KEY])
        self.assertEqual(game[1]['value'], cp.SUPERCARD_CHOICE)
        self.assertTrue(game[1]['per_game'])

    def test_port_2(self):
        user = {'Pad1': 'Dualshock', 'Pad2': 'Dualshock', 'SNAC MemCard': 'Real', cp.SUPERCARD2_KEY: 'On'}
        with mock.patch.object(cp, 'available', return_value=True):
            p = self.ln.plan(self.paths, self.prof, None, game_vals={}, system_vals=user)
        self.assertTrue(p['supercard'])
        self.assertEqual(self.bits(p, 45, 48), 0)         # Pad1: the user's Dualshock
        self.assertEqual(self.bits(p, 49, 52), 0b1010)    # Pad2: SNAC-port2
        self.assertEqual(self.bits(p, 66, 66), 0)         # SNAC MemCard: Virtual
        self.assertEqual(p['warnings'], [])

    def test_both_ports(self):
        user = {cp.SUPERCARD_KEY: 'On', cp.SUPERCARD2_KEY: 'On'}
        with mock.patch.object(cp, 'available', return_value=True):
            p = self.ln.plan(self.paths, self.prof, None, game_vals={}, system_vals=user)
        self.assertEqual(self.bits(p, 45, 48), 0b1010)
        self.assertEqual(self.bits(p, 49, 52), 0b1010)

    def test_the_other_port_on_its_own_front_port_pad(self):
        """The patch is for the whole core: a real pad in the other front port is read from USB too: said."""
        user = {cp.SUPERCARD_KEY: 'On', 'Pad2': 'SNAC-port2'}
        with mock.patch.object(cp, 'available', return_value=True):
            p = self.ln.plan(self.paths, self.prof, None, game_vals={}, system_vals=user)
        self.assertTrue(any('Pad2' in w for w in p['warnings']))

    def test_patched_rbf_follows_the_choice(self):
        exe = os.path.join(self.d, cp.EXE)
        os.makedirs(os.path.dirname(exe))
        with open(exe, 'w') as f:
            f.write('#!/bin/sh\n')
        os.chmod(exe, 0o755)
        with mock.patch.object(cp, 'index', return_value={}) as idx:
            self.assertIsNone(cp.patched_rbf(self.paths, {'rbf': '_Console/PSX'}, self.rbf, on=False))
            idx.assert_not_called()                       # off (and memcard_patch off): not looked for
            cp.patched_rbf(self.paths, {'rbf': '_Console/PSX'}, self.rbf, on=True)
            idx.assert_called()                           # on: looked for (none here: the stock core)


if __name__ == '__main__':
    unittest.main()
