"""Coreshell - RetroAchievements for a frontend, from the RA Viewer add-on (Anime0t4ku's ra_viewer.sh, installed by MiSTer
Companion or by hand in Scripts/), hidden: the frontend shows its own screens, the add-on stays as it is.

On a stock MiSTer, RetroAchievements is odelot's MiSTer_RA and his RA cores (the client: login, unlocks, his
retroachievements.cfg) and the RA Viewer (the user's progress and each game's list, from retroachievements.org's web
API with the user's own username and Web API key in its config.ini). A frontend here is neither: Coreshell keeps no
login, key or token of its own and asks RetroAchievements for no data itself. What the frontend gets:
  - status: whether the RA Viewer and the RA cores are installed, and whether the RA Viewer is set up (its username:
    never its key);
  - the RA Viewer's own saved data (Scripts/.config/ra_viewer/cache: games.json, games/<id>.json), shaped for a list;
  - fresh data when asked, and while a game runs on MiSTer_RA: when a game starts (CORENAME, as any Main writes it)
    with MiSTer_RA the Main running, the game it is is the one RetroAchievements has as the user's last played
    (asked a few seconds in, through the RA Viewer's own api_get, its config and key), then its list is fetched again
    every few minutes while it runs and once more when it ends: the RA Viewer's own get_games / get_game run in the
    background, from the add-on as installed (its code read from Scripts/ra_viewer.sh each time it changes, so Update
    All's updates are what runs), with its own config and key, writing its own cache;
  - setup (the frontend's RA page, the apps): the user's username and password checked once with RetroAchievements'
    own login, the Web API key with the web API, then written into the RA Viewer's config.ini as the user would by hand
    (the password goes only into MiSTer_RA's retroachievements.cfg, which the frontend writes).

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import configparser
import importlib.util
import json
import os
import threading
import time

SCRIPT = 'Scripts/ra_viewer.sh'                    # (under the SD card's root)
BASE = 'Scripts/.config/ra_viewer'
HEREDOC = "cat > \"$HELPER\" <<'PYEOF'\n"
GAME_EVERY = 20         # s: one game's list asked again at most this often
GAMES_EVERY = 120       # s: the whole list
PLAYING_EVERY = 180     # s: the game being played, its list asked again this often while it runs
FIRST_ASK = 15          # s into a game: RetroAchievements has it as last played by then (MiSTer_RA's session)
MEDIA = 'https://media.retroachievements.org'   # (the badges and icons the RA Viewer shows: links, for the apps)


def installed(root):
    return os.path.isfile(os.path.join(root, SCRIPT))


def ra_cores(root):
    """odelot's RA cores and MiSTer_RA, as Update All (or MiSTer Companion) puts them."""
    d = os.path.join(root, '_RA_Cores', 'Cores')
    try:
        cores = [n for n in os.listdir(d) if n.lower().endswith('.rbf')]
    except OSError:
        cores = []
    return bool(cores) and os.path.isfile(os.path.join(root, 'MiSTer_RA'))


def username(root):
    """The RA Viewer's username when it's set up (never its key)."""
    p = configparser.ConfigParser()
    try:
        p.read(os.path.join(root, BASE, 'config.ini'), encoding='utf-8')
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None
    user = p.get('retroachievements', 'username', fallback='').strip()
    key = p.get('retroachievements', 'api_key', fallback='').strip()
    if user in ('', 'YourUsername') or key in ('', 'YourWebApiKey'):
        return None
    return user


SITE = 'https://retroachievements.org'
UA = 'Coreshell (MiSTer; RA Viewer setup)'


