"""raviewer.py: RetroAchievements for the frontend from the RA Viewer add-on (its saved data, its own fetch), hidden."""
import json
import os
import tempfile
import time
import unittest

from coreshell import raviewer as rv

# a stand-in for Anime0t4ku's ra_viewer.sh: the same layout (its Python in a heredoc it writes to $HELPER), the same
# functions; it "fetches" by writing the cache files the real one gets from retroachievements.org
FAKE = r'''#!/bin/sh
TITLE="RetroAchievements Viewer for MiSTer by Anime0t4ku"
BASE="%(base)s"
HELPER="$BASE/ra_viewer.py"
cat > "$HELPER" <<'PYEOF'
#!/usr/bin/env python3
import json
from pathlib import Path
BASE = Path("%(base)s")
CACHE = BASE / "cache"
GAME_CACHE = CACHE / "games"
for p in (CACHE, GAME_CACHE):
    p.mkdir(parents=True, exist_ok=True)
CALLS = []

def get_games(force=False):
    CALLS.append(("games", force))
    data = {"fetched_at": 1700000000, "username": "f3bandit", "Results": [
        {"GameID": 1469, "Title": "Dr. Mario", "ConsoleName": "NES/Famicom", "MaxPossible": 70, "NumAwarded": 3,
         "NumAwardedHardcore": 0, "HighestAwardKind": None, "MostRecentAwardedDate": "2026-11-20T18:00:00+00:00"},
        {"GameID": 508, "Title": "Tetris", "ConsoleName": "Game Boy", "MaxPossible": 13, "NumAwarded": 13,
         "NumAwardedHardcore": 13, "HighestAwardKind": "mastered", "MostRecentAwardedDate": "2026-01-01T00:00:00+00:00"}]}
    (CACHE / "games.json").write_text(json.dumps(data))
    return data

def get_game(game_id, force=False):
    CALLS.append(("game", int(game_id), force))
    data = {"Title": "Dr. Mario", "ConsoleName": "NES/Famicom", "Achievements": {
        "2": {"ID": 2, "Title": "Second", "Description": "b", "Points": 10, "DisplayOrder": 2, "DateEarned": None},
        "1": {"ID": 1, "Title": "Virus Buster 0 - Low", "Description": "a", "Points": 5, "DisplayOrder": 1,
              "DateEarned": "2026-11-20 18:00:00", "DateEarnedHardcore": None}}}
    (GAME_CACHE / ("%%d.json" %% int(game_id))).write_text(json.dumps(data))
    return data

if __name__ == "__main__":
    import curses
PYEOF
echo "its screens would start here"
'''


