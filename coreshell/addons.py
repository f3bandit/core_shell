"""Add-ons that take the system over (their own Main, their own menu): what each does, kept in addons/*.json, so they
come back to whichever frontend started them, as they come back to MiSTer's menu on stock MiSTer
(docs/stock-behaviour.md).

An add-on such as DreamSTer stops Main, runs its own menu and program, and starts /media/fat/MiSTer again when it's done.
Its menu says "Exit To MiSTer". With a frontend registered with Coreshell (`frontend` command: its name and its Main):
- the add-on runs as a copy whose own menu names the frontend ("Exit To Phosphor"): the installed file is never
  changed, so Update All keeps updating it; a rewrite whose text isn't found (a new release) is skipped, and the
  add-on runs as it is;
- `killall MiSTer` / `pidof MiSTer` in it mean whichever Main runs (compat/, on the scripts' PATH);
- the way back is MiSTer.ini's [MiSTer] main=, as on stock; Coreshell brings the frontend's Main back if that line
  doesn't lead to it.

addons/<id>.json:
  {"name": "DreamSTer", "script": "DreamSTer.sh",              the file in Scripts/ (its name)
   "how": "...",                                              what it does to Main and how it exits (for people)
   "menu": [{"from": "Exit To MiSTer", "to": "Exit To {frontend}"}, ...],   its own menu's text, frontend named
   "checked": {"md5": "...", "on": "2026-11-16"}}             the release the rewrites were checked against

  python3 -m coreshell run-script PATH    runs a script (from the Scripts menu's /tmp/script), as its entry says

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""
import json
import os
import stat

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_DIR = '/tmp/coreshell-addons'


def entries(home=HERE):
    out = []
    d = os.path.join(home, 'addons')
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for n in names:
        if not n.endswith('.json'):
            continue
        try:
            with open(os.path.join(d, n)) as f:
                e = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(e, dict) and e.get('script'):
            e['id'] = n[:-5]
            out.append(e)
    return out


def find(script, home=HERE):
    b = os.path.basename(script).lower()
    for e in entries(home):
        if e['script'].lower() == b:
            return e
    return None


def frontend(home=HERE):
    try:
        with open(os.path.join(home, 'state', 'frontend.json')) as f:
            h = json.load(f)
        return h if isinstance(h, dict) else {}
    except (OSError, ValueError):
        return {}


def rewrite(text, entry, name):
    """The add-on's text with its menu naming the frontend; None when a rewrite's text isn't there (another release:
    it runs as it is, never half changed)."""
    for r in entry.get('menu') or []:
        frm, to = r.get('from') or '', (r.get('to') or '').replace('{frontend}', name)
        if not frm or frm not in text:
            return None
        text = text.replace(frm, to)
    return text


def prepare(script, home=HERE, log=None):
    """The file to run for this script: a copy whose menu names the frontend, or the script itself."""
    e = find(script, home)
    name = frontend(home).get('name')
    if not e or not name or not e.get('menu'):
        return script
    try:
        with open(script, encoding='utf-8', errors='surrogateescape') as f:
            text = f.read()
    except OSError:
        return script
    new = rewrite(text, e, name)
    if new is None:
        if log:
            log('addons: %s: its menu text changed (a new release?): run as it is' % e['name'])
        return script
    os.makedirs(RUN_DIR, exist_ok=True)
    out = os.path.join(RUN_DIR, os.path.basename(script))
    with open(out + '.tmp', 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write(new)
    os.chmod(out + '.tmp', stat.S_IMODE(os.stat(script).st_mode) | stat.S_IXUSR)
    os.replace(out + '.tmp', out)
    if log:
        log('addons: %s: its menu names %s' % (e['name'], name))
    return out


def run_script(script, home=HERE):
    """exec the script (as its entry says), from its own folder like MiSTer's Scripts menu."""
    def log(s):
        try:
            with open('/tmp/coreshell.log', 'a') as f:
                f.write('coreshell: %s\n' % s)
        except OSError:
            pass
    try:
        path = prepare(script, home, log)
    except Exception as e:              # (/tmp full...: the script itself, as MiSTer's Scripts menu runs it)
        log('addons: %s: %s: run as it is' % (os.path.basename(script), e))
        path = script
    os.chdir(os.path.dirname(os.path.abspath(script)))
    os.execv(path, [path])
