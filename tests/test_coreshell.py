"""Coreshell tests. Run: python3 -m unittest discover -s tests"""

import json
import os
import time
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from coreshell import confstr as cs   # noqa: E402
from coreshell import profile as pf   # noqa: E402
from coreshell import launcher as ln  # noqa: E402

SAMPLE = (
    'TEST;SS3E000000:200000,UART31250,MIDI;'
    'FS1,SFCSMCBINBS ;'
    'H1F2,BIN,Load BIOS;'
    'S0,SAVMCD,Mount Card;'
    '-;'
    'O[72:70],System Type,Auto,NTSC,PAL,Dendy,Vs.;'
    'oDE,Savestate Slot,1,2,3,4;'
    'OJK,Stereo Mix,None,25%,50%,100%;'
    'P1,Audio & Video;'
    'P1o01,Aspect Ratio,Original,Full Screen,[ARC1],[ARC2];'
    'H2P1O9B,Scandoubler Fx,None,HQ2x,CRT 25%,CRT 50%,CRT 75%;'
    'P2,Input;'
    'P2O7,Swap Joysticks,No,Yes;'
    'P2oJK,Swap Joysticks,No,Yes,Maybe;'
    'O8,Swap Joysticks,Off,On;'
    'R0,Reset;'
    'v,3;'
    'V,v260101'
)


class BitSpecs(unittest.TestCase):
    """Same answers as Main's user_io_status_bits."""

    def test_letters(self):
        self.assertEqual(cs.parse_bits('9B'), (9, 11))
        self.assertEqual(cs.parse_bits('J'), (19, 19))
        self.assertEqual(cs.parse_bits('UV'), (30, 31))

    def test_lowercase_o_adds_32(self):
        self.assertEqual(cs.parse_bits('01', ex=True), (32, 33))
        self.assertEqual(cs.parse_bits('DE', ex=True), (45, 46))

    def test_brackets(self):
        self.assertEqual(cs.parse_bits('[72:70]'), (70, 72))
        self.assertEqual(cs.parse_bits('[53]'), (53, 53))
        self.assertEqual(cs.parse_bits('[53]', ex=True), (53, 53))  # no +32 for []
        self.assertIsNone(cs.parse_bits('[128]'))
        self.assertIsNone(cs.parse_bits('[3:5]'))


class Parse(unittest.TestCase):
    def setUp(self):
        self.core = cs.parse(SAMPLE)

    def test_header(self):
        self.assertEqual(self.core['core_name'], 'TEST')
        self.assertEqual(self.core['config_ver'], 3)
        self.assertEqual(self.core['pages'], {1: 'Audio & Video', 2: 'Input'})

    def test_core_field_is_not_a_mount(self):
        self.assertEqual([m['index'] for m in self.core['mounts']], [0])

    def test_files(self):
        f = self.core['files']
        self.assertEqual((f[0]['index'], f[0]['exts']), (1, ['SFC', 'SMC', 'BIN', 'BS']))
        self.assertEqual((f[1]['index'], f[1]['flags']), (2, ['H1']))

    def test_options(self):
        by = {(o['page'], o['name']): o for o in self.core['options']}
        self.assertEqual(by[(0, 'System Type')]['bits'], [70, 72])
        self.assertEqual(by[(1, 'Aspect Ratio')]['bits'], [32, 33])
        self.assertEqual(by[(1, 'Scandoubler Fx')]['flags'], ['H2'])
        self.assertEqual(by[(1, 'Scandoubler Fx')]['bits'], [9, 11])

    def test_triggers(self):
        self.assertEqual(self.core['triggers'][0]['bits'], [0, 0])