def _http(method, url, params, timeout=20):
    """One request to RetroAchievements, its JSON answer (an error's JSON too: 401 says why)."""
    import urllib.error
    import urllib.parse
    import urllib.request
    q = urllib.parse.urlencode(params)
    if method == 'GET':
        req = urllib.request.Request(url + '?' + q, headers={'User-Agent': UA})
    else:
        req = urllib.request.Request(url, data=q.encode(), headers={'User-Agent': UA,
                                     'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(1 << 20)
    except urllib.error.HTTPError as e:
        body = e.read(1 << 16)
        if e.code not in (400, 401, 403, 422):
            raise RuntimeError('HTTP %d' % e.code)
    try:
        v = json.loads(body.decode('utf-8', 'replace'))
    except ValueError:
        raise RuntimeError('an answer that isn\'t RetroAchievements\'')
    return v if isinstance(v, dict) else {}


def write_config(root, user, key=None):
    """username (and api_key when given: '' empties it) in the RA Viewer's config.ini, its [retroachievements] section; every other
    line of the file as it was (the RA Viewer's own comments and settings). A new file when there's none."""
    path = os.path.join(root, BASE, 'config.ini')
    want = {'username': user}
    if key is not None:
        want['api_key'] = key
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    out, sect, done, seen = [], None, set(), False
    def rest():
        for k in ('username', 'api_key'):
            if k in want and k not in done:
                out.append('%s=%s' % (k, want[k]))
                done.add(k)
    for ln in lines:
        s = ln.strip()
        if s.startswith('[') and s.endswith(']'):
            if sect == 'retroachievements':
                rest()
            sect = s[1:-1].strip().lower()
            seen = seen or sect == 'retroachievements'
            out.append(ln)
            continue
        k = s.split('=', 1)[0].strip().lower() if '=' in s and not s.startswith(('#', ';')) else None
        if sect == 'retroachievements' and k in want:
            if k not in done:
                out.append('%s=%s' % (k, want[k]))
                done.add(k)
            continue
        out.append(ln)
    if sect == 'retroachievements':
        rest()
    if not seen:
        if out and out[-1].strip():
            out.append('')
        out.append('[retroachievements]')
        rest()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.new'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write('\n'.join(out) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def key_hint(root):
    """The end of the RA Viewer's key (its last 4), for showing that one's saved: never the key."""
    p = configparser.ConfigParser()
    try:
        p.read(os.path.join(root, BASE, 'config.ini'), encoding='utf-8')
    except (configparser.Error, OSError, UnicodeDecodeError):
        return ''
    key = p.get('retroachievements', 'api_key', fallback='').strip()
    return key[-4:] if len(key) >= 16 and key != 'YourWebApiKey' else ''


def _load(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _int(v, d=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def game_row(r):
    """One game of the RA Viewer's games.json (API_GetUserCompletionProgress) for a list."""
    award = str(r.get('HighestAwardKind') or '')
    icon = r.get('ImageIcon') or ''
    return {'ra_id': _int(r.get('GameID')), 'title': r.get('Title') or '', 'console': r.get('ConsoleName') or '',
            'icon': (MEDIA + icon) if icon.startswith('/') else icon,
            'total': _int(r.get('MaxPossible')), 'unlocked': _int(r.get('NumAwarded')),
            'hardcore': _int(r.get('NumAwardedHardcore')),
            'award': {'mastered': 'mastered', 'completed': 'completed', 'beaten-hardcore': 'beaten',
                      'beaten-softcore': 'beaten'}.get(award, ''),
            'last': r.get('MostRecentAwardedDate') or ''}


def ach_rows(game):
    """One game's achievements (API_GetGameInfoAndUserProgress), in its own order, as the RA Viewer lists them."""
    a = game.get('Achievements') or {}
    rows = list(a.values()) if isinstance(a, dict) else (a if isinstance(a, list) else [])
    rows.sort(key=lambda x: _int(x.get('DisplayOrder'), 999999))
    out = []
    for x in rows:
        got = bool(x.get('DateEarned') or x.get('DateEarnedHardcore'))
        b = str(x.get('BadgeName') or '')
        out.append({'id': _int(x.get('ID')), 'title': x.get('Title') or '', 'description': x.get('Description') or '',
                    'points': _int(x.get('Points')),
                    'badge': '%s/Badge/%s%s.png' % (MEDIA, b, '' if got else '_lock') if b else '',
                    'unlocked': got,
                    'hardcore': bool(x.get('DateEarnedHardcore')),
                    'date': x.get('DateEarnedHardcore') or x.get('DateEarned') or ''})
    return out


class RAViewer(object):
    def __init__(self, root='/media/fat', tmp='/tmp/coreshell', say=None):
        self.root = root
        self.tmp = tmp
        self.say = say or (lambda *a: None)
        self.lock = threading.Lock()
        self.busy = False
        self.err = ''
        self.want = []                    # games asked for while busy (one fetch at a time)
        self.asked = {}                   # game id (0: the list) -> when it was last fetched
        self._mod = None
        self._mod_key = None
        self.http = _http                 # (tests: a fake RetroAchievements)
        self.current = 0                  # the game being played on MiSTer_RA (its RetroAchievements id), 0: none
        self._gen = 0                     # each game start and end (the one being followed)

    # ------------------------------------------------------------------ what the frontend shows
    def status(self):
        user = username(self.root) if installed(self.root) else None
        games = _load(os.path.join(self.root, BASE, 'cache', 'games.json')) or {}
        return {'viewer': installed(self.root), 'set_up': bool(user), 'user': user or '',
                'key_hint': key_hint(self.root) if user else '',
                'ra_cores': ra_cores(self.root), 'updating': self.busy, 'error': self.err,
                'fetched_at': _int(games.get('fetched_at')), 'playing': self.current}

    def profile(self, refresh=False):
        """The user's games, from the RA Viewer's list (asked again in the background when old, or when asked)."""
        st = self.status()
        if st['viewer'] and st['set_up'] and (refresh or self._old(0, GAMES_EVERY)):
            self.refresh()
        data = _load(os.path.join(self.root, BASE, 'cache', 'games.json')) or {}
        rows = [game_row(r) for r in (data.get('Results') or []) if isinstance(r, dict)]
        rows.sort(key=lambda g: g['last'], reverse=True)               # (played last first, as the RA Viewer's Recent)
        st.update(games=rows, achievements=sum(g['unlocked'] for g in rows),
                  mastered=sum(1 for g in rows if g['award'] == 'mastered'), updating=self.busy)
        return st

    def game(self, ra_id, refresh=False):
        """One game's achievements, from the RA Viewer's file for it (fetched when there's none, or old)."""
        ra_id = _int(ra_id)
        st = self.status()
        path = os.path.join(self.root, BASE, 'cache', 'games', '%d.json' % ra_id)
        have = os.path.isfile(path)
        if ra_id > 0 and st['viewer'] and st['set_up'] and (refresh or not have or self._old(ra_id, GAME_EVERY)):
            self.refresh(ra_id, games=False)
        g = _load(path) or {}
        rows = ach_rows(g)
        st.update(ra_id=ra_id, title=g.get('Title') or '', console=g.get('ConsoleName') or '', achievements=rows,
                  total=len(rows), unlocked=sum(1 for a in rows if a['unlocked']),
                  hardcore=sum(1 for a in rows if a['hardcore']),
                  points=sum(a['points'] for a in rows), points_unlocked=sum(a['points'] for a in rows if a['unlocked']),
                  have=bool(g), supported=bool(rows) or not g, updating=self.busy)
        return st

    def _old(self, key, every):
        return time.time() - self.asked.get(key, 0) > every

    # ------------------------------------------------------------------ the RA Viewer's own fetch, in the background
    def module(self):
        """The RA Viewer's Python, as the installed ra_viewer.sh writes it (its heredoc), loaded as a module: only its
        functions are used (get_games, get_game: its config, its key, its cache), never its screens."""
        sh = os.path.join(self.root, SCRIPT)
        st = os.stat(sh)
        key = (st.st_size, st.st_mtime)
        if self._mod is not None and self._mod_key == key:
            return self._mod
        with open(sh, encoding='utf-8', errors='replace') as f:
            text = f.read()
        i = text.find(HEREDOC)
        j = text.find('\nPYEOF\n', i + len(HEREDOC)) if i >= 0 else -1
        if i < 0 or j < 0:
            raise RuntimeError("the RA Viewer's script has no Python part where it used to (a new version?)")
        os.makedirs(self.tmp, exist_ok=True)
        py = os.path.join(self.tmp, 'ra_viewer_addon.py')
        with open(py, 'w', encoding='utf-8') as f:
            f.write(text[i + len(HEREDOC):j + 1])
        spec = importlib.util.spec_from_file_location('ra_viewer_addon', py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for fn in ('get_games', 'get_game'):
            if not callable(getattr(mod, fn, None)):
                raise RuntimeError("the RA Viewer has no %s() any more (a new version?)" % fn)
        self._mod, self._mod_key = mod, key
        return mod

    def refresh(self, ra_id=0, games=True):
        """The RA Viewer's data fetched again in the background (its list, and one game's when ra_id)."""
        if not installed(self.root) or not username(self.root):
            return False
        with self.lock:
            job = (int(ra_id or 0), bool(games))
            if self.busy:
                if job not in self.want:
                    self.want.append(job)
                return True
            self.busy = True
        threading.Thread(target=self._run, args=(job,), name='raviewer', daemon=True).start()
        return True

    def _run(self, job):
        while job:
            ra_id, games = job
            try:
                mod = self.module()
                if games:
                    self.asked[0] = time.time()
                    mod.get_games(force=True)
                if ra_id > 0:
                    self.asked[ra_id] = time.time()
                    mod.get_game(ra_id, force=True)
                self.err = ''
            except Exception as e:           # (the network, RetroAchievements, the add-on: said, never raised)
                self.err = str(e).strip().split('\n')[0][:200]
                self.say('raviewer: %s' % self.err)
            with self.lock:
                job = self.want.pop(0) if self.want else None
                if not job:
                    self.busy = False

    # ------------------------------------------------------------------ setting it up (the frontend's RA page, the apps)
    def setup(self, user, password=None, key=None, check=True):
        """The user's RetroAchievements account checked and the RA Viewer set up with it, as the user would by hand:
        the password with RetroAchievements' own login (the one MiSTer_RA and the emulators use: no developer or
        client ID), the Web API key with the web API it's for; then the username and key into the RA Viewer's
        config.ini. The password is only checked here (the frontend writes MiSTer_RA's retroachievements.cfg) and
        never kept or logged. Returns {'ok', 'error', 'user'} ('user': the name as RetroAchievements spells it)."""
        user = (user or '').strip()
        key = (key or '').strip()
        if not user or len(user) > 64 or any(c in user for c in '\r\n=[]'):
            return {'ok': False, 'error': 'Type your RetroAchievements username'}
        if key and (len(key) > 64 or not key.isalnum()):
            return {'ok': False, 'error': "That Web API key doesn't look right (letters and numbers only)"}
        if not check:                           # (Save: stored as typed; Finish setup tests it after)
            if installed(self.root) and not write_config(self.root, user, key or None):
                return {'ok': False, 'error': "Couldn't write the RA Viewer's settings on the SD card"}
            return {'ok': True, 'error': '', 'user': user}
        try:
            if password:
                r = self.http('POST', '%s/dorequest.php' % SITE, {'r': 'login2', 'u': user, 'p': password})
                if not r or not r.get('Success'):
                    err = (r or {}).get('Error') or ''
                    return {'ok': False, 'error': 'RetroAchievements says: %s' % err if err else
                            "RetroAchievements didn't take that username and password"}
                user = r.get('User') or user
            if key:
                r = self.http('GET', '%s/API/API_GetUserProfile.php' % SITE, {'u': user, 'y': key})
                if not r or not (r.get('User') or r.get('ULID')):
                    return {'ok': False, 'error': "RetroAchievements didn't take that Web API key"}
        except Exception as e:                   # (no network, RetroAchievements down)
            return {'ok': False, 'error': "Couldn't reach RetroAchievements: %s" % str(e).split('\n')[0][:120]}
        if installed(self.root):
            if not write_config(self.root, user, key or None):
                return {'ok': False, 'error': "Couldn't write the RA Viewer's settings on the SD card"}
            self.refresh()
        return {'ok': True, 'error': '', 'user': user}

    def test(self, password=None):
        """The stored account tested with RetroAchievements (Finish setup): the RA Viewer's username and key from its
        config.ini, the password from the frontend (MiSTer_RA's retroachievements.cfg). Nothing written but the name
        as RetroAchievements spells it; the RA Viewer asked for fresh data when it works."""
        p = configparser.ConfigParser()
        try:
            p.read(os.path.join(self.root, BASE, 'config.ini'), encoding='utf-8')
        except (configparser.Error, OSError, UnicodeDecodeError):
            pass
        user = p.get('retroachievements', 'username', fallback='').strip()
        key = p.get('retroachievements', 'api_key', fallback='').strip()
        if not user:
            return {'ok': False, 'error': 'No username stored yet: type it and Save'}
        if not key:
            return {'ok': False, 'error': 'No Web API key stored yet: paste it and Save'}
        if not password:
            return {'ok': False, 'error': 'No password stored yet: type it and Save'}
        return self.setup(user, password, key)

    def forget(self):
        """The RA Viewer's username and key emptied (the user's Remove: set it up again after). Its own cache stays,
        as the RA Viewer leaves it."""
        if installed(self.root) and not write_config(self.root, '', ''):
            return {'ok': False, 'error': "Couldn't change the RA Viewer's settings on the SD card"}
        return {'ok': True, 'error': ''}

    # ------------------------------------------------------------------ the game being played (MiSTer_RA's)
    def on_event(self, ev):
        """A game started with MiSTer_RA the Main running: found (its RetroAchievements id), its list fetched, again
        every few minutes while it runs, and once more when it ends."""
        t = ev.get('type')
        if t == 'game_started':
            self.current = 0
            self._gen += 1
            threading.Thread(target=self._playing, args=(self._gen,), name='raviewer-game', daemon=True).start()
        elif t == 'game_ended':
            self._gen += 1
            gid, self.current = self.current, 0
            if gid > 0:
                threading.Timer(6.0, self.refresh, args=(gid,)).start()

    def ra_main(self):
        """MiSTer_RA is the Main running (the RA cores' Main, as MiSTer.ini sends them)."""
        for d in os.listdir('/proc'):
            if d.isdigit():
                try:
                    if os.path.basename(os.readlink('/proc/%s/exe' % d)).split(' ')[0] == 'MiSTer_RA':
                        return True
                except OSError:
                    pass
        return False

    def last_played(self, since=0):
        """The user's last played game on RetroAchievements (its id), when it was played after since (a game started
        now: never an earlier one's)."""
        mod = self.module()
        user, key = mod.read_config()
        rows = mod.api_get('API_GetUserRecentlyPlayedGames.php', {'y': key, 'u': user, 'c': 1})
        r = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
        try:
            import calendar
            when = calendar.timegm(time.strptime(str(r.get('LastPlayed', ''))[:19], '%Y-%m-%d %H:%M:%S'))
        except ValueError:
            return 0
        return _int(r.get('GameID')) if when >= (since or time.time() - 900) else 0

    def _playing(self, gen):
        started = time.time() - 120          # (RetroAchievements' clock and the SS1's: a little room)
        gid = 0
        for _ in range(4):                   # (MiSTer_RA logs in and identifies the game first: asked again if not yet)
            time.sleep(FIRST_ASK)
            if gen != self._gen or not installed(self.root) or not username(self.root) or not self.ra_main():
                return
            try:
                gid = self.last_played(started)
            except Exception as e:          # (the network, RetroAchievements, the add-on: said, never raised)
                self.say('raviewer: %s' % str(e).strip().split('\n')[0][:200])
            if gid > 0:
                break
        if gid <= 0 or gen != self._gen:
            return
        self.current = gid
        while gen == self._gen:
            self.refresh(gid)
            for _ in range(PLAYING_EVERY):
                if gen != self._gen:
                    return
                time.sleep(1)

    def playing(self):
        """The game being played on MiSTer_RA (its list, as game()), or None."""
        gid = self.current
        return self.game(gid) if gid > 0 else None
