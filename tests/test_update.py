"""quirks.py (built-in tables, quirks.json added) and update.py (a release installed, the user's files untouched,
a newer local profile kept, rolled back)."""
import io, json, os, shutil, tarfile, tempfile, unittest
from unittest import mock
from coreshell import quirks, update as up

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Quirks(unittest.TestCase):
    def test_builtin_and_added(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, 'q.json')
        self.assertEqual(quirks.card_no_picker(p), {'PSX'})
        json.dump({'card_no_picker': ['Saturn'], 'core_skip': '^(menu|foo)'}, open(p, 'w'))
        self.assertEqual(quirks.card_no_picker(p), {'PSX', 'Saturn'})
        self.assertTrue(quirks.core_skip(p).match('Foo_2026.rbf'))
        open(p, 'w').write('{broken')
        self.assertEqual(quirks.card_no_picker(p), {'PSX'})                 # (a broken file: built-in)


def _release(files):
    b = io.BytesIO()
    with tarfile.open(fileobj=b, mode='w:gz') as t:
        for name, data in files.items():
            data = data.encode() if isinstance(data, str) else data
            i = tarfile.TarInfo(name)
            i.size = len(data)
            t.addfile(i, io.BytesIO(data))
    return b.getvalue()


class Install(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.home = os.path.join(self.root, 'coreshell')
        shutil.copytree(os.path.join(SRC, 'coreshell'), os.path.join(self.home, 'coreshell'),
                        ignore=shutil.ignore_patterns('__pycache__'))
        for p, d in {'settings/psx.json': '{"mine": 1}', 'state/frontend.json': '{}', 'VERSION': 'v2026.11.01',
                     'systems/nes.json': json.dumps({'rbf': '_Console/NES', 'source': {'rbf': 'NES_20261101.rbf'}}),
                     'systems/snes.json': json.dumps({'rbf': '_Console/SNES', 'source': {'rbf': 'SNES_20260101.rbf'}}),
                     'mains/mains.json': json.dumps({'aaa': {'name': 'MiSTer', 'shim': 'local_shim'}})}.items():
            os.makedirs(os.path.dirname(os.path.join(self.home, p)), exist_ok=True)
            open(os.path.join(self.home, p), 'w').write(d)
        os.makedirs(os.path.join(self.root, '_Console'))
        open(os.path.join(self.root, '_Console', 'NES_20261101.rbf'), 'w').write('x')   # (re-dumped here)
        open(os.path.join(self.root, '_Console', 'SNES_20261001.rbf'), 'w').write('x')

    def rel(self, extra=None):
        files = {}
        for base, _, fs in os.walk(os.path.join(SRC, 'coreshell')):
            if '__pycache__' in base:
                continue
            for f in fs:
                files[os.path.relpath(os.path.join(base, f), SRC)] = open(os.path.join(base, f), 'rb').read()
        files.update({'VERSION': 'v2026.11.20', 'quirks/quirks.json': '{"card_no_picker": ["X"]}',
                      'bin/corepatch': 'ELF', 'coreshell/NEW_MARK': 'new', 'systems/overrides/x.json': '{}',
                      'systems/nes.json': json.dumps({'rbf': '_Console/NES', 'source': {'rbf': 'NES_20261001.rbf'}}),
                      'systems/snes.json': json.dumps({'rbf': '_Console/SNES', 'source': {'rbf': 'SNES_20261001.rbf'}}),
                      'mains/mains.json': json.dumps({'bbb': {'name': 'MiSTer_RA', 'shim': 'ra_shim'}})})
        files.update(extra or {})
        return _release(files)

    def test_install_and_rollback(self):
        done = up.install(self.home, self.rel())
        h = lambda p: open(os.path.join(self.home, p)).read()
        self.assertEqual(h('VERSION'), 'v2026.11.20')
        self.assertEqual(h('coreshell/NEW_MARK'), 'new')
        self.assertTrue(os.access(os.path.join(self.home, 'bin/corepatch'), os.X_OK))
        self.assertEqual(h('settings/psx.json'), '{"mine": 1}')                  # (the user's: untouched)
        self.assertIn('NES_20261101', h('systems/nes.json'))                     # (re-dumped here, newer: kept)
        self.assertIn('SNES_20261001', h('systems/snes.json'))                   # (the release's: matches the core)
        self.assertEqual(set(json.loads(h('mains/mains.json'))), {'aaa', 'bbb'})
        self.assertEqual(h('systems/overrides/x.json'), '{}')
        self.assertTrue(any(d.startswith('systems/') for d in done))
        # rolled back to a backup made before
        os.makedirs(os.path.join(self.home, 'backups', 'update'))
        shutil.copy(os.path.join(self.home, 'VERSION'), os.path.join(self.home, 'VERSION.x'))
        open(os.path.join(self.home, 'VERSION'), 'w').write('v2026.11.01')
        b = up._backup(self.home)
        open(os.path.join(self.home, 'VERSION'), 'w').write('v2026.11.20')
        up.rollback(self.home, log=lambda *a: None)
        self.assertEqual(h('VERSION'), 'v2026.11.01')
        self.assertFalse(os.path.exists(b))

    def test_rollback_and_pruning_go_by_time_not_name(self):
        d = os.path.join(self.home, 'backups', 'update')
        os.makedirs(d)
        # v2026.11.18_ sorts after v2026.11.18.2_ by name, but it's the oldest
        up.install(self.home, self.rel())
        for name in ('v2026.11.18_20261118-100000', 'v2026.11.18.1_20261118-110000', 'v2026.11.18.2_20261118-120000'):
            open(os.path.join(self.home, 'VERSION'), 'w').write(name.split('_')[0])
            os.rename(up._backup(self.home), os.path.join(d, name + '.tar.gz'))
        open(os.path.join(self.home, 'VERSION'), 'w').write('v2026.11.18.3')
        self.assertEqual(up._backups(d)[-1], 'v2026.11.18.2_20261118-120000.tar.gz')
        up.rollback(self.home, log=lambda *a: None)
        self.assertEqual(open(os.path.join(self.home, 'VERSION')).read(), 'v2026.11.18.2')   # the newest, not .18
        os.rename(up._backup(self.home), os.path.join(d, 'v2026.11.18.2_20261118-130000.tar.gz'))
        up._backup(self.home)                      # a 4th: the oldest by time is the one dropped
        self.assertNotIn('v2026.11.18_20261118-100000.tar.gz', os.listdir(d))
        self.assertIn('v2026.11.18.1_20261118-110000.tar.gz', os.listdir(d))

    def test_bad_release_changes_nothing(self):
        before = open(os.path.join(self.home, 'coreshell', '__main__.py')).read()
        with self.assertRaises(RuntimeError):
            up.install(self.home, _release({'README.md': 'x'}))
        with self.assertRaises(RuntimeError):
            up.install(self.home, self.rel({'coreshell/server.py': 'this is not python'}))
        with self.assertRaises(RuntimeError):
            up.install(self.home, _release({'../escape': 'x'}))
        self.assertEqual(open(os.path.join(self.home, 'coreshell', '__main__.py')).read(), before)
        self.assertEqual(open(os.path.join(self.home, 'VERSION')).read(), 'v2026.11.01')

    def test_newer(self):
        self.assertTrue(up._newer('v2026.11.20', 'v2026.11.01'))
        self.assertTrue(up._newer('v2026.11.20.1', 'v2026.11.20'))
        self.assertFalse(up._newer('v2026.11.01', 'v2026.11.01'))
        self.assertTrue(up._newer('v2026.11.01', '0'))

    def test_main_check(self):
        with mock.patch.object(up, 'release', return_value={'tag_name': 'v2026.11.20', 'assets': []}):
            self.assertEqual(up.main(['--check'], home=self.home), 0)



class Restart(unittest.TestCase):
    """update's restart(): the server under its own name (core-shell), or as python3 -m coreshell serve; never the
    supervisor, never itself."""

    def test_finds_the_server(self):
        procs = {'100': b'core-shell\x00\x00\x00\x00', '101': b'sh\x00/media/fat/coreshell/scripts/core-shell-supervisor\x00',
                 '102': b'/usr/bin/python3\x00-m\x00coreshell\x00serve\x00', '103': b'/usr/bin/python3\x00-m\x00coreshell\x00update\x00',
                 str(os.getpid()): b'core-shell\x00'}
        real_open = open

        def fake_open(path, *a, **k):
            if path.startswith('/proc/') and path.endswith('/cmdline'):
                return io.BytesIO(procs[path.split('/')[2]])
            return real_open(path, *a, **k)
        with mock.patch.object(up.os, 'listdir', return_value=list(procs) + ['self']), \
                mock.patch('builtins.open', fake_open), mock.patch.object(up.os, 'kill') as kill:
            up.restart(log=lambda m: None)
        self.assertEqual(sorted(c.args for c in kill.call_args_list), [(100, 15), (102, 15)])


if __name__ == '__main__':
    unittest.main()
