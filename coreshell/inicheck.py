"""Coreshell - INI check: find MiSTer.ini lines an older Main doesn't know.

Every Main parses the [MiSTer] section of the active INI. A Main built before
an option existed (the Quake and Duke3D ports' Mains, for example, predate
HDMI CEC) shows "<option>: unknown option" on screen for each one. Nothing
needs recompiling: if the line only sets Main's default value, commenting it
out changes nothing for the newer Mains and silences the older ones.

  check   lists, per INI, the lines some installed Main doesn't know
  fix     comments out those lines that are at their default value
          (backup first, each line marked); lines with a real setting are
          only reported, never touched

Which options a Main knows is read from the Main binary itself: its option
table is a list of plain 0-terminated names.

Copyright (c) 2026 f3bandit. Licensed under the Apache License 2.0 (LICENSE, NOTICE).
"""

import os
import re
import shutil
import time

# Main's non-zero defaults (cfg_parse() in Main_MiSTer cfg.cpp, Nov 2026);
# every other option defaults to 0 / empty.
DEFAULTS = {
    'COMPOSITE_SYNC': '1', 'BOOTSCREEN': '1', 'FB_TERMINAL': '1', 'CONTROLLER_INFO': '6',
    'BROWSE_EXPAND': '1', 'LOGO': '1', 'RUMBLE': '1', 'DPAD_THRESHOLD': '50',
    'WHEEL_FORCE': '50', 'DVI_MODE': '2', 'LOOKAHEAD': '1', 'HDMI_CEC_INPUT_MODE': '1',
    'HDMI_CEC_POWER_ON': '1', 'HDR_MAX_NITS': '1000', 'HDR_AVG_NITS': '250',
    'VIDEO_BRIGHTNESS': '50', 'VIDEO_CONTRAST': '50', 'VIDEO_SATURATION': '100',
    'SCREENSHOT_INFO': '1', 'VIDEO_GAIN_OFFSET': '1, 0, 1, 0, 1, 0', 'MAIN': 'MiSTer',
    'AUTOFIRE_RATES': '10,15,30', 'SCREENSHOT_IMAGE_FORMAT': 'png',
}

MARK = '; coreshell: unknown to '
_LINE = re.compile(r'^(\s*)([A-Za-z0-9_]+)(\s*=\s*)([^;\r\n]*?)(\s*)(;.*)?$')
_NAME = re.compile(rb'^[A-Z][A-Z0-9_]{1,40}$')


def known_options(binary):
    """Option names a Main binary knows (its upper-case 0-terminated strings)."""
    with open(binary, 'rb') as f:
        data = f.read()
    return {s.decode() for s in data.split(b'\x00') if _NAME.match(s)}


def ini_files(root):
    return sorted(n for n in os.listdir(root)
                  if n.lower() == 'mister.ini' or
                  (n.lower().startswith('mister_') and n.lower().endswith('.ini')))


def mains(root):
    """Every Main an INI can hand a core to (main=...), plus the default."""
    found = {'MiSTer'}
    for ini in ini_files(root):
        with open(os.path.join(root, ini), encoding='utf-8', errors='replace') as f:
            for line in f:
                m = _LINE.match(line)
                if m and m.group(2).upper() == 'MAIN' and m.group(4):
                    found.add(m.group(4).strip())
    return sorted(m for m in found if os.path.isfile(os.path.join(root, m)))


def is_default(key, value):
    v = value.strip().strip('"')
    d = DEFAULTS.get(key.upper())
    if d is not None:
        return v.replace(' ', '') == d.replace(' ', '')
    if v in ('', '0'):
        return True
    try:
        return float(v) == 0
    except ValueError:
        return False


def check(root):
    """{ini: [{line, key, value, default, unknown_to: [mains]}]} for [MiSTer] lines."""
    known = {m: known_options(os.path.join(root, m)) for m in mains(root)}
    out = {}
    for ini in ini_files(root):
        hits = []
        section = None
        with open(os.path.join(root, ini), encoding='utf-8', errors='replace') as f:
            for n, line in enumerate(f, 1):
                t = line.strip()
                if t.startswith('['):
                    section = t[1:t.find(']')].lower() if ']' in t else None
                    continue
                if section != 'mister':
                    continue
                m = _LINE.match(line)
                if not m:
                    continue
                key, value = m.group(2).upper(), m.group(4)
                missing = [b for b, opts in known.items() if key not in opts]
                if missing and len(missing) < len(known):   # unknown everywhere = a typo, not age
                    hits.append({'line': n, 'key': m.group(2), 'value': value.strip(),
                                 'default': is_default(key, value), 'unknown_to': missing})
        if hits:
            out[ini] = hits
    return out, sorted(known)


def fix(root, backups):
    """Comment out the default-valued lines from check(). Returns what changed."""
    report, _ = check(root)
    changed = {}
    stamp = time.strftime('%Y%m%d-%H%M%S')
    for ini, hits in report.items():
        todo = {h['line']: h for h in hits if h['default']}
        if not todo:
            continue
        path = os.path.join(root, ini)
        os.makedirs(backups, exist_ok=True)
        shutil.copy2(path, os.path.join(backups, '%s.%s' % (ini, stamp)))
        with open(path, encoding='utf-8', errors='replace', newline='') as f:
            lines = f.readlines()
        for n, h in todo.items():
            raw = lines[n - 1]
            eol = '\r\n' if raw.endswith('\r\n') else '\n' if raw.endswith('\n') else ''
            body = raw[:len(raw) - len(eol)] if eol else raw
            lines[n - 1] = ';%s %s%s%s' % (body, MARK, ','.join(h['unknown_to']), eol)
        tmp = path + '.coreshell'
        with open(tmp, 'w', encoding='utf-8', newline='') as f:
            f.writelines(lines)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        changed[ini] = [h['key'] for h in todo.values()]
    return changed


