# Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""kernel.py: the kernel's display against the known-good state (a stand-in probe: no MiSTer needed)."""
import json
import os
import shutil
import tempfile
import unittest

from coreshell import kernel as krn

GOOD = {'kernel': '6.18.38-MiSTer', 'linux': '260912', 'fb.device': True, 'fb.ioctl': True, 'fb.xres': 1920,
        'fb.yres': 1080, 'fb.bpp': 32, 'fb.format': '8888', 'fb.stride': 7680, 'fb.smem': '0x22001000+8294400',
        'fb.smem_seen': '0x22001000+8294400', 'mfb.params': 'format,height,mode,stride,width',
        'mfb.mode': '8888 1 1920 1080 7680', 'mem.reserved': 'mem=511M memmap=513M$511M', 'dev.mem': True,
        'dev.uinput': True, 'modules': 'btusb,rtw88_core,xpad'}


class KernelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.now = dict(GOOD)
        self.events = []
        self.k = krn.Kernel(os.path.join(self.tmp, 'coreshell'), self.tmp, prober=lambda: dict(self.now),
                            emit=lambda t, **f: self.events.append((t, f)))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def disp(self):
        with open(os.path.join(self.k.dir, 'display.json')) as f:
            return json.load(f)

    def test_first_boot_is_known_good(self):
        self.assertEqual(self.k.check()['state'], 'ok')
        self.assertEqual(self.disp()['stride'], 7680)
        self.assertEqual(self.k.check()['state'], 'ok')

    def test_status_as_last_checked(self):
        # a fresh instance (each command line run is one) says what the last check found, not "unknown"
        self.k.check()
        again = krn.Kernel(os.path.join(self.tmp, 'coreshell'), self.tmp, prober=lambda: dict(self.now))
        self.assertEqual(again.status['state'], 'ok')

    def test_new_kernel_new_values_adapted(self):
        self.k.check()
        self.now.update({'kernel': '6.20.1-MiSTer', 'fb.stride': 7936, 'fb.format': '8888', 'mfb.mode': '8888 1 1920 1080 7936'})
        st = self.k.check()
        self.assertEqual(st['state'], 'adapted')
        self.assertIn('fb.stride', [c['check'] for c in st['changes']])
        self.assertEqual(self.disp()['stride'], 7936)               # frontends read the new stride
        self.assertEqual(self.events[-1][0], 'kernel')
        self.assertEqual(self.k.check()['state'], 'ok')             # the new state is the known-good one now
        self.assertTrue(os.listdir(os.path.join(self.k.dir, 'history')))

    def test_missing_interface_degraded_known_good_kept(self):
        self.k.check()
        self.now.update({'kernel': '7.0.0-MiSTer', 'mfb.params': None, 'mfb.mode': None, 'fb.stride': 8000})
        st = self.k.check()
        self.assertEqual(st['state'], 'degraded')
        self.assertEqual(st['missing'], ['mfb.params'])
        self.assertTrue(st['advice'])
        self.assertEqual(self.disp()['stride'], 7680)               # nothing written for the broken state
        with open(os.path.join(self.k.dir, 'state')) as f:
            self.assertEqual(f.read().strip(), 'degraded')         # (corehost reads this)
        self.now = dict(GOOD)                                       # (rolled back)
        self.assertEqual(self.k.check()['state'], 'ok')

    def test_framebuffer_moved_is_missing(self):
        self.k.check()
        self.now.update({'fb.smem': None, 'fb.smem_seen': '0x30000000+8294400'})
        self.assertEqual(self.k.check()['missing'], ['fb.smem'])

    def test_driver_gone_is_a_note(self):
        self.k.check()
        full = self.now['modules']
        self.now['modules'] = 'btusb,xpad'          # (Wi-Fi not loaded yet: Coreshell's start can beat it at boot)
        st = self.k.check()
        self.assertEqual(st['state'], 'ok')         # said, not a change of the display
        self.assertEqual(st['notes'][0]['was'], 'rtw88_core')
        with open(os.path.join(self.k.dir, 'known_good.json')) as f:
            self.assertEqual(json.load(f)['modules'], full)          # the full list kept
        self.now['modules'] = full
        self.assertEqual(self.k.check()['state'], 'ok')
        self.assertNotIn('notes', self.k.status)

    def _ini(self, text=None):
        p = os.path.join(self.tmp, 'downloader.ini')
        if text is not None:
            with open(p, 'w') as f:
                f.write(text)
        with open(p) as f:
            return f.read()

    def test_hold_and_unhold_put_downloader_ini_back(self):
        before = '[mister]\nupdate_linux = true\ndownloader_retries = 3\n'
        self._ini(before)
        self.k.hold()
        self.assertIn('update_linux = false', self._ini())
        self.k.unhold()
        self.assertEqual(self._ini(), before)

    def test_unhold_keeps_changes_made_while_held(self):
        self._ini('[mister]\nupdate_linux = true\n\n[distribution_mister]\ndb_url = a\n')
        self.k.hold()
        self._ini(self._ini() + '\n[jtcores]\ndb_url = j\n')          # (Update All adds a database meanwhile)
        self.k.unhold()
        t = self._ini()
        self.assertIn('[jtcores]', t)
        self.assertIn('update_linux = true', t)
        self.assertNotIn('update_linux = false', t)

    def test_hold_with_no_key_removes_it_again(self):
        before = '[mister]\ndownloader_retries = 3\n'
        self._ini(before)
        self.k.hold()
        self.assertEqual(krn.Kernel.ini_value(self._ini(), 'mister', 'update_linux'), 'false')
        self.k.unhold()
        self.assertEqual(self._ini(), before)

    def test_hold_twice_and_backups(self):
        self._ini('[mister]\nupdate_linux = true\n')
        self.k.hold()
        self.assertEqual(self.k.hold()['note'], 'already held')
        self.k.unhold()
        self.assertEqual(self.k.unhold()['note'], 'not held by Coreshell')
        self.assertEqual(len(os.listdir(os.path.join(self.k.dir, 'backups'))) >= 1, True)

    def test_update_guard_hold_is_left_alone(self):
        self._ini('[mister]\nupdate_linux = false\n')
        d = os.path.join(self.tmp, 'phosphor', 'state')
        os.makedirs(d)
        with open(os.path.join(d, 'update-guard.state'), 'w') as f:
            f.write('hold_set=1\nhold_before=true\n')
        self.assertIn('update-guard', self.k.hold()['note'])
        self.assertEqual(self._ini(), '[mister]\nupdate_linux = false\n')

    def test_ini_put_matches_update_guard(self):
        put = krn.Kernel.ini_put
        self.assertEqual(put('', 'mister', 'update_linux', 'false'), '\n[mister]\nupdate_linux = false\n')
        self.assertEqual(put('[mister]\r\nx = 1\r\n[other]\n', 'mister', 'update_linux', 'false'),
                         '[mister]\nx = 1\nupdate_linux = false\n[other]\n')

    def test_probe_runs_here(self):
        p = krn.probe()                    # (on a PC: no framebuffer, but no crash)
        self.assertIn('kernel', p)


if __name__ == '__main__':
    unittest.main()