class RAViewer(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.root = self.d.name
        self.base = os.path.join(self.root, rv.BASE)
        os.makedirs(os.path.join(self.root, 'Scripts'))
        self.v = rv.RAViewer(self.root, tmp=os.path.join(self.root, 'tmp'))

    def tearDown(self):
        self.wait()                                        # (a fetch still writing the cache)
        self.d.cleanup()

    def install(self, user='f3bandit', key='abc123'):
        with open(os.path.join(self.root, rv.SCRIPT), 'w') as f:
            f.write(FAKE % {'base': self.base})
        os.makedirs(self.base, exist_ok=True)
        with open(os.path.join(self.base, 'config.ini'), 'w') as f:
            f.write('[retroachievements]\nusername=%s\napi_key=%s\n' % (user, key))

    def wait(self):
        for _ in range(100):
            if not self.v.busy:
                return
            time.sleep(0.02)
        self.fail('still fetching')

    def fake_ra(self, user_ok=True, key_ok=True):
        asked = []
        def http(method, url, params):
            asked.append((method, url.rsplit('/', 1)[-1], dict(params)))
            if url.endswith('dorequest.php'):
                return {'Success': True, 'User': 'F3bandit', 'Token': 't'} if user_ok else \
                    {'Success': False, 'Error': 'Invalid user/password combination.'}
            return {'User': 'F3bandit', 'ULID': 'x'} if key_ok else {'message': 'Unauthenticated.'}
        self.v.http = http
        return asked

    def test_setup_writes_the_viewers_config(self):
        self.install(user='', key='')
        with open(os.path.join(self.base, 'config.ini'), 'a') as f:
            f.write('\n[display]\n# its own\nlimit=20\n')
        asked = self.fake_ra()
        r = self.v.setup('f3bandit', 'pw', 'KEY123abc')
        self.wait()
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['user'], 'F3bandit')                # (as RetroAchievements spells it)
        with open(os.path.join(self.base, 'config.ini')) as f:
            text = f.read()
        self.assertIn('[retroachievements]\nusername=F3bandit\napi_key=KEY123abc\n', text)
        self.assertIn('[display]\n# its own\nlimit=20', text)
        self.assertNotIn('pw', text)
        self.assertEqual([a[1] for a in asked], ['dorequest.php', 'API_GetUserProfile.php'])
        self.assertTrue(self.v.status()['set_up'])
        self.assertEqual(self.v.status()['key_hint'], '')         # (too short to be a real key: no hint)
        rv.write_config(self.root, 'F3bandit', 'T5xtp70y6zFcc7BpQ6mFJ4WfGfmYfAC9')
        self.assertEqual(self.v.status()['key_hint'], 'fAC9')     # (its end only)
        self.assertNotIn('T5xtp', json.dumps(self.v.status()))

    def test_save_then_test(self):
        self.install(user='', key='')
        asked = self.fake_ra()
        self.assertTrue(self.v.setup('f3bandit', 'pw', 'K' * 32, check=False)['ok'])
        self.assertEqual(asked, [])                                   # (Save: nothing asked)
        with open(os.path.join(self.base, 'config.ini')) as f:
            self.assertIn('username=f3bandit\napi_key=' + 'K' * 32, f.read())
        r = self.v.test('pw')
        self.wait()
        self.assertTrue(r['ok'], r)
        self.assertEqual([a[1] for a in asked], ['dorequest.php', 'API_GetUserProfile.php'])
        self.assertFalse(self.v.test(None)['ok'])
        self.fake_ra(key_ok=False)
        self.assertFalse(self.v.test('pw')['ok'])

    def test_forget(self):
        self.install()
        with open(os.path.join(self.base, 'config.ini'), 'a') as f:
            f.write('[display]\nlimit=20\n')
        self.assertTrue(self.v.forget()['ok'])
        with open(os.path.join(self.base, 'config.ini')) as f:
            text = f.read()
        self.assertIn('[retroachievements]\nusername=\napi_key=\n', text)
        self.assertIn('limit=20', text)
        self.assertFalse(self.v.status()['set_up'])

    def test_setup_refused(self):
        self.install(user='', key='old')
        self.fake_ra(user_ok=False)
        r = self.v.setup('f3bandit', 'bad', 'KEY')
        self.assertFalse(r['ok'])
        self.assertIn('Invalid user', r['error'])
        self.fake_ra(key_ok=False)
        self.assertFalse(self.v.setup('f3bandit', 'pw', 'KEY')['ok'])
        self.assertFalse(self.v.setup('', 'pw', 'KEY')['ok'])
        self.assertFalse(self.v.setup('f3bandit', None, 'not a key!')['ok'])
        with open(os.path.join(self.base, 'config.ini')) as f:
            self.assertIn('api_key=old', f.read())            # (nothing written when it's refused)

    def test_setup_new_config(self):
        os.makedirs(os.path.join(self.root, 'Scripts'), exist_ok=True)
        with open(os.path.join(self.root, rv.SCRIPT), 'w') as f:
            f.write(FAKE % {'base': self.base})
        self.fake_ra()
        self.assertTrue(self.v.setup('f3bandit', None, 'KEY')['ok'])
        self.wait()
        with open(os.path.join(self.base, 'config.ini')) as f:
            self.assertEqual(f.read(), '[retroachievements]\nusername=f3bandit\napi_key=KEY\n')

    def test_not_installed(self):
        st = self.v.status()
        self.assertEqual((st['viewer'], st['set_up'], st['ra_cores']), (False, False, False))
        self.assertEqual(self.v.profile()['games'], [])
        self.assertFalse(self.v.refresh(1469))

    def test_not_set_up(self):
        self.install(user='YourUsername', key='YourWebApiKey')
        st = self.v.status()
        self.assertTrue(st['viewer'])
        self.assertFalse(st['set_up'])
        self.assertFalse(self.v.refresh())

    def test_key_never_shown(self):
        self.install()
        self.assertNotIn('abc123', json.dumps(self.v.profile()) + json.dumps(self.v.game(1469)))

    def test_games_and_game_from_the_viewers_own_fetch(self):
        self.install()
        self.v.profile()                                   # (none saved: fetched in the background)
        self.wait()
        p = self.v.profile()
        self.assertEqual([g['title'] for g in p['games']], ['Dr. Mario', 'Tetris'])   # (played last first)
        self.assertEqual(p['games'][1]['award'], 'mastered')
        self.assertEqual((p['achievements'], p['mastered']), (16, 1))
        self.v.game(1469)
        self.wait()
        g = self.v.game(1469)
        self.assertEqual([a['title'] for a in g['achievements']], ['Virus Buster 0 - Low', 'Second'])
        self.assertEqual((g['total'], g['unlocked'], g['points'], g['points_unlocked']), (2, 1, 15, 5))
        self.assertEqual(g['achievements'][0]['badge'], '')                      # (no BadgeName in the stand-in)
        calls = self.v.module().CALLS
        self.assertIn(('games', True), calls)
        self.assertIn(('game', 1469, True), calls)

    def test_ra_cores(self):
        self.assertFalse(rv.ra_cores(self.root))
        os.makedirs(os.path.join(self.root, '_RA_Cores', 'Cores'))
        open(os.path.join(self.root, '_RA_Cores', 'Cores', 'NES.rbf'), 'w').close()
        self.assertFalse(rv.ra_cores(self.root))           # (MiSTer_RA too)
        open(os.path.join(self.root, 'MiSTer_RA'), 'w').close()
        self.assertTrue(rv.ra_cores(self.root))

    def test_playing(self):                          # (a game on MiSTer_RA: found from RetroAchievements' last played)
        self.install()
        rv.FIRST_ASK, rv.PLAYING_EVERY = 0, 1
        self.v.ra_main = lambda: True
        self.v.last_played = lambda since=0: 1469
        self.v.on_event({'type': 'game_started', 'core': 'NES'})
        for _ in range(50):
            if self.v.current == 1469:
                break
            time.sleep(0.05)
        self.wait()
        self.assertTrue(os.path.isfile(os.path.join(self.base, 'cache', 'games', '1469.json')))
        self.assertEqual(self.v.playing()['ra_id'], 1469)
        self.v.on_event({'type': 'game_ended'})
        self.assertIsNone(self.v.playing())

    def test_last_played_is_this_game(self):          # (an earlier game's last played: not this one)
        self.install()
        class M(object):
            def read_config(self): return ('u', 'k')
            def __init__(self, when): self.when = when
            def api_get(self, ep, params): return [{'GameID': 1469, 'LastPlayed': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(self.when))}]
        self.v.module = lambda: M(time.time() - 600)
        self.assertEqual(self.v.last_played(time.time() - 120), 0)
        self.v.module = lambda: M(time.time() - 5)
        self.assertEqual(self.v.last_played(time.time() - 120), 1469)

    def test_not_ra_main(self):                      # (another Main: nothing asked)
        self.install()
        rv.FIRST_ASK = 0
        self.v.ra_main = lambda: False
        self.v.last_played = lambda since=0: self.fail('asked')
        self.v.on_event({'type': 'game_started', 'core': 'NES'})
        time.sleep(0.2)
        self.assertEqual(self.v.current, 0)

    def test_hidden_from_scripts(self):
        from coreshell import tools
        self.install()
        open(os.path.join(self.root, 'Scripts', 'other.sh'), 'w').close()

        class Shell(object):
            class paths(object):
                root = self.root
        names = [t['name'] for t in tools.Tools(Shell(), scripts=os.path.join(self.root, 'Scripts')).list()]
        self.assertEqual(names, ['other.sh'])

    def test_new_layout_is_said(self):
        with open(os.path.join(self.root, rv.SCRIPT), 'w') as f:
            f.write('#!/bin/sh\necho no python here\n')
        with self.assertRaises(RuntimeError):
            self.v.module()


if __name__ == '__main__':
    unittest.main()


class ServerSetup(unittest.TestCase):
    def test_refused_setup_is_an_error(self):
        from coreshell import server
        cls = next(c for c in vars(server).values() if isinstance(c, type) and hasattr(c, 'cmd_ra_viewer'))
        sh = cls.__new__(cls)

        class V(object):
            def setup(self, u, p, k, check=True):
                return {'ok': False, 'error': 'nope'} if p == 'bad' else {'ok': True, 'error': '', 'user': 'F3'}

            def status(self):
                return {'viewer': True}
        sh.raviewer = V()
        with self.assertRaises(ValueError):
            sh.cmd_ra_viewer({'setup': {'user': 'f3', 'password': 'bad'}})
        self.assertEqual(sh.cmd_ra_viewer({'setup': {'user': 'f3', 'password': 'pw'}})['user'], 'F3')
