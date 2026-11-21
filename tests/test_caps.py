import json
import os
import shutil
import tempfile
import unittest

from coreshell import caps as cp


class CapsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'fat')
        self.home = os.path.join(self.root, 'coreshell')
        os.makedirs(os.path.join(self.home, 'caps'))
        self.usb = os.path.join(self.tmp, 'usb')
        os.makedirs(self.usb)
        shutil.copy(os.path.join(cp.HERE, 'caps', 'dvd_video.json'), os.path.join(self.home, 'caps'))
        self.put('drive', {'name': 'Drive', 'kind': 'hardware', 'any': [{'glob': os.path.join(self.tmp, 'sr*')}]})
        self.put('ir', {'name': 'IR', 'kind': 'hardware', 'any': [{'usb': '1c4f:0002'}]})
        self.put('dvdsec', {'name': 'DVD section', 'all': [{'ini': 'DVD'}]})
        # dvd_video needs disc_drive: point it at ours
        e = json.load(open(os.path.join(self.home, 'caps', 'dvd_video.json')))
        e['needs'] = ['drive']
        self.put('dvd_video', e)
        self.events = []
        self.c = cp.Caps(self.root, self.home, emit=lambda n, **k: self.events.append((n, k)), usb_sys=self.usb)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def put(self, cid, e):
        with open(os.path.join(self.home, 'caps', cid + '.json'), 'w') as f:
            json.dump(e, f)

    def touch(self, rel):
        p = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w').close()

    def test_nothing_installed(self):
        c = self.c.refresh()
        self.assertFalse(c['dvd_video']['present'])
        self.assertFalse(c['drive']['present'])
        self.assertIsNone(c['dvd_video']['launch']['rbf'])

    def test_addon_needs_its_hardware(self):
        self.touch('MiSTer_DVDcss')
        self.touch('_Other/DVD_20261001.rbf')
        self.touch('_Other/DVD_20261102.rbf')
        self.assertFalse(self.c.refresh()['dvd_video']['present'])        # (no drive)
        open(os.path.join(self.tmp, 'sr0'), 'w').close()
        c = self.c.refresh()
        self.assertTrue(c['dvd_video']['present'])
        self.assertTrue(c['dvd_video']['launch']['rbf'].endswith('DVD_20261102.rbf'))   # the newest
        self.assertEqual(self.events[-1][0], 'caps')
        self.assertIn('dvd_video', self.events[-1][1]['present'])

    def test_usb_and_ini(self):
        d = os.path.join(self.usb, '1-1')
        os.makedirs(d)
        open(os.path.join(d, 'idVendor'), 'w').write('1c4f\n')
        open(os.path.join(d, 'idProduct'), 'w').write('0002\n')
        with open(os.path.join(self.root, 'MiSTer.ini'), 'w') as f:
            f.write('[MiSTer]\nvideo_mode=0\n\n[DVD]\nmain=MiSTer_DVDcss\n')
        c = self.c.refresh()
        self.assertTrue(c['ir']['present'])
        self.assertTrue(c['dvdsec']['present'])

    def test_first_look_is_no_event_and_state_file_written(self):
        self.c.refresh()
        self.assertEqual(self.events, [])
        with open(os.path.join(self.home, 'state', 'caps.json')) as f:
            self.assertIn('dvd_video', json.load(f)['caps'])

    def test_shipped_entries_load(self):
        e = cp.entries(cp.HERE)
        self.assertEqual(e['dvd_video'].get('needs'), ['disc_drive'])
        self.assertEqual(e['disc_games'].get('needs'), ['disc_drive'])
        for cid in ('disc_drive', 'disc_games', 'dvd_video', 'dock_ir', 'zaparoo', 'retroachievements'):
            self.assertIn(cid, e)
            self.assertTrue(e[cid].get('kind'))
            self.assertTrue(e[cid].get('any') or e[cid].get('all'))


if __name__ == '__main__':
    unittest.main()
