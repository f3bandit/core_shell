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

    def test_menu_item(self):
        it = ct.menu_item({ct.SETTING: 'Auto'}, {}, 'Wipeout XL (USA).cue')
        self.assertEqual((it['value'], it['from'], it['per_game'], it['live']), ('Auto', 'system', False, False))
        self.assertFalse(it['note'].startswith('Auto started'))
        it = ct.menu_item({ct.SETTING: 'Auto'}, {ct.SETTING: 'Manual'}, 'Wipeout XL (USA).cue')
        self.assertEqual((it['value'], it['from'], it['per_game']), ('Manual', 'game', True))
        last = {'serial': 'SCUS-94351', 'values': {'Pad1': 'NeGcon', 'Pad2': 'NeGcon', 'SNAC MemCard': 'Real'}}
        it = ct.menu_item({ct.SETTING: 'Auto'}, {}, None, last)
        self.assertTrue(it['note'].startswith('The core runs Pad1 NeGcon, Pad2 NeGcon, SNAC MemCard Real, set by Auto '
                                              '(this game takes: NeGcon'), it['note'])
        it = ct.menu_item({}, {}, None, {'serial': None, 'values': {'Pad1': "Pop'n"}})
        self.assertIn("Pad1 Pop'n, set by Auto (this game takes: not listed)", it['note'])

    def test_show_running(self):
        items = [{'key': 'Pad1', 'value': "Pop'n", 'note': None}, {'key': 'Pad2', 'value': 'NeGcon'},
                 {'key': 'SNAC MemCard', 'value': 'Real', 'note': 'Real: the cards.'}, {'key': 'Region', 'value': 'US'}]
        ct.show_running(items, {'values': {'Pad1': 'NeGcon', 'Pad2': 'NeGcon', 'SNAC MemCard': 'Virtual'}})
        self.assertEqual([i['value'] for i in items], ['NeGcon', 'NeGcon', 'Virtual', 'US'])
        self.assertTrue(items[0]['note'].startswith("Auto set NeGcon for this game while it runs. Your setting: Pop'n."))
        self.assertNotIn('note', items[1])                                   # (Auto's pick = the user's: as it was)
        self.assertTrue(items[2]['note'].endswith('Real: the cards.'))
        self.assertEqual((items[0]['note_plain'], items[2]['note_plain']), ('', 'Real: the cards.'))

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


class FakeMain(object):
    """Phosphor's Main's API, its opt command only: {bits: value}."""

    def __init__(self, path, vals):
        import socket
        import threading
        self.path, self.vals = path, vals
        self.s = socket.socket(socket.AF_UNIX)
        self.s.bind(path)
        self.s.listen(4)
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        import json
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            f = c.makefile('rw')
            for line in f:
                bits = line.split()[1]
                f.write(json.dumps({'ok': True, 'bits': bits, 'value': self.vals.get(bits, 0)}) + '\n')
                f.flush()
            c.close()


