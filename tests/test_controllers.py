"""controllers.py (the PS1 controller list) and tools/psx_controllers.py (how it's made)."""
import os
import sys
import tempfile
import unittest

from coreshell import controllers as ct

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SRC, 'tools'))
import psx_controllers as pc       # noqa: E402


class Shipped(unittest.TestCase):
    """The list in the package: games checked by hand."""

    def test_games(self):
        self.assertEqual(ct.psx('SCUS-94351'), ['negcon'])                    # Wipeout XL (USA)
        self.assertEqual(ct.psx('SCUS-94194'), ['dualshock', 'negcon'])       # Gran Turismo (USA)
        self.assertEqual(ct.psx('SLUS-00405'), ['guncon'])                    # Time Crisis (USA)
        self.assertEqual(ct.psx('SCUS-94423'), ['dualshock'])                 # Ape Escape (USA)
        self.assertEqual(ct.psx('SLUS-00654'), ['guncon', 'justifier'])       # Elemental Gearbolt (USA)
        self.assertEqual(ct.psx('SLUS-99999'), [])

    def test_pad(self):
        self.assertEqual(ct.psx_pad('SCUS-94351'), 'NeGcon')
        self.assertEqual(ct.psx_pad('SCUS-94194'), 'Dualshock')
        self.assertEqual(ct.psx_pad('SLUS-00405'), 'Dualshock')               # a gun game, no gun: the default
        self.assertEqual(ct.psx_pad('SLUS-00405', gun=True), 'GunCon')
        self.assertEqual(ct.psx_pad('SLUS-00654', gun=True), 'GunCon')
        self.assertEqual(ct.psx_pad(None), 'Dualshock')

    def test_serials_as_gameid_reads_them(self):
        import re
        bad = [s for s in ct.table() if not re.match(r'^[A-Z]{4}-\d{5}$', s)]
        self.assertEqual(bad, [])


class Tool(unittest.TestCase):
    def test_build(self):
        d = tempfile.mkdtemp()
        for sub in ('analog', 'redump'):
            os.makedirs(os.path.join(d, 'metadat', sub))
        with open(os.path.join(d, 'metadat', 'redump', 'Sony - PlayStation.dat'), 'w') as f:
            f.write('clrmamepro (\n\tname "x"\n)\n\n'
                    'game (\n\tname "Wipeout XL (USA)"\n\tserial "SCUS-94351"\n)\n'
                    'game (\n\tname "Wipeout 2097 (Europe)"\n\tserial "SCES-00242"\n)\n'
                    'game (\n\tname "Gran Turismo (USA)"\n\tserial "SCUS-94194"\n)\n'
                    'game (\n\tname "Gran Turismo (Europe) (Rev 1)"\n\tserial "SCES-00984-T"\n)\n'
                    'game (\n\tname "Italian Job, The (Europe)"\n\tserial "SLES-02087"\n)\n')
        with open(os.path.join(d, 'metadat', 'analog', 'Sony - PlayStation.dat'), 'w') as f:
            f.write('game (\n\tcomment "Gran Turismo (USA)"\n\tanalog 1\n\trom ( serial "SCUS-94194" )\n)\n')
        titles = os.path.join(d, 't.txt')
        with open(titles, 'w') as f:
            f.write('[negcon]\nWipeout 2097 / Wipeout XL\nGran Turismo\nItalian Job, The  # a note\nNo Such Game\n')
        games, missing = pc.build(d, titles)
        self.assertEqual(games['SCUS-94351'][0], ['negcon'])
        self.assertEqual(games['SCES-00242'][0], ['negcon'])
        self.assertEqual(games['SCUS-94194'][0], ['dualshock', 'negcon'])     # the pad's own analog first
        self.assertEqual(games['SCES-00984'][0], ['dualshock', 'negcon'])     # (another region: the same)
        self.assertEqual(games['SLES-02087'][0], ['negcon'])
        self.assertEqual(missing, ['negcon: No Such Game'])


