"""bin/restore-points (ported from Phosphor's update-guard): save, list, rollback and undo on a fake SD card.

Run on /dev/shm: restore points refuse a folder on the root file system (not a mounted drive), as on the MiSTer.
Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, '..', 'bin', 'restore-points')
SHM = '/dev/shm'


def _mounted(path):
    try:
        with open('/proc/mounts') as f:
            return any(l.split()[1] == path for l in f)
    except OSError:
        return False


@unittest.skipUnless(shutil.which('bash') and _mounted(SHM), 'needs bash and /dev/shm')
class RestorePoints(unittest.TestCase):
    def setUp(self):
        self.sd = tempfile.mkdtemp(dir=SHM)
        self.rootfs = tempfile.mkdtemp(dir=SHM)
        os.makedirs(os.path.join(self.sd, 'linux'))
        with open(os.path.join(self.rootfs, 'MiSTer.version'), 'w') as f:
            f.write('260912\n')
        self.put('MiSTer', b'main-1')
        self.put('menu.rbf', b'menu-1')
        self.put('linux/zImage_dtb', b'kernel-1')

    def tearDown(self):
        shutil.rmtree(self.sd, ignore_errors=True)
        shutil.rmtree(self.rootfs, ignore_errors=True)

    def put(self, rel, data):
        with open(os.path.join(self.sd, rel), 'wb') as f:
            f.write(data)

    def get(self, rel):
        with open(os.path.join(self.sd, rel), 'rb') as f:
            return f.read()

    def run_rp(self, *args, update_guard=None):
        env = dict(os.environ, CORESHELL_SD=self.sd, CORESHELL_ROOTFS=self.rootfs,
                   CORESHELL_UPDATE_GUARD=update_guard or os.path.join(self.sd, 'none'))
        return subprocess.run(['bash', SCRIPT] + list(args), env=env, capture_output=True, text=True, timeout=60)

    def test_save_rollback_undo(self):
        r = self.run_rp('save-point', 'before-update-all')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('Saved restore point', r.stdout)
        # an update replaces Main and the menu core
        self.put('MiSTer', b'main-two')
        self.put('menu.rbf', b'menu-two')
        r = self.run_rp('save-point', 'after')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.run_rp('points').stdout.splitlines()), 2)
        # rollback goes to the set before the one installed
        r = self.run_rp('rollback')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.get('MiSTer'), b'main-1')
        self.assertEqual(self.get('menu.rbf'), b'menu-1')
        self.assertEqual(self.get('linux/zImage_dtb'), b'kernel-1')
        undo = [l.split()[-1] for l in r.stdout.splitlines() if l.startswith('To undo:')]
        self.assertTrue(undo and undo[0].endswith('-pre-rollback'), r.stdout)
        r = self.run_rp('rollback', undo[0])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.get('MiSTer'), b'main-two')
        self.assertEqual(self.get('menu.rbf'), b'menu-two')

    def test_damaged_copy_changes_nothing(self):
        self.run_rp('save-point', 'one')
        self.put('MiSTer', b'main-two')
        store = os.path.join(self.sd, 'coreshell', 'restore-points', 'store')
        for n in os.listdir(store):
            if n.endswith('-MiSTer'):
                with open(os.path.join(store, n), 'wb') as f:
                    f.write(b'broken')
        r = self.run_rp('rollback')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('damaged', r.stdout)
        self.assertEqual(self.get('MiSTer'), b'main-two')

    def test_update_guard_takes_over(self):
        ug = os.path.join(self.sd, 'ug.sh')
        with open(ug, 'w') as f:
            f.write('#!/bin/sh\necho "update-guard $*"\n')
        os.chmod(ug, 0o755)
        r = self.run_rp('save-point', 'x', update_guard=ug)
        self.assertEqual(r.stdout.strip(), 'update-guard save-point x')
        self.assertFalse(os.path.exists(os.path.join(self.sd, 'coreshell', 'restore-points')))


if __name__ == '__main__':
    unittest.main()