class Running(unittest.TestCase):
    """While a game Auto started runs, the menus show its Pads as the core has them (server.py)."""

    def test_menu(self):
        import shutil
        from unittest import mock
        from coreshell import launcher as ln, server as sv
        d = tempfile.mkdtemp()
        paths = ln.Paths(d)
        os.makedirs(paths.systems)
        shutil.copy(os.path.join(SRC, 'systems', 'psx.json'), paths.systems)
        ln.write_settings(paths, 'psx', {'Pad1': "Pop'n", 'Pad2': "Pop'n", ct.SETTING: 'Auto'})
        auto_file = mock.patch.object(sv, 'AUTO_FILE', os.path.join(d, 'cs', 'auto.json'))
        auto_file.start()
        self.addCleanup(auto_file.stop)
        cs = sv.Coreshell(paths, fifo=os.path.join(d, 'fifo'))
        rom, disc2 = '/media/usb0/games/PSX/Wipeout XL.cue', '/media/usb0/games/PSX/Ape Escape.cue'
        open(os.path.join(d, 'CORENAME'), 'w').write('PSX')
        socks = mock.patch.object(sv, 'MAIN_SOCKS', (os.path.join(d, 'no-main.sock'),))
        socks.start()
        self.addCleanup(socks.stop)
        cs._auto_last = {'rom': rom, 'serial': 'SCUS-94351',
                         'values': {'Pad1': 'NeGcon', 'Pad2': 'NeGcon', 'SNAC MemCard': 'Real'},
                         'auto': {'Pad1': 'NeGcon', 'Pad2': 'NeGcon', 'SNAC MemCard': 'Real'}}
        cs._announced = rom

        def menu(r=rom):
            m = cs.cmd_menu({'profile': 'psx', 'rom': r})['menu']
            return {i['key']: i for i in m}
        with mock.patch.object(cs, '_in_menu', return_value=False), \
                mock.patch.object(cs.smc, 'menu_items', return_value=[]), \
                mock.patch.object(sv, 'STATE_DIR', d):
            m = menu()
            self.assertEqual((m['Pad1']['value'], m['Pad2']['value'], m['SNAC MemCard']['value']),
                             ('NeGcon', 'NeGcon', 'Real'))
            self.assertIn("Your setting: Pop'n", m['Pad1']['note'])
            self.assertTrue(m[ct.SETTING]['note'].startswith('The core runs Pad1 NeGcon'))
            self.assertEqual(menu('/media/usb0/games/PSX/Other.cue')['Pad1']['value'], "Pop'n")   # another game
            # a change in the menu while it runs: the core has it (the frontend gives it), so the menu says so;
            # a value the core doesn't have changes nothing
            with self.assertRaises(ValueError):
                cs.cmd_set({'profile': 'psx', 'rom': rom, 'key': 'Pad1', 'value': 'Joystick', 'live': True})
            self.assertEqual(menu()['Pad1']['value'], 'NeGcon')
            # only saved (not given to the core): the core still runs NeGcon, and the menu says so
            cs.cmd_set({'profile': 'psx', 'rom': rom, 'key': 'Pad1', 'value': 'Digital'})
            self.assertEqual(menu()['Pad1']['value'], 'NeGcon')
            self.assertIn('Your setting: Digital', menu()['Pad1']['note'])
            cs.cmd_set({'profile': 'psx', 'rom': rom, 'key': 'Pad1', 'value': 'Analog', 'live': True})
            m = menu()
            self.assertEqual(m['Pad1']['value'], 'Analog')
            self.assertIn('The core runs Pad1 Analog, Pad2 NeGcon, SNAC MemCard Real now; Auto started it with Pad1 '
                          'NeGcon, Pad2 NeGcon, SNAC MemCard Real', m[ct.SETTING]['note'])
            self.assertNotIn('note', m['Pad1'])                    # (the core runs the user's own setting now)
            self.assertEqual(ln.read_settings(paths, 'psx')['Pad1'], 'Analog')
            # the running Main answers: what the core has now (Pad2 changed in its own menu: Digital); a record
            # without Auto's own picks (an older Coreshell's) still tells them apart
            main = FakeMain(os.path.join(d, 'main.sock'), {'O[52:49]': 2, 'O[48:45]': 3, 'O[66]': 1})
            with mock.patch.object(sv, 'MAIN_SOCKS', (main.path,)):
                m = menu()
                saved = cs._auto_last.pop('auto')
                self.assertTrue(menu()['Pad2']['note'].startswith('The core runs Digital now (Auto started it with '
                                                                  'NeGcon)'))
                cs._auto_last['auto'] = saved
            self.assertEqual((m['Pad1']['value'], m['Pad2']['value'], m['SNAC MemCard']['value']),
                             ('Analog', 'Digital', 'Real'))
            self.assertIn('The core runs Pad1 Analog, Pad2 Digital', m[ct.SETTING]['note'])
            self.assertTrue(m['Pad2']['note'].startswith("The core runs Digital now (Auto started it with NeGcon). Your "
                                                         "setting: Pop'n."), m['Pad2']['note'])
            # another core running now (loaded from the OSD): not this game's Pads
            open(os.path.join(d, 'CORENAME'), 'w').write('SNES')
            self.assertEqual(menu()['Pad2']['value'], "Pop'n")
            open(os.path.join(d, 'CORENAME'), 'w').write('PSX')
            # Coreshell started again mid-game: it still knows
            open(os.path.join(d, 'ACTIVEGAME'), 'w').write(rom)
            cs2 = sv.Coreshell(paths, fifo=os.path.join(d, 'fifo'))
            self.assertEqual(cs2._auto_last['values']['Pad2'], 'NeGcon')
            self.assertEqual(cs2._auto_running(rom), cs2._auto_last)
            # a disc loaded from the core's OSD: the core keeps its Pads, the menus show them for that disc
            with mock.patch.object(sv.gid, 'psx', return_value='SCUS-94423'), \
                    mock.patch.object(cs.events, 'emit'):
                cs.cmd_card_event({'line': 'ROM _Console/PSX\t%s\t1' % disc2})
            self.assertEqual(menu(disc2)['Pad2']['value'], 'NeGcon')
            self.assertEqual(menu(rom)['Pad2']['value'], "Pop'n")
        # back at the menu: the user's settings, and nothing kept for a restart
        with mock.patch.object(cs, '_in_menu', return_value=True), mock.patch.object(cs.smc, 'menu_items', return_value=[]):
            self.assertEqual(menu(disc2)['Pad2']['value'], "Pop'n")
        with mock.patch.object(sv, 'STATE_DIR', d), mock.patch.object(cs.events, 'game_over'):
            cs.cmd_card_event({'line': 'MENU'})
        self.assertIsNone(cs._auto_last)
        self.assertFalse(os.path.exists(sv.AUTO_FILE))


if __name__ == '__main__':
    unittest.main()