class Profiles(unittest.TestCase):
    def setUp(self):
        self.p = pf.build(SAMPLE, 'test', 'Test', '_Console/TEST',
                          source={'rbf': 'TEST_20260101.rbf'})

    def test_basics(self):
        self.assertEqual(self.p['config_file'], 'TEST_v3.CFG')
        self.assertEqual(self.p['rom']['index'], 1)

    def test_find_option(self):
        self.assertIsNotNone(pf.find_option(self.p, 'Aspect Ratio'))
        self.assertEqual(pf.find_option(self.p, 'Swap Joysticks')['page'], '')  # main-page key
        self.assertEqual(pf.find_option(self.p, 'Input/Swap Joysticks')['bits'], [7, 7])
        self.assertEqual(pf.find_option(self.p, 'Input/Swap Joysticks #2')['bits'], [51, 52])

    def test_mgl_ok(self):
        ok = {o['key']: o['mgl_ok'] for o in self.p['menu']}
        self.assertFalse(ok['Input/Swap Joysticks'])     # Main: ambiguous in its page
        self.assertFalse(ok['Swap Joysticks'])           # no page: matches all pages
        self.assertTrue(ok['Audio & Video/Aspect Ratio'])

    def test_overrides(self):
        pf.apply_overrides(self.p, {'hide': ['Stereo Mix'],
                                    'defaults': {'Audio & Video/Aspect Ratio': 'Full Screen'}})
        names = [o['name'] for o in pf.visible_menu(self.p)]
        self.assertNotIn('Stereo Mix', names)
        self.assertEqual(pf.find_option(self.p, 'Aspect Ratio')['default'], 'Full Screen')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = ln.Paths(self.tmp.name)
        self.p = pf.build(SAMPLE, 'test', 'Test', '_Console/TEST',
                          source={'rbf': 'TEST_20260101.rbf'})
        os.makedirs(os.path.join(self.tmp.name, '_Console'))
        self.rbf = os.path.join(self.tmp.name, '_Console', 'TEST_20260101.rbf')
        open(self.rbf, 'wb').close()
        self.main = os.path.join(self.tmp.name, 'MiSTer')
        self.rom = os.path.join(self.tmp.name, 'games', 'TEST', 'Game (USA).sfc')
        os.makedirs(os.path.dirname(self.rom))
        open(self.rom, 'wb').close()
        self.fifo = os.path.join(self.tmp.name, 'fifo')

    def tearDown(self):
        self.tmp.cleanup()

    def set_main(self, new):
        with open(self.main, 'wb') as f:
            f.write(b'\0junk MGL option: %s\0' if new else b'\0junk\0')

    def test_status_bytes_layout(self):
        vals = ln.effective(self.p, {'System Type': 'Dendy', 'Aspect Ratio': '[ARC2]',
                                     'Stereo Mix': '50%'}, None)
        st = ln.status_bytes(self.p, vals)
        self.assertEqual(len(st), 16)
        n = int.from_bytes(st, 'little')
        self.assertEqual((n >> 70) & 7, 3)    # Dendy = value 3
        self.assertEqual((n >> 32) & 3, 3)    # [ARC2] = value 3
        self.assertEqual((n >> 19) & 3, 2)    # 50% = value 2
        self.assertEqual(n & 1, 0)            # reset bit clear

    def test_cfg_keeps_unknown_bits(self):
        os.makedirs(self.paths.config)
        cfg = os.path.join(self.paths.config, 'TEST_v3.CFG')
        with open(cfg, 'wb') as f:
            f.write((1 << 100 | 1 << 70).to_bytes(16, 'little'))
        ln.write_cfg(self.paths, self.p, ln.effective(self.p, {'Stereo Mix': '100%'}, None))
        n = int.from_bytes(open(cfg, 'rb').read(), 'little')
        self.assertTrue(n >> 100 & 1)          # bit nobody owns: kept
        self.assertEqual((n >> 70) & 7, 0)    # System Type back to Auto
        self.assertEqual((n >> 19) & 3, 3)
        self.assertTrue(os.path.exists(os.path.join(self.paths.backups, 'TEST_v3.CFG')))

    def test_cfg_only_what_is_set(self):
        """Nothing set: no .CFG made; something set: only it changes, and bytes after the status block stay."""
        cfg = os.path.join(self.paths.config, 'TEST_v3.CFG')
        ln.write_cfg(self.paths, self.p, {})
        self.assertFalse(os.path.exists(cfg))
        os.makedirs(self.paths.config)
        tail = b'more of the core\'s own file'
        with open(cfg, 'wb') as f:
            f.write((1 << 70).to_bytes(16, 'little') + tail)
        ln.write_cfg(self.paths, self.p, {'Stereo Mix': '100%'})
        data = open(cfg, 'rb').read()
        n = int.from_bytes(data[:16], 'little')
        self.assertEqual((n >> 70) & 7, 1)    # System Type: not set, so the user's value stays
        self.assertEqual((n >> 19) & 3, 3)
        self.assertEqual(data[16:], tail)

    def test_exact_mode(self):
        self.set_main(False)
        p = ln.launch(self.paths, self.p, self.rom, system_vals={'Stereo Mix': '25%'},
                      game_vals={}, fifo=self.fifo)
        self.assertEqual(p['mode'], 'exact')
        self.assertNotIn('<option', p['mgl'])
        self.assertIn('type="f" index="1"', p['mgl'])
        self.assertEqual(open(self.fifo).read(), 'load_core %s\n' % p['mgl_file'])
        self.assertTrue(os.path.exists(os.path.join(self.paths.config, 'TEST_v3.CFG')))

    def test_untouched_settings_kept(self):
        """Rule: Coreshell sets only what was chosen in Coreshell (and a profile's own non-first defaults); every
        other option stays as the core's .CFG has it (Quake's picture reset to defaults, Nov 16 2026)."""
        cfg = os.path.join(self.paths.config, 'TEST_v3.CFG')
        os.makedirs(self.paths.config, exist_ok=True)
        user = (3 << 70 | 3 << 19).to_bytes(16, 'little')          # the user's own System Type and Stereo Mix
        with open(cfg, 'wb') as f:
            f.write(user)
        # exact mode: the .CFG gets only the chosen option (nothing chosen: untouched)
        self.set_main(False)
        p = ln.launch(self.paths, self.p, self.rom, system_vals={}, game_vals={}, fifo=self.fifo)
        self.assertEqual(p['mode'], 'exact')
        self.assertEqual(open(cfg, 'rb').read(), user)
        p = ln.launch(self.paths, self.p, self.rom, system_vals={'Stereo Mix': '25%'}, game_vals={}, fifo=self.fifo)
        n = int.from_bytes(open(cfg, 'rb').read(), 'little')
        self.assertEqual((n >> 70) & 7, 3)                           # System Type: the user's, kept
        self.assertEqual((n >> 19) & 3, 1)                           # Stereo Mix: Coreshell's
        # handoff: only the chosen option in the mask
        mask, val = ln.handoff_status(self.p, p['values'], ['Stereo Mix'])
        m = int.from_bytes(mask, 'little')
        self.assertEqual(m, 3 << 19)

    def test_handoff_mode(self):
        """A Main with the hook: settings go in the MGL, the user's .CFG and INI are never written."""
        hooked = os.path.join(self.tmp.name, 'phosphor', 'bin', 'MiSTer_phosphor')
        os.makedirs(os.path.dirname(hooked))
        with open(hooked, 'wb') as f:
            f.write(b'\0Coreshell handoff: status\0')
        self.p['ini'] = {'vsync_adjust': '2'}
        os.makedirs(self.paths.config)
        cfg = os.path.join(self.paths.config, 'TEST_v3.CFG')
        user = (1 << 100 | 1 << 19).to_bytes(16, 'little')      # the user's own: bit 100 nobody owns
        with open(cfg, 'wb') as f:
            f.write(user)
        p = ln.launch(self.paths, self.p, self.rom, system_vals={'System Type': 'Dendy'},
                      game_vals={}, fifo=self.fifo, running=hooked)
        self.assertEqual(p['mode'], 'handoff')
        self.assertEqual(open(cfg, 'rb').read(), user)                # never written
        mask, val = ln.handoff_status(self.p, p['values'], p['set'])
        self.assertIn('<status mask="%s" value="%s"/>' % (mask.hex(), val.hex()), p['mgl'])
        self.assertIn('<setting key="vsync_adjust" value="2"/>', p['mgl'])
        m, v = int.from_bytes(mask, 'little'), int.from_bytes(val, 'little')
        self.assertEqual((v >> 70) & 7, 3)                           # Dendy
        self.assertTrue(m >> 70 & 1 and not m >> 100 & 1 and not m & 1)   # its bits; not the user's, not reset
        # sync back: nothing saved in the OSD -> nothing taken; a save -> taken
        handed = bytes((b & ~mm | vv & mm) & 0xFF for b, mm, vv in zip(user, mask, val))
        ln.remember_written(self.paths, self.p, 'test', None, None, handed)
        self.assertEqual(ln.sync_back(self.paths, self.p, 'test'), {})
        saved = bytearray(handed)
        saved[70 // 8] &= ~(7 << (70 % 8)) & 0xFF                    # the user picks Auto in the OSD and saves
        time.sleep(0.01)                                              # (a save after the launch: a later time)
        with open(cfg, 'wb') as f:
            f.write(bytes(saved))                                     # (the same bytes as the user's file before)
        self.assertEqual(ln.sync_back(self.paths, self.p, 'test'), {'System Type': 'Auto'})
        # a stock Main: the old way
        self.set_main(False)
        p = ln.launch(self.paths, self.p, self.rom, game_vals={}, fifo=self.fifo, running=self.main)
        self.assertEqual(p['mode'], 'exact')
        self.assertNotIn('<status', p['mgl'])
        self.assertTrue(any('INI settings' in w for w in p['warnings']))

    def test_hybrid_main(self):
        """A hybrid port's core: main= its port's MiSTer_hybrid, shown to every Main in the INI view (never in the
        MGL: only the running Main reads that), only with the shim and no main= of the user's for it in MiSTer.ini."""
        from coreshell.mains import SHIM_MARK
        from coreshell import inicheck as ic
        plat = os.path.join(self.tmp.name, 'games', 'Port', 'platform')
        os.makedirs(os.path.join(plat, 'hybrid.d'))
        with open(os.path.join(plat, 'hybrid.d', self.p['core_name'] + '.conf'), 'w') as f:
            f.write('launcher=/media/fat/games/Port/launch.sh\n')
        hyb = os.path.join(plat, 'MiSTer_hybrid')
        with open(hyb, 'wb') as f:
            f.write(b'stock')
        go = lambda: ln.plan(self.paths, self.p, self.rom, game_vals={}, system_vals={}, running=self.main)
        self.assertIsNone(go()['hybrid'])                             # stock build: left alone
        with open(hyb, 'wb') as f:
            f.write(b'\0' + SHIM_MARK + b'\0')
        p = go()
        self.assertEqual(p['main'], hyb)
        self.assertEqual(p['hybrid'], {'section': self.p['core_name'], 'main': hyb})
        self.assertNotIn('<setting', p['mgl'])
        self.assertNotIn('<status', p['mgl'])
        ini = os.path.join(self.tmp.name, 'MiSTer.ini')
        with open(ini, 'w') as f:
            f.write('[MiSTer]\r\nmain=phosphor/bin/MiSTer_phosphor\r\n')
        text, keys = ic.launch_view(self.tmp.name, 'MiSTer.ini', hyb, os.path.join(self.tmp.name, 'state'),
                                    ('TEST', 'main', hyb))
        self.assertTrue(text.startswith('[MiSTer]\r\nmain=phosphor/bin/MiSTer_phosphor\r\n'))
        self.assertTrue(text.endswith('[TEST]\r\nmain=%s\r\n' % hyb))
        self.assertEqual(open(ini).read(), '[MiSTer]\nmain=phosphor/bin/MiSTer_phosphor\n')   # (never written)
        for line in (';main=' + hyb, 'main=MiSTer'):                  # the user's own line wins, on or off
            with open(ini, 'w') as f:
                f.write('[MiSTer]\nmain=phosphor/bin/MiSTer_phosphor\n[%s]\n%s\n' % (self.p['core_name'], line))
            self.assertIsNone(go()['hybrid'])

    def test_name_mode(self):
        self.set_main(True)
        os.rename(self.rbf, self.rbf.replace('20260101', '20270101'))
        p = ln.launch(self.paths, self.p, self.rom,
                      system_vals={'Stereo Mix': '25%'},
                      game_vals={'Aspect Ratio': 'Full Screen'}, fifo=self.fifo)
        self.assertEqual(p['mode'], 'name')
        self.assertIn('<option name="Aspect Ratio" page="Audio &amp; Video" value="Full Screen"/>',
                      p['mgl'])
        self.assertIn('<option name="Stereo Mix" value="25%"/>', p['mgl'])
        self.assertFalse(os.path.exists(os.path.join(self.paths.config, 'TEST_v3.CFG')))

    def test_core_mode_never_writes_bits(self):
        self.set_main(False)
        os.rename(self.rbf, self.rbf.replace('20260101', '20270101'))
        p = ln.launch(self.paths, self.p, self.rom, system_vals={'Stereo Mix': '25%'},
                      game_vals={}, fifo=self.fifo)
        self.assertEqual(p['mode'], 'core')
        self.assertTrue(p['warnings'])
        self.assertNotIn('<option', p['mgl'])
        self.assertFalse(os.path.exists(os.path.join(self.paths.config, 'TEST_v3.CFG')))

    def test_name_mode_limit(self):
        self.set_main(True)
        os.rename(self.rbf, self.rbf.replace('20260101', '20270101'))
        big = SAMPLE + ''.join(';P2O[%d],Extra %d,A,B' % (100 + i, i) for i in range(15))
        prof = pf.build(big, 'test', 'Test', '_Console/TEST', source={'rbf': 'TEST_20260101.rbf'})
        vals = {'Input/Extra %d' % i: 'B' for i in range(15)}
        self.assertEqual(len([k for k in vals if pf.find_option(prof, k)]), 15)
        vals['Stereo Mix'] = '25%'
        vals['System Type'] = 'PAL'
        p = ln.plan(self.paths, prof, self.rom, system_vals=vals, game_vals={})
        self.assertEqual(p['mgl'].count('<option'), 16)
        self.assertTrue(p['warnings'])

    def test_ini_main(self):
        with open(os.path.join(self.tmp.name, 'MiSTer.ini'), 'w') as f:
            f.write('[MiSTer]\nvideo_mode=8\n[RA_*]\nmain=MiSTer_RA ; odelot\n'
                    '[A0CD-*]\nmain=MiSTer_Physical-CD\n[SNES]\nvsync_adjust=1\n')
        self.assertEqual(ln.ini_main(self.paths, ['RA_SNES']), 'MiSTer_RA')
        self.assertEqual(ln.ini_main(self.paths, ['ra_nes']), 'MiSTer_RA')
        self.assertEqual(ln.ini_main(self.paths, ['A0CD-PSX']), 'MiSTer_Physical-CD')
        self.assertEqual(ln.ini_main(self.paths, ['SNES']), 'MiSTer')

    def test_mode_follows_the_core_s_main(self):
        # stock Main has MGL options, odelot's doesn't: an RA core gets core mode
        self.set_main(True)
        with open(os.path.join(self.tmp.name, 'MiSTer_RA'), 'wb') as f:
            f.write(b'old main')
        with open(os.path.join(self.tmp.name, 'MiSTer.ini'), 'w') as f:
            f.write('[RA_*]\nmain=MiSTer_RA\n')
        os.rename(self.rbf, os.path.join(self.tmp.name, '_Console', 'RA_TEST_20270101.rbf'))
        prof = dict(self.p, rbf='_Console/RA_TEST')
        p = ln.plan(self.paths, prof, self.rom, system_vals={'Stereo Mix': '25%'}, game_vals={})
        self.assertEqual((p['main'], p['mode']), ('MiSTer_RA', 'core'))

    def test_ra_variant_setname_and_md5(self):
        self.set_main(False)
        ra = os.path.join(self.tmp.name, '_RA_Cores', 'Cores')
        os.makedirs(ra)
        with open(os.path.join(ra, 'TEST.rbf'), 'wb') as f:
            f.write(b'ra core build 1')
        import hashlib
        md5 = hashlib.md5(b'ra core build 1').hexdigest()
        prof = pf.build(SAMPLE, 'test', 'Test', '_RA_Cores/Cores/TEST', setname='RA_TEST',
                        variant='ra', source={'rbf_md5': md5})
        self.assertEqual(prof['config_file'], 'RA_TEST_v3.CFG')
        with open(os.path.join(self.tmp.name, 'MiSTer.ini'), 'w') as f:
            f.write('[RA_*]\nmain=MiSTer_RA\n')
        p = ln.plan(self.paths, prof, self.rom, system_vals={'Stereo Mix': '25%'}, game_vals={})
        self.assertEqual((p['mode'], p['main']), ('exact', 'MiSTer_RA'))
        self.assertIn('<setname same_dir="1">RA_TEST</setname>', p['mgl'])
        with open(os.path.join(ra, 'TEST.rbf'), 'wb') as f:
            f.write(b'ra core build 2, same name')
        p = ln.plan(self.paths, prof, self.rom, system_vals={'Stereo Mix': '25%'}, game_vals={})
        self.assertEqual(p['mode'], 'core')

    def test_slot_by_extension(self):
        prof = pf.build(SAMPLE + ';S1,CUECHD,Load CD', 'test', 'Test', '_Console/TEST')
        self.assertEqual(ln.slot_for(prof, 'x.sfc')['index'], 1)
        s = ln.slot_for(prof, '/g/Game (USA).chd')
        self.assertEqual((s['type'], s['index']), ('s', 1))
        self.assertNotIn('SAV', [e for x in prof['slots'] for e in x['exts']])

    def test_save_placed_with_backup(self):
        self.set_main(False)
        mine = os.path.join(self.tmp.name, 'mine.sav')
        with open(mine, 'wb') as f:
            f.write(b'new')
        dst = ln.save_path(self.paths, self.p, self.rom)
        os.makedirs(os.path.dirname(dst))
        with open(dst, 'wb') as f:
            f.write(b'old')
        ln.launch(self.paths, self.p, self.rom, save=mine, system_vals={}, game_vals={},
                  fifo=self.fifo)
        self.assertEqual(open(dst, 'rb').read(), b'new')
        self.assertTrue(dst.endswith('saves/TEST/Game (USA).sav'))
        b = os.listdir(os.path.join(self.paths.backups, 'saves', 'test'))
        self.assertEqual(len(b), 1)


class Api(unittest.TestCase):
    def setUp(self):
        from coreshell import server as sv
        self.sv = sv
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = ln.Paths(self.tmp.name)
        prof = pf.build(SAMPLE, 'test', 'Test', '_Console/TEST', source={'rbf': 'TEST_20260101.rbf'})
        pf.save(prof, os.path.join(self.paths.systems, 'test.json'))
        os.makedirs(os.path.join(self.tmp.name, '_Console'))
        open(os.path.join(self.tmp.name, '_Console', 'TEST_20260101.rbf'), 'wb').close()
        self.fifo = os.path.join(self.tmp.name, 'fifo')
        self.shell = sv.Coreshell(self.paths, fifo=self.fifo)
        self.sock = os.path.join(self.tmp.name, 'cs.sock')
        self.srv = sv.Server(self.shell, self.sock)
        import threading
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def q(self, **req):
        req.setdefault('v', 1)
        return self.sv.request(req, self.sock)

    def test_version_gate(self):
        self.assertFalse(self.q(v=2, cmd='hello')['ok'])
        self.assertEqual(self.q(cmd='hello')['api'], 1)

    def test_menu_set_launch(self):
        r = self.q(cmd='systems')
        self.assertEqual(r['systems'][0]['profile'], 'test')
        self.assertTrue(r['systems'][0]['installed'])
        self.assertTrue(self.q(cmd='set', profile='test', key='Stereo Mix', value='50%')['ok'])
        self.assertFalse(self.q(cmd='set', profile='test', key='Stereo Mix', value='75%')['ok'])
        self.assertTrue(self.q(cmd='set', profile='test', key='Aspect Ratio',
                               value='Full Screen', game='Game (USA).sfc')['ok'])
        menu = {i['key']: i for i in self.q(cmd='menu', profile='test', game='Game (USA).sfc')['menu']}
        self.assertEqual(menu['Stereo Mix']['value'], '50%')
        self.assertTrue(menu['Audio & Video/Aspect Ratio']['per_game'])
        rom = os.path.join(self.tmp.name, 'games', 'TEST', 'Game (USA).sfc')
        r = self.q(cmd='launch', profile='test', rom=rom)
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['plan']['mode'], 'exact')
        n = int.from_bytes(open(os.path.join(self.paths.config, 'TEST_v3.CFG'), 'rb').read(), 'little')
        self.assertEqual((n >> 19) & 3, 2)    # system setting
        self.assertEqual((n >> 32) & 3, 1)    # per-game override
        self.assertTrue(open(self.fifo).read().startswith('load_core '))


    def test_osd_changes_come_back(self):
        self.q(cmd='set', profile='test', key='Stereo Mix', value='50%')
        self.q(cmd='set', profile='test', key='Audio & Video/Aspect Ratio', value='Full Screen',
               game='Game (USA).sfc')
        rom = os.path.join(self.tmp.name, 'games', 'TEST', 'Game (USA).sfc')
        self.assertEqual(self.q(cmd='launch', profile='test', rom=rom)['plan']['mode'], 'exact')
        cfg = os.path.join(self.paths.config, 'TEST_v3.CFG')
        n = int.from_bytes(open(cfg, 'rb').read(), 'little')
        n = (n & ~(3 << 19)) | (3 << 19)          # OSD: Stereo Mix 100%
        n = n & ~(3 << 32)                         # OSD: Aspect Ratio back to Original
        with open(cfg, 'wb') as f:
            f.write(n.to_bytes(16, 'little'))
        r = self.q(cmd='sync', profile='test')
        self.assertEqual(r['synced'], {'Stereo Mix': '100%', 'Audio & Video/Aspect Ratio': 'Original'})
        menu = {i['key']: i for i in self.q(cmd='menu', profile='test', game='Game (USA).sfc')['menu']}
        self.assertEqual(menu['Stereo Mix']['value'], '100%')
        self.assertEqual(menu['Audio & Video/Aspect Ratio']['value'], 'Original')
        self.assertTrue(menu['Audio & Video/Aspect Ratio']['per_game'])   # back into the game's layer
        self.assertEqual(self.q(cmd='sync', profile='test')['synced'], {})

    def test_save_picker(self):
        rom = os.path.join(self.tmp.name, 'games', 'TEST', 'Game (USA).sfc')
        p = self.shell.profile('test')
        cur = ln.save_path(self.paths, p, rom)
        os.makedirs(os.path.dirname(cur), exist_ok=True)
        with open(cur, 'wb') as f:
            f.write(b'mine')
        lib = os.path.join(self.paths.home, 'saves', 'test', 'Game (USA)')
        os.makedirs(lib)
        with open(os.path.join(lib, '100 percent.sav'), 'wb') as f:
            f.write(b'from the PC')
        saves = self.q(cmd='saves', profile='test', rom=rom)['saves']
        self.assertEqual([x['from'] for x in saves], ['current', 'library'])
        self.assertTrue(self.q(cmd='launch', profile='test', rom=rom, save=saves[1]['path'])['ok'])
        self.assertEqual(open(cur, 'rb').read(), b'from the PC')
        saves = self.q(cmd='saves', profile='test', rom=rom)['saves']
        self.assertEqual(sorted(x['from'] for x in saves), ['backup', 'current', 'library'])

    def test_extra_slots(self):
        slots = {x['id']: x for x in self.q(cmd='slots', profile='test')['slots']}
        self.assertEqual(slots['s0']['label'], 'Mount Card')
        self.assertFalse(slots['s0']['game'])
        self.assertTrue(slots['f1']['game'])
        rom = os.path.join(self.tmp.name, 'games', 'TEST', 'Game (USA).sfc')
        r = self.q(cmd='plan', profile='test', rom=rom, files={'s0': '/media/fat/saves/TEST/card.sav'})
        self.assertIn('<file delay="1" type="s" index="0" path="/media/fat/saves/TEST/card.sav"/>', r['plan']['mgl'])
        self.assertFalse(self.q(cmd='plan', profile='test', rom=rom, files={'s0': '/x/card.txt'})['ok'])
        self.assertFalse(self.q(cmd='plan', profile='test', rom=rom, files={'s9': '/x/card.sav'})['ok'])

    def test_find_by_core(self):
        r = self.q(cmd='menu', rbf='_Console/TEST_20260101.rbf')
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['profile'], 'test')
        self.assertFalse(self.q(cmd='menu', rbf='_Console/TEST', setname='RA_TEST')['ok'])

