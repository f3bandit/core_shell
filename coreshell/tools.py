"""Coreshell - the tools section: MiSTer's Scripts folder from the unified menu.

Scripts run on the TV the way MiSTer's Scripts menu runs them: Main switches
the screen to its Linux terminal, the script runs with keyboard and pad,
"Press any key to continue", and the screen comes back. Only the frontend can
start that: it's the one drawing on the screen, so it has to stop drawing and
hand the console back first (Phosphor's shell does it with Phosphor's Main's
"script" command; Console Mode with its own). So:

  * Coreshell lists the scripts (tools), says which ask for input, and which
    change cores (Update All);
  * the frontend runs a script through its Main and tells Coreshell when it
    ended (tool_done): after Update All, Coreshell refreshes the profiles of
    the cores it changed;
  * scripts that need no input can also run here in the background
    (tool_run), their output in a log the frontend shows (tool_status).

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import glob
import os
import re
import signal
import subprocess
import threading
import time

LOG = '/tmp/coreshell_tool.log'

# what the scan below can't tell from the script itself
KNOWN = {
    'update_all.sh': {'interactive': False, 'about': 'Update All: cores, Main, Linux and extras',
                      'refresh_after': True},
    'update_all_latest_log.sh': {'interactive': False, 'about': "Update All's last log"},
    'update_all_timeline.sh': {'interactive': False, 'about': "Update All's timeline"},
    'update.sh': {'interactive': False, 'about': 'MiSTer updater', 'refresh_after': True},
    # an add-on the frontend shows in its own screens (raviewer.py): kept, updated by Update All, not listed
    'ra_viewer.sh': {'hidden': True, 'about': 'RetroAchievements Viewer (shown in the frontend\'s own screens)'},
}

# a script that waits for a person: dialog/whiptail menus, a read from the keyboard
# (not `read ... <<<` / `read ... < file`), select menus, a pager
_ASKS = re.compile(r'(^|[^\w-])(dialog|whiptail)\s|(^|[;&|\s(])read\s[^\n<]*$'
                   r'|(^|[^\w])select\s+\w+\s+in\s|(^|[^\w])(less|more)\s', re.M)
_ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\r')


def scan(path):
    """Does this script wait for a person?"""
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read(400000)
    except OSError:
        return True
    code = '\n'.join(l for l in text.split('\n') if not l.lstrip().startswith('#'))
    return bool(_ASKS.search(code))


class Tools(object):
    def __init__(self, shell, scripts=None, log=LOG):
        self.shell = shell
        self.scripts = scripts or os.path.join(shell.paths.root, 'Scripts')
        self.log = log
        self.proc = None
        self.status = {'state': 'idle'}
        self.lock = threading.Lock()

    def list(self):
        out = []
        for f in sorted(glob.glob(os.path.join(self.scripts, '*.sh')), key=str.lower):
            name = os.path.basename(f)
            k = KNOWN.get(name, {})
            if k.get('hidden'):
                continue
            out.append({'name': name, 'title': k.get('about') or os.path.splitext(name)[0].replace('_', ' '),
                        'path': f, 'interactive': k.get('interactive', scan(f)),
                        'refresh_after': bool(k.get('refresh_after'))})
        return out

    def run(self, name, force=False):
        """Run a script in the background, its output in the log (one at a time). An
        interactive one only with force: it gets no keyboard, so it may stop at its
        first question; the frontend runs those on the TV through its Main."""
        tool = next((t for t in self.list() if t['name'] == name), None)
        if not tool:
            raise KeyError('no script %r in %s' % (name, self.scripts))
        if tool['interactive'] and not force:
            raise ValueError('%s asks for input: run it in a terminal screen' % name)
        with self.lock:
            if self.proc and self.proc.poll() is None:
                raise RuntimeError('%s is still running' % self.status.get('name'))
            logf = open(self.log, 'wb')
            env = dict(os.environ, TERM='dumb', HOME='/root')
            self.proc = subprocess.Popen(['/bin/bash', tool['path']], cwd=self.scripts,
                                         stdin=subprocess.DEVNULL, stdout=logf, stderr=subprocess.STDOUT,
                                         env=env, start_new_session=True)
            logf.close()
            self.status = {'state': 'running', 'name': name, 'started': int(time.time()), 'exit': None,
                           'refresh_after': tool['refresh_after']}
        threading.Thread(target=self._wait, args=(self.proc, self.status), daemon=True).start()
        return self.status

    def done(self, name, exit_code):
        """The frontend ran a script on the TV and it ended: refresh after Update All."""
        tool = next((t for t in self.list() if t['name'] == name), None)
        if not tool:
            raise KeyError('no script %r in %s' % (name, self.scripts))
        out = {'name': name, 'exit': exit_code, 'refresh': False}
        if exit_code == 0 and tool['refresh_after']:
            self.shell.refresher.start(wait=5)          # (already running: it looks again once it's done)
            out['refresh'] = True
        return out

    def _wait(self, proc, st):
        rc = proc.wait()
        st['exit'] = rc
        st['ended'] = int(time.time())
        if st['state'] == 'running':
            st['state'] = 'done' if rc == 0 else 'failed'
        if st['state'] == 'done' and st.get('refresh_after'):
            st['refresh'] = self.shell.refresher.start()      # the cores it changed get new profiles

    def stop(self):
        p = self.proc
        if p and p.poll() is None:
            self.status['state'] = 'stopped'
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                os.killpg(p.pid, signal.SIGKILL)
            return True
        return False

    def output(self, lines=40):
        """The last lines of the running/last script's output, terminal codes removed."""
        try:
            with open(self.log, 'rb') as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 65536))
                text = f.read().decode('utf-8', 'replace')
        except OSError:
            return []
        return [l for l in _ANSI.sub('', text).split('\n') if l.strip()][-lines:]