def undo(root, backups):
    """Put back every line fix() commented out (by its marker)."""
    restored = {}
    for ini in ini_files(root):
        path = os.path.join(root, ini)
        with open(path, encoding='utf-8', errors='replace', newline='') as f:
            lines = f.readlines()
        keys = []
        for i, raw in enumerate(lines):
            if raw.startswith(';') and MARK in raw:
                eol = '\r\n' if raw.endswith('\r\n') else '\n' if raw.endswith('\n') else ''
                lines[i] = raw[1:raw.index(' ' + MARK)] + eol
                keys.append(lines[i].split('=', 1)[0].strip())
        if keys:
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.writelines(lines)
            restored[ini] = keys
    return restored


# ------------------------------------------------------------------ per launch
# The user's INI is theirs and is never written. While an older Main starts (it reads the INI once, at start),
# Coreshell lays its own view of the INI over it (a bind mount: the file on the SD card is untouched, and a
# reboot drops the mount), without the lines that Main doesn't know, and takes the view away once that Main is
# up. What each Main doesn't know is kept per Main in ini_fixes.json (worked out again when the Main changes).

import subprocess

FIXES = 'ini_fixes.json'
VIEW_DIR = '/tmp/coreshell_ini'
LAUNCH_MARK = '; coreshell: while %s starts'


def _load(path, default):
    import json
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    import json
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def main_unknowns(root, main, state):
    """Options the stock Main knows and this Main doesn't (saved per Main, by its size and date)."""
    mpath, spath = os.path.join(root, main), os.path.join(root, 'MiSTer')
    if main.lower() == 'mister' or not os.path.isfile(mpath) or not os.path.isfile(spath):
        return []
    st = os.stat(mpath)
    sig = '%d:%d' % (st.st_size, int(st.st_mtime))
    os.makedirs(state, exist_ok=True)
    fixes = _load(os.path.join(state, FIXES), {})
    hit = fixes.get(main)
    if hit and hit.get('sig') == sig:
        return hit['unknown']
    unknown = sorted(known_options(spath) - known_options(mpath))
    fixes[main] = {'sig': sig, 'unknown': unknown}
    _save(os.path.join(state, FIXES), fixes)
    return unknown


def launch_view(root, ini, main, state, add=None):
    """The INI as `main` should see it: (text, what's changed), or (None, []) when it can read the
    user's INI as it is. Only default-valued [MiSTer] lines are left out; real settings stay.
    add: (section, key, value): a line the launch needs that the user's INI has no say on (a hybrid port's
    [<core>] main=, as its installer writes it), put at the end, where it's the last word."""
    unknown = set(main_unknowns(root, main, state))
    if not unknown and not add:
        return None, []
    with open(os.path.join(root, ini), encoding='utf-8', errors='replace', newline='') as f:
        lines = f.readlines()
    section, keys = None, []
    for i, raw in enumerate(lines):
        t = raw.strip()
        if t.startswith('['):
            section = t[1:t.find(']')].lower() if ']' in t else None
            continue
        if section != 'mister':
            continue
        m = _LINE.match(raw)
        if not m or m.group(2).upper() not in unknown or not is_default(m.group(2), m.group(4)):
            continue
        eol = '\r\n' if raw.endswith('\r\n') else '\n' if raw.endswith('\n') else ''
        body = raw[:len(raw) - len(eol)] if eol else raw
        lines[i] = ';%s %s%s' % (body, LAUNCH_MARK % main, eol)
        keys.append(m.group(2))
    if add:
        eol = '\r\n' if lines and lines[-1].endswith('\r\n') else '\n'
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += eol
        lines += [eol, LAUNCH_MARK % main + eol, '[%s]%s' % (add[0], eol), '%s=%s%s' % (add[1], add[2], eol)]
        keys.append('[%s] %s' % (add[0], add[1]))
    return (''.join(lines), keys) if keys else (None, [])


def _mounted(path):
    try:
        with open('/proc/mounts') as f:
            return any(l.split()[1].replace('\\040', ' ') == path for l in f)
    except OSError:
        return False


def view_for_launch(root, ini, main, state, view_dir=VIEW_DIR, bind=True, add=None):
    """Lay the view over the user's INI for `main`'s start. Returns what it changed."""
    restore(root, view_dir)                       # (one left from before: never stack them)
    text, keys = launch_view(root, ini, main, state, add)
    if not keys:
        return []
    os.makedirs(view_dir, exist_ok=True)
    view = os.path.join(view_dir, ini)
    with open(view, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    if bind:
        subprocess.check_call(['mount', '--bind', view, os.path.join(root, ini)])
    return keys


def restore(root, view_dir=VIEW_DIR):
    """Take the view away (also at Coreshell's start). The user's file was never written."""
    done = None
    try:
        names = os.listdir(view_dir)
    except OSError:
        return None
    for n in names:
        target = os.path.join(root, n)
        if _mounted(target):
            subprocess.call(['umount', target])
            done = n
        try:
            os.unlink(os.path.join(view_dir, n))
        except OSError:
            pass
    return done


def main_started(main, after_pids, timeout=20.0, settle=3.0):
    """Wait for a new process running `main` (not one of after_pids), then a moment for its INI read."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for d in os.listdir('/proc'):
            if not d.isdigit() or int(d) in after_pids:
                continue
            try:
                exe = os.readlink('/proc/%s/exe' % d)
            except OSError:
                continue
            if os.path.basename(exe).lower() == os.path.basename(main).lower():
                time.sleep(settle)
                return True
        time.sleep(0.2)
    return False


def pids():
    return {int(d) for d in os.listdir('/proc') if d.isdigit()}