class Auto(unittest.TestCase):
    """Auto: the game's Pad types and SNAC MemCard for that launch only; the game's own choices win."""

    def test_picks(self):
        ports_card = {1: 'smc', 2: 'empty'}
        self.assertEqual(ct.picks({}, {}, 'SCUS-94351', ports_card),
                         {'Pad1': 'NeGcon', 'Pad2': 'NeGcon', 'SNAC MemCard': 'Real'})
        # the front port's own controller, or Off: as the user has it
        self.assertEqual(ct.picks({'Pad1': 'SNAC-port1', 'Pad2': 'Off'}, {}, 'SCUS-94351', None), {})
        # the game's own choice wins
        self.assertEqual(ct.picks({'Pad1': 'Analog'}, {'Pad1': 'Analog', 'SNAC MemCard': 'Virtual'},
                                  'SCUS-94351', ports_card), {'Pad2': 'NeGcon'})
        # no card in the front ports: the virtual card; the ports not known: SNAC MemCard left
        self.assertEqual(ct.picks({'SNAC MemCard': 'Real'}, {}, 'SCUS-94423', {1: 'empty', 2: 'empty'}),
                         {'SNAC MemCard': 'Virtual'})
        self.assertEqual(ct.picks({}, {}, 'SCUS-94423', None), {})          # (Dualshock already)
        self.assertEqual(ct.picks({'Pad1': 'NeGcon'}, {}, 'SLUS-99999', None), {'Pad1': 'Dualshock'})
        # the disc couldn't be read: the pads as the user has them, the card still by the front ports
        self.assertEqual(ct.picks({'Pad1': "Pop'n", 'Pad2': "Pop'n"}, {}, None, ports_card), {'SNAC MemCard': 'Real'})
        self.assertEqual(ct.picks({'Pad1': "Pop'n"}, {}, '', None), {})

    def test_probe(self):
        self.assertEqual(ct.parse_probe(['port 1 smc ABCD 120', 'port 2 empty']), {1: 'smc', 2: 'empty'})
        self.assertIsNone(ct.parse_probe(['busy memory manager']))
        self.assertIsNone(ct.parse_probe(['noports']))

    def test_on(self):
        self.assertFalse(ct.auto_on({}, {}))
        self.assertTrue(ct.auto_on({ct.SETTING: 'Auto'}, {}))
        self.assertFalse(ct.auto_on({ct.SETTING: 'Auto'}, {ct.SETTING: 'Manual'}))   # a game set back to Manual

    def test_plan(self):
        import json
        from unittest import mock
        from coreshell import launcher as ln
        from coreshell.launcher import Paths
        prof = json.load(open(os.path.join(SRC, 'systems', 'psx.json')))
        d = tempfile.mkdtemp()
        paths = Paths(d)
        rbf = os.path.join(d, 'PSX_20260807.rbf')
        open(rbf, 'wb').close()
        with mock.patch.multiple(ln, installed_rbf=mock.Mock(return_value=rbf), is_exact=mock.Mock(return_value=True),
                                 handoff_main=mock.Mock(return_value=True), ini_main=mock.Mock(return_value='MiSTer'),
                                 hybrid_main=mock.Mock(return_value=None), ini_core_main=mock.Mock(return_value=None)):
            auto = {'serial': 'SCUS-94351', 'ports': {1: 'smc', 2: 'empty'}}
            p = ln.plan(paths, prof, None, game_vals={}, system_vals={ct.SETTING: 'Auto'}, auto=auto)
            self.assertEqual(p['auto'], {'Pad1': 'NeGcon', 'Pad2': 'NeGcon', 'SNAC MemCard': 'Real'})
            self.assertEqual(p['values']['Pad1'], 'NeGcon')
            self.assertIn('Pad1', p['set'])
            p = ln.plan(paths, prof, None, game_vals={}, system_vals={}, auto=auto)          # Auto off
            self.assertEqual((p['auto'], p['set']), ({}, []))
            with mock.patch.object(ln, 'handoff_main', return_value=False):                # exact: never the .CFG
                p = ln.plan(paths, prof, None, game_vals={}, system_vals={ct.SETTING: 'Auto'}, auto=auto)
            self.assertEqual(p['auto'], {})
            self.assertTrue(any('Auto' in w for w in p['warnings']))


if __name__ == '__main__':
    unittest.main()