class FakeSmcd(object):
    """smcd's socket, answering as super-memcard's smcd/smcd.c does."""
    def __init__(self, path):
        import socketserver
        import threading
        self.cards = {'NES': 1}
        self.got = []
        me = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                l = self.rfile.readline().decode().rstrip('\n')
                me.got.append(l)
                if l == 'SYSTEMS':
                    out = 'NES\t3\nPSX\t1\n'
                elif l.startswith('CARDS '):
                    s = l[6:]
                    cur = me.cards.get(s, 1)
                    out = 'card\t1\t0\t0\t%d\ncard\t3\t5\t1763000000\t%d\nnew\t2\n' % (cur == 1, cur == 3)
                elif l.startswith('CARD '):
                    s, n = l[5:].split('\t')
                    me.cards[s] = int(n)
                    out = 'card ok %s\n' % n
                else:
                    out = ''
                self.wfile.write((out + 'END\n').encode())

        self.srv = socketserver.ThreadingUnixStreamServer(path, H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class SuperMemCardPage(unittest.TestCase):
    def setUp(self):
        from coreshell import server as sv
        from coreshell import supermemcard as smc
        self.smc = smc
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = ln.Paths(self.tmp.name)
        for sysname, saves in (('nes', 'saves/NES'), ('psx', 'saves/PSX'), ('c64', 'saves/C64')):
            prof = pf.build(SAMPLE, sysname, sysname, '_Console/TEST', saves_dir=saves,
                            source={'rbf': 'TEST_20260101.rbf'})
            pf.save(prof, os.path.join(self.paths.systems, sysname + '.json'))
        self.fake = FakeSmcd(os.path.join(self.tmp.name, 'smcd.sock'))
        self.shell = sv.Coreshell(self.paths, fifo=os.path.join(self.tmp.name, 'fifo'))
        self.shell.smc = smc.SuperMemCard(os.path.join(self.tmp.name, 'smcd.sock'), background=False)

    def tearDown(self):
        self.fake.close()
        self.tmp.cleanup()

    def q(self, **req):
        req.setdefault('v', 1)
        return self.shell.handle(req)

    def card_item(self, prof):
        return [i for i in self.q(cmd='menu', profile=prof)['menu'] if i['key'] == self.smc.CARD_KEY]

    def test_card_picker(self):
        item, = self.card_item('nes')
        self.assertEqual(item['values'], ['1', '3', '2 (new)'])
        self.assertEqual(item['value'], '1')
        r = self.q(cmd='set', profile='nes', key=self.smc.CARD_KEY, value='3')
        self.assertTrue(r['ok'], r)
        self.assertIn('CARD NES\t3', self.fake.got)
        self.assertEqual(self.card_item('nes')[0]['value'], '3')
        self.assertTrue(self.q(cmd='reset', profile='nes', key=self.smc.CARD_KEY)['ok'])

    def test_no_picker(self):
        self.assertEqual(self.card_item('psx'), [])     # PSX keeps its own cards
        self.assertEqual(self.card_item('c64'), [])     # not a Super MemCard system
        self.assertFalse(self.q(cmd='set', profile='psx', key=self.smc.CARD_KEY, value='1')['ok'])

    def test_no_smcd(self):
        self.fake.close()
        os.unlink(os.path.join(self.tmp.name, 'smcd.sock'))
        self.assertEqual(self.card_item('nes'), [])


class Refresh(unittest.TestCase):
    def setUp(self):
        from coreshell import refresh as rf
        from coreshell import server as sv
        self.rf = rf
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = ln.Paths(self.tmp.name)
        os.makedirs(os.path.join(self.tmp.name, '_Console'))
        self.rbf = os.path.join(self.tmp.name, '_Console', 'TEST_20260101.rbf')
        with open(self.rbf, 'wb') as f:
            f.write(b'build 1')
        prof = pf.build(SAMPLE, 'test', 'TEST', '_Console/TEST',
                        source={'rbf': 'TEST_20260101.rbf', 'rbf_md5': ln.file_md5(self.rbf)})
        pf.save(prof, os.path.join(self.paths.systems, 'test.json'))
        self.shell = sv.Coreshell(self.paths, fifo=os.path.join(self.tmp.name, 'fifo'))

    def tearDown(self):
        self.tmp.cleanup()

    def update_core(self, confstr):
        """Update All puts a new build in: its dump as tools/dump_confstr.py writes it."""
        os.unlink(self.rbf)
        self.rbf = os.path.join(self.tmp.name, '_Console', 'TEST_20261101.rbf')
        with open(self.rbf, 'wb') as f:
            f.write(b'build 2')
        os.makedirs(os.path.join(self.paths.home, 'dumps'), exist_ok=True)
        with open(self.rf.dump_name(self.paths, self.rbf), 'w') as f:
            json.dump({'rbf': '_Console/TEST_20261101.rbf', 'rbf_md5': ln.file_md5(self.rbf),
                       'main': 'MiSTer', 'confstr': confstr}, f)

    def test_nothing_stale(self):
        self.assertEqual(self.rf.stale(self.paths, self.shell.profiles()), [])

    def test_changed_core_rebuilt(self):
        self.update_core(SAMPLE.replace('OJK,Stereo Mix', 'OLM,Stereo Mix'))
        todo = self.rf.stale(self.paths, self.shell.profiles())
        self.assertEqual([(i['base'], i['why'], i['profile']) for i in todo],
                         [('_Console/TEST', 'changed', 'test')])
        pid = self.rf.rebuild(self.paths, todo[0], self.shell.profiles())
        self.assertEqual(pid, 'test')
        p = self.shell.profile('test')
        self.assertTrue(ln.is_exact(p, self.rbf))
        self.assertEqual(pf.find_option(p, 'Stereo Mix')['bits'], [21, 22])
        self.assertEqual(self.rf.stale(self.paths, self.shell.profiles()), [])

    def test_new_core_and_failures_remembered(self):
        with open(os.path.join(self.tmp.name, '_Console', 'NEWSYS_20261101.rbf'), 'wb') as f:
            f.write(b'x')
        todo = self.rf.stale(self.paths, self.shell.profiles())
        self.assertEqual([(i['base'], i['why']) for i in todo], [('_Console/NEWSYS', 'new')])
        self.rf.write_state(self.paths, {'failed': {'_Console/NEWSYS': ln.file_md5(todo[0]['rbf'])}})
        self.assertEqual(self.rf.stale(self.paths, self.shell.profiles()), [])

    def test_disc_profiles(self):
        d = os.path.join(self.tmp.name, '_Physical Disc Cores')
        os.makedirs(d)
        with open(os.path.join(d, 'TEST.mgl'), 'w') as f:
            f.write('<mistergamedescription>\n<rbf>_Console/TEST</rbf>\n'
                    '<setname same_dir="1">A0CD-TEST</setname>\n</mistergamedescription>\n')
        self.assertEqual(self.rf.disc_profiles(self.paths, self.shell.profiles()), ['test@disc'])
        self.assertEqual(self.rf.disc_profiles(self.paths, self.shell.profiles()), [])   # unchanged
        p = self.shell.profile('test@disc')
        self.assertEqual((p['setname'], p['config_file'], p['rom'], p['variant']),
                         ('A0CD-TEST', 'A0CD-TEST_v3.CFG', None, 'disc'))
        self.assertEqual(self.rf.stale(self.paths, self.shell.profiles()), [])
        with open(os.path.join(self.tmp.name, 'MiSTer.ini'), 'w') as f:
            f.write('[MiSTer]\n[A0CD-*]\nmain=MiSTer_Physical-CD\n')
        plan = ln.plan(self.paths, p)
        self.assertEqual(plan['main'], 'MiSTer_Physical-CD')
        self.assertIn('<setname same_dir="1">A0CD-TEST</setname>', plan['mgl'])
        self.assertNotIn('<file', plan['mgl'])

    def test_api(self):
        r = self.shell.handle({'v': 1, 'cmd': 'stale'})
        self.assertEqual(r, {'stale': [], 'ok': True})
        self.assertEqual(self.shell.handle({'v': 1, 'cmd': 'hello'})['refresh']['state'], 'idle')


class Diff(unittest.TestCase):
    def test_breaking_only_when_a_setting_is_lost(self):
        old = pf.build(SAMPLE, 'test', 'TEST', '_Console/TEST')
        moved = pf.build(SAMPLE.replace('OJK,Stereo Mix', 'OLM,Stereo Mix').replace('v,3', 'v,4'),
                         'test', 'TEST', '_Console/TEST')
        d = pf.diff(old, moved)
        self.assertEqual(sorted(x['what'] for x in d), ['bits moved', 'config file renamed'])
        self.assertFalse(any(x['breaking'] for x in d))
        cut = pf.build(SAMPLE.replace('OJK,Stereo Mix,None,25%,50%,100%', 'OJK,Stereo Mix,None,50%')
                       .replace('P2O7,Swap Joysticks,No,Yes;', ''), 'test', 'TEST', '_Console/TEST')
        bad = sorted((x['what'], x.get('option')) for x in pf.diff(old, cut) if x['breaking'])
        self.assertEqual(bad, [('option removed', 'Input/Swap Joysticks #2'),
                               ('values removed', 'Stereo Mix')])


class Junk(unittest.TestCase):
    def test_debug_aids_hidden(self):
        prof = pf.build(SAMPLE.replace('v,3;', 'P3,Debug;P3OP,Show Layer,No,Yes;OQ,Debug Dots,Off,On;v,3;'),
                        'test', 'TEST', '_Console/TEST')
        keys = [pf.option_key(o) for o in pf.visible_menu(prof)]
        self.assertNotIn('Debug/Show Layer', keys)
        self.assertNotIn('Debug Dots', keys)
        self.assertIn('Stereo Mix', keys)
        prof['show'] = ['Debug Dots']
        self.assertIn('Debug Dots', [pf.option_key(o) for o in pf.visible_menu(prof)])


class ToolsPage(unittest.TestCase):
    def setUp(self):
        from coreshell import server as sv
        from coreshell import tools as tl
        self.tl = tl
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = ln.Paths(self.tmp.name)
        sd = os.path.join(self.tmp.name, 'Scripts')
        os.makedirs(sd)
        scripts = {'hello.sh': 'echo hello from the tools page\n',
                   'ask.sh': 'read -p "Name? " NAME\necho $NAME\n',
                   'menu.sh': 'dialog --menu "Pick" 10 30 2 a A b B\n',
                   'conf.sh': 'IFS="," read -ra X <<< "$Y"\necho ok\n'}
        for n, body in scripts.items():
            with open(os.path.join(sd, n), 'w') as f:
                f.write('#!/bin/bash\n' + body)
        self.shell = sv.Coreshell(self.paths, fifo=os.path.join(self.tmp.name, 'fifo'))
        self.shell.tools = tl.Tools(self.shell, log=os.path.join(self.tmp.name, 'tool.log'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_and_run(self):
        tools = {t['name']: t['interactive'] for t in self.shell.handle({'v': 1, 'cmd': 'tools'})['tools']}
        self.assertEqual(tools, {'ask.sh': True, 'conf.sh': False, 'hello.sh': False, 'menu.sh': True})
        r = self.shell.handle({'v': 1, 'cmd': 'tool_run', 'name': 'ask.sh'})
        self.assertFalse(r['ok'])                          # needs a terminal
        self.assertTrue(self.shell.handle({'v': 1, 'cmd': 'tool_run', 'name': 'hello.sh'})['ok'])
        for _ in range(50):
            st = self.shell.handle({'v': 1, 'cmd': 'tool_status'})
            if st['tool']['state'] != 'running':
                break
            time.sleep(0.1)
        self.assertEqual((st['tool']['state'], st['tool']['exit']), ('done', 0))
        self.assertEqual(st['output'], ['hello from the tools page'])


class Displays(unittest.TestCase):
    def setUp(self):
        from coreshell import server as sv
        self.sv = sv
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = ln.Paths(self.tmp.name)
        prof = pf.build(SAMPLE, 'test', 'Test', '_Console/TEST', source={'rbf': 'TEST_20260101.rbf'})
        pf.save(prof, os.path.join(self.paths.systems, 'test.json'))
        os.makedirs(os.path.join(self.tmp.name, '_Console'))
        open(os.path.join(self.tmp.name, '_Console', 'TEST_20260101.rbf'), 'wb').close()
        for n in ('MiSTer.ini', 'MiSTer_YPbP.ini', 'MiSTer_RGsB.ini'):
            open(os.path.join(self.tmp.name, n), 'w').close()
        self.shell = sv.Coreshell(self.paths, fifo=os.path.join(self.tmp.name, 'fifo'))
        self.slot = 0
        ln_active = ln.active_ini_slot
        self.addCleanup(setattr, ln, 'active_ini_slot', ln_active)
        ln.active_ini_slot = lambda mem='/dev/mem': self.slot

    def tearDown(self):
        self.tmp.cleanup()

    def q(self, **req):
        req.setdefault('v', 1)
        r = self.shell.handle(req)
        self.assertTrue(r['ok'], r)
        return r

    def test_alt_ini_names_and_sets(self):
        self.assertEqual(ln.alt_ini_names(self.paths), ['MiSTer_RGsB.ini', 'MiSTer_YPbP.ini'])
        self.assertEqual(ln.active_ini(self.paths, 2), 'MiSTer_YPbP.ini')
        self.assertEqual(ln.display_set(self.paths, 'MiSTer_YPbP.ini'), 'MiSTer_YPbP')
        with open(os.path.join(self.paths.home, 'displays.json'), 'w') as f:
            json.dump({'MiSTer_RGsB.ini': 'crt', 'mister_ypbp.ini': 'crt'}, f)
        self.assertEqual(ln.display_set(self.paths, 'MiSTer_YPbP.ini'), 'crt')

    def test_crt_and_hdmi_keep_their_own_video_settings(self):
        os.makedirs(self.paths.home, exist_ok=True)
        with open(os.path.join(self.paths.home, 'displays.json'), 'w') as f:
            json.dump({'MiSTer.ini': 'hdmi', 'MiSTer_RGsB.ini': 'crt'}, f)
        self.q(cmd='set', profile='test', key='Stereo Mix', value='50%')               # everywhere
        self.q(cmd='set', profile='test', key='Scandoubler Fx', value='HQ2x', display='hdmi')
        self.q(cmd='set', profile='test', key='Scandoubler Fx', value='CRT 50%', display='crt')
        self.slot = 0
        m = {i['key']: i for i in self.q(cmd='menu', profile='test')['menu']}
        self.assertEqual(m['Audio & Video/Scandoubler Fx']['value'], 'HQ2x')
        self.assertTrue(m['Audio & Video/Scandoubler Fx']['per_display'])
        self.assertEqual(m['Stereo Mix']['value'], '50%')
        self.slot = 1                                                                 # MiSTer_RGsB.ini
        self.assertEqual(self.q(cmd='hello')['display'], 'crt')
        m = {i['key']: i for i in self.q(cmd='menu', profile='test')['menu']}
        self.assertEqual(m['Audio & Video/Scandoubler Fx']['value'], 'CRT 50%')
        rom = os.path.join(self.tmp.name, 'games', 'TEST', 'g.sfc')
        p = self.q(cmd='launch', profile='test', rom=rom)['plan']
        self.assertEqual((p['ini'], p['display'], p['mode']), ('MiSTer_RGsB.ini', 'crt', 'exact'))
        n = int.from_bytes(open(os.path.join(self.paths.config, 'TEST_v3.CFG'), 'rb').read(), 'little')
        self.assertEqual((n >> 9) & 7, 3)                                             # CRT 50%

    def test_main_read_from_active_ini(self):
        with open(os.path.join(self.tmp.name, 'MiSTer_RGsB.ini'), 'w') as f:
            f.write('[TEST]\nmain=MiSTer_crt\n')
        self.slot = 1
        self.assertEqual(ln.ini_main(self.paths, ['TEST']), 'MiSTer_crt')
        self.slot = 0
        self.assertEqual(ln.ini_main(self.paths, ['TEST']), 'MiSTer')


    def test_ini_list_follows_the_menus_main(self):
        r = self.tmp.name
        for n in ('MiSTer_SVID.ini', 'MiSTer_RGHV.ini'):
            open(os.path.join(r, n), 'w').close()
        os.makedirs(os.path.join(r, 'INI', 'sub.ini'))                      # (a folder: not an INI)
        for n in ('b.ini', 'a.ini', '.hidden.ini', 'notes.txt'):
            open(os.path.join(r, 'INI', n), 'w').close()
        # MiSTer's Main with the INI folder: 3 from the root in name order, then the folder's (slots 4 on)
        self.assertEqual(ln.alt_ini_names(self.paths, (3, True)),
                         ['MiSTer_RGHV.ini', 'MiSTer_RGsB.ini', 'MiSTer_SVID.ini', 'INI/a.ini', 'INI/b.ini'])
        # Phosphor's from before the INI folder: 4 from the root (the SuperStation's four video profiles)
        self.assertEqual(ln.alt_ini_names(self.paths, (4, False)),
                         ['MiSTer_RGHV.ini', 'MiSTer_RGsB.ini', 'MiSTer_SVID.ini', 'MiSTer_YPbP.ini'])
        # an older Main: no folder, the first 3 found
        self.assertEqual(len(ln.alt_ini_names(self.paths, (3, False))), 3)
        # fewer root INIs than slots: the folder's still start after them, as in Main
        for n in ('MiSTer_SVID.ini', 'MiSTer_RGHV.ini', 'MiSTer_YPbP.ini'):
            os.remove(os.path.join(r, n))
        names = ln.alt_ini_names(self.paths, (3, True))
        self.assertEqual(names, ['MiSTer_RGsB.ini', '', '', 'INI/a.ini', 'INI/b.ini'])
        ln._INI_RULE.clear()
        main = os.path.join(r, 'MiSTer')
        with open(main, 'wb') as f:
            f.write(b'xx /tmp/phosphor-main.sock\0 Config: %s\0yy')
        self.assertEqual(ln.ini_rule(main), (3, True))                            # (Phosphor's follows MiSTer's)
        with open(os.path.join(r, 'oldphos'), 'wb') as f:
            f.write(b'xx /tmp/phosphor-main.sock\0 Config:\0yy')
        self.assertEqual(ln.ini_rule(os.path.join(r, 'oldphos')), (4, False))
        with open(os.path.join(r, 'old'), 'wb') as f:
            f.write(b' Config:\0')
        self.assertEqual(ln.ini_rule(os.path.join(r, 'old')), (3, False))
        orig = ln.ini_rule
        self.addCleanup(setattr, ln, 'ini_rule', orig)
        ln.ini_rule = lambda path: (3, True)
        self.assertEqual(ln.ini_list(self.paths), ['MiSTer.ini', 'MiSTer_RGsB.ini', 'INI/a.ini', 'INI/b.ini'])
        self.slot = 4
        self.assertEqual(ln.active_ini(self.paths, 4), 'INI/a.ini')
        self.assertEqual(ln.active_ini(self.paths, 2), 'MiSTer.ini')               # (an empty slot)
        mem = os.path.join(r, 'mem')
        with open(mem, 'wb') as f:
            f.truncate(ln.ALTCFG_PHYS + 0x1000)
        ln.set_ini(self.paths, 'INI/b.ini', fifo=os.path.join(r, 'cmd'), mem=mem, corename=os.path.join(r, 'none'))
        with open(mem, 'rb') as f:
            f.seek(ln.ALTCFG_PHYS + 0xF04)
            self.assertEqual(f.read(4), bytes([0x34, 0x99, 0xBA, 5]))

    def test_set_ini(self):
        mem = os.path.join(self.tmp.name, 'mem')
        with open(mem, 'wb') as f:
            f.truncate(ln.ALTCFG_PHYS + 0x1000)
        fifo = os.path.join(self.tmp.name, 'cmd')
        self.assertEqual(ln.ini_list(self.paths), ['MiSTer.ini', 'MiSTer_RGsB.ini', 'MiSTer_YPbP.ini'])
        self.assertEqual(ln.set_ini(self.paths, 'mister_ypbp.ini', fifo=fifo, mem=mem,
                                    corename=os.path.join(self.tmp.name, 'none')), 'MiSTer_YPbP.ini')
        with open(mem, 'rb') as f:
            f.seek(ln.ALTCFG_PHYS + 0xF04)
            self.assertEqual(f.read(4), bytes([0x34, 0x99, 0xBA, 2]))
        self.assertIn('load_core', open(fifo).read())
        with self.assertRaises(KeyError):
            ln.set_ini(self.paths, 'MiSTer_Nope.ini', fifo=fifo, mem=mem)

class IniCheck(unittest.TestCase):
    def setUp(self):
        from coreshell import inicheck as ic
        self.ic = ic
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        new = b'\x00'.join([b'MAIN', b'VIDEO_MODE', b'HDMI_CEC', b'HDMI_CEC_POWER_ON', b'HDR', b'VSYNC_ADJUST'])
        old = b'\x00'.join([b'MAIN', b'VIDEO_MODE', b'VSYNC_ADJUST'])
        for n, d in (('MiSTer', new), ('MiSTer_Quake', old)):
            with open(os.path.join(self.root, n), 'wb') as f:
                f.write(b'\x7fELF\x00' + d + b'\x00')
        self.ini = os.path.join(self.root, 'MiSTer.ini')
        with open(self.ini, 'w', newline='') as f:
            f.write('[MiSTer]\r\nvideo_mode=8\r\nhdmi_cec=0             ; CEC off\r\n'
                    'hdmi_cec_power_on=1\r\nhdr=1\r\ntypo_option=1\r\n'
                    '[Quake]\r\nmain=MiSTer_Quake\r\nhdmi_cec=0\r\n')

    def tearDown(self):
        self.tmp.cleanup()

    def test_check(self):
        report, mains = self.ic.check(self.root)
        self.assertEqual(mains, ['MiSTer', 'MiSTer_Quake'])
        hits = {h['key']: h for h in report['MiSTer.ini']}
        self.assertEqual(set(hits), {'hdmi_cec', 'hdmi_cec_power_on', 'hdr'})   # typo skipped
        self.assertTrue(hits['hdmi_cec']['default'])
        self.assertTrue(hits['hdmi_cec_power_on']['default'])    # Main's default is 1
        self.assertFalse(hits['hdr']['default'])                 # a real setting
        self.assertEqual(hits['hdr']['unknown_to'], ['MiSTer_Quake'])

    def test_fix_and_undo(self):
        before = open(self.ini, newline='').read()
        changed = self.ic.fix(self.root, os.path.join(self.root, 'bk'))
        self.assertEqual(sorted(changed['MiSTer.ini']), ['hdmi_cec', 'hdmi_cec_power_on'])
        after = open(self.ini, newline='').read()
        self.assertIn(';hdmi_cec=0             ; CEC off ; coreshell: unknown to MiSTer_Quake\r\n', after)
        self.assertIn('\r\nhdr=1\r\n', after)                 # real setting untouched
        self.assertIn('[Quake]\r\nmain=MiSTer_Quake\r\nhdmi_cec=0', after)   # other sections untouched
        self.assertEqual(len(os.listdir(os.path.join(self.root, 'bk'))), 1)
        report, _ = self.ic.check(self.root)
        self.assertEqual([h['key'] for h in report['MiSTer.ini']], ['hdr'])
        self.ic.undo(self.root, None)
        self.assertEqual(open(self.ini, newline='').read(), before)

    def test_launch_view(self):
        before = open(self.ini, newline='').read()
        state, view = os.path.join(self.root, 'state'), os.path.join(self.root, 'view')
        self.assertEqual(self.ic.view_for_launch(self.root, 'MiSTer.ini', 'MiSTer', state, view, bind=False), [])
        keys = self.ic.view_for_launch(self.root, 'MiSTer.ini', 'MiSTer_Quake', state, view, bind=False)
        self.assertEqual(sorted(keys), ['hdmi_cec', 'hdmi_cec_power_on'])     # not hdr (a real setting)
        self.assertEqual(open(self.ini, newline='').read(), before)          # the user's INI: never written
        seen = open(os.path.join(view, 'MiSTer.ini'), newline='').read()
        self.assertIn(';hdmi_cec=0             ; CEC off ; coreshell: while MiSTer_Quake starts\r\n', seen)
        self.assertIn('\r\nhdr=1\r\n', seen)
        self.assertIn('[Quake]\r\nmain=MiSTer_Quake\r\nhdmi_cec=0', seen)
        saved = json.load(open(os.path.join(state, 'ini_fixes.json')))
        self.assertEqual(saved['MiSTer_Quake']['unknown'], ['HDMI_CEC', 'HDMI_CEC_POWER_ON', 'HDR'])
        self.ic.restore(self.root, view)
        self.assertEqual(os.listdir(view), [])


class RealProfiles(unittest.TestCase):
    """Every shipped profile loads and its menu makes sense."""

    def test_profiles(self):
        d = os.path.join(ROOT, 'systems')
        names = [n for n in os.listdir(d) if n.endswith('.json')]
        self.assertTrue(names)
        for n in names:
            p = pf.load(os.path.join(d, n))
            if p['rom']:                      # None = launch-only (ChipID, ports...)
                self.assertTrue(p['rom']['exts'], n)
            keys = [pf.option_key(o) for o in p['menu']]
            self.assertEqual(len(keys), len(set(keys)), n)
            for o in p['menu']:
                s, e = o['bits']
                self.assertLessEqual(len(o['values']), 1 << (e - s + 1), (n, o['name']))
                self.assertIn(o['default'], o['values'])


if __name__ == '__main__':
    unittest.main()
